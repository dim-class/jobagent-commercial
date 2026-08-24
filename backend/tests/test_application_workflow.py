"""Application workflow: decisions, transitions, and the append-only trail.

Nothing here calls OpenAI and nothing here contacts a recruitment site. Every
status change represents something the *human* said they did.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import ApplicationEvent, EventType, Job, JobStatus
from app.services import application_workflow as workflow
from app.services.application_workflow import InvalidTransitionError, can_transition
from tests.conftest import make_job_payload


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def create_job(client, **overrides) -> int:
    response = client.post("/api/jobs", json=make_job_payload(**overrides))
    assert response.status_code == 201
    return response.json()["job"]["id"]


def events_of(client, job_id: int) -> list[dict]:
    return client.get(f"/api/jobs/{job_id}/application-events").json()


def event_types(client, job_id: int) -> list[str]:
    return [e["event_type"] for e in events_of(client, job_id)]


# --------------------------------------------------------------------------
# mark applied
# --------------------------------------------------------------------------


def test_mark_applied_requires_explicit_confirmation(client):
    """"applied" is a real-world action, so it is never inferred."""
    job_id = create_job(client)
    response = client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": False})

    assert response.status_code == 422
    assert "确认" in response.json()["message"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "new"


def test_mark_applied_records_status_and_event(client):
    job_id = create_job(client)
    response = client.post(
        f"/api/jobs/{job_id}/mark-applied",
        json={"confirmed": True, "note": "已在 BOSS 上打招呼"},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "applied"
    assert body["previous_status"] == "new"
    assert body["event"]["event_type"] == "applied"
    assert body["event"]["notes"] == "已在 BOSS 上打招呼"

    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "applied"
    assert "applied" in event_types(client, job_id)


def test_mark_applied_clears_a_pending_defer(client):
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/later", json={"preset": "tomorrow"})
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})

    from app.db.session import SessionLocal

    with SessionLocal() as session:
        assert session.get(Job, job_id).review_after is None


# --------------------------------------------------------------------------
# skip
# --------------------------------------------------------------------------


def test_skip_with_reason(client):
    job_id = create_job(client)
    response = client.post(f"/api/jobs/{job_id}/skip", json={"reason": "薪资太低"})
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "skipped"
    assert body["event"]["metadata_json"]["skip_reason"] == "薪资太低"
    assert "薪资太低" in body["event"]["notes"]


def test_skip_without_a_reason_is_allowed(client):
    job_id = create_job(client)
    body = client.post(f"/api/jobs/{job_id}/skip", json={}).json()
    assert body["status"] == "skipped"
    assert body["event"]["metadata_json"] == {}


def test_skip_reasons_are_offered_by_the_queue(client):
    reasons = client.get("/api/application-queue").json()["facets"]["skip_reasons"]
    assert "薪资太低" in reasons
    assert "外包/驻场" in reasons


# --------------------------------------------------------------------------
# later
# --------------------------------------------------------------------------


def test_later_defers_without_changing_status(client):
    """稍后处理 is a scheduling hint, not a decision."""
    job_id = create_job(client)
    response = client.post(f"/api/jobs/{job_id}/later", json={"preset": "tomorrow"})
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "new", "deferring must not decide anything"
    assert body["event"]["event_type"] == "later"
    assert body["event"]["metadata_json"]["preset"] == "tomorrow"
    assert body["event"]["metadata_json"]["review_after"]


def test_later_custom_requires_a_date(client):
    job_id = create_job(client)
    response = client.post(f"/api/jobs/{job_id}/later", json={"preset": "custom"})
    assert response.status_code == 422
    assert "日期" in response.json()["message"]


def test_later_custom_date_is_stored(client, db):
    job_id = create_job(client)
    when = datetime.now(timezone.utc) + timedelta(days=3)
    client.post(
        f"/api/jobs/{job_id}/later",
        json={"preset": "custom", "review_after": when.isoformat()},
    )
    db.expire_all()
    assert db.get(Job, job_id).review_after is not None


# --------------------------------------------------------------------------
# reset / undo
# --------------------------------------------------------------------------


def test_reset_restores_pending_without_erasing_history(client):
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    response = client.post(f"/api/jobs/{job_id}/reset-status", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "reviewed"

    kinds = event_types(client, job_id)
    assert "applied" in kinds, "the original decision must survive"
    assert "status_reset" in kinds
    assert kinds.index("applied") < kinds.index("status_reset")


def test_applied_reset_applied_reads_as_a_trail(client):
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    client.post(f"/api/jobs/{job_id}/reset-status", json={})
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})

    kinds = [k for k in event_types(client, job_id) if k in ("applied", "status_reset")]
    assert kinds == ["applied", "status_reset", "applied"]


def test_skip_can_be_undone(client):
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/skip", json={"reason": "地点不合适"})
    client.post(f"/api/jobs/{job_id}/reset-status", json={})

    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "reviewed"
    assert "skipped" in event_types(client, job_id)


def test_reset_on_an_already_pending_job_is_rejected(client):
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/reset-status", json={})
    second = client.post(f"/api/jobs/{job_id}/reset-status", json={})
    assert second.status_code == 422


# --------------------------------------------------------------------------
# funnel steps
# --------------------------------------------------------------------------


def test_record_reply(client):
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    body = client.post(f"/api/jobs/{job_id}/reply", json={"response_type": "positive"}).json()

    assert body["status"] == "replied"
    assert body["event"]["metadata_json"]["response_type"] == "positive"


def test_record_interview_with_round(client):
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    body = client.post(f"/api/jobs/{job_id}/interview", json={"round": "技术面"}).json()

    assert body["status"] == "interview"
    assert body["event"]["metadata_json"]["interview_round"] == "技术面"


def test_interview_without_a_recorded_reply_is_allowed(client):
    """Real processes skip steps - an interview can be booked with no HR reply."""
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    assert client.post(f"/api/jobs/{job_id}/interview", json={}).status_code == 200


def test_record_offer_with_salary(client):
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    client.post(f"/api/jobs/{job_id}/interview", json={"round": "终面"})
    body = client.post(f"/api/jobs/{job_id}/offer", json={"salary_text": "30-40K"}).json()

    assert body["status"] == "offer"
    assert body["event"]["metadata_json"]["offer_salary"] == "30-40K"


def test_record_rejection_with_reason(client):
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    body = client.post(f"/api/jobs/{job_id}/reject", json={"reason": "经验不足"}).json()

    assert body["status"] == "rejected"
    assert body["event"]["metadata_json"]["reject_reason"] == "经验不足"


def test_a_full_funnel_keeps_every_event(client):
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    client.post(f"/api/jobs/{job_id}/reply", json={"response_type": "positive"})
    client.post(f"/api/jobs/{job_id}/interview", json={"round": "一面"})
    client.post(f"/api/jobs/{job_id}/offer", json={"salary_text": "35K"})

    kinds = event_types(client, job_id)
    for expected in ("applied", "replied", "interview", "offer"):
        assert expected in kinds
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "offer"


# --------------------------------------------------------------------------
# transition rules
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        (JobStatus.new, JobStatus.applied, True),
        (JobStatus.new, JobStatus.skipped, True),
        (JobStatus.reviewed, JobStatus.applied, True),
        (JobStatus.applied, JobStatus.replied, True),
        (JobStatus.applied, JobStatus.interview, True),
        (JobStatus.applied, JobStatus.rejected, True),
        (JobStatus.replied, JobStatus.interview, True),
        (JobStatus.interview, JobStatus.offer, True),
        (JobStatus.interview, JobStatus.rejected, True),
        # nonsensical
        (JobStatus.offer, JobStatus.new, False),
        (JobStatus.offer, JobStatus.applied, False),
        (JobStatus.new, JobStatus.offer, False),
        (JobStatus.skipped, JobStatus.applied, False),
        (JobStatus.rejected, JobStatus.interview, False),
    ],
)
def test_transition_rules(current, target, allowed):
    assert can_transition(current, target) is allowed


def test_invalid_transition_is_rejected_with_a_helpful_message(client):
    job_id = create_job(client)
    response = client.post(f"/api/jobs/{job_id}/offer", json={})

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_transition"
    assert "恢复待处理" in response.json()["message"]


def test_reset_is_the_escape_hatch_from_a_terminal_state(client):
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    client.post(f"/api/jobs/{job_id}/reject", json={})
    assert client.post(f"/api/jobs/{job_id}/interview", json={}).status_code == 422

    client.post(f"/api/jobs/{job_id}/reset-status", json={})
    assert client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True}).status_code == 200


# --------------------------------------------------------------------------
# service-level guarantees
# --------------------------------------------------------------------------


def test_workflow_service_is_the_only_status_mutator(db, client):
    """Routes delegate; the service owns the transition + the event."""
    job_id = create_job(client)
    from app.schemas.application import MarkAppliedRequest

    outcome = workflow.mark_applied(db, job_id, MarkAppliedRequest(confirmed=True))
    assert outcome.job.status is JobStatus.applied
    assert outcome.event.event_type is EventType.applied
    assert outcome.previous_status is JobStatus.new


def test_events_are_never_deleted(db, client):
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    client.post(f"/api/jobs/{job_id}/reset-status", json={})
    client.post(f"/api/jobs/{job_id}/skip", json={"reason": "其他"})

    db.expire_all()
    rows = db.query(ApplicationEvent).filter(ApplicationEvent.job_id == job_id).all()
    kinds = [r.event_type.value for r in rows]
    assert "applied" in kinds and "status_reset" in kinds and "skipped" in kinds


def test_event_metadata_is_validated_by_schema(client):
    """Unknown fields on a workflow payload are rejected, not silently stored."""
    job_id = create_job(client)
    response = client.post(
        f"/api/jobs/{job_id}/interview", json={"round": "不存在的轮次"}
    )
    assert response.status_code == 422


def test_manual_note_event(client):
    job_id = create_job(client)
    body = client.post(
        f"/api/jobs/{job_id}/application-events", json={"note": "内推联系人：小王"}
    ).json()
    assert body["event_type"] == "note"
    assert body["notes"] == "内推联系人：小王"


def test_workflow_on_a_missing_job_is_404(client):
    assert client.post("/api/jobs/999999/skip", json={}).status_code == 404
    assert client.get("/api/jobs/999999/application-events").status_code == 404
