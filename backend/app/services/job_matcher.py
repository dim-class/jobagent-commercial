"""Orchestration for a single job x resume analysis.

Flow:
    load active resume + strategy + job
        -> deterministic pre-analysis          (app.services.scoring)
        -> cache lookup by stable cache_key    (app.services.hashing)
        -> JobMatchAgent                       (app.agents.job_match_agent)
        -> guardrails                          (app.services.scoring)
        -> persist JobAnalysis + ApplicationEvent

Caching is mandatory (see CLAUDE.md): the key covers resume content, career
strategy, normalized JD, model and prompt version, so any change to any of
those produces a fresh analysis while a repeated click costs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.job_match_agent import run_job_match
from app.agents.prompts import PROMPT_VERSION
from app.core.career_strategy import load_strategy, strategy_hash
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.models import ApplicationEvent, EventType, Job, JobAnalysis, JobStatus, Resume
from app.schemas.analysis import JobMatchResult
from app.services.hashing import analysis_cache_key, hash_text
from app.services.scoring import PreAnalysis, apply_guardrails, compute_pre_analysis

logger = get_logger(__name__)

GREETING_MAX_CHARS = 220

# ``JobAnalysis.result_json`` stores the model result plus this side-car key, so
# the deterministic features that produced it survive with the row.
PRE_ANALYSIS_KEY = "_pre_analysis"


@dataclass(slots=True)
class AnalysisOutcome:
    analysis: JobAnalysis
    result: JobMatchResult
    pre_analysis: dict[str, Any]
    cached: bool


def get_active_resume(db: Session) -> Resume:
    resume = db.scalar(select(Resume).where(Resume.is_active.is_(True)).limit(1))
    if resume is None:
        resume = db.scalar(select(Resume).order_by(Resume.created_at.desc()).limit(1))
    if resume is None:
        raise ValidationError(
            "尚未上传简历，无法进行匹配分析。请先在「简历」页面上传 PDF 或 DOCX。",
            detail={"action": "upload_resume"},
        )
    return resume


def resolve_model(*, use_smart: bool, settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    return cfg.openai_model_smart if use_smart else cfg.openai_model_fast


def build_pre_analysis(job: Job, resume: Resume, strategy: dict[str, Any]) -> PreAnalysis:
    profile = resume.parsed_profile_json or {}
    resume_skills = list(profile.get("skills") or [])
    return compute_pre_analysis(
        title=job.title,
        company=job.company,
        city=job.city,
        salary_text=job.salary_text,
        experience_text=job.experience_text,
        normalized_description=job.normalized_description,
        strategy=strategy,
        resume_skills=resume_skills,
    )


def compute_cache_key(
    *, job: Job, resume: Resume, strategy: dict[str, Any], model: str
) -> str:
    return analysis_cache_key(
        resume_hash=resume.content_hash or hash_text(resume.raw_text),
        strategy_hash=strategy_hash(strategy),
        job_hash=job.content_hash,
        model=model,
        prompt_version=PROMPT_VERSION,
    )


def find_cached(db: Session, cache_key: str) -> JobAnalysis | None:
    return db.scalar(select(JobAnalysis).where(JobAnalysis.cache_key == cache_key).limit(1))


def result_from_row(analysis: JobAnalysis) -> JobMatchResult:
    """Rebuild the typed result from a stored row, dropping the side-car key."""
    payload = dict(analysis.result_json or {})
    payload.pop(PRE_ANALYSIS_KEY, None)
    return JobMatchResult.model_validate(payload)


def pre_analysis_from_row(analysis: JobAnalysis) -> dict[str, Any] | None:
    return (analysis.result_json or {}).get(PRE_ANALYSIS_KEY)


def latest_analysis(db: Session, job_id: int) -> JobAnalysis | None:
    return db.scalar(
        select(JobAnalysis)
        .where(JobAnalysis.job_id == job_id)
        .order_by(JobAnalysis.created_at.desc(), JobAnalysis.id.desc())
        .limit(1)
    )


def _sanitize(result: JobMatchResult, pre: PreAnalysis, strategy: dict[str, Any]) -> JobMatchResult:
    """Clamp scores, enforce strategy guardrails, tidy the greeting."""
    payload = apply_guardrails(result.model_dump(mode="json"), pre, strategy)

    greeting = (payload.get("greeting_message") or "").strip()
    if len(greeting) > GREETING_MAX_CHARS:
        greeting = greeting[:GREETING_MAX_CHARS].rstrip() + "…"
    payload["greeting_message"] = greeting

    for key in ("matched_skills", "missing_skills", "strengths", "gaps", "risk_flags"):
        seen: list[str] = []
        for item in payload.get(key) or []:
            text = str(item).strip()
            if text and text not in seen:
                seen.append(text)
        payload[key] = seen

    return JobMatchResult.model_validate(payload)


def get_resume_for_analysis(db: Session, resume_id: int | None) -> Resume:
    """Which resume to analyse against.

    ``None`` means the **active analysis resume** (v0.7 terminology). Passing an
    explicit id analyses against that variant instead - which is a different
    question from "which resume did I apply with", and never affects it.

    Archived variants are allowed here on purpose: comparing against a retired
    variant is a read-only question about fit, not a new application.
    """
    if resume_id is None:
        return get_active_resume(db)
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise NotFoundError(f"简历 {resume_id} 不存在", detail={"resume_id": resume_id})
    return resume


async def analyze_job(
    db: Session,
    job_id: int,
    *,
    force: bool = False,
    use_smart_model: bool = False,
    settings: Settings | None = None,
    resume_id: int | None = None,
    mark_reviewed: bool = True,
    no_retries: bool = False,
) -> AnalysisOutcome:
    """Analyze one job against a resume, using the cache unless forced.

    Defaults to the active analysis resume; pass ``resume_id`` to score the same
    JD against another variant. The cache key already covers resume content, so
    each variant gets its own cached row and re-checking one costs nothing.

    ``mark_reviewed`` defaults to ``True`` - a human looking directly at one
    job (the standalone ``/api/jobs/{id}/analyze`` family, batch analysis)
    has, by definition, now seen a machine opinion on it. Task-scoped bulk
    matching (``task_matching.run_task_match``) passes ``mark_reviewed=False``:
    scoring up to twenty candidates in the background is not the human
    reviewing any one of them, and M5a's contract is that it never touches
    ``Job.status`` at all - see ``services/task_matching.py``.
    """
    cfg = settings or get_settings()
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"岗位 {job_id} 不存在", detail={"job_id": job_id})

    resume = get_resume_for_analysis(db, resume_id)
    strategy = load_strategy()
    model = resolve_model(use_smart=use_smart_model, settings=cfg)
    cache_key = compute_cache_key(job=job, resume=resume, strategy=strategy, model=model)
    pre = build_pre_analysis(job, resume, strategy)

    if not force:
        cached = find_cached(db, cache_key)
        if cached is not None:
            log_event(
                logger,
                "analysis.cache_hit",
                job_id=job.id,
                resume_id=resume.id,
                model=model,
                cache_key=cache_key[:12],
            )
            return AnalysisOutcome(
                analysis=cached,
                result=result_from_row(cached),
                pre_analysis=pre.to_dict(),
                cached=True,
            )

    log_event(
        logger,
        "analysis.started",
        job_id=job.id,
        resume_id=resume.id,
        model=model,
        force=force,
        heuristic=pre.heuristic_score,
        jd_chars=len(job.normalized_description or ""),
    )

    raw_result = await run_job_match(
        model_name=model,
        resume_profile=resume.parsed_profile_json or {},
        resume_excerpt=resume.raw_text or "",
        strategy=strategy,
        job={
            "company": job.company,
            "title": job.title,
            "city": job.city,
            "salary_text": job.salary_text,
            "experience_text": job.experience_text,
            "education_text": job.education_text,
            "normalized_description": job.normalized_description,
        },
        pre_analysis=pre.to_dict(),
        settings=cfg,
        **({"no_retries": True} if no_retries else {}),
    )
    result = _sanitize(raw_result, pre, strategy)

    analysis = _persist(
        db,
        job=job,
        resume=resume,
        model=model,
        cache_key=cache_key,
        result=result,
        pre=pre,
        force=force,
        mark_reviewed=mark_reviewed,
    )

    log_event(
        logger,
        "analysis.completed",
        job_id=job.id,
        model=model,
        score=result.overall_score,
        verdict=result.verdict.value,
        heuristic=pre.heuristic_score,
        cache_key=cache_key[:12],
    )
    return AnalysisOutcome(
        analysis=analysis, result=result, pre_analysis=pre.to_dict(), cached=False
    )


def _persist(
    db: Session,
    *,
    job: Job,
    resume: Resume,
    model: str,
    cache_key: str,
    result: JobMatchResult,
    pre: PreAnalysis,
    force: bool,
    mark_reviewed: bool = True,
) -> JobAnalysis:
    """Upsert on ``cache_key`` (a forced re-run replaces the cached row)."""
    payload = result.model_dump(mode="json")
    payload[PRE_ANALYSIS_KEY] = pre.to_dict()

    analysis = find_cached(db, cache_key)
    if analysis is None:
        analysis = JobAnalysis(
            job_id=job.id,
            resume_id=resume.id,
            model=model,
            prompt_version=PROMPT_VERSION,
            cache_key=cache_key,
        )
        db.add(analysis)

    analysis.resume_id = resume.id
    analysis.model = model
    analysis.prompt_version = PROMPT_VERSION
    analysis.overall_score = result.overall_score
    analysis.verdict = result.verdict
    analysis.result_json = payload

    # The user has now seen a machine opinion on this job - but only when a
    # human is looking directly at this one job (see `analyze_job`'s
    # docstring). Never read-then-restore `job.status` here: `job` was loaded
    # before the awaited model call in `analyze_job`, so by the time we get
    # here a human could have changed the real status themselves, through a
    # different DB session, while this call was awaiting the model. Simply
    # never touching the column when `mark_reviewed` is false is what avoids
    # that lost-update - there is no snapshot to restore, and no window where
    # this function's own read of `job.status` could go stale and overwrite
    # a concurrent human write.
    if mark_reviewed and job.status == JobStatus.new:
        job.status = JobStatus.reviewed

    db.add(
        ApplicationEvent(
            job_id=job.id,
            event_type=EventType.analyzed,
            notes=(
                f"{model} -> {result.overall_score} 分 / {result.verdict.value}"
                f"（启发式基线 {pre.heuristic_score}）"
            ),
        )
    )
    db.commit()
    db.refresh(analysis)
    return analysis
