"""Decision-support persistence and orchestration (v1.0).

Where ``test_offer_decision`` covers the arithmetic, this covers the wiring:
which revision a figure is read from, when a currency is convertible, and what
a frozen snapshot promises.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.models import Currency
from app.schemas.offer import AcceptOfferRequest
from app.services import decision_support, offer_management

from tests.test_offer_management import applied_job, company_revision, counter, make_offer


def offer_with(db, amount: float, **kwargs):
    job = applied_job(db, **{k: v for k, v in kwargs.pop("job", {}).items()})
    return make_offer(db, job, initial={"base_salary_annual": amount}, **kwargs)


def weights(db, **raw):
    return decision_support.update_profile(db, weights=raw)


# --------------------------------------------------------------------------
# profile
# --------------------------------------------------------------------------


def test_a_new_profile_weighs_nothing(db):
    """The app must not ship an opinion about what makes a job good."""
    profile = decision_support.get_active_profile(db)
    assert profile.weights_json == {}
    assert profile.deal_breakers_json == []
    assert profile.fx_rates_json == {}
    assert profile.base_currency is Currency.CNY


def test_the_same_profile_is_reused(db):
    first = decision_support.get_active_profile(db)
    second = decision_support.get_active_profile(db)
    assert first.id == second.id


def test_weights_are_stored_raw_not_normalized(db):
    """So the numbers the user typed are still recognisable when they return."""
    profile = weights(db, compensation=5, career_growth=3)
    assert profile.weights_json == {"compensation": 5.0, "career_growth": 3.0}


def test_a_zero_weight_is_kept_as_entered(db):
    profile = weights(db, compensation=5, brand_value=0)
    assert profile.weights_json["brand_value"] == 0.0


def test_an_unknown_dimension_is_rejected(db):
    with pytest.raises(ValidationError):
        weights(db, astrology=5)


def test_a_negative_weight_is_rejected(db):
    with pytest.raises(ValidationError) as excinfo:
        weights(db, compensation=-1)
    assert "0" in str(excinfo.value)


def test_an_unknown_deal_breaker_is_rejected(db):
    with pytest.raises(ValidationError):
        decision_support.update_profile(db, deal_breakers=[{"kind": "must_be_fun"}])


def test_a_non_positive_exchange_rate_is_rejected(db):
    with pytest.raises(ValidationError):
        decision_support.update_profile(db, fx_rates={"JPY": 0})


# --------------------------------------------------------------------------
# assessments
# --------------------------------------------------------------------------


def test_ratings_are_stored_and_replaced(db):
    offer = offer_with(db, 300_000)
    decision_support.upsert_assessment(db, offer.id, ratings={"career_growth": 4})
    row = decision_support.upsert_assessment(db, offer.id, ratings={"career_growth": 2})
    assert row.ratings_json == {"career_growth": 2}


def test_a_null_rating_means_no_view_not_a_one(db):
    offer = offer_with(db, 300_000)
    decision_support.upsert_assessment(
        db, offer.id, ratings={"career_growth": 4, "role_fit": 5}
    )
    row = decision_support.upsert_assessment(
        db, offer.id, ratings={"career_growth": None, "role_fit": 5}
    )
    assert "career_growth" not in row.ratings_json
    assert row.ratings_json == {"role_fit": 5}


def test_a_rating_outside_one_to_five_is_rejected(db):
    offer = offer_with(db, 300_000)
    with pytest.raises(ValidationError):
        decision_support.upsert_assessment(db, offer.id, ratings={"role_fit": 7})


def test_one_assessment_per_offer(db):
    offer = offer_with(db, 300_000)
    first = decision_support.upsert_assessment(db, offer.id, ratings={"role_fit": 3})
    second = decision_support.upsert_assessment(db, offer.id, notes="想清楚了")
    assert first.id == second.id
    assert second.ratings_json == {"role_fit": 3}


# --------------------------------------------------------------------------
# which revision the figures come from
# --------------------------------------------------------------------------


def test_a_candidate_counter_is_not_read_as_the_company_offer(db):
    """The load-bearing one: what you asked for must never score as an offer."""
    offer = offer_with(db, 300_000)
    counter(db, offer, base_salary_annual=500_000)
    offer = offer_management.get_offer(db, offer.id)

    facts = decision_support.build_facts(
        db, offer, base_currency="CNY", fx_rates={}
    )
    assert facts.guaranteed_cash == 300_000


def test_the_latest_company_revision_is_used(db):
    offer = offer_with(db, 300_000)
    counter(db, offer, base_salary_annual=400_000)
    company_revision(db, offer, base_salary_annual=340_000)
    offer = offer_management.get_offer(db, offer.id)

    facts = decision_support.build_facts(db, offer, base_currency="CNY", fx_rates={})
    assert facts.guaranteed_cash == 340_000


def test_an_accepted_offer_reads_the_frozen_revision(db):
    """``accepted_revision_id`` is what every later read uses.

    Here the accepted revision is deliberately *not* the latest company one, so
    "latest wins" and "accepted wins" give different answers - and only the
    second is correct.
    """
    offer = offer_with(db, 300_000)
    initial_id = offer.revisions[0].id
    company_revision(db, offer, base_salary_annual=350_000)
    offer = offer_management.get_offer(db, offer.id)
    assert offer_management.latest_company_revision(offer).id != initial_id

    offer_management.accept_offer(
        db, offer.id, AcceptOfferRequest(confirmed=True, revision_id=initial_id)
    )
    offer = offer_management.get_offer(db, offer.id)

    facts = decision_support.build_facts(db, offer, base_currency="CNY", fx_rates={})
    assert offer.accepted_revision_id == initial_id
    assert facts.revision_id == initial_id
    assert facts.guaranteed_cash == 300_000


def test_an_accepted_offer_takes_no_further_revisions(db):
    """Which is the other half of why the frozen id cannot drift."""
    offer = offer_with(db, 300_000)
    offer_management.accept_offer(db, offer.id, AcceptOfferRequest(confirmed=True))
    with pytest.raises(ValidationError):
        company_revision(
            db, offer_management.get_offer(db, offer.id), base_salary_annual=999_000
        )


# --------------------------------------------------------------------------
# currencies
# --------------------------------------------------------------------------


def test_without_a_rate_a_foreign_offer_is_not_comparable(db):
    job = applied_job(db)
    offer = make_offer(
        db, job, currency=Currency.JPY, initial={"base_salary_annual": 8_000_000}
    )
    facts = decision_support.build_facts(db, offer, base_currency="CNY", fx_rates={})

    assert facts.guaranteed_cash == 8_000_000
    assert facts.comparable_guaranteed_cash is None
    assert facts.compensation_comparable_flag() is False


def test_a_supplied_rate_converts_and_is_recorded(db):
    job = applied_job(db)
    offer = make_offer(
        db, job, currency=Currency.JPY, initial={"base_salary_annual": 10_000_000}
    )
    facts = decision_support.build_facts(
        db, offer, base_currency="CNY", fx_rates={"JPY": 0.05}
    )
    assert facts.comparable_guaranteed_cash == pytest.approx(500_000)
    assert facts.fx_rate_used == 0.05


def test_the_base_currency_needs_no_rate(db):
    offer = offer_with(db, 300_000)
    facts = decision_support.build_facts(db, offer, base_currency="CNY", fx_rates={})
    assert facts.comparable_guaranteed_cash == 300_000
    assert facts.fx_rate_used is None


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------


def test_comparing_fewer_than_two_offers_is_rejected(db):
    offer = offer_with(db, 300_000)
    with pytest.raises(ValidationError):
        decision_support.compare(db, [offer.id])


def test_a_duplicate_offer_id_is_not_a_comparison(db):
    offer = offer_with(db, 300_000)
    with pytest.raises(ValidationError):
        decision_support.compare(db, [offer.id, offer.id])


def test_the_comparison_uses_the_stored_weights(db):
    weights(db, compensation=1)
    a = offer_with(db, 400_000)
    b = offer_with(db, 300_000)

    result, profile, facts = decision_support.compare(db, [a.id, b.id])
    assert result.weights == {"compensation": 1.0}
    assert result.winner_offer_id == a.id
    assert set(facts) == {a.id, b.id}
    assert profile.weights_json == {"compensation": 1.0}


def test_visa_support_is_read_from_the_recorded_benefits(db):
    job = applied_job(db)
    offer = make_offer(
        db, job, initial={"base_salary_annual": 300_000}, benefits={"visa_support": True}
    )
    facts = decision_support.build_facts(db, offer, base_currency="CNY", fx_rates={})
    assert facts.visa_support is True

    plain = offer_with(db, 300_000)
    assert (
        decision_support.build_facts(
            db, plain, base_currency="CNY", fx_rates={}
        ).visa_support
        is None
    )


# --------------------------------------------------------------------------
# snapshots
# --------------------------------------------------------------------------


def snapshot_of(db, offer_ids, name="比较"):
    result, profile, facts = decision_support.compare(db, offer_ids)
    return decision_support.save_snapshot(
        db, name=name, result=result, profile=profile, facts_by_offer=facts
    )


def test_a_snapshot_records_the_whole_computation(db):
    weights(db, compensation=6, career_growth=4)
    a = offer_with(db, 400_000)
    b = offer_with(db, 300_000)
    decision_support.upsert_assessment(db, a.id, ratings={"career_growth": 5})
    decision_support.upsert_assessment(db, b.id, ratings={"career_growth": 3})

    snapshot = snapshot_of(db, [a.id, b.id])
    payload = snapshot.payload_json

    assert payload["raw_weights"] == {"compensation": 6.0, "career_growth": 4.0}
    assert payload["normalized_weights"] == {"compensation": 0.6, "career_growth": 0.4}
    assert snapshot.offer_ids_json == [a.id, b.id]

    first = next(o for o in payload["offers"] if o["offer_id"] == a.id)
    assert first["revision_id"] is not None
    assert first["ratings"] == {"career_growth": 5}
    assert first["weighted_contributions"]["compensation"] == pytest.approx(0.6)


def test_a_snapshot_does_not_change_when_the_offer_does(db):
    """The point of freezing: a past decision stays explicable in past terms."""
    weights(db, compensation=1)
    a = offer_with(db, 400_000)
    b = offer_with(db, 300_000)
    snapshot = snapshot_of(db, [a.id, b.id])
    before = snapshot.payload_json

    company_revision(db, offer_management.get_offer(db, b.id), base_salary_annual=900_000)
    decision_support.upsert_assessment(db, b.id, ratings={"career_growth": 5})
    decision_support.update_profile(db, weights={"remote_work": 10})

    after = decision_support.get_snapshot(db, snapshot.id).payload_json
    assert after == before
    assert after["winner_offer_id"] == a.id


def test_a_snapshot_records_the_exact_exchange_rate_used(db):
    """Not the rate today - the rate that produced these numbers."""
    decision_support.update_profile(
        db, weights={"compensation": 1}, fx_rates={"JPY": 0.05}
    )
    cny = offer_with(db, 400_000)
    job = applied_job(db)
    jpy = make_offer(
        db, job, currency=Currency.JPY, initial={"base_salary_annual": 10_000_000}
    )

    snapshot = snapshot_of(db, [cny.id, jpy.id])
    assert snapshot.payload_json["fx_rates_used"] == {"JPY": 0.05}
    foreign = next(
        o for o in snapshot.payload_json["offers"] if o["offer_id"] == jpy.id
    )
    assert foreign["fx_rate_used"] == 0.05
    assert foreign["currency"] == "JPY"

    decision_support.update_profile(db, fx_rates={"JPY": 0.09})
    assert decision_support.get_snapshot(db, snapshot.id).payload_json[
        "fx_rates_used"
    ] == {"JPY": 0.05}


def test_two_snapshots_of_unchanged_data_agree(db):
    weights(db, compensation=1)
    a = offer_with(db, 400_000)
    b = offer_with(db, 300_000)

    first = snapshot_of(db, [a.id, b.id])
    second = snapshot_of(db, [a.id, b.id])
    assert first.payload_json == second.payload_json
    assert first.id != second.id


def test_snapshots_are_listed_newest_first_and_can_be_deleted(db):
    weights(db, compensation=1)
    a = offer_with(db, 400_000)
    b = offer_with(db, 300_000)
    old = snapshot_of(db, [a.id, b.id], name="第一次")
    new = snapshot_of(db, [a.id, b.id], name="第二次")

    listed = decision_support.list_snapshots(db)
    assert [s.id for s in listed][0] == new.id

    decision_support.delete_snapshot(db, old.id)
    assert [s.id for s in decision_support.list_snapshots(db)] == [new.id]


# --------------------------------------------------------------------------
# competing offers and negotiation
# --------------------------------------------------------------------------


def test_competing_offers_lists_the_undecided_ones(db):
    keep = offer_with(db, 400_000)
    other = offer_with(db, 300_000)
    settled = offer_with(db, 200_000)
    offer_management.accept_offer(db, settled.id, AcceptOfferRequest(confirmed=True))

    ids = [o.id for o in decision_support.competing_offers(db, keep.id)]
    assert ids == [other.id]


def test_accepting_does_not_decline_the_others(db):
    keep = offer_with(db, 400_000)
    other = offer_with(db, 300_000)

    offer_management.accept_offer(db, keep.id, AcceptOfferRequest(confirmed=True))

    still_open = offer_management.get_offer(db, other.id)
    assert still_open.status.value in {"received", "negotiating"}
    assert still_open.decline_reason is None


def test_the_negotiation_position_shows_the_gap(db):
    offer = offer_with(db, 300_000)
    counter(db, offer, base_salary_annual=360_000)
    offer = offer_management.get_offer(db, offer.id)

    position = decision_support.negotiation_position(db, offer)
    assert position["current_company_cash"] == 300_000
    assert position["latest_candidate_ask"] == 360_000
    assert position["gap"] == pytest.approx(60_000)


def test_below_the_minimum_warns_and_nothing_else(db):
    offer = offer_with(db, 250_000)
    decision_support.upsert_assessment(db, offer.id, minimum_total_cash=300_000)
    offer = offer_management.get_offer(db, offer.id)

    position = decision_support.negotiation_position(db, offer)
    assert position["warnings"], "a below-minimum offer should be called out"
    assert offer.status.value == "received"  # not declined, not closed


def test_the_negotiation_position_survives_a_missing_counter(db):
    offer = offer_with(db, 300_000)
    position = decision_support.negotiation_position(db, offer)
    assert position["latest_candidate_ask"] is None
    assert position["gap"] is None


# --------------------------------------------------------------------------
# cost
# --------------------------------------------------------------------------


def test_decision_support_makes_no_openai_call(db, monkeypatch):
    import app.services.job_matcher as job_matcher

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("decision support must never call the model")

    monkeypatch.setattr(job_matcher, "run_job_match", _explode)

    weights(db, compensation=5, career_growth=3, remote_work=2)
    a = offer_with(db, 400_000)
    b = offer_with(db, 300_000)
    decision_support.upsert_assessment(db, a.id, ratings={"career_growth": 4})

    result, profile, facts = decision_support.compare(db, [a.id, b.id])
    decision_support.save_snapshot(
        db, name="无AI", result=result, profile=profile, facts_by_offer=facts
    )
    assert len(result.offers) == 2
