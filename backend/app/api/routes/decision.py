"""Offer decision-support endpoints (v1.0).

Thin routes: preferences, ratings and snapshots go through
``services/decision_support``; the arithmetic lives in ``offer_decision``.

Zero OpenAI calls. Nothing here accepts, declines or ranks anything on the
user's behalf - it scores what they told it to score and shows its working.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.api.routes.offers import _deadline_state
from app.db.session import get_db
from app.models import OFFER_STATUS_LABEL, DecisionProfile, DecisionSnapshot
from app.schemas.decision import (
    AssessmentOut,
    AssessmentUpdate,
    CompetingOfferOut,
    CompetingOffersResponse,
    DealBreakerCheckOut,
    DealBreakerItem,
    DecisionComparisonOut,
    DecisionProfileOut,
    DecisionProfileUpdate,
    DimensionScoreOut,
    NegotiationPositionOut,
    OfferScoreOut,
    SnapshotCreateRequest,
    SnapshotDetail,
    SnapshotListResponse,
    SnapshotSummary,
)
from app.services import decision_support, offer_management
from app.services.offer_decision import (
    DIMENSION_LABEL,
    ComparisonResult,
    OfferFacts,
    normalize_weights,
)

router = APIRouter(prefix="/api/decision", tags=["decision"])


def _deadline_states(db: Session, offer_ids: list[int]) -> dict[int, tuple[str, int | None]]:
    """Deadline buckets, computed once and passed through for *display*.

    Kept out of the scoring engine deliberately: urgency is not quality.
    """
    states: dict[int, tuple[str, int | None]] = {}
    for offer_id in offer_ids:
        offer = offer_management.get_offer(db, offer_id)
        states[offer_id] = _deadline_state(offer.decision_deadline)
    return states


# --------------------------------------------------------------------------
# profile
# --------------------------------------------------------------------------


def _profile_out(profile: DecisionProfile) -> DecisionProfileOut:
    return DecisionProfileOut(
        id=profile.id,
        name=profile.name,
        weights={k: float(v) for k, v in (profile.weights_json or {}).items()},
        normalized_weights=normalize_weights(profile.weights_json),
        deal_breakers=[
            DealBreakerItem(**item) for item in (profile.deal_breakers_json or [])
        ],
        fx_rates={k: float(v) for k, v in (profile.fx_rates_json or {}).items()},
        base_currency=profile.base_currency,
        notes=profile.notes,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


@router.get("/profile", response_model=DecisionProfileOut)
def get_profile(db: Session = Depends(get_db)) -> DecisionProfileOut:
    """Your weights, hard constraints and any exchange rates you supplied."""
    return _profile_out(decision_support.get_active_profile(db))


@router.patch("/profile", response_model=DecisionProfileOut)
def update_profile(
    payload: DecisionProfileUpdate = Body(...),
    db: Session = Depends(get_db),
) -> DecisionProfileOut:
    """Store preferences exactly as entered. Nothing here is suggested by AI."""
    profile = decision_support.update_profile(
        db,
        name=payload.name,
        weights=payload.weights,
        deal_breakers=(
            [item.model_dump(mode="json") for item in payload.deal_breakers]
            if payload.deal_breakers is not None
            else None
        ),
        fx_rates=payload.fx_rates,
        base_currency=payload.base_currency,
        notes=payload.notes,
    )
    return _profile_out(profile)


# --------------------------------------------------------------------------
# assessments
# --------------------------------------------------------------------------


@router.get("/offers/{offer_id}/assessment", response_model=AssessmentOut)
def get_assessment(offer_id: int, db: Session = Depends(get_db)) -> AssessmentOut:
    offer_management.get_offer(db, offer_id)  # 404s cleanly for a bad id
    row = decision_support.get_assessment(db, offer_id)
    if row is None:
        return AssessmentOut(offer_id=offer_id)
    return AssessmentOut(
        offer_id=offer_id,
        ratings={k: int(v) for k, v in (row.ratings_json or {}).items()},
        notes=row.notes,
        target_total_cash=row.target_total_cash,
        ideal_total_cash=row.ideal_total_cash,
        minimum_total_cash=row.minimum_total_cash,
        updated_at=row.updated_at,
    )


@router.put("/offers/{offer_id}/assessment", response_model=AssessmentOut)
def save_assessment(
    offer_id: int,
    payload: AssessmentUpdate = Body(...),
    db: Session = Depends(get_db),
) -> AssessmentOut:
    """Your own 1-5 read of an offer.

    Only you rate a company here - nothing is scored by a model, and a rating
    left blank stays blank rather than becoming a zero.
    """
    row = decision_support.upsert_assessment(
        db,
        offer_id,
        ratings=payload.ratings,
        notes=payload.notes,
        target_total_cash=payload.target_total_cash,
        ideal_total_cash=payload.ideal_total_cash,
        minimum_total_cash=payload.minimum_total_cash,
        clear_targets=payload.clear_targets,
    )
    return AssessmentOut(
        offer_id=offer_id,
        ratings={k: int(v) for k, v in (row.ratings_json or {}).items()},
        notes=row.notes,
        target_total_cash=row.target_total_cash,
        ideal_total_cash=row.ideal_total_cash,
        minimum_total_cash=row.minimum_total_cash,
        updated_at=row.updated_at,
    )


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------


def _comparison_out(
    result: ComparisonResult, facts: dict[int, OfferFacts]
) -> DecisionComparisonOut:
    offers = []
    for score in result.offers:
        fact = facts.get(score.offer_id)
        offers.append(
            OfferScoreOut(
                offer_id=score.offer_id,
                company=score.company,
                title=score.title,
                total_score=score.total_score,
                coverage=score.coverage,
                dimension_scores=[
                    DimensionScoreOut(
                        **asdict(d), label=DIMENSION_LABEL.get(d.dimension, d.dimension)
                    )
                    for d in score.dimension_scores
                ],
                weighted_contributions=score.weighted_contributions,
                missing_dimensions=score.missing_dimensions,
                deal_breakers=[
                    DealBreakerCheckOut(**asdict(c)) for c in score.deal_breakers
                ],
                warnings=score.warnings,
                strengths=score.strengths,
                trade_offs=score.trade_offs,
                days_to_deadline=score.days_to_deadline,
                deadline_state=score.deadline_state,
                compensation_comparable=score.compensation_comparable,
                currency=fact.currency if fact else "CNY",
                revision_id=fact.revision_id if fact else None,
                fx_rate_used=fact.fx_rate_used if fact else None,
                guaranteed_cash=fact.guaranteed_cash if fact else None,
                target_cash=fact.target_cash if fact else None,
            )
        )

    message = (
        "综合评分只是把你自己的权重和评分算了一遍，最终决定仍然由你做出。"
        if result.winner_offer_id is not None
        else (result.winner_blocked_reason or "评分已列出，但没有给出综合结论。")
    )
    return DecisionComparisonOut(
        offers=offers,
        normalized_weights=result.weights,
        base_currency=result.base_currency,
        winner_offer_id=result.winner_offer_id,
        winner_blocked_reason=result.winner_blocked_reason,
        min_coverage=result.min_coverage,
        mixed_currency=result.mixed_currency,
        fx_rates_used=result.fx_rates_used,
        notes=result.notes,
        message=message,
    )


def _run_comparison(db: Session, offer_ids: list[int]):
    unique = list(dict.fromkeys(offer_ids))
    return decision_support.compare(
        db, unique, deadline_states=_deadline_states(db, unique)
    )


@router.get("/compare", response_model=DecisionComparisonOut)
def compare(
    db: Session = Depends(get_db),
    offer_ids: list[int] = Query(default=[], alias="offer_id"),
) -> DecisionComparisonOut:
    """Score 2-4 offers against your weights.

    Every dimension's score, weight and contribution is returned so the total
    can be checked by hand - the page never shows a bare number. A winner is
    named only when weighted coverage clears ``OFFER_DECISION_MIN_COVERAGE``.
    """
    result, _profile, facts = _run_comparison(db, offer_ids)
    return _comparison_out(result, facts)


# --------------------------------------------------------------------------
# snapshots
# --------------------------------------------------------------------------


def _snapshot_summary(snapshot: DecisionSnapshot) -> SnapshotSummary:
    payload = snapshot.payload_json or {}
    return SnapshotSummary(
        id=snapshot.id,
        name=snapshot.name,
        offer_ids=list(snapshot.offer_ids_json or []),
        created_at=snapshot.created_at,
        notes=snapshot.notes,
        winner_offer_id=payload.get("winner_offer_id"),
        companies=[o.get("company", "") for o in payload.get("offers", [])],
    )


@router.post("/snapshots", response_model=SnapshotDetail)
def create_snapshot(
    payload: SnapshotCreateRequest = Body(...),
    db: Session = Depends(get_db),
) -> SnapshotDetail:
    """Freeze a comparison.

    Stores the offers, the exact revision each figure came from, the weights,
    the ratings, the FX rates, the deal-breakers and the results. Changing any
    of those later leaves this record untouched.
    """
    result, profile, facts = _run_comparison(db, payload.offer_ids)
    snapshot = decision_support.save_snapshot(
        db,
        name=payload.name,
        result=result,
        profile=profile,
        facts_by_offer=facts,
        notes=payload.notes,
    )
    summary = _snapshot_summary(snapshot)
    return SnapshotDetail(**summary.model_dump(), payload=snapshot.payload_json or {})


@router.get("/snapshots", response_model=SnapshotListResponse)
def list_snapshots(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
) -> SnapshotListResponse:
    rows = decision_support.list_snapshots(db, limit=limit)
    return SnapshotListResponse(
        items=[_snapshot_summary(r) for r in rows], total=len(rows)
    )


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotDetail)
def get_snapshot(snapshot_id: int, db: Session = Depends(get_db)) -> SnapshotDetail:
    snapshot = decision_support.get_snapshot(db, snapshot_id)
    summary = _snapshot_summary(snapshot)
    return SnapshotDetail(**summary.model_dump(), payload=snapshot.payload_json or {})


@router.delete("/snapshots/{snapshot_id}")
def delete_snapshot(snapshot_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    decision_support.delete_snapshot(db, snapshot_id)
    return {"message": "已删除该决策记录"}


# --------------------------------------------------------------------------
# negotiation + competing offers
# --------------------------------------------------------------------------


@router.get(
    "/offers/{offer_id}/negotiation", response_model=NegotiationPositionOut
)
def negotiation_position(
    offer_id: int, db: Session = Depends(get_db)
) -> NegotiationPositionOut:
    """The company's current offer against your own targets.

    Below-minimum shows a warning. It never declines anything.
    """
    offer = offer_management.get_offer(db, offer_id)
    return NegotiationPositionOut(**decision_support.negotiation_position(db, offer))


@router.get(
    "/offers/{offer_id}/competing", response_model=CompetingOffersResponse
)
def competing(offer_id: int, db: Session = Depends(get_db)) -> CompetingOffersResponse:
    """Other offers still awaiting a decision.

    Shown before accepting so nothing is forgotten. Accepting is never blocked,
    and no competing offer is ever declined automatically - telling the others
    no is your call, and often needs to happen in a particular order.
    """
    offer_management.get_offer(db, offer_id)
    rows = decision_support.competing_offers(db, offer_id)

    items = []
    for row in rows:
        state, days = _deadline_state(row.decision_deadline)
        items.append(
            CompetingOfferOut(
                offer_id=row.id,
                company=row.job.company if row.job else "",
                title=row.job.title if row.job else "",
                status=OFFER_STATUS_LABEL[row.status],
                decision_deadline=row.decision_deadline,
                days_to_deadline=days,
            )
        )

    return CompetingOffersResponse(
        items=items,
        total=len(items),
        message=(
            f"你还有 {len(items)} 个 Offer 尚未决定。接受这一个不会自动拒绝它们 —— "
            "需要你自己逐个答复。"
            if items
            else "没有其他待决定的 Offer。"
        ),
    )
