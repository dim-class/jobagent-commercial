"""SupervisedSession - M4a bounded-session scaffolding, no navigation.

A row records one human-approved, bounded session: which task it is for,
the caps the human approved (each independently re-checked against the
immutable POC ceilings in ``services/supervised_sessions.py``), and the
exact ``https://www.zhipin.com`` tab it is scoped to. M4a never navigates,
so ``pages_visited`` / ``candidates_extracted`` / ``scrolls_used`` never
leave 0 - the columns exist now so M4b/M4c (separately authorized, not
implemented) can advance them without a new migration.

``SupervisedSessionEvent`` is the append-only audit trail, exactly like
``ApplicationEvent`` and ``OrchestrationEvent``: rows are never updated or
deleted. See CLAUDE.md's "Chrome extension - M4 supervised navigation
policy" and docs/orchestration/ROADMAP.md M4a.
"""

from __future__ import annotations

from datetime import datetime

from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import SupervisedSessionEventType, SupervisedSessionStatus


class SupervisedSession(Base):
    __tablename__ = "supervised_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("job_search_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[SupervisedSessionStatus] = mapped_column(
        SAEnum(SupervisedSessionStatus, native_enum=False, length=16),
        nullable=False,
        default=SupervisedSessionStatus.running,
    )
    #: Human-approved caps. Each is re-validated against the immutable POC
    #: ceiling in the service layer at creation time - never trusted just
    #: because a client sent it.
    page_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    scroll_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Exactly ``"https://www.zhipin.com"`` - the same host
    #: ``extension/manifest.json``'s ``content_scripts.matches`` declares.
    #: Never a full URL: no path, no query, so no session token is ever
    #: stored here.
    tab_origin: Mapped[str] = mapped_column(String(64), nullable=False)
    #: An immutable snapshot of the approved ``JobSearchTask`` criteria at
    #: the moment this session started - name, keywords, city, experience/
    #: education text, salary band, exclusions, resume_id, caps, mode. Taken
    #: from the task row itself (never from client input) so it cannot be
    #: spoofed, and never touched again after creation, so a later edit to
    #: the task cannot rewrite what this session was actually approved
    #: against. Holds search criteria only - never a URL or a token.
    approved_criteria_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    pages_visited: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidates_extracted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scrolls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: A short reason code (e.g. "user_stop", "stale_tab"). Plain string
    #: rather than a DB enum so a later, separately-authorized milestone can
    #: add reasons (cap_reached, wrong_origin, ...) without a migration.
    stop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    task: Mapped["JobSearchTask"] = relationship()  # noqa: F821
    events: Mapped[list["SupervisedSessionEvent"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SupervisedSessionEvent.created_at.asc()",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SupervisedSession id={self.id} task={self.task_id} status={self.status}>"


class SupervisedSessionEvent(Base):
    """Append-only. M4a only ever writes ``started`` and ``stopped``."""

    __tablename__ = "supervised_session_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("supervised_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[SupervisedSessionEventType] = mapped_column(
        SAEnum(SupervisedSessionEventType, native_enum=False, length=16), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped["SupervisedSession"] = relationship(back_populates="events")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<SupervisedSessionEvent id={self.id} session={self.session_id} "
            f"type={self.event_type}>"
        )
