"""Career-strategy audit and recommendation decisions (v0.6).

Two small tables that exist so the loop stays honest:

* ``CareerStrategyChange`` - what the strategy was before and after a change,
  and whether a human made it directly or accepted an analytics proposal. The
  analysis cache already keys on ``strategy_hash``, so this records the *human
  decision* around that hash, not the hash itself.
* ``StrategyRecommendationDecision`` - which proposals the user already
  answered, so a dismissed suggestion stops nagging. Keyed by a semantic
  signature, so genuinely new evidence produces a new proposal.

Neither table ever changes the strategy on its own.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import RecommendationDecision, StrategyChangeSource


class CareerStrategyChange(Base):
    """One recorded edit to the per-user ``data/career_strategy.yaml``."""

    __tablename__ = "career_strategy_changes"

    id: Mapped[int] = mapped_column(primary_key=True)

    before_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    after_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: Full snapshots. The YAML is small and this is the only place the "what
    #: did my strategy look like when those outcomes happened?" question can be
    #: answered later.
    before_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    after_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    source: Mapped[StrategyChangeSource] = mapped_column(
        SAEnum(StrategyChangeSource, native_enum=False, length=32),
        nullable=False,
        default=StrategyChangeSource.manual,
    )
    recommendation_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CareerStrategyChange id={self.id} source={self.source}>"


class StrategyRecommendationDecision(Base):
    """The human's answer to one proposal, keyed by its semantic signature."""

    __tablename__ = "strategy_recommendation_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    signature: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    decision: Mapped[RecommendationDecision] = mapped_column(
        SAEnum(RecommendationDecision, native_enum=False, length=16), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("signature", name="uq_strategy_recommendation_signature"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StrategyRecommendationDecision {self.signature} {self.decision}>"
