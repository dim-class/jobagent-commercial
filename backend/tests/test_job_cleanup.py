"""Deleting low-scoring jobs - and never deleting a decision."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.models import ApplicationEvent, EventType, Job, JobAnalysis, JobStatus, Verdict
from app.services import job_cleanup
from tests.conftest import make_job_payload


def analysis_payload(score: int, verdict: str) -> dict:
    return {
        "overall_score": score,
        "verdict": verdict,
        "role_fit_score": score,
        "skill_fit_score": score,
        "experience_fit_score": score,
        "location_fit_score": 100,
        "salary_fit_score": 70,
        "matched_skills": ["AWS", "Terraform", "Kubernetes", "Python"],
        "missing_skills": ["Go"],
        "strengths": ["云运维经验匹配"],
        "gaps": [],
        "risk_flags": [],
        "experience_gap": "无明显差距",
        "role_summary": "云平台运维",
        "reasoning_summary": f"{verdict} 理由摘要。",
        "greeting_message": "您好，我有云运维经验，期待沟通。",
    }


@pytest.fixture
def make_analyzed(client, db, active_resume):
    """Create a job with a stored analysis, without calling any model."""
    counter = {"n": 0}

    def _make(score: int = 85, verdict: str = "apply", **job_overrides) -> int:
        counter["n"] += 1
        n = counter["n"]
        payload = make_job_payload(
            company=job_overrides.pop("company", f"公司{n}"),
            **job_overrides,
        )
        payload["raw_description"] = payload["raw_description"] + f"\n\n编号 {n}"
        job_id = client.post("/api/jobs", json=payload).json()["job"]["id"]

        db.add(
            JobAnalysis(
                job_id=job_id,
                resume_id=active_resume.id,
                model="test-model-fast",
                prompt_version="v1",
                cache_key=f"cache-{n}",
                overall_score=score,
                verdict=Verdict(verdict),
                result_json=analysis_payload(score, verdict),
            )
        )
        db.commit()
        return job_id

    return _make




def _plan(client, threshold: int = 40) -> dict:
    return client.get("/api/jobs/cleanup/plan", params={"threshold": threshold}).json()


def test_a_low_scoring_job_nobody_touched_is_deletable(client, make_analyzed):
    job_id = make_analyzed(score=20, verdict="skip")
    plan = _plan(client)
    assert plan["deletable"] == 1
    assert plan["protected"] == 0

    reply = client.post("/api/jobs/cleanup", json={
        "threshold": 40, "expected_count": 1, "confirmed": True})
    assert reply.status_code == 200
    assert reply.json()["detail"]["deleted"] == 1
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_a_job_above_the_threshold_is_never_touched(client, make_analyzed):
    keep = make_analyzed(score=40, verdict="apply")
    assert _plan(client)["deletable"] == 0
    client.post("/api/jobs/cleanup", json={
        "threshold": 40, "expected_count": 0, "confirmed": True})
    assert client.get(f"/api/jobs/{keep}").status_code == 200


def test_a_job_the_human_decided_on_is_protected_however_low_it_scored(
    client, db, make_analyzed
):
    """`ApplicationEvent` is append-only and the analytics are built on it.
    Deleting a job that was applied to would quietly rewrite the funnel."""
    job_id = make_analyzed(score=10, verdict="skip")
    db.add(ApplicationEvent(job_id=job_id, event_type=EventType.applied))
    db.commit()

    plan = _plan(client)
    assert plan["deletable"] == 0
    assert plan["protected"] == 1

    client.post("/api/jobs/cleanup", json={
        "threshold": 40, "expected_count": 0, "confirmed": True})
    assert client.get(f"/api/jobs/{job_id}").status_code == 200


@pytest.mark.parametrize("event", [
    EventType.skipped, EventType.replied, EventType.later, EventType.status_reset,
    EventType.candidate_reply, EventType.application_result_unknown,
])
def test_every_kind_of_human_decision_protects(client, db, make_analyzed, event):
    job_id = make_analyzed(score=5, verdict="skip")
    db.add(ApplicationEvent(job_id=job_id, event_type=event))
    db.commit()
    assert _plan(client)["protected"] == 1


@pytest.mark.parametrize("event", [
    EventType.viewed, EventType.analyzed, EventType.saved, EventType.note,
    EventType.greeting_copied,
])
def test_machine_bookkeeping_does_not_protect(client, db, make_analyzed, event):
    """Otherwise nothing would ever be deletable: intake writes `note` and the
    analysis writes `analyzed` on every job there is."""
    job_id = make_analyzed(score=5, verdict="skip")
    db.add(ApplicationEvent(job_id=job_id, event_type=event))
    db.commit()
    assert _plan(client)["deletable"] == 1
    assert _plan(client)["protected"] == 0


def test_an_unanalyzed_job_is_never_deleted(client, db, job_payload):
    """No score means nothing to judge it by. It is reported separately and
    left alone - being new is not a reason to be thrown away."""
    client.post("/api/jobs", json=job_payload)
    plan = _plan(client)
    assert plan["unscored"] == 1
    assert plan["deletable"] == 0


def test_without_a_confirmation_nothing_is_deleted(client, make_analyzed):
    job_id = make_analyzed(score=10, verdict="skip")
    reply = client.post("/api/jobs/cleanup", json={
        "threshold": 40, "expected_count": 1, "confirmed": False})
    assert reply.status_code == 422
    assert client.get(f"/api/jobs/{job_id}").status_code == 200


def test_a_count_that_moved_cancels_instead_of_deleting_a_different_set(
    client, make_analyzed
):
    """The same rule `applied_backfill` uses: a selection that changed between
    reading the dialog and pressing the button must not be acted on."""
    first = make_analyzed(score=10, verdict="skip")
    second = make_analyzed(score=12, verdict="skip")
    # The dialog was read when there was one job; there are two now.
    reply = client.post("/api/jobs/cleanup", json={
        "threshold": 40, "expected_count": 1, "confirmed": True})
    assert reply.status_code == 422
    for job_id in (first, second):
        assert client.get(f"/api/jobs/{job_id}").status_code == 200


def test_the_threshold_is_bounded(db):
    with pytest.raises(ValidationError):
        job_cleanup.plan(db, threshold=101)


def test_a_decided_status_protects_even_with_no_events(client, db, make_analyzed):
    """Status and the event trail are separate records; either one means a
    person acted."""
    job_id = make_analyzed(score=8, verdict="skip")
    db.get(Job, job_id).status = JobStatus.applied
    db.commit()
    assert _plan(client)["protected"] == 1
