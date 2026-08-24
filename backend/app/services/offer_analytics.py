"""Offer analytics (v0.9).

**Zero OpenAI calls**, like v0.6 through v0.8. Every number is a count, a
median or a ratio over rows the human entered, and the statistics come from
``statistics.py`` - same maturity windows, Wilson intervals, confidence tiers
and "tier first, then lower bound" ranking. There is no second definition of
confidence.

Three rules specific to money:

**Currencies are never mixed.** Every compensation figure is reported inside
one currency bucket. v0.9 fetches no FX rates, so ranking 400K CNY against
8M JPY would be inventing an exchange rate the user never supplied.

**Accepted compensation comes from the frozen snapshot.** What was accepted is
read through ``Offer.accepted_revision_id``, never recomputed from whatever
revision happens to be latest. A correction entered next month must not rewrite
what a past decision was made on.

**A candidate counter is not an offer.** Everything that reads "what was
offered" goes through ``offer_management.latest_company_revision``.

Compensation values appear in this payload because the page needs them. They
are never written to a log.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.models import (
    DECLINE_REASON_LABEL,
    DeclineReason,
    Offer,
    OfferStatus,
    Resume,
)
from app.schemas.analytics import (
    CohortStat,
    CountItem,
    CurrencyCompensation,
    MoneyStat,
    NegotiationUpliftStat,
    ObservationKind,
    OfferAnalyticsResult,
    OfferCohortStat,
    OfferFunnel,
    StrategyObservation,
    TimeWindow,
)
from app.services.application_analytics import (
    SOURCE_LABEL,
    AnalyticsFilters,
    _cohort,
    _coverage,
    build_rate,
    build_records,
)
from app.services.interview_analytics import ProcessRecord, load_processes_by_cycle
from app.services.offer_management import (
    breakdown_of,
    latest_company_revision,
    negotiation_summary,
)
from app.services.statistics import Confidence, percentiles


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# the unit of analysis
# --------------------------------------------------------------------------


class OfferRecord:
    """One application, its interview process, and its offer - same cycle."""

    __slots__ = ("process", "offer", "_company", "_accepted")

    def __init__(self, process: ProcessRecord, offer: Offer | None):
        self.process = process
        self.offer = offer
        # The company's current offer, never a candidate counter.
        self._company = latest_company_revision(offer) if offer else None
        self._accepted = None
        if offer is not None and offer.accepted_revision_id is not None:
            self._accepted = next(
                (r for r in offer.revisions if r.id == offer.accepted_revision_id), None
            )

    @property
    def record(self):
        return self.process.record

    @property
    def has_offer(self) -> bool:
        return self.offer is not None

    @property
    def accepted(self) -> bool:
        return self.offer is not None and self.offer.status is OfferStatus.accepted

    @property
    def declined(self) -> bool:
        return self.offer is not None and self.offer.status is OfferStatus.declined

    @property
    def currency(self) -> str | None:
        return self.offer.currency.value if self.offer else None

    def comparable_revision(self):
        """The revision analytics should read.

        For an accepted offer that is the frozen ``accepted_revision_id``; for
        everything else it is the company's current offer. Reading "latest"
        would let a later edit rewrite a past decision.
        """
        return self._accepted or self._company

    def breakdown(self):
        return breakdown_of(self.comparable_revision())


def build_offer_records(
    db: Session, filters: AnalyticsFilters, *, now: datetime | None = None
) -> list[OfferRecord]:
    """Every application in the window, paired with its own cycle's offer."""
    moment = now or datetime.now(timezone.utc)
    records = build_records(db, filters, now=moment)
    processes = load_processes_by_cycle(db)
    offers = {
        o.applied_event_id: o
        for o in db.scalars(select(Offer).options(selectinload(Offer.revisions))).unique()
    }
    return [
        OfferRecord(
            ProcessRecord(record, processes.get(record.cycle.applied_event_id)),
            offers.get(record.cycle.applied_event_id),
        )
        for record in records
    ]


# --------------------------------------------------------------------------
# money, one currency at a time
# --------------------------------------------------------------------------


def money_stat(currency: str, values: Iterable[float | None]) -> MoneyStat | None:
    """Median-first distribution. ``None`` when nothing was filled in."""
    present = sorted(v for v in values if v is not None)
    if not present:
        return None
    p25, median, p75 = percentiles(present)
    return MoneyStat(
        currency=currency,
        sample=len(present),
        median=median,
        p25=p25,
        p75=p75,
        minimum=present[0],
        maximum=present[-1],
    )


def _compensation_by_currency(rows: list[OfferRecord]) -> list[CurrencyCompensation]:
    """Group compensation strictly within each currency."""
    buckets: dict[str, list[OfferRecord]] = defaultdict(list)
    for row in rows:
        if row.currency:
            buckets[row.currency].append(row)

    out: list[CurrencyCompensation] = []
    for currency in sorted(buckets):
        items = buckets[currency]
        breakdowns = [row.breakdown() for row in items]
        present = [b for b in breakdowns if b is not None]
        out.append(
            CurrencyCompensation(
                currency=currency,
                offers=len(items),
                base_annual=money_stat(currency, (b.base_annual for b in present)),
                first_year_guaranteed=money_stat(
                    currency, (b.first_year_guaranteed_cash for b in present)
                ),
                first_year_target=money_stat(
                    currency, (b.first_year_target_cash for b in present)
                ),
                estimated_total_comp=money_stat(
                    currency, (b.estimated_first_year_total_comp for b in present)
                ),
            )
        )
    return out


def _negotiation_by_currency(rows: list[OfferRecord]) -> list[NegotiationUpliftStat]:
    """Uplift between the first and current *company* offers.

    Only offers that recorded a counter followed by a company revision count
    toward ``with_full_sequence`` - a change without that ordering is a change,
    not evidence that negotiating produced it.
    """
    buckets: dict[str, list[Offer]] = defaultdict(list)
    for row in rows:
        if row.offer is not None and row.currency:
            buckets[row.currency].append(row.offer)

    out: list[NegotiationUpliftStat] = []
    for currency in sorted(buckets):
        base_abs: list[float] = []
        base_pct: list[float] = []
        guaranteed_abs: list[float] = []
        signing_gained = 0
        full_sequence = 0

        for offer in buckets[currency]:
            summary = negotiation_summary(offer)
            if summary.has_negotiation_sequence:
                full_sequence += 1
            if summary.base and summary.base.absolute is not None:
                base_abs.append(summary.base.absolute)
                if summary.base.percentage is not None:
                    base_pct.append(summary.base.percentage)
            if (
                summary.first_year_guaranteed
                and summary.first_year_guaranteed.absolute is not None
            ):
                guaranteed_abs.append(summary.first_year_guaranteed.absolute)
            if summary.signing_bonus_gained:
                signing_gained += 1

        if not (base_abs or guaranteed_abs or full_sequence):
            continue

        _, base_median, _ = percentiles(base_abs)
        _, pct_median, _ = percentiles(base_pct)
        _, guaranteed_median, _ = percentiles(guaranteed_abs)
        out.append(
            NegotiationUpliftStat(
                currency=currency,
                sample=len(base_abs),
                median_base_uplift=base_median,
                median_base_uplift_pct=pct_median,
                median_guaranteed_uplift=guaranteed_median,
                offers_with_signing_gained=signing_gained,
                with_full_sequence=full_sequence,
            )
        )
    return out


# --------------------------------------------------------------------------
# funnel and dimensions
# --------------------------------------------------------------------------


def _stat(successes: int, trials: int, cfg: Settings):
    return build_rate(
        successes,
        trials,
        min_sample=cfg.analytics_min_sample,
        recommend_sample=cfg.analytics_recommend_sample,
    )


def build_funnel(rows: list[OfferRecord], *, cfg: Settings) -> OfferFunnel:
    applications = len(rows)
    interviewed = sum(1 for r in rows if r.process.reached_any_interview)
    offers = sum(1 for r in rows if r.has_offer)
    accepted = sum(1 for r in rows if r.accepted)
    declined = sum(1 for r in rows if r.declined)
    decided = accepted + declined

    return OfferFunnel(
        applications=applications,
        reached_any_interview=interviewed,
        offers=offers,
        accepted=accepted,
        declined=declined,
        withdrawn=sum(
            1 for r in rows if r.offer and r.offer.status is OfferStatus.withdrawn
        ),
        expired=sum(1 for r in rows if r.offer and r.offer.status is OfferStatus.expired),
        pending=sum(
            1
            for r in rows
            if r.offer
            and r.offer.status in {OfferStatus.received, OfferStatus.negotiating}
        ),
        application_to_offer=_stat(offers, applications, cfg),
        interview_to_offer=_stat(offers, interviewed, cfg),
        # Denominator is offers actually decided - a pending offer has not been
        # turned down, it just has not been answered yet.
        offer_acceptance_rate=_stat(accepted, decided, cfg),
    )


def _offer_cohort(
    key: str,
    label: str,
    rows: list[OfferRecord],
    *,
    now: datetime,
    cfg: Settings,
    resume: Resume | None = None,
) -> OfferCohortStat:
    base: CohortStat = _cohort(key, label, [r.record for r in rows], now=now, cfg=cfg)
    offers = sum(1 for r in rows if r.has_offer)
    interviewed = sum(1 for r in rows if r.process.reached_any_interview)

    return OfferCohortStat(
        **base.model_dump(),
        offers_recorded=offers,
        accepted=sum(1 for r in rows if r.accepted),
        declined=sum(1 for r in rows if r.declined),
        offer_reach_rate=_stat(offers, len(rows), cfg),
        interview_to_offer_rate=_stat(offers, interviewed, cfg),
        resume_id=resume.id if resume else None,
        resume_archived=bool(resume and resume.archived),
    )


def _rank(cohorts: list[OfferCohortStat]) -> list[OfferCohortStat]:
    """v0.6 rule, unchanged: confidence tier first, then the Wilson lower bound."""
    cohorts.sort(
        key=lambda c: (
            c.offer_reach_rate.confidence is not Confidence.insufficient,
            c.offer_reach_rate.ranking_score,
            c.applications,
        ),
        reverse=True,
    )
    return cohorts


def _group(
    rows: list[OfferRecord],
    key_of: Callable[[OfferRecord], str | None],
    *,
    label_of: Callable[[str], str],
    now: datetime,
    cfg: Settings,
    resumes: dict[int, Resume] | None = None,
) -> list[OfferCohortStat]:
    buckets: dict[str, list[OfferRecord]] = defaultdict(list)
    for row in rows:
        key = key_of(row)
        if key is not None:
            buckets[key].append(row)

    return _rank(
        [
            _offer_cohort(
                key,
                label_of(key),
                items,
                now=now,
                cfg=cfg,
                resume=(resumes or {}).get(int(key))
                if resumes and key.isdigit()
                else None,
            )
            for key, items in buckets.items()
        ]
    )


# --------------------------------------------------------------------------
# observations (templates, never an LLM)
# --------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


def build_observations(
    result: OfferAnalyticsResult, *, cfg: Settings
) -> list[StrategyObservation]:
    observations: list[StrategyObservation] = []
    funnel = result.funnel

    if funnel.applications == 0:
        return [
            StrategyObservation(
                kind=ObservationKind.insufficient_data,
                dimension="offer",
                target="全部",
                text="当前时间窗内还没有投递记录，暂时无法分析 Offer。",
                metric="applications",
            )
        ]

    stat = funnel.application_to_offer
    if stat.denominator:
        observations.append(
            StrategyObservation(
                kind=ObservationKind.descriptive,
                dimension="offer",
                target="Offer 转化",
                text=(
                    f"{stat.denominator} 次投递中收到 {stat.numerator} 个 Offer"
                    f"（{_pct(stat.rate)}）。"
                ),
                metric="application_to_offer",
                numerator=stat.numerator,
                denominator=stat.denominator,
                confidence=stat.confidence,
            )
        )

    for entry in result.compensation:
        base = entry.base_annual
        if base is None or base.sample < cfg.analytics_min_sample:
            if base is not None:
                observations.append(
                    StrategyObservation(
                        kind=ObservationKind.insufficient_data,
                        dimension="compensation",
                        target=entry.currency,
                        text=(
                            f"{entry.currency} 的 Offer 只有 {base.sample} 个，"
                            "样本不足，中位数仅供参考。"
                        ),
                        metric="base_annual_median",
                        numerator=base.sample,
                        denominator=base.sample,
                    )
                )
            continue
        observations.append(
            StrategyObservation(
                kind=ObservationKind.descriptive,
                dimension="compensation",
                target=entry.currency,
                text=(
                    f"{entry.currency}：{base.sample} 个 Offer 的基础年薪中位数为 "
                    f"{base.median:,.0f}（P25 {base.p25:,.0f} / P75 {base.p75:,.0f}）。"
                ),
                metric="base_annual_median",
                numerator=base.sample,
                denominator=base.sample,
            )
        )

    for entry in result.negotiation:
        if entry.with_full_sequence == 0:
            continue
        if entry.median_base_uplift is None:
            continue
        observations.append(
            StrategyObservation(
                kind=ObservationKind.descriptive,
                dimension="negotiation",
                target=entry.currency,
                text=(
                    f"{entry.currency}：有完整谈薪记录的 {entry.with_full_sequence} 个 Offer 中，"
                    f"基础年薪较初始 Offer 的中位变化为 {entry.median_base_uplift:,.0f}"
                    + (
                        f"（{_pct(entry.median_base_uplift_pct)}）。"
                        if entry.median_base_uplift_pct is not None
                        else "。"
                    )
                ),
                metric="base_uplift",
                numerator=entry.with_full_sequence,
                denominator=entry.sample,
            )
        )

    if result.decline_reasons:
        top = result.decline_reasons[0]
        observations.append(
            StrategyObservation(
                kind=ObservationKind.descriptive,
                dimension="decline_reason",
                target=top.label,
                text=f"拒绝 Offer 最常见的原因是「{top.label}」，共 {top.count} 次。",
                metric="decline_reason_count",
                numerator=top.count,
                denominator=funnel.declined,
            )
        )

    coverage = result.resume_attribution_coverage
    if coverage.total and (coverage.ratio or 0) < 1.0:
        missing = coverage.total - coverage.covered
        observations.append(
            StrategyObservation(
                kind=ObservationKind.insufficient_data,
                dimension="resume_attribution",
                target="未记录简历",
                text=(
                    f"有 {missing}/{coverage.total} 次投递没有记录使用的简历，"
                    "按简历比较 Offer 转化的可信度有限。"
                ),
                metric="resume_attribution_coverage",
                numerator=coverage.covered,
                denominator=coverage.total,
            )
        )

    if result.legacy_offer_events:
        observations.append(
            StrategyObservation(
                kind=ObservationKind.insufficient_data,
                dimension="legacy",
                target="历史 Offer 记录",
                text=(
                    f"有 {result.legacy_offer_events} 条旧版 Offer 记录没有薪酬明细，"
                    "不会计入任何薪酬统计。可以在岗位详情里手动补充。"
                ),
                metric="legacy_offer_events",
                numerator=result.legacy_offer_events,
            )
        )

    return observations


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def compute_offer_analytics(
    db: Session,
    filters: AnalyticsFilters | None = None,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> OfferAnalyticsResult:
    """The whole offer analytics payload. No AI, no network, no FX."""
    cfg = settings or get_settings()
    active = filters or AnalyticsFilters()
    moment = now or datetime.now(timezone.utc)

    rows = build_offer_records(db, active, now=moment)
    resumes = {r.id: r for r in db.scalars(select(Resume))}
    with_offer = [r for r in rows if r.has_offer]
    accepted_rows = [r for r in rows if r.accepted]

    declines: Counter[str] = Counter()
    for row in with_offer:
        if row.offer.decline_reason is not None:
            declines[row.offer.decline_reason.value] += 1

    attributed = sum(1 for r in rows if r.record.resume_attributed)

    result = OfferAnalyticsResult(
        window=active.window,
        generated_at=moment,
        timezone=cfg.report_timezone,
        min_sample=cfg.analytics_min_sample,
        recommend_sample=cfg.analytics_recommend_sample,
        filters=active.as_dict(),
        funnel=build_funnel(rows, cfg=cfg),
        compensation=_compensation_by_currency(with_offer),
        accepted_compensation=_compensation_by_currency(accepted_rows),
        negotiation=_negotiation_by_currency(with_offer),
        by_resume=_group(
            rows,
            lambda r: str(r.record.resume_id) if r.record.resume_id else None,
            label_of=lambda k: resumes[int(k)].display_name
            if int(k) in resumes
            else f"#{k}",
            now=moment,
            cfg=cfg,
            resumes=resumes,
        ),
        by_city=_group(
            rows, lambda r: r.record.city, label_of=lambda k: k, now=moment, cfg=cfg
        ),
        by_role_family=_group(
            rows, lambda r: r.record.role, label_of=lambda k: k, now=moment, cfg=cfg
        ),
        by_source=_group(
            rows,
            lambda r: r.record.source,
            label_of=lambda k: SOURCE_LABEL.get(k, k),
            now=moment,
            cfg=cfg,
        ),
        decline_reasons=[
            CountItem(
                key=key,
                label=DECLINE_REASON_LABEL.get(DeclineReason(key), key),
                count=count,
            )
            for key, count in declines.most_common()
        ],
        resume_attribution_coverage=_coverage(attributed, len(rows)),
        legacy_offer_events=count_legacy_offer_events(db),
    )
    result.observations = build_observations(result, cfg=cfg)
    result.notes = _notes(result, cfg=cfg)
    return result


def count_legacy_offer_events(db: Session) -> int:
    from app.services.offer_management import legacy_offer_events

    return len(legacy_offer_events(db))


def _notes(result: OfferAnalyticsResult, *, cfg: Settings) -> list[str]:
    notes: list[str] = []
    funnel = result.funnel

    if funnel.applications == 0:
        notes.append("当前时间窗内还没有投递记录。")
    elif funnel.offers == 0:
        notes.append("当前时间窗内还没有记录任何 Offer。")
    elif funnel.offers < cfg.analytics_min_sample:
        notes.append(
            f"Offer 样本只有 {funnel.offers} 个（低于 {cfg.analytics_min_sample}），"
            "薪酬中位数仅供参考。"
        )

    if len(result.compensation) > 1:
        currencies = "、".join(entry.currency for entry in result.compensation)
        notes.append(
            f"涉及多种币种（{currencies}），各自单独统计，不做跨币种比较 —— "
            "系统不会自行使用任何汇率。"
        )
    return notes
