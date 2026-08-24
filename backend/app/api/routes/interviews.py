"""Interview pipeline endpoints (v0.8).

Thin routes: every state change goes through ``services/interview_pipeline``,
which is to interviews what ``application_workflow`` is to ``Job.status``.

No calendar integration, no email, no automatic acceptance, no automatic
recruiter replies. Everything here records something a human did or decided.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models import (
    ROUND_TYPE_LABEL,
    InterviewOutcome,
    InterviewProcess,
    InterviewProcessStatus,
    InterviewRound,
    InterviewRoundStatus,
)
from app.schemas.interview import (
    InterviewBoardResponse,
    LegacyInterviewMilestone,
    PreparationNotes,
    ProcessActionResponse,
    ProcessCreateRequest,
    ProcessListResponse,
    ProcessOut,
    ProcessUpdateRequest,
    ProcessWithdrawRequest,
    RoundActionResponse,
    RoundCancelRequest,
    RoundCompleteRequest,
    RoundCreateRequest,
    RoundOut,
    RoundUpdateRequest,
    InterviewSuggestion,
    SuggestionListResponse,
    UpcomingInterviewItem,
    UpcomingInterviewsResponse,
)
from app.services import interview_pipeline
from app.services.timezones import local_now, report_timezone, to_local

router = APIRouter(prefix="/api", tags=["interviews"])

#: How far ahead the 面试 board and the dashboard look.
UPCOMING_DAYS = 30
DASHBOARD_LIMIT = 5


def _utc(moment: datetime | None) -> datetime | None:
    """SQLite has no timezone type and hands back naive datetimes.

    Comparing one of those to an aware ``now`` raises, so every stored moment
    is normalized before it is used in a comparison. The rest of the codebase
    does the same (see ``application_cycles._as_utc``).
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# serialization
# --------------------------------------------------------------------------


def _round_out(row: InterviewRound) -> RoundOut:
    prep = row.preparation_json or {}
    return RoundOut(
        id=row.id,
        interview_process_id=row.interview_process_id,
        round_index=row.round_index,
        round_type=row.round_type,
        round_type_label=ROUND_TYPE_LABEL[row.round_type],
        custom_round_name=row.custom_round_name,
        display_name=row.display_name,
        scheduled_at=row.scheduled_at,
        duration_minutes=row.duration_minutes,
        completed_at=row.completed_at,
        status=row.status,
        outcome=row.outcome,
        failure_reason=row.failure_reason,
        interviewer_name=row.interviewer_name,
        interviewer_role=row.interviewer_role,
        location_type=row.location_type,
        meeting_url=row.meeting_url,
        feedback_text=row.feedback_text,
        notes=row.notes,
        feedback_tags=list(row.feedback_tags or []),
        preparation=PreparationNotes(
            questions_asked=list(prep.get("questions_asked") or []),
            weak_points=list(prep.get("weak_points") or []),
            follow_up_topics=list(prep.get("follow_up_topics") or []),
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _process_out(db: Session, process: InterviewProcess) -> ProcessOut:
    """Serialize a process together with its application-cycle context.

    The resume shown is the one recorded on that cycle's ``applied`` event -
    frozen at application time, never the currently active analysis resume.
    """
    context = interview_pipeline.cycle_context(db, process)
    current = interview_pipeline.current_round(process)
    job = process.job

    return ProcessOut(
        id=process.id,
        job_id=process.job_id,
        applied_event_id=process.applied_event_id,
        status=process.status,
        ended_after_round_type=process.ended_after_round_type,
        failure_reason=process.failure_reason,
        withdraw_reason=process.withdraw_reason,
        closed_at=process.closed_at,
        notes=process.notes,
        created_at=process.created_at,
        updated_at=process.updated_at,
        rounds=[_round_out(r) for r in sorted(process.rounds, key=lambda r: (r.round_index, r.id))],
        company=job.company if job else "",
        title=job.title if job else "",
        city=job.city if job else None,
        applied_at=context.applied_at if context else None,
        resume_id=context.resume_id if context else None,
        resume_label=context.resume_label if context else None,
        resume_archived=context.resume_archived if context else False,
        current_round=_round_out(current) if current else None,
        next_scheduled_at=interview_pipeline.next_scheduled_at(process),
        rounds_passed=interview_pipeline.rounds_passed(process),
    )


# --------------------------------------------------------------------------
# board
# --------------------------------------------------------------------------


def _day_label(day_offset: int) -> str:
    return {0: "今天", 1: "明天"}.get(day_offset, "未来7天")


@router.get("/interviews", response_model=InterviewBoardResponse)
def interview_board(db: Session = Depends(get_db)) -> InterviewBoardResponse:
    """The 面试 page.

    Days are grouped in ``REPORT_TIMEZONE`` (Asia/Tokyo by default), never in
    the browser's timezone - "today" has to mean the same thing on the server
    and on the page.
    """
    cfg = get_settings()
    now = datetime.now(timezone.utc)
    today = local_now().date()

    processes = interview_pipeline.list_processes(db)
    upcoming_buckets: dict[str, list[ProcessOut]] = {}
    awaiting: list[ProcessOut] = []
    completed: list[ProcessOut] = []
    unscheduled: list[ProcessOut] = []

    for process in processes:
        payload = _process_out(db, process)

        if process.status is not InterviewProcessStatus.ongoing:
            completed.append(payload)
            continue

        scheduled = [
            r
            for r in process.rounds
            if r.status is InterviewRoundStatus.scheduled and r.scheduled_at
        ]
        future = [r for r in scheduled if _utc(r.scheduled_at) >= now]

        if future:
            soonest = min(future, key=lambda r: _utc(r.scheduled_at))
            local_day = to_local(soonest.scheduled_at).date()
            offset = (local_day - today).days
            if offset <= UPCOMING_DAYS:
                key = local_day.isoformat()
                upcoming_buckets.setdefault(key, []).append(payload)
                continue

        # Past its scheduled time, or completed rounds with no result yet.
        if scheduled or any(
            r.outcome is InterviewOutcome.pending
            and r.status is InterviewRoundStatus.completed
            for r in process.rounds
        ):
            awaiting.append(payload)
        else:
            unscheduled.append(payload)

    groups = []
    for key in sorted(upcoming_buckets):
        offset = (datetime.fromisoformat(key).date() - today).days
        items = sorted(
            upcoming_buckets[key],
            key=lambda p: _utc(p.next_scheduled_at) or datetime.max.replace(tzinfo=timezone.utc),
        )
        groups.append(
            {
                "key": key,
                "label": f"{_day_label(offset)} · {key}" if offset <= 1 else key,
                "items": items,
            }
        )

    awaiting.sort(key=lambda p: _utc(p.next_scheduled_at) or _utc(p.updated_at))
    completed.sort(key=lambda p: _utc(p.closed_at) or _utc(p.updated_at), reverse=True)

    legacy = [
        LegacyInterviewMilestone(
            job_id=event.job_id,
            event_id=event.id,
            company=event.job.company if event.job else "",
            title=event.job.title if event.job else "",
            occurred_at=event.created_at,
            note=event.notes,
            legacy_round_label=(event.metadata_json or {}).get("interview_round"),
        )
        for event in interview_pipeline.legacy_interview_events(db)
    ]

    return InterviewBoardResponse(
        timezone=cfg.report_timezone,
        generated_at=now,
        upcoming=groups,
        awaiting_result=awaiting,
        completed=completed,
        ongoing_without_schedule=unscheduled,
        legacy_milestones=legacy,
    )


@router.get("/interviews/upcoming", response_model=UpcomingInterviewsResponse)
def upcoming_interviews(
    db: Session = Depends(get_db),
    limit: int = Query(default=DASHBOARD_LIMIT, ge=1, le=20),
) -> UpcomingInterviewsResponse:
    """Compact dashboard list.

    A local view of what the user recorded - JobAgent reads no calendar and no
    inbox. Meeting URLs are deliberately omitted; a glance does not need them.
    """
    cfg = get_settings()
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=UPCOMING_DAYS)
    today = local_now().date()

    items: list[UpcomingInterviewItem] = []
    for process in interview_pipeline.list_processes(db):
        if process.status is not InterviewProcessStatus.ongoing:
            continue
        for row in process.rounds:
            if row.status is not InterviewRoundStatus.scheduled or not row.scheduled_at:
                continue
            when = _utc(row.scheduled_at)
            if not (now <= when <= horizon):
                continue
            local_day = to_local(row.scheduled_at).date()
            offset = (local_day - today).days
            items.append(
                UpcomingInterviewItem(
                    process_id=process.id,
                    job_id=process.job_id,
                    round_id=row.id,
                    company=process.job.company if process.job else "",
                    title=process.job.title if process.job else "",
                    round_label=row.display_name,
                    scheduled_at=when,
                    location_type=row.location_type,
                    day_key=_day_label(offset) if offset <= 1 else local_day.isoformat(),
                )
            )

    items.sort(key=lambda i: _utc(i.scheduled_at))
    total = len(items)
    return UpcomingInterviewsResponse(
        timezone=cfg.report_timezone,
        items=items[:limit],
        total=total,
        message=(
            f"接下来 {UPCOMING_DAYS} 天内有 {total} 场已安排的面试"
            if total
            else "暂无已安排的面试。面试时间需要你手动记录，JobAgent 不读取日历。"
        ),
    )


# --------------------------------------------------------------------------
# processes
# --------------------------------------------------------------------------


@router.get("/jobs/{job_id}/interviews", response_model=ProcessListResponse)
def job_interviews(job_id: int, db: Session = Depends(get_db)) -> ProcessListResponse:
    """Every process for a job - a re-applied job legitimately has several."""
    processes = interview_pipeline.list_processes(db, job_id=job_id)
    return ProcessListResponse(
        items=[_process_out(db, p) for p in processes], total=len(processes)
    )


@router.post("/jobs/{job_id}/interviews", response_model=ProcessActionResponse)
def create_interview_process(
    job_id: int,
    payload: ProcessCreateRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> ProcessActionResponse:
    """Open an interview process on one application cycle.

    Refuses if that cycle already has one: one candidacy, one process.
    """
    process = interview_pipeline.create_process(
        db, job_id, payload or ProcessCreateRequest()
    )
    return ProcessActionResponse(
        process=_process_out(db, process), message="已创建面试流程"
    )


@router.get("/interviews/{process_id}", response_model=ProcessOut)
def get_interview_process(process_id: int, db: Session = Depends(get_db)) -> ProcessOut:
    return _process_out(db, interview_pipeline.get_process(db, process_id))


@router.patch("/interviews/{process_id}", response_model=ProcessActionResponse)
def update_interview_process(
    process_id: int,
    payload: ProcessUpdateRequest = Body(...),
    db: Session = Depends(get_db),
) -> ProcessActionResponse:
    process = interview_pipeline.update_process(db, process_id, notes=payload.notes)
    return ProcessActionResponse(process=_process_out(db, process), message="已更新")


@router.post("/interviews/{process_id}/withdraw", response_model=ProcessActionResponse)
def withdraw_interview_process(
    process_id: int,
    payload: ProcessWithdrawRequest = Body(...),
    db: Session = Depends(get_db),
) -> ProcessActionResponse:
    """The candidate ends the process.

    Recorded as a withdrawal, never as a rejection - analytics keeps the two
    apart so your own decisions are not counted as failures.
    """
    process = interview_pipeline.withdraw_process(db, process_id, payload)
    return ProcessActionResponse(
        process=_process_out(db, process), message="已记录：主动终止该面试流程"
    )


# --------------------------------------------------------------------------
# rounds
# --------------------------------------------------------------------------


@router.post("/interviews/{process_id}/rounds", response_model=RoundActionResponse)
def add_round(
    process_id: int,
    payload: RoundCreateRequest = Body(...),
    db: Session = Depends(get_db),
) -> RoundActionResponse:
    process, row = interview_pipeline.add_round(db, process_id, payload)
    return RoundActionResponse(
        process=_process_out(db, process),
        round=_round_out(row),
        message=f"已添加：{row.display_name}",
        job_status=process.job.status.value if process.job else None,
    )


@router.patch("/interview-rounds/{round_id}", response_model=RoundActionResponse)
def update_round(
    round_id: int,
    payload: RoundUpdateRequest = Body(...),
    db: Session = Depends(get_db),
) -> RoundActionResponse:
    """Edit round details. The outcome is not editable here - use /complete."""
    process, row = interview_pipeline.update_round(db, round_id, payload)
    return RoundActionResponse(
        process=_process_out(db, process), round=_round_out(row), message="已更新面试轮次"
    )


@router.post("/interview-rounds/{round_id}/complete", response_model=RoundActionResponse)
def complete_round(
    round_id: int,
    payload: RoundCompleteRequest = Body(...),
    db: Session = Depends(get_db),
) -> RoundActionResponse:
    """Record what happened in a round.

    Requires ``confirmed``. Re-recording an already-recorded outcome also
    requires ``correction=true`` - a stored result is never silently replaced.
    """
    process, row, job_status = interview_pipeline.complete_round(db, round_id, payload)
    return RoundActionResponse(
        process=_process_out(db, process),
        round=_round_out(row),
        message=f"已记录结果：{row.display_name}",
        job_status=job_status,
    )


@router.post("/interview-rounds/{round_id}/cancel", response_model=RoundActionResponse)
def cancel_round(
    round_id: int,
    payload: RoundCancelRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> RoundActionResponse:
    process, row = interview_pipeline.cancel_round(
        db, round_id, payload or RoundCancelRequest()
    )
    return RoundActionResponse(
        process=_process_out(db, process), round=_round_out(row), message="已取消该轮次"
    )


# --------------------------------------------------------------------------
# recruiter conversation handoff
# --------------------------------------------------------------------------


@router.get(
    "/recruiter/conversations/{conversation_id}/interview-suggestions",
    response_model=SuggestionListResponse,
)
def conversation_interview_suggestions(
    conversation_id: int, db: Session = Depends(get_db)
) -> SuggestionListResponse:
    """Times the recruiter proposed, as suggestions only.

    Reading this creates nothing and calls no model - it reads analyses that
    already exist. The human confirms a suggestion, and only that confirmation
    (POST /api/interviews/{id}/rounds) creates a round.
    """
    rows = interview_pipeline.interview_suggestions(db, conversation_id)
    actionable = sum(1 for row in rows if row["can_add"])
    return SuggestionListResponse(
        items=[InterviewSuggestion(**row) for row in rows],
        total=len(rows),
        message=(
            f"检测到 {len(rows)} 个可能的面试时间，其中 {actionable} 个可直接添加。"
            "确认后才会创建面试轮次。"
            if rows
            else "没有检测到明确的面试时间。"
        ),
    )
