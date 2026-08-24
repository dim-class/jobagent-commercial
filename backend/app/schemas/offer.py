"""Offer schemas (v0.9).

Compensation appears in offer and analytics payloads because the UI needs it.
It never appears in a log line, and unrelated endpoints do not return it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


# --------------------------------------------------------------------------
# compensation input, shared by every revision-creating request
# --------------------------------------------------------------------------


class CompensationFields(BaseModel):
    """Raw figures for one revision. Everything optional - missing stays missing.

    Amounts are in the parent offer's currency; nothing here is ever combined
    across currencies.
    """

    base_salary_annual: float | None = Field(default=None, ge=0)
    base_salary_monthly: float | None = Field(default=None, ge=0)
    months_per_year: int | None = Field(
        default=None, ge=1, le=24, description="如 14薪；留空表示未说明，不等于 12"
    )

    bonus_guaranteed: float | None = Field(default=None, ge=0)
    bonus_target: float | None = Field(
        default=None, ge=0, description="目标奖金不是保证到手的钱"
    )
    signing_bonus: float | None = Field(default=None, ge=0, description="一次性")

    stock_value: float | None = Field(default=None, ge=0)
    stock_type: EquityType | None = None
    stock_vesting_years: float | None = Field(default=None, gt=0, le=20)
    stock_vesting_text: str | None = Field(default=None, max_length=512)

    allowances_annual: float | None = Field(default=None, ge=0)
    overtime_pay_text: str | None = Field(default=None, max_length=512)
    housing_value: float | None = Field(
        default=None, ge=0, description="只有你明确估值后才计入现金"
    )
    transport_value: float | None = Field(default=None, ge=0)
    other_cash_annual: float | None = Field(default=None, ge=0)

    #: The offer's own wording. Never discarded, even when parsed.
    salary_text_original: str | None = Field(default=None, max_length=4000)


class RevisionCreateRequest(CompensationFields):
    """Append a revision. Source decides whether this is an offer or an ask."""

    revision_type: RevisionType = RevisionType.company_revision
    source: RevisionSource = RevisionSource.company
    effective_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=4000)

    # Candidate-side asks that are not money.
    requested_start_date: date | None = None
    requested_remote_policy: RemotePolicy | None = None
    other_request: str | None = Field(default=None, max_length=2000)

    #: Set when this revision exists to correct an earlier one's figures. The
    #: original row is never edited in place.
    corrects_revision_id: int | None = None

    @model_validator(mode="after")
    def _check_source(self) -> "RevisionCreateRequest":
        """Keep type and source consistent.

        A ``candidate_counter`` from the company, or a ``company_revision``
        from the candidate, would corrupt every "current company offer" read.
        """
        if (
            self.revision_type is RevisionType.candidate_counter
            and self.source is not RevisionSource.candidate
        ):
            raise ValueError("我方诉求的来源必须是 candidate。")
        if (
            self.source is RevisionSource.candidate
            and self.revision_type is not RevisionType.candidate_counter
        ):
            raise ValueError("candidate 来源的修订只能是「我方诉求」。")
        return self


class CounterRequest(CompensationFields):
    """我的谈薪诉求 - stored, never sent.

    JobAgent records what you intend to ask for. It does not message anyone.
    """

    requested_start_date: date | None = None
    requested_remote_policy: RemotePolicy | None = None
    other_request: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=4000)


class OfferCreateRequest(BaseModel):
    """Record an offer, with its initial terms."""

    applied_event_id: int | None = Field(
        default=None, description="留空则使用该岗位当前的投递周期"
    )
    interview_process_id: int | None = None
    confirmed: bool = Field(default=False, description="必须为 true —— 记录真实 Offer")

    currency: Currency = Currency.CNY
    received_at: datetime | None = None
    decision_deadline: datetime | None = None
    proposed_start_date: date | None = None
    employment_type: EmploymentType | None = None
    work_location: str | None = Field(default=None, max_length=256)
    remote_policy: RemotePolicy = RemotePolicy.unknown
    probation_text: str | None = Field(default=None, max_length=512)
    benefits: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = Field(default=None, max_length=4000)

    #: The initial terms. Optional so an offer can be recorded before the
    #: numbers are known.
    initial: CompensationFields | None = None


class OfferUpdateRequest(BaseModel):
    """Correct offer-level metadata. Never touches negotiation history."""

    decision_deadline: datetime | None = None
    clear_decision_deadline: bool = False
    proposed_start_date: date | None = None
    employment_type: EmploymentType | None = None
    work_location: str | None = Field(default=None, max_length=256)
    remote_policy: RemotePolicy | None = None
    probation_text: str | None = Field(default=None, max_length=512)
    benefits: dict[str, Any] | None = None
    notes: str | None = Field(default=None, max_length=4000)


class AcceptOfferRequest(BaseModel):
    """Accepting freezes which revision was accepted."""

    confirmed: bool = Field(default=False, description="必须为 true")
    #: Defaults to the current *company* offer - never a candidate counter.
    revision_id: int | None = None
    decided_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=4000)


class DeclineOfferRequest(BaseModel):
    """Declining an offer is the candidate's decision, never a rejection."""

    confirmed: bool = Field(default=False, description="必须为 true")
    reason: DeclineReason = DeclineReason.other
    revision_id: int | None = None
    decided_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=4000)


class CloseOfferRequest(BaseModel):
    """Mark an offer withdrawn by the company, or expired."""

    confirmed: bool = Field(default=False, description="必须为 true")
    notes: str | None = Field(default=None, max_length=4000)


class ParseOfferTextRequest(BaseModel):
    """Read a pasted offer. Deterministic, and saves nothing."""

    text: str = Field(min_length=1, max_length=8000)


# --------------------------------------------------------------------------
# responses
# --------------------------------------------------------------------------


class CompensationSummary(BaseModel):
    """Everything derived from one revision, uncertainty kept visible."""

    currency: Currency = Currency.CNY
    base_annual: float | None = None
    first_year_guaranteed_cash: float | None = None
    first_year_target_cash: float | None = None
    steady_state_guaranteed_cash: float | None = None
    steady_state_target_cash: float | None = None
    equity_annualized: float | None = None
    estimated_first_year_total_comp: float | None = None
    #: True when a grant exists but could not be valued. The UI shows
    #: 「未计入可比较总包」 rather than a number.
    equity_excluded: bool = False
    notes: list[str] = Field(default_factory=list)


class RevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    offer_id: int
    revision_index: int
    revision_type: RevisionType
    revision_type_label: str = ""
    source: RevisionSource
    #: False for candidate counters - what you asked for is not an offer.
    is_company_offer: bool = True

    base_salary_annual: float | None = None
    base_salary_monthly: float | None = None
    months_per_year: int | None = None
    bonus_guaranteed: float | None = None
    bonus_target: float | None = None
    signing_bonus: float | None = None
    stock_value: float | None = None
    stock_type: EquityType | None = None
    stock_vesting_years: float | None = None
    stock_vesting_text: str | None = None
    allowances_annual: float | None = None
    overtime_pay_text: str | None = None
    housing_value: float | None = None
    transport_value: float | None = None
    other_cash_annual: float | None = None
    salary_text_original: str | None = None
    currency: Currency = Currency.CNY

    requested_start_date: date | None = None
    requested_remote_policy: RemotePolicy | None = None
    other_request: str | None = None

    effective_at: datetime | None = None
    notes: str | None = None
    corrects_revision_id: int | None = None
    created_at: datetime

    summary: CompensationSummary = Field(default_factory=CompensationSummary)


class UpliftOut(BaseModel):
    """Change between two *company* figures. Null when either is missing."""

    absolute: float | None = None
    percentage: float | None = None
    initial: float | None = None
    final: float | None = None


class NegotiationSummary(BaseModel):
    """What moved between the first and the current company offer."""

    rounds: int = 0
    candidate_counters: int = 0
    company_revisions: int = 0
    base: UpliftOut = Field(default_factory=UpliftOut)
    first_year_guaranteed: UpliftOut = Field(default_factory=UpliftOut)
    first_year_target: UpliftOut = Field(default_factory=UpliftOut)
    signing_bonus_gained: float | None = None
    remote_changed: bool = False
    start_date_changed: bool = False
    #: Only true when there is a recorded counter *and* a later company
    #: revision. Without both, a change is not evidence of negotiation.
    has_negotiation_sequence: bool = False


class OfferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    applied_event_id: int
    interview_process_id: int | None = None
    status: OfferStatus
    status_label: str = ""
    currency: Currency

    received_at: datetime | None = None
    decision_deadline: datetime | None = None
    proposed_start_date: date | None = None
    employment_type: EmploymentType | None = None
    work_location: str | None = None
    remote_policy: RemotePolicy
    probation_text: str | None = None
    benefits: dict[str, Any] = Field(default_factory=dict)

    accepted_revision_id: int | None = None
    declined_revision_id: int | None = None
    decline_reason: DeclineReason | None = None
    decline_reason_label: str | None = None
    decided_at: datetime | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    revisions: list[RevisionOut] = Field(default_factory=list)

    # --- context, resolved from the application cycle ------------------
    company: str = ""
    title: str = ""
    city: str | None = None
    applied_at: datetime | None = None
    #: The resume used for THIS cycle - frozen at application time (v0.7).
    resume_id: int | None = None
    resume_label: str | None = None
    resume_archived: bool = False

    # --- derived, deterministic ----------------------------------------
    #: The company's current offer. NOT the latest revision - a candidate
    #: counter is an ask, not an offer.
    current_company_revision_id: int | None = None
    current_company_summary: CompensationSummary | None = None
    #: The most recent thing you asked for, if any.
    latest_counter_revision_id: int | None = None
    latest_counter_summary: CompensationSummary | None = None
    #: What was actually accepted, frozen. Never recomputed from "latest".
    accepted_summary: CompensationSummary | None = None
    negotiation: NegotiationSummary = Field(default_factory=NegotiationSummary)

    #: Days until the deadline in the reporting timezone. Negative = past.
    days_to_deadline: int | None = None
    deadline_state: str = "none"  # none | today | tomorrow | soon | later | past


class OfferListResponse(BaseModel):
    items: list[OfferOut] = Field(default_factory=list)
    total: int = 0


class OfferBoardResponse(BaseModel):
    """The Offer page, bucketed by what needs a decision."""

    timezone: str = "Asia/Tokyo"
    generated_at: datetime
    pending: list[OfferOut] = Field(default_factory=list)
    negotiating: list[OfferOut] = Field(default_factory=list)
    accepted: list[OfferOut] = Field(default_factory=list)
    closed: list[OfferOut] = Field(default_factory=list)
    #: Pre-v0.9 ``offer`` events with no Offer row. Never auto-converted.
    legacy_offer_events: list["LegacyOfferMilestone"] = Field(default_factory=list)


class LegacyOfferMilestone(BaseModel):
    """A pre-v0.9 offer event. Compensation detail is unknown, not guessed."""

    job_id: int
    event_id: int
    company: str = ""
    title: str = ""
    occurred_at: datetime
    note: str | None = None
    #: Whatever v0.4 stored, e.g. "35k". Not parsed into a structured offer.
    legacy_salary_text: str | None = None


class OfferActionResponse(BaseModel):
    offer: OfferOut
    message: str = ""
    job_status: str | None = None


class ParsedSalaryOut(BaseModel):
    """What the parser could establish. Always needs human confirmation."""

    base_salary_annual: float | None = None
    base_salary_monthly: float | None = None
    months_per_year: int | None = None
    currency: Currency | None = None
    confidence: str = "none"
    matched_text: str = ""
    notes: list[str] = Field(default_factory=list)
    message: str = ""


class OfferComparisonRow(BaseModel):
    """One offer in the comparison table, in its own currency."""

    offer_id: int
    job_id: int
    company: str = ""
    title: str = ""
    city: str | None = None
    status: OfferStatus
    currency: Currency

    base_annual: float | None = None
    first_year_guaranteed_cash: float | None = None
    first_year_target_cash: float | None = None
    estimated_first_year_total_comp: float | None = None
    equity_excluded: bool = False

    bonus_target: float | None = None
    signing_bonus: float | None = None
    stock_value: float | None = None
    stock_type: EquityType | None = None

    remote_policy: RemotePolicy
    work_location: str | None = None
    proposed_start_date: date | None = None
    decision_deadline: datetime | None = None
    deadline_state: str = "none"
    benefits: dict[str, Any] = Field(default_factory=dict)
    resume_label: str | None = None


class OfferComparisonResponse(BaseModel):
    """Side-by-side comparison. Deliberately names no overall winner.

    Non-cash factors are shown as themselves rather than folded into a score,
    and offers in different currencies are never ranked against each other.
    """

    rows: list[OfferComparisonRow] = Field(default_factory=list)
    currencies: list[Currency] = Field(default_factory=list)
    #: True when the selection spans more than one currency.
    mixed_currency: bool = False
    message: str = ""
    notes: list[str] = Field(default_factory=list)


OfferBoardResponse.model_rebuild()
