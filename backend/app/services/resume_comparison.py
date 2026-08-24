"""Comparing resume variants on the same JD (v0.7).

This is **AI fit**, not recruiter outcome. A high score means the model thinks a
variant matches a posting; it says nothing about whether an HR actually replied.
Those two questions live in different places on purpose - see
``application_analytics`` for the outcome side.

Cost rules, which are the reason this module exists separately:

* reading the matrix is free. It only reads analyses that already exist;
* an empty cell stays empty. Nothing here fills the grid automatically;
* running the missing combinations is an explicit, confirmed user action, and
  the caller is told beforehand exactly how many calls that will cost.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.career_strategy import load_strategy
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.models import Job, JobAnalysis, Resume
from app.services.job_matcher import (
    analyze_job,
    compute_cache_key,
    find_cached,
    resolve_model,
)

logger = get_logger(__name__)

#: Comparing more than a handful at once is a cost trap, not a feature.
MAX_COMPARE_VARIANTS = 5


@dataclass(slots=True)
class CellPlan:
    """One (job, resume) combination and whether it is already paid for."""

    resume: Resume
    cache_key: str
    cached: JobAnalysis | None

    @property
    def needs_api_call(self) -> bool:
        return self.cached is None


def _get_job(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"岗位 {job_id} 不存在", detail={"job_id": job_id})
    return job


def plan_comparison(
    db: Session,
    job_id: int,
    resume_ids: list[int],
    *,
    use_smart_model: bool = False,
    settings: Settings | None = None,
) -> tuple[Job, list[CellPlan]]:
    """Work out which combinations are cached and which would cost money.

    Pure read. Calling this never spends anything, which is what lets the UI
    show an honest "will analyse N variants" warning *before* confirming.
    """
    cfg = settings or get_settings()
    job = _get_job(db, job_id)

    unique_ids = list(dict.fromkeys(resume_ids))
    if not unique_ids:
        raise ValidationError("请至少选择一份简历。", detail={"field": "resume_ids"})
    if len(unique_ids) > MAX_COMPARE_VARIANTS:
        raise ValidationError(
            f"一次最多比较 {MAX_COMPARE_VARIANTS} 份简历。",
            detail={"field": "resume_ids", "max": MAX_COMPARE_VARIANTS},
        )

    strategy = load_strategy()
    model = resolve_model(use_smart=use_smart_model, settings=cfg)

    plans: list[CellPlan] = []
    for resume_id in unique_ids:
        resume = db.get(Resume, resume_id)
        if resume is None:
            raise NotFoundError(f"简历 {resume_id} 不存在", detail={"resume_id": resume_id})
        cache_key = compute_cache_key(job=job, resume=resume, strategy=strategy, model=model)
        plans.append(CellPlan(resume=resume, cache_key=cache_key, cached=find_cached(db, cache_key)))

    return job, plans


def pending_call_count(plans: list[CellPlan]) -> int:
    return sum(1 for plan in plans if plan.needs_api_call)


async def run_comparison(
    db: Session,
    job_id: int,
    resume_ids: list[int],
    *,
    confirmed: bool,
    use_smart_model: bool = False,
    settings: Settings | None = None,
) -> tuple[Job, list[CellPlan]]:
    """Fill in the missing cells - only with an explicit confirmation.

    Cached combinations are never re-run, so confirming a comparison that is
    already fully cached costs nothing and the confirmation is a formality.
    """
    job, plans = plan_comparison(
        db, job_id, resume_ids, use_smart_model=use_smart_model, settings=settings
    )
    pending = pending_call_count(plans)

    if pending and not confirmed:
        raise ValidationError(
            f"将分析 {pending} 份尚未分析的简历版本，可能产生 API 费用。请确认后再继续。",
            detail={"field": "confirmed", "pending_analyses": pending},
        )

    for plan in plans:
        if not plan.needs_api_call:
            continue
        outcome = await analyze_job(
            db,
            job_id,
            resume_id=plan.resume.id,
            use_smart_model=use_smart_model,
            settings=settings,
        )
        plan.cached = outcome.analysis

    log_event(
        logger,
        "resume.comparison_run",
        job_id=job_id,
        variants=len(plans),
        api_calls=pending,
    )
    return job, plans


# --------------------------------------------------------------------------
# the score matrix
# --------------------------------------------------------------------------


def analyses_for_job(db: Session, job_id: int) -> list[JobAnalysis]:
    """Every stored analysis of one job, newest first per resume."""
    return list(
        db.scalars(
            select(JobAnalysis)
            .where(JobAnalysis.job_id == job_id)
            .order_by(JobAnalysis.created_at.desc(), JobAnalysis.id.desc())
        )
    )


def latest_by_resume(analyses: list[JobAnalysis]) -> dict[int, JobAnalysis]:
    """One analysis per resume - the most recent, matching queue semantics."""
    out: dict[int, JobAnalysis] = {}
    for analysis in analyses:
        out.setdefault(analysis.resume_id, analysis)
    return out
