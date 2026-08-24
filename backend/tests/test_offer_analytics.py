"""Offer analytics (v0.9).

Reuses the v0.6 statistics wholesale. The tests here pin the money-specific
rules: currencies never mix, accepted compensation comes from the frozen
snapshot, and a candidate counter never enters a compensation figure.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Currency, DeclineReason, JobStatus, OfferStatus, RemotePolicy
from app.schemas.analytics import ObservationKind, TimeWindow
from app.schemas.offer import (
    AcceptOfferRequest,
    DeclineOfferRequest,
    OfferCreateRequest,
)
from app.services import offer_analytics, offer_management
from app.services.application_analytics import AnalyticsFilters
from app.services.offer_analytics import compute_offer_analytics
from app.services.statistics import Confidence

from tests.test_career_analytics import NOW, make_job
from tests.test_offer_management import (
    company_revision,
    counter,
    final_revision,
    make_offer,
)
from tests.test_resume_variants import apply_with, make_resume


def run(db, **kwargs):
    filters = AnalyticsFilters(**kwargs) if kwargs else AnalyticsFilters()
    return compute_offer_analytics(db, filters, now=NOW)


def applied_days_ago(db, resume=None, *, days=30, **job_kwargs):
    from app.models import ResumeUsage
    from app.schemas.application import MarkAppliedRequest
    from app.services import application_workflow

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


def offered(db, resume=None, *, base=300_000, currency=Currency.CNY, **job_kwargs):
    job = applied_days_ago(db, resume, **job_kwargs)
    offer = make_offer(
        db, job, currency=currency, initial={"base_salary_annual": base}
    )
    return job, offer


# --------------------------------------------------------------------------
# empty state
# --------------------------------------------------------------------------


def test_no_applications_reports_nothing(db):
    result = run(db, window=TimeWindow.all_time)
    assert result.funnel.applications == 0
    assert result.funnel.application_to_offer.rate is None, "0/0 is unknown, not 0%"
    assert result.observations[0].kind is ObservationKind.insufficient_data


def test_applications_without_offers_are_reported_honestly(db):
    for _ in range(3):
        applied_days_ago(db)

    result = run(db, window=TimeWindow.all_time)
    assert result.funnel.applications == 3
    assert result.funnel.offers == 0
    assert result.funnel.application_to_offer.rate == 0.0
    assert any("还没有记录任何 Offer" in note for note in result.notes)


# --------------------------------------------------------------------------
# the funnel
# --------------------------------------------------------------------------


def test_the_offer_funnel(db):
    for _ in range(6):
        applied_days_ago(db)
    for _ in range(2):
        offered(db)
    _, accepted = offered(db)
    offer_management.accept_offer(db, accepted.id, AcceptOfferRequest(confirmed=True))
    _, declined = offered(db)
    offer_management.decline_offer(
        db, declined.id, DeclineOfferRequest(confirmed=True, reason=DeclineReason.salary)
    )

    funnel = run(db, window=TimeWindow.all_time).funnel
    assert funnel.applications == 10
    assert funnel.offers == 4
    assert funnel.accepted == 1
    assert funnel.declined == 1
    assert funnel.pending == 2
    assert funnel.application_to_offer.rate == pytest.approx(0.4)


def test_the_acceptance_rate_excludes_undecided_offers(db):
    """A pending offer has not been turned down - it has not been answered."""
    _, accepted = offered(db)
    offer_management.accept_offer(db, accepted.id, AcceptOfferRequest(confirmed=True))
    _, declined = offered(db)
    offer_management.decline_offer(db, declined.id, DeclineOfferRequest(confirmed=True))
    offered(db)  # still pending

    stat = run(db, window=TimeWindow.all_time).funnel.offer_acceptance_rate
    assert (stat.numerator, stat.denominator) == (1, 2)


def test_interview_to_offer_uses_recorded_rounds(db):
    """The denominator is applications that actually reached an interview."""
    from app.models import InterviewRoundType
    from tests.test_interview_pipeline import add, open_process

    with_offer = applied_days_ago(db)
    add(db, open_process(db, with_offer), InterviewRoundType.technical)
    make_offer(db, with_offer, initial={"base_salary_annual": 300_000})

    without_offer = applied_days_ago(db)
    add(db, open_process(db, without_offer), InterviewRoundType.hr)

    # A third application that never interviewed at all.
    applied_days_ago(db)

    stat = run(db, window=TimeWindow.all_time).funnel.interview_to_offer
    assert stat.denominator == 2, "only interviewed applications count"
    assert stat.numerator == 1


# --------------------------------------------------------------------------
# compensation, one currency at a time
# --------------------------------------------------------------------------


def test_compensation_medians_within_one_currency(db):
    for base in (280_000, 300_000, 340_000):
        offered(db, base=base)

    entries = run(db, window=TimeWindow.all_time).compensation
    assert len(entries) == 1
    entry = entries[0]
    assert entry.currency == "CNY"
    assert entry.base_annual.sample == 3
    assert entry.base_annual.median == 300_000
    assert entry.base_annual.minimum == 280_000
    assert entry.base_annual.maximum == 340_000


def test_currencies_are_never_merged(db):
    """400K CNY and 8M JPY are two rows, never one ranking."""
    offered(db, base=400_000, currency=Currency.CNY)
    offered(db, base=8_000_000, currency=Currency.JPY)

    result = run(db, window=TimeWindow.all_time)
    currencies = {entry.currency for entry in result.compensation}
    assert currencies == {"CNY", "JPY"}
    for entry in result.compensation:
        assert entry.offers == 1
    assert any("不做跨币种比较" in note for note in result.notes)


def test_a_mixed_currency_run_says_it_uses_no_exchange_rate(db):
    offered(db, base=400_000, currency=Currency.CNY)
    offered(db, base=8_000_000, currency=Currency.JPY)

    notes = " ".join(run(db, window=TimeWindow.all_time).notes)
    assert "汇率" in notes


def test_compensation_uses_the_company_offer_not_the_counter(db):
    job, offer = offered(db, base=300_000)
    counter(db, offer, base_salary_annual=500_000)

    entry = run(db, window=TimeWindow.all_time).compensation[0]
    assert entry.base_annual.median == 300_000, "the 500K ask is not an offer"


def test_accepted_compensation_uses_the_frozen_snapshot(db):
    """A later revision must not rewrite what a past decision was made on."""
    from app.models import OfferRevision, RevisionSource, RevisionType

    job, offer = offered(db, base=300_000)
    offer_management.accept_offer(db, offer.id, AcceptOfferRequest(confirmed=True))

    db.add(
        OfferRevision(
            offer_id=offer.id,
            revision_index=99,
            revision_type=RevisionType.company_revision,
            source=RevisionSource.company,
            base_salary_annual=999_000,
            currency=offer.currency,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    accepted = run(db, window=TimeWindow.all_time).accepted_compensation
    assert accepted[0].base_annual.median == 300_000


def test_offers_with_no_figures_are_not_counted_as_zero(db):
    job = applied_days_ago(db)
    make_offer(db, job)  # no numbers at all

    entry = run(db, window=TimeWindow.all_time).compensation[0]
    assert entry.offers == 1
    assert entry.base_annual is None, "unknown is not 0"


# --------------------------------------------------------------------------
# negotiation
# --------------------------------------------------------------------------


def test_negotiation_uplift_is_aggregated_per_currency(db):
    for _ in range(2):
        job, offer = offered(db, base=300_000)
        counter(db, offer, base_salary_annual=350_000)
        offer = offer_management.get_offer(db, offer.id)
        final_revision(db, offer, base_salary_annual=330_000)

    entries = run(db, window=TimeWindow.all_time).negotiation
    assert len(entries) == 1
    entry = entries[0]
    assert entry.currency == "CNY"
    assert entry.median_base_uplift == 30_000
    assert entry.median_base_uplift_pct == pytest.approx(0.1)
    assert entry.with_full_sequence == 2


def test_a_change_without_a_counter_is_not_a_negotiation_sequence(db):
    job, offer = offered(db, base=300_000)
    company_revision(db, offer, base_salary_annual=330_000)

    entry = run(db, window=TimeWindow.all_time).negotiation[0]
    assert entry.median_base_uplift == 30_000, "the change is still reported"
    assert entry.with_full_sequence == 0, "but not as evidence of negotiating"


def test_an_offer_with_one_revision_contributes_no_uplift(db):
    offered(db, base=300_000)
    assert run(db, window=TimeWindow.all_time).negotiation == []


# --------------------------------------------------------------------------
# dimensions
# --------------------------------------------------------------------------


def test_offer_rate_by_resume(db):
    devops = make_resume(db, variant_name="DevOps版")
    cloud = make_resume(db, variant_name="Cloud版")

    for i in range(10):
        if i < 4:
            offered(db, devops)
        else:
            applied_days_ago(db, devops)
    for i in range(10):
        if i < 1:
            offered(db, cloud)
        else:
            applied_days_ago(db, cloud)

    rows = {r.resume_id: r for r in run(db, window=TimeWindow.all_time).by_resume}
    assert rows[devops.id].offers_recorded == 4
    assert rows[devops.id].offer_reach_rate.rate == pytest.approx(0.4)
    assert rows[cloud.id].offers_recorded == 1
    assert run(db, window=TimeWindow.all_time).by_resume[0].resume_id == devops.id


def test_offer_attribution_follows_the_cycle(db):
    from app.schemas.application import ResetRequest
    from app.services import application_workflow

    cloud = make_resume(db, variant_name="Cloud版", active=True)
    devops = make_resume(db, variant_name="DevOps版")

    job = applied_days_ago(db, cloud, days=60)
    application_workflow.reset_status(db, job.id, ResetRequest())
    apply_with(db, job, devops)
    db.refresh(job)
    make_offer(db, job, initial={"base_salary_annual": 300_000})

    rows = {r.resume_id: r for r in run(db, window=TimeWindow.all_time).by_resume}
    assert rows[devops.id].offers_recorded == 1
    assert cloud.id not in rows, "the superseded cycle is not an application"


def test_offer_rate_by_city_and_role(db):
    for i in range(6):
        if i < 3:
            offered(db, city="杭州", title="DevOps 工程师")
        else:
            applied_days_ago(db, city="杭州", title="DevOps 工程师")
    for _ in range(6):
        applied_days_ago(db, city="北京", title="SRE 工程师")

    result = run(db, window=TimeWindow.all_time)
    cities = {c.key: c for c in result.by_city}
    roles = {c.key: c for c in result.by_role_family}
    assert cities["杭州"].offer_reach_rate.rate == pytest.approx(0.5)
    assert cities["北京"].offers_recorded == 0
    assert roles["DevOps"].offers_recorded == 3
    assert result.by_city[0].key == "杭州"


def test_offer_rate_by_source(db):
    for i in range(4):
        if i < 2:
            offered(db, source="boss")
        else:
            applied_days_ago(db, source="boss")

    row = run(db, window=TimeWindow.all_time).by_source[0]
    assert row.key == "boss" and row.label == "BOSS直聘"
    assert row.offers_recorded == 2


def test_a_lucky_small_cohort_does_not_top_the_table(db):
    """Same v0.6 rule: tier first, then the conservative score."""
    for i in range(18):
        if i < 6:
            offered(db, city="杭州")
        else:
            applied_days_ago(db, city="杭州")
    offered(db, city="广州")

    result = run(db, window=TimeWindow.all_time)
    assert result.by_city[0].key == "杭州"
    guangzhou = next(c for c in result.by_city if c.key == "广州")
    assert guangzhou.offer_reach_rate.rate == 1.0, "the raw rate stays honest"
    assert guangzhou.offer_reach_rate.confidence is Confidence.insufficient


# --------------------------------------------------------------------------
# decline reasons
# --------------------------------------------------------------------------


def test_decline_reasons_are_aggregated(db):
    for reason in (
        DeclineReason.salary,
        DeclineReason.salary,
        DeclineReason.accepted_other_offer,
    ):
        _, offer = offered(db)
        offer_management.decline_offer(
            db, offer.id, DeclineOfferRequest(confirmed=True, reason=reason)
        )

    reasons = {r.key: r for r in run(db, window=TimeWindow.all_time).decline_reasons}
    assert reasons["salary"].count == 2
    assert reasons["salary"].label == "薪资"
    assert reasons["accepted_other_offer"].count == 1


def test_an_accepted_offer_contributes_no_decline_reason(db):
    _, offer = offered(db)
    offer_management.accept_offer(db, offer.id, AcceptOfferRequest(confirmed=True))
    assert run(db, window=TimeWindow.all_time).decline_reasons == []


# --------------------------------------------------------------------------
# coverage, legacy, spend
# --------------------------------------------------------------------------


def test_resume_attribution_coverage_is_reported(db):
    devops = make_resume(db, variant_name="DevOps版")
    for _ in range(4):
        applied_days_ago(db, devops)
    applied_days_ago(db, None)

    coverage = run(db, window=TimeWindow.all_time).resume_attribution_coverage
    assert (coverage.covered, coverage.total) == (4, 5)


def test_a_legacy_offer_event_is_reported_separately(db):
    from app.models import EventType
    from tests.test_career_analytics import add_event

    job = applied_days_ago(db)
    add_event(db, job, EventType.offer, at=NOW)
    db.refresh(job)

    result = run(db, window=TimeWindow.all_time)
    assert result.funnel.offers == 0, "a bare milestone is not a structured offer"
    assert result.legacy_offer_events == 1
    assert any(o.dimension == "legacy" for o in result.observations)


def test_small_offer_samples_are_flagged(db):
    offered(db, base=300_000)
    result = run(db, window=TimeWindow.all_time)
    assert any("样本只有" in note for note in result.notes)
    assert any(
        o.kind is ObservationKind.insufficient_data and o.dimension == "compensation"
        for o in result.observations
    )


def test_filters_apply(db):
    offered(db, city="杭州")
    offered(db, city="北京")
    assert run(db, window=TimeWindow.all_time, city="杭州").funnel.offers == 1


def test_offer_analytics_is_deterministic(db):
    offered(db, base=300_000)
    assert run(db, window=TimeWindow.all_time).model_dump_json() == run(
        db, window=TimeWindow.all_time
    ).model_dump_json()


def test_offer_analytics_makes_no_openai_call(db, monkeypatch):
    import app.services.job_matcher as job_matcher

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("offer analytics must never call the model")

    monkeypatch.setattr(job_matcher, "run_job_match", _explode)

    job, offer = offered(db, base=300_000)
    counter(db, offer, base_salary_annual=350_000)
    assert run(db, window=TimeWindow.all_time).funnel.offers == 1
