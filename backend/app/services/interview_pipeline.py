"""The only place interview process / round state changes (v0.8).

Routes never touch these models directly, exactly as routes never touch
``Job.status`` directly (see ``application_workflow``).

Three rules this module exists to hold:

**A process belongs to one application cycle.** The link is
``applied_event_id``, resolved once at creation and never re-derived. A job
applied to with Resume A, reset, then applied to again with Resume B has two
cycles; an interview belongs to exactly one, and which resume gets the credit
follows from that - never from ``Job.status`` and never from whichever resume
happens to be active today.

**Detail lives here, milestones live in the event trail.** A round records
schedules, interviewers and feedback, all of which get edited. The append-only
``ApplicationEvent`` trail records the business facts a human would want in the
timeline: an interview happened, a result was recorded, a process was
withdrawn. Editing a field does not emit an event; that would drown the
timeline in noise.

**Nothing is inferred.** A recruiter message that looks like a scheduling
request produces a *suggestion*; only an explicit human action creates a round.
An outcome is never overwritten silently - re-recording one requires an
explicit correction flag and leaves an audit event behind.

``meeting_url`` is never logged here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.models import (
    ROUND_TYPE_LABEL,
    ApplicationEvent,
    EventType,
    InterviewOutcome,
    InterviewProcess,
    InterviewProcessStatus,
    InterviewRound,
    InterviewRoundStatus,
    InterviewRoundType,
    Job,
    JobStatus,
    Resume,
)
from app.schemas.application import InterviewRequest, RejectRequest
from app.schemas.interview import (
    ProcessCreateRequest,
    ProcessWithdrawRequest,
    RoundCancelRequest,
    RoundCompleteRequest,
    RoundCreateRequest,
    RoundUpdateRequest,
)
from app.services import application_workflow
from app.services.application_cycles import build_cycles, effective_cycle

logger = get_logger(__name__)

PREPARATION_KEYS = ("questions_asked", "weak_points", "follow_up_topics")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


def _process_query():
    return select(InterviewProcess).options(
        selectinload(InterviewProcess.rounds),
        selectinload(InterviewProcess.job).selectinload(Job.events),
    )


def get_process(db: Session, process_id: int) -> InterviewProcess:
    process = db.scalar(_process_query().where(InterviewProcess.id == process_id))
    if process is None:
        raise NotFoundError(
            f"面试流程 {process_id} 不存在", detail={"process_id": process_id}
        )
    return process


def get_round(db: Session, round_id: int) -> InterviewRound:
    row = db.get(InterviewRound, round_id)
    if row is None:
        raise NotFoundError(f"面试轮次 {round_id} 不存在", detail={"round_id": round_id})
    return row


def list_processes(db: Session, *, job_id: int | None = None) -> list[InterviewProcess]:
    stmt = _process_query()
    if job_id is not None:
        stmt = stmt.where(InterviewProcess.job_id == job_id)
    return list(db.scalars(stmt.order_by(InterviewProcess.created_at.asc())).unique())


def process_for_job(db: Session, job_id: int) -> InterviewProcess | None:
    """The process on the job's *current* application cycle, if any.

    A superseded cycle's process is history and is deliberately not returned:
    the job detail page is asking "what is happening now".
    """
    job = db.get(Job, job_id)
    if job is None:
        return None
    cycle = effective_cycle(job)
    if cycle is None or cycle.applied_event_id is None:
        return None
    return db.scalar(
        _process_query().where(
            InterviewProcess.applied_event_id == cycle.applied_event_id
        )
    )


# --------------------------------------------------------------------------
# cycle attribution
# --------------------------------------------------------------------------


@dataclass(slots=True)
class CycleContext:
    """Everything a process needs to know about the cycle it belongs to."""

    applied_event_id: int
    applied_at: datetime
    resume_id: int | None
    resume_label: str | None
    resume_archived: bool


def cycle_context(db: Session, process: InterviewProcess) -> CycleContext | None:
    """Resolve the process's cycle. Read-only, and never guesses.

    The resume comes from the cycle's own ``applied`` event, so it is frozen at
    application time exactly as v0.7 requires.
    """
    job = process.job or db.get(Job, process.job_id)
    if job is None:
        return None
    for cycle in build_cycles(job):
        if cycle.applied_event_id == process.applied_event_id:
            resume = db.get(Resume, cycle.resume_id) if cycle.resume_id else None
            return CycleContext(
                applied_event_id=process.applied_event_id,
                applied_at=cycle.applied_at,
                resume_id=cycle.resume_id,
                resume_label=resume.display_name if resume else None,
                resume_archived=bool(resume and resume.archived),
            )
    return None


def _resolve_applied_event(db: Session, job: Job, applied_event_id: int | None) -> int:
    cycles = build_cycles(job)
    if not cycles:
        raise ValidationError(
            "该岗位还没有投递记录，无法创建面试流程。请先在「投递队列」确认已投递。",
            detail={"job_id": job.id},
        )

    if applied_event_id is not None:
        if not any(c.applied_event_id == applied_event_id for c in cycles):
            raise NotFoundError(
                "找不到对应的投递记录。",
                detail={"applied_event_id": applied_event_id, "job_id": job.id},
            )
        return applied_event_id

    cycle = effective_cycle(job)
    if cycle is None or cycle.applied_event_id is None:
        raise ValidationError(
            "该岗位当前没有有效的投递记录（可能已被「恢复待处理」撤销）。",
            detail={"job_id": job.id},
        )
    return cycle.applied_event_id


# --------------------------------------------------------------------------
# process lifecycle
# --------------------------------------------------------------------------


def create_process(
    db: Session, job_id: int, payload: ProcessCreateRequest
) -> InterviewProcess:
    """Open a process for one application cycle.

    Refuses a duplicate: one cycle has one process, or every funnel would
    double-count the same candidacy.
    """
    job = application_workflow.get_job(db, job_id)
    applied_event_id = _resolve_applied_event(db, job, payload.applied_event_id)

    existing = db.scalar(
        select(InterviewProcess).where(
            InterviewProcess.applied_event_id == applied_event_id
        )
    )
    if existing is not None:
        raise ValidationError(
            "这次投递已经有面试流程了，直接在其中添加轮次即可。",
            detail={"process_id": existing.id, "applied_event_id": applied_event_id},
        )

    process = InterviewProcess(
        job_id=job.id,
        applied_event_id=applied_event_id,
        status=InterviewProcessStatus.ongoing,
        notes=(payload.notes or "").strip() or None,
    )
    db.add(process)
    db.flush()

    if payload.first_round is not None:
        _add_round_row(db, process, payload.first_round)

    db.commit()
    db.refresh(process)
    log_event(
        logger,
        "interview.process_created",
        process_id=process.id,
        job_id=job.id,
        applied_event_id=applied_event_id,
    )
    return get_process(db, process.id)


def ensure_process(db: Session, job_id: int) -> InterviewProcess:
    """Get the current cycle's process, creating it if it does not exist yet.

    Used by the v0.4 记录面试 path so that action keeps working and now feeds
    the pipeline instead of a parallel one.
    """
    existing = process_for_job(db, job_id)
    if existing is not None:
        return existing
    return create_process(db, job_id, ProcessCreateRequest())


def update_process(db: Session, process_id: int, *, notes: str | None) -> InterviewProcess:
    process = get_process(db, process_id)
    if notes is not None:
        process.notes = (notes or "").strip() or None
    db.commit()
    return get_process(db, process_id)


def withdraw_process(
    db: Session, process_id: int, payload: ProcessWithdrawRequest
) -> InterviewProcess:
    """The candidate stopped.

    Deliberately **not** a rejection: analytics must never report a withdrawal
    as the employer turning the candidate down, or every drop-off number
    becomes a story about failure that did not happen.
    """
    if not payload.confirmed:
        raise ValidationError(
            "需要明确确认后才能终止该面试流程。", detail={"field": "confirmed"}
        )
    process = get_process(db, process_id)
    if process.is_closed:
        raise ValidationError(
            "该面试流程已经结束了。", detail={"status": process.status.value}
        )

    process.status = InterviewProcessStatus.withdrawn
    process.withdraw_reason = payload.reason
    process.closed_at = _now()
    process.ended_after_round_type = _last_meaningful_round_type(process)
    if payload.notes:
        process.notes = payload.notes.strip() or process.notes

    application_workflow._append_event(  # noqa: SLF001 - same package boundary
        db,
        process.job,
        EventType.interview_withdrawn,
        notes=payload.notes or "候选人主动终止面试流程",
        metadata={
            "process_id": process.id,
            "withdraw_reason": payload.reason.value,
            "applied_event_id": process.applied_event_id,
        },
    )
    db.commit()
    log_event(
        logger,
        "interview.process_withdrawn",
        process_id=process.id,
        reason=payload.reason.value,
    )
    return get_process(db, process_id)


def close_process_as_offer(db: Session, process: InterviewProcess) -> None:
    """Mark the process as ended in an offer.

    The workflow ``offer`` milestone still belongs to ``ApplicationEvent`` via
    ``application_workflow.record_offer`` - this only records that the interview
    process itself is over. No duplicate business semantics.
    """
    process.status = InterviewProcessStatus.offer
    process.closed_at = _now()
    process.ended_after_round_type = _last_meaningful_round_type(process)


def close_process_as_rejected(db: Session, process: InterviewProcess, *, reason=None) -> None:
    process.status = InterviewProcessStatus.rejected
    process.closed_at = _now()
    process.failure_reason = reason
    process.ended_after_round_type = _last_meaningful_round_type(process)


def _last_meaningful_round_type(process: InterviewProcess) -> InterviewRoundType | None:
    """Where the candidacy actually got to.

    The last *completed* round, ignoring cancellations - a round that never
    happened is not where the process ended.
    """
    completed = [
        r
        for r in sorted(process.rounds, key=lambda r: (r.round_index, r.id))
        if r.status is InterviewRoundStatus.completed
    ]
    return completed[-1].round_type if completed else None


# --------------------------------------------------------------------------
# rounds
# --------------------------------------------------------------------------


def _next_round_index(process: InterviewProcess) -> int:
    return max((r.round_index for r in process.rounds), default=0) + 1


def _add_round_row(
    db: Session, process: InterviewProcess, payload: RoundCreateRequest
) -> InterviewRound:
    scheduled = _as_utc(payload.scheduled_at) if payload.scheduled_at else None
    row = InterviewRound(
        interview_process_id=process.id,
        round_index=payload.round_index or _next_round_index(process),
        round_type=payload.round_type,
        custom_round_name=(payload.custom_round_name or "").strip() or None,
        scheduled_at=scheduled,
        duration_minutes=payload.duration_minutes,
        # A time is what separates "agreed in principle" from "on the calendar".
        status=InterviewRoundStatus.scheduled if scheduled else InterviewRoundStatus.planned,
        outcome=InterviewOutcome.pending,
        location_type=payload.location_type,
        meeting_url=(payload.meeting_url or "").strip() or None,
        interviewer_name=(payload.interviewer_name or "").strip() or None,
        interviewer_role=(payload.interviewer_role or "").strip() or None,
        notes=(payload.notes or "").strip() or None,
        feedback_tags=[],
        preparation_json={},
    )
    db.add(row)
    db.flush()
    process.rounds.append(row)
    return row


def add_round(
    db: Session, process_id: int, payload: RoundCreateRequest
) -> tuple[InterviewProcess, InterviewRound]:
    process = get_process(db, process_id)
    if process.is_closed:
        raise ValidationError(
            "该面试流程已结束，不能再添加轮次。如果流程重新启动，请新建流程。",
            detail={"status": process.status.value},
        )

    row = _add_round_row(db, process, payload)

    # One event per real milestone, not per field edit.
    application_workflow._append_event(  # noqa: SLF001
        db,
        process.job,
        EventType.interview_scheduled if row.scheduled_at else EventType.interview,
        notes=f"添加面试轮次：{row.display_name}",
        metadata=_round_event_metadata(process, row),
    )
    # A recorded interview round means the human is interviewing.
    _sync_job_status_to_interview(db, process)

    db.commit()
    log_event(
        logger,
        "interview.round_added",
        process_id=process.id,
        round_id=row.id,
        round_type=row.round_type.value,
        scheduled=bool(row.scheduled_at),
    )
    return get_process(db, process_id), get_round(db, row.id)


def update_round(
    db: Session, round_id: int, payload: RoundUpdateRequest
) -> tuple[InterviewProcess, InterviewRound]:
    """Edit round details. Never touches the outcome - that is /complete."""
    row = get_round(db, round_id)

    if payload.round_type is not None:
        row.round_type = payload.round_type
    if payload.custom_round_name is not None:
        row.custom_round_name = (payload.custom_round_name or "").strip() or None
    if payload.round_index is not None:
        row.round_index = payload.round_index
    if payload.duration_minutes is not None:
        row.duration_minutes = payload.duration_minutes
    if payload.location_type is not None:
        row.location_type = payload.location_type
    if payload.meeting_url is not None:
        row.meeting_url = (payload.meeting_url or "").strip() or None
    if payload.interviewer_name is not None:
        row.interviewer_name = (payload.interviewer_name or "").strip() or None
    if payload.interviewer_role is not None:
        row.interviewer_role = (payload.interviewer_role or "").strip() or None
    if payload.notes is not None:
        row.notes = (payload.notes or "").strip() or None
    if payload.preparation is not None:
        row.preparation_json = payload.preparation.model_dump()

    if payload.clear_scheduled_at:
        row.scheduled_at = None
        if row.status is InterviewRoundStatus.scheduled:
            row.status = InterviewRoundStatus.planned
    elif payload.scheduled_at is not None:
        row.scheduled_at = _as_utc(payload.scheduled_at)
        if row.status is InterviewRoundStatus.planned:
            row.status = InterviewRoundStatus.scheduled

    db.commit()
    log_event(logger, "interview.round_updated", round_id=row.id)
    return get_process(db, row.interview_process_id), get_round(db, round_id)


def cancel_round(
    db: Session, round_id: int, payload: RoundCancelRequest
) -> tuple[InterviewProcess, InterviewRound]:
    row = get_round(db, round_id)
    if row.status is InterviewRoundStatus.completed:
        raise ValidationError(
            "已记录结果的轮次不能取消。如果记录有误，请使用「更正结果」。",
            detail={"round_id": round_id},
        )

    row.status = InterviewRoundStatus.cancelled
    row.outcome = InterviewOutcome.unknown
    if payload.reason:
        row.notes = f"{row.notes}\n取消原因：{payload.reason}".strip() if row.notes else (
            f"取消原因：{payload.reason}"
        )

    process = get_process(db, row.interview_process_id)
    application_workflow._append_event(  # noqa: SLF001
        db,
        process.job,
        EventType.interview_cancelled,
        notes=payload.reason or f"取消面试轮次：{row.display_name}",
        metadata=_round_event_metadata(process, row),
    )
    db.commit()
    log_event(logger, "interview.round_cancelled", round_id=row.id)
    return get_process(db, row.interview_process_id), get_round(db, round_id)


def complete_round(
    db: Session, round_id: int, payload: RoundCompleteRequest
) -> tuple[InterviewProcess, InterviewRound, str | None]:
    """Record the result of a round.

    Requires ``confirmed``. Re-recording an already-completed round requires
    ``correction=true`` as well: silently overwriting a recorded outcome would
    make the history untrustworthy, and this is data a human entered once
    already.
    """
    if not payload.confirmed:
        raise ValidationError(
            "需要明确确认后才能记录面试结果。", detail={"field": "confirmed"}
        )

    row = get_round(db, round_id)
    process = get_process(db, row.interview_process_id)

    was_completed = row.status is InterviewRoundStatus.completed
    if was_completed and not payload.correction:
        raise ValidationError(
            f"该轮次已经记录过结果（{OUTCOME_LABEL[row.outcome]}）。"
            "如需修改，请使用「更正结果」。",
            detail={"round_id": round_id, "outcome": row.outcome.value},
        )

    previous_outcome = row.outcome
    row.status = InterviewRoundStatus.completed
    row.outcome = payload.outcome
    row.completed_at = _as_utc(payload.completed_at) if payload.completed_at else _now()
    if payload.feedback_text is not None:
        row.feedback_text = payload.feedback_text.strip() or None
    if payload.notes is not None:
        row.notes = payload.notes.strip() or None
    if payload.feedback_tags:
        row.feedback_tags = [tag.value for tag in payload.feedback_tags]
    row.failure_reason = (
        payload.failure_reason if payload.outcome is InterviewOutcome.failed else None
    )
    if payload.preparation is not None:
        row.preparation_json = payload.preparation.model_dump()

    metadata = _round_event_metadata(process, row)
    metadata["outcome"] = payload.outcome.value
    if was_completed:
        metadata["previous_outcome"] = previous_outcome.value

    event_type = (
        EventType.interview_round_corrected
        if was_completed
        else _EVENT_FOR_OUTCOME[payload.outcome]
    )
    application_workflow._append_event(  # noqa: SLF001
        db,
        process.job,
        event_type,
        notes=(
            f"更正面试结果：{row.display_name} → {OUTCOME_LABEL[payload.outcome]}"
            if was_completed
            else f"{row.display_name}：{OUTCOME_LABEL[payload.outcome]}"
        ),
        metadata=metadata,
    )

    job_status: str | None = None
    if payload.outcome is InterviewOutcome.failed and payload.also_record_rejection:
        # Reuse the existing rejection path - one definition of "rejected".
        db.commit()
        outcome = application_workflow.record_rejection(
            db,
            process.job_id,
            RejectRequest(
                reason=payload.failure_reason.value if payload.failure_reason else None,
                note=f"{row.display_name}未通过",
            ),
        )
        job_status = outcome.job.status.value
        process = get_process(db, row.interview_process_id)
        close_process_as_rejected(db, process, reason=payload.failure_reason)

    db.commit()
    log_event(
        logger,
        "interview.round_completed",
        round_id=row.id,
        process_id=process.id,
        outcome=payload.outcome.value,
        correction=was_completed,
    )
    return get_process(db, row.interview_process_id), get_round(db, round_id), job_status


OUTCOME_LABEL: dict[InterviewOutcome, str] = {
    InterviewOutcome.pending: "结果待定",
    InterviewOutcome.passed: "通过",
    InterviewOutcome.failed: "未通过",
    InterviewOutcome.unknown: "未知",
}

_EVENT_FOR_OUTCOME: dict[InterviewOutcome, EventType] = {
    InterviewOutcome.passed: EventType.interview_passed,
    InterviewOutcome.failed: EventType.interview_failed,
    InterviewOutcome.pending: EventType.interview_completed,
    InterviewOutcome.unknown: EventType.interview_completed,
}


def _round_event_metadata(process: InterviewProcess, row: InterviewRound) -> dict[str, Any]:
    """What goes into the audit trail.

    Deliberately excludes ``meeting_url`` and any feedback text: the event
    trail is a milestone log, not a copy of the interview record.
    """
    metadata: dict[str, Any] = {
        "process_id": process.id,
        "round_id": row.id,
        "round_index": row.round_index,
        "round_type": row.round_type.value,
        "round_label": row.display_name,
        # v0.4 key, kept so existing timeline readers keep working.
        "interview_round": row.display_name,
        "applied_event_id": process.applied_event_id,
    }
    if row.scheduled_at:
        metadata["interview_at"] = _as_utc(row.scheduled_at).isoformat()
    return metadata


def _sync_job_status_to_interview(db: Session, process: InterviewProcess) -> None:
    """Move Job.status to ``interview`` when that transition is legal.

    Coarse workflow status only; the detailed truth is the pipeline. Never
    forces ``offer`` or ``rejected`` - those stay explicit human actions.
    """
    job = process.job
    if job.status is JobStatus.interview:
        return
    if application_workflow.can_transition(job.status, JobStatus.interview):
        job.status = JobStatus.interview
        job.review_after = None


# --------------------------------------------------------------------------
# the v0.4 记录面试 path, now feeding the pipeline
# --------------------------------------------------------------------------

#: v0.4 round labels -> v0.8 round types. Only used for a *new* action the user
#: is taking right now; never applied retroactively to stored legacy events.
LEGACY_ROUND_TYPE: dict[str, InterviewRoundType] = {
    "HR": InterviewRoundType.hr,
    "一面": InterviewRoundType.technical,
    "二面": InterviewRoundType.technical,
    "技术面": InterviewRoundType.technical,
    "终面": InterviewRoundType.final,
    "其他": InterviewRoundType.other,
}


def record_interview_round(
    db: Session, job_id: int, payload: InterviewRequest
) -> tuple[InterviewProcess, InterviewRound]:
    """The v0.4 记录面试 action, delegated into the pipeline.

    Creates the process on the current cycle if needed, then appends a round.
    There is no parallel interview path any more.
    """
    process = ensure_process(db, job_id)
    round_type = (
        LEGACY_ROUND_TYPE.get(payload.round.value, InterviewRoundType.other)
        if payload.round
        else InterviewRoundType.other
    )
    return add_round(
        db,
        process.id,
        RoundCreateRequest(
            round_type=round_type,
            custom_round_name=payload.round.value if payload.round else None,
            scheduled_at=payload.interview_at,
            notes=payload.note,
        ),
    )


# --------------------------------------------------------------------------
# derived state
# --------------------------------------------------------------------------


def current_round(process: InterviewProcess) -> InterviewRound | None:
    """The round the user is waiting on.

    The earliest round that is not finished; None when everything is done.
    """
    pending = [
        r
        for r in sorted(process.rounds, key=lambda r: (r.round_index, r.id))
        if r.status in {InterviewRoundStatus.planned, InterviewRoundStatus.scheduled}
    ]
    return pending[0] if pending else None


def rounds_passed(process: InterviewProcess) -> int:
    return sum(1 for r in process.rounds if r.outcome is InterviewOutcome.passed)


def next_scheduled_at(process: InterviewProcess) -> datetime | None:
    times = [
        _as_utc(r.scheduled_at)
        for r in process.rounds
        if r.scheduled_at and r.status is InterviewRoundStatus.scheduled
    ]
    return min(times) if times else None


def legacy_interview_events(db: Session) -> list[ApplicationEvent]:
    """Pre-v0.8 ``interview`` events with no process on their cycle.

    Surfaced as milestones with unknown round detail. Mapping a generic old
    event onto "HR" or "technical" would be inventing history.
    """
    processes = {p.applied_event_id for p in db.scalars(select(InterviewProcess))}
    rows: list[ApplicationEvent] = []

    for job in db.scalars(
        select(Job).options(selectinload(Job.events), selectinload(Job.interview_processes))
    ).unique():
        if job.interview_processes:
            continue
        cycle_ids = {c.applied_event_id for c in build_cycles(job)}
        if cycle_ids & processes:
            continue
        for event in job.events:
            # Only the original v0.4 event kind. v0.8 events always have a
            # process behind them.
            if event.event_type is EventType.interview and not event.metadata_json.get(
                "process_id"
            ):
                rows.append(event)

    rows.sort(key=lambda e: (_as_utc(e.created_at), e.id), reverse=True)
    return rows


# --------------------------------------------------------------------------
# recruiter conversation handoff (v0.5 integration)
# --------------------------------------------------------------------------
#
# The recruiter agent can already spot "are you free Wednesday at 3?". That
# detection produces a **suggestion** and nothing else: no round, no schedule,
# no status change. AI detection is not workflow mutation - the human reads the
# suggestion, confirms, and only then does a round exist.


#: Conversation stages where a proposed time is plausibly an interview.
_SCHEDULING_STAGES = {"interview_scheduling", "screening", "initial_contact"}


def interview_suggestions(db: Session, conversation_id: int) -> list[dict[str, Any]]:
    """Times a recruiter proposed, as suggestions the human may act on.

    Reads stored analyses only - this never calls the model, and calling it
    creates nothing. ``can_add`` is False when the time is ambiguous or the
    conversation has no linked job, because there would be nothing solid to
    prefill.
    """
    from app.models import RecruiterConversation, RecruiterMessage

    conversation = db.scalar(
        select(RecruiterConversation)
        .options(
            selectinload(RecruiterConversation.messages).selectinload(
                RecruiterMessage.analyses
            )
        )
        .where(RecruiterConversation.id == conversation_id)
    )
    if conversation is None:
        raise NotFoundError(
            f"会话 {conversation_id} 不存在", detail={"conversation_id": conversation_id}
        )

    out: list[dict[str, Any]] = []
    for message in conversation.messages:
        if not message.analyses:
            continue
        latest = max(message.analyses, key=lambda a: (a.created_at, a.id))
        payload = latest.result_json or {}
        stage = str(payload.get("conversation_stage") or "")

        for mention in payload.get("dates_times") or []:
            raw = str(mention.get("raw_text") or "")
            normalized = mention.get("normalized_at")
            ambiguous = bool(mention.get("is_ambiguous", True))
            context = str(mention.get("context") or "")

            # Only surface times that plausibly concern an interview.
            if stage not in _SCHEDULING_STAGES and "面试" not in context:
                continue

            scheduled_at: datetime | None = None
            if normalized:
                try:
                    scheduled_at = _as_utc(datetime.fromisoformat(str(normalized)))
                except ValueError:
                    scheduled_at = None

            can_add = bool(conversation.job_id) and scheduled_at is not None and not ambiguous
            if conversation.job_id is None:
                reason = "该会话还没有关联岗位，无法添加到面试流程。"
            elif scheduled_at is None or ambiguous:
                reason = "时间不明确，请人工确认后再填写。"
            else:
                reason = "确认后可添加到面试流程。"

            out.append(
                {
                    "conversation_id": conversation.id,
                    "message_id": message.id,
                    "job_id": conversation.job_id,
                    "scheduled_at": scheduled_at,
                    "raw_text": raw,
                    "is_ambiguous": ambiguous,
                    "suggested_round_type": _guess_round_type(context, stage),
                    "can_add": can_add,
                    "reason": reason,
                }
            )
    return out


def _guess_round_type(context: str, stage: str) -> InterviewRoundType | None:
    """A hint for the prefilled form, not a decision.

    Returns None rather than guessing when nothing in the text points anywhere;
    the human picks the round type either way.
    """
    text = f"{context} {stage}".lower()
    if "技术" in text or "technical" in text:
        return InterviewRoundType.technical
    if "终面" in text or "final" in text:
        return InterviewRoundType.final
    if "hr" in text or "初次" in text or "screening" in text:
        return InterviewRoundType.hr
    return None
