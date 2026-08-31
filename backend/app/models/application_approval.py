"""One human confirmation authorizing one application to one job (M6).

This table is the *sole* authority for whether an application may be executed.
Nothing else - not a feature flag, not an AI verdict, not a queue position,
not a previous approval - authorizes anything. See CLAUDE.md, "M6 -
human-confirmed single application execution".

Everything the user was shown at confirmation time is snapshotted here, so a
later change to the job, the resume or the answers can be *detected* rather
than assumed away: a stale approval fails closed instead of executing against
inputs the human never saw.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

#: An approval may be used once. `consumed` is terminal whatever the outcome -
#: a retry is a new human confirmation, never a re-use of this row.
APPROVAL_STATES = ("pending", "executing", "consumed", "invalidated")

#: What the one attempt turned out to be. `unknown` is a real answer, not a
#: gap: it means the site's response could not be verified, and it must never
#: be quietly upgraded to `applied`.
APPROVAL_OUTCOMES = ("applied", "unknown", "failed")


class ApplicationApproval(Base, TimestampMixin):
    __tablename__ = "application_approvals"
    __table_args__ = (
        Index("ix_application_approvals_job_id", "job_id"),
        Index("ix_application_approvals_state", "state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    #: The one job this confirmation authorizes. Never a list, never a filter.
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )

    # --- identity snapshot, taken from what the human was shown ------------
    company: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    #: The resume variant the human chose to submit. RESTRICT, like
    #: `application_events.resume_id`: a resume an approval points at must not
    #: be deleted out from under it.
    resume_id: Mapped[int] = mapped_column(
        ForeignKey("resumes.id", ondelete="RESTRICT"), nullable=False
    )
    #: The resume's content hash *at confirmation time*. Editing or replacing
    #: the variant afterwards invalidates the approval.
    resume_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Empty by design for BOSS's unknown dynamic greeting mode. The hash binds
    #: that empty compatibility marker; neither value represents site text.
    answers_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    answers_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The only accepted BOSS mode says the first greeting is dynamic,
    #: unpreviewable and uncontrolled. It must never be labelled site-verified.
    answers_source: Mapped[str] = mapped_column(
        String(48), nullable=False, default="boss_dynamic_unverified"
    )

    # --- lifecycle ---------------------------------------------------------
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    #: Why a `pending` approval stopped being usable. Never a free-text excuse:
    #: one of the fixed reasons in `application_approval.py`.
    invalidated_reason: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str | None] = mapped_column(String(16))
    outcome_detail: Mapped[str | None] = mapped_column(String(256))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Set by the atomic one-attempt claim immediately before the browser
    #: action. Once set, this approval can never return to pending/retryable.
    attempt_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Set only when a *verified* submission recorded an application, so the
    #: "exactly one applied event" guarantee is checkable from this row.
    applied_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("application_events.id", ondelete="SET NULL")
    )

    job: Mapped["Job"] = relationship("Job")  # noqa: F821
    resume: Mapped["Resume"] = relationship("Resume")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ApplicationApproval id={self.id} job_id={self.job_id} "
            f"state={self.state!r} outcome={self.outcome!r}>"
        )
