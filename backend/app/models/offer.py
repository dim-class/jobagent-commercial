"""Offer and negotiation models (v0.9).

``ApplicationEvent`` stays the append-only milestone trail. An offer's
compensation changes during negotiation, its deadline moves, and one offer can
carry several revisions - none of which belongs in an audit log, so it lives
here.

Two rules the rest of the codebase depends on:

**An offer belongs to one application cycle**, named by ``applied_event_id``,
exactly as an ``InterviewProcess`` does. Which resume gets credit for an offer
follows from that cycle's ``applied`` event (v0.7), never from ``Job.status``
and never from whichever resume is active today.

**Negotiation history is append-only.** An ``OfferRevision`` is never edited to
reflect a new round; a new revision is added. Crucially a *candidate counter*
is what you asked for, not what the company is offering - see
``services/offer_management.latest_company_revision``.

``accepted_revision_id`` freezes what was actually accepted. Later revisions or
corrections must never move the compensation a past decision was made on.

Compensation is sensitive: nothing here is ever written to a log.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
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
    Currency,
    DeclineReason,
    EmploymentType,
    EquityType,
    OfferStatus,
    RemotePolicy,
    RevisionSource,
    RevisionType,
)


class Offer(Base, TimestampMixin):
    """One offer, belonging to one application cycle."""

    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: THE attribution link.
    #:
    #: CASCADE, not RESTRICT: the ``applied`` event and this offer are both
    #: children of the same job, so RESTRICT did not protect the attribution -
    #: it simply made the job undeletable. The event trail is append-only, so
    #: the only way this row disappears is with the job it belongs to.
    applied_event_id: Mapped[int] = mapped_column(
        ForeignKey("application_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Optional on purpose: not every offer follows a recorded interview
    #: process. Direct recruiter approaches skip interviews entirely.
    interview_process_id: Mapped[int | None] = mapped_column(
        ForeignKey("interview_processes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status: Mapped[OfferStatus] = mapped_column(
        SAEnum(OfferStatus, native_enum=False, length=16),
        nullable=False,
        default=OfferStatus.received,
        index=True,
    )
    #: Every figure on this offer is in this currency. Amounts are never
    #: combined or ranked across currencies - v0.9 fetches no FX rates.
    currency: Mapped[Currency] = mapped_column(
        SAEnum(Currency, native_enum=False, length=8),
        nullable=False,
        default=Currency.CNY,
        index=True,
    )

    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    proposed_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    employment_type: Mapped[EmploymentType | None] = mapped_column(
        SAEnum(EmploymentType, native_enum=False, length=16), nullable=True
    )
    work_location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    remote_policy: Mapped[RemotePolicy] = mapped_column(
        SAEnum(RemotePolicy, native_enum=False, length=16),
        nullable=False,
        default=RemotePolicy.unknown,
    )
    probation_text: Mapped[str | None] = mapped_column(String(512), nullable=True)

    #: Structured non-cash factors, never assigned a monetary value.
    #: {"paid_leave": "15天", "visa_support": true, ...}
    benefits_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )

    # --- decision snapshots -------------------------------------------
    #: Exactly which revision was accepted. Frozen: later revisions and
    #: corrections must never move what a past decision was made on.
    #:
    #: SET NULL rather than RESTRICT: ``offers`` and ``offer_revisions``
    #: reference each other, and RESTRICT here gave the two tables no valid
    #: delete order at all - it made deleting a job that carried an accepted
    #: offer fail outright. Nothing in the app deletes a revision on its own
    #: (there is no such endpoint, and history is append-only), so the freeze
    #: is preserved by design rather than by a constraint that deadlocks.
    accepted_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("offer_revisions.id", ondelete="SET NULL"), nullable=True
    )
    #: The company offer that stood at the moment of declining.
    declined_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("offer_revisions.id", ondelete="SET NULL"), nullable=True
    )
    decline_reason: Mapped[DeclineReason | None] = mapped_column(
        SAEnum(DeclineReason, native_enum=False, length=24), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped["Job"] = relationship(back_populates="offers")  # noqa: F821
    #: Your subjective read of this offer (v1.0). Optional - an unrated offer
    #: simply has lower decision coverage.
    assessment: Mapped["OfferAssessment | None"] = relationship(  # noqa: F821
        back_populates="offer", cascade="all, delete-orphan", uselist=False
    )
    applied_event: Mapped["ApplicationEvent"] = relationship()  # noqa: F821
    revisions: Mapped[list["OfferRevision"]] = relationship(
        back_populates="offer",
        cascade="all, delete-orphan",
        order_by="OfferRevision.revision_index.asc()",
        foreign_keys="OfferRevision.offer_id",
    )

    __table_args__ = (
        # One offer per application cycle. A second would double-count the same
        # opportunity in every funnel.
        UniqueConstraint("applied_event_id", name="uq_offers_applied_event"),
        Index("ix_offers_job_status", "job_id", "status"),
    )

    @property
    def is_closed(self) -> bool:
        from app.models.enums import CLOSED_OFFER_STATUSES

        return self.status in CLOSED_OFFER_STATUSES

    def __repr__(self) -> str:  # pragma: no cover - never log compensation
        return (
            f"<Offer id={self.id} job={self.job_id} "
            f"cycle={self.applied_event_id} status={self.status}>"
        )


class OfferRevision(Base):
    """One state of an offer: what was offered, or what was asked for.

    Never updated to reflect a later round - a new revision is appended, so the
    negotiation history stays readable end to end.
    """

    __tablename__ = "offer_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    revision_type: Mapped[RevisionType] = mapped_column(
        SAEnum(RevisionType, native_enum=False, length=24),
        nullable=False,
        default=RevisionType.initial,
        index=True,
    )
    #: Who this came from. A ``candidate`` revision is a request, not an offer.
    source: Mapped[RevisionSource] = mapped_column(
        SAEnum(RevisionSource, native_enum=False, length=16),
        nullable=False,
        default=RevisionSource.company,
        index=True,
    )

    # --- compensation, all in the parent offer's currency ---------------
    base_salary_annual: Mapped[float | None] = mapped_column(Float, nullable=True)
    base_salary_monthly: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: 14薪 / 16薪. NULL means "not stated", not "12".
    months_per_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    bonus_guaranteed: Mapped[float | None] = mapped_column(Float, nullable=True)
    bonus_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    signing_bonus: Mapped[float | None] = mapped_column(Float, nullable=True)

    stock_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    stock_type: Mapped[EquityType | None] = mapped_column(
        SAEnum(EquityType, native_enum=False, length=24), nullable=True
    )
    stock_vesting_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    stock_vesting_text: Mapped[str | None] = mapped_column(String(512), nullable=True)

    allowances_annual: Mapped[float | None] = mapped_column(Float, nullable=True)
    overtime_pay_text: Mapped[str | None] = mapped_column(String(512), nullable=True)
    housing_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    transport_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    other_cash_annual: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: The offer's own wording, kept verbatim. Parsed numbers can be wrong or
    #: overridden; the original phrasing is the only unambiguous record.
    salary_text_original: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Denormalized from the parent so a revision is self-describing, and so a
    #: currency change can never silently reinterpret stored history.
    currency: Mapped[Currency] = mapped_column(
        SAEnum(Currency, native_enum=False, length=8),
        nullable=False,
        default=Currency.CNY,
    )

    #: Candidate-side asks that are not money.
    requested_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    requested_remote_policy: Mapped[RemotePolicy | None] = mapped_column(
        SAEnum(RemotePolicy, native_enum=False, length=16), nullable=True
    )
    other_request: Mapped[str | None] = mapped_column(Text, nullable=True)

    effective_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Set on a revision appended to correct an earlier one's figures. The
    #: original row stays exactly as entered.
    corrects_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("offer_revisions.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    offer: Mapped["Offer"] = relationship(
        back_populates="revisions", foreign_keys=[offer_id]
    )

    __table_args__ = (
        Index("ix_offer_revisions_offer_index", "offer_id", "revision_index"),
        Index("ix_offer_revisions_offer_source", "offer_id", "source"),
    )

    @property
    def is_company_offer(self) -> bool:
        """Whether this states what the company is offering.

        A candidate counter is excluded: what you asked for is not an offer.
        """
        from app.models.enums import COMPANY_REVISION_SOURCES

        return self.source in COMPANY_REVISION_SOURCES

    def __repr__(self) -> str:  # pragma: no cover - never log compensation
        return (
            f"<OfferRevision id={self.id} offer={self.offer_id} "
            f"#{self.revision_index} {self.revision_type}/{self.source}>"
        )


#: Chinese labels, defined once so API, analytics and UI agree.
REVISION_TYPE_LABEL: dict[RevisionType, str] = {
    RevisionType.initial: "初始 Offer",
    RevisionType.company_revision: "公司调整",
    RevisionType.candidate_counter: "我方诉求",
    RevisionType.final: "最终 Offer",
    RevisionType.other: "其他",
}

OFFER_STATUS_LABEL: dict[OfferStatus, str] = {
    OfferStatus.draft: "草稿",
    OfferStatus.received: "待决定",
    OfferStatus.negotiating: "谈判中",
    OfferStatus.accepted: "已接受",
    OfferStatus.declined: "已拒绝",
    OfferStatus.withdrawn: "对方撤回",
    OfferStatus.expired: "已过期",
}

DECLINE_REASON_LABEL: dict[DeclineReason, str] = {
    DeclineReason.salary: "薪资",
    DeclineReason.role_content: "岗位内容",
    DeclineReason.location: "地点",
    DeclineReason.remote_policy: "远程政策",
    DeclineReason.company: "公司",
    DeclineReason.growth: "发展空间",
    DeclineReason.accepted_other_offer: "接受其他Offer",
    DeclineReason.visa: "签证",
    DeclineReason.start_date: "入职时间",
    DeclineReason.personal: "个人原因",
    DeclineReason.other: "其他",
}
