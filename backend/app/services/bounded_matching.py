"""Opt-in sequencing only. All scoring/cache writes still use job_matcher.

One immutable approval per SearchTask, at most three distinct claims including
failed/uncertain calls. CAS claims are committed before awaiting the model.
No scheduler or retry: the extension explicitly advances one captured job.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agents.prompts import PROMPT_VERSION
from app.core.career_strategy import load_strategy, strategy_hash
from app.core.config import Settings, get_settings
from app.core.errors import ValidationError
from app.models import Job, JobAnalysis, JobSearchTask, Resume, SearchTaskRunStatus, TaskCandidate
from app.services import job_matcher, task_console
from app.services.hashing import hash_text


def quote(db: Session, task_id: int, cap: int, *, settings: Settings | None = None) -> dict:
    cfg = settings or get_settings()
    task = task_console.get_task(db, task_id)
    if not task.is_search_plan or type(cap) is not int or not 1 <= cap <= min(3, cfg.max_analyses_per_run, task.max_candidates or 20):
        raise ValidationError("自动匹配仅支持搜索计划，候选与分析上限为 1–3（并受配置上限约束）。")
    resume = job_matcher.get_active_resume(db)
    if resume.archived_at or not resume.is_active:
        raise ValidationError("请先明确选择一份未归档的当前分析简历。")
    model = job_matcher.resolve_model(use_smart=False, settings=cfg)
    fingerprint = hash_text(repr((task.id, task.city_id, task.city, task.keywords,
        task.max_candidates, task.min_score, resume.id, hash_text(resume.raw_text or ""),
        resume.parsed_profile_json, strategy_hash(load_strategy()), model, PROMPT_VERSION, cap)))
    return {"task_id": task_id, "cap": cap, "resume_id": resume.id,
            "resume_name": resume.variant_name or resume.filename, "model": model,
            "fingerprint": fingerprint}


def approve(db: Session, task_id: int, cap: int, confirmed: bool, fingerprint: str) -> dict:
    task = task_console.get_task(db, task_id)
    if task.run_status != SearchTaskRunStatus.pending or task.match_run_json:
        raise ValidationError("该任务已开始或已有授权；恢复不能补充额度。")
    plan = quote(db, task_id, cap)
    if confirmed is not True or fingerprint != plan["fingerprint"]:
        raise ValidationError("请重新确认当前简历、模型和最多分析数量（可能产生 API 费用）。")
    previous = list(db.scalars(select(TaskCandidate.job_id).where(TaskCandidate.task_id == task_id)))
    return {**plan, "excluded_job_ids": previous, "entries": [],
            "approved_at": datetime.now(timezone.utc).isoformat()}


def _save(db: Session, task: JobSearchTask, ledger: dict, *, require_running: bool = False) -> None:
    conditions = [JobSearchTask.id == task.id, JobSearchTask.match_revision == task.match_revision]
    if require_running:
        conditions.append(JobSearchTask.run_status == SearchTaskRunStatus.running)
    changed = db.execute(update(JobSearchTask).where(*conditions).values(
        match_run_json=ledger, match_revision=task.match_revision + 1
    ).execution_options(synchronize_session=False)).rowcount
    if changed != 1:
        db.rollback()
        raise ValidationError("任务状态已变化或匹配请求并发；未发起新的模型调用。")
    db.commit()
    db.refresh(task)


async def step(db: Session, task_id: int, job_id: int, canonical_url: str,
               *, settings: Settings | None = None) -> dict:
    cfg = settings or get_settings()
    task = task_console.get_task(db, task_id)
    db.refresh(task)
    ledger = deepcopy(task.match_run_json)
    if not ledger or task.run_status != SearchTaskRunStatus.running:
        raise ValidationError("未授权自动匹配或任务已暂停/结束，不会调用模型。")
    # Replaying an existing claim reads only; a crash/timeout never spends again.
    existing = next((e for e in ledger["entries"] if e["job_id"] == job_id), None)
    if existing:
        return existing
    if any(e["state"] == "running" for e in ledger["entries"]):
        raise ValidationError("有结果未确认的模型调用，请人工核对；不会自动重试或继续付费。")
    if len(ledger["entries"]) >= ledger["cap"]:
        raise ValidationError("本次自动匹配额度已用完（失败也占名额）。")
    current = quote(db, task_id, ledger["cap"], settings=cfg)
    if current["fingerprint"] != ledger["fingerprint"]:
        raise ValidationError("简历、模型、策略或任务条件已变化，请结束本次任务并重新确认费用。")
    job = db.get(Job, job_id)
    associated = db.scalar(select(TaskCandidate.id).where(
        TaskCandidate.task_id == task_id, TaskCandidate.job_id == job_id))
    if (not job or not associated or job_id in ledger["excluded_job_ids"]
            or not re.fullmatch(r"https://www\.zhipin\.com/job_detail/[A-Za-z0-9_-]+\.html", canonical_url)
            or job.source_url != canonical_url
            or not job.normalized_description.strip()):
        raise ValidationError("仅匹配本次新关联且身份一致、包含 JD 的岗位。")
    resume = job_matcher.get_resume_for_analysis(db, ledger["resume_id"])
    key = job_matcher.compute_cache_key(job=job, resume=resume, strategy=load_strategy(), model=ledger["model"])
    cached = job_matcher.find_cached(db, key)
    entry = {"job_id": job_id, "cache_key": key, "state": "done" if cached else "running",
             "cached": cached is not None, "analysis_id": cached.id if cached else None,
             "error": None}
    ledger["entries"].append(entry)
    _save(db, task, ledger, require_running=True)  # durable reservation BEFORE any paid await
    if cached:
        return entry
    try:
        outcome = await job_matcher.analyze_job(db, job_id, resume_id=ledger["resume_id"],
            settings=cfg, mark_reviewed=False, no_retries=True)
        entry.update(state="done", analysis_id=outcome.analysis.id, cached=outcome.cached)
    except Exception:  # safe, fixed diagnostic; never serialize upstream response/secrets
        db.rollback()
        entry.update(state="failed", error="匹配失败，已占用名额；未自动重试，请人工核对。")
    # Persist only our ledger. Never overwrite a concurrent pause/cancel or human Job.status.
    db.refresh(task)
    fresh = deepcopy(task.match_run_json)
    fresh["entries"] = [entry if e["job_id"] == job_id else e for e in fresh["entries"]]
    _save(db, task, fresh)
    return entry


def review_reasons(job: Job, analysis: JobAnalysis | None) -> list[str]:
    """Conservative review flags, not currency conversion or new scoring."""
    reasons = []
    missing = [name for name, value in (("公司", job.company), ("城市", job.city),
        ("薪资", job.salary_text), ("经验", job.experience_text), ("学历", job.education_text),
        ("JD", job.normalized_description)) if not (value or "").strip()]
    if missing:
        reasons.append("信息缺失：" + "、".join(missing))
    text = job.title + "\n" + job.normalized_description
    overseas = re.search(r"东京|大阪|日本|新加坡|香港|美国|澳大利亚|JPY|日元|日圆|USD|美元|SGD", text, re.I)
    if overseas:
        reasons.append("包含境外地点/币种信息：请核对实际工作地及薪资币种，不作跨币种比较。")
    locations = re.findall(r"(?:工作地点|办公地点|工作地|工作地址)[：:\s]+([^\n。；]{2,50})", text)
    if job.city and any(job.city not in location for location in locations):
        reasons.append("JD 工作地与列表城市可能不一致，请确认。")
    if analysis:
        reasons.extend(str(r) for r in (analysis.result_json or {}).get("risk_flags", []) if r)
    return list(dict.fromkeys(reasons))


def review(db: Session, task_id: int) -> dict:
    task = task_console.get_task(db, task_id)
    ledger = task.match_run_json
    if not ledger:
        return {"task_id": task_id, "enabled": False, "items": []}
    items = []
    for entry in ledger["entries"]:
        job = db.get(Job, entry["job_id"])
        if not job:
            continue
        analysis = db.get(JobAnalysis, entry["analysis_id"]) if entry["analysis_id"] else None
        reasons = review_reasons(job, analysis)
        # A changed JD must not inherit a recommendation for old facts.
        resume = db.get(Resume, ledger["resume_id"])
        current_key = job_matcher.compute_cache_key(job=job, resume=resume,
            strategy=load_strategy(), model=ledger["model"]) if resume else None
        if current_key != entry["cache_key"]:
            reasons.append("分析依据已变化，旧分数仅供参考；本次不会自动重算。")
        verdict = analysis.verdict.value if analysis else None
        bucket = "待确认" if reasons or not analysis else ("建议复核" if verdict in ("apply", "strong_apply") else "低匹配")
        items.append({"job_id": job.id, "title": job.title, "company": job.company,
            "score": analysis.overall_score if analysis else None, "verdict": verdict,
            "state": entry["state"], "cached": entry["cached"], "error": entry["error"],
            "bucket": bucket, "review_reasons": reasons,
            "summary": (analysis.result_json or {}).get("reasoning_summary", "") if analysis else ""})
    rank = {"建议复核": 0, "待确认": 1, "低匹配": 2}
    items.sort(key=lambda i: (rank[i["bucket"]], -(i["score"] if i["score"] is not None else -1), i["job_id"]))
    return {"task_id": task_id, "enabled": True, "state": task.run_status.value,
        "cap": ledger["cap"], "used": len(ledger["entries"]),
        "completed": sum(e["state"] == "done" for e in ledger["entries"]),
        "failed": sum(e["state"] == "failed" for e in ledger["entries"]),
        "uncertain": sum(e["state"] == "running" for e in ledger["entries"]),
        "resume_id": ledger["resume_id"], "model": ledger["model"], "items": items}
