"""Decision-support HTTP surface (v1.0).

The page must never be able to show a bare total. Every response here carries
the weights, the per-dimension scores, the contributions and the coverage that
produced the number - or no number at all.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.services.timezones import local_now

from tests.test_offer_api import applied, create


def offer_for(client, db, amount: float, **payload) -> dict:
    job = applied(client, db)
    return create(client, job.id, initial={"base_salary_annual": amount}, **payload)


def set_weights(client, **raw) -> dict:
    response = client.patch("/api/decision/profile", json={"weights": raw})
    assert response.status_code == 200, response.text
    return response.json()


def compare(client, *offer_ids) -> dict:
    query = "&".join(f"offer_id={oid}" for oid in offer_ids)
    response = client.get(f"/api/decision/compare?{query}")
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------
# profile
# --------------------------------------------------------------------------


def test_the_profile_starts_empty(client):
    body = client.get("/api/decision/profile").json()
    assert body["weights"] == {}
    assert body["normalized_weights"] == {}
    assert body["deal_breakers"] == []
    assert body["base_currency"] == "CNY"


def test_the_profile_returns_both_raw_and_normalized_weights(client):
    body = set_weights(client, compensation=5, career_growth=3, remote_work=2)
    assert body["weights"] == {"compensation": 5, "career_growth": 3, "remote_work": 2}
    assert body["normalized_weights"] == {
        "compensation": 0.5,
        "career_growth": 0.3,
        "remote_work": 0.2,
    }


def test_an_unknown_dimension_is_a_clean_422(client):
    response = client.patch("/api/decision/profile", json={"weights": {"vibes": 5}})
    assert response.status_code == 422
    assert "vibes" in response.text


def test_deal_breakers_and_rates_round_trip(client):
    response = client.patch(
        "/api/decision/profile",
        json={
            "deal_breakers": [
                {"kind": "minimum_guaranteed_cash", "value": 350000},
                {"kind": "required_location", "value": "东京"},
            ],
            "fx_rates": {"JPY": 0.05},
            "base_currency": "CNY",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [b["kind"] for b in body["deal_breakers"]] == [
        "minimum_guaranteed_cash",
        "required_location",
    ]
    assert body["fx_rates"] == {"JPY": 0.05}


# --------------------------------------------------------------------------
# assessments
# --------------------------------------------------------------------------


def test_an_unrated_offer_returns_an_empty_assessment(client, db):
    offer = offer_for(client, db, 300000)
    body = client.get(f"/api/decision/offers/{offer['id']}/assessment").json()
    assert body == {
        "offer_id": offer["id"],
        "ratings": {},
        "notes": None,
        "target_total_cash": None,
        "ideal_total_cash": None,
        "minimum_total_cash": None,
        "updated_at": None,
    }


def test_ratings_round_trip(client, db):
    offer = offer_for(client, db, 300000)
    response = client.put(
        f"/api/decision/offers/{offer['id']}/assessment",
        json={"ratings": {"career_growth": 4, "work_life_balance": 2}, "notes": "还行"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["ratings"] == {"career_growth": 4, "work_life_balance": 2}

    stored = client.get(f"/api/decision/offers/{offer['id']}/assessment").json()
    assert stored["notes"] == "还行"


def test_a_null_rating_clears_it(client, db):
    offer = offer_for(client, db, 300000)
    client.put(
        f"/api/decision/offers/{offer['id']}/assessment",
        json={"ratings": {"career_growth": 4}},
    )
    body = client.put(
        f"/api/decision/offers/{offer['id']}/assessment",
        json={"ratings": {"career_growth": None}},
    ).json()
    assert body["ratings"] == {}


def test_an_out_of_range_rating_is_a_clean_422(client, db):
    offer = offer_for(client, db, 300000)
    response = client.put(
        f"/api/decision/offers/{offer['id']}/assessment",
        json={"ratings": {"career_growth": 9}},
    )
    assert response.status_code == 422


def test_an_assessment_for_a_missing_offer_is_404(client):
    assert client.get("/api/decision/offers/9999/assessment").status_code == 404


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------


def test_a_comparison_shows_its_working(client, db):
    set_weights(client, compensation=5, career_growth=3, remote_work=2)
    a = offer_for(client, db, 400000, remote_policy="remote")
    b = offer_for(client, db, 300000, remote_policy="onsite")
    for offer, rating in ((a, 4), (b, 5)):
        client.put(
            f"/api/decision/offers/{offer['id']}/assessment",
            json={"ratings": {"career_growth": rating}},
        )

    body = compare(client, a["id"], b["id"])
    first = next(o for o in body["offers"] if o["offer_id"] == a["id"])

    assert body["normalized_weights"]["compensation"] == 0.5
    assert first["coverage"] == 1.0
    assert {d["dimension"] for d in first["dimension_scores"]} == {
        "compensation",
        "career_growth",
        "remote_work",
    }
    # The total is exactly the contributions the page shows.
    assert first["total_score"] == pytest.approx(
        sum(first["weighted_contributions"].values())
    )
    assert all(d["label"] for d in first["dimension_scores"])


def test_a_missing_dimension_is_reported_not_zeroed(client, db):
    set_weights(client, compensation=5, career_growth=5)
    a = offer_for(client, db, 400000)
    b = offer_for(client, db, 300000)

    body = compare(client, a["id"], b["id"])
    first = body["offers"][0]

    assert first["missing_dimensions"] == ["career_growth"]
    assert first["coverage"] == pytest.approx(0.5)
    assert body["winner_offer_id"] is None
    assert "覆盖率" in body["winner_blocked_reason"]


def test_with_no_weights_there_is_no_total(client, db):
    a = offer_for(client, db, 400000)
    b = offer_for(client, db, 300000)

    body = compare(client, a["id"], b["id"])
    assert all(o["total_score"] is None for o in body["offers"])
    assert body["winner_offer_id"] is None
    assert body["message"]


def test_comparing_one_offer_is_a_clean_422(client, db):
    offer = offer_for(client, db, 300000)
    response = client.get(f"/api/decision/compare?offer_id={offer['id']}")
    assert response.status_code == 422


def test_the_deadline_is_returned_but_never_scored(client, db):
    set_weights(client, compensation=1)
    soon = (local_now() + timedelta(days=1)).isoformat()
    a = offer_for(client, db, 400000)
    b = offer_for(client, db, 300000, decision_deadline=soon)

    body = compare(client, a["id"], b["id"])
    urgent = next(o for o in body["offers"] if o["offer_id"] == b["id"])
    calm = next(o for o in body["offers"] if o["offer_id"] == a["id"])

    assert urgent["days_to_deadline"] == 1
    assert urgent["deadline_state"] in {"tomorrow", "today", "soon"}
    assert calm["deadline_state"] == "none"
    # The urgent one is still the lower-paid one; urgency changed nothing.
    assert body["winner_offer_id"] == a["id"]


def test_a_failed_deal_breaker_is_shown_not_enforced(client, db):
    set_weights(client, compensation=1)
    client.patch(
        "/api/decision/profile",
        json={"deal_breakers": [{"kind": "minimum_guaranteed_cash", "value": 350000}]},
    )
    a = offer_for(client, db, 400000)
    b = offer_for(client, db, 300000)

    body = compare(client, a["id"], b["id"])
    low = next(o for o in body["offers"] if o["offer_id"] == b["id"])

    assert low["deal_breakers"][0]["result"] == "failed"
    assert low["deal_breakers"][0]["label"]
    assert low["total_score"] is not None  # still compared, still visible


def test_cross_currency_without_a_rate_is_marked_incomparable(client, db):
    set_weights(client, compensation=1)
    cny = offer_for(client, db, 400000)
    job = applied(client, db)
    jpy = create(
        client, job.id, currency="JPY", initial={"base_salary_annual": 8000000}
    )

    body = compare(client, cny["id"], jpy["id"])
    foreign = next(o for o in body["offers"] if o["offer_id"] == jpy["id"])

    assert body["mixed_currency"] is True
    assert foreign["compensation_comparable"] is False
    assert foreign["total_score"] is None
    assert body["winner_offer_id"] is None
    assert any("汇率" in note for note in body["notes"])


def test_cross_currency_with_a_rate_compares(client, db):
    client.patch(
        "/api/decision/profile",
        json={"weights": {"compensation": 1}, "fx_rates": {"JPY": 0.05}},
    )
    cny = offer_for(client, db, 400000)
    job = applied(client, db)
    jpy = create(
        client, job.id, currency="JPY", initial={"base_salary_annual": 10000000}
    )

    body = compare(client, cny["id"], jpy["id"])
    foreign = next(o for o in body["offers"] if o["offer_id"] == jpy["id"])

    assert foreign["fx_rate_used"] == 0.05
    assert foreign["currency"] == "JPY"
    assert body["winner_offer_id"] == jpy["id"]


def test_a_candidate_counter_never_becomes_the_offer(client, db):
    set_weights(client, compensation=1)
    a = offer_for(client, db, 300000)
    b = offer_for(client, db, 320000)
    response = client.post(
        f"/api/offers/{a['id']}/counter", json={"base_salary_annual": 500000}
    )
    assert response.status_code == 200, response.text

    body = compare(client, a["id"], b["id"])
    countered = next(o for o in body["offers"] if o["offer_id"] == a["id"])

    assert countered["guaranteed_cash"] == 300000
    assert body["winner_offer_id"] == b["id"]


# --------------------------------------------------------------------------
# snapshots
# --------------------------------------------------------------------------


def test_a_snapshot_freezes_the_comparison(client, db):
    set_weights(client, compensation=1)
    a = offer_for(client, db, 400000)
    b = offer_for(client, db, 300000)

    created = client.post(
        "/api/decision/snapshots",
        json={"offer_ids": [a["id"], b["id"]], "name": "最终对比"},
    )
    assert created.status_code == 200, created.text
    snapshot = created.json()
    assert snapshot["name"] == "最终对比"
    assert snapshot["winner_offer_id"] == a["id"]
    assert snapshot["payload"]["raw_weights"] == {"compensation": 1}

    # Change everything the score depended on.
    set_weights(client, remote_work=1)
    client.post(
        f"/api/offers/{b['id']}/revisions",
        json={
            "revision_type": "company_revision",
            "source": "company",
            "base_salary_annual": 900000,
        },
    )

    reread = client.get(f"/api/decision/snapshots/{snapshot['id']}").json()
    assert reread["payload"] == snapshot["payload"]
    assert reread["winner_offer_id"] == a["id"]


def test_snapshots_list_and_delete(client, db):
    set_weights(client, compensation=1)
    a = offer_for(client, db, 400000)
    b = offer_for(client, db, 300000)
    snapshot = client.post(
        "/api/decision/snapshots", json={"offer_ids": [a["id"], b["id"]]}
    ).json()

    listed = client.get("/api/decision/snapshots").json()
    assert listed["total"] == 1
    assert listed["items"][0]["companies"]

    assert client.delete(f"/api/decision/snapshots/{snapshot['id']}").status_code == 200
    assert client.get("/api/decision/snapshots").json()["total"] == 0
    assert client.get(f"/api/decision/snapshots/{snapshot['id']}").status_code == 404


def test_a_snapshot_needs_at_least_two_offers(client, db):
    offer = offer_for(client, db, 300000)
    response = client.post(
        "/api/decision/snapshots", json={"offer_ids": [offer["id"]]}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# negotiation and competing offers
# --------------------------------------------------------------------------


def test_the_negotiation_position_shows_the_gap_and_the_targets(client, db):
    offer = offer_for(client, db, 300000)
    client.post(f"/api/offers/{offer['id']}/counter", json={"base_salary_annual": 360000})
    client.put(
        f"/api/decision/offers/{offer['id']}/assessment",
        json={"target_total_cash": 340000, "minimum_total_cash": 280000},
    )

    body = client.get(f"/api/decision/offers/{offer['id']}/negotiation").json()
    assert body["current_company_cash"] == 300000
    assert body["latest_candidate_ask"] == 360000
    assert body["gap"] == 60000
    assert body["target_total_cash"] == 340000
    assert body["warnings"] == []


def test_below_the_minimum_warns_only(client, db):
    offer = offer_for(client, db, 250000)
    client.put(
        f"/api/decision/offers/{offer['id']}/assessment",
        json={"minimum_total_cash": 300000},
    )

    body = client.get(f"/api/decision/offers/{offer['id']}/negotiation").json()
    assert body["warnings"]

    still_open = client.get(f"/api/offers/{offer['id']}").json()
    assert still_open["status"] == "received"


def test_competing_offers_are_listed_before_accepting(client, db):
    keep = offer_for(client, db, 400000)
    other = offer_for(client, db, 300000)

    body = client.get(f"/api/decision/offers/{keep['id']}/competing").json()
    assert [o["offer_id"] for o in body["items"]] == [other["id"]]
    assert "不会自动拒绝" in body["message"]


def test_accepting_leaves_the_competing_offers_alone(client, db):
    keep = offer_for(client, db, 400000)
    other = offer_for(client, db, 300000)

    accepted = client.post(f"/api/offers/{keep['id']}/accept", json={"confirmed": True})
    assert accepted.status_code == 200, accepted.text

    untouched = client.get(f"/api/offers/{other['id']}").json()
    assert untouched["status"] == "received"
    assert untouched["decline_reason"] is None


def test_with_nothing_else_open_the_competing_list_is_empty(client, db):
    offer = offer_for(client, db, 400000)
    body = client.get(f"/api/decision/offers/{offer['id']}/competing").json()
    assert body["items"] == []
    assert body["total"] == 0
