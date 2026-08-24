"""Dashboard aggregates."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.db.session import get_db
from app.models import Job, JobAnalysis, Resume
from app.schemas.common import DashboardSummary, ScoreBucket, TopJob
from app.services import application_metrics
from app.services.timezones import is_local_today

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_BUCKETS: list[tuple[str, int, int]] = [
    ("90-100", 90, 101),
    ("80-89", 80, 90),
    ("70-79", 70, 80),
    ("60-69", 60, 70),
    ("0-59", 0, 60),
]


def _latest(job: Job) -> JobAnalysis | None:
    if not job.analyses:
        return None
    return max(job.analyses, key=lambda a: (a.created_at, a.id))


@router.get("/summary", response_model=DashboardSummary)
def summary(
    db: Session = Depends(get_db),
    top_n: int = Query(default=8, ge=1, le=50),
) -> DashboardSummary:
    settings = get_settings()
    jobs = list(db.scalars(select(Job).options(selectinload(Job.analyses))).unique())

    counts = {"strong_apply": 0, "apply": 0, "maybe": 0, "skip": 0}
    by_status: dict[str, int] = {}
    by_city: dict[str, int] = {}
    scores: list[int] = []
    scored: list[tuple[Job, JobAnalysis]] = []

    for job in jobs:
        by_status[job.status.value] = by_status.get(job.status.value, 0) + 1
        city = job.city or "未标注"
        by_city[city] = by_city.get(city, 0) + 1

        analysis = _latest(job)
        if analysis is None:
            continue
        scored.append((job, analysis))
        scores.append(analysis.overall_score)
        counts[analysis.verdict.value] = counts.get(analysis.verdict.value, 0) + 1

    buckets = [
        ScoreBucket(label=label, count=sum(1 for s in scores if low <= s < high))
        for label, low, high in _BUCKETS
    ]

    scored.sort(key=lambda pair: pair[1].overall_score, reverse=True)
    top_jobs = [
        TopJob(
            job_id=job.id,
            company=job.company,
            title=job.title,
            city=job.city,
            salary_text=job.salary_text,
            overall_score=analysis.overall_score,
            verdict=analysis.verdict.value,
        )
        for job, analysis in scored[:top_n]
    ]

    active_resume = db.scalar(select(Resume).where(Resume.is_active.is_(True)).limit(1))

    # v0.4 funnel. Deterministic counts over what the human actually did.
    metrics = application_metrics.compute_metrics(db)
    from app.models import ApplicationEvent, EventType

    applied_today = sum(
        1
        for e in db.scalars(select(ApplicationEvent).where(
            ApplicationEvent.event_type == EventType.applied
        ))
        if is_local_today(e.created_at)
    )

    return DashboardSummary(
        total_jobs=len(jobs),
        analyzed_jobs=len(scored),
        unanalyzed_jobs=len(jobs) - len(scored),
        recommended=counts["strong_apply"] + counts["apply"],
        strong_apply=counts["strong_apply"],
        apply=counts["apply"],
        maybe=counts["maybe"],
        skip=counts["skip"],
        average_score=round(sum(scores) / len(scores), 1) if scores else None,
        by_status=by_status,
        by_city=dict(sorted(by_city.items(), key=lambda kv: -kv[1])),
        score_buckets=buckets,
        top_jobs=top_jobs,
        active_resume_id=active_resume.id if active_resume else None,
        openai_configured=settings.openai_configured,
        funnel=metrics.counts.model_dump(),
        rates=metrics.rates.model_dump(),
        daily_target=settings.daily_application_target,
        applied_today=applied_today,
    )
