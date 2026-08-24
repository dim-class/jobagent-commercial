"""Interview analytics (v0.8).

Reuses the v0.6 statistics wholesale - these tests exist mostly to prove that,
and to pin the two distinctions that are easy to get wrong: stages come from
recorded rounds rather than ``Job.status``, and a candidate withdrawal is never
counted as an employer rejection.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import (
    FeedbackTag,
    InterviewFailureReason,
    InterviewOutcome,
    InterviewRoundType,
    JobStatus,
    WithdrawReason,
)
from app.schemas.analytics import ObservationKind, TimeWindow
from app.schemas.application import OfferRequest, RejectRequest, ResetRequest
from app.schemas.interview import ProcessWithdrawRequest, RoundCancelRequest
from app.services import application_workflow, interview_analytics, interview_pipeline
from app.services.application_analytics import AnalyticsFilters
from app.services.interview_analytics import compute_interview_analytics
from app.services.statistics import Confidence

from tests.test_career_analytics import NOW, add_event, make_job
from tests.test_interview_pipeline import FUTURE, add, complete, open_process
from tests.test_resume_variants import apply_with, make_resume


def run(db, **kwargs):
    filters = AnalyticsFilters(**kwargs) if kwargs else AnalyticsFilters()
    return compute_interview_analytics(db, filters, now=NOW)


def applied_days_ago(db, resume=None, *, days=30, **job_kwargs):
    """An application backdated so it sits inside the analytics window."""
    from app.models import ResumeUsage
    from app.schemas.application import MarkAppliedRequest

    job = make_job(db, status=JobStatus.new, **job_kwargs)
    application_workflow.mark_applied(
        db,
        job.id,
        MarkAppliedRequest(
            confirmed=True,
            applied_at=NOW - timedelta(days=days),
            resume_id=resume.id if resume else None,
            resume_usage=ResumeUsage.used if resume else ResumeUsage.unknown,
        ),
    )
    db.refresh(job)
    return job


def pipeline(db, job, *stages, **kwargs):
    """Run a job through rounds. Each stage is (round_type, outcome)."""
    process = open_process(db, job)
    last = None
    for round_type, outcome in stages:
        _, row = add(db, process, round_type, **kwargs)
        if outcome is not None:
            complete(db, row.id, outcome)
        last = row
    return interview_pipeline.get_process(db, process.id), last


# --------------------------------------------------------------------------
# empty state
# --------------------------------------------------------------------------


def test_no_applications_reports_nothing(db):
    result = run(db, window=TimeWindow.all_time)

    assert result.funnel.applications == 0
    assert result.funnel.application_to_interview.rate is None, "0/0 is unknown, not 0%"
    assert result.observations[0].kind is ObservationKind.insufficient_data


def test_applications_without_interviews_are_reported_honestly(db):
    for _ in range(3):
        applied_days_ago(db)

    result = run(db, window=TimeWindow.all_time)
    assert result.funnel.applications == 3
    assert result.funnel.reached_any_interview == 0
    assert result.funnel.application_to_interview.rate == 0.0
    assert any("还没有记录任何面试轮次" in note for note in result.notes)


# --------------------------------------------------------------------------
# the funnel
# --------------------------------------------------------------------------


def test_stages_come_from_recorded_rounds(db):
    job = applied_days_ago(db)
    pipeline(
        db,
        job,
        (InterviewRoundType.hr, InterviewOutcome.passed),
        (InterviewRoundType.technical, InterviewOutcome.passed),
        (InterviewRoundType.final, InterviewOutcome.pending),
    )

    funnel = run(db, window=TimeWindow.all_time).funnel
    assert funnel.reached_any_interview == 1
    assert funnel.reached_technical == 1
    assert funnel.reached_final == 1


def test_job_status_alone_never_creates_a_stage(db):
    """A hand-set status is not evidence that an interview happened."""
    job = applied_days_ago(db)
    job.status = JobStatus.interview
    db.commit()

    funnel = run(db, window=TimeWindow.all_time).funnel
    assert funnel.applications == 1
    assert funnel.reached_any_interview == 0, "no rounds means no interview reached"


def test_a_legacy_interview_event_is_not_counted_as_a_stage(db):
    """A generic pre-v0.8 event says nothing about which stage was reached."""
    job = applied_days_ago(db)
    add_event(db, job, __import__("app.models", fromlist=["EventType"]).EventType.interview, at=NOW)
    db.refresh(job)

    result = run(db, window=TimeWindow.all_time)
    assert result.funnel.reached_any_interview == 0
    assert result.legacy_interview_events == 1
    assert any(o.dimension == "legacy" for o in result.observations)


def test_each_stage_carries_its_own_denominator(db):
    for _ in range(4):
        applied_days_ago(db)
    for _ in range(3):
        job = applied_days_ago(db)
        pipeline(db, job, (InterviewRoundType.hr, InterviewOutcome.passed))
    for _ in range(2):
        job = applied_days_ago(db)
        pipeline(
            db,
            job,
            (InterviewRoundType.hr, InterviewOutcome.passed),
            (InterviewRoundType.technical, InterviewOutcome.passed),
        )

    stages = {s.key: s for s in run(db, window=TimeWindow.all_time).funnel.stages}
    assert stages["any_interview"].reached == 5
    assert stages["any_interview"].eligible == 9
    assert stages["technical"].reached == 2
    assert stages["technical"].eligible == 5, "technical is over those who interviewed"


def test_stage_rates_carry_wilson_intervals(db):
    for i in range(10):
        job = applied_days_ago(db)
        if i < 4:
            pipeline(db, job, (InterviewRoundType.hr, InterviewOutcome.passed))

    stat = run(db, window=TimeWindow.all_time).funnel.application_to_interview
    assert (stat.numerator, stat.denominator) == (4, 10)
    assert stat.ci_low is not None and stat.ci_high is not None
    assert stat.ci_low < (stat.rate or 0) < stat.ci_high


def test_the_offer_funnel(db):
    job = applied_days_ago(db)
    pipeline(
        db,
        job,
        (InterviewRoundType.hr, InterviewOutcome.passed),
        (InterviewRoundType.technical, InterviewOutcome.passed),
        (InterviewRoundType.final, InterviewOutcome.passed),
    )
    application_workflow.record_offer(db, job.id, OfferRequest())

    funnel = run(db, window=TimeWindow.all_time).funnel
    assert funnel.reached_final == 1
    assert funnel.offers == 1
    assert funnel.final_to_offer.rate == 1.0
    assert funnel.final_to_offer.denominator == 1


def test_first_to_next_round_counts_multi_round_processes(db):
    one_round = applied_days_ago(db)
    pipeline(db, one_round, (InterviewRoundType.hr, InterviewOutcome.failed))
    two_rounds = applied_days_ago(db)
    pipeline(
        db,
        two_rounds,
        (InterviewRoundType.hr, InterviewOutcome.passed),
        (InterviewRoundType.technical, InterviewOutcome.pending),
    )

    stat = run(db, window=TimeWindow.all_time).funnel.first_to_next_round
    assert (stat.numerator, stat.denominator) == (1, 2)


# --------------------------------------------------------------------------
# round conversion
# --------------------------------------------------------------------------


def test_round_conversion_counts_entered_passed_failed(db):
    for i in range(4):
        job = applied_days_ago(db)
        pipeline(
            db,
            job,
            (
                InterviewRoundType.technical,
                InterviewOutcome.passed if i < 3 else InterviewOutcome.failed,
            ),
        )

    rows = {r.round_type: r for r in run(db, window=TimeWindow.all_time).round_conversion}
    technical = rows["technical"]
    assert (technical.entered, technical.passed, technical.failed) == (4, 3, 1)
    assert technical.pass_rate.rate == 0.75
    assert technical.label == "技术面"


def test_a_pending_round_is_not_counted_as_a_failure(db):
    job = applied_days_ago(db)
    pipeline(
        db,
        job,
        (InterviewRoundType.hr, InterviewOutcome.passed),
    )
    other = applied_days_ago(db)
    pipeline(db, other, (InterviewRoundType.hr, InterviewOutcome.pending))

    row = {r.round_type: r for r in run(db, window=TimeWindow.all_time).round_conversion}["hr"]
    assert (row.entered, row.passed, row.failed, row.pending) == (2, 1, 0, 1)
    assert row.pass_rate.denominator == 1, "pending is excluded from the pass rate"
    assert row.pass_rate.rate == 1.0


def test_a_cancelled_round_is_counted_separately(db):
    job = applied_days_ago(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.hr)
    interview_pipeline.cancel_round(db, row.id, RoundCancelRequest())

    rows = {r.round_type: r for r in run(db, window=TimeWindow.all_time).round_conversion}
    assert rows["hr"].cancelled == 1
    assert rows["hr"].entered == 0


# --------------------------------------------------------------------------
# drop-off
# --------------------------------------------------------------------------


def test_drop_off_reports_where_candidacies_ended(db):
    for _ in range(3):
        job = applied_days_ago(db)
        pipeline(db, job, (InterviewRoundType.hr, InterviewOutcome.failed))
        application_workflow.record_rejection(db, job.id, RejectRequest())
    for _ in range(5):
        job = applied_days_ago(db)
        pipeline(
            db,
            job,
            (InterviewRoundType.hr, InterviewOutcome.passed),
            (InterviewRoundType.technical, InterviewOutcome.failed),
        )
        application_workflow.record_rejection(db, job.id, RejectRequest())

    rows = {r.round_type: r for r in run(db, window=TimeWindow.all_time).drop_off}
    assert rows["technical"].rejected_after == 5
    assert rows["hr"].rejected_after == 3
    assert run(db, window=TimeWindow.all_time).drop_off[0].round_type == "technical"


def test_a_withdrawal_is_never_counted_as_a_rejection(db):
    """The single most important distinction in this module."""
    job = applied_days_ago(db)
    process, _ = pipeline(
        db, job, (InterviewRoundType.technical, InterviewOutcome.passed)
    )
    interview_pipeline.withdraw_process(
        db,
        process.id,
        ProcessWithdrawRequest(confirmed=True, reason=WithdrawReason.accepted_other_offer),
    )

    result = run(db, window=TimeWindow.all_time)
    rows = {r.round_type: r for r in result.drop_off}
    assert rows["technical"].withdrawn_after == 1
    assert rows["technical"].rejected_after == 0
    assert result.funnel.withdrawn == 1
    assert result.funnel.rejected == 0
    assert any("主动终止" in note for note in result.notes)


def test_a_thin_drop_off_bucket_is_not_called_a_weak_point(db):
    job = applied_days_ago(db)
    pipeline(db, job, (InterviewRoundType.technical, InterviewOutcome.failed))
    application_workflow.record_rejection(db, job.id, RejectRequest())

    result = run(db, window=TimeWindow.all_time)
    notes = [o for o in result.observations if o.dimension == "drop_off"]
    assert notes and notes[0].kind is ObservationKind.insufficient_data
    assert "样本不足" in notes[0].text


# --------------------------------------------------------------------------
# latency
# --------------------------------------------------------------------------


def test_application_to_first_interview_latency(db):
    for offset in (5, 10, 15):
        job = applied_days_ago(db, days=40)
        process = open_process(db, job)
        add(
            db,
            process,
            InterviewRoundType.hr,
            scheduled_at=NOW - timedelta(days=40 - offset),
        )

    latency = run(db, window=TimeWindow.all_time).latency.application_to_first_interview
    assert latency.sample == 3
    assert latency.median_hours == pytest.approx(10 * 24, abs=1)


def test_round_to_round_latency(db):
    job = applied_days_ago(db, days=40)
    process = open_process(db, job)
    add(db, process, InterviewRoundType.hr, scheduled_at=NOW - timedelta(days=30))
    add(db, process, InterviewRoundType.technical, scheduled_at=NOW - timedelta(days=23))

    latency = run(db, window=TimeWindow.all_time).latency.first_to_second_round
    assert latency.sample == 1
    assert latency.median_hours == pytest.approx(7 * 24, abs=1)


def test_latency_reports_percentiles(db):
    for offset in (2, 4, 6, 8, 200):
        job = applied_days_ago(db, days=300)
        process = open_process(db, job)
        add(
            db,
            process,
            InterviewRoundType.hr,
            scheduled_at=NOW - timedelta(days=300) + timedelta(hours=offset),
        )

    latency = run(db, window=TimeWindow.all_time).latency.application_to_first_interview
    assert latency.median_hours == pytest.approx(6, abs=0.5)
    assert latency.p25_hours == pytest.approx(4, abs=0.5)
    assert latency.p75_hours == pytest.approx(8, abs=0.5)


def test_latency_is_empty_without_scheduled_rounds(db):
    job = applied_days_ago(db)
    pipeline(db, job, (InterviewRoundType.hr, InterviewOutcome.passed))

    latency = run(db, window=TimeWindow.all_time).latency
    assert latency.application_to_first_interview.sample == 0
    assert latency.application_to_first_interview.median_hours is None


# --------------------------------------------------------------------------
# dimensions
# --------------------------------------------------------------------------


def test_resume_variant_interview_analytics(db):
    devops = make_resume(db, variant_name="DevOps版")
    cloud = make_resume(db, variant_name="Cloud版")

    for i in range(18):
        job = applied_days_ago(db, devops)
        if i < 5:
            stages = [(InterviewRoundType.hr, InterviewOutcome.passed)]
            if i < 3:
                stages.append((InterviewRoundType.final, InterviewOutcome.passed))
            pipeline(db, job, *stages)
            if i < 1:
                application_workflow.record_offer(db, job.id, OfferRequest())
    for i in range(15):
        job = applied_days_ago(db, cloud)
        if i < 3:
            stages = [(InterviewRoundType.hr, InterviewOutcome.passed)]
            if i < 1:
                stages.append((InterviewRoundType.final, InterviewOutcome.passed))
            pipeline(db, job, *stages)

    result = run(db, window=TimeWindow.all_time)
    rows = {r.resume_id: r for r in result.by_resume}

    assert rows[devops.id].applications == 18
    assert rows[devops.id].reached_any_interview == 5
    assert rows[devops.id].reached_final == 3
    assert rows[devops.id].interview_offers == 1
    assert rows[cloud.id].reached_any_interview == 3
    assert rows[cloud.id].reached_final == 1
    assert result.by_resume[0].resume_id == devops.id


def test_resume_interview_attribution_follows_the_cycle(db):
    """Cycle 1 Cloud版 reset; cycle 2 DevOps版 gets the interview."""
    cloud = make_resume(db, variant_name="Cloud版", active=True)
    devops = make_resume(db, variant_name="DevOps版")

    job = applied_days_ago(db, cloud, days=40)
    application_workflow.reset_status(db, job.id, ResetRequest())
    apply_with(db, job, devops)
    db.refresh(job)
    pipeline(db, job, (InterviewRoundType.technical, InterviewOutcome.passed))

    result = run(db, window=TimeWindow.all_time)
    rows = {r.resume_id: r for r in result.by_resume}
    assert rows[devops.id].reached_any_interview == 1
    assert cloud.id not in rows, "the superseded cycle is not an application"


def test_city_and_role_interview_analytics(db):
    for i in range(6):
        job = applied_days_ago(db, city="杭州", title="DevOps 工程师")
        if i < 4:
            pipeline(db, job, (InterviewRoundType.hr, InterviewOutcome.passed))
    for i in range(6):
        job = applied_days_ago(db, city="北京", title="SRE 工程师")
        if i < 1:
            pipeline(db, job, (InterviewRoundType.hr, InterviewOutcome.passed))

    result = run(db, window=TimeWindow.all_time)
    cities = {c.key: c for c in result.by_city}
    roles = {c.key: c for c in result.by_role_family}

    assert cities["杭州"].interview_reach_rate.rate == pytest.approx(4 / 6, abs=0.01)
    assert cities["北京"].interview_reach_rate.rate == pytest.approx(1 / 6, abs=0.01)
    assert result.by_city[0].key == "杭州"
    assert roles["DevOps"].reached_any_interview == 4


def test_source_interview_analytics(db):
    for i in range(6):
        job = applied_days_ago(db, source="boss")
        if i < 4:
            pipeline(db, job, (InterviewRoundType.hr, InterviewOutcome.passed))

    row = run(db, window=TimeWindow.all_time).by_source[0]
    assert row.key == "boss" and row.label == "BOSS直聘"
    assert row.reached_any_interview == 4


def test_a_lucky_small_cohort_does_not_top_the_table(db):
    """Same v0.6 rule: tier first, then the conservative score."""
    for i in range(18):
        job = applied_days_ago(db, city="杭州")
        if i < 8:
            pipeline(db, job, (InterviewRoundType.hr, InterviewOutcome.passed))
    job = applied_days_ago(db, city="广州")
    pipeline(db, job, (InterviewRoundType.hr, InterviewOutcome.passed))

    result = run(db, window=TimeWindow.all_time)
    assert result.by_city[0].key == "杭州"
    guangzhou = next(c for c in result.by_city if c.key == "广州")
    assert guangzhou.interview_reach_rate.rate == 1.0, "the raw rate stays honest"
    assert guangzhou.interview_reach_rate.confidence is Confidence.insufficient


# --------------------------------------------------------------------------
# tags, reasons, coverage
# --------------------------------------------------------------------------


def test_feedback_tags_are_aggregated_only_when_the_user_chose_them(db):
    job = applied_days_ago(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.technical)
    complete(
        db,
        row.id,
        InterviewOutcome.failed,
        feedback_text="Kubernetes 深度不足，网络也不熟",
        feedback_tags=[FeedbackTag.technical_depth, FeedbackTag.cloud],
    )

    result = run(db, window=TimeWindow.all_time)
    tags = {t.key: t for t in result.feedback_tags}
    assert tags["technical_depth"].count == 1
    assert tags["technical_depth"].label == "技术深度"
    assert "kubernetes" not in {t.key.lower() for t in result.feedback_tags}, (
        "nothing is inferred from the free text"
    )


def test_failure_reasons_are_aggregated(db):
    for _ in range(2):
        job = applied_days_ago(db)
        process = open_process(db, job)
        _, row = add(db, process, InterviewRoundType.technical)
        complete(
            db,
            row.id,
            InterviewOutcome.failed,
            failure_reason=InterviewFailureReason.technical_depth,
        )

    reasons = {r.key: r for r in run(db, window=TimeWindow.all_time).failure_reasons}
    assert reasons["technical_depth"].count == 2
    assert reasons["technical_depth"].label == "技术深度不足"


def test_withdraw_reasons_are_aggregated_separately(db):
    job = applied_days_ago(db)
    process, _ = pipeline(db, job, (InterviewRoundType.hr, InterviewOutcome.passed))
    interview_pipeline.withdraw_process(
        db,
        process.id,
        ProcessWithdrawRequest(confirmed=True, reason=WithdrawReason.salary),
    )

    result = run(db, window=TimeWindow.all_time)
    assert {r.key for r in result.withdraw_reasons} == {"salary"}
    assert result.failure_reasons == []


def test_resume_attribution_coverage_is_reported(db):
    devops = make_resume(db, variant_name="DevOps版")
    for _ in range(4):
        applied_days_ago(db, devops)
    applied_days_ago(db, None)

    coverage = run(db, window=TimeWindow.all_time).resume_attribution_coverage
    assert (coverage.covered, coverage.total) == (4, 5)
    assert coverage.ratio == 0.8


def test_low_attribution_coverage_is_called_out(db):
    devops = make_resume(db, variant_name="DevOps版")
    applied_days_ago(db, devops)
    applied_days_ago(db, None)

    result = run(db, window=TimeWindow.all_time)
    notes = [o for o in result.observations if o.dimension == "resume_attribution"]
    assert notes and "没有记录使用的简历" in notes[0].text


# --------------------------------------------------------------------------
# filters, privacy, spend
# --------------------------------------------------------------------------


def test_time_windows_apply(db):
    recent = applied_days_ago(db, days=3)
    pipeline(db, recent, (InterviewRoundType.hr, InterviewOutcome.passed))
    old = applied_days_ago(db, days=200)
    pipeline(db, old, (InterviewRoundType.hr, InterviewOutcome.passed))

    assert run(db, window=TimeWindow.d7).funnel.applications == 1
    assert run(db, window=TimeWindow.all_time).funnel.applications == 2


def test_city_and_resume_filters_apply(db):
    devops = make_resume(db, variant_name="DevOps版")
    applied_days_ago(db, devops, city="杭州")
    applied_days_ago(db, devops, city="北京")
    applied_days_ago(db, None, city="杭州")

    assert run(db, window=TimeWindow.all_time, city="杭州").funnel.applications == 2
    assert (
        run(db, window=TimeWindow.all_time, resume_id=devops.id).funnel.applications == 2
    )


def test_analytics_never_exposes_a_meeting_url(db):
    """Meeting links often embed an access token; analytics is shareable."""
    job = applied_days_ago(db)
    process = open_process(db, job)
    add(
        db,
        process,
        InterviewRoundType.technical,
        scheduled_at=FUTURE,
        meeting_url="https://meet.example.com/x?token=SUPERSECRET",
        interviewer_name="张面试官",
    )

    payload = run(db, window=TimeWindow.all_time).model_dump_json()
    assert "SUPERSECRET" not in payload
    assert "meet.example.com" not in payload
    assert "张面试官" not in payload


def test_analytics_never_exposes_feedback_text(db):
    job = applied_days_ago(db)
    process = open_process(db, job)
    _, row = add(db, process, InterviewRoundType.technical)
    complete(
        db,
        row.id,
        InterviewOutcome.failed,
        feedback_text="非常私密的面试反馈原文",
    )

    payload = run(db, window=TimeWindow.all_time).model_dump_json()
    assert "非常私密的面试反馈原文" not in payload


def test_interview_analytics_is_deterministic(db):
    job = applied_days_ago(db)
    pipeline(db, job, (InterviewRoundType.hr, InterviewOutcome.passed))

    assert run(db, window=TimeWindow.all_time).model_dump_json() == run(
        db, window=TimeWindow.all_time
    ).model_dump_json()


def test_interview_analytics_makes_no_openai_call(db, monkeypatch):
    import app.services.job_matcher as job_matcher

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("interview analytics must never call the model")

    monkeypatch.setattr(job_matcher, "run_job_match", _explode)

    job = applied_days_ago(db)
    pipeline(
        db,
        job,
        (InterviewRoundType.hr, InterviewOutcome.passed),
        (InterviewRoundType.technical, InterviewOutcome.failed),
    )
    assert run(db, window=TimeWindow.all_time).funnel.reached_technical == 1
