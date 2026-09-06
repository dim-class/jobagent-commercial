"""求职任务控制台 - attention subview (M2).

One thin, read-only aggregate endpoint. It composes existing services/routes
(application queue, recruiter inbox, interview board, offer board, job
analysis state) and writes nothing - no ``Job.status`` change, no AI call, no
new persistence. See docs/orchestration/ROADMAP.md - M2.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.console import (
    AiSettingsIn,
    AiSettingsOut,
    ConsoleAttentionOut,
    ReadinessOut,
)
from app.services import ai_settings, console_attention, console_readiness

router = APIRouter(prefix="/api/console", tags=["console"])


@router.get("/attention", response_model=ConsoleAttentionOut)
def get_attention(db: Session = Depends(get_db)) -> ConsoleAttentionOut:
    return console_attention.build_attention(db)


@router.get("/readiness", response_model=ReadinessOut)
def get_readiness(db: Session = Depends(get_db)) -> ReadinessOut:
    """What a fresh installation still needs. Free, read-only, no model call."""

    return console_readiness.build_readiness(db)


@router.get("/ai-settings", response_model=AiSettingsOut)
def get_ai_settings() -> AiSettingsOut:
    """Whether AI is configured, and against which endpoint. Never the key."""

    return AiSettingsOut(**ai_settings.describe())


@router.put("/ai-settings", response_model=AiSettingsOut)
def put_ai_settings(payload: AiSettingsIn = Body(...)) -> AiSettingsOut:
    """Write the credentials into `backend/.env` and use them immediately.

    The key still lives only in that file and in this process - it is never
    stored in the database, never logged, and never returned by this or any
    other endpoint.
    """

    values = {
        "OPENAI_API_KEY": payload.api_key,
        "OPENAI_BASE_URL": payload.base_url,
        "OPENAI_MODEL_FAST": payload.model_fast,
        "OPENAI_MODEL_SMART": payload.model_smart,
    }
    return AiSettingsOut(**ai_settings.save({k: v for k, v in values.items() if v is not None}))
