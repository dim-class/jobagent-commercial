"""M6 loopback API for one human-confirmed application attempt.

These endpoints only persist and validate the approval gate.  They never drive
a browser.  Outcome recording additionally requires a Chrome-extension origin,
so the local console cannot claim it observed a recruitment-site result.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from app.api.routes.extension import ForbiddenError, re_extension_origin, require_loopback
from app.core.config import get_settings
from app.db.session import get_db
from app.schemas.application import (
    ApplicationApprovalAbandonRequest,
    ApplicationApprovalCheckOut,
    ApplicationApprovalCheckRequest,
    ApplicationApprovalCreate,
    ApplicationApprovalOut,
    ApplicationApprovalOutcomeRequest,
)
from app.services import application_approval

router = APIRouter(prefix="/api/application-approvals", tags=["application-execution"])


def _require_feature() -> None:
    if not get_settings().human_confirmed_apply_enabled:
        raise ForbiddenError("逐岗位人工确认投递功能尚未启用。")


def _require_extension(request: Request) -> None:
    origin = request.headers.get("origin", "")
    if not origin or not re_extension_origin(origin):
        raise ForbiddenError("投递执行结果只接受本机 JobAgent 扩展上报。")


@router.post("/jobs/{job_id}", response_model=ApplicationApprovalOut)
def create_approval(
    request: Request,
    job_id: int,
    payload: ApplicationApprovalCreate = Body(...),
    db: Session = Depends(get_db),
) -> ApplicationApprovalOut:
    require_loopback(request)
    _require_feature()
    return application_approval.request_approval(
        db,
        job_id,
        resume_id=payload.resume_id,
        answers=payload.answers_text,
        answers_source=payload.answers_source,
        confirmed=payload.confirmed,
    )


@router.get("/{approval_id}", response_model=ApplicationApprovalOut)
def get_approval(
    request: Request, approval_id: int, db: Session = Depends(get_db)
) -> ApplicationApprovalOut:
    require_loopback(request)
    _require_feature()
    return application_approval.get_approval(db, approval_id)


@router.post("/{approval_id}/validate", response_model=ApplicationApprovalCheckOut)
def validate_approval(
    request: Request,
    approval_id: int,
    payload: ApplicationApprovalCheckRequest = Body(...),
    db: Session = Depends(get_db),
) -> ApplicationApprovalCheckOut:
    require_loopback(request)
    _require_feature()
    _require_extension(request)
    approval = application_approval.get_approval(db, approval_id)
    check = application_approval.validate(
        db,
        approval,
        observed_url=payload.observed_url,
        observed_external_id=payload.observed_external_id,
    )
    return ApplicationApprovalCheckOut(ok=check.ok, reason=check.reason, approval=approval)


@router.post("/{approval_id}/begin", response_model=ApplicationApprovalOut)
def begin_attempt(
    request: Request,
    approval_id: int,
    payload: ApplicationApprovalCheckRequest = Body(...),
    db: Session = Depends(get_db),
) -> ApplicationApprovalOut:
    """Atomically consume the one permitted attempt before any page click."""
    require_loopback(request)
    _require_feature()
    _require_extension(request)
    return application_approval.begin_attempt(
        db,
        approval_id,
        observed_url=payload.observed_url,
        observed_external_id=payload.observed_external_id,
    )


@router.post("/{approval_id}/abandon", response_model=ApplicationApprovalOut)
def abandon_attempt(
    request: Request,
    approval_id: int,
    payload: ApplicationApprovalAbandonRequest = Body(...),
    db: Session = Depends(get_db),
) -> ApplicationApprovalOut:
    """Close out a claimed attempt whose result never came back.

    Deliberately NOT extension-gated: the whole point is that the extension
    stopped answering. It is the human, who can look at the site themselves,
    reporting that the result is unknown - it can never record success, and it
    never retries.
    """
    require_loopback(request)
    _require_feature()
    return application_approval.abandon_attempt(
        db, approval_id, confirmed=payload.confirmed
    )


@router.post("/{approval_id}/outcome", response_model=ApplicationApprovalOut)
def record_outcome(
    request: Request,
    approval_id: int,
    payload: ApplicationApprovalOutcomeRequest = Body(...),
    db: Session = Depends(get_db),
) -> ApplicationApprovalOut:
    require_loopback(request)
    _require_feature()
    _require_extension(request)
    return application_approval.record_outcome(
        db,
        approval_id,
        outcome=payload.outcome,
        detail=payload.detail,
        observed_url=payload.observed_url,
        observed_external_id=payload.observed_external_id,
    )
