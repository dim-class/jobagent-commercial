"""The only place ``Job.status`` is allowed to change (v0.4).

Routes never mutate status directly - they call one of the record_* functions
here, each of which does exactly two things: move the status and append an
``ApplicationEvent``. History is append-only; a correction adds a
``status_reset`` event rather than deleting anything.

Nothing here contacts a recruitment site. ``applied`` means "the human told us
they applied, out there, themselves". JobAgent submits nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.models import ApplicationEvent, EventType, Job, JobStatus, Resume, ResumeUsage
from app.schemas.application import (
    AttributeResumeRequest,
    InterviewRequest,
    LaterPreset,
    LaterRequest,
    MarkAppliedRequest,
    OfferRequest,
    RejectRequest,
    ReplyRequest,
    ResetRequest,
    SkipRequest,
)
from app.services.application_cycles import build_cycles
from app.services.timezones import local_now, start_of_local_tomorrow

logger = get_logger(__name__)

#: Statuses a job may move to from a given status.
#
# Deliberately permissive: real hiring processes skip steps (an interview can
# be scheduled with no recorded HR reply). Only clearly nonsensical jumps are
# blocked, and an explicit reset always provides an escape hatch.
_OPEN = {JobStatus.new, JobStatus.reviewed, JobStatus.saved}

ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.new: {JobStatus.applied, JobStatus.skipped, JobStatus.saved, JobStatus.reviewed},
    JobStatus.reviewed: {JobStatus.applied, JobStatus.skipped, JobStatus.saved},
    JobStatus.saved: {JobStatus.applied, JobStatus.skipped, JobStatus.reviewed},
    JobStatus.skipped: set(),  # only via reset
    JobStatus.applied: {
        JobStatus.replied,
        JobStatus.interview,
        JobStatus.offer,
        JobStatus.rejected,
    },
    JobStatus.replied: {JobStatus.interview, JobStatus.offer, JobStatus.rejected},
    JobStatus.interview: {JobStatus.offer, JobStatus.rejected, JobStatus.interview},
    JobStatus.offer: {JobStatus.rejected},
    JobStatus.rejected: set(),  # only via reset
}

#: Where a reset returns a job to.
RESET_STATUS = JobStatus.reviewed


@dataclass(slots=True)
class WorkflowOutcome:
    job: Job
    event: ApplicationEvent
    previous_status: JobStatus
    message: str


class InvalidTransitionError(ValidationError):
    code = "invalid_transition"


def get_job(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise NotFoundError(f"岗位 {job_id} 不存在", detail={"job_id": job_id})
    return job


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    if current == target:
        return True
    return target in ALLOWED_TRANSITIONS.get(current, set())


def _assert_transition(job: Job, target: JobStatus) -> None:
    if not can_transition(job.status, target):
        raise InvalidTransitionError(
            f"不能从「{_STATUS_LABEL[job.status]}」直接变更为「{_STATUS_LABEL[target]}」。"
            "如需修改，请先「恢复待处理」。",
            detail={"from": job.status.value, "to": target.value},
        )


_STATUS_LABEL: dict[JobStatus, str] = {
    JobStatus.new: "待处理",
    JobStatus.reviewed: "已查看",
    JobStatus.saved: "已收藏",
    JobStatus.skipped: "已跳过",
    JobStatus.applied: "已投递",
    JobStatus.replied: "已回复",
    JobStatus.interview: "面试中",
    JobStatus.offer: "已offer",
    JobStatus.rejected: "已拒绝",
}


def _append_event(
    db: Session,
    job: Job,
    event_type: EventType,
    *,
    notes: str | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: datetime | None = None,
    resume_id: int | None = None,
) -> ApplicationEvent:
    event = ApplicationEvent(
        job_id=job.id,
        event_type=event_type,
        notes=notes,
        metadata_json=metadata or {},
        resume_id=resume_id,
    )
    if created_at is not None:
        event.created_at = _as_utc(created_at)
    db.add(event)
    return event


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _commit(
    db: Session, job: Job, event: ApplicationEvent, previous: JobStatus, message: str
) -> WorkflowOutcome:
    db.commit()
    db.refresh(job)
    db.refresh(event)
    log_event(
        logger,
        "workflow.status_changed",
        job_id=job.id,
        frm=previous.value,
        to=job.status.value,
        event_type=event.event_type.value,
    )
    return WorkflowOutcome(job=job, event=event, previous_status=previous, message=message)


# --------------------------------------------------------------------------
# the decisions
# --------------------------------------------------------------------------


def _resume_for_new_application(db: Session, resume_id: int | None) -> Resume | None:
    """Validate a resume chosen for a *new* application.

    Archived variants stay selectable for historical corrections but not for a
    fresh application - the point of archiving is "stop using this one".
    """
    if resume_id is None:
        return None
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise NotFoundError(f"简历 {resume_id} 不存在", detail={"resume_id": resume_id})
    if resume.archived:
        raise ValidationError(
            f"「{resume.display_name}」已归档，不能用于新的投递记录。"
            "如果这是历史投递的补充记录，请使用「补充历史简历」。",
            detail={"resume_id": resume_id, "archived": True},
        )
    return resume


def _attribution_metadata(resume: Resume | None, usage: ResumeUsage) -> dict[str, Any]:
    """The event side-car. ``resume_id`` on the column stays authoritative;
    the name is a snapshot so old events stay readable after a rename."""
    metadata: dict[str, Any] = {"resume_usage": usage.value}
    if resume is not None:
        metadata["resume_variant_name"] = resume.display_name
    return metadata


def mark_applied(db: Session, job_id: int, payload: MarkAppliedRequest) -> WorkflowOutcome:
    """Record that the human applied on the recruitment platform themselves.

    Requires ``confirmed=true``: this represents a real external action, so it
    is never inferred and never done in bulk.

    The resume recorded here is **the one the human says they submitted**, not
    the active analysis resume. The caller may default the UI to the active
    resume, but nothing here substitutes it: an unspecified resume is stored as
    ``unknown`` rather than guessed.
    """
    if not payload.confirmed:
        raise ValidationError(
            "需要明确确认后才能标记为已投递。",
            detail={"field": "confirmed"},
        )
    job = get_job(db, job_id)
    previous = job.status
    _assert_transition(job, JobStatus.applied)

    resume = _resume_for_new_application(db, payload.resume_id)
    usage = payload.resume_usage

    job.status = JobStatus.applied
    job.review_after = None
    metadata = {"source": "manual_confirmation", **_attribution_metadata(resume, usage)}
    event = _append_event(
        db,
        job,
        EventType.applied,
        notes=payload.note or "用户确认已在招聘平台完成投递",
        metadata=metadata,
        created_at=payload.applied_at,
        resume_id=resume.id if resume else None,
    )
    outcome = _commit(db, job, event, previous, "已记录投递")
    log_event(
        logger,
        "workflow.application_resume_recorded",
        job_id=job.id,
        resume_id=resume.id if resume else None,
        usage=usage.value,
    )
    return outcome


def attribute_resume(
    db: Session, job_id: int, payload: AttributeResumeRequest
) -> WorkflowOutcome:
    """Fill in or correct which resume an existing application used.

    Appends a corrective event rather than editing the original ``applied``
    row: history stays intact and the correction itself is auditable. Archived
    resumes *are* allowed here - past applications may well have used one.
    """
    job = get_job(db, job_id)
    cycles = build_cycles(job)
    if not cycles:
        raise ValidationError(
            "该岗位还没有投递记录，无法补充简历信息。", detail={"job_id": job_id}
        )

    if payload.applied_event_id is not None:
        target = next(
            (c for c in cycles if c.applied_event_id == payload.applied_event_id), None
        )
        if target is None:
            raise NotFoundError(
                "找不到对应的投递记录。", detail={"applied_event_id": payload.applied_event_id}
            )
    else:
        target = cycles[-1]

    resume: Resume | None = None
    if payload.resume_id is not None:
        resume = db.get(Resume, payload.resume_id)
        if resume is None:
            raise NotFoundError(
                f"简历 {payload.resume_id} 不存在", detail={"resume_id": payload.resume_id}
            )

    previous_resume_id = target.resume_id
    already_known = target.resume_attributed
    event_type = (
        EventType.application_resume_changed
        if already_known
        else EventType.application_resume_attributed
    )

    label = resume.display_name if resume else _USAGE_LABEL[payload.resume_usage]
    metadata: dict[str, Any] = {
        "applied_event_id": target.applied_event_id,
        "previous_resume_id": previous_resume_id,
        **_attribution_metadata(resume, payload.resume_usage),
    }
    event = _append_event(
        db,
        job,
        event_type,
        notes=payload.note or f"补充历史投递所用简历：{label}",
        metadata=metadata,
        resume_id=resume.id if resume else None,
    )
    db.commit()
    db.refresh(job)
    db.refresh(event)
    log_event(
        logger,
        "workflow.application_resume_attributed",
        job_id=job.id,
        applied_event_id=target.applied_event_id,
        previous_resume_id=previous_resume_id,
        resume_id=resume.id if resume else None,
        corrected=already_known,
    )
    return WorkflowOutcome(
        job=job,
        event=event,
        previous_status=job.status,
        message="已更新该次投递所使用的简历" if already_known else "已补充该次投递所使用的简历",
    )


_USAGE_LABEL: dict[ResumeUsage, str] = {
    ResumeUsage.used: "已提交简历",
    ResumeUsage.no_resume: "未提交简历",
    ResumeUsage.unknown: "不确定",
}


def skip(db: Session, job_id: int, payload: SkipRequest) -> WorkflowOutcome:
    job = get_job(db, job_id)
    previous = job.status
    _assert_transition(job, JobStatus.skipped)

    job.status = JobStatus.skipped
    job.review_after = None
    reason = (payload.reason or "").strip() or None
    notes = payload.note or (f"跳过原因：{reason}" if reason else "已跳过")
    event = _append_event(
        db, job, EventType.skipped, notes=notes, metadata={"skip_reason": reason} if reason else {}
    )
    return _commit(db, job, event, previous, "已跳过")


def defer(db: Session, job_id: int, payload: LaterRequest) -> WorkflowOutcome:
    """稍后处理 - not a skip. The job stays eligible, just not right now."""
    job = get_job(db, job_id)
    previous = job.status

    if payload.preset is LaterPreset.custom:
        if payload.review_after is None:
            raise ValidationError("请选择稍后处理的日期。", detail={"field": "review_after"})
        review_after = _as_utc(payload.review_after)
    elif payload.preset is LaterPreset.today:
        review_after = _as_utc(local_now() + timedelta(hours=4))
    else:
        review_after = _as_utc(start_of_local_tomorrow())

    job.review_after = review_after
    event = _append_event(
        db,
        job,
        EventType.later,
        notes=payload.note or "稍后处理",
        metadata={"review_after": review_after.isoformat(), "preset": payload.preset.value},
    )
    # Status is untouched: deferring is a scheduling hint, not a decision.
    return _commit(db, job, event, previous, "已移出当前队列，到期后会自动回来")


def reset_status(db: Session, job_id: int, payload: ResetRequest) -> WorkflowOutcome:
    """Undo a mistake without erasing it.

    The previous events stay exactly where they are; this appends a
    ``status_reset`` so the trail reads applied -> status_reset -> applied.
    """
    job = get_job(db, job_id)
    previous = job.status
    if previous == RESET_STATUS and job.review_after is None:
        raise ValidationError("该岗位已经是待处理状态。", detail={"status": previous.value})

    job.status = RESET_STATUS
    job.review_after = None
    event = _append_event(
        db,
        job,
        EventType.status_reset,
        notes=payload.note or f"状态由「{_STATUS_LABEL[previous]}」恢复为待处理",
        metadata={"from": previous.value, "to": RESET_STATUS.value},
    )
    return _commit(db, job, event, previous, "已恢复为待处理")


def record_reply(db: Session, job_id: int, payload: ReplyRequest) -> WorkflowOutcome:
    job = get_job(db, job_id)
    previous = job.status
    _assert_transition(job, JobStatus.replied)

    job.status = JobStatus.replied
    event = _append_event(
        db,
        job,
        EventType.replied,
        notes=payload.note or "记录 HR 回复",
        metadata={"response_type": payload.response_type.value},
        created_at=payload.replied_at,
    )
    return _commit(db, job, event, previous, "已记录 HR 回复")


def record_interview(db: Session, job_id: int, payload: InterviewRequest) -> WorkflowOutcome:
    """记录面试 - delegates into the v0.8 interview pipeline.

    There is deliberately no parallel interview path any more: this creates (or
    reuses) the process on the job's current application cycle and appends a
    round, so the pipeline is always the source of truth. Job.status still
    moves to ``interview`` here, because that is this module's job.

    Imported lazily - ``interview_pipeline`` depends on this module for the
    event-append and status-transition helpers.
    """
    from app.services import interview_pipeline

    job = get_job(db, job_id)
    previous = job.status
    _assert_transition(job, JobStatus.interview)

    _process, row = interview_pipeline.record_interview_round(db, job_id, payload)

    db.refresh(job)
    event = db.scalar(
        select(ApplicationEvent)
        .where(ApplicationEvent.job_id == job_id)
        .order_by(ApplicationEvent.created_at.desc(), ApplicationEvent.id.desc())
        .limit(1)
    )
    log_event(
        logger,
        "workflow.status_changed",
        job_id=job.id,
        frm=previous.value,
        to=job.status.value,
        event_type=event.event_type.value if event else "interview",
    )
    return WorkflowOutcome(
        job=job,
        event=event,
        previous_status=previous,
        message=f"已记录面试：{row.display_name}",
    )


def record_offer(db: Session, job_id: int, payload: OfferRequest) -> WorkflowOutcome:
    job = get_job(db, job_id)
    previous = job.status
    _assert_transition(job, JobStatus.offer)

    job.status = JobStatus.offer
    metadata: dict[str, Any] = {}
    if payload.salary_text:
        metadata["offer_salary"] = payload.salary_text.strip()
    event = _append_event(
        db, job, EventType.offer, notes=payload.note or "记录 Offer", metadata=metadata
    )
    _close_interview_process(db, job, offered=True)
    outcome = _commit(db, job, event, previous, "已记录 Offer")

    # v0.9: the same action also opens the structured offer record, so there is
    # no parallel "offer happened" path. Imported lazily - offer_management
    # depends on this module for the event-append helper.
    from app.services import offer_management

    offer_management.record_offer_from_workflow(db, job_id, payload)
    db.refresh(job)
    return outcome


def record_rejection(db: Session, job_id: int, payload: RejectRequest) -> WorkflowOutcome:
    job = get_job(db, job_id)
    previous = job.status
    _assert_transition(job, JobStatus.rejected)

    job.status = JobStatus.rejected
    reason = (payload.reason or "").strip() or None
    event = _append_event(
        db,
        job,
        EventType.rejected,
        notes=payload.note or (f"未通过：{reason}" if reason else "记录拒绝"),
        metadata={"reject_reason": reason} if reason else {},
    )
    _close_interview_process(db, job, offered=False)
    return _commit(db, job, event, previous, "已记录拒绝")


def _close_interview_process(db: Session, job: Job, *, offered: bool) -> None:
    """Mirror an offer/rejection onto the job's open interview process.

    The workflow milestone stays in the event trail; this only records that the
    interview process itself ended, and where. Previously passed rounds are
    untouched - a rejection at the final round does not erase the four rounds
    the candidate cleared to get there.
    """
    from app.services import interview_pipeline

    process = interview_pipeline.process_for_job(db, job.id)
    if process is None or process.is_closed:
        return
    if offered:
        interview_pipeline.close_process_as_offer(db, process)
    else:
        interview_pipeline.close_process_as_rejected(db, process)


def set_status(
    db: Session, job_id: int, target: JobStatus, *, note: str | None = None
) -> WorkflowOutcome:
    """Generic status change used by the legacy PATCH /api/jobs/{id} route.

    Exists so that *every* status change - including the older editing route -
    goes through the same transition rules and leaves the same audit trail.
    Prefer the dedicated record_* helpers, which capture richer metadata.
    """
    job = get_job(db, job_id)
    previous = job.status
    if previous == target:
        raise ValidationError(
            f"岗位已经是「{_STATUS_LABEL[target]}」状态。", detail={"status": target.value}
        )
    _assert_transition(job, target)

    job.status = target
    if target not in {JobStatus.applied, JobStatus.skipped}:
        job.review_after = None

    event = _append_event(
        db,
        job,
        EventType.status_changed,
        notes=note or f"{previous.value} -> {target.value}",
        metadata={"from": previous.value, "to": target.value},
    )
    return _commit(db, job, event, previous, f"状态已更新为「{_STATUS_LABEL[target]}」")


# --------------------------------------------------------------------------
# history
# --------------------------------------------------------------------------


def list_events(db: Session, job_id: int) -> list[ApplicationEvent]:
    get_job(db, job_id)
    return list(
        db.scalars(
            select(ApplicationEvent)
            .where(ApplicationEvent.job_id == job_id)
            .order_by(ApplicationEvent.created_at.asc(), ApplicationEvent.id.asc())
        )
    )


def add_note(db: Session, job_id: int, note: str) -> ApplicationEvent:
    job = get_job(db, job_id)
    event = _append_event(db, job, EventType.note, notes=note)
    db.commit()
    db.refresh(event)
    return event
