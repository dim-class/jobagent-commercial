"""Settings + career-strategy endpoints.

The API key is never returned here - only whether one is configured.
"""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.core import career_strategy as strategy_store
from app.core.config import get_settings
from app.schemas.common import SettingsResponse
from app.schemas.strategy import CareerStrategy, CareerStrategyResponse

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsResponse)
def read_settings() -> SettingsResponse:
    """Non-secret runtime configuration for the 设置 page."""
    cfg = get_settings()
    return SettingsResponse(
        openai_configured=cfg.openai_configured,
        model_fast=cfg.openai_model_fast,
        model_smart=cfg.openai_model_smart,
        database_url=cfg.database_url,
        max_analyses_per_run=cfg.max_analyses_per_run,
        auto_apply=cfg.auto_apply_enabled,
        prompt_version=cfg.prompt_version,
        strategy_path=str(strategy_store.strategy_path()),
        version=__version__,
    )


@router.get("/career-strategy", response_model=CareerStrategyResponse)
def read_career_strategy() -> CareerStrategyResponse:
    data = strategy_store.load_strategy()
    return CareerStrategyResponse(
        strategy=CareerStrategy.model_validate(data),
        path=str(strategy_store.strategy_path()),
        hash=strategy_store.strategy_hash(data),
    )


@router.put("/career-strategy", response_model=CareerStrategyResponse)
def update_career_strategy(payload: CareerStrategy) -> CareerStrategyResponse:
    """Persist the strategy back to ``config/career_strategy.yaml``.

    Changing the strategy changes the analysis cache key, so previously
    analyzed jobs will be re-analyzed on their next 分析 click.
    """
    saved = strategy_store.save_strategy(payload.model_dump(mode="json"))
    return CareerStrategyResponse(
        strategy=CareerStrategy.model_validate(saved),
        path=str(strategy_store.strategy_path()),
        hash=strategy_store.strategy_hash(saved),
    )
