"""Deterministic offer scoring (v1.0).

Pure functions over recorded facts and user-entered preferences. No database,
no network, no OpenAI - which is what lets every number on the comparison page
be recomputed and checked by hand.

The rules that make the score trustworthy rather than impressive:

**Unknown is not zero.** A dimension the user has not rated, or a fact the offer
does not record, is *excluded* from the weighted average - it does not score 0
and quietly sink the offer. What it does instead is reduce ``coverage``.

**Below a coverage floor, nothing wins.** If the dimensions that actually have
data account for less than ``OFFER_DECISION_MIN_COVERAGE`` of the total weight,
the comparison reports scores but names no winner. A confident ranking built on
a third of the inputs is worse than no ranking.

**Urgency is not quality.** A deadline tomorrow makes an offer *urgent*; it does
not make it *better*. Deadline information travels beside the score and never
inside it.

**A candidate counter is not an offer.** Compensation is always read from the
latest company-origin revision - or, for an accepted offer, from the frozen
``accepted_revision_id``. What you asked for never scores.

**Currencies do not mix without a rate you supplied.** With no FX rate, offers
in different currencies are not comparable on compensation, and the engine says
so rather than inventing one.

**Deal-breakers report, they do not reject.** pass / fail / unknown, surfaced
next to the score. Nothing here declines an offer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from app.models.enums import (
    DERIVED_DIMENSIONS,
    DealBreakerKind,
    DealBreakerResult,
    DecisionDimension,
)

#: Ratings are 1-5; 1 maps to 0.0 and 5 to 1.0 so the worst rating is a real
#: floor rather than a negative contribution.
MIN_RATING = 1
MAX_RATING = 5

#: Weight below which a dimension is treated as "not considered" at all.
WEIGHT_EPSILON = 1e-9

#: Compensation is scored relative to the best comparable offer in the same
#: comparison. Scoring it on an absolute scale would need a salary model the
#: app does not have and the user did not supply.
_COMPENSATION_FLOOR = 0.0


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def rating_to_score(rating: Any) -> float | None:
    """Map a 1-5 rating onto 0-1. Anything outside the scale is 'unrated'."""
    value = _num(rating)
    if value is None:
        return None
    if not (MIN_RATING <= value <= MAX_RATING):
        return None
    return (value - MIN_RATING) / (MAX_RATING - MIN_RATING)


# --------------------------------------------------------------------------
# weights
# --------------------------------------------------------------------------


def normalize_weights(raw: dict[str, Any] | None) -> dict[str, float]:
    """Turn user-entered weights into a distribution summing to 1.

    Negative and non-numeric entries are dropped rather than inverted - a
    "negative importance" has no meaning here and would silently flip a
    dimension's sense. All-zero (or empty) input yields ``{}``: the caller then
    has nothing to rank on and says so, instead of the engine inventing an even
    split the user never asked for.
    """
    cleaned: dict[str, float] = {}
    for key, value in (raw or {}).items():
        try:
            dimension = DecisionDimension(key)
        except ValueError:
            continue  # an unknown dimension is ignored, never guessed at
        weight = _num(value)
        if weight is None or weight <= WEIGHT_EPSILON:
            continue
        cleaned[dimension.value] = weight

    total = sum(cleaned.values())
    if total <= WEIGHT_EPSILON:
        return {}
    return {key: value / total for key, value in cleaned.items()}


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------


@dataclass(slots=True)
class OfferFacts:
    """Everything the engine needs about one offer, already resolved.

    The caller has already picked the right revision (company-origin, or the
    frozen accepted one) and converted compensation if - and only if - a rate
    was supplied.
    """

    offer_id: int
    company: str = ""
    title: str = ""
    currency: str = "CNY"
    #: The revision these figures came from, recorded for the snapshot.
    revision_id: int | None = None

    #: In the offer's own currency.
    guaranteed_cash: float | None = None
    target_cash: float | None = None
    #: Present only when equity could actually be valued.
    equity_annualized: float | None = None
    equity_excluded: bool = False

    #: Converted into the comparison's base currency. ``None`` when no rate was
    #: available, which is what makes the offer non-comparable on compensation.
    comparable_guaranteed_cash: float | None = None
    comparable_target_cash: float | None = None
    fx_rate_used: float | None = None

    remote_policy: str = "unknown"
    work_location: str | None = None
    proposed_start_date: date | None = None
    visa_support: bool | None = None
    #: {dimension: 1..5}, exactly as the human entered them.
    ratings: dict[str, Any] = field(default_factory=dict)

    #: Days until the decision deadline. Carried for display only - it never
    #: enters the score.
    days_to_deadline: int | None = None
    deadline_state: str = "none"


@dataclass(slots=True)
class DealBreaker:
    kind: DealBreakerKind
    value: Any = None


# --------------------------------------------------------------------------
# outputs
# --------------------------------------------------------------------------


@dataclass(slots=True)
class DimensionScore:
    dimension: str
    #: 0-1, or None when there was nothing to score.
    score: float | None
    weight: float
    #: ``weight * score``, or None. This is what the UI shows so the total is
    #: never a black box.
    contribution: float | None
    known: bool
    source: str  # "derived" | "rating" | "missing"
    detail: str = ""


@dataclass(slots=True)
class DealBreakerCheck:
    kind: str
    label: str
    result: DealBreakerResult
    detail: str = ""


@dataclass(slots=True)
class OfferScore:
    offer_id: int
    company: str = ""
    title: str = ""
    #: 0-1 over the dimensions that had data. ``None`` when nothing did.
    total_score: float | None = None
    #: Share of total weight that had data behind it.
    coverage: float = 0.0
    dimension_scores: list[DimensionScore] = field(default_factory=list)
    missing_dimensions: list[str] = field(default_factory=list)
    deal_breakers: list[DealBreakerCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    trade_offs: list[str] = field(default_factory=list)
    #: Display only. Never folded into total_score.
    days_to_deadline: int | None = None
    deadline_state: str = "none"
    #: False when compensation could not be put on a common scale.
    compensation_comparable: bool = True

    @property
    def weighted_contributions(self) -> dict[str, float]:
        return {
            d.dimension: d.contribution
            for d in self.dimension_scores
            if d.contribution is not None
        }


@dataclass(slots=True)
class ComparisonResult:
    offers: list[OfferScore] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    base_currency: str = "CNY"
    #: Only set when coverage is adequate everywhere and a single offer leads.
    winner_offer_id: int | None = None
    #: Why there is no winner, when there is not one.
    winner_blocked_reason: str = ""
    min_coverage: float = 0.7
    mixed_currency: bool = False
    fx_rates_used: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


DIMENSION_LABEL: dict[str, str] = {
    "compensation": "薪酬",
    "career_growth": "职业成长",
    "role_fit": "岗位匹配",
    "remote_work": "远程办公",
    "location": "地点",
    "work_life_balance": "工作生活平衡",
    "company_stability": "公司稳定性",
    "technology_fit": "技术契合",
    "language_environment": "语言环境",
    "visa_support": "签证支持",
    "brand_value": "品牌价值",
}

DEAL_BREAKER_LABEL: dict[str, str] = {
    "minimum_guaranteed_cash": "最低保证现金",
    "requires_visa_support": "需要签证支持",
    "requires_remote_or_hybrid": "需要远程/混合办公",
    "required_location": "指定城市",
    "latest_start_date": "最晚入职日期",
}


# --------------------------------------------------------------------------
# derived dimensions
# --------------------------------------------------------------------------


def _compensation_score(
    facts: OfferFacts, best_guaranteed: float | None, best_target: float | None
) -> tuple[float | None, str]:
    """Score compensation relative to the strongest comparable offer.

    Guaranteed cash dominates; target cash contributes only as a tie-breaker,
    because a target bonus is not money in hand. Unvalued equity contributes
    nothing at all rather than inflating the figure.
    """
    guaranteed = facts.comparable_guaranteed_cash
    if guaranteed is None or best_guaranteed is None or best_guaranteed <= 0:
        return (None, "缺少可比较的保证现金")

    primary = max(_COMPENSATION_FLOOR, min(1.0, guaranteed / best_guaranteed))

    target = facts.comparable_target_cash
    if target is not None and best_target and best_target > 0:
        secondary = max(_COMPENSATION_FLOOR, min(1.0, target / best_target))
        # 4:1 - the guaranteed figure is what the decision should rest on.
        score = 0.8 * primary + 0.2 * secondary
        detail = "以保证现金为主，目标现金次要"
    else:
        score = primary
        detail = "仅基于保证现金"

    if facts.equity_excluded:
        detail += "；股权无法折算，未计入"
    return (score, detail)


def _remote_score(facts: OfferFacts) -> tuple[float | None, str]:
    mapping = {"remote": 1.0, "hybrid": 0.6, "onsite": 0.0}
    if facts.remote_policy not in mapping:
        return (None, "未记录远程政策")
    return (mapping[facts.remote_policy], f"远程政策：{facts.remote_policy}")


def _visa_score(facts: OfferFacts) -> tuple[float | None, str]:
    if facts.visa_support is None:
        return (None, "未记录签证支持")
    return (1.0 if facts.visa_support else 0.0, "提供签证支持" if facts.visa_support else "不提供签证支持")


# --------------------------------------------------------------------------
# deal breakers
# --------------------------------------------------------------------------


def evaluate_deal_breaker(breaker: DealBreaker, facts: OfferFacts) -> DealBreakerCheck:
    """pass / fail / unknown. Never rejects anything.

    ``unknown`` is a real third state: the offer simply does not record the
    fact, and treating that as a failure would penalise incomplete data rather
    than a bad offer.
    """
    kind = breaker.kind
    label = DEAL_BREAKER_LABEL.get(kind.value, kind.value)

    if kind is DealBreakerKind.minimum_guaranteed_cash:
        required = _num(breaker.value)
        actual = facts.comparable_guaranteed_cash
        if required is None:
            return DealBreakerCheck(kind.value, label, DealBreakerResult.unknown, "未设置门槛")
        if actual is None:
            return DealBreakerCheck(
                kind.value, label, DealBreakerResult.unknown, "缺少可比较的保证现金"
            )
        passed = actual >= required
        return DealBreakerCheck(
            kind.value,
            label,
            DealBreakerResult.passed if passed else DealBreakerResult.failed,
            "达到门槛" if passed else "低于门槛",
        )

    if kind is DealBreakerKind.requires_visa_support:
        if facts.visa_support is None:
            return DealBreakerCheck(
                kind.value, label, DealBreakerResult.unknown, "未记录签证支持"
            )
        return DealBreakerCheck(
            kind.value,
            label,
            DealBreakerResult.passed if facts.visa_support else DealBreakerResult.failed,
            "提供" if facts.visa_support else "不提供",
        )

    if kind is DealBreakerKind.requires_remote_or_hybrid:
        if facts.remote_policy == "unknown":
            return DealBreakerCheck(
                kind.value, label, DealBreakerResult.unknown, "未记录远程政策"
            )
        passed = facts.remote_policy in {"remote", "hybrid"}
        return DealBreakerCheck(
            kind.value,
            label,
            DealBreakerResult.passed if passed else DealBreakerResult.failed,
            facts.remote_policy,
        )

    if kind is DealBreakerKind.required_location:
        required = str(breaker.value or "").strip()
        if not required:
            return DealBreakerCheck(kind.value, label, DealBreakerResult.unknown, "未设置城市")
        if not facts.work_location:
            return DealBreakerCheck(
                kind.value, label, DealBreakerResult.unknown, "未记录工作地点"
            )
        passed = required in facts.work_location
        return DealBreakerCheck(
            kind.value,
            label,
            DealBreakerResult.passed if passed else DealBreakerResult.failed,
            facts.work_location,
        )

    if kind is DealBreakerKind.latest_start_date:
        limit = breaker.value
        if isinstance(limit, str):
            try:
                limit = date.fromisoformat(limit)
            except ValueError:
                limit = None
        if not isinstance(limit, date):
            return DealBreakerCheck(kind.value, label, DealBreakerResult.unknown, "未设置日期")
        if facts.proposed_start_date is None:
            return DealBreakerCheck(
                kind.value, label, DealBreakerResult.unknown, "未记录入职日期"
            )
        passed = facts.proposed_start_date <= limit
        return DealBreakerCheck(
            kind.value,
            label,
            DealBreakerResult.passed if passed else DealBreakerResult.failed,
            facts.proposed_start_date.isoformat(),
        )

    return DealBreakerCheck(kind.value, label, DealBreakerResult.unknown)


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------


def score_offer(
    facts: OfferFacts,
    *,
    weights: dict[str, float],
    deal_breakers: Iterable[DealBreaker] = (),
    best_guaranteed: float | None = None,
    best_target: float | None = None,
) -> OfferScore:
    """Score one offer against normalized weights.

    ``weights`` must already be normalized. Dimensions with no data are listed
    in ``missing_dimensions`` and excluded from the average, so the total is
    always over the weight that actually had something behind it.
    """
    dimension_scores: list[DimensionScore] = []
    missing: list[str] = []
    known_weight = 0.0
    weighted_sum = 0.0

    for key, weight in weights.items():
        dimension = DecisionDimension(key)

        if dimension is DecisionDimension.compensation:
            score, detail = _compensation_score(facts, best_guaranteed, best_target)
            source = "derived"
        elif dimension is DecisionDimension.remote_work:
            score, detail = _remote_score(facts)
            source = "derived"
        elif dimension is DecisionDimension.visa_support:
            score, detail = _visa_score(facts)
            source = "derived"
        else:
            score = rating_to_score(facts.ratings.get(key))
            source = "rating"
            detail = "" if score is not None else "未评分"

        # A derived dimension with no fact recorded may still have a rating -
        # the user's own read is a legitimate fallback for remote/visa.
        if score is None and dimension in DERIVED_DIMENSIONS:
            fallback = rating_to_score(facts.ratings.get(key))
            if fallback is not None:
                score, source, detail = fallback, "rating", "使用你的评分"

        if score is None:
            missing.append(key)
            dimension_scores.append(
                DimensionScore(key, None, weight, None, False, "missing", detail)
            )
            continue

        contribution = weight * score
        known_weight += weight
        weighted_sum += contribution
        dimension_scores.append(
            DimensionScore(key, score, weight, contribution, True, source, detail)
        )

    coverage = known_weight if weights else 0.0
    # Renormalize over what was actually known, so a 60%-covered offer is not
    # mechanically worse than a fully-covered one - the honesty lives in
    # `coverage`, which the UI shows alongside.
    total = (weighted_sum / known_weight) if known_weight > WEIGHT_EPSILON else None

    checks = [evaluate_deal_breaker(breaker, facts) for breaker in deal_breakers]

    warnings: list[str] = []
    if not weights:
        warnings.append("还没有设置任何权重，无法计算综合评分。")
    if missing:
        labels = "、".join(DIMENSION_LABEL.get(k, k) for k in missing)
        warnings.append(f"以下维度缺少数据，未计入评分：{labels}。")
    if facts.equity_excluded:
        warnings.append("股权无法折算，未计入薪酬比较。")
    if not facts.compensation_comparable_flag():
        warnings.append("缺少汇率，该 Offer 的薪酬无法与其他币种比较。")
    if any(c.result is DealBreakerResult.failed for c in checks):
        warnings.append("有硬性条件未满足，请自行判断是否仍然考虑。")

    strengths, trade_offs = _strengths_and_trade_offs(dimension_scores)

    return OfferScore(
        offer_id=facts.offer_id,
        company=facts.company,
        title=facts.title,
        total_score=total,
        coverage=coverage,
        dimension_scores=dimension_scores,
        missing_dimensions=missing,
        deal_breakers=checks,
        warnings=warnings,
        strengths=strengths,
        trade_offs=trade_offs,
        days_to_deadline=facts.days_to_deadline,
        deadline_state=facts.deadline_state,
        compensation_comparable=facts.compensation_comparable_flag(),
    )


def _strengths_and_trade_offs(
    scores: list[DimensionScore], *, top: int = 3
) -> tuple[list[str], list[str]]:
    """Template sentences naming where an offer is strong and where it gives up.

    Ranked by *contribution*, not raw score: a dimension the user weighted at
    2% is not a headline no matter how well it scores.
    """
    known = [d for d in scores if d.known and d.contribution is not None]
    if not known:
        return ([], [])

    by_contribution = sorted(known, key=lambda d: d.contribution, reverse=True)
    strengths = [
        f"{DIMENSION_LABEL.get(d.dimension, d.dimension)}（{round(d.score * 100)}分）"
        for d in by_contribution[:top]
        if d.score >= 0.6
    ]
    weakest = sorted(known, key=lambda d: (d.score, -d.weight))
    trade_offs = [
        f"{DIMENSION_LABEL.get(d.dimension, d.dimension)}（{round(d.score * 100)}分）"
        for d in weakest[:top]
        if d.score <= 0.4
    ]
    return (strengths, trade_offs)


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------


def compare_offers(
    offers: list[OfferFacts],
    *,
    raw_weights: dict[str, Any] | None,
    deal_breakers: Iterable[DealBreaker] = (),
    base_currency: str = "CNY",
    fx_rates: dict[str, float] | None = None,
    min_coverage: float = 0.7,
) -> ComparisonResult:
    """Score a set of offers together and decide whether a winner may be named.

    Compensation is relative, so it can only be scored across the set - which
    is why this is one call rather than N independent ones.
    """
    weights = normalize_weights(raw_weights)
    breakers = list(deal_breakers)

    comparable = [o for o in offers if o.comparable_guaranteed_cash is not None]
    best_guaranteed = max(
        (o.comparable_guaranteed_cash for o in comparable), default=None
    )
    best_target = max(
        (o.comparable_target_cash for o in comparable if o.comparable_target_cash),
        default=None,
    )

    scored = [
        score_offer(
            facts,
            weights=weights,
            deal_breakers=breakers,
            best_guaranteed=best_guaranteed,
            best_target=best_target,
        )
        for facts in offers
    ]

    currencies = {o.currency for o in offers}
    mixed = len(currencies) > 1

    notes: list[str] = []
    if mixed and fx_rates:
        notes.append(
            "跨币种金额使用了你填写的汇率换算，结果完全取决于该汇率是否合适。"
        )
    elif mixed:
        notes.append(
            "所选 Offer 涉及多种币种且未填写汇率，薪酬维度无法比较 —— "
            "系统不会自行使用任何汇率。"
        )

    winner_id, blocked = _pick_winner(scored, min_coverage=min_coverage)
    if blocked:
        notes.append(blocked)

    return ComparisonResult(
        offers=scored,
        weights=weights,
        base_currency=base_currency,
        winner_offer_id=winner_id,
        winner_blocked_reason=blocked,
        min_coverage=min_coverage,
        mixed_currency=mixed,
        fx_rates_used=dict(fx_rates or {}),
        notes=notes,
    )


def _pick_winner(
    scored: list[OfferScore], *, min_coverage: float
) -> tuple[int | None, str]:
    """Name a leader only when the data supports one.

    Three separate gates, each of which alone is enough to withhold a verdict:
    no weights, thin coverage, or a tie. A ranking asserted over missing inputs
    is the failure mode this whole module exists to avoid.
    """
    if not scored:
        return (None, "")

    if not any(s.total_score is not None for s in scored):
        return (None, "还没有足够的数据来计算综合评分。")

    thin = [s for s in scored if s.coverage < min_coverage]
    if thin:
        names = "、".join(s.company for s in thin) or "部分 Offer"
        return (
            None,
            f"「{names}」的信息覆盖率低于 {round(min_coverage * 100)}%，"
            "暂不给出综合结论。补充评分或缺失字段后可以再看。",
        )

    ranked = sorted(
        (s for s in scored if s.total_score is not None),
        key=lambda s: s.total_score,
        reverse=True,
    )
    if len(ranked) >= 2 and abs(ranked[0].total_score - ranked[1].total_score) < 1e-6:
        return (None, "综合评分持平，没有明显更优的一个。")

    return (ranked[0].offer_id, "")


# Attached rather than defined inline so ``OfferFacts`` stays a plain data
# holder: whether compensation is comparable is a question about the
# comparison, not a property of the offer on its own.
def _compensation_comparable_flag(self: OfferFacts) -> bool:
    """False when there is a figure that could not be put on a common scale."""
    return not (
        self.guaranteed_cash is not None and self.comparable_guaranteed_cash is None
    )


OfferFacts.compensation_comparable_flag = _compensation_comparable_flag  # type: ignore[attr-defined]
