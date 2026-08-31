"""M5a: explicit, task-scoped candidate matching + human review.

This is deliberately the smallest useful slice: score a task's *existing*
`TaskCandidate` associations against the active analysis resume, using the
same cache/guardrail pipeline every other analysis path already uses. It
never searches, captures, applies, favorites, messages, or drives a browser,
and it never calls the smart model automatically - only the fast model, the
same one bulk analysis always uses (CLAUDE.md: "Never call both automatically
for one job").

Cost discipline, same shape as `resume_comparison.py`:

* reading the plan is free - it only reads analyses that already exist;
* running the missing ones is an explicit, confirmed action, and the caller
  is told beforehand exactly how many calls that will cost;
* the configured `settings.max_analyses_per_run` bounds one confirmed run -
  a task with more pending candidates than the cap needs another confirmed
  click to keep going, never a silent unbounded loop of paid calls. Read
  from `Settings` (never hard-coded - CLAUDE.md: "Read from config, never
  hardcoded"), so lowering it in `.env`/tests genuinely lowers what one run
  will do.

No parallel persistence path: every score still lands in the same
`JobAnalysis` table via `job_matcher.analyze_job`, and this module writes
nothing else. `Job.status` is never touched here - see `application_workflow`.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.career_strategy import load_strategy
from app.core.config import Settings, get_settings
from app.core.errors import AppError, ValidationError
from app.models import Job, JobAnalysis, JobSearchTask, Resume
from app.services import task_console
from app.services.ai_diagnostics import classify_openai_error
from app.services.job_matcher import (
    analyze_job,
    compute_cache_key,
    find_cached,
    get_active_resume,
    resolve_model,
)

@dataclass(slots=True)
class CandidateMatchPlan:
    """One task candidate and whether scoring it would cost an API call."""

    job: Job
    cache_key: str
    cached: JobAnalysis | None

    @property
    def needs_api_call(self) -> bool:
        return self.cached is None


@dataclass(slots=True)
class CandidateMatchOutcome:
    """The result of actually running (or skipping) one candidate.

    On failure, `error` is always a safe, actionable Chinese message; the
    remaining fields are populated only when the failure came from a
    classified upstream/model call (see `ai_diagnostics.classify_openai_error`)
    and are never derived from a raw exception body or response.
    """

    job_id: int
    cached: bool
    analysis: JobAnalysis | None
    error: str | None = None
    category: str | None = None
    http_status: int | None = None
    error_code: str | None = None
    request_id: str | None = None


def plan_task_match(
    db: Session, task_id: int, *, settings: Settings | None = None
) -> tuple[JobSearchTask, Resume, str, list[CandidateMatchPlan]]:
    """Work out which of this task's candidates are already scored.

    Pure read - never calls OpenAI, never writes a row. Always the **active**
    analysis resume and the **fast** model, exactly like bulk analysis; this
    never auto-runs the smart model.
    """
    task = task_console.get_task(db, task_id)
    associations = task_console.list_candidates(db, task_id)
    resume, model, plans = plan_jobs(
        db, [association.job for association in associations], settings=settings
    )
    return task, resume, model, plans


def plan_jobs(
    db: Session, jobs: list[Job], *, settings: Settings | None = None
) -> tuple[Resume, str, list[CandidateMatchPlan]]:
    """Build the canonical active-resume/fast-model cache plan for jobs.

    M5b reuses this exact primitive after it has resolved and deduplicated an
    explicit set of task associations.  It remains a pure read and preserves
    M5a's single cache-key implementation.
    """
    cfg = settings or get_settings()
    resume = get_active_resume(db)
    strategy = load_strategy()
    model = resolve_model(use_smart=False, settings=cfg)
    plans: list[CandidateMatchPlan] = []
    for job in jobs:
        cache_key = compute_cache_key(job=job, resume=resume, strategy=strategy, model=model)
        plans.append(
            CandidateMatchPlan(job=job, cache_key=cache_key, cached=find_cached(db, cache_key))
        )
    return resume, model, plans


def pending_call_count(plans: list[CandidateMatchPlan]) -> int:
    return sum(1 for plan in plans if plan.needs_api_call)


def max_analyses_per_run(settings: Settings | None = None) -> int:
    """The configured cap - never hard-coded, read fresh each call so a
    lowered test/deployment setting always takes effect."""
    return (settings or get_settings()).max_analyses_per_run


async def run_task_match(
    db: Session,
    task_id: int,
    *,
    confirmed: bool,
    settings: Settings | None = None,
) -> tuple[JobSearchTask, list[CandidateMatchOutcome]]:
    """Score every not-yet-cached candidate, up to the configured
    `settings.max_analyses_per_run`.

    Rejects outright (writes nothing, spends nothing) if there is pending
    paid work and the caller has not confirmed it. Each candidate fails
    independently - one bad job never aborts the rest of the run, and every
    outcome (cached, freshly scored, or errored) is reported back.
    """
    cfg = settings or get_settings()
    cap = cfg.max_analyses_per_run
    task, _resume, _model, plans = plan_task_match(db, task_id, settings=cfg)
    pending = pending_call_count(plans)
    pending_this_run = min(pending, cap)

    if pending_this_run and not confirmed:
        raise ValidationError(
            f"将分析 {pending_this_run} 个尚未分析的候选岗位，可能产生 API 费用。请确认后再继续。",
            detail={
                "field": "confirmed",
                "pending_analyses": pending_this_run,
                "pending_total": pending,
                "cap": cap,
            },
        )

    outcomes: list[CandidateMatchOutcome] = []
    api_calls_made = 0
    for plan in plans:
        if not plan.needs_api_call:
            outcomes.append(
                CandidateMatchOutcome(job_id=plan.job.id, cached=True, analysis=plan.cached)
            )
            continue
        if api_calls_made >= cap:
            # Cap reached this run - leave it genuinely unanalyzed, never
            # silently skip while claiming success.
            outcomes.append(
                CandidateMatchOutcome(
                    job_id=plan.job.id,
                    cached=False,
                    analysis=None,
                    error="本次已达到分析数量上限，请再次确认以继续。",
                )
            )
            continue
        try:
            outcome = await analyze_job(
                db,
                plan.job.id,
                use_smart_model=False,
                settings=settings,
                # M5a never touches Job.status (see module docstring) - a
                # background bulk match across up to twenty candidates is not
                # a human reviewing any one of them, unlike the standalone
                # /api/jobs/{id}/analyze family this shares `_persist` with.
                mark_reviewed=False,
            )
            outcomes.append(
                CandidateMatchOutcome(job_id=plan.job.id, cached=False, analysis=outcome.analysis)
            )
        except AppError as exc:
            # `UpstreamError` already carries a classification in `detail`
            # (see `job_match_agent.run_job_match`); reuse it rather than
            # reclassifying, so there is one source of truth. Other AppErrors
            # (e.g. a missing API key) have no such fields - `exc.message` is
            # already a safe, actionable Chinese message on its own.
            detail = exc.detail or {}
            outcomes.append(
                CandidateMatchOutcome(
                    job_id=plan.job.id,
                    cached=False,
                    analysis=None,
                    error=exc.message,
                    category=detail.get("category"),
                    http_status=detail.get("http_status"),
                    error_code=detail.get("error_code"),
                    request_id=detail.get("request_id"),
                )
            )
        except Exception as exc:  # noqa: BLE001 - one bad job must not abort the run
            classification = classify_openai_error(exc)
            outcomes.append(
                CandidateMatchOutcome(
                    job_id=plan.job.id,
                    cached=False,
                    analysis=None,
                    error=classification.message,
                    category=classification.category,
                    http_status=classification.http_status,
                    error_code=classification.error_code,
                    request_id=classification.request_id,
                )
            )
        finally:
            api_calls_made += 1

    return task, outcomes
