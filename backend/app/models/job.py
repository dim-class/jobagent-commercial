"""Job ORM model.

Duplicate prevention works on two axes:
  * ``content_hash`` - sha256 of the normalized description (+ company/title),
    unique across the table;
  * ``(source, external_id)`` - unique when the source supplies a stable id.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import JobStatus


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual", index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    company: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    city: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    salary_text: Mapped[str | None] = mapped_column(String(128), nullable=True)
    experience_text: Mapped[str | None] = mapped_column(String(128), nullable=True)
    education_text: Mapped[str | None] = mapped_column(String(128), nullable=True)

    raw_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    normalized_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, native_enum=False, length=16),
        nullable=False,
        default=JobStatus.new,
        index=True,
    )

    #: Set by 稍后处理. The job stays eligible but drops out of the immediate
    #: queue until this moment passes. Nothing polls it - the queue simply
    #: compares it to "now" when it is read.
    review_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    analyses: Mapped[list["JobAnalysis"]] = relationship(  # noqa: F821
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobAnalysis.created_at.desc()",
    )
    events: Mapped[list["ApplicationEvent"]] = relationship(  # noqa: F821
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="ApplicationEvent.created_at.desc()",
    )
    #: Recruiter threads. Deleting a job leaves the conversation intact with a
    #: null job_id - the communication history is worth keeping either way.
    conversations: Mapped[list["RecruiterConversation"]] = relationship(  # noqa: F821
        back_populates="job",
        order_by="RecruiterConversation.created_at.desc()",
    )
    #: Interview processes (v0.8). One per application cycle, so a job that was
    #: applied to, reset, and applied to again can carry more than one.
    interview_processes: Mapped[list["InterviewProcess"]] = relationship(  # noqa: F821
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="InterviewProcess.created_at.asc()",
    )
    #: Offers (v0.9). One per application cycle, so a re-applied job can carry
    #: more than one.
    offers: Mapped[list["Offer"]] = relationship(  # noqa: F821
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="Offer.created_at.asc()",
    )

    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_jobs_content_hash"),
        UniqueConstraint("source", "external_id", name="uq_jobs_source_external_id"),
        Index("ix_jobs_city_status", "city", "status"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Job id={self.id} {self.company!r} {self.title!r} {self.city!r}>"
