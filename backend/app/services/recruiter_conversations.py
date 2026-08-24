"""Recruiter conversation persistence and the human-decision boundary (v0.5).

What this service does: store what the recruiter said, store what the user says
they sent, schedule follow-ups, and close threads.

What it never does: read an inbox, send a message, or move ``Job.status`` on
its own. Job status only ever changes through
``services.application_workflow`` after an explicit human confirmation, exactly
as in v0.4.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.models import (
    ApplicationEvent,
    ConversationStatus,
    EventType,
    Job,
    JobStatus,
    MessageDirection,
    RecruiterConversation,
    RecruiterMessage,
    RecruiterSource,
)
from app.models.enums import OPEN_CONVERSATION_STATUSES
from app.schemas.recruiter import (
    CloseRequest,
    ConversationCreate,
    ConversationUpdate,
    FollowUpPreset,
    FollowUpRequest,
    InputMode,
    MarkSentRequest,
)
from app.services.hashing import hash_text
from app.services.recruiter_parser import ParsedMessage, split_conversation
from app.services.timezones import local_now, start_of_local_tomorrow

logger = get_logger(__name__)


@dataclass(slots=True)
class MessageIntake:
    message: RecruiterMessage
    duplicate: bool
    parsed_count: int
    warnings: list[str]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# lookup
# --------------------------------------------------------------------------


def load_conversation(db: Session, conversation_id: int) -> RecruiterConversation:
    conversation = db.scalar(
        select(RecruiterConversation)
        .options(
            selectinload(RecruiterConversation.messages).selectinload(RecruiterMessage.analyses),
            selectinload(RecruiterConversation.job),
        )
        .where(RecruiterConversation.id == conversation_id)
    )
    if conversation is None:
        raise NotFoundError(
            f"沟通记录 {conversation_id} 不存在", detail={"conversation_id": conversation_id}
        )
    return conversation


def list_conversations(db: Session) -> list[RecruiterConversation]:
    return list(
        db.scalars(
            select(RecruiterConversation)
            .options(
                selectinload(RecruiterConversation.messages).selectinload(
                    RecruiterMessage.analyses
                ),
                selectinload(RecruiterConversation.job),
            )
            .order_by(RecruiterConversation.updated_at.desc())
        ).unique()
    )


def conversations_for_job(db: Session, job_id: int) -> list[RecruiterConversation]:
    return [c for c in list_conversations(db) if c.job_id == job_id]


def get_message(db: Session, conversation_id: int, message_id: int) -> RecruiterMessage:
    conversation = load_conversation(db, conversation_id)
    for message in conversation.messages:
        if message.id == message_id:
            return message
    raise NotFoundError(f"消息 {message_id} 不存在", detail={"message_id": message_id})


# --------------------------------------------------------------------------
# create / update
# --------------------------------------------------------------------------


def create_conversation(db: Session, payload: ConversationCreate) -> RecruiterConversation:
    job: Job | None = None
    if payload.job_id is not None:
        job = db.get(Job, payload.job_id)
        if job is None:
            raise NotFoundError(f"岗位 {payload.job_id} 不存在", detail={"job_id": payload.job_id})

    conversation = RecruiterConversation(
        job_id=payload.job_id,
        source=payload.source,
        recruiter_name=payload.recruiter_name,
        # Fall back to the linked job so a thread is identifiable in the list.
        company=payload.company or (job.company if job else None),
        title=payload.title or (job.title if job else None),
        status=ConversationStatus.needs_reply,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    log_event(
        logger,
        "recruiter.conversation_created",
        conversation_id=conversation.id,
        job_id=conversation.job_id,
        source=conversation.source.value,
    )
    return load_conversation(db, conversation.id)


def update_conversation(
    db: Session, conversation_id: int, payload: ConversationUpdate
) -> RecruiterConversation:
    conversation = load_conversation(db, conversation_id)
    data = payload.model_dump(exclude_unset=True)

    if "job_id" in data:
        job_id = data["job_id"]
        if job_id is not None and db.get(Job, job_id) is None:
            raise NotFoundError(f"岗位 {job_id} 不存在", detail={"job_id": job_id})
        conversation.job_id = job_id

    for field in ("source", "recruiter_name", "company", "title", "status"):
        if field in data:
            setattr(conversation, field, data[field])

    db.commit()
    log_event(
        logger, "recruiter.conversation_updated", conversation_id=conversation.id,
        job_id=conversation.job_id,
    )
    return load_conversation(db, conversation.id)


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------


def normalize_for_hash(text: str) -> str:
    return " ".join((text or "").split()).lower()


def find_duplicate_message(
    conversation: RecruiterConversation, *, direction: MessageDirection, content_hash: str
) -> RecruiterMessage | None:
    """Duplicates are scoped to one thread and one direction.

    "好的，谢谢" appearing in two unrelated conversations is two real messages,
    so this never de-duplicates globally.
    """
    for message in conversation.messages:
        if message.direction is direction and message.content_hash == content_hash:
            return message
    return None


def add_message(
    db: Session,
    conversation: RecruiterConversation,
    *,
    text: str,
    direction: MessageDirection,
    source_message_time_text: str | None = None,
    captured_at: datetime | None = None,
    commit: bool = True,
) -> tuple[RecruiterMessage, bool]:
    """Append one message. Returns ``(message, duplicate)``."""
    body = (text or "").strip()
    if not body:
        raise ValidationError("消息内容为空，请粘贴 HR 消息后再保存。")

    content_hash = hash_text(normalize_for_hash(body))
    existing = find_duplicate_message(
        conversation, direction=direction, content_hash=content_hash
    )
    if existing is not None:
        log_event(
            logger,
            "recruiter.message_duplicate",
            conversation_id=conversation.id,
            message_id=existing.id,
        )
        return existing, True

    now = _utcnow()
    message = RecruiterMessage(
        conversation_id=conversation.id,
        direction=direction,
        raw_text=body,
        content_hash=content_hash,
        source_message_time_text=source_message_time_text,
        captured_at=_as_utc(captured_at) if captured_at else now,
        created_at=now,
    )
    db.add(message)

    conversation.last_message_at = message.captured_at
    if direction is MessageDirection.recruiter:
        # A new recruiter message means the ball is back with the user.
        conversation.status = ConversationStatus.needs_reply
        conversation.next_action_at = None
    elif conversation.status is ConversationStatus.needs_reply:
        conversation.status = ConversationStatus.waiting_recruiter

    if commit:
        db.commit()
        db.refresh(message)
    else:
        db.flush()

    log_event(
        logger,
        "recruiter.message_added",
        conversation_id=conversation.id,
        message_id=message.id,
        direction=direction.value,
        chars=len(body),
    )
    return message, False


def add_text_intake(
    db: Session,
    conversation: RecruiterConversation,
    *,
    text: str,
    direction: MessageDirection,
    input_mode: InputMode,
    source_message_time_text: str | None = None,
) -> MessageIntake:
    """Parse a paste into one or more messages and store them in one transaction.

    Returns the message that should be analyzed - the last recruiter turn when
    a transcript was pasted, otherwise the single message.
    """
    parsed = split_conversation(text, mode=input_mode, default_direction=direction)
    if not parsed.messages:
        raise ValidationError("没有检测到消息内容，请粘贴 HR 消息后再保存。")

    stored: list[tuple[RecruiterMessage, bool]] = []
    for item in parsed.messages:
        stored.append(
            add_message(
                db,
                conversation,
                text=item.text,
                direction=item.direction,
                source_message_time_text=source_message_time_text,
                commit=False,
            )
        )
    db.commit()
    for message, _ in stored:
        db.refresh(message)

    target = _pick_analysis_target(stored, direction)
    duplicate = all(dup for _, dup in stored)
    return MessageIntake(
        message=target,
        duplicate=duplicate,
        parsed_count=len(stored),
        warnings=list(parsed.warnings),
    )


def _pick_analysis_target(
    stored: list[tuple[RecruiterMessage, bool]], fallback_direction: MessageDirection
) -> RecruiterMessage:
    for message, _ in reversed(stored):
        if message.direction is MessageDirection.recruiter:
            return message
    return stored[-1][0]


def transcript_to_messages(
    transcript_messages: list[dict[str, Any]], *, default: MessageDirection
) -> list[ParsedMessage]:
    """Map a vision transcript onto typed directions."""
    out: list[ParsedMessage] = []
    for item in transcript_messages:
        speaker = str(item.get("speaker") or "").strip().lower()
        direction = MessageDirection.user if speaker == "user" else MessageDirection.recruiter
        text = str(item.get("text") or "").strip()
        if text:
            out.append(ParsedMessage(direction=direction or default, text=text))
    return out


# --------------------------------------------------------------------------
# the human decisions
# --------------------------------------------------------------------------


def mark_sent(
    db: Session, conversation_id: int, payload: MarkSentRequest
) -> tuple[RecruiterConversation, RecruiterMessage]:
    """Record that the human sent a reply themselves, outside JobAgent.

    Stores the **final edited text**, not the model's draft. Requires explicit
    confirmation: JobAgent transmits nothing and must never imply that it did.
    """
    if not payload.confirmed:
        raise ValidationError(
            "需要确认后才能标记为已回复。", detail={"field": "confirmed"}
        )
    conversation = load_conversation(db, conversation_id)

    message, duplicate = add_message(
        db,
        conversation,
        text=payload.final_text,
        direction=MessageDirection.user,
        commit=False,
    )
    conversation.status = ConversationStatus.waiting_recruiter
    conversation.next_action_at = None

    if payload.record_job_event and conversation.job_id is not None:
        db.add(
            ApplicationEvent(
                job_id=conversation.job_id,
                event_type=EventType.candidate_reply,
                notes="用户确认已在外部渠道回复招聘方",
                metadata_json={
                    "conversation_id": conversation.id,
                    "message_id": message.id,
                    "source": "manual_confirmation",
                },
            )
        )

    db.commit()
    db.refresh(message)
    log_event(
        logger,
        "recruiter.marked_sent",
        conversation_id=conversation.id,
        message_id=message.id,
        duplicate=duplicate,
        job_id=conversation.job_id,
    )
    return load_conversation(db, conversation.id), message


def schedule_follow_up(
    db: Session, conversation_id: int, payload: FollowUpRequest
) -> RecruiterConversation:
    conversation = load_conversation(db, conversation_id)

    if payload.preset is FollowUpPreset.custom:
        if payload.next_action_at is None:
            raise ValidationError("请选择跟进日期。", detail={"field": "next_action_at"})
        when = _as_utc(payload.next_action_at)
    elif payload.preset is FollowUpPreset.in_3_days:
        when = _as_utc(start_of_local_tomorrow() + timedelta(days=2))
    elif payload.preset is FollowUpPreset.in_1_week:
        when = _as_utc(start_of_local_tomorrow() + timedelta(days=6))
    else:
        when = _as_utc(start_of_local_tomorrow())

    conversation.next_action_at = when
    conversation.status = ConversationStatus.waiting_recruiter
    db.commit()
    log_event(
        logger, "recruiter.follow_up_scheduled", conversation_id=conversation.id,
        preset=payload.preset.value,
    )
    return load_conversation(db, conversation.id)


def close_conversation(
    db: Session, conversation_id: int, payload: CloseRequest
) -> tuple[RecruiterConversation, bool]:
    """Close a thread. Returns ``(conversation, job_rejection_recorded)``.

    Closing a conversation is not a job decision. ``also_record_job_rejection``
    is opt-in and, when set, goes through ``application_workflow`` like every
    other status change.
    """
    conversation = load_conversation(db, conversation_id)
    conversation.status = ConversationStatus.closed
    conversation.close_reason = payload.reason.value
    conversation.next_action_at = None
    db.commit()

    recorded = False
    if payload.also_record_job_rejection and conversation.job_id is not None:
        from app.schemas.application import RejectRequest
        from app.services import application_workflow

        job = db.get(Job, conversation.job_id)
        if job is not None and job.status is not JobStatus.rejected:
            application_workflow.record_rejection(
                db,
                conversation.job_id,
                RejectRequest(reason=payload.reason.value, note=payload.note),
            )
            recorded = True

    log_event(
        logger,
        "recruiter.conversation_closed",
        conversation_id=conversation.id,
        reason=payload.reason.value,
        job_rejection=recorded,
    )
    return load_conversation(db, conversation.id), recorded


# --------------------------------------------------------------------------
# derived state
# --------------------------------------------------------------------------


def effective_status(conversation: RecruiterConversation, *, now: datetime | None = None) -> ConversationStatus:
    """Status as the inbox should show it, with due follow-ups promoted."""
    if conversation.status is ConversationStatus.closed:
        return ConversationStatus.closed
    moment = now or local_now()
    if conversation.next_action_at is not None:
        due = conversation.next_action_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due <= moment:
            return ConversationStatus.follow_up_due
    return conversation.status


def latest_message(conversation: RecruiterConversation) -> RecruiterMessage | None:
    if not conversation.messages:
        return None
    return max(conversation.messages, key=lambda m: (m.created_at, m.id))


def suggests_recruiter_reply_event(conversation: RecruiterConversation) -> bool:
    """Whether to offer the 记录为HR回复 button.

    Offering it is the whole point - the status change itself still requires
    the human to press it.
    """
    if conversation.job_id is None or conversation.job is None:
        return False
    newest = latest_message(conversation)
    if newest is None or newest.direction is not MessageDirection.recruiter:
        return False
    return conversation.job.status is JobStatus.applied


def is_open(conversation: RecruiterConversation, *, now: datetime | None = None) -> bool:
    return effective_status(conversation, now=now) in OPEN_CONVERSATION_STATUSES


__all__ = [
    "MessageIntake",
    "add_message",
    "add_text_intake",
    "close_conversation",
    "conversations_for_job",
    "create_conversation",
    "effective_status",
    "find_duplicate_message",
    "get_message",
    "is_open",
    "latest_message",
    "list_conversations",
    "load_conversation",
    "mark_sent",
    "normalize_for_hash",
    "schedule_follow_up",
    "suggests_recruiter_reply_event",
    "transcript_to_messages",
    "update_conversation",
]
