"""Strategy proposals derived from outcomes (v0.6).

    DATA OBSERVES -> SYSTEM SUGGESTS -> HUMAN DECIDES

Nothing here writes ``career_strategy.yaml``. A proposal is a suggestion with
its evidence attached; applying one requires an explicit confirmed call, which
then goes through the existing ``core.career_strategy.save_strategy`` service
and records a ``CareerStrategyChange`` audit row.

The rules are deliberately strict. "杭州 is great, 2/3 replied" is exactly the
mistake this module exists to prevent, so a cohort must clear three bars:

1. at least ``ANALYTICS_RECOMMEND_SAMPLE`` mature applications;
2. a Wilson **lower** bound above the overall rate (not just a higher点估计);
3. a non-``insufficient`` confidence band.

Everything below that becomes a ``collect_more_data`` note instead.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.career_strategy import load_strategy, save_strategy, strategy_hash
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.models import (
    CareerStrategyChange,
    RecommendationDecision,
    StrategyChangeSource,
    StrategyRecommendationDecision,
)
from app.schemas.analytics import (
    CareerAnalyticsResult,
    CohortStat,
    ProposalEvidence,
    ProposalType,
    StrategyAdjustmentProposal,
    StrategyDiff,
    TimeWindow,
)
from app.services.statistics import (
    Confidence,
    is_meaningfully_better,
    is_meaningfully_worse,
)

logger = get_logger(__name__)

#: A skill must be missing from this many high-scoring jobs to be worth naming.
SKILL_CANDIDATE_MIN = 3


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


def make_signature(
    proposal_type: ProposalType, target: str, evidence: ProposalEvidence
) -> str:
    """Semantic signature: same suggestion + same-ish evidence = same signature.

    The denominator is bucketed rather than exact so one extra application does
    not resurrect a dismissed proposal, while a genuine change in the evidence
    (a new bucket) legitimately produces a new one.
    """
    bucket = evidence.denominator // 5
    payload = "|".join(
        [
            proposal_type.value,
            target,
            evidence.metric,
            evidence.window.value,
            str(bucket),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _evidence(cohort: CohortStat, window: TimeWindow, baseline: float | None) -> ProposalEvidence:
    stat = cohort.mature_reply_rate
    return ProposalEvidence(
        metric="mature_reply_rate",
        numerator=stat.numerator,
        denominator=stat.denominator,
        rate=stat.rate,
        ci_low=stat.ci_low,
        ci_high=stat.ci_high,
        comparison_rate=baseline,
        window=window,
    )


def _qualifies(cohort: CohortStat, *, cfg: Settings) -> bool:
    stat = cohort.mature_reply_rate
    return (
        stat.denominator >= cfg.analytics_recommend_sample
        and stat.confidence is not Confidence.insufficient
    )


# --------------------------------------------------------------------------
# proposal generation
# --------------------------------------------------------------------------


def build_proposals(
    result: CareerAnalyticsResult, *, settings: Settings | None = None
) -> list[StrategyAdjustmentProposal]:
    """Deterministic proposals. Returns [] when the data cannot support any."""
    cfg = settings or get_settings()
    baseline = result.summary.mature_reply_rate.rate
    strategy = load_strategy()
    proposals: list[StrategyAdjustmentProposal] = []

    proposals.extend(_city_proposals(result, strategy, baseline, cfg))
    proposals.extend(_role_proposals(result, baseline, cfg))
    proposals.extend(_source_proposals(result, baseline, cfg))
    proposals.extend(_score_floor_proposals(result, cfg))
    proposals.extend(_skill_proposals(result, cfg))
    proposals.extend(_collect_more_data(result, cfg))
    return proposals


def _city_proposals(
    result: CareerAnalyticsResult,
    strategy: dict[str, Any],
    baseline: float | None,
    cfg: Settings,
) -> list[StrategyAdjustmentProposal]:
    cities: list[str] = list(strategy.get("target_cities") or [])
    out: list[StrategyAdjustmentProposal] = []

    for cohort in result.by_city:
        if not _qualifies(cohort, cfg=cfg):
            continue
        stat = cohort.mature_reply_rate
        better = is_meaningfully_better(stat.numerator, stat.denominator, baseline_rate=baseline)
        worse = is_meaningfully_worse(stat.numerator, stat.denominator, baseline_rate=baseline)
        if not (better or worse):
            continue
        if cohort.key not in cities:
            continue  # only reorder cities the user actually targets

        reordered = [cohort.key] + [c for c in cities if c != cohort.key]
        if worse:
            reordered = [c for c in cities if c != cohort.key] + [cohort.key]

        proposal_type = (
            ProposalType.increase_city_priority if better else ProposalType.decrease_city_priority
        )
        evidence = _evidence(cohort, result.window, baseline)
        direction = "高于" if better else "低于"
        out.append(
            StrategyAdjustmentProposal(
                signature=make_signature(proposal_type, cohort.key, evidence),
                type=proposal_type,
                target=cohort.key,
                current_value=cities,
                suggested_value=reordered,
                reason=(
                    f"「{cohort.label}」的成熟回复率 {_pct(stat.rate)}"
                    f"（{stat.numerator}/{stat.denominator}），{direction}整体的 {_pct(baseline)}，"
                    f"95% 置信区间 {_pct(stat.ci_low)}–{_pct(stat.ci_high)}。"
                ),
                evidence=evidence,
                sample_size=stat.denominator,
                confidence=stat.confidence,
                impact_description=(
                    "调整 target_cities 的顺序（第一个最优先）。这只影响你自己的优先级参考，"
                    "不会自动改变任何岗位的状态。"
                ),
                applicable=True,
            )
        )
    return out


def _role_proposals(
    result: CareerAnalyticsResult, baseline: float | None, cfg: Settings
) -> list[StrategyAdjustmentProposal]:
    out: list[StrategyAdjustmentProposal] = []
    for cohort in result.by_role_family:
        if not _qualifies(cohort, cfg=cfg):
            continue
        stat = cohort.mature_reply_rate
        better = is_meaningfully_better(stat.numerator, stat.denominator, baseline_rate=baseline)
        worse = is_meaningfully_worse(stat.numerator, stat.denominator, baseline_rate=baseline)
        if not (better or worse):
            continue

        proposal_type = (
            ProposalType.increase_role_priority if better else ProposalType.decrease_role_priority
        )
        evidence = _evidence(cohort, result.window, baseline)
        direction = "高于" if better else "低于"
        out.append(
            StrategyAdjustmentProposal(
                signature=make_signature(proposal_type, cohort.key, evidence),
                type=proposal_type,
                target=cohort.key,
                reason=(
                    f"{cohort.label} 方向的成熟回复率 {_pct(stat.rate)}"
                    f"（{stat.numerator}/{stat.denominator}），{direction}整体的 {_pct(baseline)}。"
                ),
                evidence=evidence,
                sample_size=stat.denominator,
                confidence=stat.confidence,
                impact_description=(
                    "建议在接下来的投递中相应调整这个方向的比重。"
                    "这是一条参考建议，不会自动修改求职策略。"
                ),
                applicable=False,
            )
        )
    return out


def _source_proposals(
    result: CareerAnalyticsResult, baseline: float | None, cfg: Settings
) -> list[StrategyAdjustmentProposal]:
    out: list[StrategyAdjustmentProposal] = []
    for cohort in result.by_source:
        if not _qualifies(cohort, cfg=cfg):
            continue
        stat = cohort.mature_reply_rate
        better = is_meaningfully_better(stat.numerator, stat.denominator, baseline_rate=baseline)
        worse = is_meaningfully_worse(stat.numerator, stat.denominator, baseline_rate=baseline)
        if not (better or worse):
            continue

        proposal_type = (
            ProposalType.prioritize_source if better else ProposalType.deprioritize_source
        )
        evidence = _evidence(cohort, result.window, baseline)
        verb = "更值得投入时间" if better else "转化偏低"
        out.append(
            StrategyAdjustmentProposal(
                signature=make_signature(proposal_type, cohort.key, evidence),
                type=proposal_type,
                target=cohort.key,
                reason=(
                    f"来自「{cohort.label}」的岗位成熟回复率 {_pct(stat.rate)}"
                    f"（{stat.numerator}/{stat.denominator}），{verb}。"
                ),
                evidence=evidence,
                sample_size=stat.denominator,
                confidence=stat.confidence,
                impact_description=(
                    "仅说明该来源目前的转化表现；来源只代表机会从哪里来，"
                    "不代表 JobAgent 会去操作那个平台。"
                ),
                applicable=False,
            )
        )
    return out


def _score_floor_proposals(
    result: CareerAnalyticsResult, cfg: Settings
) -> list[StrategyAdjustmentProposal]:
    """Observe whether the match score predicts outcomes. Never auto-tunes it."""
    bands = {b.key: b for b in result.by_score_band}
    high = [bands[k] for k in ("90-100", "80-89") if k in bands]
    mid = bands.get("70-79")
    low = [bands[k] for k in ("60-69", "<60") if k in bands]

    low_denominator = sum(b.mature_reply_rate.denominator for b in low)
    low_replies = sum(b.mature_reply_rate.numerator for b in low)
    high_denominator = sum(b.mature_reply_rate.denominator for b in high)
    high_replies = sum(b.mature_reply_rate.numerator for b in high)

    if low_denominator < cfg.analytics_recommend_sample:
        return []

    from app.services.statistics import rate as ratio

    low_rate = ratio(low_replies, low_denominator)
    high_rate = ratio(high_replies, high_denominator)
    evidence = ProposalEvidence(
        metric="score_band_mature_reply_rate",
        numerator=low_replies,
        denominator=low_denominator,
        rate=low_rate,
        comparison_rate=high_rate,
        window=result.window,
    )

    if high_rate is not None and low_rate is not None and low_rate + 0.10 < high_rate:
        reason = (
            f"70 分以下岗位累计 {low_denominator} 次成熟投递，仅 {low_replies} 次回复"
            f"（{_pct(low_rate)}），明显低于 80 分以上的 {_pct(high_rate)}。"
        )
        impact = (
            "可考虑把人工优先审查线保持在 70 分以上。"
            "建议只调整审查优先级，不要自动屏蔽低分岗位。"
        )
    elif high_rate is not None and low_rate is not None and low_rate >= high_rate:
        reason = (
            f"70 分以下岗位的成熟回复率 {_pct(low_rate)}"
            f"（{low_replies}/{low_denominator}），并不低于高分段的 {_pct(high_rate)}。"
        )
        impact = "匹配分目前对结果的区分度有限，可考虑放宽人工筛选门槛，并继续观察。"
    else:
        return []

    mid_note = ""
    if mid and mid.mature_reply_rate.denominator:
        mid_note = (
            f" 70–79 分区间为 {_pct(mid.mature_reply_rate.rate)}"
            f"（{mid.mature_reply_rate.numerator}/{mid.mature_reply_rate.denominator}）。"
        )

    return [
        StrategyAdjustmentProposal(
            signature=make_signature(ProposalType.consider_score_floor_change, "score_floor", evidence),
            type=ProposalType.consider_score_floor_change,
            target="score_floor",
            reason=reason + mid_note,
            evidence=evidence,
            sample_size=low_denominator,
            confidence=result.by_score_band[0].mature_reply_rate.confidence
            if result.by_score_band
            else Confidence.insufficient,
            impact_description=impact,
            applicable=False,
        )
    ]


def _skill_proposals(
    result: CareerAnalyticsResult, cfg: Settings
) -> list[StrategyAdjustmentProposal]:
    """Name skills that keep coming up as gaps. Descriptive, not causal."""
    out: list[StrategyAdjustmentProposal] = []
    for item in result.skills.missing_in_high_score_jobs[:3]:
        if item.count < SKILL_CANDIDATE_MIN:
            continue
        evidence = ProposalEvidence(
            metric="missing_skill_in_high_score_jobs",
            numerator=item.count,
            denominator=result.summary.applications,
            window=result.window,
        )
        out.append(
            StrategyAdjustmentProposal(
                signature=make_signature(
                    ProposalType.skill_learning_candidate, item.key, evidence
                ),
                type=ProposalType.skill_learning_candidate,
                target=item.key,
                reason=(
                    f"在 80 分以上的岗位中，「{item.label}」作为缺失技能出现了 {item.count} 次。"
                ),
                evidence=evidence,
                sample_size=item.count,
                confidence=Confidence.low,
                impact_description=(
                    "可作为学习候选方向。这只是出现频率的统计，"
                    "并不意味着补上这项技能就一定会带来面试。"
                ),
                applicable=False,
            )
        )
    return out


def _collect_more_data(
    result: CareerAnalyticsResult, cfg: Settings
) -> list[StrategyAdjustmentProposal]:
    """The honest default: say so when the evidence is thin."""
    promising: list[CohortStat] = []
    baseline = result.summary.mature_reply_rate.rate

    for cohort in list(result.by_city) + list(result.by_city_role):
        stat = cohort.mature_reply_rate
        if stat.denominator == 0 or stat.denominator >= cfg.analytics_recommend_sample:
            continue
        if stat.rate is not None and baseline is not None and stat.rate > baseline:
            promising.append(cohort)

    out: list[StrategyAdjustmentProposal] = []
    for cohort in promising[:3]:
        stat = cohort.mature_reply_rate
        evidence = _evidence(cohort, result.window, baseline)
        out.append(
            StrategyAdjustmentProposal(
                signature=make_signature(
                    ProposalType.collect_more_data, cohort.key, evidence
                ),
                type=ProposalType.collect_more_data,
                target=cohort.key,
                reason=(
                    f"「{cohort.label}」当前表现较好（{stat.numerator}/{stat.denominator}），"
                    f"但只有 {stat.denominator} 个成熟样本，还不足以作为调整依据。"
                ),
                evidence=evidence,
                sample_size=stat.denominator,
                confidence=stat.confidence,
                impact_description="建议继续在这个方向投递，积累到足够样本后再判断。",
                applicable=False,
            )
        )
    return out


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------


def load_decisions(db: Session) -> dict[str, str]:
    return {
        row.signature: row.decision.value
        for row in db.scalars(select(StrategyRecommendationDecision))
    }


def visible_proposals(
    db: Session, result: CareerAnalyticsResult, *, settings: Settings | None = None
) -> tuple[list[StrategyAdjustmentProposal], int]:
    """Proposals minus the ones already answered. Returns ``(visible, hidden)``."""
    proposals = build_proposals(result, settings=settings)
    decisions = load_decisions(db)

    visible: list[StrategyAdjustmentProposal] = []
    hidden = 0
    for proposal in proposals:
        decision = decisions.get(proposal.signature)
        if decision == RecommendationDecision.dismissed.value:
            hidden += 1
            continue
        proposal.decision = decision
        visible.append(proposal)
    return visible, hidden


def find_proposal(
    db: Session, result: CareerAnalyticsResult, signature: str, *, settings: Settings | None = None
) -> StrategyAdjustmentProposal:
    for proposal in build_proposals(result, settings=settings):
        if proposal.signature == signature:
            return proposal
    raise NotFoundError(
        "该建议已不再适用（可能是数据已经变化）。请刷新策略分析页面。",
        detail={"signature": signature},
    )


def build_diff(proposal: StrategyAdjustmentProposal) -> list[StrategyDiff]:
    """Exactly what applying would change - shown before anything is written."""
    if not proposal.applicable:
        return []
    return [
        StrategyDiff(
            signature=proposal.signature,
            field="target_cities",
            before=proposal.current_value,
            after=proposal.suggested_value,
            description="调整目标城市的优先级顺序（第一个最优先）",
        )
    ]


def record_decision(
    db: Session, signature: str, decision: RecommendationDecision, *, note: str | None = None
) -> StrategyRecommendationDecision:
    row = db.scalar(
        select(StrategyRecommendationDecision).where(
            StrategyRecommendationDecision.signature == signature
        )
    )
    if row is None:
        row = StrategyRecommendationDecision(
            signature=signature, decision=decision, created_at=datetime.now(timezone.utc)
        )
        db.add(row)
    row.decision = decision
    row.notes = note
    db.commit()
    db.refresh(row)
    log_event(
        logger, "analytics.recommendation_decided", signature=signature, decision=decision.value
    )
    return row


def apply_proposal(
    db: Session,
    proposal: StrategyAdjustmentProposal,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    """Write the change through the existing strategy service, and audit it.

    Only ever reached from a confirmed request. Routes never touch the YAML.
    """
    if not proposal.applicable:
        raise ValidationError(
            "这条建议是参考性的，没有可直接应用的策略修改。",
            detail={"signature": proposal.signature, "type": proposal.type.value},
        )

    before = load_strategy()
    before_hash = strategy_hash(before)

    updated = dict(before)
    updated["target_cities"] = list(proposal.suggested_value or before.get("target_cities") or [])
    after = save_strategy(updated)
    after_hash = strategy_hash(after)

    db.add(
        CareerStrategyChange(
            before_hash=before_hash,
            after_hash=after_hash,
            before_json=before,
            after_json=after,
            source=StrategyChangeSource.analytics_recommendation,
            recommendation_signature=proposal.signature,
            notes=note or proposal.reason,
            created_at=datetime.now(timezone.utc),
        )
    )
    record_decision(db, proposal.signature, RecommendationDecision.accepted, note=note)

    log_event(
        logger,
        "analytics.strategy_applied",
        signature=proposal.signature,
        type=proposal.type.value,
        before=before_hash[:12],
        after=after_hash[:12],
    )
    return after


def strategy_changes(db: Session) -> list[CareerStrategyChange]:
    return list(
        db.scalars(select(CareerStrategyChange).order_by(CareerStrategyChange.created_at.desc()))
    )
