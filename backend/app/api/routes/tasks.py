"""求职任务控制台 endpoints (M1).

Thin routes: persistence lives in ``services/task_console``. Creating a task
stores criteria only; attaching a candidate associates an already-captured
job. Neither ever searches, captures, clicks, or navigates anything, and
neither ever calls a model or changes ``Job.status``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import JobSearchTask, OrchestrationEvent, TaskCandidate
from app.schemas.orchestration_event import (
    OrchestrationEventCreate,
    OrchestrationEventListResponse,
    OrchestrationEventOut,
)
from app.schemas.task import (
    CandidateAnalysisOut,
    CandidateAssociationRequest,
    TaskCandidateListResponse,
    TaskCandidateOut,
    TaskCreate,
    TaskListResponse,
    TaskOut,
)
from app.services import orchestration_events, task_console

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _task_out(task: JobSearchTask) -> TaskOut:
    return TaskOut(
        id=task.id,
        name=task.name,
        keywords=task.keywords,
        city=task.city,
        experience_text=task.experience_text,
        education_text=task.education_text,
        salary_min=task.salary_min,
        salary_max=task.salary_max,
        # Passed through exactly as stored: ``None`` (never set) and ``[]``
        # (explicitly "no exclusions") must not be collapsed together.
        exclusions=task.exclusions_json,
        resume_id=task.resume_id,
        max_candidates=task.max_candidates,
        min_score=task.min_score,
        mode=task.mode,
        notes=task.notes,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _candidate_out(association: TaskCandidate) -> TaskCandidateOut:
    job = association.job
    latest_analysis = job.analyses[0] if job.analyses else None
    return TaskCandidateOut(
        job_id=job.id,
        title=job.title,
        company=job.company,
        city=job.city,
        salary_text=job.salary_text,
        status=job.status,
        added_at=association.created_at,
        analysis=(
            CandidateAnalysisOut(
                overall_score=latest_analysis.overall_score,
                verdict=latest_analysis.verdict,
            )
            if latest_analysis is not None
            else None
        ),
    )


@router.post("", response_model=TaskOut)
def create_task(payload: TaskCreate = Body(...), db: Session = Depends(get_db)) -> TaskOut:
    """Store search criteria and a mode. Never searches or captures anything."""
    task = task_console.create_task(
        db,
        name=payload.name,
        keywords=payload.keywords,
        city=payload.city,
        experience_text=payload.experience_text,
        education_text=payload.education_text,
        salary_min=payload.salary_min,
        salary_max=payload.salary_max,
        exclusions=payload.exclusions,
        resume_id=payload.resume_id,
        max_candidates=payload.max_candidates,
        min_score=payload.min_score,
        mode=payload.mode,
        notes=payload.notes,
    )
    return _task_out(task)


@router.get("", response_model=TaskListResponse)
def list_tasks(db: Session = Depends(get_db)) -> TaskListResponse:
    tasks = task_console.list_tasks(db)
    return TaskListResponse(items=[_task_out(t) for t in tasks], total=len(tasks))


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)) -> TaskOut:
    return _task_out(task_console.get_task(db, task_id))


@router.post("/{task_id}/candidates", response_model=TaskCandidateOut)
def add_candidate(
    task_id: int,
    payload: CandidateAssociationRequest = Body(...),
    db: Session = Depends(get_db),
) -> TaskCandidateOut:
    """Attach an already-captured job to this task.

    The job must already exist - captured through Quick Capture, browser
    capture, or the extension, exactly as today. This endpoint performs no
    capture of its own. Attaching the same job twice is a no-op, not an
    error or a duplicate row.
    """
    association = task_console.add_candidate(db, task_id, payload.job_id)
    return _candidate_out(association)


@router.get("/{task_id}/candidates", response_model=TaskCandidateListResponse)
def list_candidates(task_id: int, db: Session = Depends(get_db)) -> TaskCandidateListResponse:
    associations = task_console.list_candidates(db, task_id)
    return TaskCandidateListResponse(
        items=[_candidate_out(a) for a in associations], total=len(associations)
    )


# --------------------------------------------------------------------------
# M3: append-only console audit trail
# --------------------------------------------------------------------------


def _as_utc(moment: datetime) -> datetime:
    """SQLite has no timezone type and hands back naive datetimes - see
    ``application_cycles._as_utc``. Without this, a naive ``created_at`` is
    serialized without a UTC offset and the frontend misreads it as local
    time (e.g. 12:55 shown as 12:55 instead of 21:55 in Asia/Tokyo)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _event_out(event: OrchestrationEvent) -> OrchestrationEventOut:
    return OrchestrationEventOut(
        id=event.id,
        task_id=event.task_id,
        job_id=event.job_id,
        event_type=event.event_type,
        note=event.note,
        created_at=_as_utc(event.created_at),
    )


@router.post("/{task_id}/events", response_model=OrchestrationEventOut)
def add_event(
    task_id: int,
    payload: OrchestrationEventCreate = Body(...),
    db: Session = Depends(get_db),
) -> OrchestrationEventOut:
    """Record one explicit human action. Never fired automatically - see
    docs/orchestration/ROADMAP.md (M3)."""
    event = orchestration_events.add_event(
        db,
        task_id,
        event_type=payload.event_type,
        job_id=payload.job_id,
        note=payload.note,
    )
    return _event_out(event)


@router.get("/{task_id}/events", response_model=OrchestrationEventListResponse)
def list_events(task_id: int, db: Session = Depends(get_db)) -> OrchestrationEventListResponse:
    events = orchestration_events.list_events(db, task_id)
    return OrchestrationEventListResponse(
        items=[_event_out(e) for e in events], total=len(events)
    )
