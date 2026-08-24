"""Offer decision-support schemas (v1.0).

Weights, ratings, FX rates, deal-breakers and negotiation targets appear in
these payloads because the pages need them. None of it is ever logged.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Currency, DealBreakerKind, DealBreakerResult


# --------------------------------------------------------------------------
# profile
# --------------------------------------------------------------------------


class DealBreakerItem(BaseModel):
    kind: DealBreakerKind
    #: Shape depends on ``kind``: a number, a bool, a city, or an ISO date.
    value: Any = None


class DecisionProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    #: Raw, as entered. Normalization happens at calculation time.
    weights: dict[str, float] = Field(default_factory=dict)
    #: Same weights scaled to sum to 1 - what the score actually used.
    normalized_weights: dict[str, float] = Field(default_factory=dict)
    deal_breakers: list[DealBreakerItem] = Field(default_factory=list)
    #: User-entered only. v1.0 looks nothing up.
    fx_rates: dict[str, float] = Field(default_factory=dict)
    base_currency: Currency = Currency.CNY
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class DecisionProfileUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    #: 0 means "I do not care about this", which is different from omitting it.
    weights: dict[str, float] | None = None
    deal_breakers: list[DealBreakerItem] | None = None
    fx_rates: dict[str, float] | None = None
    base_currency: Currency | None = None
    notes: str | None = Field(default=None, max_length=2000)


# --------------------------------------------------------------------------
# assessment
# --------------------------------------------------------------------------


class AssessmentUpdate(BaseModel):
    """Your own 1-5 read of one offer, plus your negotiation targets."""

    #: {dimension: 1..5}. ``null`` clears a rating - "no view" is not a 1.
    ratings: dict[str, int | None] | None = None
    notes: str | None = Field(default=None, max_length=4000)
    target_total_cash: float | None = Field(default=None, ge=0)
    ideal_total_cash: float | None = Field(default=None, ge=0)
    minimum_total_cash: float | None = Field(default=None, ge=0)
    clear_targets: bool = False


class AssessmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    offer_id: int
    ratings: dict[str, int] = Field(default_factory=dict)
    notes: str | None = None
    target_total_cash: float | None = None
    ideal_total_cash: float | None = None
    minimum_total_cash: float | None = None
    updated_at: datetime | None = None


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------


class DimensionScoreOut(BaseModel):
    dimension: str
    label: str = ""
    #: 0-1, or null when there was nothing to score. Never 0 for "unknown".
    score: float | None = None
    weight: float = 0.0
    #: weight x score. Shown so the total is never a black box.
    contribution: float | None = None
    known: bool = False
    source: str = "missing"  # derived | rating | missing
    detail: str = ""


class DealBreakerCheckOut(BaseModel):
    kind: str
    label: str
    #: passed / failed / unknown - unknown means the fact was never recorded.
    result: DealBreakerResult
    detail: str = ""


class OfferScoreOut(BaseModel):
    offer_id: int
    company: str = ""
    title: str = ""
    total_score: float | None = None
    #: Share of total weight that had data behind it.
    coverage: float = 0.0
    dimension_scores: list[DimensionScoreOut] = Field(default_factory=list)
    weighted_contributions: dict[str, float] = Field(default_factory=dict)
    missing_dimensions: list[str] = Field(default_factory=list)
    deal_breakers: list[DealBreakerCheckOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    trade_offs: list[str] = Field(default_factory=list)
    #: Display only. Urgency never enters the score.
    days_to_deadline: int | None = None
    deadline_state: str = "none"
    compensation_comparable: bool = True
    currency: Currency = Currency.CNY
    #: Which revision the figures came from.
    revision_id: int | None = None
    fx_rate_used: float | None = None
    guaranteed_cash: float | None = None
    target_cash: float | None = None


class DecisionComparisonOut(BaseModel):
    """A scored comparison. Names a winner only when the data supports one."""

    offers: list[OfferScoreOut] = Field(default_factory=list)
    normalized_weights: dict[str, float] = Field(default_factory=dict)
    base_currency: Currency = Currency.CNY
    winner_offer_id: int | None = None
    winner_blocked_reason: str = ""
    min_coverage: float = 0.7
    mixed_currency: bool = False
    fx_rates_used: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    message: str = ""


# --------------------------------------------------------------------------
# snapshots
# --------------------------------------------------------------------------


class SnapshotCreateRequest(BaseModel):
    offer_ids: list[int] = Field(min_length=2, max_length=4)
    name: str = Field(default="", max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class SnapshotSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    offer_ids: list[int] = Field(default_factory=list)
    created_at: datetime
    notes: str | None = None
    winner_offer_id: int | None = None
    companies: list[str] = Field(default_factory=list)


class SnapshotDetail(SnapshotSummary):
    """The frozen document. Later edits never change it."""

    payload: dict[str, Any] = Field(default_factory=dict)


class SnapshotListResponse(BaseModel):
    items: list[SnapshotSummary] = Field(default_factory=list)
    total: int = 0


# --------------------------------------------------------------------------
# negotiation + competing offers
# --------------------------------------------------------------------------


class NegotiationPositionOut(BaseModel):
    """Where the company's current offer sits against your own targets."""

    currency: Currency = Currency.CNY
    current_company_cash: float | None = None
    latest_candidate_ask: float | None = None
    #: ask - current. Null when either side is missing.
    gap: float | None = None
    target_total_cash: float | None = None
    ideal_total_cash: float | None = None
    minimum_total_cash: float | None = None
    #: Below-minimum warns. It never declines anything.
    warnings: list[str] = Field(default_factory=list)


class CompetingOfferOut(BaseModel):
    offer_id: int
    company: str = ""
    title: str = ""
    status: str = ""
    decision_deadline: datetime | None = None
    days_to_deadline: int | None = None


class CompetingOffersResponse(BaseModel):
    """Shown before accepting. Never blocks, never auto-declines."""

    items: list[CompetingOfferOut] = Field(default_factory=list)
    total: int = 0
    message: str = ""
