"""Offer HTTP surface (v0.9).

Covers the board, comparison, deadline semantics and the confirmation gates.
Nothing here calls OpenAI.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import JobStatus
from app.services.timezones import local_now

from tests.test_career_analytics import make_job
from tests.test_resume_variants import apply_with, make_resume


def applied(client, db, resume=None, **job_kwargs):
    job = make_job(db, status=JobStatus.new, **job_kwargs)
    apply_with(db, job, resume)
    db.refresh(job)
    return job


def create(client, job_id, **payload) -> dict:
    body = {"confirmed": True, **payload}
    response = client.post(f"/api/jobs/{job_id}/offers", json=body)
    assert response.status_code == 200, response.text
    return response.json()["offer"]


# --------------------------------------------------------------------------
# creation and revisions
# --------------------------------------------------------------------------


def test_creating_an_offer_over_http(client, db):
    resume = make_resume(db, variant_name="DevOps版")
    job = applied(client, db, resume)

    offer = create(
        client,
        job.id,
        currency="CNY",
        initial={"base_salary_annual": 300000, "bonus_target": 60000},
    )

    assert offer["status"] == "received"
    assert offer["resume_label"] == "DevOps版"
    assert offer["current_company_summary"]["first_year_guaranteed_cash"] == 300000
    assert offer["current_company_summary"]["first_year_target_cash"] == 360000


def test_creating_an_offer_requires_confirmation(client, db):
    job = applied(client, db)
    response = client.post(f"/api/jobs/{job.id}/offers", json={"confirmed": False})
    assert response.status_code == 422


def test_a_duplicate_offer_is_refused(client, db):
    job = applied(client, db)
    create(client, job.id)
    response = client.post(f"/api/jobs/{job.id}/offers", json={"confirmed": True})
    assert response.status_code == 422
    assert "已经记录过 Offer" in response.json()["message"]


def test_a_counter_is_recorded_but_not_sent(client, db):
    job = applied(client, db)
    offer = create(client, job.id, initial={"base_salary_annual": 300000})

    body = client.post(
        f"/api/offers/{offer['id']}/counter",
        json={"base_salary_annual": 350000, "other_request": "希望远程"},
    ).json()

    assert "不会替你发送任何消息" in body["message"]
    assert body["offer"]["latest_counter_summary"]["base_annual"] == 350000
    assert body["offer"]["current_company_summary"]["base_annual"] == 300000, (
        "the company's offer is unchanged by your asking"
    )


def test_the_negotiation_timeline_is_exposed_in_order(client, db):
    job = applied(client, db)
    offer = create(client, job.id, initial={"base_salary_annual": 300000})
    oid = offer["id"]

    client.post(f"/api/offers/{oid}/counter", json={"base_salary_annual": 350000})
    client.post(
        f"/api/offers/{oid}/revisions",
        json={
            "revision_type": "company_revision",
            "source": "company",
            "base_salary_annual": 330000,
            "signing_bonus": 20000,
        },
    )
    body = client.get(f"/api/offers/{oid}").json()

    assert [r["revision_type"] for r in body["revisions"]] == [
        "initial",
        "candidate_counter",
        "company_revision",
    ]
    assert [r["is_company_offer"] for r in body["revisions"]] == [True, False, True]
    assert body["revisions"][1]["revision_type_label"] == "我方诉求"
    assert body["negotiation"]["base"]["absolute"] == 30000
    assert body["negotiation"]["has_negotiation_sequence"] is True


def test_a_candidate_source_cannot_claim_to_be_a_company_revision(client, db):
    job = applied(client, db)
    offer = create(client, job.id)
    response = client.post(
        f"/api/offers/{offer['id']}/revisions",
        json={"revision_type": "company_revision", "source": "candidate"},
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------


def test_accepting_requires_confirmation(client, db):
    job = applied(client, db)
    offer = create(client, job.id, initial={"base_salary_annual": 300000})
    response = client.post(
        f"/api/offers/{offer['id']}/accept", json={"confirmed": False}
    )
    assert response.status_code == 422


def test_accepting_freezes_the_revision(client, db):
    job = applied(client, db)
    offer = create(client, job.id, initial={"base_salary_annual": 300000})
    oid = offer["id"]
    client.post(
        f"/api/offers/{oid}/revisions",
        json={
            "revision_type": "final",
            "source": "company",
            "base_salary_annual": 340000,
        },
    )

    body = client.post(f"/api/offers/{oid}/accept", json={"confirmed": True}).json()

    assert body["offer"]["status"] == "accepted"
    assert body["offer"]["accepted_summary"]["base_annual"] == 340000
    assert body["offer"]["accepted_revision_id"] is not None


def test_you_cannot_accept_your_own_counter_over_http(client, db):
    job = applied(client, db)
    offer = create(client, job.id, initial={"base_salary_annual": 300000})
    oid = offer["id"]
    counter = client.post(
        f"/api/offers/{oid}/counter", json={"base_salary_annual": 350000}
    ).json()
    counter_id = counter["offer"]["latest_counter_revision_id"]

    response = client.post(
        f"/api/offers/{oid}/accept", json={"confirmed": True, "revision_id": counter_id}
    )
    assert response.status_code == 422
    assert "我方诉求" in response.json()["message"]


def test_declining_records_a_reason(client, db):
    job = applied(client, db)
    offer = create(client, job.id, initial={"base_salary_annual": 300000})

    body = client.post(
        f"/api/offers/{offer['id']}/decline",
        json={"confirmed": True, "reason": "salary"},
    ).json()

    assert body["offer"]["status"] == "declined"
    assert body["offer"]["decline_reason_label"] == "薪资"

    db.expire_all()
    job = db.get(type(job), job.id)
    assert job.status is not JobStatus.rejected


def test_expiring_is_explicit(client, db):
    job = applied(client, db)
    offer = create(client, job.id)
    body = client.post(
        f"/api/offers/{offer['id']}/expire", json={"confirmed": True}
    ).json()
    assert body["offer"]["status"] == "expired"


# --------------------------------------------------------------------------
# deadlines
# --------------------------------------------------------------------------


def test_a_deadline_today_is_labelled_today(client, db):
    job = applied(client, db)
    later_today = local_now().replace(hour=23, minute=0)
    offer = create(client, job.id, decision_deadline=later_today.isoformat())

    assert offer["deadline_state"] == "today"
    assert offer["days_to_deadline"] == 0


def test_a_deadline_tomorrow_is_labelled_tomorrow(client, db):
    job = applied(client, db)
    offer = create(
        client, job.id, decision_deadline=(local_now() + timedelta(days=1)).isoformat()
    )
    assert offer["deadline_state"] == "tomorrow"


def test_a_deadline_within_three_days_is_soon(client, db):
    job = applied(client, db)
    offer = create(
        client, job.id, decision_deadline=(local_now() + timedelta(days=3)).isoformat()
    )
    assert offer["deadline_state"] == "soon"


def test_a_far_deadline_is_later(client, db):
    job = applied(client, db)
    offer = create(
        client, job.id, decision_deadline=(local_now() + timedelta(days=20)).isoformat()
    )
    assert offer["deadline_state"] == "later"


def test_a_passed_deadline_is_displayed_but_never_auto_expires(client, db):
    job = applied(client, db)
    offer = create(
        client, job.id, decision_deadline=(local_now() - timedelta(days=4)).isoformat()
    )
    assert offer["deadline_state"] == "past"
    assert offer["status"] == "received", "the offer is not marked expired on its own"


def test_no_deadline_is_no_state(client, db):
    job = applied(client, db)
    offer = create(client, job.id)
    assert offer["deadline_state"] == "none"
    assert offer["days_to_deadline"] is None


# --------------------------------------------------------------------------
# the board
# --------------------------------------------------------------------------


def test_the_board_is_empty_on_a_fresh_database(client):
    body = client.get("/api/offers").json()
    assert body["pending"] == []
    assert body["timezone"] == "Asia/Tokyo"


def test_the_board_buckets_offers_by_state(client, db):
    pending_job = applied(client, db)
    create(client, pending_job.id, initial={"base_salary_annual": 300000})

    negotiating_job = applied(client, db)
    negotiating = create(client, negotiating_job.id, initial={"base_salary_annual": 300000})
    client.post(
        f"/api/offers/{negotiating['id']}/counter", json={"base_salary_annual": 350000}
    )

    accepted_job = applied(client, db)
    accepted = create(client, accepted_job.id, initial={"base_salary_annual": 300000})
    client.post(f"/api/offers/{accepted['id']}/accept", json={"confirmed": True})

    declined_job = applied(client, db)
    declined = create(client, declined_job.id)
    client.post(
        f"/api/offers/{declined['id']}/decline",
        json={"confirmed": True, "reason": "location"},
    )

    body = client.get("/api/offers").json()
    assert len(body["pending"]) == 1
    assert len(body["negotiating"]) == 1
    assert len(body["accepted"]) == 1
    assert len(body["closed"]) == 1


def test_the_board_sorts_the_soonest_deadline_first(client, db):
    far = applied(client, db)
    create(client, far.id, decision_deadline=(local_now() + timedelta(days=10)).isoformat())
    soon = applied(client, db)
    soon_offer = create(
        client, soon.id, decision_deadline=(local_now() + timedelta(days=1)).isoformat()
    )
    no_deadline = applied(client, db)
    create(client, no_deadline.id)

    pending = client.get("/api/offers").json()["pending"]
    assert pending[0]["id"] == soon_offer["id"]
    assert pending[-1]["deadline_state"] == "none", "no deadline is not most urgent"


def test_legacy_offer_events_appear_unconverted(client, db):
    from app.models import EventType
    from tests.test_career_analytics import NOW, add_event

    job = applied(client, db)
    add_event(db, job, EventType.offer, at=NOW)
    db.refresh(job)

    body = client.get("/api/offers").json()
    assert len(body["legacy_offer_events"]) == 1
    assert body["legacy_offer_events"][0]["job_id"] == job.id


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------


def test_comparing_two_offers(client, db):
    a = applied(client, db, city="杭州")
    offer_a = create(
        client,
        a.id,
        initial={"base_salary_annual": 300000, "signing_bonus": 20000},
        remote_policy="hybrid",
    )
    b = applied(client, db, city="北京")
    offer_b = create(
        client, b.id, initial={"base_salary_annual": 340000}, remote_policy="onsite"
    )

    body = client.get(
        f"/api/offers/compare?offer_id={offer_a['id']}&offer_id={offer_b['id']}"
    ).json()

    assert len(body["rows"]) == 2
    assert body["mixed_currency"] is False
    assert "不会给出综合评分" in body["message"]
    by_id = {r["offer_id"]: r for r in body["rows"]}
    assert by_id[offer_a["id"]]["first_year_guaranteed_cash"] == 320000
    assert by_id[offer_b["id"]]["first_year_guaranteed_cash"] == 340000


def test_comparing_across_currencies_says_it_does_not_convert(client, db):
    a = applied(client, db)
    offer_a = create(client, a.id, currency="CNY", initial={"base_salary_annual": 400000})
    b = applied(client, db)
    offer_b = create(client, b.id, currency="JPY", initial={"base_salary_annual": 8000000})

    body = client.get(
        f"/api/offers/compare?offer_id={offer_a['id']}&offer_id={offer_b['id']}"
    ).json()

    assert body["mixed_currency"] is True
    assert set(body["currencies"]) == {"CNY", "JPY"}
    assert any("不做换算或排名" in note for note in body["notes"])
    # Both native values survive - neither is converted.
    values = {r["currency"]: r["base_annual"] for r in body["rows"]}
    assert values == {"CNY": 400000, "JPY": 8000000}


def test_comparison_names_no_winner(client, db):
    a = applied(client, db)
    offer_a = create(client, a.id, initial={"base_salary_annual": 300000})
    b = applied(client, db)
    offer_b = create(client, b.id, initial={"base_salary_annual": 400000})

    body = client.get(
        f"/api/offers/compare?offer_id={offer_a['id']}&offer_id={offer_b['id']}"
    ).json()
    assert "winner" not in body
    assert "score" not in body
    for row in body["rows"]:
        assert "score" not in row


def test_comparison_needs_at_least_two_offers(client, db):
    job = applied(client, db)
    offer = create(client, job.id)
    response = client.get(f"/api/offers/compare?offer_id={offer['id']}")
    assert response.status_code == 422


def test_comparison_caps_the_selection(client, db):
    ids = []
    for _ in range(5):
        job = applied(client, db)
        ids.append(create(client, job.id)["id"])

    query = "&".join(f"offer_id={i}" for i in ids)
    response = client.get(f"/api/offers/compare?{query}")
    assert response.status_code == 422


def test_unvaluable_equity_is_flagged_in_the_comparison(client, db):
    a = applied(client, db)
    offer_a = create(
        client,
        a.id,
        initial={"base_salary_annual": 300000, "stock_value": 400000},
    )
    b = applied(client, db)
    offer_b = create(client, b.id, initial={"base_salary_annual": 340000})

    body = client.get(
        f"/api/offers/compare?offer_id={offer_a['id']}&offer_id={offer_b['id']}"
    ).json()
    row = next(r for r in body["rows"] if r["offer_id"] == offer_a["id"])
    assert row["equity_excluded"] is True
    assert any("未计入可比较总包" in note for note in body["notes"])


# --------------------------------------------------------------------------
# text parsing
# --------------------------------------------------------------------------


def test_parsing_offer_text_saves_nothing(client, db):
    body = client.post(
        "/api/offers/parse-text", json={"text": "年薪600万日元，另有绩效奖金"}
    ).json()

    assert body["base_salary_annual"] == 6000000
    assert body["currency"] == "JPY"
    assert body["confidence"] == "high"
    assert "你填写的数值始终优先" in body["message"]
    # Nothing was persisted.
    assert client.get("/api/offers").json()["pending"] == []


def test_parsing_reports_when_it_cannot_tell(client):
    body = client.post("/api/offers/parse-text", json={"text": "面议，另有股票"}).json()
    assert body["base_salary_annual"] is None
    assert body["confidence"] == "none"
    assert "请手动填写" in body["message"]


def test_a_user_correction_wins_over_the_parsed_value(client, db):
    """Parsed 600万; the user says 620万. The user wins, wording preserved."""
    text = "年薪600万日元"
    parsed = client.post("/api/offers/parse-text", json={"text": text}).json()
    assert parsed["base_salary_annual"] == 6000000

    job = applied(client, db)
    offer = create(
        client,
        job.id,
        currency="JPY",
        initial={"base_salary_annual": 6200000, "salary_text_original": text},
    )

    assert offer["current_company_summary"]["base_annual"] == 6200000
    assert offer["revisions"][0]["salary_text_original"] == text


# --------------------------------------------------------------------------
# analytics endpoint
# --------------------------------------------------------------------------


def test_the_analytics_endpoint_works_on_an_empty_database(client):
    body = client.get("/api/analytics/offers").json()
    assert body["funnel"]["applications"] == 0
    assert body["funnel"]["application_to_offer"]["rate"] is None


def test_the_analytics_endpoint_reports_the_funnel(client, db):
    job = applied(client, db)
    create(client, job.id, initial={"base_salary_annual": 300000})

    body = client.get("/api/analytics/offers").json()
    assert body["funnel"]["offers"] == 1
    assert body["compensation"][0]["currency"] == "CNY"


def test_the_analytics_endpoints_make_no_openai_call(client, db, monkeypatch):
    import app.services.job_matcher as job_matcher

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("offer analytics must never call the model")

    monkeypatch.setattr(job_matcher, "run_job_match", _explode)

    job = applied(client, db)
    create(client, job.id, initial={"base_salary_annual": 300000})

    assert client.get("/api/analytics/offers").status_code == 200
    assert client.get("/api/offers").status_code == 200
    assert client.post("/api/offers/parse-text", json={"text": "年薪30万"}).status_code == 200
