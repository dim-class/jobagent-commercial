"""求职任务控制台 service (M1).

Every function here only reads or writes ``JobSearchTask`` / ``TaskCandidate``
rows. Nothing here calls an AI model, captures a posting, or touches
``Job.status`` - candidate association is an explicit, human-driven action on
a job that already exists (captured through an existing, unchanged intake
path), never a search or a scrape. See docs/orchestration/ROADMAP.md.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.models import Job, JobSearchTask, TaskCandidate, TaskMode


def create_task(
    db: Session,
    *,
    name: str,
    keywords: str | None = None,
    city: str | None = None,
    experience_text: str | None = None,
    education_text: str | None = None,
    salary_min: int | None = None,
    salary_max: int | None = None,
    exclusions: list[str] | None = None,
    resume_id: int | None = None,
    max_candidates: int | None = None,
    min_score: int | None = None,
    mode: TaskMode = TaskMode.manual_review_only,
    notes: str | None = None,
) -> JobSearchTask:
    """Persist exactly the criteria given. Omitted optionals stay absent.

    ``exclusions=None`` (the caller never set this criterion) and
    ``exclusions=[]`` (the caller explicitly said "no exclusions") are
    stored as distinct values - ``NULL`` vs. an empty JSON array - never
    collapsed into the same thing.
    """
    task = JobSearchTask(
        name=name,
        keywords=keywords,
        city=city,
        experience_text=experience_text,
        education_text=education_text,
        salary_min=salary_min,
        salary_max=salary_max,
        exclusions_json=list(exclusions) if exclusions is not None else None,
        resume_id=resume_id,
        max_candidates=max_candidates,
        min_score=min_score,
        mode=mode,
        notes=notes,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def get_task(db: Session, task_id: int) -> JobSearchTask:
    task = db.get(JobSearchTask, task_id)
    if task is None:
        raise NotFoundError(f"任务 {task_id} 不存在", detail={"task_id": task_id})
    return task


def list_tasks(db: Session, *, limit: int = 100) -> list[JobSearchTask]:
    stmt = select(JobSearchTask).order_by(JobSearchTask.created_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


def add_candidate(db: Session, task_id: int, job_id: int) -> TaskCandidate:
    """Attach an already-captured job to a task.

    Idempotent: attaching the same job to the same task twice returns the
    existing association unchanged rather than creating a duplicate row,
    raising, or being blocked by ``max_candidates`` - a re-association adds
    no new candidate, so the cap only applies to a genuinely new one. Never
    creates or fetches a job - the job must already exist, captured through
    an existing intake path.
    """
    task = get_task(db, task_id)
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"岗位 {job_id} 不存在", detail={"job_id": job_id})

    existing = db.scalar(
        select(TaskCandidate).where(
            TaskCandidate.task_id == task_id, TaskCandidate.job_id == job_id
        )
    )
    if existing is not None:
        return existing

    if task.max_candidates is not None:
        current_count = (
            db.scalar(
                select(func.count())
                .select_from(TaskCandidate)
                .where(TaskCandidate.task_id == task_id)
            )
            or 0
        )
        if current_count >= task.max_candidates:
            raise ValidationError(
                f"任务「{task.name}」已达到候选上限（{task.max_candidates}），"
                "无法关联新的岗位。",
                detail={"task_id": task_id, "max_candidates": task.max_candidates},
            )

    association = TaskCandidate(task_id=task_id, job_id=job_id)
    db.add(association)
    db.commit()
    db.refresh(association)
    return association


def list_candidates(db: Session, task_id: int) -> list[TaskCandidate]:
    get_task(db, task_id)
    stmt = (
        select(TaskCandidate)
        .where(TaskCandidate.task_id == task_id)
        .order_by(TaskCandidate.created_at.desc())
    )
    return list(db.scalars(stmt).all())
