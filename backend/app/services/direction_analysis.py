"""AI-assisted search-direction analysis: the plan/confirm gate around it.

The shape follows `resume_comparison.py` and `task_matching.py`: a pure-read
plan that costs nothing, and one run that spends money and only with an
explicit `confirmed=true`. A cached result is free and is served automatically,
which is why the cache key covers every input that could change the answer.

This module never *ranks* anything. It fetches (or refreshes) the model's view;
`search_direction_ranking` combines it with real outcome evidence.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.direction_agent import run_direction_analysis
from app.agents.direction_prompts import DIRECTION_PROMPT_VERSION
from app.core.career_strategy import load_strategy, strategy_hash
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger, log_event
from app.models import Resume
from app.models.direction_analysis import ResumeDirectionAnalysis as CachedAnalysis
from app.schemas.direction import ResumeDirectionAnalysis
from app.services import search_keyword_analytics as keyword_analytics
from app.services.hashing import hash_json, hash_parts
from app.services.job_matcher import get_active_resume

logger = get_logger(__name__)


class NoResumeError(AppError):
    status_code = 400
    code = "no_active_resume"


class NotConfirmedError(AppError):
    status_code = 400
    code = "confirmation_required"


@dataclass(slots=True)
class DirectionPlan:
    """What an AI direction analysis would cost right now."""

    resume_id: int | None
    resume_name: str
    model: str
    candidates: list[str] = field(default_factory=list)
    cached: bool = False
    #: 0 when the answer is already cached, 1 otherwise. Never more: this is
    #: one call over the whole résumé, not one per direction.
    pending_calls: int = 0
    #: Present when cached, so the caller can render without spending.
    result: ResumeDirectionAnalysis | None = None
    openai_configured: bool = True


def _optional_active_resume(db: Session) -> Resume | None:
    """`get_active_resume` raises when there is none, which is right for an
    analysis request and wrong for a plan the console reads on every load."""
    try:
        return get_active_resume(db)
    except AppError:
        return None


def _candidates(strategy: dict) -> list[str]:
    roles = [str(r).strip() for r in (strategy.get("preferred_roles") or []) if str(r).strip()]
    return list(dict.fromkeys(roles))


def compute_cache_key(*, resume: Resume, strategy: dict, candidates: list[str], model: str) -> str:
    """Every input that can change the answer, and nothing that cannot.

    The candidate list is in the key because adding a direction to the strategy
    genuinely changes the question - `strategy_hash` already covers it, but
    listing it explicitly keeps the key honest if the two ever diverge.
    """
    return hash_parts(
        resume.content_hash,
        strategy_hash(strategy),
        hash_json(candidates),
        model.strip(),
        DIRECTION_PROMPT_VERSION,
    )


def find_cached(db: Session, cache_key: str) -> CachedAnalysis | None:
    return db.scalars(
        select(CachedAnalysis).where(CachedAnalysis.cache_key == cache_key)
    ).first()


def _history(db: Session, candidates: list[str]) -> dict[str, dict[str, object]]:
    """Past outcomes per direction, passed to the model as context only."""
    cohorts = {c.keyword: c for c in keyword_analytics.compute(db).cohorts}
    out: dict[str, dict[str, object]] = {}
    for keyword in candidates:
        cohort = cohorts.get(keyword)
        if cohort and cohort.jobs:
            out[keyword] = {"已搜岗位数": cohort.jobs, "其中被判定推荐": cohort.recommended}
    return out


def plan(db: Session) -> DirectionPlan:
    """Pure read. Calling this never spends anything."""
    cfg = get_settings()
    strategy = load_strategy()
    candidates = _candidates(strategy)
    resume = _optional_active_resume(db)
    if resume is None:
        return DirectionPlan(
            resume_id=None,
            resume_name="",
            model=cfg.openai_model_fast,
            candidates=candidates,
            openai_configured=cfg.openai_configured,
        )

    key = compute_cache_key(
        resume=resume, strategy=strategy, candidates=candidates, model=cfg.openai_model_fast
    )
    cached = find_cached(db, key)
    return DirectionPlan(
        resume_id=resume.id,
        resume_name=resume.variant_name or resume.filename,
        model=cfg.openai_model_fast,
        candidates=candidates,
        cached=cached is not None,
        pending_calls=0 if cached is not None else 1,
        result=(
            ResumeDirectionAnalysis.model_validate(cached.result_json) if cached else None
        ),
        openai_configured=cfg.openai_configured,
    )


def cached_analysis(db: Session) -> ResumeDirectionAnalysis | None:
    """The current cached view, or None. Never calls a model."""
    return plan(db).result


def run(db: Session, *, confirmed: bool, force: bool = False) -> DirectionPlan:
    """The one place this feature spends money. One call, and only on confirm."""
    if not confirmed:
        raise NotConfirmedError("需要显式确认后才会调用 AI 分析岗位方向。")

    # The key is required to *call* the model, not to read an answer already
    # paid for - so the cache is checked first.
    cfg = get_settings()
    strategy = load_strategy()
    candidates = _candidates(strategy)
    resume = _optional_active_resume(db)
    if resume is None:
        raise NoResumeError("没有设为「当前分析简历」的简历，请先在简历页上传或激活一份。")
    if not candidates:
        raise NoResumeError("职业策略里没有岗位方向，请先在设置中添加。")

    key = compute_cache_key(
        resume=resume, strategy=strategy, candidates=candidates, model=cfg.openai_model_fast
    )
    cached = find_cached(db, key)
    if cached is not None and not force:
        log_event(logger, "direction.cache_hit", resume_id=resume.id, candidates=len(candidates))
        return plan(db)

    # The key check lives in the agent, as it does for `job_matcher`: it is the
    # first thing `run_direction_analysis` does, so no network call can precede
    # it, and a test that stubs the agent is not blocked by it.
    result = asyncio.run(
        run_direction_analysis(
            model_name=cfg.openai_model_fast,
            resume_profile=resume.parsed_profile_json or {},
            resume_excerpt=resume.raw_text or "",
            strategy=strategy,
            candidates=candidates,
            history=_history(db, candidates),
            settings=cfg,
        )
    )

    payload = result.model_dump()
    if cached is not None:
        cached.result_json = payload
        cached.model = cfg.openai_model_fast
    else:
        db.add(
            CachedAnalysis(
                cache_key=key,
                resume_id=resume.id,
                model=cfg.openai_model_fast,
                prompt_version=DIRECTION_PROMPT_VERSION,
                result_json=payload,
            )
        )
    db.commit()
    log_event(
        logger,
        "direction.analyzed",
        resume_id=resume.id,
        model=cfg.openai_model_fast,
        candidates=len(candidates),
        suggested=len(result.suggested),
    )
    return plan(db)
