"""ApplicationEvent - append-only audit trail for a job.

Rows are never updated or deleted. Correcting a mistake appends a
``status_reset`` event rather than erasing what happened, so the history stays
an honest record of what the user actually did.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import EventType


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[EventType] = mapped_column(
        SAEnum(EventType, native_enum=False, length=40), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Structured, event-specific detail (skip_reason, interview_round, ...).
    #: One JSON column instead of a nullable column per event kind; the shape
    #: is validated by the Pydantic payloads in ``schemas/application.py``.
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    #: Which resume the human actually submitted (v0.7). Meaningful on
    #: ``applied`` and on the corrective ``application_resume_*`` events.
    #:
    #: A typed FK rather than a key inside ``metadata_json``: it cannot go
    #: dangling, attribution coverage is one SQL count, and a resume that
    #: history references can never be deleted by accident. NULL means "not
    #: recorded" - it is never a shorthand for the currently active resume.
    resume_id: Mapped[int | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    job: Mapped["Job"] = relationship(back_populates="events")  # noqa: F821
    resume: Mapped["Resume | None"] = relationship(  # noqa: F821
        back_populates="application_events"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ApplicationEvent id={self.id} job={self.job_id} type={self.event_type}>"
