"""Interview pipeline HTTP surface (v0.8).

Includes the recruiter handoff, where the rule is that AI detection surfaces a
suggestion and a human creates the round. Nothing here calls OpenAI.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import (
    ConversationStatus,
    InterviewRoundType,
    JobStatus,
    MessageDirection,
    RecruiterConversation,
    RecruiterMessage,
    RecruiterMessageAnalysis,
    RecruiterSource,
)
from app.services import interview_pipeline

from tests.test_career_analytics import make_job
from tests.test_resume_variants import apply_with, make_resume


def applied(client, db, resume=None, **job_kwargs):
    job = make_job(db, status=JobStatus.new, **job_kwargs)
    apply_with(db, job, resume)
    db.refresh(job)
    return job


def open_via_api(client, job_id: int) -> dict:
    response = client.post(f"/api/jobs/{job_id}/interviews", json={})
    assert response.status_code == 200, response.text
    return response.json()["process"]


def add_via_api(client, process_id: int, **payload) -> dict:
    body = {"round_type": "hr", **payload}
    response = client.post(f"/api/interviews/{process_id}/rounds", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------
# processes and rounds
# --------------------------------------------------------------------------


def test_creating_a_process_over_http(client, db):
    resume = make_resume(db, variant_name="DevOps版")
    job = applied(client, db, resume)

    process = open_via_api(client, job.id)

    assert process["job_id"] == job.id
    assert process["status"] == "ongoing"
    assert process["resume_label"] == "DevOps版", "the cycle's resume, not the active one"


def test_a_duplicate_process_is_refused(client, db):
    job = applied(client, db)
    open_via_api(client, job.id)

    response = client.post(f"/api/jobs/{job.id}/interviews", json={})
    assert response.status_code == 422
    assert "已经有面试流程" in response.json()["message"]


def test_a_job_without_an_application_is_refused(client, db):
    job = make_job(db, status=JobStatus.new)
    response = client.post(f"/api/jobs/{job.id}/interviews", json={})
    assert response.status_code == 422


def test_adding_rounds_builds_the_pipeline(client, db):
    job = applied(client, db)
    process = open_via_api(client, job.id)

    add_via_api(client, process["id"], round_type="hr")
    add_via_api(client, process["id"], round_type="technical")
    body = add_via_api(client, process["id"], round_type="final")

    rounds = body["process"]["rounds"]
    assert [r["round_type"] for r in rounds] == ["hr", "technical", "final"]
    assert [r["round_index"] for r in rounds] == [1, 2, 3]
    assert rounds[0]["round_type_label"] == "HR面"
    assert body["job_status"] == "interview"


def test_the_current_round_is_exposed(client, db):
    job = applied(client, db)
    process = open_via_api(client, job.id)
    first = add_via_api(client, process["id"], round_type="hr")["round"]
    add_via_api(client, process["id"], round_type="technical")

    client.post(
        f"/api/interview-rounds/{first['id']}/complete",
        json={"confirmed": True, "outcome": "passed"},
    )

    body = client.get(f"/api/interviews/{process['id']}").json()
    assert body["current_round"]["round_type"] == "technical"
    assert body["rounds_passed"] == 1


def test_completing_a_round_requires_confirmation(client, db):
    job = applied(client, db)
    process = open_via_api(client, job.id)
    row = add_via_api(client, process["id"])["round"]

    response = client.post(
        f"/api/interview-rounds/{row['id']}/complete",
        json={"confirmed": False, "outcome": "passed"},
    )
    assert response.status_code == 422


def test_recording_a_failed_round_with_a_rejection(client, db):
    job = applied(client, db)
    process = open_via_api(client, job.id)
    row = add_via_api(client, process["id"], round_type="technical")["round"]

    body = client.post(
        f"/api/interview-rounds/{row['id']}/complete",
        json={
            "confirmed": True,
            "outcome": "failed",
            "failure_reason": "technical_depth",
            "also_record_rejection": True,
            "feedback_text": "深度不够",
        },
    ).json()

    assert body["job_status"] == "rejected"
    assert body["process"]["status"] == "rejected"
    assert body["process"]["ended_after_round_type"] == "technical"


def test_re_recording_an_outcome_is_refused_without_a_correction_flag(client, db):
    job = applied(client, db)
    process = open_via_api(client, job.id)
    row = add_via_api(client, process["id"])["round"]
    path = f"/api/interview-rounds/{row['id']}/complete"

    assert client.post(path, json={"confirmed": True, "outcome": "passed"}).status_code == 200
    second = client.post(path, json={"confirmed": True, "outcome": "failed"})
    assert second.status_code == 422
    assert "已经记录过结果" in second.json()["message"]

    corrected = client.post(
        path, json={"confirmed": True, "outcome": "failed", "correction": True}
    )
    assert corrected.status_code == 200
    assert corrected.json()["round"]["outcome"] == "failed"


def test_updating_a_round(client, db):
    job = applied(client, db)
    process = open_via_api(client, job.id)
    row = add_via_api(client, process["id"])["round"]

    body = client.patch(
        f"/api/interview-rounds/{row['id']}",
        json={"interviewer_name": "王女士", "duration_minutes": 45, "location_type": "online"},
    ).json()

    assert body["round"]["interviewer_name"] == "王女士"
    assert body["round"]["duration_minutes"] == 45
    assert body["round"]["location_type"] == "online"


def test_cancelling_a_round(client, db):
    job = applied(client, db)
    process = open_via_api(client, job.id)
    row = add_via_api(client, process["id"])["round"]

    body = client.post(
        f"/api/interview-rounds/{row['id']}/cancel", json={"reason": "对方改期"}
    ).json()
    assert body["round"]["status"] == "cancelled"


def test_withdrawing_a_process(client, db):
    job = applied(client, db)
    process = open_via_api(client, job.id)
    add_via_api(client, process["id"])

    body = client.post(
        f"/api/interviews/{process['id']}/withdraw",
        json={"confirmed": True, "reason": "accepted_other_offer"},
    ).json()

    assert body["process"]["status"] == "withdrawn"
    assert body["process"]["withdraw_reason"] == "accepted_other_offer"

    db.expire_all()
    job = db.get(type(job), job.id)
    assert job.status is not JobStatus.rejected


def test_listing_a_jobs_processes(client, db):
    job = applied(client, db)
    open_via_api(client, job.id)

    body = client.get(f"/api/jobs/{job.id}/interviews").json()
    assert body["total"] == 1


# --------------------------------------------------------------------------
# the board
# --------------------------------------------------------------------------


def test_the_board_is_empty_on_a_fresh_database(client):
    body = client.get("/api/interviews").json()
    assert body["upcoming"] == []
    assert body["awaiting_result"] == []
    assert body["timezone"] == "Asia/Tokyo"


def test_upcoming_interviews_are_grouped_by_local_day(client, db):
    """Grouping uses Asia/Tokyo, never the browser's timezone."""
    from app.services.timezones import local_now

    job = applied(client, db)
    process = open_via_api(client, job.id)

    tomorrow_local = local_now() + timedelta(days=1)
    add_via_api(
        client,
        process["id"],
        round_type="technical",
        scheduled_at=tomorrow_local.isoformat(),
    )

    body = client.get("/api/interviews").json()
    assert len(body["upcoming"]) == 1
    assert "明天" in body["upcoming"][0]["label"]


def test_a_today_interview_is_labelled_today(client, db):
    from app.services.timezones import local_now

    job = applied(client, db)
    process = open_via_api(client, job.id)
    later_today = local_now().replace(hour=23, minute=30)
    if later_today <= local_now():
        pytest.skip("no remaining hours in the local day to schedule into")

    add_via_api(
        client, process["id"], round_type="hr", scheduled_at=later_today.isoformat()
    )

    body = client.get("/api/interviews").json()
    assert "今天" in body["upcoming"][0]["label"]


def test_a_past_scheduled_round_moves_to_awaiting_result(client, db):
    job = applied(client, db)
    process = open_via_api(client, job.id)
    past = datetime.now(timezone.utc) - timedelta(days=2)
    add_via_api(client, process["id"], round_type="hr", scheduled_at=past.isoformat())

    body = client.get("/api/interviews").json()
    assert body["upcoming"] == []
    assert len(body["awaiting_result"]) == 1


def test_a_closed_process_moves_to_completed(client, db):
    job = applied(client, db)
    process = open_via_api(client, job.id)
    add_via_api(client, process["id"])
    client.post(
        f"/api/interviews/{process['id']}/withdraw", json={"confirmed": True}
    )

    body = client.get("/api/interviews").json()
    assert len(body["completed"]) == 1


def test_the_dashboard_list_omits_meeting_urls(client, db):
    from app.services.timezones import local_now

    job = applied(client, db)
    process = open_via_api(client, job.id)
    add_via_api(
        client,
        process["id"],
        round_type="technical",
        scheduled_at=(local_now() + timedelta(days=2)).isoformat(),
        meeting_url="https://meet.example.com/x?token=SUPERSECRET",
    )

    response = client.get("/api/interviews/upcoming")
    assert "SUPERSECRET" not in response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["round_label"] == "技术面"


def test_the_meeting_url_is_available_on_the_round_itself(client, db):
    job = applied(client, db)
    process = open_via_api(client, job.id)
    add_via_api(
        client, process["id"], meeting_url="https://meet.example.com/x?token=abc"
    )

    body = client.get(f"/api/interviews/{process['id']}").json()
    assert body["rounds"][0]["meeting_url"].endswith("token=abc")


def test_the_dashboard_says_it_reads_no_calendar(client):
    body = client.get("/api/interviews/upcoming").json()
    assert body["total"] == 0
    assert "不读取日历" in body["message"]


def test_legacy_milestones_appear_unclassified(client, db):
    from app.models import EventType

    from tests.test_career_analytics import NOW, add_event

    job = applied(client, db)
    add_event(db, job, EventType.interview, at=NOW)
    db.refresh(job)

    body = client.get("/api/interviews").json()
    assert len(body["legacy_milestones"]) == 1
    milestone = body["legacy_milestones"][0]
    assert milestone["job_id"] == job.id
    assert "round_type" not in milestone, "no round type is invented"


# --------------------------------------------------------------------------
# analytics endpoint
# --------------------------------------------------------------------------


def test_the_analytics_endpoint_works_on_an_empty_database(client):
    body = client.get("/api/analytics/interviews").json()
    assert body["funnel"]["applications"] == 0
    assert body["funnel"]["application_to_interview"]["rate"] is None


def test_the_analytics_endpoint_reports_the_funnel(client, db):
    job = applied(client, db)
    process = open_via_api(client, job.id)
    row = add_via_api(client, process["id"], round_type="technical")["round"]
    client.post(
        f"/api/interview-rounds/{row['id']}/complete",
        json={"confirmed": True, "outcome": "passed"},
    )

    body = client.get("/api/analytics/interviews?window=all").json()
    assert body["funnel"]["reached_any_interview"] == 1
    assert body["funnel"]["reached_technical"] == 1
    assert body["round_conversion"][0]["label"] == "技术面"


def test_the_analytics_endpoint_supports_filters(client, db):
    applied(client, db, city="杭州")
    applied(client, db, city="北京")

    body = client.get("/api/analytics/interviews?window=all&city=杭州").json()
    assert body["funnel"]["applications"] == 1
    assert body["filters"]["city"] == "杭州"


def test_the_analytics_endpoints_make_no_openai_call(client, db, monkeypatch):
    import app.services.job_matcher as job_matcher

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("interview analytics must never call the model")

    monkeypatch.setattr(job_matcher, "run_job_match", _explode)

    job = applied(client, db)
    open_via_api(client, job.id)

    assert client.get("/api/analytics/interviews").status_code == 200
    assert client.get("/api/interviews").status_code == 200
    assert client.get("/api/interviews/upcoming").status_code == 200


# --------------------------------------------------------------------------
# recruiter handoff
# --------------------------------------------------------------------------


def make_conversation(db, job, *, dates, stage="interview_scheduling"):
    conversation = RecruiterConversation(
        job_id=job.id if job else None,
        source=RecruiterSource.boss,
        recruiter_name="HR",
        status=ConversationStatus.needs_reply,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)

    now = datetime.now(timezone.utc)
    message = RecruiterMessage(
        conversation_id=conversation.id,
        direction=MessageDirection.recruiter,
        raw_text="下周三下午方便面试吗？",
        content_hash=f"msg-{conversation.id:06d}",
        captured_at=now,
        created_at=now,
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    db.add(
        RecruiterMessageAnalysis(
            message_id=message.id,
            job_id=job.id if job else None,
            model="test-model",
            prompt_version="test",
            cache_key=f"rc-{conversation.id:06d}",
            result_json={"conversation_stage": stage, "dates_times": dates},
            created_at=now,
        )
    )
    db.commit()
    return conversation


def test_a_recruiter_message_produces_a_suggestion_not_an_interview(client, db):
    """AI detection is not workflow mutation."""
    job = applied(client, db)
    when = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    conversation = make_conversation(
        db,
        job,
        dates=[
            {
                "raw_text": "下周三 15:00",
                "normalized_at": when,
                "is_ambiguous": False,
                "context": "技术面试",
            }
        ],
    )

    body = client.get(
        f"/api/recruiter/conversations/{conversation.id}/interview-suggestions"
    ).json()

    assert body["total"] == 1
    suggestion = body["items"][0]
    assert suggestion["can_add"] is True
    assert suggestion["suggested_round_type"] == "technical"
    assert "确认后才会创建" in body["message"]

    # Crucially: nothing was created.
    assert interview_pipeline.list_processes(db, job_id=job.id) == []


def test_confirming_a_suggestion_is_what_creates_the_round(client, db):
    job = applied(client, db)
    when = datetime.now(timezone.utc) + timedelta(days=3)
    conversation = make_conversation(
        db,
        job,
        dates=[
            {
                "raw_text": "下周三 15:00",
                "normalized_at": when.isoformat(),
                "is_ambiguous": False,
                "context": "技术面试",
            }
        ],
    )
    suggestion = client.get(
        f"/api/recruiter/conversations/{conversation.id}/interview-suggestions"
    ).json()["items"][0]

    process = open_via_api(client, job.id)
    body = add_via_api(
        client,
        process["id"],
        round_type=suggestion["suggested_round_type"],
        scheduled_at=suggestion["scheduled_at"],
    )

    assert body["round"]["round_type"] == "technical"
    assert body["round"]["status"] == "scheduled"


def test_an_ambiguous_time_cannot_be_added_directly(client, db):
    job = applied(client, db)
    conversation = make_conversation(
        db,
        job,
        dates=[
            {
                "raw_text": "下周找个时间",
                "normalized_at": None,
                "is_ambiguous": True,
                "context": "面试",
            }
        ],
    )

    suggestion = client.get(
        f"/api/recruiter/conversations/{conversation.id}/interview-suggestions"
    ).json()["items"][0]
    assert suggestion["can_add"] is False
    assert "时间不明确" in suggestion["reason"]


def test_a_conversation_without_a_job_cannot_add_an_interview(client, db):
    when = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    conversation = make_conversation(
        db,
        None,
        dates=[
            {
                "raw_text": "下周三 15:00",
                "normalized_at": when,
                "is_ambiguous": False,
                "context": "面试",
            }
        ],
    )

    suggestion = client.get(
        f"/api/recruiter/conversations/{conversation.id}/interview-suggestions"
    ).json()["items"][0]
    assert suggestion["can_add"] is False
    assert "没有关联岗位" in suggestion["reason"]


def test_a_non_scheduling_conversation_yields_no_suggestion(client, db):
    job = applied(client, db)
    when = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    conversation = make_conversation(
        db,
        job,
        stage="salary_discussion",
        dates=[
            {
                "raw_text": "下周三",
                "normalized_at": when,
                "is_ambiguous": False,
                "context": "发offer",
            }
        ],
    )

    body = client.get(
        f"/api/recruiter/conversations/{conversation.id}/interview-suggestions"
    ).json()
    assert body["total"] == 0
    assert "没有检测到" in body["message"]
