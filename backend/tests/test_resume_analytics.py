"""Resume variant analytics (v0.7).

Same statistics as v0.6 - deliberately. These tests check that the resume
dimension reuses the shared maturity/Wilson/confidence machinery rather than
inventing a second, friendlier definition of "better".
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import EventType, JobStatus, ResumeUsage
from app.schemas.analytics import TimeWindow
from app.services import resume_analytics, resume_variants
from app.services.application_analytics import AnalyticsFilters
from app.services.resume_analytics import (
    UNKNOWN_KEY,
    compute_resume_analytics,
    rankable,
)
from app.services.statistics import Confidence

from tests.test_career_analytics import (
    NOW,
    add_analysis,
    add_event,
    applied_job,
    make_job,
)
from tests.test_resume_variants import apply_with, make_resume


def run(db, **kwargs):
    filters = AnalyticsFilters(**kwargs) if kwargs else AnalyticsFilters()
    return compute_resume_analytics(db, filters, now=NOW)


def cohort(result, resume_id: int):
    return next((c for c in result.by_resume if c.resume_id == resume_id), None)


def applied_with_resume(
    db,
    resume,
    *,
    days_ago: float = 20,
    replied: bool = False,
    interviewed: bool = False,
    offered: bool = False,
    **job_kwargs,
):
    """One application attributed to a variant, with its outcome."""
    job = make_job(db, status=JobStatus.new, **job_kwargs)
    applied_at = NOW - timedelta(days=days_ago)

    from app.schemas.application import MarkAppliedRequest
    from app.services import application_workflow

    application_workflow.mark_applied(
        db,
        job.id,
        MarkAppliedRequest(
            confirmed=True,
            applied_at=applied_at,
            resume_id=resume.id if resume else None,
            resume_usage=ResumeUsage.used if resume else ResumeUsage.unknown,
        ),
    )
    db.refresh(job)
    if replied:
        add_event(db, job, EventType.replied, at=applied_at + timedelta(hours=8))
    if interviewed:
        add_event(db, job, EventType.interview, at=applied_at + timedelta(hours=96))
    if offered:
        add_event(db, job, EventType.offer, at=applied_at + timedelta(hours=300))
    db.refresh(job)
    return job


def seed_cohort(db, resume, *, applications: int, replies: int, interviews: int, **kw):
    for i in range(applications):
        applied_with_resume(
            db,
            resume,
            replied=i < replies,
            interviewed=i < interviews,
            **kw,
        )


# --------------------------------------------------------------------------
# empty / degenerate
# --------------------------------------------------------------------------


def test_no_applications_produces_no_variant_rows(db):
    make_resume(db, variant_name="Cloud版")
    result = run(db)

    assert result.by_resume == []
    assert result.summary.applications == 0
    assert result.attribution_coverage.total == 0
    assert result.attribution_coverage.ratio is None


def test_the_observational_warning_is_always_present(db):
    result = run(db)
    assert "观察性数据" in result.observational_warning
    assert "A/B" in result.observational_warning


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------


def test_each_variant_is_counted_separately(db):
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")

    seed_cohort(db, devops, applications=18, replies=8, interviews=5)
    seed_cohort(db, cloud, applications=15, replies=5, interviews=3)

    result = run(db, window=TimeWindow.all_time)

    d = cohort(result, devops.id)
    c = cohort(result, cloud.id)
    assert (d.applications, d.replies, d.interviews) == (18, 8, 5)
    assert (c.applications, c.replies, c.interviews) == (15, 5, 3)
    assert d.label == "DevOps版"


def test_variant_rates_use_the_shared_maturity_rules(db):
    devops = make_resume(db, variant_name="DevOps版")
    seed_cohort(db, devops, applications=10, replies=4, interviews=0)
    # Applied this morning: counted, but not yet a fair test.
    applied_with_resume(db, devops, days_ago=0.1)

    result = run(db, window=TimeWindow.all_time)
    stat = cohort(result, devops.id)

    assert stat.applications == 11
    assert stat.mature_applications == 10, "today's application is not yet mature"
    assert stat.mature_reply_rate.denominator == 10
    assert stat.mature_reply_rate.rate == 0.4


def test_variant_rates_carry_wilson_intervals(db):
    devops = make_resume(db, variant_name="DevOps版")
    seed_cohort(db, devops, applications=18, replies=8, interviews=0)

    stat = cohort(run(db, window=TimeWindow.all_time), devops.id).mature_reply_rate
    assert (stat.numerator, stat.denominator) == (8, 18)
    assert stat.ci_low is not None and stat.ci_high is not None
    assert stat.ci_low < (stat.rate or 0) < stat.ci_high


def test_offers_are_attributed_to_the_right_variant(db):
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")

    applied_with_resume(db, devops, replied=True, interviewed=True, offered=True)
    applied_with_resume(db, cloud)

    result = run(db, window=TimeWindow.all_time)
    assert cohort(result, devops.id).offers == 1
    assert cohort(result, cloud.id).offers == 0


def test_a_variant_carries_its_archived_and_active_flags(db):
    active = make_resume(db, variant_name="Cloud版", active=True)
    retired = make_resume(db, variant_name="旧版")
    applied_with_resume(db, active)
    applied_with_resume(db, retired)
    resume_variants.archive(db, retired.id)

    result = run(db, window=TimeWindow.all_time)
    assert cohort(result, active.id).is_active_analysis_resume is True
    assert cohort(result, retired.id).archived is True


# --------------------------------------------------------------------------
# ranking - the v0.6 rule, unchanged
# --------------------------------------------------------------------------


def test_a_lucky_small_variant_does_not_become_the_winner(db):
    """Infra版 2/2 = 100% must not outrank DevOps版 8/18."""
    devops = make_resume(db, variant_name="DevOps版")
    cloud = make_resume(db, variant_name="Cloud版")
    infra = make_resume(db, variant_name="Infra版")

    seed_cohort(db, devops, applications=18, replies=8, interviews=5)
    seed_cohort(db, cloud, applications=15, replies=5, interviews=3)
    seed_cohort(db, infra, applications=2, replies=2, interviews=1)

    result = run(db, window=TimeWindow.all_time)

    assert result.by_resume[0].resume_id == devops.id
    assert result.by_resume[-1].resume_id == infra.id

    infra_stat = cohort(result, infra.id).mature_reply_rate
    assert infra_stat.rate == 1.0, "the raw rate is still reported honestly"
    assert infra_stat.confidence is Confidence.insufficient


def test_a_one_of_one_variant_never_tops_the_table(db):
    devops = make_resume(db, variant_name="DevOps版")
    lucky = make_resume(db, variant_name="试验版")

    seed_cohort(db, devops, applications=18, replies=8, interviews=0)
    applied_with_resume(db, lucky, replied=True)

    result = run(db, window=TimeWindow.all_time)
    assert result.by_resume[0].resume_id == devops.id


def test_a_thin_variant_is_named_rather_than_hidden(db):
    devops = make_resume(db, variant_name="DevOps版")
    infra = make_resume(db, variant_name="Infra版")
    seed_cohort(db, devops, applications=18, replies=8, interviews=0)
    seed_cohort(db, infra, applications=4, replies=1, interviews=0)

    result = run(db, window=TimeWindow.all_time)
    notes = [o for o in result.observations if o.target == "Infra版"]
    assert notes and "暂不足以下结论" in notes[0].text


# --------------------------------------------------------------------------
# attribution coverage
# --------------------------------------------------------------------------


def test_unattributed_applications_are_reported_separately(db):
    devops = make_resume(db, variant_name="DevOps版")
    seed_cohort(db, devops, applications=8, replies=3, interviews=0)
    for _ in range(2):
        applied_with_resume(db, None)

    result = run(db, window=TimeWindow.all_time)

    assert result.unattributed is not None
    assert result.unattributed.key == UNKNOWN_KEY
    assert result.unattributed.applications == 2
    assert result.unattributed.resume_id is None
    assert [c.resume_id for c in result.by_resume] == [devops.id]


def test_unattributed_applications_still_count_in_the_overall_total(db):
    devops = make_resume(db, variant_name="DevOps版")
    seed_cohort(db, devops, applications=8, replies=3, interviews=0)
    for _ in range(2):
        applied_with_resume(db, None, replied=True)

    result = run(db, window=TimeWindow.all_time)
    assert result.summary.applications == 10, "overall metrics include them"
    assert result.summary.replies == 5
    assert cohort(result, devops.id).applications == 8, "but no variant claims them"


def test_attribution_coverage_is_reported(db):
    devops = make_resume(db, variant_name="DevOps版")
    seed_cohort(db, devops, applications=8, replies=0, interviews=0)
    for _ in range(2):
        applied_with_resume(db, None)

    coverage = run(db, window=TimeWindow.all_time).attribution_coverage
    assert (coverage.covered, coverage.total) == (8, 10)
    assert coverage.ratio == 0.8


def test_low_coverage_produces_a_warning_observation(db):
    devops = make_resume(db, variant_name="DevOps版")
    seed_cohort(db, devops, applications=8, replies=3, interviews=0)
    for _ in range(2):
        applied_with_resume(db, None)

    result = run(db, window=TimeWindow.all_time)
    warnings = [o for o in result.observations if o.dimension == "resume_attribution"]
    assert warnings and "没有记录实际使用的简历" in warnings[0].text


def test_full_coverage_produces_no_warning(db):
    devops = make_resume(db, variant_name="DevOps版")
    seed_cohort(db, devops, applications=8, replies=3, interviews=0)

    result = run(db, window=TimeWindow.all_time)
    assert not [o for o in result.observations if o.dimension == "resume_attribution"]


def test_variants_are_not_ranked_when_attribution_is_mostly_missing(db):
    """Half the data missing means the comparison is not worth trusting."""
    devops = make_resume(db, variant_name="DevOps版")
    cloud = make_resume(db, variant_name="Cloud版")
    seed_cohort(db, devops, applications=8, replies=5, interviews=0)
    seed_cohort(db, cloud, applications=8, replies=2, interviews=0)
    for _ in range(20):
        applied_with_resume(db, None)

    result = run(db, window=TimeWindow.all_time)
    assert (result.attribution_coverage.ratio or 0) < 0.6
    assert rankable(result) is False


def test_variants_are_rankable_with_good_coverage_and_two_real_cohorts(db):
    devops = make_resume(db, variant_name="DevOps版")
    cloud = make_resume(db, variant_name="Cloud版")
    seed_cohort(db, devops, applications=18, replies=8, interviews=0)
    seed_cohort(db, cloud, applications=15, replies=5, interviews=0)

    assert rankable(run(db, window=TimeWindow.all_time)) is True


def test_one_variant_alone_is_not_rankable(db):
    devops = make_resume(db, variant_name="DevOps版")
    seed_cohort(db, devops, applications=18, replies=8, interviews=0)

    assert rankable(run(db, window=TimeWindow.all_time)) is False, "nothing to compare against"


# --------------------------------------------------------------------------
# like-for-like breakdowns
# --------------------------------------------------------------------------


def test_resume_by_role_compares_within_the_same_kind_of_job(db):
    devops = make_resume(db, variant_name="DevOps版")
    cloud = make_resume(db, variant_name="Cloud版")

    seed_cohort(
        db, devops, applications=6, replies=4, interviews=0, title="DevOps 工程师"
    )
    seed_cohort(db, cloud, applications=6, replies=1, interviews=0, title="DevOps 工程师")

    result = run(db, window=TimeWindow.all_time)
    rows = [r for r in result.by_resume_role if r.dimension_key == "DevOps"]
    assert rows, "a DevOps-roles row exists"
    assert {c.resume_id for c in rows[0].resumes} == {devops.id, cloud.id}
    assert rows[0].resumes[0].resume_id == devops.id


def test_a_slice_with_only_one_variant_is_not_a_comparison(db):
    devops = make_resume(db, variant_name="DevOps版")
    seed_cohort(db, devops, applications=6, replies=4, interviews=0, title="DevOps 工程师")

    result = run(db, window=TimeWindow.all_time)
    assert result.by_resume_role == []


def test_thin_slices_are_dropped_rather_than_shown(db):
    devops = make_resume(db, variant_name="DevOps版")
    cloud = make_resume(db, variant_name="Cloud版")
    applied_with_resume(db, devops, title="SRE 工程师")
    applied_with_resume(db, cloud, title="SRE 工程师")

    result = run(db, window=TimeWindow.all_time)
    assert not [r for r in result.by_resume_role if r.dimension_key == "SRE"]


def test_resume_by_city_works_the_same_way(db):
    devops = make_resume(db, variant_name="DevOps版")
    cloud = make_resume(db, variant_name="Cloud版")
    seed_cohort(db, devops, applications=5, replies=4, interviews=0, city="杭州")
    seed_cohort(db, cloud, applications=5, replies=1, interviews=0, city="杭州")

    rows = [r for r in run(db, window=TimeWindow.all_time).by_resume_city]
    assert rows and rows[0].dimension_label == "杭州"
    assert rows[0].resumes[0].resume_id == devops.id


# --------------------------------------------------------------------------
# filters
# --------------------------------------------------------------------------


def test_time_windows_apply_to_resume_analytics(db):
    devops = make_resume(db, variant_name="DevOps版")
    applied_with_resume(db, devops, days_ago=3)
    applied_with_resume(db, devops, days_ago=60)

    assert run(db, window=TimeWindow.d7).summary.applications == 1
    assert run(db, window=TimeWindow.all_time).summary.applications == 2


def test_city_and_role_filters_apply(db):
    devops = make_resume(db, variant_name="DevOps版")
    applied_with_resume(db, devops, city="杭州", title="DevOps 工程师")
    applied_with_resume(db, devops, city="北京", title="DevOps 工程师")
    applied_with_resume(db, devops, city="杭州", title="SRE 工程师")

    assert run(db, window=TimeWindow.all_time, city="杭州").summary.applications == 2
    assert (
        run(db, window=TimeWindow.all_time, city="杭州", role_family="SRE").summary.applications
        == 1
    )


def test_filtering_by_resume_narrows_to_one_variant(db):
    devops = make_resume(db, variant_name="DevOps版")
    cloud = make_resume(db, variant_name="Cloud版")
    seed_cohort(db, devops, applications=3, replies=0, interviews=0)
    seed_cohort(db, cloud, applications=5, replies=0, interviews=0)

    result = run(db, window=TimeWindow.all_time, resume_id=cloud.id)
    assert result.summary.applications == 5
    assert [c.resume_id for c in result.by_resume] == [cloud.id]


# --------------------------------------------------------------------------
# AI fit vs real outcome
# --------------------------------------------------------------------------


def test_the_fit_matrix_uses_only_cached_analyses(db):
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")
    job = make_job(db, status=JobStatus.new, company="杭州公司", title="SRE 工程师")

    add_analysis(db, job, cloud, score=88)
    add_analysis(db, job, devops, score=92)

    fit = run(db, window=TimeWindow.all_time).fit_comparison
    assert fit.comparable_jobs == 1
    assert fit.resumes[0].resume_id == devops.id
    assert fit.resumes[0].average_score == 92.0
    assert len(fit.matrix) == 1
    assert {c.resume_id for c in fit.matrix[0].cells} == {cloud.id, devops.id}


def test_a_job_analyzed_with_one_variant_is_not_comparable(db):
    cloud = make_resume(db, variant_name="Cloud版")
    job = make_job(db, status=JobStatus.new)
    add_analysis(db, job, cloud, score=88)

    fit = run(db, window=TimeWindow.all_time).fit_comparison
    assert fit.comparable_jobs == 0
    assert fit.matrix == []


def test_missing_matrix_cells_stay_empty(db):
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")
    infra = make_resume(db, variant_name="Infra版")
    job = make_job(db, status=JobStatus.new)
    add_analysis(db, job, cloud, score=88)
    add_analysis(db, job, devops, score=92)
    other = make_job(db, status=JobStatus.new)
    add_analysis(db, other, infra, score=70)
    add_analysis(db, other, cloud, score=60)

    fit = run(db, window=TimeWindow.all_time).fit_comparison
    row = next(r for r in fit.matrix if r.job_id == job.id)
    infra_cell = next((c for c in row.cells if c.resume_id == infra.id), None)
    assert infra_cell is None or infra_cell.score is None, "no cell is invented"


def test_ai_fit_and_real_outcome_are_separate_numbers(db):
    """A variant can score higher and still convert worse."""
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")

    # AI thinks Cloud版 fits better.
    fit_job = make_job(db, status=JobStatus.new)
    add_analysis(db, fit_job, cloud, score=95)
    add_analysis(db, fit_job, devops, score=70)

    # Reality says otherwise.
    seed_cohort(db, devops, applications=18, replies=9, interviews=0)
    seed_cohort(db, cloud, applications=18, replies=2, interviews=0)

    result = run(db, window=TimeWindow.all_time)
    assert result.fit_comparison.resumes[0].resume_id == cloud.id, "AI prefers Cloud版"
    assert result.by_resume[0].resume_id == devops.id, "recruiters preferred DevOps版"


def test_the_fit_observation_says_it_is_not_recruiter_feedback(db):
    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")
    for _ in range(2):
        job = make_job(db, status=JobStatus.new)
        add_analysis(db, job, cloud, score=90)
        add_analysis(db, job, devops, score=80)

    notes = [o for o in run(db, window=TimeWindow.all_time).observations if o.dimension == "resume_fit"]
    assert notes
    assert "不代表 HR 的真实反馈" in notes[0].text


# --------------------------------------------------------------------------
# determinism / spend
# --------------------------------------------------------------------------


def test_resume_analytics_is_deterministic(db):
    devops = make_resume(db, variant_name="DevOps版")
    seed_cohort(db, devops, applications=6, replies=3, interviews=1)

    assert run(db).model_dump_json() == run(db).model_dump_json()


def test_resume_analytics_makes_no_openai_call(db, monkeypatch):
    import app.services.job_matcher as job_matcher

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("resume analytics must never call the model")

    monkeypatch.setattr(job_matcher, "run_job_match", _explode)

    cloud = make_resume(db, variant_name="Cloud版")
    devops = make_resume(db, variant_name="DevOps版")
    seed_cohort(db, devops, applications=6, replies=3, interviews=1)
    job = make_job(db, status=JobStatus.new)
    add_analysis(db, job, cloud, score=88)
    add_analysis(db, job, devops, score=92)

    result = run(db, window=TimeWindow.all_time)
    assert result.by_resume and result.fit_comparison.comparable_jobs == 1


def test_no_observation_claims_a_variant_caused_anything(db):
    devops = make_resume(db, variant_name="DevOps版")
    cloud = make_resume(db, variant_name="Cloud版")
    seed_cohort(db, devops, applications=18, replies=9, interviews=0, title="DevOps 工程师")
    seed_cohort(db, cloud, applications=18, replies=2, interviews=0, title="DevOps 工程师")

    for observation in run(db, window=TimeWindow.all_time).observations:
        assert "因为" not in observation.text
        assert "导致" not in observation.text
        assert "使得" not in observation.text
