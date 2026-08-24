"""Resume ORM model.

Since v0.7 a Resume row *is* a variant. There is no separate ResumeVariant
table: a variant differs from its parent by content, and the content already
lives here, so a second table would only add a join and a way to disagree.

Two different notions of "current" exist and must never be conflated:

``is_active``
    the **analysis** resume - what a newly captured job is matched against;

the resume recorded on an application cycle
    what the human actually submitted for one specific application.

Switching ``is_active`` says nothing about past applications. See
``services/application_cycles.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Resume(Base, TimestampMixin):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)  # pdf | docx | txt
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parsed_profile_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    #: The **analysis** resume flag - not "the resume I apply with".
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    # --- variant metadata (v0.7) ----------------------------------------

    #: Human-facing name, e.g. "Cloud版". Defaults to the filename on upload.
    variant_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Coarse bucket for grouping, e.g. "cloud" / "devops" / "infra".
    variant_group: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: Set when this row was cloned from another variant.
    parent_resume_id: Mapped[int | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Archived variants stay in historical analytics forever; they just cannot
    #: be picked for a *new* application. Deleting them would erase history.
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    analyses: Mapped[list["JobAnalysis"]] = relationship(  # noqa: F821
        back_populates="resume", cascade="all, delete-orphan"
    )
    #: Applications that recorded this resume. Deliberately *not* cascading:
    #: a resume referenced by history must never be deleted out from under it.
    application_events: Mapped[list["ApplicationEvent"]] = relationship(  # noqa: F821
        back_populates="resume"
    )
    children: Mapped[list["Resume"]] = relationship(
        back_populates="parent", remote_side="Resume.parent_resume_id"
    )
    parent: Mapped["Resume | None"] = relationship(
        back_populates="children", remote_side="Resume.id"
    )

    __table_args__ = (
        Index("ix_resumes_active_created", "is_active", "created_at"),
        Index("ix_resumes_archived_created", "archived_at", "created_at"),
    )

    @property
    def archived(self) -> bool:
        return self.archived_at is not None

    @property
    def display_name(self) -> str:
        """What every UI and every observation sentence should call this row."""
        return (self.variant_name or "").strip() or self.filename

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Resume id={self.id} variant={self.display_name!r} "
            f"active={self.is_active} archived={self.archived}>"
        )
