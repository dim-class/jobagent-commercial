"""Funnel metrics, role-family classification, and the local-day boundary.

Everything here is deterministic arithmetic over rows the human created - no
LLM is involved, by design.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import ApplicationEvent, EventType, JobAnalysis, Verdict
from app.services import application_metrics
from app.services.application_metrics import role_family, source_bucket
from app.services.timezones import is_local_today, to_local
from tests.conftest import make_job_payload
from tests.test_application_queue import analysis_payload


@pytest.fixture
def make_analyzed(client, db, active_resume):
    counter = {"n": 0}

    def _make(score: int = 85, verdict: str = "apply", **job_overrides) -> int:
        counter["n"] += 1
        n = counter["n"]
        payload = make_job_payload(company=job_overrides.pop("company", f"公司{n}"), **job_overrides)
        payload["raw_description"] += f"\n\n编号 {n}"
        job_id = client.post("/api/jobs", json=payload).json()["job"]["id"]
        db.add(
            JobAnalysis(
                job_id=job_id,
                resume_id=active_resume.id,
                model="test-model-fast",
                prompt_version="v1",
                cache_key=f"m-cache-{n}",
                overall_score=score,
                verdict=Verdict(verdict),
                result_json=analysis_payload(score, verdict),
            )
        )
        db.commit()
        return job_id

    return _make


def metrics(client) -> dict:
    return client.get("/api/application-queue/metrics").json()


# --------------------------------------------------------------------------
# role family
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("SRE 工程师", "SRE"),
        ("Site Reliability Engineer", "SRE"),
        ("站点可靠性工程师", "SRE"),
        ("DevOps工程师", "DevOps"),
        ("运维开发工程师", "DevOps"),
        ("Platform Engineer", "Platform"),
        ("云平台工程师", "Platform"),
        ("云计算工程师", "Cloud"),
        ("Cloud Engineer", "Cloud"),
        ("基础架构工程师", "Infrastructure"),
        ("系统运维工程师", "Infrastructure"),
        ("Java 后端开发工程师", "Other"),
        ("产品经理", "Other"),
        ("", "Other"),
    ],
)
def test_role_family_classification(title, expected):
    assert role_family(title) == expected


def test_role_family_is_deterministic():
    for _ in range(3):
        assert role_family("云原生工程师") == "Cloud"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("manual", "manual"),
        ("boss", "boss"),
        ("liepin", "liepin"),
        ("zhaopin", "zhaopin"),
        ("job51", "job51"),
        ("demo", "demo"),
        ("something-else", "other"),
        (None, "other"),
    ],
)
def test_source_bucket(source, expected):
    assert source_bucket(source) == expected


# --------------------------------------------------------------------------
# funnel counts
# --------------------------------------------------------------------------


def test_empty_funnel_has_no_rates(client):
    body = metrics(client)
    assert body["counts"]["total_jobs"] == 0
    assert body["rates"]["application_response_rate"] is None, "no denominator, no rate"


def test_funnel_counts_progress(client, make_analyzed):
    applied = make_analyzed(90, "apply")
    replied = make_analyzed(88, "apply")
    interviewed = make_analyzed(86, "apply")
    make_analyzed(41, "skip")

    client.post(f"/api/jobs/{applied}/mark-applied", json={"confirmed": True})
    client.post(f"/api/jobs/{replied}/mark-applied", json={"confirmed": True})
    client.post(f"/api/jobs/{replied}/reply", json={"response_type": "positive"})
    client.post(f"/api/jobs/{interviewed}/mark-applied", json={"confirmed": True})
    client.post(f"/api/jobs/{interviewed}/interview", json={"round": "一面"})

    counts = metrics(client)["counts"]
    assert counts["total_jobs"] == 4
    assert counts["analyzed_jobs"] == 4
    assert counts["recommended_jobs"] == 3, "the skip verdict is not recommended"
    assert counts["applied_jobs"] == 3, "further stages still count as applied"
    assert counts["replied_jobs"] == 2, "an interview implies a response"
    assert counts["interview_jobs"] == 1


def test_offer_counts_all_the_way_down_the_funnel(client, make_analyzed):
    job_id = make_analyzed(92, "strong_apply")
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    client.post(f"/api/jobs/{job_id}/interview", json={})
    client.post(f"/api/jobs/{job_id}/offer", json={"salary_text": "40K"})

    counts = metrics(client)["counts"]
    assert counts["applied_jobs"] == 1
    assert counts["replied_jobs"] == 1
    assert counts["interview_jobs"] == 1
    assert counts["offer_jobs"] == 1


def test_rejected_still_counts_as_applied(client, make_analyzed):
    job_id = make_analyzed(80, "apply")
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    client.post(f"/api/jobs/{job_id}/reject", json={"reason": "经验不足"})

    counts = metrics(client)["counts"]
    assert counts["applied_jobs"] == 1
    assert counts["rejected_jobs"] == 1
    assert counts["replied_jobs"] == 0


# --------------------------------------------------------------------------
# rates
# --------------------------------------------------------------------------


def test_response_and_interview_rates(client, make_analyzed):
    ids = [make_analyzed(85, "apply") for _ in range(4)]
    for job_id in ids:
        client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})

    client.post(f"/api/jobs/{ids[0]}/reply", json={"response_type": "positive"})
    client.post(f"/api/jobs/{ids[1]}/reply", json={"response_type": "neutral"})
    client.post(f"/api/jobs/{ids[1]}/interview", json={"round": "一面"})

    rates = metrics(client)["rates"]
    assert rates["application_response_rate"] == 0.5   # 2 replied / 4 applied
    assert rates["application_interview_rate"] == 0.25  # 1 interview / 4 applied
    assert rates["response_interview_rate"] == 0.5     # 1 interview / 2 replied


def test_rates_are_none_not_zero_without_a_denominator(client, make_analyzed):
    make_analyzed(85, "apply")  # analyzed but never applied to
    rates = metrics(client)["rates"]
    assert rates["application_response_rate"] is None
    assert rates["application_interview_rate"] is None
    assert rates["response_interview_rate"] is None


# --------------------------------------------------------------------------
# breakdowns
# --------------------------------------------------------------------------


def test_breakdown_by_city_role_family_and_source(client, make_analyzed):
    beijing = make_analyzed(90, "apply", city="北京", title="SRE 工程师")
    make_analyzed(88, "apply", city="上海", title="DevOps工程师")
    client.post(f"/api/jobs/{beijing}/mark-applied", json={"confirmed": True})

    body = metrics(client)
    assert body["by_city"]["北京"]["applied"] == 1
    assert body["by_city"]["上海"]["applied"] == 0
    assert body["by_role_family"]["SRE"]["jobs"] == 1
    assert body["by_role_family"]["DevOps"]["jobs"] == 1
    assert body["by_source"]["manual"]["jobs"] == 2


# --------------------------------------------------------------------------
# local-day boundary (Asia/Tokyo)
# --------------------------------------------------------------------------


def test_local_day_boundary_is_not_naive_utc():
    """16:30Z on the 20th is already the 21st in Tokyo."""
    late_utc = datetime(2026, 8, 20, 16, 30, tzinfo=timezone.utc)
    assert to_local(late_utc).date().isoformat() == "2026-08-21"

    early_utc = datetime(2026, 8, 20, 14, 30, tzinfo=timezone.utc)
    assert to_local(early_utc).date().isoformat() == "2026-08-20"


def test_naive_timestamps_are_treated_as_utc():
    naive = datetime(2026, 8, 20, 16, 30)
    assert to_local(naive).date().isoformat() == "2026-08-21"


def test_yesterdays_event_is_not_counted_today(client, make_analyzed, db):
    job_id = make_analyzed(88, "apply")
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})

    db.expire_all()
    event = (
        db.query(ApplicationEvent)
        .filter(ApplicationEvent.event_type == EventType.applied)
        .one()
    )
    event.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    db.commit()

    assert is_local_today(event.created_at) is False
    summary = client.get("/api/application-queue").json()["summary"]
    assert summary["applied_today"] == 0


def test_todays_event_is_counted_today(client, make_analyzed):
    job_id = make_analyzed(88, "apply")
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    assert client.get("/api/application-queue").json()["summary"]["applied_today"] == 1


# --------------------------------------------------------------------------
# dashboard integration
# --------------------------------------------------------------------------


def test_dashboard_exposes_the_funnel(client, make_analyzed):
    job_id = make_analyzed(90, "apply")
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    client.post(f"/api/jobs/{job_id}/reply", json={"response_type": "positive"})

    body = client.get("/api/dashboard/summary").json()
    assert body["funnel"]["applied_jobs"] == 1
    assert body["funnel"]["replied_jobs"] == 1
    assert body["rates"]["application_response_rate"] == 1.0
    assert body["daily_target"] == 10
    assert body["applied_today"] == 1


def test_metrics_service_is_importable_standalone(db):
    """v0.6 analytics will reuse this - it must not depend on the HTTP layer."""
    result = application_metrics.compute_metrics(db)
    assert result.counts.total_jobs == 0
    assert result.rates.application_response_rate is None
