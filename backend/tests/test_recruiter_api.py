"""Recruiter conversation API: intake, analysis, and the human boundary.

No test here spends OpenAI credit - the agent is always patched. No test here
reads an inbox or contacts a recruitment site; there is no such code to reach.

The rules this file exists to pin down:
  * copying a draft changes nothing;
  * a recruiter message never moves ``Job.status`` on its own;
  * mark-sent stores the user's **edited** text, and only after confirmation.
"""

from __future__ import annotations

import io
import struct
import zlib
from datetime import datetime, timedelta, timezone

import pytest

from app.models import (
    ApplicationEvent,
    ConversationStatus,
    EventType,
    Job,
    JobStatus,
    MessageDirection,
    RecruiterMessage,
)
from app.schemas.recruiter import RecruiterMessageAnalysisResult
from app.services import recruiter_message_analyzer as analyzer
from tests.conftest import make_job_payload

RECRUITER_MSG = "您好，想确认您的期望薪资和最快到岗时间。另外下周三下午方便面试吗？"
SECOND_MSG = "好的。那我们安排下周三 15:00 的线上面试，可以吗？"
JAPANESE_MSG = "お世話になっております。希望年収と入社可能日を教えていただけますか。"
ENGLISH_MSG = "Hi! Could you share your expected salary and interview availability?"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def make_analysis(**overrides) -> RecruiterMessageAnalysisResult:
    payload = {
        "sentiment": "positive",
        "conversation_stage": "interview_scheduling",
        "needs_reply": True,
        "urgency": "normal",
        "summary": "HR 询问期望薪资、到岗时间，并提出下周三面试。",
        "recruiter_requests": [
            {
                "type": "expected_salary",
                "summary": "询问期望薪资",
                "required": True,
                "answer_found_in_profile": False,
                "suggested_answer": None,
            },
            {
                "type": "start_date",
                "summary": "询问最快到岗时间",
                "required": True,
                "answer_found_in_profile": False,
                "suggested_answer": None,
            },
            {
                "type": "interview_availability",
                "summary": "确认下周三是否方便面试",
                "required": True,
                "answer_found_in_profile": False,
                "suggested_answer": None,
            },
        ],
        "action_items": [
            {"summary": "确认下周三的空闲时段", "blocking": True},
            {"summary": "确定期望薪资区间", "blocking": True},
        ],
        "dates_times": [
            {
                "raw_text": "下周三下午",
                "normalized_at": None,
                "normalized_date": None,
                "is_ambiguous": True,
                "context": "面试",
            }
        ],
        "missing_information": ["期望薪资尚未配置"],
        "risk_flags": [],
        "suggested_reply": "您好，感谢联系。我对该职位有兴趣，下周三下午均可安排线上面试。期望薪资为【请填写】。",
        "suggested_reply_language": "zh",
        "confidence": 82,
    }
    payload.update(overrides)
    return RecruiterMessageAnalysisResult.model_validate(payload)


@pytest.fixture
def fake_agent(monkeypatch):
    """Patch the agent and record every call. Zero API spend."""
    calls: list[dict] = []
    box = {"result": make_analysis()}

    async def _fake(**kwargs):
        calls.append(kwargs)
        return box["result"]

    monkeypatch.setattr("app.agents.recruiter_agent.run_message_analysis", _fake)
    return type("FakeAgent", (), {"calls": calls, "box": box})


@pytest.fixture
def ai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def create_job(client, **overrides) -> int:
    return client.post("/api/jobs", json=make_job_payload(**overrides)).json()["job"]["id"]


def new_conversation(client, **payload) -> dict:
    body = {"source": "boss", **payload}
    response = client.post("/api/recruiter-conversations", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def add_text(client, conversation_id: int, text: str, **kwargs) -> dict:
    return client.post(
        f"/api/recruiter-conversations/{conversation_id}/messages/text",
        json={"text": text, **kwargs},
    ).json()


# --------------------------------------------------------------------------
# conversations
# --------------------------------------------------------------------------


def test_conversation_without_a_job(client):
    body = new_conversation(client, recruiter_name="张女士", company="示例科技")
    assert body["job_id"] is None
    assert body["status"] == "needs_reply"
    assert body["recruiter_name"] == "张女士"


def test_conversation_linked_to_a_job_inherits_its_labels(client):
    job_id = create_job(client, company="星澜科技", title="DevOps工程师")
    body = new_conversation(client, job_id=job_id)

    assert body["job_id"] == job_id
    assert body["company"] == "星澜科技"
    assert body["title"] == "DevOps工程师"
    assert body["job_company"] == "星澜科技"


def test_linking_a_missing_job_is_404(client):
    assert client.post(
        "/api/recruiter-conversations", json={"job_id": 999999}
    ).status_code == 404


def test_conversation_can_be_linked_later(client):
    job_id = create_job(client)
    conversation = new_conversation(client)
    assert conversation["job_id"] is None

    updated = client.patch(
        f"/api/recruiter-conversations/{conversation['id']}", json={"job_id": job_id}
    ).json()
    assert updated["job_id"] == job_id


def test_missing_conversation_is_404(client):
    assert client.get("/api/recruiter-conversations/999999").status_code == 404


# --------------------------------------------------------------------------
# message intake
# --------------------------------------------------------------------------


def test_add_recruiter_message_without_ai(client):
    """No API key configured: intake still works, analysis is skipped."""
    conversation = new_conversation(client)
    body = add_text(client, conversation["id"], RECRUITER_MSG)

    assert body["duplicate"] is False
    assert body["ai_used"] is False
    assert body["ai_available"] is False
    assert body["message"]["direction"] == "recruiter"
    assert body["message"]["raw_text"] == RECRUITER_MSG
    assert body["conversation"]["status"] == "needs_reply"


def test_messages_persist_separately_from_application_events(client, db):
    """Message bodies never get copied into the workflow trail."""
    job_id = create_job(client)
    conversation = new_conversation(client, job_id=job_id)
    add_text(client, conversation["id"], RECRUITER_MSG)

    db.expire_all()
    assert db.query(RecruiterMessage).count() == 1
    for event in db.query(ApplicationEvent).all():
        assert RECRUITER_MSG not in (event.notes or "")
        assert RECRUITER_MSG not in str(event.metadata_json or {})


def test_duplicate_message_within_a_conversation_is_detected(client):
    conversation = new_conversation(client)
    first = add_text(client, conversation["id"], RECRUITER_MSG)
    second = add_text(client, conversation["id"], RECRUITER_MSG)

    assert second["duplicate"] is True
    assert second["message"]["id"] == first["message"]["id"]
    assert len(second["conversation"]["messages"]) == 1


def test_duplicate_detection_ignores_whitespace_only_changes(client):
    conversation = new_conversation(client)
    add_text(client, conversation["id"], RECRUITER_MSG)
    reflowed = add_text(client, conversation["id"], f"  {RECRUITER_MSG}  ")
    assert reflowed["duplicate"] is True


def test_identical_text_in_two_conversations_is_not_a_duplicate(client):
    """"好的，谢谢" in unrelated threads is two real messages."""
    a = new_conversation(client)
    b = new_conversation(client)
    add_text(client, a["id"], "好的，谢谢您。")
    other = add_text(client, b["id"], "好的，谢谢您。")

    assert other["duplicate"] is False
    assert len(other["conversation"]["messages"]) == 1


def test_pasted_transcript_creates_multiple_messages(client):
    conversation = new_conversation(client)
    transcript = "HR：您好，请问期望薪资？\n我：30-40K。\nHR：好的，方便下周三面试吗？"
    body = add_text(client, conversation["id"], transcript)

    assert body["parsed_message_count"] == 3
    directions = [m["direction"] for m in body["conversation"]["messages"]]
    assert directions == ["recruiter", "user", "recruiter"]
    # the newest recruiter turn is the one worth analyzing
    assert "下周三" in body["message"]["raw_text"]


def test_empty_message_is_rejected(client):
    conversation = new_conversation(client)
    response = client.post(
        f"/api/recruiter-conversations/{conversation['id']}/messages/text",
        json={"text": "   "},
    )
    assert response.status_code == 422


def test_appending_a_recruiter_message_reopens_needs_reply(client):
    conversation = new_conversation(client)
    add_text(client, conversation["id"], RECRUITER_MSG)
    client.post(
        f"/api/recruiter-conversations/{conversation['id']}/mark-sent",
        json={"confirmed": True, "final_text": "好的，我下周三可以。"},
    )
    assert (
        client.get(f"/api/recruiter-conversations/{conversation['id']}").json()["status"]
        == "waiting_recruiter"
    )

    add_text(client, conversation["id"], SECOND_MSG)
    assert (
        client.get(f"/api/recruiter-conversations/{conversation['id']}").json()["status"]
        == "needs_reply"
    )


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------


def test_analysis_runs_on_intake(client, fake_agent, ai_key):
    conversation = new_conversation(client)
    body = add_text(client, conversation["id"], RECRUITER_MSG)

    assert body["ai_used"] is True
    analysis = body["message"]["analysis"]
    assert analysis["sentiment"] == "positive"
    assert analysis["conversation_stage"] == "interview_scheduling"
    assert analysis["needs_reply"] is True
    assert {r["type"] for r in analysis["recruiter_requests"]} == {
        "expected_salary",
        "start_date",
        "interview_availability",
    }
    assert len(analysis["action_items"]) == 2


def test_analysis_is_cached(client, fake_agent, ai_key):
    conversation = new_conversation(client)
    body = add_text(client, conversation["id"], RECRUITER_MSG)
    message_id = body["message"]["id"]

    again = client.post(
        f"/api/recruiter-conversations/{conversation['id']}/messages/{message_id}/analyze",
        json={},
    ).json()
    assert again["cached"] is True
    assert len(fake_agent.calls) == 1, "a cache hit must not call the model"


def test_force_bypasses_the_cache(client, fake_agent, ai_key):
    conversation = new_conversation(client)
    message_id = add_text(client, conversation["id"], RECRUITER_MSG)["message"]["id"]

    forced = client.post(
        f"/api/recruiter-conversations/{conversation['id']}/messages/{message_id}/analyze",
        json={"force": True},
    ).json()
    assert forced["cached"] is False
    assert len(fake_agent.calls) == 2


def test_smart_model_has_a_separate_cache(client, fake_agent, ai_key):
    conversation = new_conversation(client)
    message_id = add_text(client, conversation["id"], RECRUITER_MSG)["message"]["id"]

    smart = client.post(
        f"/api/recruiter-conversations/{conversation['id']}/messages/{message_id}/reanalyze-smart",
        json={},
    ).json()
    assert smart["model"] == "test-model-smart"
    assert smart["cached"] is False
    assert len(fake_agent.calls) == 2

    again = client.post(
        f"/api/recruiter-conversations/{conversation['id']}/messages/{message_id}/reanalyze-smart",
        json={},
    ).json()
    assert again["cached"] is True
    assert len(fake_agent.calls) == 2


def test_language_override_invalidates_the_cache(client, fake_agent, ai_key):
    conversation = new_conversation(client)
    message_id = add_text(client, conversation["id"], RECRUITER_MSG)["message"]["id"]

    client.post(
        f"/api/recruiter-conversations/{conversation['id']}/messages/{message_id}/analyze",
        json={"language": "en"},
    )
    assert len(fake_agent.calls) == 2
    assert fake_agent.calls[-1]["language_preference"] == "en"


def test_linking_a_job_invalidates_the_cache(client, fake_agent, ai_key):
    conversation = new_conversation(client)
    message_id = add_text(client, conversation["id"], RECRUITER_MSG)["message"]["id"]
    assert len(fake_agent.calls) == 1

    job_id = create_job(client)
    client.patch(f"/api/recruiter-conversations/{conversation['id']}", json={"job_id": job_id})
    client.post(
        f"/api/recruiter-conversations/{conversation['id']}/messages/{message_id}/analyze",
        json={},
    )
    assert len(fake_agent.calls) == 2, "job context changed, so the reading must be redone"
    assert fake_agent.calls[-1]["job"] is not None


def test_unrelated_application_events_do_not_invalidate_the_cache(client, fake_agent, ai_key):
    job_id = create_job(client)
    conversation = new_conversation(client, job_id=job_id)
    message_id = add_text(client, conversation["id"], RECRUITER_MSG)["message"]["id"]

    client.post(f"/api/jobs/{job_id}/application-events", json={"note": "无关备注"})
    client.post(
        f"/api/recruiter-conversations/{conversation['id']}/messages/{message_id}/analyze",
        json={},
    )
    assert len(fake_agent.calls) == 1, "an unrelated event must not force re-analysis"


def test_analysis_receives_bounded_context(client, fake_agent, ai_key):
    """A long thread must not grow the prompt without limit."""
    from app.agents.recruiter_prompts import MAX_CONTEXT_MESSAGES

    conversation = new_conversation(client)
    for i in range(MAX_CONTEXT_MESSAGES + 4):
        add_text(client, conversation["id"], f"这是第 {i} 条招聘方消息，请确认。")

    prior = fake_agent.calls[-1]["prior_messages"]
    assert len(prior) <= MAX_CONTEXT_MESSAGES


def test_deterministic_signals_are_returned_alongside_the_analysis(client, fake_agent, ai_key):
    conversation = new_conversation(client)
    message_id = add_text(client, conversation["id"], RECRUITER_MSG)["message"]["id"]

    body = client.post(
        f"/api/recruiter-conversations/{conversation['id']}/messages/{message_id}/analyze",
        json={},
    ).json()
    signals = body["deterministic_signals"]
    assert signals["language"] == "zh"
    assert "expected_salary" in signals["request_types"]


def test_analysis_failure_does_not_lose_the_message(client, ai_key, monkeypatch):
    from app.core.errors import UpstreamError

    async def _fail(**kwargs):
        raise UpstreamError("AI 分析暂时不可用，请稍后重试。")

    monkeypatch.setattr("app.agents.recruiter_agent.run_message_analysis", _fail)

    conversation = new_conversation(client)
    body = add_text(client, conversation["id"], RECRUITER_MSG)

    assert body["ai_used"] is False
    assert "AI 分析暂时不可用" in body["ai_error"]
    assert body["message"]["raw_text"] == RECRUITER_MSG, "the message is still stored"


def test_our_own_messages_are_not_analyzed(client, fake_agent, ai_key):
    conversation = new_conversation(client)
    add_text(client, conversation["id"], "您好，我是候选人。", direction="user")
    assert fake_agent.calls == []


# --------------------------------------------------------------------------
# reply language
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected_language"),
    [(RECRUITER_MSG, "zh"), (JAPANESE_MSG, "ja"), (ENGLISH_MSG, "en")],
)
def test_detected_language_is_passed_to_the_agent(
    client, fake_agent, ai_key, message, expected_language
):
    conversation = new_conversation(client)
    add_text(client, conversation["id"], message)

    assert fake_agent.calls[-1]["detected_language"] == expected_language
    assert fake_agent.calls[-1]["language_preference"] == "auto"


def test_manual_language_override_is_passed_through(client, fake_agent, ai_key):
    conversation = new_conversation(client)
    add_text(client, conversation["id"], RECRUITER_MSG, language="ja")
    assert fake_agent.calls[-1]["language_preference"] == "ja"


def test_reply_language_is_reported(client, fake_agent, ai_key):
    fake_agent.box["result"] = make_analysis(
        suggested_reply="ご連絡ありがとうございます。来週水曜の午後で調整可能です。",
        suggested_reply_language="ja",
    )
    conversation = new_conversation(client)
    body = add_text(client, conversation["id"], JAPANESE_MSG)
    assert body["message"]["analysis"]["suggested_reply_language"] == "ja"


# --------------------------------------------------------------------------
# grounding: never invent
# --------------------------------------------------------------------------


def test_unconfigured_salary_is_never_answered(client, fake_agent, ai_key):
    """The model must not quote a salary the user never configured."""
    fake_agent.box["result"] = make_analysis(
        recruiter_requests=[
            {
                "type": "expected_salary",
                "summary": "询问期望薪资",
                "required": True,
                "answer_found_in_profile": True,
                "suggested_answer": "我的期望是 45-55K",  # fabricated
            }
        ],
        missing_information=[],
    )
    conversation = new_conversation(client)
    body = add_text(client, conversation["id"], RECRUITER_MSG)

    request = body["message"]["analysis"]["recruiter_requests"][0]
    assert request["suggested_answer"] is None
    assert request["answer_found_in_profile"] is False
    assert any("期望薪资" in m for m in body["message"]["analysis"]["missing_information"])


def test_current_salary_is_always_treated_as_unknown(client, fake_agent, ai_key):
    fake_agent.box["result"] = make_analysis(
        recruiter_requests=[
            {
                "type": "current_salary",
                "summary": "询问当前薪资",
                "required": True,
                "answer_found_in_profile": True,
                "suggested_answer": "目前 38K",
            }
        ]
    )
    conversation = new_conversation(client)
    body = add_text(client, conversation["id"], RECRUITER_MSG)

    request = body["message"]["analysis"]["recruiter_requests"][0]
    assert request["suggested_answer"] is None
    assert any("当前薪资" in m for m in body["message"]["analysis"]["missing_information"])


def test_configured_salary_may_be_used(client, fake_agent, ai_key):
    """When the user HAS configured a salary, a grounded answer survives."""
    strategy = client.get("/api/settings/career-strategy").json()["strategy"]
    strategy["salary"]["min_monthly_cny"] = 30000
    client.put("/api/settings/career-strategy", json=strategy)

    fake_agent.box["result"] = make_analysis(
        recruiter_requests=[
            {
                "type": "expected_salary",
                "summary": "询问期望薪资",
                "required": True,
                "answer_found_in_profile": True,
                "suggested_answer": "期望月薪 30K 以上",
            }
        ],
        missing_information=[],
    )
    conversation = new_conversation(client)
    body = add_text(client, conversation["id"], RECRUITER_MSG)
    request = body["message"]["analysis"]["recruiter_requests"][0]
    assert request["suggested_answer"] == "期望月薪 30K 以上"


def test_placeholder_answers_are_left_alone(client, fake_agent, ai_key):
    fake_agent.box["result"] = make_analysis(
        recruiter_requests=[
            {
                "type": "expected_salary",
                "summary": "询问期望薪资",
                "required": True,
                "answer_found_in_profile": False,
                "suggested_answer": "我的期望薪资为【请填写】",
            }
        ]
    )
    conversation = new_conversation(client)
    body = add_text(client, conversation["id"], RECRUITER_MSG)
    assert "【请填写】" in body["message"]["analysis"]["recruiter_requests"][0]["suggested_answer"]


def test_missing_resume_is_flagged(client, fake_agent, ai_key):
    conversation = new_conversation(client)
    body = add_text(client, conversation["id"], RECRUITER_MSG)
    assert any(
        "简历" in m for m in body["message"]["analysis"]["missing_information"]
    ), "with no resume, the reply must not cite experience"


def test_profile_is_passed_as_the_only_source_of_truth(client, fake_agent, ai_key, active_resume):
    conversation = new_conversation(client)
    add_text(client, conversation["id"], RECRUITER_MSG)

    profile = fake_agent.calls[-1]["profile"]
    assert profile is not None
    assert "AWS" in (profile.get("skills") or [])


def test_ambiguous_dates_are_preserved_not_guessed(client, fake_agent, ai_key):
    conversation = new_conversation(client)
    body = add_text(client, conversation["id"], RECRUITER_MSG)

    mention = body["message"]["analysis"]["dates_times"][0]
    assert mention["raw_text"] == "下周三下午"
    assert mention["is_ambiguous"] is True
    assert mention["normalized_at"] is None
    assert mention["normalized_date"] is None


def test_unambiguous_dates_may_be_normalized(client, fake_agent, ai_key):
    fake_agent.box["result"] = make_analysis(
        dates_times=[
            {
                "raw_text": "8月26日 15:00",
                "normalized_at": "2026-08-26T15:00:00+09:00",
                "normalized_date": "2026-08-26",
                "is_ambiguous": False,
                "context": "面试",
            }
        ]
    )
    conversation = new_conversation(client)
    body = add_text(client, conversation["id"], RECRUITER_MSG)

    mention = body["message"]["analysis"]["dates_times"][0]
    assert mention["is_ambiguous"] is False
    assert mention["normalized_date"] == "2026-08-26"


def test_timezone_and_today_are_supplied_to_the_agent(client, fake_agent, ai_key):
    conversation = new_conversation(client)
    add_text(client, conversation["id"], RECRUITER_MSG)
    assert fake_agent.calls[-1]["timezone_name"] == "Asia/Tokyo"
    assert fake_agent.calls[-1]["today"]


# --------------------------------------------------------------------------
# the human boundary
# --------------------------------------------------------------------------


def test_recruiter_message_never_changes_job_status(client, fake_agent, ai_key, db):
    """An AI reading is an interpretation, never a workflow decision."""
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})

    conversation = new_conversation(client, job_id=job_id)
    body = add_text(client, conversation["id"], RECRUITER_MSG)

    db.expire_all()
    assert db.get(Job, job_id).status is JobStatus.applied, "still applied, not replied"
    assert body["conversation"]["suggests_recruiter_reply_event"] is True, "button offered only"


def test_recording_the_hr_reply_uses_the_existing_workflow(client, fake_agent, ai_key, db):
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    conversation = new_conversation(client, job_id=job_id)
    add_text(client, conversation["id"], RECRUITER_MSG)

    response = client.post(f"/api/jobs/{job_id}/reply", json={"response_type": "positive"})
    assert response.status_code == 200
    assert response.json()["status"] == "replied"

    db.expire_all()
    kinds = [e.event_type for e in db.query(ApplicationEvent).all()]
    assert EventType.replied in kinds


def test_interview_mention_does_not_create_an_interview(client, fake_agent, ai_key, db):
    """Extraction surfaces a candidate time; only the human records it."""
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    conversation = new_conversation(client, job_id=job_id)
    add_text(client, conversation["id"], RECRUITER_MSG)

    db.expire_all()
    assert db.get(Job, job_id).status is JobStatus.applied
    assert EventType.interview not in [e.event_type for e in db.query(ApplicationEvent).all()]

    client.post(f"/api/jobs/{job_id}/interview", json={"round": "一面"})
    db.expire_all()
    assert db.get(Job, job_id).status is JobStatus.interview


def test_no_suggestion_when_the_job_was_never_applied_to(client, fake_agent, ai_key):
    job_id = create_job(client)  # status new
    conversation = new_conversation(client, job_id=job_id)
    body = add_text(client, conversation["id"], RECRUITER_MSG)
    assert body["conversation"]["suggests_recruiter_reply_event"] is False


# --------------------------------------------------------------------------
# mark sent
# --------------------------------------------------------------------------


def test_mark_sent_requires_confirmation(client):
    conversation = new_conversation(client)
    add_text(client, conversation["id"], RECRUITER_MSG)

    response = client.post(
        f"/api/recruiter-conversations/{conversation['id']}/mark-sent",
        json={"confirmed": False, "final_text": "您好。"},
    )
    assert response.status_code == 422
    assert "确认" in response.json()["message"]


def test_mark_sent_stores_the_edited_text_not_the_draft(client, fake_agent, ai_key):
    """The user edits before sending; what is stored is what they sent."""
    conversation = new_conversation(client)
    body = add_text(client, conversation["id"], RECRUITER_MSG)
    draft = body["message"]["analysis"]["suggested_reply"]

    edited = "您好，感谢联系。下周三 14:00 之后我都可以，期望薪资 35-45K。"
    assert edited != draft

    result = client.post(
        f"/api/recruiter-conversations/{conversation['id']}/mark-sent",
        json={"confirmed": True, "final_text": edited},
    ).json()

    user_messages = [
        m for m in result["conversation"]["messages"] if m["direction"] == "user"
    ]
    assert len(user_messages) == 1
    assert user_messages[0]["raw_text"] == edited
    assert draft not in [m["raw_text"] for m in user_messages]


def test_copying_a_draft_changes_nothing(client, fake_agent, ai_key, db):
    """There is no endpoint for copying - reading the analysis is inert."""
    job_id = create_job(client)
    conversation = new_conversation(client, job_id=job_id)
    message_id = add_text(client, conversation["id"], RECRUITER_MSG)["message"]["id"]

    before_status = client.get(f"/api/jobs/{job_id}").json()["status"]
    before_events = len(client.get(f"/api/jobs/{job_id}/application-events").json())

    client.get(f"/api/recruiter-conversations/{conversation['id']}")
    client.post(
        f"/api/recruiter-conversations/{conversation['id']}/messages/{message_id}/analyze",
        json={},
    )

    db.expire_all()
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == before_status
    assert len(client.get(f"/api/jobs/{job_id}/application-events").json()) == before_events
    assert db.query(RecruiterMessage).filter_by(direction=MessageDirection.user).count() == 0


def test_mark_sent_appends_a_candidate_reply_event_when_linked(client, db):
    job_id = create_job(client)
    conversation = new_conversation(client, job_id=job_id)
    add_text(client, conversation["id"], RECRUITER_MSG)

    client.post(
        f"/api/recruiter-conversations/{conversation['id']}/mark-sent",
        json={"confirmed": True, "final_text": "好的，下周三可以。"},
    )

    events = client.get(f"/api/jobs/{job_id}/application-events").json()
    candidate = [e for e in events if e["event_type"] == "candidate_reply"]
    assert len(candidate) == 1
    assert candidate[0]["metadata_json"]["conversation_id"] == conversation["id"]
    assert "message_id" in candidate[0]["metadata_json"]
    assert "好的，下周三可以。" not in (candidate[0]["notes"] or "")


def test_mark_sent_without_a_job_link_is_fine(client):
    conversation = new_conversation(client)
    add_text(client, conversation["id"], RECRUITER_MSG)
    result = client.post(
        f"/api/recruiter-conversations/{conversation['id']}/mark-sent",
        json={"confirmed": True, "final_text": "收到，谢谢。"},
    )
    assert result.status_code == 200
    assert result.json()["conversation"]["status"] == "waiting_recruiter"


def test_mark_sent_can_skip_the_job_event(client):
    job_id = create_job(client)
    conversation = new_conversation(client, job_id=job_id)
    add_text(client, conversation["id"], RECRUITER_MSG)

    client.post(
        f"/api/recruiter-conversations/{conversation['id']}/mark-sent",
        json={"confirmed": True, "final_text": "好的。", "record_job_event": False},
    )
    events = client.get(f"/api/jobs/{job_id}/application-events").json()
    assert not [e for e in events if e["event_type"] == "candidate_reply"]


# --------------------------------------------------------------------------
# follow-up and closing
# --------------------------------------------------------------------------


def test_follow_up_scheduling(client):
    conversation = new_conversation(client)
    add_text(client, conversation["id"], RECRUITER_MSG)

    body = client.post(
        f"/api/recruiter-conversations/{conversation['id']}/follow-up",
        json={"preset": "in_3_days"},
    ).json()
    assert body["conversation"]["next_action_at"] is not None
    assert body["conversation"]["status"] == "waiting_recruiter"


def test_custom_follow_up_requires_a_date(client):
    conversation = new_conversation(client)
    response = client.post(
        f"/api/recruiter-conversations/{conversation['id']}/follow-up",
        json={"preset": "custom"},
    )
    assert response.status_code == 422


def test_a_due_follow_up_surfaces_in_the_inbox(client, db):
    from app.models import RecruiterConversation

    conversation = new_conversation(client)
    add_text(client, conversation["id"], RECRUITER_MSG)
    client.post(
        f"/api/recruiter-conversations/{conversation['id']}/follow-up",
        json={"preset": "tomorrow"},
    )

    db.expire_all()
    row = db.get(RecruiterConversation, conversation["id"])
    row.next_action_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()

    inbox = client.get("/api/recruiter-conversations").json()
    assert inbox["summary"]["follow_up_due"] == 1
    assert inbox["items"][0]["status"] == "follow_up_due"


def test_closing_a_conversation_does_not_touch_the_job(client, db):
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    conversation = new_conversation(client, job_id=job_id)
    add_text(client, conversation["id"], RECRUITER_MSG)

    body = client.post(
        f"/api/recruiter-conversations/{conversation['id']}/close",
        json={"reason": "已拒绝"},
    ).json()

    assert body["conversation"]["status"] == "closed"
    db.expire_all()
    assert db.get(Job, job_id).status is JobStatus.applied, "closing a thread is not a job decision"


def test_closing_can_optionally_record_a_job_rejection(client, db):
    job_id = create_job(client)
    client.post(f"/api/jobs/{job_id}/mark-applied", json={"confirmed": True})
    conversation = new_conversation(client, job_id=job_id)

    client.post(
        f"/api/recruiter-conversations/{conversation['id']}/close",
        json={"reason": "已拒绝", "also_record_job_rejection": True},
    )
    db.expire_all()
    assert db.get(Job, job_id).status is JobStatus.rejected
    assert EventType.rejected in [e.event_type for e in db.query(ApplicationEvent).all()]


# --------------------------------------------------------------------------
# inbox
# --------------------------------------------------------------------------


def test_inbox_summary_counts(client, fake_agent, ai_key):
    a = new_conversation(client)
    add_text(client, a["id"], RECRUITER_MSG)

    b = new_conversation(client)
    add_text(client, b["id"], "您好，请问方便聊聊吗？")
    client.post(
        f"/api/recruiter-conversations/{b['id']}/mark-sent",
        json={"confirmed": True, "final_text": "方便的。"},
    )

    summary = client.get("/api/recruiter-conversations").json()["summary"]
    assert summary["needs_reply"] == 1
    assert summary["waiting_recruiter"] == 1
    assert summary["received_today"] == 2
    assert summary["replied_today"] == 1
    assert summary["timezone"] == "Asia/Tokyo"


def test_inbox_filters(client):
    job_id = create_job(client)
    linked = new_conversation(client, job_id=job_id, company="星澜科技")
    add_text(client, linked["id"], RECRUITER_MSG)
    other = new_conversation(client, company="另一家公司")
    add_text(client, other["id"], "您好。")

    assert client.get(
        "/api/recruiter-conversations", params={"job_id": job_id}
    ).json()["total"] == 1
    assert client.get(
        "/api/recruiter-conversations", params={"keyword": "星澜"}
    ).json()["total"] == 1
    assert client.get(
        "/api/recruiter-conversations", params={"status": "needs_reply"}
    ).json()["total"] == 2


def test_needs_reply_sorts_above_waiting(client):
    waiting = new_conversation(client)
    add_text(client, waiting["id"], "您好。")
    client.post(
        f"/api/recruiter-conversations/{waiting['id']}/mark-sent",
        json={"confirmed": True, "final_text": "您好。"},
    )
    pending = new_conversation(client)
    add_text(client, pending["id"], RECRUITER_MSG)

    items = client.get("/api/recruiter-conversations").json()["items"]
    assert items[0]["id"] == pending["id"]


def test_empty_inbox(client):
    body = client.get("/api/recruiter-conversations").json()
    assert body["total"] == 0
    assert body["summary"]["needs_reply"] == 0


# --------------------------------------------------------------------------
# screenshots
# --------------------------------------------------------------------------


def make_png(padding: int = 0) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return png + b"\x00" * padding


@pytest.fixture
def fake_vision(monkeypatch):
    from app.agents.recruiter_agent import VisionMessage, VisionTranscript

    seen: list[tuple[int, str]] = []
    box = {
        "transcript": VisionTranscript(
            messages=[
                VisionMessage(speaker="recruiter", text="您好，方便下周三面试吗？", time_text="10:31"),
                VisionMessage(speaker="user", text="好的，我看一下时间。"),
            ],
            partial=True,
            notes=["顶部消息被截断"],
        )
    }

    async def _fake(image_bytes, mime_type, *, settings=None):
        seen.append((len(image_bytes), mime_type))
        return box["transcript"], "test-model-fast"

    monkeypatch.setattr("app.agents.recruiter_agent.run_vision_transcript", _fake)
    return type("FakeVision", (), {"seen": seen, "box": box})


def upload(client, conversation_id: int, payload: bytes, name: str, content_type: str):
    return client.post(
        f"/api/recruiter-conversations/{conversation_id}/messages/image",
        files={"file": (name, io.BytesIO(payload), content_type)},
    )


def test_screenshot_creates_messages(client, fake_vision, fake_agent, ai_key):
    conversation = new_conversation(client)
    response = upload(client, conversation["id"], make_png(), "chat.png", "image/png")
    assert response.status_code == 200

    body = response.json()
    assert body["parsed_message_count"] == 2
    assert "截图可能只包含部分对话" in body["warnings"]
    assert "顶部消息被截断" in body["warnings"]

    directions = [m["direction"] for m in body["conversation"]["messages"]]
    assert directions == ["recruiter", "user"]


def test_screenshot_mime_is_validated_by_magic_bytes(client, fake_vision, ai_key):
    """Reuses the v0.3 validator - a GIF named .png is still rejected."""
    conversation = new_conversation(client)
    response = upload(client, conversation["id"], b"GIF89a" + b"\x00" * 40, "fake.png", "image/png")

    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_image"
    assert fake_vision.seen == []


def test_oversized_screenshot_is_rejected(client, fake_vision, ai_key, monkeypatch):
    monkeypatch.setenv("QUICK_CAPTURE_MAX_IMAGE_MB", "1")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        conversation = new_conversation(client)
        response = upload(
            client, conversation["id"], make_png(padding=1024 * 1024 + 10), "big.png", "image/png"
        )
        assert response.status_code == 422
        assert response.json()["code"] == "image_too_large"
        assert fake_vision.seen == []
    finally:
        get_settings.cache_clear()


def test_screenshot_bytes_are_never_persisted(client, fake_vision, fake_agent, ai_key):
    from app.core.paths import DATA_DIR

    conversation = new_conversation(client)
    before = {p for p in DATA_DIR.rglob("*") if p.is_file()}
    upload(client, conversation["id"], make_png(), "chat.png", "image/png")
    after = {p for p in DATA_DIR.rglob("*") if p.is_file()}

    assert after == before, "conversation screenshots must not be written to disk"


def test_screenshot_without_an_api_key_explains_the_fallback(client):
    conversation = new_conversation(client)
    response = upload(client, conversation["id"], make_png(), "chat.png", "image/png")
    assert response.status_code == 503
    assert "文字粘贴" in response.json()["message"]


def test_conversation_summary_is_derived_from_the_latest_analysis(client, fake_agent, ai_key):
    conversation = new_conversation(client)
    add_text(client, conversation["id"], RECRUITER_MSG)

    detail = client.get(f"/api/recruiter-conversations/{conversation['id']}").json()
    assert detail["summary"] == "HR 询问期望薪资、到岗时间，并提出下周三面试。"
    assert detail["stage"] == "interview_scheduling"
    assert detail["action_item_count"] == 2


def test_prior_summary_is_supplied_when_appending(client, fake_agent, ai_key):
    conversation = new_conversation(client)
    add_text(client, conversation["id"], RECRUITER_MSG)
    add_text(client, conversation["id"], SECOND_MSG)

    assert fake_agent.calls[-1]["conversation_summary"], "the running summary is reused"
    assert len(fake_agent.calls[-1]["prior_messages"]) >= 1


def test_every_event_type_has_a_chinese_timeline_label():
    """The UI must never show a raw event name like ``candidate_reply``.

    Adding an EventType without a label is exactly the kind of slip this
    catches - the frontend map lives in ApplicationTimeline.tsx.
    """
    import re
    from pathlib import Path

    from app.models import EventType

    source = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "components"
        / "ApplicationTimeline.tsx"
    ).read_text(encoding="utf-8")
    labelled = set(re.findall(r"^\s{2}(\w+):\s*'", source, flags=re.MULTILINE))

    missing = [e.value for e in EventType if e.value not in labelled]
    assert not missing, f"no Chinese timeline label for: {missing}"
