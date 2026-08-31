"""Strategy proposals and the human-approval boundary (v0.6).

    DATA OBSERVES -> SYSTEM SUGGESTS -> HUMAN DECIDES

The tests that matter most here are the negative ones: that a small cohort
produces no recommendation, and that reading, previewing or generating a
proposal never edits ``career_strategy.yaml``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.career_strategy import load_strategy, strategy_hash
from app.models import (
    CareerStrategyChange,
    MessageDirection,
    RecommendationDecision,
    RecruiterConversation,
    RecruiterMessage,
    RecruiterMessageAnalysis,
    RecruiterSource,
    StrategyChangeSource,
    StrategyRecommendationDecision,
)
from app.schemas.analytics import ProposalType, TimeWindow
from app.services import strategy_recommendations as recs
from app.services.application_analytics import AnalyticsFilters, compute_analytics
from app.services.statistics import Confidence

from tests.test_career_analytics import NOW, applied_job, make_job


def analytics(db, **kwargs):
    filters = AnalyticsFilters(**kwargs) if kwargs else AnalyticsFilters()
    return compute_analytics(db, filters, now=NOW)


def proposals_of(db, **kwargs):
    return recs.build_proposals(analytics(db, **kwargs))


def by_type(proposals, proposal_type: ProposalType):
    return [p for p in proposals if p.type is proposal_type]


# --------------------------------------------------------------------------
# suppression - the default answer is "not enough data"
# --------------------------------------------------------------------------


def test_no_data_produces_no_proposals(db):
    assert proposals_of(db) == []


def test_a_two_of_three_cohort_never_becomes_a_recommendation(db):
    """The exact mistake this module exists to prevent."""
    for i in range(3):
        applied_job(db, days_ago=20, city="杭州", replied_after_h=6 if i < 2 else None)
    for _ in range(10):
        applied_job(db, days_ago=20, city="北京")

    proposals = proposals_of(db)
    assert by_type(proposals, ProposalType.increase_city_priority) == []


def test_a_thin_but_promising_cohort_becomes_a_collect_more_data_note(db):
    for i in range(3):
        applied_job(db, days_ago=20, city="杭州", replied_after_h=6 if i < 2 else None)
    for _ in range(10):
        applied_job(db, days_ago=20, city="北京")

    notes = by_type(proposals_of(db), ProposalType.collect_more_data)
    assert any(n.target == "杭州" for n in notes)
    note = next(n for n in notes if n.target == "杭州")
    assert note.applicable is False
    assert "还不足以作为调整依据" in note.reason


def test_a_cohort_below_the_recommend_sample_is_never_actionable(db):
    """A perfect 7/7 still says nothing: 7 < ANALYTICS_RECOMMEND_SAMPLE."""
    for _ in range(7):
        applied_job(db, days_ago=20, city="杭州", replied_after_h=6)
    for _ in range(12):
        applied_job(db, days_ago=20, city="北京")

    proposals = proposals_of(db)
    assert [p.target for p in proposals if p.applicable] == ["北京"], (
        "北京 has 12 mature samples and legitimately qualifies; 杭州 does not"
    )
    assert [p.target for p in by_type(proposals, ProposalType.increase_city_priority)] == []


# --------------------------------------------------------------------------
# generation - when the evidence really is there
# --------------------------------------------------------------------------


def strong_hangzhou(db, *, now=None) -> None:
    """A cohort that genuinely clears every bar.

    ``now`` lets the HTTP tests anchor the history to the real clock; the unit
    tests here keep the frozen ``NOW`` they also pass into ``compute_analytics``.
    """
    extra = {} if now is None else {"now": now}
    for _ in range(12):
        applied_job(db, days_ago=20, city="杭州", replied_after_h=6, **extra)
    for _ in range(20):
        applied_job(db, days_ago=20, city="北京", **extra)


def test_a_well_evidenced_city_produces_an_actionable_proposal(db):
    strong_hangzhou(db)

    proposals = by_type(proposals_of(db), ProposalType.increase_city_priority)
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.target == "杭州"
    assert proposal.applicable is True
    assert proposal.sample_size == 12
    assert proposal.confidence is Confidence.moderate
    assert proposal.suggested_value[0] == "杭州", "the suggested order leads with it"
    assert "12/12" in proposal.reason


def test_a_proposal_always_carries_its_evidence(db):
    strong_hangzhou(db)

    proposal = by_type(proposals_of(db), ProposalType.increase_city_priority)[0]
    evidence = proposal.evidence
    assert evidence.metric == "mature_reply_rate"
    assert (evidence.numerator, evidence.denominator) == (12, 12)
    assert evidence.comparison_rate is not None
    assert evidence.ci_low is not None and evidence.ci_high is not None
    assert evidence.window is TimeWindow.d30


def test_a_clearly_underperforming_city_is_flagged_too(db):
    for _ in range(20):
        applied_job(db, days_ago=20, city="北京", replied_after_h=6)
    for _ in range(12):
        applied_job(db, days_ago=20, city="上海")

    proposals = by_type(proposals_of(db), ProposalType.decrease_city_priority)
    assert [p.target for p in proposals] == ["上海"]
    assert proposals[0].suggested_value[-1] == "上海", "demoted, never deleted"


def test_a_city_outside_the_strategy_is_not_reordered(db):
    """成都 is not in target_cities - reordering it would be meaningless."""
    for _ in range(12):
        applied_job(db, days_ago=20, city="成都", replied_after_h=6)
    for _ in range(20):
        applied_job(db, days_ago=20, city="北京")

    assert by_type(proposals_of(db), ProposalType.increase_city_priority) == []


def test_role_and_source_proposals_are_advisory_only(db):
    for _ in range(12):
        applied_job(db, days_ago=20, title="SRE 工程师", source="boss", replied_after_h=6)
    for _ in range(20):
        applied_job(db, days_ago=20, title="桌面运维工程师", source="manual")

    proposals = proposals_of(db)
    advisory = by_type(proposals, ProposalType.increase_role_priority) + by_type(
        proposals, ProposalType.prioritize_source
    )
    assert advisory, "the observation is still reported"
    assert all(p.applicable is False for p in advisory), "but nothing auto-edits a role list"


def test_a_skill_needs_three_appearances_to_be_named(db, active_resume):
    from tests.test_career_analytics import add_analysis

    for _ in range(3):
        job = applied_job(db, days_ago=20)
        add_analysis(db, job, active_resume, score=88, missing=["Go"])
    job = applied_job(db, days_ago=20)
    add_analysis(db, job, active_resume, score=88, missing=["Rust"])

    proposals = by_type(proposals_of(db), ProposalType.skill_learning_candidate)
    assert [p.target for p in proposals] == ["Go"]
    assert "并不意味着补上这项技能就一定会带来面试" in proposals[0].impact_description


def test_no_proposal_ever_claims_causality(db):
    strong_hangzhou(db)

    for proposal in proposals_of(db):
        text = proposal.reason + proposal.impact_description
        assert "因为" not in text and "导致" not in text


# --------------------------------------------------------------------------
# signatures and dismissal
# --------------------------------------------------------------------------


def test_the_same_evidence_yields_the_same_signature(db):
    strong_hangzhou(db)

    first = by_type(proposals_of(db), ProposalType.increase_city_priority)[0]
    second = by_type(proposals_of(db), ProposalType.increase_city_priority)[0]
    assert first.signature == second.signature


def test_one_more_application_does_not_resurrect_a_dismissal(db):
    strong_hangzhou(db)
    before = by_type(proposals_of(db), ProposalType.increase_city_priority)[0].signature

    applied_job(db, days_ago=20, city="杭州", replied_after_h=6)
    after = by_type(proposals_of(db), ProposalType.increase_city_priority)[0].signature
    assert before == after


def test_substantially_new_evidence_produces_a_new_signature(db):
    strong_hangzhou(db)
    before = by_type(proposals_of(db), ProposalType.increase_city_priority)[0].signature

    for _ in range(5):
        applied_job(db, days_ago=20, city="杭州", replied_after_h=6)
    after = by_type(proposals_of(db), ProposalType.increase_city_priority)[0].signature
    assert before != after, "a new evidence bucket deserves to be asked again"


def test_a_dismissed_proposal_is_hidden_but_counted(db):
    strong_hangzhou(db)
    result = analytics(db)
    proposal = by_type(recs.build_proposals(result), ProposalType.increase_city_priority)[0]

    recs.record_decision(db, proposal.signature, RecommendationDecision.dismissed)

    visible, hidden = recs.visible_proposals(db, result)
    assert proposal.signature not in {p.signature for p in visible}
    assert hidden >= 1


def test_dismissing_does_not_touch_the_strategy(db):
    strong_hangzhou(db)
    result = analytics(db)
    proposal = by_type(recs.build_proposals(result), ProposalType.increase_city_priority)[0]

    before = strategy_hash(load_strategy())
    recs.record_decision(db, proposal.signature, RecommendationDecision.dismissed)
    assert strategy_hash(load_strategy(force=True)) == before


# --------------------------------------------------------------------------
# the approval boundary
# --------------------------------------------------------------------------


def test_generating_proposals_never_edits_the_strategy(db):
    strong_hangzhou(db)
    before = strategy_hash(load_strategy())

    proposals = proposals_of(db)
    assert proposals, "there is something to propose"
    assert strategy_hash(load_strategy(force=True)) == before, "reading changed nothing"
    assert db.query(CareerStrategyChange).count() == 0


def test_the_diff_preview_writes_nothing(db):
    strong_hangzhou(db)
    result = analytics(db)
    proposal = by_type(recs.build_proposals(result), ProposalType.increase_city_priority)[0]
    before = load_strategy()

    diff = recs.build_diff(proposal)
    assert len(diff) == 1
    assert diff[0].field == "target_cities"
    assert diff[0].before == before["target_cities"]
    assert diff[0].after[0] == "杭州"
    assert load_strategy(force=True)["target_cities"] == before["target_cities"]


def test_applying_writes_the_strategy_and_an_audit_row(db):
    strong_hangzhou(db)
    result = analytics(db)
    proposal = by_type(recs.build_proposals(result), ProposalType.increase_city_priority)[0]
    before = load_strategy()

    after = recs.apply_proposal(db, proposal, note="试一个月")

    assert after["target_cities"][0] == "杭州"
    assert load_strategy(force=True)["target_cities"][0] == "杭州"
    assert set(after["target_cities"]) == set(before["target_cities"]), "no city was dropped"

    change = db.query(CareerStrategyChange).one()
    assert change.source is StrategyChangeSource.analytics_recommendation
    assert change.recommendation_signature == proposal.signature
    assert change.before_hash != change.after_hash
    assert change.before_json["target_cities"] == before["target_cities"]
    assert change.notes == "试一个月"


def test_applying_records_the_decision_as_accepted(db):
    strong_hangzhou(db)
    result = analytics(db)
    proposal = by_type(recs.build_proposals(result), ProposalType.increase_city_priority)[0]

    recs.apply_proposal(db, proposal)

    row = db.query(StrategyRecommendationDecision).one()
    assert row.signature == proposal.signature
    assert row.decision is RecommendationDecision.accepted


def test_an_advisory_proposal_cannot_be_applied(db):
    from app.core.errors import ValidationError

    for _ in range(12):
        applied_job(db, days_ago=20, title="SRE 工程师", replied_after_h=6)
    for _ in range(20):
        applied_job(db, days_ago=20, title="桌面运维工程师")

    advisory = by_type(proposals_of(db), ProposalType.increase_role_priority)
    assert advisory

    try:
        recs.apply_proposal(db, advisory[0])
    except ValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("an advisory proposal must not be applicable")


def test_the_audit_trail_keeps_every_change(db):
    strong_hangzhou(db)
    result = analytics(db)
    proposal = by_type(recs.build_proposals(result), ProposalType.increase_city_priority)[0]

    recs.apply_proposal(db, proposal)
    recs.apply_proposal(db, proposal)

    assert len(recs.strategy_changes(db)) == 2, "history is appended, never replaced"


# --------------------------------------------------------------------------
# recruiter insights feed the analytics page
# --------------------------------------------------------------------------


def add_recruiter_analysis(db, job, *, requests, sentiment="positive", stage="screening"):
    add_recruiter_analysis.counter = getattr(add_recruiter_analysis, "counter", 0) + 1
    n = add_recruiter_analysis.counter
    conversation = RecruiterConversation(
        job_id=job.id if job else None,
        source=RecruiterSource.boss,
        recruiter_name="HR",
        company="示例科技",
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)

    message = RecruiterMessage(
        conversation_id=conversation.id,
        direction=MessageDirection.recruiter,
        raw_text="您好，方便聊聊吗？",
        content_hash=f"msg-{n:06d}",
        captured_at=NOW,
        created_at=NOW,
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    db.add(
        RecruiterMessageAnalysis(
            message_id=message.id,
            job_id=job.id if job else None,
            model="test-model-fast",
            prompt_version="test",
            cache_key=f"rcache-{n:06d}",
            result_json={
                "recruiter_requests": [{"type": r} for r in requests],
                "sentiment": sentiment,
                "conversation_stage": stage,
            },
            created_at=NOW,
        )
    )
    db.commit()
    return conversation


def test_recruiter_questions_are_counted_and_labelled(db):
    job = applied_job(db, days_ago=20, replied_after_h=6)
    add_recruiter_analysis(db, job, requests=["expected_salary", "interview_availability"])
    add_recruiter_analysis(db, job, requests=["expected_salary"])

    recruiter = analytics(db).recruiter
    top = recruiter.requests[0]
    assert (top.key, top.label, top.count) == ("expected_salary", "期望薪资", 2)
    assert recruiter.sentiment[0].label == "积极"
    assert recruiter.stage[0].label == "初步筛选"


def test_unanalyzed_recruiter_messages_lower_the_coverage(db):
    job = applied_job(db, days_ago=20, replied_after_h=6)
    add_recruiter_analysis(db, job, requests=["expected_salary"])

    conversation = RecruiterConversation(job_id=job.id, source=RecruiterSource.boss)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    db.add(
        RecruiterMessage(
            conversation_id=conversation.id,
            direction=MessageDirection.recruiter,
            raw_text="未分析的消息",
            content_hash="msg-unanalyzed",
            captured_at=NOW,
            created_at=NOW,
        )
    )
    db.commit()

    coverage = analytics(db).recruiter.analysis_coverage
    assert (coverage.covered, coverage.total) == (1, 2)
    assert coverage.ratio == 0.5


def test_conversation_coverage_counts_linked_applications(db):
    linked = applied_job(db, days_ago=20, replied_after_h=6)
    applied_job(db, days_ago=20)
    add_recruiter_analysis(db, linked, requests=["expected_salary"])

    coverage = analytics(db).data_quality.conversation_coverage
    assert (coverage.covered, coverage.total) == (1, 2)
