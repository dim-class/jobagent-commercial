"""The deterministic offer-scoring engine (v1.0).

The load-bearing tests are about what the engine *refuses* to do: invent a
weight, treat unknown as zero, name a winner over missing data, mix currencies
without a rate the user supplied, or let a deadline make an offer look better.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models.enums import DealBreakerKind, DealBreakerResult
from app.services.offer_decision import (
    DealBreaker,
    OfferFacts,
    compare_offers,
    evaluate_deal_breaker,
    normalize_weights,
    rating_to_score,
    score_offer,
)


def facts(offer_id: int, **kwargs) -> OfferFacts:
    base = {
        "company": f"公司{offer_id}",
        "title": "云平台工程师",
        "currency": "CNY",
    }
    base.update(kwargs)
    return OfferFacts(offer_id=offer_id, **base)


def cash(offer_id: int, amount: float, **kwargs) -> OfferFacts:
    """An offer whose compensation is already on the comparison's scale."""
    kwargs.setdefault("guaranteed_cash", amount)
    kwargs.setdefault("comparable_guaranteed_cash", amount)
    return facts(offer_id, **kwargs)


# --------------------------------------------------------------------------
# weights
# --------------------------------------------------------------------------


def test_weights_are_normalized_to_sum_to_one():
    weights = normalize_weights({"compensation": 5, "career_growth": 3, "remote_work": 2})
    assert weights == {"compensation": 0.5, "career_growth": 0.3, "remote_work": 0.2}
    assert sum(weights.values()) == pytest.approx(1.0)


def test_any_scale_of_weights_gives_the_same_distribution():
    """1/2/3 and 100/200/300 mean the same thing - only the ratios matter."""
    small = normalize_weights({"compensation": 1, "location": 2, "role_fit": 3})
    large = normalize_weights({"compensation": 100, "location": 200, "role_fit": 300})
    assert small == large


def test_all_zero_weights_produce_no_distribution():
    """Not an even split - the app must never invent an opinion about the job."""
    assert normalize_weights({"compensation": 0, "location": 0}) == {}
    assert normalize_weights({}) == {}
    assert normalize_weights(None) == {}


def test_a_zero_weight_dimension_is_dropped_not_scored():
    weights = normalize_weights({"compensation": 4, "brand_value": 0})
    assert weights == {"compensation": 1.0}


def test_negative_and_unknown_weights_are_dropped():
    weights = normalize_weights(
        {"compensation": 2, "career_growth": -5, "astrology": 9}
    )
    assert weights == {"compensation": 1.0}


def test_with_no_weights_there_is_no_score():
    score = score_offer(cash(1, 300_000), weights={})
    assert score.total_score is None
    assert score.coverage == 0.0
    assert any("权重" in w for w in score.warnings)


# --------------------------------------------------------------------------
# ratings
# --------------------------------------------------------------------------


def test_ratings_map_one_to_zero_and_five_to_one():
    assert rating_to_score(1) == 0.0
    assert rating_to_score(3) == 0.5
    assert rating_to_score(5) == 1.0


def test_an_absent_rating_is_not_a_zero():
    """The whole point: 未评分 lowers coverage, it does not lower the score."""
    weights = normalize_weights({"career_growth": 1, "role_fit": 1})

    rated = score_offer(facts(1, ratings={"career_growth": 5, "role_fit": 5}), weights=weights)
    partial = score_offer(facts(2, ratings={"career_growth": 5}), weights=weights)

    assert rated.total_score == 1.0
    assert partial.total_score == 1.0  # NOT 0.5 - the unrated half is excluded
    assert partial.coverage == pytest.approx(0.5)
    assert partial.missing_dimensions == ["role_fit"]


def test_a_rating_outside_the_scale_is_treated_as_unrated():
    weights = normalize_weights({"career_growth": 1})
    score = score_offer(facts(1, ratings={"career_growth": 9}), weights=weights)
    assert score.total_score is None
    assert score.missing_dimensions == ["career_growth"]


def test_the_user_rating_is_a_fallback_for_a_derived_dimension():
    """Remote policy unrecorded, but the user has a view - use it, and say so."""
    weights = normalize_weights({"remote_work": 1})
    score = score_offer(
        facts(1, remote_policy="unknown", ratings={"remote_work": 4}), weights=weights
    )
    assert score.total_score == pytest.approx(0.75)
    assert score.dimension_scores[0].source == "rating"


# --------------------------------------------------------------------------
# coverage and transparency
# --------------------------------------------------------------------------


def test_the_total_is_exactly_the_sum_of_the_contributions():
    """Nothing in the total that is not shown in the breakdown."""
    weights = normalize_weights({"compensation": 5, "career_growth": 3, "remote_work": 2})
    score = score_offer(
        cash(1, 400_000, remote_policy="remote", ratings={"career_growth": 4}),
        weights=weights,
        best_guaranteed=400_000,
    )

    contributions = score.weighted_contributions
    assert contributions["compensation"] == pytest.approx(0.5)
    assert contributions["career_growth"] == pytest.approx(0.3 * 0.75)
    assert contributions["remote_work"] == pytest.approx(0.2)
    assert score.coverage == pytest.approx(1.0)
    assert score.total_score == pytest.approx(sum(contributions.values()))


def test_every_weighted_dimension_appears_in_the_breakdown():
    weights = normalize_weights({"compensation": 1, "language_environment": 1})
    score = score_offer(cash(1, 300_000), weights=weights, best_guaranteed=300_000)
    assert {d.dimension for d in score.dimension_scores} == set(weights)
    missing = [d for d in score.dimension_scores if not d.known]
    assert missing[0].score is None and missing[0].contribution is None


def test_a_partially_covered_offer_reports_its_coverage():
    weights = normalize_weights(
        {"compensation": 4, "career_growth": 3, "work_life_balance": 3}
    )
    score = score_offer(
        cash(1, 300_000, ratings={"career_growth": 5}),
        weights=weights,
        best_guaranteed=300_000,
    )
    assert score.coverage == pytest.approx(0.7)
    assert score.missing_dimensions == ["work_life_balance"]


# --------------------------------------------------------------------------
# compensation
# --------------------------------------------------------------------------


def test_guaranteed_cash_dominates_target_cash():
    """A big target bonus must not out-rank money that is actually guaranteed."""
    weights = normalize_weights({"compensation": 1})
    solid = cash(1, 400_000, target_cash=400_000, comparable_target_cash=400_000)
    speculative = cash(
        2, 300_000, target_cash=500_000, comparable_target_cash=500_000
    )

    result = compare_offers(
        [solid, speculative], raw_weights={"compensation": 1}, min_coverage=0.7
    )
    by_id = {s.offer_id: s for s in result.offers}
    assert by_id[1].total_score > by_id[2].total_score
    assert weights == {"compensation": 1.0}


def test_unvalued_equity_does_not_inflate_the_score():
    weights = normalize_weights({"compensation": 1})
    score = score_offer(
        cash(1, 300_000, equity_excluded=True),
        weights=weights,
        best_guaranteed=300_000,
    )
    assert score.total_score == 1.0  # scored on cash alone
    assert any("股权" in w for w in score.warnings)


def test_an_offer_with_no_comparable_cash_is_missing_not_zero():
    weights = normalize_weights({"compensation": 1})
    score = score_offer(facts(1), weights=weights, best_guaranteed=300_000)
    assert score.total_score is None
    assert score.missing_dimensions == ["compensation"]


# --------------------------------------------------------------------------
# currencies
# --------------------------------------------------------------------------


def test_same_currency_offers_compare_directly():
    result = compare_offers(
        [cash(1, 400_000), cash(2, 300_000)],
        raw_weights={"compensation": 1},
        base_currency="CNY",
    )
    assert result.mixed_currency is False
    assert result.winner_offer_id == 1


def test_without_a_rate_cross_currency_compensation_is_not_comparable():
    """8,000,000 JPY must never be ranked below 400,000 CNY by raw number."""
    cny = cash(1, 400_000, currency="CNY")
    jpy = facts(2, currency="JPY", guaranteed_cash=8_000_000)  # no comparable value

    result = compare_offers(
        [cny, jpy],
        raw_weights={"compensation": 1},
        base_currency="CNY",
        fx_rates={},
    )
    by_id = {s.offer_id: s for s in result.offers}

    assert result.mixed_currency is True
    assert by_id[2].compensation_comparable is False
    assert by_id[2].total_score is None
    assert any("汇率" in n for n in result.notes)
    assert result.winner_offer_id is None


def test_with_a_supplied_rate_cross_currency_offers_compare():
    rate = 0.05  # CNY per JPY, entered by the user
    cny = cash(1, 400_000, currency="CNY")
    jpy = facts(
        2,
        currency="JPY",
        guaranteed_cash=10_000_000,
        comparable_guaranteed_cash=10_000_000 * rate,
        fx_rate_used=rate,
    )

    result = compare_offers(
        [cny, jpy],
        raw_weights={"compensation": 1},
        base_currency="CNY",
        fx_rates={"JPY": rate},
    )
    assert result.winner_offer_id == 2  # 500,000 CNY-equivalent
    assert result.fx_rates_used == {"JPY": rate}
    assert any("汇率" in n for n in result.notes)


# --------------------------------------------------------------------------
# deal breakers
# --------------------------------------------------------------------------


def test_a_deal_breaker_passes_fails_or_is_unknown():
    breaker = DealBreaker(kind=DealBreakerKind.minimum_guaranteed_cash, value=350_000)

    assert evaluate_deal_breaker(breaker, cash(1, 400_000)).result is DealBreakerResult.passed
    assert evaluate_deal_breaker(breaker, cash(2, 300_000)).result is DealBreakerResult.failed
    # No figure recorded at all - unknown, which is not a failure.
    assert evaluate_deal_breaker(breaker, facts(3)).result is DealBreakerResult.unknown


def test_an_unrecorded_fact_is_unknown_not_failed():
    checks = [
        evaluate_deal_breaker(
            DealBreaker(kind=DealBreakerKind.requires_visa_support), facts(1)
        ),
        evaluate_deal_breaker(
            DealBreaker(kind=DealBreakerKind.requires_remote_or_hybrid),
            facts(1, remote_policy="unknown"),
        ),
        evaluate_deal_breaker(
            DealBreaker(kind=DealBreakerKind.required_location, value="东京"), facts(1)
        ),
        evaluate_deal_breaker(
            DealBreaker(kind=DealBreakerKind.latest_start_date, value="2026-10-01"),
            facts(1),
        ),
    ]
    assert all(c.result is DealBreakerResult.unknown for c in checks)


def test_location_and_start_date_deal_breakers_evaluate():
    location = DealBreaker(kind=DealBreakerKind.required_location, value="东京")
    assert (
        evaluate_deal_breaker(location, facts(1, work_location="东京都港区")).result
        is DealBreakerResult.passed
    )
    assert (
        evaluate_deal_breaker(location, facts(1, work_location="大阪市")).result
        is DealBreakerResult.failed
    )

    start = DealBreaker(kind=DealBreakerKind.latest_start_date, value="2026-10-01")
    assert (
        evaluate_deal_breaker(start, facts(1, proposed_start_date=date(2026, 9, 1))).result
        is DealBreakerResult.passed
    )
    assert (
        evaluate_deal_breaker(start, facts(1, proposed_start_date=date(2026, 11, 1))).result
        is DealBreakerResult.failed
    )


def test_a_failed_deal_breaker_warns_but_does_not_reject():
    score = score_offer(
        cash(1, 200_000, ratings={"career_growth": 5}),
        weights=normalize_weights({"compensation": 1, "career_growth": 1}),
        deal_breakers=[
            DealBreaker(kind=DealBreakerKind.minimum_guaranteed_cash, value=400_000)
        ],
        best_guaranteed=200_000,
    )
    assert score.deal_breakers[0].result is DealBreakerResult.failed
    assert score.total_score is not None  # still scored, still shown
    assert any("硬性条件" in w for w in score.warnings)


def test_a_failed_deal_breaker_does_not_remove_the_offer_from_the_comparison():
    result = compare_offers(
        [cash(1, 200_000), cash(2, 400_000)],
        raw_weights={"compensation": 1},
        deal_breakers=[
            DealBreaker(kind=DealBreakerKind.minimum_guaranteed_cash, value=300_000)
        ],
    )
    assert {s.offer_id for s in result.offers} == {1, 2}


# --------------------------------------------------------------------------
# naming a winner
# --------------------------------------------------------------------------


def test_no_winner_below_the_coverage_floor():
    weights = {"compensation": 1, "career_growth": 1, "work_life_balance": 1}
    result = compare_offers(
        [cash(1, 400_000), cash(2, 300_000)], raw_weights=weights, min_coverage=0.7
    )
    assert result.winner_offer_id is None
    assert "覆盖率" in result.winner_blocked_reason
    # The scores are still reported - withholding a verdict is not withholding data.
    assert all(s.total_score is not None for s in result.offers)


def test_no_winner_when_scores_tie():
    result = compare_offers(
        [cash(1, 400_000), cash(2, 400_000)], raw_weights={"compensation": 1}
    )
    assert result.winner_offer_id is None
    assert "持平" in result.winner_blocked_reason


def test_no_winner_without_weights():
    result = compare_offers([cash(1, 400_000), cash(2, 300_000)], raw_weights={})
    assert result.winner_offer_id is None
    assert all(s.total_score is None for s in result.offers)


def test_a_winner_is_named_when_coverage_is_adequate():
    result = compare_offers(
        [cash(1, 400_000), cash(2, 300_000)],
        raw_weights={"compensation": 1},
        min_coverage=0.7,
    )
    assert result.winner_offer_id == 1
    assert result.winner_blocked_reason == ""


# --------------------------------------------------------------------------
# urgency is not quality
# --------------------------------------------------------------------------


def test_the_deadline_never_changes_the_score():
    weights = {"compensation": 1}
    calm = compare_offers(
        [cash(1, 400_000), cash(2, 300_000)], raw_weights=weights
    )
    urgent = compare_offers(
        [
            cash(1, 400_000, days_to_deadline=30, deadline_state="later"),
            cash(2, 300_000, days_to_deadline=0, deadline_state="today"),
        ],
        raw_weights=weights,
    )

    assert [s.total_score for s in calm.offers] == [s.total_score for s in urgent.offers]
    assert calm.winner_offer_id == urgent.winner_offer_id
    # Carried for display, and only for display.
    assert urgent.offers[1].deadline_state == "today"


# --------------------------------------------------------------------------
# strengths and trade-offs
# --------------------------------------------------------------------------


def test_strengths_are_ranked_by_contribution_not_raw_score():
    """A dimension weighted at 2% is not a headline however well it scores."""
    score = score_offer(
        cash(
            1,
            400_000,
            ratings={"brand_value": 5, "career_growth": 4},
        ),
        weights=normalize_weights(
            {"compensation": 60, "career_growth": 38, "brand_value": 2}
        ),
        best_guaranteed=400_000,
    )
    assert score.strengths[0].startswith("薪酬")
    assert "品牌价值" not in score.strengths[0]


def test_trade_offs_name_the_weak_dimensions():
    score = score_offer(
        cash(1, 400_000, remote_policy="onsite", ratings={"work_life_balance": 1}),
        weights=normalize_weights(
            {"compensation": 3, "remote_work": 3, "work_life_balance": 3}
        ),
        best_guaranteed=400_000,
    )
    joined = "".join(score.trade_offs)
    assert "远程办公" in joined
    assert "工作生活平衡" in joined
