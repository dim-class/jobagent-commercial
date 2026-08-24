"""Quick Capture endpoints (v0.3).

    POST /api/quick-capture/text/parse    parse pasted page text
    POST /api/quick-capture/image/parse   parse an uploaded screenshot
    POST /api/quick-capture/confirm       save the user-edited candidate

The human browses recruitment sites in their own browser and hands us the
content. These endpoints never fetch a URL, never contact a recruitment site,
and never save anything before the user confirms it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.schemas.quick_capture import (
    ConfirmRequest,
    ConfirmResponse,
    ParseResponse,
    TextParseRequest,
)
from app.services import quick_capture

logger = get_logger(__name__)
router = APIRouter(prefix="/api/quick-capture", tags=["quick-capture"])


@router.post("/text/parse", response_model=ParseResponse)
async def parse_text(payload: TextParseRequest) -> ParseResponse:
    """Deterministic parse, with one AI pass only when it is not good enough.

    Nothing is saved. The response is a proposal for the user to review.
    """
    outcome = await quick_capture.parse_text(
        payload.text,
        source_url=payload.source_url,
        allow_ai=payload.allow_ai,
    )
    return ParseResponse(
        candidate=outcome.candidate,
        ai_used=outcome.ai_used,
        ai_available=outcome.ai_available,
        ai_error=outcome.ai_error,
        message=outcome.message,
    )


@router.post("/image/parse", response_model=ParseResponse)
async def parse_image(
    file: UploadFile = File(..., description="职位页面截图 PNG / JPG / JPEG / WEBP"),
    source_url: str | None = Form(default=None),
) -> ParseResponse:
    """Extract a job from a screenshot the user uploaded.

    The image is validated, hashed, sent for extraction and then dropped. It is
    never written to disk and its bytes are never logged.
    """
    payload = await file.read()
    outcome = await quick_capture.parse_image(
        payload,
        content_type=file.content_type,
        filename=file.filename,
        source_url=source_url,
    )
    return ParseResponse(
        candidate=outcome.candidate,
        ai_used=outcome.ai_used,
        ai_available=outcome.ai_available,
        ai_error=outcome.ai_error,
        message=outcome.message,
    )


@router.post("/confirm", response_model=ConfirmResponse)
def confirm(payload: ConfirmRequest, db: Session = Depends(get_db)) -> ConfirmResponse:
    """Save the candidate exactly as the user edited it.

    A duplicate is a normal outcome, not an error: the existing job id comes
    back so the UI can offer to open it.
    """
    outcome = quick_capture.confirm_candidate(db, payload.candidate)
    job = outcome.job

    # Reuse the job routes' serialisation so a quick-captured job looks
    # identical to every other job.
    from app.api.routes.jobs import _to_detail

    return ConfirmResponse(
        job_id=job.id,
        duplicate=outcome.duplicate,
        job=_to_detail(job),
        message=(
            f"该岗位已存在（{job.company} · {job.title}）"
            if outcome.duplicate
            else f"已保存：{job.company or '未知公司'} · {job.title}"
        ),
    )


@router.get("/settings", response_model=dict)
def quick_capture_settings() -> dict:
    """Non-secret capability flags so the UI can adapt (no keys are returned)."""
    cfg = get_settings()
    return {
        "ai_extraction_enabled": cfg.quick_capture_ai_extraction,
        "openai_configured": cfg.openai_configured,
        "max_image_mb": cfg.quick_capture_max_image_mb,
        "accepted_image_types": sorted(set(quick_capture.ALLOWED_IMAGE_TYPES)),
    }
