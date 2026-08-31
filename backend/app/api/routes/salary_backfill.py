"""Loopback-only control plane for bounded historical salary maintenance."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.extension import require_loopback
from app.db.session import get_db
from app.models import SalaryBackfillRun
from app.schemas.salary_backfill import (
    SalaryBackfillCreateRequest,
    SalaryBackfillAuthorizeRemainingRequest,
    SalaryBackfillItemOut,
    SalaryBackfillItemResultRequest,
    SalaryBackfillPauseRequest,
    SalaryBackfillPlanItem,
    SalaryBackfillPlanOut,
    SalaryBackfillRunOut,
)
from app.services import salary_backfill

router = APIRouter(prefix="/api/jobs/salary-backfill", tags=["salary-backfill"])


def _run_out(run: SalaryBackfillRun) -> SalaryBackfillRunOut:
    return SalaryBackfillRunOut(
        id=run.id, state=run.state, total_jobs=run.total_jobs,
        processed_jobs=run.processed_jobs, updated_jobs=run.updated_jobs,
        unavailable_jobs=run.unavailable_jobs, failed_jobs=run.failed_jobs,
        session_processed=run.session_processed, session_cap=run.session_cap,
        current_job_id=run.current_job_id,
        paused_reason=run.paused_reason, last_action=run.last_action,
        last_error=run.last_error, created_at=run.created_at, updated_at=run.updated_at,
        items=[SalaryBackfillItemOut(
            job_id=item.job_id, company=item.job.company, title=item.job.title,
            source_url=item.job.source_url or "", position=item.position,
            state=item.state, reason=item.reason,
        ) for item in run.items],
    )


@router.get("/plan", response_model=SalaryBackfillPlanOut)
def get_plan(request: Request, db: Session = Depends(get_db)) -> SalaryBackfillPlanOut:
    require_loopback(request)
    value = salary_backfill.plan(db)
    return SalaryBackfillPlanOut(**{**value, "items": [
        SalaryBackfillPlanItem(job_id=job.id, company=job.company, title=job.title,
                               source_url=job.source_url or "")
        for job in value["items"]
    ]})


@router.get("/runs/active", response_model=SalaryBackfillRunOut | None)
def active_run(request: Request, db: Session = Depends(get_db)) -> SalaryBackfillRunOut | None:
    require_loopback(request)
    run_id = db.scalar(select(SalaryBackfillRun.id).where(
        SalaryBackfillRun.state.in_(salary_backfill.OPEN_STATES)).order_by(SalaryBackfillRun.id.desc()))
    return _run_out(salary_backfill.get_run(db, run_id)) if run_id else None


@router.post("/runs", response_model=SalaryBackfillRunOut)
def create_run(request: Request, payload: SalaryBackfillCreateRequest = Body(...),
               db: Session = Depends(get_db)) -> SalaryBackfillRunOut:
    require_loopback(request)
    return _run_out(salary_backfill.create_run(
        db, job_ids=payload.job_ids, expected_fingerprint=payload.fingerprint,
        confirmed=payload.confirmed))


@router.get("/runs/{run_id}", response_model=SalaryBackfillRunOut)
def get_run(request: Request, run_id: int, db: Session = Depends(get_db)) -> SalaryBackfillRunOut:
    require_loopback(request)
    return _run_out(salary_backfill.get_run(db, run_id))


@router.post("/runs/{run_id}/start", response_model=SalaryBackfillRunOut)
def start(request: Request, run_id: int, db: Session = Depends(get_db)) -> SalaryBackfillRunOut:
    require_loopback(request)
    return _run_out(salary_backfill.start_or_resume(db, run_id))


@router.post("/runs/{run_id}/authorize-remaining", response_model=SalaryBackfillRunOut)
def authorize_remaining(
    request: Request,
    run_id: int,
    payload: SalaryBackfillAuthorizeRemainingRequest = Body(...),
    db: Session = Depends(get_db),
) -> SalaryBackfillRunOut:
    require_loopback(request)
    return _run_out(salary_backfill.authorize_remaining(
        db, run_id, confirmed=payload.confirmed))


@router.post("/runs/{run_id}/claim", response_model=SalaryBackfillRunOut)
def claim(request: Request, run_id: int, db: Session = Depends(get_db)) -> SalaryBackfillRunOut:
    require_loopback(request)
    return _run_out(salary_backfill.claim_next(db, run_id))


@router.post("/runs/{run_id}/item", response_model=SalaryBackfillRunOut)
def item_result(request: Request, run_id: int, payload: SalaryBackfillItemResultRequest = Body(...),
                db: Session = Depends(get_db)) -> SalaryBackfillRunOut:
    require_loopback(request)
    return _run_out(salary_backfill.record_item(
        db, run_id, job_id=payload.job_id, outcome=payload.outcome, reason=payload.reason))


@router.post("/runs/{run_id}/pause", response_model=SalaryBackfillRunOut)
def pause(request: Request, run_id: int, payload: SalaryBackfillPauseRequest = Body(...),
          db: Session = Depends(get_db)) -> SalaryBackfillRunOut:
    require_loopback(request)
    return _run_out(salary_backfill.pause(db, run_id, reason=payload.reason))


@router.post("/runs/{run_id}/cancel", response_model=SalaryBackfillRunOut)
def cancel(request: Request, run_id: int, db: Session = Depends(get_db)) -> SalaryBackfillRunOut:
    require_loopback(request)
    return _run_out(salary_backfill.cancel(db, run_id))
