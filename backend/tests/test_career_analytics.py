"""Career analytics over real rows (v0.6).

Everything here is deterministic. No test in this file calls OpenAI, and the
analytics engine has no code path that could - that is the point of the phase.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.models import (
    ApplicationEvent,
    EventType,
    Job,
    JobAnalysis,
    JobStatus,
    Verdict,
)
from app.schemas.analytics import ObservationKind, TimeWindow
from app.services.application_analytics import (
    AnalyticsFilters,
    build_records,
    compute_analytics,
)
from app.services.statistics import Confidence

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# fixtures / builders
# --------------------------------------------------------------------------


def make_job(
    db,
    *,
    title: str = "云计算工程师",
    company: str = "示例科技",
    city: str | None = "北京",
    source: str = "manual",
    salary_text: str | None = "20k-30k",
    status: JobStatus = JobStatus.applied,
    seq: int | None = None,
) -> Job:
    """A job row. ``content_hash`` is unique per call so dedup never fires."""
    make_job.counter = getattr(make_job, "counter", 0) + 1
    n = seq if seq is not None else make_job.counter
    job = Job(
        source=source,
        external_id=f"ext-{n}",
        company=company,
        title=title,
        city=city,
        salary_text=salary_text,
        raw_description="岗位职责：负责云平台运维。",
        normalized_description="岗位职责：负责云平台运维。",
        content_hash=f"hash-{n:06d}",
        status=status,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def add_event(db, job: Job, event_type: EventType, *, at: datetime) -> ApplicationEvent:
    event = ApplicationEvent(job_id=job.id, event_type=event_type, created_at=at, metadata_json={})
    db.add(event)
    db.commit()
    return event


def add_analysis(
    db,
    job: Job,
    resume,
    *,
    score: int = 85,
    verdict: Verdict = Verdict.apply,
    matched: list[str] | None = None,
    missing: list[str] | None = None,
) -> JobAnalysis:
    add_analysis.counter = getattr(add_analysis, "counter", 0) + 1
    analysis = JobAnalysis(
        job_id=job.id,
        resume_id=resume.id,
        model="test-model-fast",
        prompt_version="test",
        cache_key=f"cache-{add_analysis.counter:06d}",
        overall_score=score,
        verdict=verdict,
        result_json={
            "overall_score": score,
            "verdict": verdict.value,
            "matched_skills": matched or [],
            "missing_skills": missing or [],
        },
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis


def applied_job(
    db,
    *,
    days_ago: float,
    replied_after_h: float | None = None,
    interview_after_h: float | None = None,
    offer_after_h: float | None = None,
    rejected_after_h: float | None = None,
    **job_kwargs,
) -> Job:
    """A job with a complete, coherent event history."""
    job = make_job(db, **job_kwargs)
    applied_at = NOW - timedelta(days=days_ago)
    add_event(db, job, EventType.applied, at=applied_at)
    for hours, kind in (
        (replied_after_h, EventType.replied),
        (interview_after_h, EventType.interview),
        (offer_after_h, EventType.offer),
        (rejected_after_h, EventType.rejected),
    ):
        if hours is not None:
            add_event(db, job, kind, at=applied_at + timedelta(hours=hours))
    db.refresh(job)
    return job


def cohort_by_key(cohorts, key: str):
    for cohort in cohorts:
        if cohort.key == key:
            return cohort
    return None


def run(db, **kwargs):
    filters = AnalyticsFilters(**kwargs) if kwargs else AnalyticsFilters()
    return compute_analytics(db, filters, now=NOW)


# --------------------------------------------------------------------------
# empty state
# --------------------------------------------------------------------------


def test_no_applications_reports_nothing_rather_than_zero_percent(db):
    result = run(db)

    assert result.summary.applications == 0
    assert result.summary.mature_reply_rate.rate is None, "0/0 is unknown, not 0%"
    assert result.summary.mature_reply_rate.confidence is Confidence.insufficient
    assert result.observations[0].kind is ObservationKind.insufficient_data
    assert "还没有投递记录" in result.data_quality.notes[0]


def test_collected_but_never_applied_jobs_are_not_applications(db, active_resume):
    job = make_job(db, status=JobStatus.new)
    add_analysis(db, job, active_resume, score=92, verdict=Verdict.strong_apply)
    add_event(db, job, EventType.analyzed, at=NOW - timedelta(days=1))

    result = run(db)
    assert result.summary.applications == 0, "analysis is not an application"


# --------------------------------------------------------------------------
# maturity - the core honesty rule
# --------------------------------------------------------------------------


def test_a_fresh_application_is_counted_but_not_yet_judged(db):
    applied_job(db, days_ago=0.1)

    result = run(db)
    assert result.summary.applications == 1
    assert result.summary.mature_applications == 0
    assert result.summary.mature_reply_rate.rate is None, "applied today has not failed"
    assert result.summary.raw_reply_rate.rate == 0.0


def test_an_old_silent_application_becomes_a_mature_zero(db):
    applied_job(db, days_ago=30)

    result = run(db)
    assert result.summary.mature_applications == 1
    assert result.summary.mature_reply_rate.rate == 0.0
    assert result.summary.mature_no_response == 1


def test_a_reply_makes_an_application_mature_immediately(db):
    applied_job(db, days_ago=0.2, replied_after_h=2)

    result = run(db)
    assert result.summary.mature_applications == 1
    assert result.summary.mature_reply_rate.rate == 1.0
    assert result.summary.mature_no_response == 0


def test_interviews_use_the_longer_maturity_window(db):
    """Applied 10 days ago: mature for replies, not yet for interviews."""
    applied_job(db, days_ago=10, replied_after_h=5)

    result = run(db)
    assert result.summary.mature_reply_rate.denominator == 1
    assert result.summary.interview_rate.denominator == 0
    assert result.summary.interview_rate.rate is None


# --------------------------------------------------------------------------
# funnel from event history
# --------------------------------------------------------------------------


def test_the_funnel_is_reconstructed_from_events(db):
    applied_job(db, days_ago=40, replied_after_h=10, interview_after_h=100, offer_after_h=400)
    applied_job(db, days_ago=40, replied_after_h=20)
    applied_job(db, days_ago=40, rejected_after_h=50)
    applied_job(db, days_ago=40)

    result = run(db, window=TimeWindow.all_time)
    assert result.summary.applications == 4
    assert result.summary.replies == 2
    assert result.summary.interviews == 1
    assert result.summary.offers == 1
    assert result.summary.rejections == 1
    assert result.summary.mature_reply_rate.rate == 0.5


def test_a_withdrawn_application_is_not_a_failed_one(db):
    job = make_job(db, status=JobStatus.new)
    applied_at = NOW - timedelta(days=20)
    add_event(db, job, EventType.applied, at=applied_at)
    add_event(db, job, EventType.status_reset, at=applied_at + timedelta(hours=1))

    result = run(db)
    assert result.summary.applications == 0, "status_reset removes the cycle entirely"


def test_reapplying_counts_once_using_the_newest_cycle(db):
    job = make_job(db)
    first = NOW - timedelta(days=40)
    add_event(db, job, EventType.applied, at=first)
    add_event(db, job, EventType.status_reset, at=first + timedelta(hours=2))
    second = NOW - timedelta(days=20)
    add_event(db, job, EventType.applied, at=second)
    add_event(db, job, EventType.replied, at=second + timedelta(hours=6))

    result = run(db, window=TimeWindow.all_time)
    assert result.summary.applications == 1
    assert result.summary.replies == 1
    assert result.summary.reply_latency.median_hours == pytest.approx(6.0)


def test_a_current_status_alone_never_creates_an_application(db):
    """Only the event trail counts - a hand-set status is not evidence."""
    make_job(db, status=JobStatus.applied)

    result = run(db, window=TimeWindow.all_time)
    assert result.summary.applications == 0


# --------------------------------------------------------------------------
# AI verdict vs. human action
# --------------------------------------------------------------------------


def test_a_skip_verdict_does_not_become_a_human_decision(db, active_resume):
    """A recommendation is not an outcome. Only applied events are."""
    skipped = make_job(db, status=JobStatus.new)
    add_analysis(db, skipped, active_resume, score=40, verdict=Verdict.skip)

    applied = applied_job(db, days_ago=20, seq=900)
    add_analysis(db, applied, active_resume, score=88, verdict=Verdict.apply)

    result = run(db)
    assert result.summary.applications == 1
    assert [c.key for c in result.by_verdict] == ["apply"]


def test_verdict_breakdown_reflects_what_was_applied_to(db, active_resume):
    for i in range(3):
        job = applied_job(db, days_ago=20, replied_after_h=5 if i == 0 else None)
        add_analysis(db, job, active_resume, score=92, verdict=Verdict.strong_apply)
    job = applied_job(db, days_ago=20)
    add_analysis(db, job, active_resume, score=65, verdict=Verdict.maybe)

    result = run(db)
    strong = cohort_by_key(result.by_verdict, "strong_apply")
    assert strong is not None
    assert strong.label == "强烈推荐"
    assert (strong.applications, strong.replies) == (3, 1)


# --------------------------------------------------------------------------
# dimensions
# --------------------------------------------------------------------------


def test_city_breakdown(db):
    for _ in range(4):
        applied_job(db, days_ago=20, city="北京", replied_after_h=8)
    for _ in range(3):
        applied_job(db, days_ago=20, city="上海")

    result = run(db)
    beijing = cohort_by_key(result.by_city, "北京")
    shanghai = cohort_by_key(result.by_city, "上海")
    assert beijing.mature_reply_rate.rate == 1.0
    assert shanghai.mature_reply_rate.rate == 0.0
    assert result.by_city[0].key == "北京", "ranked by the conservative score"


def test_role_family_breakdown(db):
    applied_job(db, days_ago=20, title="SRE 工程师", replied_after_h=4)
    applied_job(db, days_ago=20, title="DevOps 工程师")
    applied_job(db, days_ago=20, title="云计算工程师")

    result = run(db)
    keys = {c.key for c in result.by_role_family}
    assert {"SRE", "DevOps", "Cloud"} <= keys


def test_source_breakdown_uses_display_labels(db):
    applied_job(db, days_ago=20, source="boss", replied_after_h=4)
    applied_job(db, days_ago=20, source="manual")

    result = run(db)
    boss = cohort_by_key(result.by_source, "boss")
    assert boss is not None and boss.label == "BOSS直聘"


def test_score_band_breakdown_counts_analyzed_but_unapplied_jobs(db, active_resume):
    applied = applied_job(db, days_ago=20, replied_after_h=6)
    add_analysis(db, applied, active_resume, score=93)

    never_applied = make_job(db, status=JobStatus.new)
    add_analysis(db, never_applied, active_resume, score=95)

    result = run(db)
    band = cohort_by_key(result.by_score_band, "90-100")
    assert band is not None
    assert band.applications == 1
    assert band.analyzed_jobs == 2, "both analyzed jobs land in the band"


def test_salary_bands_only_use_parsable_salaries(db):
    applied_job(db, days_ago=20, salary_text="25k-35k")
    applied_job(db, days_ago=20, salary_text="面议")

    result = run(db)
    assert result.summary.applications == 2
    assert sum(c.applications for c in result.by_salary_band) == 1
    assert result.salary_parse_coverage.covered == 1
    assert result.salary_parse_coverage.total == 2


def test_city_role_intersection_needs_more_than_one_sample(db):
    for _ in range(2):
        applied_job(db, days_ago=20, city="杭州", title="DevOps 工程师", replied_after_h=6)
    applied_job(db, days_ago=20, city="广州", title="SRE 工程师")

    result = run(db)
    keys = [c.key for c in result.by_city_role]
    assert "杭州 · DevOps" in keys
    assert "广州 · SRE" not in keys, "a single data point is not an intersection"


def test_jobs_without_a_city_are_left_out_of_city_tables(db):
    applied_job(db, days_ago=20, city=None)

    result = run(db)
    assert result.summary.applications == 1
    assert result.by_city == []
    assert result.data_quality.city_coverage.covered == 0


# --------------------------------------------------------------------------
# ranking honesty
# --------------------------------------------------------------------------


def test_a_perfect_one_of_one_city_does_not_rank_first(db):
    """The headline case from the spec: 广州 1/1 must not beat 杭州 12/12."""
    for _ in range(12):
        applied_job(db, days_ago=20, city="杭州", replied_after_h=6)
    applied_job(db, days_ago=20, city="广州", replied_after_h=6)

    result = run(db)
    assert result.by_city[0].key == "杭州"
    guangzhou = cohort_by_key(result.by_city, "广州")
    assert guangzhou.mature_reply_rate.rate == 1.0, "the raw rate is still reported honestly"
    assert guangzhou.mature_reply_rate.confidence is Confidence.insufficient


def test_small_cohorts_are_labelled_insufficient_not_hidden(db):
    for _ in range(3):
        applied_job(db, days_ago=20, city="成都", replied_after_h=6)

    result = run(db)
    chengdu = cohort_by_key(result.by_city, "成都")
    assert chengdu is not None, "small cohorts are shown"
    assert chengdu.mature_reply_rate.confidence is Confidence.insufficient
    kinds = {o.kind for o in result.observations if o.target == "成都"}
    assert kinds == {ObservationKind.insufficient_data}


def test_every_rate_carries_its_numerator_and_denominator(db):
    for _ in range(6):
        applied_job(db, days_ago=20, city="北京", replied_after_h=6)

    result = run(db)
    stat = cohort_by_key(result.by_city, "北京").mature_reply_rate
    assert (stat.numerator, stat.denominator) == (6, 6)
    assert stat.ci_low is not None and stat.ci_high is not None
    assert stat.ci_low < 1.0, "6/6 is not certainty"


# --------------------------------------------------------------------------
# latency
# --------------------------------------------------------------------------


def test_reply_latency_is_a_median(db):
    for hours in (2, 4, 6, 8, 200):
        applied_job(db, days_ago=20, replied_after_h=hours)

    result = run(db)
    assert result.summary.reply_latency.sample == 5
    assert result.summary.reply_latency.median_hours == pytest.approx(6.0)
    assert result.summary.reply_latency.p75_hours == pytest.approx(8.0)


def test_latency_ignores_applications_that_never_replied(db):
    applied_job(db, days_ago=20, replied_after_h=12)
    applied_job(db, days_ago=20)

    result = run(db)
    assert result.summary.reply_latency.sample == 1
    assert result.summary.reply_latency.median_hours == pytest.approx(12.0)


def test_interview_latency_is_measured_from_the_application(db):
    applied_job(db, days_ago=40, replied_after_h=10, interview_after_h=120)

    result = run(db, window=TimeWindow.all_time)
    assert result.summary.interview_latency.median_hours == pytest.approx(120.0)


# --------------------------------------------------------------------------
# time windows and filters
# --------------------------------------------------------------------------


def test_time_windows_slice_by_application_date(db):
    applied_job(db, days_ago=3)
    applied_job(db, days_ago=20)
    applied_job(db, days_ago=60)

    assert run(db, window=TimeWindow.d7).summary.applications == 1
    assert run(db, window=TimeWindow.d30).summary.applications == 2
    assert run(db, window=TimeWindow.d90).summary.applications == 3
    assert run(db, window=TimeWindow.all_time).summary.applications == 3


def test_city_filter(db):
    applied_job(db, days_ago=20, city="北京")
    applied_job(db, days_ago=20, city="上海")

    result = run(db, city="北京")
    assert result.summary.applications == 1
    assert result.filters["city"] == "北京"


def test_role_and_source_filters_combine(db):
    applied_job(db, days_ago=20, title="SRE 工程师", source="boss")
    applied_job(db, days_ago=20, title="SRE 工程师", source="manual")
    applied_job(db, days_ago=20, title="DevOps 工程师", source="boss")

    result = run(db, role_family="SRE", source="boss")
    assert result.summary.applications == 1


def test_the_baseline_respects_the_active_filter(db):
    """Comparisons are within the same window and filter, never all-time."""
    for _ in range(6):
        applied_job(db, days_ago=20, city="北京", replied_after_h=5)
    for _ in range(6):
        applied_job(db, days_ago=60, city="北京")

    result = run(db, window=TimeWindow.d30)
    assert result.summary.mature_reply_rate.rate == 1.0


# --------------------------------------------------------------------------
# skills - descriptive only
# --------------------------------------------------------------------------


def test_skill_outcomes_are_counted_over_converted_applications(db, active_resume):
    job = applied_job(db, days_ago=40, replied_after_h=6, interview_after_h=80)
    add_analysis(db, job, active_resume, matched=["Kubernetes", "Terraform"])
    other = applied_job(db, days_ago=40)
    add_analysis(db, other, active_resume, matched=["Kubernetes"])

    result = run(db, window=TimeWindow.all_time)
    k8s = next(s for s in result.skills.matched_in_interviews if s.skill == "Kubernetes")
    assert (k8s.applied, k8s.interviewed) == (2, 1)


def test_missing_skills_are_only_collected_from_high_scoring_jobs(db, active_resume):
    high = applied_job(db, days_ago=20)
    add_analysis(db, high, active_resume, score=88, missing=["Go"])
    low = applied_job(db, days_ago=20)
    add_analysis(db, low, active_resume, score=55, missing=["Rust"])

    result = run(db)
    labels = {item.key for item in result.skills.missing_in_high_score_jobs}
    assert labels == {"Go"}


# --------------------------------------------------------------------------
# data quality
# --------------------------------------------------------------------------


def test_data_quality_reports_coverage_and_warns_on_thin_samples(db):
    applied_job(db, days_ago=20, city="北京", salary_text="25k-35k")
    applied_job(db, days_ago=20, city=None, salary_text="面议")
    applied_job(db, days_ago=20, city=None, salary_text=None)

    result = run(db)
    quality = result.data_quality
    assert quality.applications == 3
    assert quality.city_coverage.ratio == pytest.approx(1 / 3, abs=1e-4)
    assert quality.salary_coverage.ratio == pytest.approx(1 / 3, abs=1e-4)
    assert any("成熟样本只有" in note for note in quality.notes)
    assert any("薪资" in note for note in quality.notes)


def test_the_salary_warning_needs_more_than_half_missing(db):
    """Exactly half is not "more than half" - the note stays quiet."""
    applied_job(db, days_ago=20, salary_text="25k-35k")
    applied_job(db, days_ago=20, salary_text="面议")

    result = run(db)
    assert not any("薪资" in note for note in result.data_quality.notes)


def test_data_quality_stops_warning_once_the_sample_is_adequate(db):
    cfg = get_settings()
    for _ in range(cfg.analytics_min_sample + 2):
        applied_job(db, days_ago=20, salary_text="25k-35k")

    result = run(db)
    assert not any("成熟样本只有" in note for note in result.data_quality.notes)


# --------------------------------------------------------------------------
# reporting metadata
# --------------------------------------------------------------------------


def test_the_result_states_its_own_assumptions(db):
    cfg = get_settings()
    result = run(db)

    assert result.timezone == cfg.report_timezone
    assert result.response_maturity_days == cfg.response_maturity_days
    assert result.interview_maturity_days == cfg.interview_maturity_days
    assert result.min_sample == cfg.analytics_min_sample
    assert result.recommend_sample == cfg.analytics_recommend_sample


def test_analytics_is_deterministic(db):
    for _ in range(5):
        applied_job(db, days_ago=20, replied_after_h=6)

    first = run(db).model_dump_json()
    second = run(db).model_dump_json()
    assert first == second


def test_build_records_never_queries_per_job(db):
    """Guards the eager-loaded bulk query against an N+1 regression."""
    from sqlalchemy import event as sa_event

    for _ in range(8):
        applied_job(db, days_ago=20, replied_after_h=6)

    statements: list[str] = []

    def _record(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    sa_event.listen(db.get_bind(), "before_cursor_execute", _record)
    try:
        records = build_records(db, AnalyticsFilters(), now=NOW)
    finally:
        sa_event.remove(db.get_bind(), "before_cursor_execute", _record)

    assert len(records) == 8
    assert len(statements) <= 6, f"looks like an N+1: {len(statements)} selects"


# --------------------------------------------------------------------------
# reporting timezone
# --------------------------------------------------------------------------


def test_the_report_timezone_is_available_on_this_machine(db):
    """Windows ships no IANA database - tzdata must be installed."""
    from zoneinfo import ZoneInfo

    from app.services.timezones import report_timezone

    tz = report_timezone()
    assert tz != timezone.utc, "Asia/Tokyo fell back to UTC - is tzdata installed?"
    assert isinstance(tz, ZoneInfo)


def test_a_late_evening_application_belongs_to_the_local_day(db):
    """22:00 in Tokyo is 13:00 UTC the same day; 08:00 Tokyo is the *previous*
    UTC day. Comparing naive UTC dates would mis-file both."""
    from app.services.timezones import to_local

    tokyo_late = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)   # 22:00 JST 8/20
    tokyo_early = datetime(2026, 8, 20, 23, 0, tzinfo=timezone.utc)  # 08:00 JST 8/21

    assert to_local(tokyo_late).date().isoformat() == "2026-08-20"
    assert to_local(tokyo_early).date().isoformat() == "2026-08-21"


def test_window_boundaries_are_computed_in_utc_not_local_dates(db):
    """The window is a rolling duration, so the timezone cannot shift it."""
    applied_job(db, days_ago=6.9)
    applied_job(db, days_ago=7.1)

    assert run(db, window=TimeWindow.d7).summary.applications == 1


def test_maturity_is_a_duration_not_a_calendar_day(db):
    """Applied 6 days and 23 hours ago is not yet mature, whatever the date."""
    applied_job(db, days_ago=6.95)
    assert run(db).summary.mature_applications == 0

    applied_job(db, days_ago=7.05)
    assert run(db).summary.mature_applications == 1


def test_a_perfect_small_cohort_never_ranks_above_an_adequate_one(db):
    """2/2 has a Wilson lower bound of 0.34, beating 5/12 at 0.19.

    Ranking on the lower bound alone is therefore not enough: a cohort the page
    labels 样本不足 must sort below every adequately-sampled cohort, or the
    table would contradict its own confidence column.
    """
    for i in range(12):
        applied_job(db, days_ago=20, city="杭州", replied_after_h=6 if i < 5 else None)
    for _ in range(2):
        applied_job(db, days_ago=20, city="广州", replied_after_h=6)

    result = run(db)
    guangzhou = cohort_by_key(result.by_city, "广州")
    hangzhou = cohort_by_key(result.by_city, "杭州")

    assert result.by_city[0].key == "杭州"
    assert guangzhou.mature_reply_rate.rate == 1.0, "the raw rate is still honest"
    assert (
        guangzhou.mature_reply_rate.ranking_score
        > hangzhou.mature_reply_rate.ranking_score
    ), "the lower bound really does favour 广州 - the tier is what saves us"
    assert guangzhou.mature_reply_rate.confidence is Confidence.insufficient


def test_ranking_still_uses_the_lower_bound_within_a_tier(db):
    """Tiering only separates 样本不足 from the rest; inside a tier the
    conservative score still decides."""
    for _ in range(20):
        applied_job(db, days_ago=20, city="北京", replied_after_h=6)
    for i in range(10):
        applied_job(db, days_ago=20, city="上海", replied_after_h=6 if i < 3 else None)

    result = run(db)
    assert [c.key for c in result.by_city] == ["北京", "上海"]


def test_a_striking_small_cohort_is_warned_about_even_when_ranked_last(db):
    """广州 2/2 = 100% sorts to the bottom, but silence would be worse than a
    warning: it is the row a reader is most likely to over-read."""
    for i in range(12):
        applied_job(db, days_ago=20, city="杭州", replied_after_h=6 if i < 5 else None)
    for i in range(10):
        applied_job(db, days_ago=20, city="北京", replied_after_h=6 if i < 3 else None)
    for i in range(8):
        applied_job(db, days_ago=20, city="上海", replied_after_h=6 if i < 1 else None)
    for _ in range(2):
        applied_job(db, days_ago=20, city="广州", replied_after_h=6)

    result = run(db)
    assert result.by_city[-1].key == "广州", "it really is ranked last"

    guangzhou = [o for o in result.observations if o.target == "广州"]
    assert guangzhou, "a bottom-ranked but striking cohort still gets a note"
    assert guangzhou[0].kind is ObservationKind.insufficient_data
    assert "样本不足" in guangzhou[0].text


def test_an_unremarkable_small_cohort_is_not_dragged_into_the_notes(db):
    """Only cohorts that beat the baseline get the extra mention - otherwise
    every thin bucket would generate noise."""
    for i in range(12):
        applied_job(db, days_ago=20, city="杭州", replied_after_h=6 if i < 8 else None)
    for i in range(10):
        applied_job(db, days_ago=20, city="北京", replied_after_h=6 if i < 6 else None)
    for i in range(8):
        applied_job(db, days_ago=20, city="上海", replied_after_h=6 if i < 4 else None)
    applied_job(db, days_ago=20, city="广州")

    result = run(db)
    assert not [o for o in result.observations if o.target == "广州"]
