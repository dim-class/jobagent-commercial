"""Offer decision-support models (v1.0).

Three rows, three purposes:

``DecisionProfile``
    What *you* care about - weights, hard constraints, and any exchange rates
    you chose to supply. Entirely user-entered. Nothing here is ever suggested
    or filled in by a model.

``OfferAssessment``
    Your subjective read of one offer: 1-5 on things no dataset can answer
    (growth, stability, work-life balance, brand), plus your own negotiation
    targets. ``None`` means "not rated", which is never treated as zero.

``DecisionSnapshot``
    A frozen comparison. The whole computation - offers, revision ids, weights,
    ratings, FX rates, deal-breakers and results - is stored as one JSON
    document, so changing your weights tomorrow cannot rewrite what you decided
    on today.

Compensation figures, ratings, FX rates, notes and negotiation targets are all
sensitive. Nothing in this module is ever written to a log.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import Currency


class DecisionProfile(Base, TimestampMixin):
    """One named set of preferences. Usually there is exactly one."""

    __tablename__ = "decision_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="默认偏好")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True
    )

    #: {dimension: weight}. Raw as the user typed them - normalization happens
    #: at calculation time so the stored numbers stay recognisable.
    weights_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    #: [{"kind": ..., "value": ...}]. Hard constraints, evaluated to
    #: pass/fail/unknown - never used to reject an offer automatically.
    deal_breakers_json: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    #: {"JPY": 0.05, ...} - units of ``base_currency`` per unit of the key.
    #: Entirely user-supplied; v1.0 looks nothing up.
    fx_rates_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    base_currency: Mapped[Currency] = mapped_column(
        SAEnum(Currency, native_enum=False, length=8),
        nullable=False,
        default=Currency.CNY,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - never log weights
        return f"<DecisionProfile id={self.id} name={self.name!r} active={self.is_active}>"


class OfferAssessment(Base, TimestampMixin):
    """Your subjective read of one offer, plus your negotiation targets."""

    __tablename__ = "offer_assessments"

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: {dimension: 1..5}. A dimension absent from this dict is *unrated*, which
    #: reduces coverage rather than scoring zero.
    ratings_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- negotiation targets, in the offer's own currency ---------------
    #: What you would be happy with.
    target_total_cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: What you would be delighted with.
    ideal_total_cash: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Below this you would rather walk. A warning only - never an auto-decline.
    minimum_total_cash: Mapped[float | None] = mapped_column(Float, nullable=True)

    offer: Mapped["Offer"] = relationship(back_populates="assessment")  # noqa: F821

    __table_args__ = (
        # One assessment per offer; re-rating updates it in place.
        UniqueConstraint("offer_id", name="uq_offer_assessments_offer"),
    )

    def __repr__(self) -> str:  # pragma: no cover - never log ratings
        return f"<OfferAssessment id={self.id} offer={self.offer_id}>"


class DecisionSnapshot(Base):
    """A frozen comparison.

    ``payload_json`` holds the entire computation, so a later change to weights,
    ratings, FX rates or the offers themselves cannot alter what this snapshot
    says. There is deliberately no update path.
    """

    __tablename__ = "decision_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: Offer ids compared, denormalized for cheap listing and filtering.
    offer_ids_json: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    #: The complete frozen result. Never rewritten.
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: No ``updated_at``: a snapshot is written once and never revised, so a
    #: modification timestamp would only ever be a lie.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_decision_snapshots_created", "created_at"),)

    def __repr__(self) -> str:  # pragma: no cover - never log the payload
        return f"<DecisionSnapshot id={self.id} name={self.name!r}>"
