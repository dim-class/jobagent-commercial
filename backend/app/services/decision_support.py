"""Decision-support persistence and orchestration (v1.0).

The only place ``DecisionProfile`` / ``OfferAssessment`` / ``DecisionSnapshot``
change, and the bridge between stored offers and the pure scoring engine in
``offer_decision``.

Two responsibilities worth stating:

**Resolving the right revision.** Compensation always comes from the latest
*company-origin* revision, or from the frozen ``accepted_revision_id`` on an
accepted offer. What the candidate asked for never feeds a score.

**Converting only when told to.** An offer in a currency the user supplied no
rate for is marked non-comparable on compensation rather than converted at some
assumed rate.

Compensation figures, ratings, FX rates, notes, deal-breakers and negotiation
targets are sensitive. Nothing here is written to a log.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.models import (
    DealBreakerKind,
    DecisionDimension,
    DecisionProfile,
    DecisionSnapshot,
    Offer,
    OfferAssessment,
    OfferStatus,
)
from app.services import offer_management
from app.services.offer_decision import (
    ComparisonResult,
    DealBreaker,
    OfferFacts,
    compare_offers,
)

logger = get_logger(__name__)

#: A brand-new profile weighs nothing until the user says otherwise. An
#: opinionated default would silently become "the app's opinion" of what
#: matters in a job.
DEFAULT_WEIGHTS: dict[str, float] = {}

#: Benefit keys that mean "visa support is provided".
_VISA_KEYS = ("visa_support", "visa", "visa_sponsorship")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# profile
# --------------------------------------------------------------------------


def get_active_profile(db: Session, *, create: bool = True) -> DecisionProfile | None:
    """The user's preferences. Created empty on first use."""
    profile = db.scalar(
        select(DecisionProfile)
        .where(DecisionProfile.is_active.is_(True))
        .order_by(DecisionProfile.id.asc())
        .limit(1)
    )
    if profile is not None or not create:
        return profile

    profile = DecisionProfile(
        name="默认偏好",
        is_active=True,
        weights_json=dict(DEFAULT_WEIGHTS),
        deal_breakers_json=[],
        fx_rates_json={},
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    log_event(logger, "decision.profile_created", profile_id=profile.id)
    return profile


def update_profile(
    db: Session,
    *,
    name: str | None = None,
    weights: dict[str, Any] | None = None,
    deal_breakers: list[dict[str, Any]] | None = None,
    fx_rates: dict[str, Any] | None = None,
    base_currency: Any = None,
    notes: str | None = None,
) -> DecisionProfile:
    """Store preferences exactly as entered.

    Weights are kept raw - normalization happens at calculation time, so the
    numbers the user typed stay recognisable when they come back to them.
    """
    profile = get_active_profile(db)

    if name is not None:
        profile.name = name.strip() or profile.name
    if weights is not None:
        profile.weights_json = _clean_weights(weights)
    if deal_breakers is not None:
        profile.deal_breakers_json = _clean_deal_breakers(deal_breakers)
    if fx_rates is not None:
        profile.fx_rates_json = _clean_fx(fx_rates)
    if base_currency is not None:
        profile.base_currency = base_currency
    if notes is not None:
        profile.notes = notes.strip() or None

    db.commit()
    db.refresh(profile)
    # Ids and counts only - never the weights or rates themselves.
    log_event(
        logger,
        "decision.profile_updated",
        profile_id=profile.id,
        weighted_dimensions=len(profile.weights_json or {}),
        deal_breakers=len(profile.deal_breakers_json or []),
    )
    return profile


def _clean_weights(raw: dict[str, Any]) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for key, value in (raw or {}).items():
        try:
            DecisionDimension(key)
        except ValueError:
            raise ValidationError(
                f"未知的评估维度：{key}", detail={"dimension": key}
            ) from None
        try:
            weight = float(value)
        except (TypeError, ValueError):
            raise ValidationError(
                f"「{key}」的权重必须是数字。", detail={"dimension": key}
            ) from None
        if weight < 0:
            raise ValidationError(
                "权重不能为负数。如果不在意某个维度，把它设为 0 即可。",
                detail={"dimension": key},
            )
        cleaned[key] = weight
    return cleaned


def _clean_deal_breakers(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in raw or []:
        kind = str((item or {}).get("kind") or "")
        try:
            DealBreakerKind(kind)
        except ValueError:
            raise ValidationError(
                f"未知的硬性条件：{kind}", detail={"kind": kind}
            ) from None
        cleaned.append({"kind": kind, "value": (item or {}).get("value")})
    return cleaned


def _clean_fx(raw: dict[str, Any]) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for code, value in (raw or {}).items():
        try:
            rate = float(value)
        except (TypeError, ValueError):
            raise ValidationError(
                f"「{code}」的汇率必须是数字。", detail={"currency": code}
            ) from None
        if rate <= 0:
            raise ValidationError(
                "汇率必须大于 0。", detail={"currency": code}
            )
        cleaned[str(code)] = rate
    return cleaned


def deal_breakers_of(profile: DecisionProfile | None) -> list[DealBreaker]:
    if profile is None:
        return []
    out: list[DealBreaker] = []
    for item in profile.deal_breakers_json or []:
        try:
            kind = DealBreakerKind(str(item.get("kind")))
        except (ValueError, AttributeError):
            continue
        out.append(DealBreaker(kind=kind, value=item.get("value")))
    return out


# --------------------------------------------------------------------------
# assessments
# --------------------------------------------------------------------------


def get_assessment(db: Session, offer_id: int) -> OfferAssessment | None:
    return db.scalar(select(OfferAssessment).where(OfferAssessment.offer_id == offer_id))


def upsert_assessment(
    db: Session,
    offer_id: int,
    *,
    ratings: dict[str, Any] | None = None,
    notes: str | None = None,
    target_total_cash: float | None = None,
    ideal_total_cash: float | None = None,
    minimum_total_cash: float | None = None,
    clear_targets: bool = False,
) -> OfferAssessment:
    """Record your own read of an offer.

    A rating of ``None`` removes it - "I no longer have a view" is different
    from "I rate it 1", and the engine treats them differently too.
    """
    offer = offer_management.get_offer(db, offer_id)
    row = get_assessment(db, offer.id)
    if row is None:
        row = OfferAssessment(offer_id=offer.id, ratings_json={})
        db.add(row)

    if ratings is not None:
        row.ratings_json = _clean_ratings(ratings)
    if notes is not None:
        row.notes = notes.strip() or None

    if clear_targets:
        row.target_total_cash = None
        row.ideal_total_cash = None
        row.minimum_total_cash = None
    else:
        if target_total_cash is not None:
            row.target_total_cash = target_total_cash
        if ideal_total_cash is not None:
            row.ideal_total_cash = ideal_total_cash
        if minimum_total_cash is not None:
            row.minimum_total_cash = minimum_total_cash

    db.commit()
    db.refresh(row)
    # Count only - never the ratings or the targets.
    log_event(
        logger,
        "decision.assessment_saved",
        offer_id=offer.id,
        rated_dimensions=len(row.ratings_json or {}),
    )
    return row


def _clean_ratings(raw: dict[str, Any]) -> dict[str, int]:
    cleaned: dict[str, int] = {}
    for key, value in (raw or {}).items():
        try:
            DecisionDimension(key)
        except ValueError:
            raise ValidationError(
                f"未知的评估维度：{key}", detail={"dimension": key}
            ) from None
        if value is None:
            continue  # "不确定" - stored as absent, which is not a zero
        try:
            rating = int(value)
        except (TypeError, ValueError):
            raise ValidationError(
                f"「{key}」的评分必须是 1-5 的整数，或留空表示不确定。",
                detail={"dimension": key},
            ) from None
        if not (1 <= rating <= 5):
            raise ValidationError(
                f"「{key}」的评分必须在 1-5 之间。", detail={"dimension": key}
            )
        cleaned[key] = rating
    return cleaned


# --------------------------------------------------------------------------
# building facts
# --------------------------------------------------------------------------


def _visa_support_of(offer: Offer) -> bool | None:
    """Read visa support out of the offer's benefits. ``None`` when unrecorded."""
    benefits = offer.benefits_json or {}
    for key in _VISA_KEYS:
        if key in benefits:
            value = benefits[key]
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                text = value.strip().lower()
                if not text:
                    return None
                return text not in {"no", "false", "无", "不提供", "0"}
            return bool(value)
    return None


def build_facts(
    db: Session,
    offer: Offer,
    *,
    base_currency: str,
    fx_rates: dict[str, float],
    deadline_state: str = "none",
    days_to_deadline: int | None = None,
) -> OfferFacts:
    """Resolve one offer into the flat shape the scoring engine consumes.

    Uses the accepted revision when there is one, otherwise the latest
    company-origin revision - never a candidate counter.
    """
    revision = offer_management.accepted_revision(
        db, offer
    ) or offer_management.latest_company_revision(offer)
    breakdown = offer_management.breakdown_of(revision)
    assessment = get_assessment(db, offer.id)

    currency = offer.currency.value
    rate = _rate_for(currency, base_currency, fx_rates)

    guaranteed = breakdown.first_year_guaranteed_cash if breakdown else None
    target = breakdown.first_year_target_cash if breakdown else None

    return OfferFacts(
        offer_id=offer.id,
        company=offer.job.company if offer.job else "",
        title=offer.job.title if offer.job else "",
        currency=currency,
        revision_id=revision.id if revision else None,
        guaranteed_cash=guaranteed,
        target_cash=target,
        equity_annualized=breakdown.equity_annualized if breakdown else None,
        equity_excluded=bool(breakdown and breakdown.equity_excluded),
        comparable_guaranteed_cash=(
            None if (guaranteed is None or rate is None) else guaranteed * rate
        ),
        comparable_target_cash=(
            None if (target is None or rate is None) else target * rate
        ),
        fx_rate_used=rate if currency != base_currency else None,
        remote_policy=offer.remote_policy.value,
        work_location=offer.work_location,
        proposed_start_date=offer.proposed_start_date,
        visa_support=_visa_support_of(offer),
        ratings=dict(assessment.ratings_json or {}) if assessment else {},
        days_to_deadline=days_to_deadline,
        deadline_state=deadline_state,
    )


def _rate_for(
    currency: str, base_currency: str, fx_rates: dict[str, float]
) -> float | None:
    """Units of ``base_currency`` per unit of ``currency``.

    Returns ``None`` when the user supplied no rate - which makes the offer
    non-comparable on compensation rather than converted at a guess.
    """
    if currency == base_currency:
        return 1.0
    rate = (fx_rates or {}).get(currency)
    try:
        value = float(rate)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------


def compare(
    db: Session,
    offer_ids: list[int],
    *,
    settings: Settings | None = None,
    deadline_states: dict[int, tuple[str, int | None]] | None = None,
) -> tuple[ComparisonResult, DecisionProfile, dict[int, OfferFacts]]:
    """Score a set of offers against the active profile.

    Returns the resolved facts alongside the result: the snapshot needs to
    record *which* revision and rate each figure came from, and re-deriving
    them afterwards would risk recording something the score never saw.
    """
    cfg = settings or get_settings()
    unique = list(dict.fromkeys(offer_ids))
    if len(unique) < 2:
        raise ValidationError(
            "请至少选择两个 Offer 进行比较。", detail={"field": "offer_id"}
        )

    profile = get_active_profile(db)
    base_currency = profile.base_currency.value
    fx_rates = dict(profile.fx_rates_json or {})
    states = deadline_states or {}

    facts: list[OfferFacts] = []
    for offer_id in unique:
        offer = offer_management.get_offer(db, offer_id)
        state, days = states.get(offer_id, ("none", None))
        facts.append(
            build_facts(
                db,
                offer,
                base_currency=base_currency,
                fx_rates=fx_rates,
                deadline_state=state,
                days_to_deadline=days,
            )
        )

    result = compare_offers(
        facts,
        raw_weights=profile.weights_json,
        deal_breakers=deal_breakers_of(profile),
        base_currency=base_currency,
        fx_rates=fx_rates,
        min_coverage=cfg.offer_decision_min_coverage,
    )
    return result, profile, {f.offer_id: f for f in facts}


# --------------------------------------------------------------------------
# snapshots
# --------------------------------------------------------------------------


def save_snapshot(
    db: Session,
    *,
    name: str,
    result: ComparisonResult,
    profile: DecisionProfile,
    facts_by_offer: dict[int, OfferFacts],
    notes: str | None = None,
) -> DecisionSnapshot:
    """Freeze a comparison.

    The whole computation is stored as one document - offers, the exact
    revision each figure came from, the weights, the ratings, the FX rates, the
    deal-breakers and the results. Editing any of those tomorrow leaves this
    snapshot untouched, which is the entire point: a decision should still be
    explicable in terms of what you knew when you made it.
    """
    payload = {
        "version": 1,
        "base_currency": result.base_currency,
        "min_coverage": result.min_coverage,
        "mixed_currency": result.mixed_currency,
        "fx_rates_used": dict(result.fx_rates_used),
        "normalized_weights": dict(result.weights),
        "raw_weights": dict(profile.weights_json or {}),
        "deal_breakers": list(profile.deal_breakers_json or []),
        "winner_offer_id": result.winner_offer_id,
        "winner_blocked_reason": result.winner_blocked_reason,
        "notes": list(result.notes),
        "offers": [
            {
                **asdict(score),
                # A property, so ``asdict`` does not pick it up - and the whole
                # point of the snapshot is that the arithmetic stays checkable.
                "weighted_contributions": dict(score.weighted_contributions),
                # The revision each figure was read from, so the snapshot can be
                # traced back even after later revisions are added.
                "revision_id": facts_by_offer[score.offer_id].revision_id
                if score.offer_id in facts_by_offer
                else None,
                "currency": facts_by_offer[score.offer_id].currency
                if score.offer_id in facts_by_offer
                else None,
                "fx_rate_used": facts_by_offer[score.offer_id].fx_rate_used
                if score.offer_id in facts_by_offer
                else None,
                "ratings": dict(facts_by_offer[score.offer_id].ratings)
                if score.offer_id in facts_by_offer
                else {},
            }
            for score in result.offers
        ],
    }

    snapshot = DecisionSnapshot(
        name=(name or "").strip() or f"比较 {_now().date().isoformat()}",
        offer_ids_json=[s.offer_id for s in result.offers],
        payload_json=_jsonable(payload),
        notes=(notes or "").strip() or None,
        created_at=_now(),
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    # Ids and counts only - the payload holds compensation and ratings.
    log_event(
        logger,
        "decision.snapshot_saved",
        snapshot_id=snapshot.id,
        offers=len(snapshot.offer_ids_json or []),
    )
    return snapshot


def _jsonable(value: Any) -> Any:
    """Make dataclass output JSON-safe without losing anything."""
    from datetime import date

    from app.models.enums import DealBreakerResult

    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, DealBreakerResult):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def list_snapshots(db: Session, *, limit: int = 50) -> list[DecisionSnapshot]:
    return list(
        db.scalars(
            select(DecisionSnapshot)
            .order_by(DecisionSnapshot.created_at.desc(), DecisionSnapshot.id.desc())
            .limit(limit)
        )
    )


def get_snapshot(db: Session, snapshot_id: int) -> DecisionSnapshot:
    snapshot = db.get(DecisionSnapshot, snapshot_id)
    if snapshot is None:
        raise NotFoundError(
            f"决策记录 {snapshot_id} 不存在", detail={"snapshot_id": snapshot_id}
        )
    return snapshot


def delete_snapshot(db: Session, snapshot_id: int) -> None:
    db.delete(get_snapshot(db, snapshot_id))
    db.commit()
    log_event(logger, "decision.snapshot_deleted", snapshot_id=snapshot_id)


# --------------------------------------------------------------------------
# competing offers
# --------------------------------------------------------------------------


def competing_offers(db: Session, offer_id: int) -> list[Offer]:
    """Other offers still awaiting a decision.

    Surfaced as a warning when accepting one. Nothing is declined
    automatically - telling three other companies no is the user's call, and
    often needs to happen in a particular order.
    """
    return list(
        db.scalars(
            select(Offer)
            .options(selectinload(Offer.revisions), selectinload(Offer.job))
            .where(
                Offer.id != offer_id,
                Offer.status.in_([OfferStatus.received, OfferStatus.negotiating]),
            )
            .order_by(Offer.decision_deadline.asc().nulls_last())
        ).unique()
    )


# --------------------------------------------------------------------------
# negotiation position
# --------------------------------------------------------------------------


def negotiation_position(db: Session, offer: Offer) -> dict[str, Any]:
    """Where the current company offer sits against your own targets.

    Below-minimum produces a **warning**, never an automatic action. Whether an
    offer below your line is still worth taking is not arithmetic.
    """
    company = offer_management.latest_company_revision(offer)
    counter = offer_management.latest_candidate_counter(offer)
    company_break = offer_management.breakdown_of(company)
    counter_break = offer_management.breakdown_of(counter)
    assessment = get_assessment(db, offer.id)

    current = company_break.first_year_target_cash if company_break else None
    asked = counter_break.first_year_target_cash if counter_break else None
    gap = None if (current is None or asked is None) else round(asked - current, 2)

    warnings: list[str] = []
    minimum = assessment.minimum_total_cash if assessment else None
    if minimum is not None and current is not None and current < minimum:
        warnings.append(
            "当前 Offer 低于你设定的最低接受线。这只是提醒 —— 是否仍然考虑由你决定。"
        )

    return {
        "currency": offer.currency.value,
        "current_company_cash": current,
        "latest_candidate_ask": asked,
        "gap": gap,
        "target_total_cash": assessment.target_total_cash if assessment else None,
        "ideal_total_cash": assessment.ideal_total_cash if assessment else None,
        "minimum_total_cash": minimum,
        "warnings": warnings,
    }
