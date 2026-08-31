"""Recruiter conversation persistence (v0.5).

Why these live outside ``ApplicationEvent``:

* ``ApplicationEvent`` records **workflow facts** - "HR replied", "I applied".
* ``RecruiterMessage`` records the **communication content** itself.

An event may point at a message through its ``metadata_json``, but message
    bodies are never copied into the event trail.

Nothing here is transmitted anywhere. Rows are either content the human pasted
or uploaded, or an M7 foreground scan of one explicitly selected BOSS chat.
JobAgent sends nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import (
    ConversationStatus,
    MessageDirection,
    RecruiterSource,
)


class RecruiterConversation(Base, TimestampMixin):
    """One thread with one recruiter. May or may not be linked to a Job."""

    __tablename__ = "recruiter_conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Optional on purpose: a recruiter may reach out before the job exists.
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    source: Mapped[RecruiterSource] = mapped_column(
        SAEnum(RecruiterSource, native_enum=False, length=16),
        nullable=False,
        default=RecruiterSource.other,
    )
    recruiter_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    company: Mapped[str | None] = mapped_column(String(256), nullable=True)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)

    status: Mapped[ConversationStatus] = mapped_column(
        SAEnum(ConversationStatus, native_enum=False, length=24),
        nullable=False,
        default=ConversationStatus.needs_reply,
        index=True,
    )
    close_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    #: Set by 稍后跟进. Nothing polls it; the inbox compares it to now on read.
    next_action_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    job: Mapped["Job | None"] = relationship(back_populates="conversations")  # noqa: F821
    messages: Mapped[list["RecruiterMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="RecruiterMessage.created_at.asc()",
    )

    __table_args__ = (Index("ix_recruiter_conversations_status_next", "status", "next_action_at"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RecruiterConversation id={self.id} job={self.job_id} status={self.status}>"


class RecruiterMessage(Base):
    """One message in a thread, from either side.

    ``direction=user`` rows are written only after the human confirms they sent
    the text themselves - JobAgent never sends anything.
    """

    __tablename__ = "recruiter_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("recruiter_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    direction: Mapped[MessageDirection] = mapped_column(
        SAEnum(MessageDirection, native_enum=False, length=16), nullable=False
    )

    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: sha256 of the normalized body - used to avoid capturing the same message
    #: twice **within one conversation** (never globally: "好的，谢谢" is not a
    #: duplicate across unrelated threads).
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: Stable BOSS DOM identity used only by the explicit M7 incremental scan.
    #: Never a security/session token and never read from a URL.
    source_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Whatever timestamp the recruiter's UI displayed, verbatim. Not parsed.
    source_message_time_text: Mapped[str | None] = mapped_column(String(128), nullable=True)

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    conversation: Mapped["RecruiterConversation"] = relationship(back_populates="messages")
    analyses: Mapped[list["RecruiterMessageAnalysis"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="RecruiterMessageAnalysis.created_at.desc()",
    )

    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "direction",
            "content_hash",
            name="uq_recruiter_messages_conversation_content",
        ),
        UniqueConstraint(
            "conversation_id",
            "source_message_id",
            name="uq_recruiter_messages_conversation_source_id",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RecruiterMessage id={self.id} conv={self.conversation_id} dir={self.direction}>"


class RecruiterMessageAnalysis(Base):
    """A cached AI reading of one recruiter message.

    ``result_json`` holds the whole typed result so the shape can evolve
    without a migration, exactly like ``JobAnalysis.result_json``.
    """

    __tablename__ = "recruiter_message_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("recruiter_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    resume_id: Mapped[int | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True
    )

    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    #: sha256 over message + job context + resume + strategy + model + prompt +
    #: reply language. See services/recruiter_message_analyzer.py.
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)

    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    message: Mapped["RecruiterMessage"] = relationship(back_populates="analyses")

    __table_args__ = (
        UniqueConstraint("cache_key", name="uq_recruiter_message_analyses_cache_key"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RecruiterMessageAnalysis id={self.id} message={self.message_id}>"
