"""Analytics HTTP surface (v0.6).

Routes are thin, so these tests focus on the contract the frontend depends on
and on the one rule that must never bend: reading analytics changes nothing,
and applying a proposal needs an explicit confirmation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.career_strategy import load_strategy, strategy_hash
from app.models import CareerStrategyChange, RecommendationDecision
from app.schemas.analytics import ProposalType
from app.services import strategy_recommendations as recs
from app.services.application_analytics import AnalyticsFilters, compute_analytics

from tests.test_career_analytics import NOW, applied_job as _applied_job_at
from tests.test_strategy_recommendations import strong_hangzhou as _strong_hangzhou_at


def applied_job(db, **kwargs):
    """Build the history relative to the REAL clock, not the frozen `NOW`.

    The unit tests pass `now=NOW` into `compute_analytics`, but these go through
    the HTTP route, which uses the real time. Anchoring the fixtures to a frozen
    date meant they silently drifted out of the default 30-day window as real
    time moved on - the suite began failing on 2026-08-31 with no code change.
    """
    return _applied_job_at(db, now=datetime.now(timezone.utc), **kwargs)


def strong_hangzhou(db):
    """Same cohort, anchored to the real clock - see `applied_job` above."""
    return _strong_hangzhou_at(db, now=datetime.now(timezone.utc))


def city_proposal(db):
    result = compute_analytics(db, AnalyticsFilters(), now=NOW)
    return next(
        p
        for p in recs.build_proposals(result)
        if p.type is ProposalType.increase_city_priority
    )


# --------------------------------------------------------------------------
# GET /api/analytics/career
# --------------------------------------------------------------------------


def test_career_analytics_works_on_an_empty_database(client):
    response = client.get("/api/analytics/career")
    assert response.status_code == 200

    body = response.json()
    assert body["summary"]["applications"] == 0
    assert body["summary"]["mature_reply_rate"]["rate"] is None
    assert body["observations"][0]["kind"] == "insufficient_data"


def test_career_analytics_reports_the_funnel(client, db):
    for _ in range(6):
        applied_job(db, days_ago=20, city="北京", replied_after_h=6)

    body = client.get("/api/analytics/career").json()
    assert body["summary"]["applications"] == 6
    assert body["summary"]["replies"] == 6
    assert body["by_city"][0]["key"] == "北京"
    assert body["by_city"][0]["mature_reply_rate"]["denominator"] == 6


def test_window_and_filters_are_query_parameters(client, db, monkeypatch):
    # This fixture's events are relative to NOW, not the machine's current day.
    monkeypatch.setattr('app.api.routes.analytics.analytics.compute_analytics',
                        lambda session, filters: compute_analytics(session, filters, now=NOW))
    applied_job(db, days_ago=3, city="北京")
    applied_job(db, days_ago=60, city="上海")

    assert client.get("/api/analytics/career?window=7d").json()["summary"]["applications"] == 1
    body = client.get("/api/analytics/career?window=all&city=上海").json()
    assert body["summary"]["applications"] == 1
    assert body["filters"]["city"] == "上海"


def test_an_unknown_window_is_rejected(client):
    assert client.get("/api/analytics/career?window=yesterday").status_code == 422


def test_the_response_states_its_maturity_assumptions(client):
    body = client.get("/api/analytics/career").json()
    assert body["response_maturity_days"] == 7
    assert body["interview_maturity_days"] == 14
    assert body["timezone"] == "Asia/Tokyo"


# --------------------------------------------------------------------------
# GET /api/analytics/career/dashboard
# --------------------------------------------------------------------------


def test_the_dashboard_card_stays_silent_without_evidence(client, db):
    for _ in range(2):
        applied_job(db, days_ago=20, city="北京", replied_after_h=6)

    body = client.get("/api/analytics/career/dashboard").json()
    assert body["has_signal"] is False
    assert body["best_direction"] is None
    assert body["applications"] == 2


def test_the_dashboard_card_speaks_once_a_cohort_matures(client, db):
    for _ in range(6):
        applied_job(db, days_ago=20, city="北京", title="SRE 工程师", replied_after_h=6)

    body = client.get("/api/analytics/career/dashboard").json()
    assert body["has_signal"] is True
    assert body["best_direction"] == "北京 · SRE"
    assert body["mature_reply_rate"]["denominator"] == 6


# --------------------------------------------------------------------------
# GET /api/analytics/strategy-recommendations
# --------------------------------------------------------------------------


def test_recommendations_are_empty_and_say_why(client, db):
    for _ in range(2):
        applied_job(db, days_ago=20, city="杭州", replied_after_h=6)

    body = client.get("/api/analytics/strategy-recommendations").json()
    assert body["proposals"] == []
    assert "还不足以" in body["message"]


def test_recommendations_carry_evidence_and_a_confirmation_notice(client, db):
    strong_hangzhou(db)

    body = client.get("/api/analytics/strategy-recommendations").json()
    proposal = next(p for p in body["proposals"] if p["type"] == "increase_city_priority")
    assert proposal["sample_size"] == 12
    assert proposal["evidence"]["denominator"] == 12
    assert proposal["applicable"] is True
    assert "需要你确认后才会生效" in body["message"]


def test_reading_recommendations_does_not_change_the_strategy(client, db):
    strong_hangzhou(db)
    before = strategy_hash(load_strategy())

    client.get("/api/analytics/strategy-recommendations")

    assert strategy_hash(load_strategy(force=True)) == before
    assert db.query(CareerStrategyChange).count() == 0


# --------------------------------------------------------------------------
# preview / apply / dismiss
# --------------------------------------------------------------------------


def test_preview_shows_the_diff_without_writing(client, db):
    strong_hangzhou(db)
    proposal = city_proposal(db)
    before = load_strategy()["target_cities"]

    body = client.post(
        f"/api/analytics/strategy-recommendations/{proposal.signature}/preview"
    ).json()

    assert body["applied"] is False
    assert body["diff"][0]["before"] == before
    assert body["diff"][0]["after"][0] == "杭州"
    assert load_strategy(force=True)["target_cities"] == before


def test_applying_without_confirmation_is_refused(client, db):
    strong_hangzhou(db)
    proposal = city_proposal(db)
    before = load_strategy()["target_cities"]

    response = client.post(
        f"/api/analytics/strategy-recommendations/{proposal.signature}/apply",
        json={"confirmed": False},
    )

    assert response.status_code == 422
    assert load_strategy(force=True)["target_cities"] == before, "nothing was written"


def test_applying_with_an_empty_body_is_refused(client, db):
    strong_hangzhou(db)
    proposal = city_proposal(db)

    response = client.post(
        f"/api/analytics/strategy-recommendations/{proposal.signature}/apply"
    )
    assert response.status_code == 422


def test_a_confirmed_apply_writes_the_strategy(client, db):
    strong_hangzhou(db)
    proposal = city_proposal(db)

    body = client.post(
        f"/api/analytics/strategy-recommendations/{proposal.signature}/apply",
        json={"confirmed": True, "note": "先试一个月"},
    ).json()

    assert body["applied"] is True
    assert body["strategy"]["target_cities"][0] == "杭州"
    assert load_strategy(force=True)["target_cities"][0] == "杭州"

    change = db.query(CareerStrategyChange).one()
    assert change.recommendation_signature == proposal.signature
    assert change.notes == "先试一个月"


def test_an_unknown_signature_is_a_clean_404(client, db):
    strong_hangzhou(db)
    response = client.post("/api/analytics/strategy-recommendations/deadbeef/apply", json={"confirmed": True})
    assert response.status_code == 404
    assert "刷新" in response.json()["message"]


def test_dismissing_hides_the_proposal_next_time(client, db):
    strong_hangzhou(db)
    proposal = city_proposal(db)

    dismissed = client.post(
        f"/api/analytics/strategy-recommendations/{proposal.signature}/dismiss"
    ).json()
    assert dismissed["decision"] == "dismissed"

    body = client.get("/api/analytics/strategy-recommendations").json()
    assert proposal.signature not in {p["signature"] for p in body["proposals"]}
    assert body["suppressed"] >= 1
    assert load_strategy(force=True)["target_cities"][0] != "杭州"


def test_dismissal_is_idempotent(client, db):
    strong_hangzhou(db)
    proposal = city_proposal(db)
    path = f"/api/analytics/strategy-recommendations/{proposal.signature}/dismiss"

    assert client.post(path).status_code == 200
    assert client.post(path).status_code == 200
    assert db.query(recs.StrategyRecommendationDecision).count() == 1


def test_an_accepted_proposal_is_still_visible_with_its_decision(client, db):
    strong_hangzhou(db)
    proposal = city_proposal(db)
    recs.record_decision(db, proposal.signature, RecommendationDecision.accepted)

    body = client.get("/api/analytics/strategy-recommendations").json()
    match = next(
        (p for p in body["proposals"] if p["signature"] == proposal.signature), None
    )
    assert match is not None and match["decision"] == "accepted"


# --------------------------------------------------------------------------
# spend
# --------------------------------------------------------------------------


def test_the_analytics_endpoints_make_no_openai_call(client, db, monkeypatch):
    """v0.6 is deterministic: any agent invocation here is a bug."""
    import app.services.job_matcher as job_matcher

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("analytics must never call the model")

    monkeypatch.setattr(job_matcher, "run_job_match", _explode)
    strong_hangzhou(db)

    assert client.get("/api/analytics/career").status_code == 200
    assert client.get("/api/analytics/career/dashboard").status_code == 200
    assert client.get("/api/analytics/strategy-recommendations").status_code == 200
