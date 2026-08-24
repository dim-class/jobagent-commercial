"""Wilson intervals, conservative ranking, percentiles, application cycles.

Pure logic - no database, no network, no OpenAI.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import EventType
from app.services.application_cycles import (
    ApplicationCycle,
    build_cycles,
    effective_cycle,
    in_window,
    window_start,
)
from app.services.statistics import (
    Confidence,
    classify_confidence,
    is_meaningfully_better,
    is_meaningfully_worse,
    percentiles,
    rate,
    wilson_interval,
    wilson_lower_bound,
)


# --------------------------------------------------------------------------
# Wilson interval
# --------------------------------------------------------------------------


def test_no_sample_has_no_interval():
    """0/0 is undefined, not "0% to 100%"."""
    assert wilson_interval(0, 0) is None
    assert rate(0, 0) is None
    assert wilson_lower_bound(0, 0) == 0.0


def test_zero_successes_still_has_an_upper_bound():
    interval = wilson_interval(0, 10)
    assert interval is not None
    assert interval.low == 0.0
    assert 0.25 < interval.high < 0.31, "0/10 does not rule out a ~28% true rate"


def test_all_successes_does_not_claim_certainty():
    interval = wilson_interval(10, 10)
    assert interval is not None
    assert interval.high == 1.0
    assert 0.70 < interval.low < 0.75, "10/10 still admits the rate may be ~72%"


@pytest.mark.parametrize(
    ("successes", "trials", "expected_low", "expected_high"),
    [
        # Reference values for the Wilson score interval at 95%.
        (4, 10, 0.168, 0.687),
        (18, 30, 0.423, 0.754),
        (1, 1, 0.207, 1.0),
        (2, 3, 0.208, 0.939),
        (50, 100, 0.404, 0.596),
    ],
)
def test_known_wilson_values(successes, trials, expected_low, expected_high):
    interval = wilson_interval(successes, trials)
    assert interval is not None
    assert interval.low == pytest.approx(expected_low, abs=0.002)
    assert interval.high == pytest.approx(expected_high, abs=0.002)


def test_interval_always_stays_inside_zero_and_one():
    for successes, trials in [(0, 1), (1, 1), (1, 2), (99, 100), (0, 3)]:
        interval = wilson_interval(successes, trials)
        assert interval is not None
        assert 0.0 <= interval.low <= interval.high <= 1.0


def test_successes_are_clamped_to_trials():
    assert wilson_interval(15, 10) == wilson_interval(10, 10)


# --------------------------------------------------------------------------
# conservative ranking
# --------------------------------------------------------------------------


def test_a_lucky_single_sample_does_not_outrank_a_solid_cohort():
    """The whole point of ranking on the lower bound."""
    lucky = wilson_lower_bound(1, 1)        # raw 100%
    solid = wilson_lower_bound(18, 30)      # raw 60%
    assert lucky < solid


def test_two_of_three_does_not_beat_a_large_cohort():
    assert wilson_lower_bound(2, 3) < wilson_lower_bound(12, 30)


def test_more_evidence_at_the_same_rate_ranks_higher():
    assert wilson_lower_bound(2, 4) < wilson_lower_bound(20, 40) < wilson_lower_bound(200, 400)


def test_ranking_is_monotonic_in_successes():
    assert wilson_lower_bound(3, 10) < wilson_lower_bound(6, 10) < wilson_lower_bound(9, 10)


# --------------------------------------------------------------------------
# confidence bands
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("trials", "expected"),
    [
        (0, Confidence.insufficient),
        (4, Confidence.insufficient),
        (5, Confidence.low),
        (7, Confidence.low),
        (8, Confidence.moderate),
        (15, Confidence.moderate),
        (16, Confidence.strong),
        (99, Confidence.strong),
    ],
)
def test_confidence_bands(trials, expected):
    assert classify_confidence(trials, min_sample=5, recommend_sample=8) is expected


def test_confidence_thresholds_are_configurable():
    assert classify_confidence(6, min_sample=10, recommend_sample=20) is Confidence.insufficient


# --------------------------------------------------------------------------
# meaningful difference
# --------------------------------------------------------------------------


def test_small_sample_never_counts_as_meaningfully_better():
    """2/3 = 67% beats a 24% baseline on paper, but proves nothing."""
    assert is_meaningfully_better(2, 3, baseline_rate=0.24) is False


def test_large_clear_difference_is_detected():
    assert is_meaningfully_better(18, 30, baseline_rate=0.24) is True


def test_clearly_worse_cohort_is_detected():
    assert is_meaningfully_worse(1, 30, baseline_rate=0.30) is True


def test_no_baseline_means_no_comparison():
    assert is_meaningfully_better(18, 30, baseline_rate=None) is False
    assert is_meaningfully_worse(1, 30, baseline_rate=None) is False


# --------------------------------------------------------------------------
# percentiles
# --------------------------------------------------------------------------


def test_percentiles_of_an_empty_list():
    assert percentiles([]) == (None, None, None)


def test_percentiles_basic():
    assert percentiles([1, 2, 3, 4, 5]) == (2, 3, 4)


def test_percentiles_single_value():
    assert percentiles([12.5]) == (12.5, 12.5, 12.5)


def test_median_resists_one_outlier():
    """One three-week reply must not become the "typical" latency."""
    _p25, median, _p75 = percentiles([2, 3, 4, 5, 500])
    assert median == 4


# --------------------------------------------------------------------------
# application cycles
# --------------------------------------------------------------------------


class _Event:
    """Minimal stand-in for ApplicationEvent."""

    _next = 1

    def __init__(
        self,
        event_type: EventType,
        created_at: datetime,
        *,
        resume_id: int | None = None,
        metadata: dict | None = None,
    ):
        self.event_type = event_type
        self.created_at = created_at
        self.resume_id = resume_id
        self.metadata_json = metadata or {}
        self.id = _Event._next
        _Event._next += 1


class _Job:
    def __init__(self, events):
        self.id = 1
        self.events = events


BASE = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)


def at(hours: float) -> datetime:
    return BASE + timedelta(hours=hours)


def test_a_job_never_applied_to_has_no_cycle():
    job = _Job([_Event(EventType.analyzed, at(0))])
    assert effective_cycle(job) is None


def test_a_simple_cycle_records_outcomes():
    job = _Job(
        [
            _Event(EventType.applied, at(0)),
            _Event(EventType.replied, at(18)),
            _Event(EventType.interview, at(72)),
        ]
    )
    cycle = effective_cycle(job)
    assert cycle is not None
    assert cycle.replied and cycle.interviewed
    assert cycle.hours_to_reply() == pytest.approx(18.0)
    assert cycle.hours_to_interview() == pytest.approx(72.0)
    assert cycle.offered is False


def test_a_reset_cycle_is_superseded_and_never_counted():
    """The user withdrew that application - it is not a failed one."""
    job = _Job([_Event(EventType.applied, at(0)), _Event(EventType.status_reset, at(1))])

    cycles = build_cycles(job)
    assert len(cycles) == 1 and cycles[0].superseded is True
    assert effective_cycle(job) is None, "a withdrawn attempt is not an application"


def test_reapplying_after_a_reset_uses_the_newest_cycle():
    job = _Job(
        [
            _Event(EventType.applied, at(0)),
            _Event(EventType.replied, at(5)),
            _Event(EventType.status_reset, at(10)),
            _Event(EventType.applied, at(20)),
        ]
    )
    cycle = effective_cycle(job)
    assert cycle is not None
    assert cycle.applied_at == at(20)
    assert cycle.replied is False, "the earlier cycle's reply must not leak forward"


def test_outcomes_are_not_mixed_across_cycles():
    job = _Job(
        [
            _Event(EventType.applied, at(0)),
            _Event(EventType.status_reset, at(1)),
            _Event(EventType.applied, at(2)),
            _Event(EventType.replied, at(30)),
        ]
    )
    cycles = build_cycles(job)
    assert cycles[0].superseded is True and cycles[0].replied is False
    assert cycles[1].replied is True


def test_events_outside_a_cycle_are_ignored():
    job = _Job([_Event(EventType.replied, at(0)), _Event(EventType.applied, at(5))])
    cycle = effective_cycle(job)
    assert cycle is not None and cycle.replied is False


def test_offer_cycle_still_counts_as_replied_and_interviewed():
    """Cohort membership is cumulative, driven by events not current status."""
    job = _Job(
        [
            _Event(EventType.applied, at(0)),
            _Event(EventType.replied, at(10)),
            _Event(EventType.interview, at(50)),
            _Event(EventType.offer, at(200)),
        ]
    )
    cycle = effective_cycle(job)
    assert cycle is not None
    assert (cycle.replied, cycle.interviewed, cycle.offered) == (True, True, True)


# --------------------------------------------------------------------------
# maturity
# --------------------------------------------------------------------------


def cycle_at(hours_ago: float, *, replied: bool = False) -> ApplicationCycle:
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    applied = now - timedelta(hours=hours_ago)
    return ApplicationCycle(
        job_id=1,
        applied_at=applied,
        replied_at=applied + timedelta(hours=1) if replied else None,
    )


NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def test_a_recent_application_without_a_reply_is_not_mature():
    """Applied two hours ago: not a failure, just not tested yet."""
    cycle = cycle_at(2)
    assert cycle.is_response_mature(now=NOW, maturity_days=7) is False
    assert cycle.is_mature_no_response(now=NOW, maturity_days=7) is False


def test_a_recent_application_with_a_reply_is_mature():
    assert cycle_at(2, replied=True).is_response_mature(now=NOW, maturity_days=7) is True


def test_an_old_application_without_a_reply_is_mature():
    cycle = cycle_at(24 * 30)
    assert cycle.is_response_mature(now=NOW, maturity_days=7) is True
    assert cycle.is_mature_no_response(now=NOW, maturity_days=7) is True


def test_maturity_boundary_is_exact():
    assert cycle_at(24 * 7).is_response_mature(now=NOW, maturity_days=7) is True
    assert cycle_at(24 * 7 - 1).is_response_mature(now=NOW, maturity_days=7) is False


def test_interview_maturity_uses_its_own_longer_window():
    cycle = cycle_at(24 * 10)
    assert cycle.is_response_mature(now=NOW, maturity_days=7) is True
    assert cycle.is_interview_mature(now=NOW, maturity_days=14) is False


# --------------------------------------------------------------------------
# windows
# --------------------------------------------------------------------------


def test_all_time_window_has_no_start():
    assert window_start(None, now=NOW) is None


def test_window_start_is_relative_to_now():
    assert window_start(7, now=NOW) == NOW - timedelta(days=7)


def test_in_window_uses_the_application_time():
    recent = cycle_at(24 * 3)
    old = cycle_at(24 * 60)
    since = window_start(30, now=NOW)

    assert in_window(recent, since=since) is True
    assert in_window(old, since=since) is False
    assert in_window(old, since=None) is True, "all-time never caps"
