"""Interview pipeline models (v0.8).

``ApplicationEvent`` is an append-only workflow audit trail. It records that
something happened; it is deliberately not the database for a multi-round
process with schedules, interviewers and feedback that all get edited. Those
live here.

The link that matters is ``InterviewProcess.applied_event_id``: a process
belongs to **one specific application cycle**, not to a job. A job applied to
with Resume A, reset, then applied to again with Resume B has two cycles, and
an interview belongs to exactly one of them. Resolving that through
``Job.status`` or through whichever resume is active today would attribute
outcomes to the wrong variant - see ``services/application_cycles.py``.

``meeting_url`` may embed an access token. It is never logged and never enters
analytics; only the interview UI and its own endpoints ever return it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import (
    InterviewFailureReason,
    InterviewLocationType,
    InterviewOutcome,
    InterviewProcessStatus,
    InterviewRoundStatus,
    InterviewRoundType,
    WithdrawReason,
)


class InterviewProcess(Base, TimestampMixin):
    """One interview process, belonging to one application cycle."""

    __tablename__ = "interview_processes"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: THE attribution link.
    #:
    #: CASCADE, not RESTRICT: the ``applied`` event and this process are both
    #: children of the same job, so RESTRICT never protected the attribution -
    #: it only made the job undeletable, because the ORM removes the events
    #: before the process that points at them. The event trail is append-only,
    #: so the sole way this row disappears is with the job it belongs to.
    applied_event_id: Mapped[int] = mapped_column(
        ForeignKey("application_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[InterviewProcessStatus] = mapped_column(
        SAEnum(InterviewProcessStatus, native_enum=False, length=16),
        nullable=False,
        default=InterviewProcessStatus.ongoing,
        index=True,
    )

    #: Which round type the process ended after, when it ended. Kept so
    #: drop-off analytics never has to re-derive it, and so a later correction
    #: to a round cannot silently rewrite where the candidacy actually stopped.
    ended_after_round_type: Mapped[InterviewRoundType | None] = mapped_column(
        SAEnum(InterviewRoundType, native_enum=False, length=16), nullable=True
    )
    failure_reason: Mapped[InterviewFailureReason | None] = mapped_column(
        SAEnum(InterviewFailureReason, native_enum=False, length=24), nullable=True
    )
    withdraw_reason: Mapped[WithdrawReason | None] = mapped_column(
        SAEnum(WithdrawReason, native_enum=False, length=24), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped["Job"] = relationship(back_populates="interview_processes")  # noqa: F821
    applied_event: Mapped["ApplicationEvent"] = relationship()  # noqa: F821
    rounds: Mapped[list["InterviewRound"]] = relationship(
        back_populates="process",
        cascade="all, delete-orphan",
        order_by="InterviewRound.round_index.asc()",
    )

    __table_args__ = (
        # One process per application cycle. A second one would double-count
        # the same candidacy in every funnel.
        UniqueConstraint("applied_event_id", name="uq_interview_processes_applied_event"),
        Index("ix_interview_processes_job_status", "job_id", "status"),
    )

    @property
    def is_closed(self) -> bool:
        return self.status is not InterviewProcessStatus.ongoing

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<InterviewProcess id={self.id} job={self.job_id} "
            f"cycle={self.applied_event_id} status={self.status}>"
        )


class InterviewRound(Base, TimestampMixin):
    """One round within a process."""

    __tablename__ = "interview_rounds"

    id: Mapped[int] = mapped_column(primary_key=True)
    interview_process_id: Mapped[int] = mapped_column(
        ForeignKey("interview_processes.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: 1-based position in the pipeline. Not unique-constrained: correcting an
    #: order can transiently collide, and a duplicate index is a display quirk,
    #: not a data-integrity failure worth rejecting a user's edit over.
    round_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    round_type: Mapped[InterviewRoundType] = mapped_column(
        SAEnum(InterviewRoundType, native_enum=False, length=16),
        nullable=False,
        default=InterviewRoundType.other,
        index=True,
    )
    custom_round_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    status: Mapped[InterviewRoundStatus] = mapped_column(
        SAEnum(InterviewRoundStatus, native_enum=False, length=16),
        nullable=False,
        default=InterviewRoundStatus.planned,
        index=True,
    )
    outcome: Mapped[InterviewOutcome] = mapped_column(
        SAEnum(InterviewOutcome, native_enum=False, length=16),
        nullable=False,
        default=InterviewOutcome.pending,
        index=True,
    )
    failure_reason: Mapped[InterviewFailureReason | None] = mapped_column(
        SAEnum(InterviewFailureReason, native_enum=False, length=24), nullable=True
    )

    interviewer_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    interviewer_role: Mapped[str | None] = mapped_column(String(128), nullable=True)

    location_type: Mapped[InterviewLocationType] = mapped_column(
        SAEnum(InterviewLocationType, native_enum=False, length=16),
        nullable=False,
        default=InterviewLocationType.unknown,
    )
    #: Potentially sensitive - meeting links often carry an access token.
    #: Never logged, never returned by analytics.
    meeting_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    #: What the interviewer/recruiter actually said.
    feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The user's own recollection. Kept apart from feedback_text so analytics
    #: never mistakes a personal note for something an interviewer said.
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: User-chosen tags. Never inferred from the free text.
    feedback_tags: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    #: {"questions_asked": [], "weak_points": [], "follow_up_topics": []}
    #: One JSON column instead of three nullable text columns.
    preparation_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )

    process: Mapped["InterviewProcess"] = relationship(back_populates="rounds")

    __table_args__ = (
        Index("ix_interview_rounds_process_index", "interview_process_id", "round_index"),
        Index("ix_interview_rounds_status_scheduled", "status", "scheduled_at"),
    )

    @property
    def display_name(self) -> str:
        return (self.custom_round_name or "").strip() or ROUND_TYPE_LABEL.get(
            self.round_type, self.round_type.value
        )

    @property
    def is_technical(self) -> bool:
        from app.models.enums import TECHNICAL_ROUND_TYPES

        return self.round_type in TECHNICAL_ROUND_TYPES

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<InterviewRound id={self.id} process={self.interview_process_id} "
            f"#{self.round_index} {self.round_type} {self.status}/{self.outcome}>"
        )


#: Chinese labels, defined once so API, analytics and UI agree.
ROUND_TYPE_LABEL: dict[InterviewRoundType, str] = {
    InterviewRoundType.hr: "HR面",
    InterviewRoundType.screening: "初筛",
    InterviewRoundType.technical: "技术面",
    InterviewRoundType.coding: "编程面",
    InterviewRoundType.system_design: "系统设计",
    InterviewRoundType.manager: "主管面",
    InterviewRoundType.culture: "文化面",
    InterviewRoundType.final: "终面",
    InterviewRoundType.other: "其他",
}
