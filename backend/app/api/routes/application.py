"""Application queue + workflow endpoints (v0.4).

    GET  /api/application-queue                   today's proposals
    GET  /api/application-queue/metrics           funnel counts + rates
    GET  /api/jobs/{id}/application-events        full history
    POST /api/jobs/{id}/mark-applied              human confirms a real apply
    POST /api/jobs/{id}/skip | later | reset-status
    POST /api/jobs/{id}/reply | interview | offer | reject

Routes are thin: every status change goes through
``services.application_workflow``. No endpoint here contacts a recruitment
site - each one records something the human says they already did.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models import JobStatus, Verdict
from app.schemas.application import (
    AttributeResumeRequest,
    ApplicationEventOut,
    ApplicationMetrics,
    InterviewRequest,
    LaterRequest,
    MarkAppliedRequest,
    OfferRequest,
    QueueResponse,
    RejectRequest,
    ReplyRequest,
    ResetRequest,
    SkipRequest,
    SKIP_REASONS,
    WorkflowResponse,
)
from app.services import application_metrics, application_queue, application_workflow
from app.services.application_queue import QueueFilters

queue_router = APIRouter(prefix="/api/application-queue", tags=["application-queue"])
job_router = APIRouter(prefix="/api/jobs", tags=["application-workflow"])


# --------------------------------------------------------------------------
# queue
# --------------------------------------------------------------------------


@queue_router.get("", response_model=QueueResponse)
def get_queue(
    db: Session = Depends(get_db),
    city: str | None = Query(default=None),
    verdict: Verdict | None = Query(default=None),
    min_score: int | None = Query(default=None, ge=0, le=100),
    keyword: str | None = Query(default=None, description="职位/公司/技能关键词"),
    job_status: JobStatus | None = Query(default=None, alias="status"),
    source: str | None = Query(default=None),
    include_maybe: bool = Query(default=False, description="包含 Maybe 的岗位"),
    include_decided: bool = Query(default=False, description="包含已处理的岗位"),
    sort: str = Query(default="recommended", pattern="^(recommended|score|newest|salary)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> QueueResponse:
    """Today's proposals, derived from Job + latest analysis on every read."""
    settings = get_settings()
    filters = QueueFilters(
        city=city,
        verdict=verdict,
        min_score=min_score,
        keyword=keyword,
        status=job_status,
        source=source,
        include_maybe=include_maybe,
        include_decided=include_decided,
        sort=sort,
        limit=limit,
        offset=offset,
    )

    proposals = application_queue.all_proposals(db)
    eligible = [p for p in proposals if application_queue.is_eligible(p, filters)]
    # A facet drives its own dropdown, so it is computed with its own dimension
    # excluded. Counting cities over the already-city-filtered rows leaves the
    # menu holding only the city you picked, with no way back to the others.
    city_facet_source = (
        eligible
        if filters.city is None
        else [
            p
            for p in proposals
            if application_queue.is_eligible(p, replace(filters, city=None))
        ]
    )

    # Deferred jobs stay eligible but drop below the immediate work.
    from app.schemas.application import ProposalState

    due = [p for p in eligible if p.proposal_state is not ProposalState.later]
    deferred = [p for p in eligible if p.proposal_state is ProposalState.later]
    ordered = application_queue.sort_proposals(due, sort) + application_queue.sort_proposals(
        deferred, sort
    )

    page = ordered[offset : offset + limit]
    return QueueResponse(
        items=page,
        total=len(ordered),
        summary=application_queue.build_summary(
            db,
            proposals,
            daily_target=settings.daily_application_target,
            timezone_name=settings.report_timezone,
        ),
        facets={
            **application_queue.build_facets(city_facet_source),
            "skip_reasons": list(SKIP_REASONS),
            "sorts": list(application_queue.SORT_KEYS),
        },
    )


@queue_router.get("/metrics", response_model=ApplicationMetrics)
def get_metrics(db: Session = Depends(get_db)) -> ApplicationMetrics:
    """Deterministic funnel counts and rates. No LLM involved."""
    return application_metrics.compute_metrics(db)


# --------------------------------------------------------------------------
# per-job workflow
# --------------------------------------------------------------------------


def _to_event(event) -> ApplicationEventOut:  # noqa: ANN001
    return ApplicationEventOut(
        id=event.id,
        event_type=event.event_type.value,
        notes=event.notes,
        metadata_json=event.metadata_json or {},
        created_at=event.created_at,
    )


def _to_response(outcome: application_workflow.WorkflowOutcome) -> WorkflowResponse:
    return WorkflowResponse(
        job_id=outcome.job.id,
        status=outcome.job.status,
        previous_status=outcome.previous_status,
        event=_to_event(outcome.event),
        message=outcome.message,
    )


@job_router.get("/{job_id}/application-events", response_model=list[ApplicationEventOut])
def list_application_events(
    job_id: int, db: Session = Depends(get_db)
) -> list[ApplicationEventOut]:
    """Full append-only history, oldest first."""
    return [_to_event(e) for e in application_workflow.list_events(db, job_id)]


@job_router.post("/{job_id}/application-events", response_model=ApplicationEventOut)
def add_application_note(
    job_id: int,
    note: str = Body(..., embed=True, max_length=1000),
    db: Session = Depends(get_db),
) -> ApplicationEventOut:
    return _to_event(application_workflow.add_note(db, job_id, note))


@job_router.post("/{job_id}/mark-applied", response_model=WorkflowResponse)
def mark_applied(
    job_id: int,
    payload: MarkAppliedRequest,
    db: Session = Depends(get_db),
) -> WorkflowResponse:
    """Record that the user applied on the platform themselves.

    Requires an explicit ``confirmed`` flag; there is deliberately no bulk
    equivalent, because this represents a real action in the outside world.

    ``resume_id`` records **which resume was actually submitted**. The UI
    defaults it to the active analysis resume, but the user can change it - and
    if they leave it unset it is stored as ``unknown`` rather than assumed.
    """
    return _to_response(application_workflow.mark_applied(db, job_id, payload))


@job_router.post("/{job_id}/attribute-resume", response_model=WorkflowResponse)
def attribute_resume(
    job_id: int,
    payload: AttributeResumeRequest = Body(...),
    db: Session = Depends(get_db),
) -> WorkflowResponse:
    """补充历史简历 - say which resume an already-recorded application used.

    Explicit human editing only. This appends a corrective event rather than
    rewriting the original ``applied`` row, so the original history survives and
    the correction is itself auditable.
    """
    return _to_response(application_workflow.attribute_resume(db, job_id, payload))


@job_router.post("/{job_id}/skip", response_model=WorkflowResponse)
def skip_job(
    job_id: int,
    payload: SkipRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> WorkflowResponse:
    return _to_response(application_workflow.skip(db, job_id, payload or SkipRequest()))


@job_router.post("/{job_id}/later", response_model=WorkflowResponse)
def defer_job(
    job_id: int,
    payload: LaterRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> WorkflowResponse:
    return _to_response(application_workflow.defer(db, job_id, payload or LaterRequest()))


@job_router.post("/{job_id}/reset-status", response_model=WorkflowResponse)
def reset_status(
    job_id: int,
    payload: ResetRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> WorkflowResponse:
    """Undo a decision. History is preserved; a status_reset event is appended."""
    return _to_response(application_workflow.reset_status(db, job_id, payload or ResetRequest()))


@job_router.post("/{job_id}/reply", response_model=WorkflowResponse)
def record_reply(
    job_id: int,
    payload: ReplyRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> WorkflowResponse:
    return _to_response(application_workflow.record_reply(db, job_id, payload or ReplyRequest()))


@job_router.post("/{job_id}/interview", response_model=WorkflowResponse)
def record_interview(
    job_id: int,
    payload: InterviewRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> WorkflowResponse:
    return _to_response(
        application_workflow.record_interview(db, job_id, payload or InterviewRequest())
    )


@job_router.post("/{job_id}/offer", response_model=WorkflowResponse)
def record_offer(
    job_id: int,
    payload: OfferRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> WorkflowResponse:
    return _to_response(application_workflow.record_offer(db, job_id, payload or OfferRequest()))


@job_router.post("/{job_id}/reject", response_model=WorkflowResponse)
def record_rejection(
    job_id: int,
    payload: RejectRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> WorkflowResponse:
    return _to_response(
        application_workflow.record_rejection(db, job_id, payload or RejectRequest())
    )
