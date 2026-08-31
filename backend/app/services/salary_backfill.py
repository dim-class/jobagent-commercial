"""Bounded salary backfill planning and progress; never browser control."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFoundError, ValidationError
from app.models import Job, SalaryBackfillItem, SalaryBackfillRun
from app.services.salary_text import is_valid_salary_text

SESSION_CAP = 3
MAX_PLAN_JOBS = 100
OPEN_STATES = {"pending", "running", "paused"}


def _eligible(job: Job) -> bool:
    # An unreadable salary (obfuscated-font placeholders shown as boxes) counts
    # as missing, so those jobs re-enter this same bounded backfill/OCR plan.
    if is_valid_salary_text(job.salary_text):
        return False
    if job.source != "boss" or not job.source_url:
        return False
    parsed = urlsplit(job.source_url)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "www.zhipin.com"
        and parsed.query == ""
        and parsed.fragment == ""
        and parsed.path.startswith("/job_detail/")
        and parsed.path.endswith(".html")
        and bool(job.external_id)
        and parsed.path == f"/job_detail/{job.external_id}.html"
    )


def all_jobs(db: Session) -> list[Job]:
    return list(db.scalars(select(Job).order_by(Job.id.asc())))


def eligible_jobs(db: Session) -> list[Job]:
    return [job for job in all_jobs(db) if _eligible(job)][:MAX_PLAN_JOBS]


def fingerprint(jobs: list[Job]) -> str:
    material = "|".join(
        f"{job.id}:{job.updated_at.isoformat() if job.updated_at else ''}:{job.external_id}"
        for job in jobs
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def plan(db: Session) -> dict:
    # "Present" means a salary a human can actually read - a stored placeholder
    # string counts as missing, exactly as `_eligible` treats it.
    jobs_all = all_jobs(db)
    total = len(jobs_all)
    present = sum(1 for job in jobs_all if is_valid_salary_text(job.salary_text))
    jobs = [job for job in jobs_all if _eligible(job)][:MAX_PLAN_JOBS]
    missing = total - present
    return {
        "total_jobs": total,
        "salary_present": present,
        "salary_missing": missing,
        "eligible_jobs": len(jobs),
        "ineligible_jobs": max(0, missing - len(jobs)),
        "fingerprint": fingerprint(jobs),
        "items": jobs,
    }


def create_run(db: Session, *, job_ids: list[int], expected_fingerprint: str, confirmed: bool) -> SalaryBackfillRun:
    if not confirmed:
        raise ValidationError("必须明确确认薪资回填计划。")
    if len(job_ids) != len(set(job_ids)):
        raise ValidationError("回填计划不能包含重复岗位。")
    active = db.scalar(select(SalaryBackfillRun).where(SalaryBackfillRun.state.in_(OPEN_STATES)))
    if active:
        raise ValidationError("已有未结束的薪资回填计划。", detail={"run_id": active.id})
    eligible = eligible_jobs(db)
    available = {job.id: job for job in eligible}
    if set(job_ids) != set(available):
        raise ValidationError("岗位列表已变化，请刷新计划后重新确认。")
    if fingerprint(eligible) != expected_fingerprint:
        raise ValidationError("岗位薪资状态已变化，请刷新计划后重新确认。")
    selected = [available[job_id] for job_id in job_ids]
    run = SalaryBackfillRun(state="pending", total_jobs=len(selected), last_action="plan_created")
    db.add(run)
    db.flush()
    for position, job in enumerate(selected):
        db.add(SalaryBackfillItem(run_id=run.id, job_id=job.id, position=position, state="pending"))
    db.commit()
    return get_run(db, run.id)


def get_run(db: Session, run_id: int) -> SalaryBackfillRun:
    run = db.scalar(
        select(SalaryBackfillRun)
        .options(selectinload(SalaryBackfillRun.items).selectinload(SalaryBackfillItem.job))
        .where(SalaryBackfillRun.id == run_id)
    )
    if not run:
        raise NotFoundError("薪资回填计划不存在。", detail={"run_id": run_id})
    return run


def start_or_resume(db: Session, run_id: int) -> SalaryBackfillRun:
    run = get_run(db, run_id)
    if run.state not in {"pending", "paused"}:
        raise ValidationError("当前回填计划不能开始或恢复。", detail={"state": run.state})
    run.state = "running"
    run.session_processed = 0
    run.paused_reason = None
    run.last_error = None
    run.last_action = "resumed" if run.started_at else "started"
    run.started_at = run.started_at or datetime.now(timezone.utc)
    db.commit()
    return get_run(db, run_id)


def authorize_remaining(db: Session, run_id: int, *, confirmed: bool) -> SalaryBackfillRun:
    """Apply one explicit finite authorization to the current run's remaining jobs.

    Allowed on a run that is paused *or* still ``pending``: authorizing before
    the first start is what lets a small plan run as one continuous session
    instead of a string of three-job batches. It is still one human click, still
    scoped to this run, and still bounded by ``MAX_PLAN_JOBS`` - every pause
    condition (login, verification, foreground loss, worker error, manual pause)
    is untouched.
    """
    if not confirmed:
        raise ValidationError("必须明确确认处理本计划的剩余岗位。")
    run = get_run(db, run_id)
    if run.state not in {"pending", "paused"} or run.current_job_id is not None:
        raise ValidationError("只有未开始或已暂停、且没有处理中岗位的计划可以授权剩余全部。")
    remaining = run.total_jobs - run.processed_jobs
    if remaining < 1:
        raise ValidationError("当前计划没有待处理岗位。")
    run.session_cap = min(remaining, MAX_PLAN_JOBS)
    run.last_action = "remaining_authorized"
    db.commit()
    return get_run(db, run_id)


def claim_next(db: Session, run_id: int) -> SalaryBackfillRun:
    run = get_run(db, run_id)
    if run.state != "running":
        raise ValidationError("回填计划未在运行。")
    if run.current_job_id is None:
        item = next((item for item in run.items if item.state == "pending"), None)
        if item is None:
            run.state = "completed"
            run.stopped_at = datetime.now(timezone.utc)
            run.last_action = "completed"
        elif run.session_processed >= run.session_cap:
            run.state = "paused"
            run.paused_reason = "batch_limit"
            run.last_action = "paused_batch_limit"
        else:
            run.current_job_id = item.job_id
            run.last_action = "claimed_job"
        db.commit()
    return get_run(db, run_id)


def record_item(db: Session, run_id: int, *, job_id: int, outcome: str, reason: str | None) -> SalaryBackfillRun:
    run = get_run(db, run_id)
    if run.state != "running" or run.current_job_id != job_id:
        raise ValidationError("回填岗位与当前计划状态不一致。")
    item = next((item for item in run.items if item.job_id == job_id), None)
    if not item or item.state != "pending":
        raise ValidationError("该岗位已处理或不属于本计划。")
    if outcome == "updated" and not is_valid_salary_text(item.job.salary_text):
        raise ValidationError("岗位薪资尚未通过 canonical intake 补齐，不能标记成功。")
    item.state = outcome
    item.reason = (reason or None)
    item.processed_at = datetime.now(timezone.utc)
    run.processed_jobs += 1
    run.session_processed += 1
    if outcome == "updated":
        run.updated_jobs += 1
    elif outcome == "unavailable":
        run.unavailable_jobs += 1
    else:
        run.failed_jobs += 1
    run.current_job_id = None
    run.last_action = f"item_{outcome}"
    run.last_error = reason if outcome == "failed" else None
    db.commit()
    return claim_next(db, run_id)


def pause(db: Session, run_id: int, *, reason: str) -> SalaryBackfillRun:
    run = get_run(db, run_id)
    if run.state != "running":
        raise ValidationError("只有运行中的回填计划可以暂停。")
    run.state = "paused"
    run.paused_reason = reason
    run.last_action = f"paused_{reason}"
    db.commit()
    return get_run(db, run_id)


def cancel(db: Session, run_id: int) -> SalaryBackfillRun:
    run = get_run(db, run_id)
    if run.state == "cancelled":
        return run
    if run.state not in OPEN_STATES:
        raise ValidationError("已结束的回填计划不能取消。")
    run.state = "cancelled"
    run.current_job_id = None
    run.paused_reason = None
    run.stopped_at = datetime.now(timezone.utc)
    run.last_action = "cancelled"
    db.commit()
    return get_run(db, run_id)
