"""求职任务控制台 - attention subview (M2) - service and API tests.

Covers the M2 acceptance bar: every section is a real composition of an
existing service/route (application queue, recruiter inbox, interview board,
offer board, job analysis state), counts and items are honest (never
fabricated, honest empty states), and the endpoint never calls the model,
never writes any row, and never touches ``Job.status``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import (
    ConversationStatus,
    InterviewRoundType,
    Job,
    JobAnalysis,
    JobStatus,
    Offer,
    OfferRevision,
    OfferStatus,
    RecruiterConversation,
    RecruiterMessage,
    Verdict,
)
from app.schemas.interview import ProcessCreateRequest, RoundCreateRequest
from app.schemas.offer import CompensationFields, OfferCreateRequest
from app.schemas.recruiter import ConversationCreate
from app.services import console_attention, interview_pipeline, offer_management
from app.services import recruiter_conversations as convo

from tests.test_career_analytics import make_job
from tests.test_resume_variants import apply_with

FUTURE = datetime.now(timezone.utc) + timedelta(days=3)


def make_analysis(
    db, job: Job, resume_id: int, *, score: int = 88, verdict: Verdict = Verdict.strong_apply
) -> JobAnalysis:
    analysis = JobAnalysis(
        job_id=job.id,
        resume_id=resume_id,
        model="test-model",
        prompt_version="v1",
        cache_key=f"cache-{job.id}-{score}",
        overall_score=score,
        verdict=verdict,
        result_json={"overall_score": score, "verdict": verdict.value},
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis


# --------------------------------------------------------------------------
# empty state
# --------------------------------------------------------------------------


def test_empty_state_is_honest_zero_not_fabricated(client):
    body = client.get("/api/console/attention").json()

    assert body["analysis_pending"]["count"] == 0
    assert body["analysis_pending"]["items"] == []
    assert body["queue"]["summary"]["pending"] == 0
    assert body["queue"]["items"] == []
    assert body["recruiter"]["summary"]["needs_reply"] == 0
    assert body["recruiter"]["items"] == []
    assert body["interviews"]["total"] == 0
    assert body["interviews"]["items"] == []
    assert body["offers"]["pending_count"] == 0
    assert body["offers"]["items"] == []


# --------------------------------------------------------------------------
# each section is a real composition, not invented data
# --------------------------------------------------------------------------


def test_analysis_pending_excludes_analyzed_jobs(client, db, active_resume):
    unanalyzed = make_job(db, status=JobStatus.new)
    analyzed = make_job(db, status=JobStatus.new)
    make_analysis(db, analyzed, active_resume.id)

    body = client.get("/api/console/attention").json()

    assert body["analysis_pending"]["count"] == 1
    ids = [item["id"] for item in body["analysis_pending"]["items"]]
    assert ids == [unanalyzed.id]


def test_queue_section_matches_the_application_queue_service(client, db, active_resume):
    job = make_job(db, status=JobStatus.new)
    make_analysis(db, job, active_resume.id, score=91, verdict=Verdict.strong_apply)

    body = client.get("/api/console/attention").json()

    assert body["queue"]["summary"]["pending"] == 1
    assert body["queue"]["summary"]["strong_apply"] == 1
    assert [item["job_id"] for item in body["queue"]["items"]] == [job.id]


def test_recruiter_section_lists_conversations_needing_a_reply(client, db):
    conversation = convo.create_conversation(db, ConversationCreate(company="示例公司"))
    assert conversation.status is ConversationStatus.needs_reply

    body = client.get("/api/console/attention").json()

    assert body["recruiter"]["summary"]["needs_reply"] == 1
    assert [item["id"] for item in body["recruiter"]["items"]] == [conversation.id]


def test_interview_section_reuses_the_upcoming_interviews_endpoint(db, client):
    job = make_job(db, status=JobStatus.new)
    apply_with(db, job, None)
    db.refresh(job)
    process = interview_pipeline.create_process(db, job.id, ProcessCreateRequest())
    interview_pipeline.add_round(
        db, process.id, RoundCreateRequest(round_type=InterviewRoundType.hr, scheduled_at=FUTURE)
    )

    from app.api.routes.interviews import upcoming_interviews

    direct = upcoming_interviews(db=db, limit=5)
    body = client.get("/api/console/attention").json()

    assert body["interviews"]["total"] == direct.total == 1
    assert body["interviews"]["items"][0]["process_id"] == process.id


def test_offer_section_lists_pending_offers(db, client):
    job = make_job(db, status=JobStatus.new)
    apply_with(db, job, None)
    db.refresh(job)
    offer = offer_management.create_offer(
        db,
        job.id,
        OfferCreateRequest(confirmed=True, initial=CompensationFields(base_salary_annual=300000)),
    )
    assert offer.status is OfferStatus.received

    body = client.get("/api/console/attention").json()

    assert body["offers"]["pending_count"] == 1
    assert [item["id"] for item in body["offers"]["items"]] == [offer.id]


# --------------------------------------------------------------------------
# what it must never do
# --------------------------------------------------------------------------


def test_console_attention_makes_no_openai_call(client, db, monkeypatch):
    import app.services.job_matcher as job_matcher
    import app.services.recruiter_message_analyzer as analyzer

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("console attention must never call the model")

    monkeypatch.setattr(job_matcher, "run_job_match", _explode)
    monkeypatch.setattr(analyzer, "analyze_message", _explode)

    job = make_job(db, status=JobStatus.new)
    convo.create_conversation(db, ConversationCreate(job_id=job.id))

    resp = client.get("/api/console/attention")
    assert resp.status_code == 200


def test_console_attention_writes_nothing(db, client, active_resume):
    job = make_job(db, status=JobStatus.new)
    make_analysis(db, job, active_resume.id)
    convo.create_conversation(db, ConversationCreate(company="示例公司"))

    def counts():
        return {
            "jobs": db.query(Job).count(),
            "analyses": db.query(JobAnalysis).count(),
            "conversations": db.query(RecruiterConversation).count(),
            "messages": db.query(RecruiterMessage).count(),
            "offers": db.query(Offer).count(),
            "revisions": db.query(OfferRevision).count(),
        }

    before = counts()
    before_status = db.get(Job, job.id).status

    resp = client.get("/api/console/attention")
    assert resp.status_code == 200

    db.expire_all()
    assert counts() == before
    assert db.get(Job, job.id).status == before_status


def test_build_attention_is_a_plain_read_composition(db):
    """Calling the service function directly must not require a request/AI key."""
    result = console_attention.build_attention(db)
    assert result.analysis_pending.count == 0
    assert result.queue.summary.pending == 0
    assert result.recruiter.summary.needs_reply == 0
    assert result.interviews.total == 0
    assert result.offers.pending_count == 0
