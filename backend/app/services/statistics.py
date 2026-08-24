"""Small-sample statistics for outcome analytics (v0.6).

Pure functions, no dependencies beyond the stdlib - deliberately not scipy,
which would be a heavy dependency for one formula.

The point of this module is honesty about uncertainty. ``1/1`` is 100% and also
tells you almost nothing; the Wilson interval and the lower-bound ranking make
that visible instead of letting a lucky single application top a table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

#: z for a two-sided 95% interval.
Z_95 = 1.959963984540054


class Confidence(str, Enum):
    """How much weight a cohort's rate deserves, by sample size alone."""

    insufficient = "insufficient"
    low = "low"
    moderate = "moderate"
    strong = "strong"


@dataclass(slots=True, frozen=True)
class Interval:
    low: float
    high: float


def wilson_interval(successes: int, trials: int, *, z: float = Z_95) -> Interval | None:
    """Wilson score interval for a binomial proportion.

    Returns ``None`` when there is no sample at all - an interval over zero
    trials is not "0% to 100%", it is undefined, and callers should say so.

    Chosen over the normal approximation because it stays inside [0, 1] and
    behaves sensibly at 0/n and n/n, which is exactly where a job-search
    dataset lives.
    """
    if trials <= 0:
        return None
    successes = max(0, min(successes, trials))

    n = float(trials)
    p = successes / n
    z2 = z * z

    denominator = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denominator
    margin = (z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))) / denominator

    return Interval(
        low=max(0.0, round(centre - margin, 6)),
        high=min(1.0, round(centre + margin, 6)),
    )


def wilson_lower_bound(successes: int, trials: int, *, z: float = Z_95) -> float:
    """Conservative score used for ranking cohorts.

    Ranking by raw percentage lets ``1/1`` (100%) outrank ``18/30`` (60%),
    which is exactly backwards: the second cohort is far better evidenced. The
    Wilson lower bound answers "what rate can we defend given this sample?", so
    small cohorts sink until they earn their position.

    ``0`` for an empty sample, so unsampled cohorts sort last.
    """
    interval = wilson_interval(successes, trials, z=z)
    return 0.0 if interval is None else interval.low


def rate(successes: int, trials: int) -> float | None:
    """``None`` for an empty denominator - never a misleading 0.0."""
    if trials <= 0:
        return None
    return round(successes / trials, 6)


def classify_confidence(
    trials: int, *, min_sample: int, recommend_sample: int
) -> Confidence:
    """Sample-size bands. Thresholds come from settings, not magic numbers."""
    if trials < min_sample:
        return Confidence.insufficient
    if trials < recommend_sample:
        return Confidence.low
    if trials < recommend_sample * 2:
        return Confidence.moderate
    return Confidence.strong


def is_meaningfully_better(
    successes: int,
    trials: int,
    *,
    baseline_rate: float | None,
    z: float = Z_95,
) -> bool:
    """True when a cohort beats the baseline by more than noise.

    The test is deliberately strict: the cohort's Wilson *lower* bound must sit
    above the overall rate. A point estimate above the baseline is not enough -
    that is how "2/3 replied, 杭州 is great" happens.
    """
    if baseline_rate is None or trials <= 0:
        return False
    return wilson_lower_bound(successes, trials, z=z) > baseline_rate


def is_meaningfully_worse(
    successes: int,
    trials: int,
    *,
    baseline_rate: float | None,
    z: float = Z_95,
) -> bool:
    """Mirror image: the cohort's Wilson *upper* bound is below the baseline."""
    if baseline_rate is None or trials <= 0:
        return False
    interval = wilson_interval(successes, trials, z=z)
    return interval is not None and interval.high < baseline_rate


def percentiles(values: list[float]) -> tuple[float | None, float | None, float | None]:
    """``(p25, median, p75)`` using linear interpolation.

    Median rather than mean throughout: one recruiter who replied after three
    weeks should not drag the "typical" latency with them.
    """
    if not values:
        return (None, None, None)
    ordered = sorted(values)
    return (_quantile(ordered, 0.25), _quantile(ordered, 0.5), _quantile(ordered, 0.75))


def _quantile(ordered: list[float], q: float) -> float:
    if len(ordered) == 1:
        return round(ordered[0], 4)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[int(position)], 4)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 4)
