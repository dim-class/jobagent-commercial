"""Interview pipeline analytics (v0.8).

**Zero OpenAI calls**, like v0.6 and v0.7. Every number is a count over rows the
human entered. The statistics are not re-invented: maturity windows, Wilson
intervals, confidence tiers and conservative ranking all come from
``application_analytics`` and ``statistics``, so an interview cohort is judged
by exactly the same yardstick as a city or a resume cohort.

Three rules this module holds:

**Stages come from the pipeline, not from ``Job.status``.** A job sitting at
status ``interview`` says a human clicked something; the rounds say what
actually happened, and only the rounds can answer "did this reach technical?".

**A withdrawal is not a rejection.** ``candidate_withdrawn`` and employer
rejection are counted separately everywhere. Merging them would turn the user's
own decisions into a story about failing.

**Attribution follows the application cycle.** Which resume gets credit for an
interview comes from that cycle's ``applied`` event (v0.7), never from whichever
resume is active today.

Nothing here ever reads or returns ``meeting_url``, interviewer names, or
feedback bodies. Only user-chosen tags and structured reasons are aggregated.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.models import (
    ROUND_TYPE_LABEL,
    EventType,
    InterviewOutcome,
    InterviewProcess,
    InterviewProcessStatus,
    InterviewRound,
    InterviewRoundStatus,
    InterviewRoundType,
    Job,
    Resume,
)
from app.models.enums import TECHNICAL_ROUND_TYPES
from app.schemas.analytics import (
    CohortStat,
    CountItem,
    DropOffStat,
    InterviewAnalyticsResult,
    InterviewCohortStat,
    InterviewFunnel,
    InterviewLatency,
    ObservationKind,
    RoundConversionStat,
    StageStat,
    StrategyObservation,
    TimeWindow,
)
from app.services.application_analytics import (
    SOURCE_LABEL,
    AnalyticsFilters,
    AppliedRecord,
    _cohort,
    _coverage,
    build_latency,
    build_rate,
    build_records,
)
from app.services.statistics import Confidence, rate

#: Stage keys, in funnel order.
STAGE_ORDER: tuple[tuple[str, str], ...] = (
    ("applied", "投递"),
    ("any_interview", "进入面试"),
    ("technical", "技术面"),
    ("final", "终面"),
    ("offer", "Offer"),
)

FAILURE_REASON_LABEL: dict[str, str] = {
    "technical_depth": "技术深度不足",
    "experience_years": "经验年限",
    "language": "语言",
    "role_fit": "岗位匹配",
    "salary": "薪资",
    "visa": "签证",
    "culture_fit": "文化匹配",
    "position_cancelled": "职位取消",
    "unknown": "未知",
    "other": "其他",
}

WITHDRAW_REASON_LABEL: dict[str, str] = {
    "accepted_other_offer": "接受其他Offer",
    "salary": "薪资不合适",
    "company": "公司不合适",
    "location": "地点",
    "role_content": "岗位内容",
    "personal": "个人原因",
    "other": "其他",
}

FEEDBACK_TAG_LABEL: dict[str, str] = {
    "technical_depth": "技术深度",
    "communication": "沟通表达",
    "language": "语言",
    "experience": "工作经验",
    "cloud": "云平台",
    "coding": "编程",
    "system_design": "系统设计",
    "culture": "文化匹配",
    "salary": "薪资",
    "other": "其他",
}


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# the unit of analysis
# --------------------------------------------------------------------------


class ProcessRecord:
    """One application, plus the interview process on that exact cycle."""

    __slots__ = ("record", "process", "rounds")

    def __init__(self, record: AppliedRecord, process: InterviewProcess | None):
        self.record = record
        self.process = process
        self.rounds: list[InterviewRound] = (
            sorted(
                (r for r in process.rounds if r.status is not InterviewRoundStatus.cancelled),
                key=lambda r: (r.round_index, r.id),
            )
            if process
            else []
        )

    # -- stage reach ----------------------------------------------------

    @property
    def has_process(self) -> bool:
        return self.process is not None

    @property
    def reached_any_interview(self) -> bool:
        """A round exists at all. Planned counts - it was offered."""
        return bool(self.rounds)

    @property
    def reached_technical(self) -> bool:
        return any(r.round_type in TECHNICAL_ROUND_TYPES for r in self.rounds)

    @property
    def reached_final(self) -> bool:
        return any(r.round_type is InterviewRoundType.final for r in self.rounds)

    @property
    def offered(self) -> bool:
        """Offer per the *workflow* trail, which owns that milestone."""
        return self.record.cycle.offered or (
            self.process is not None
            and self.process.status is InterviewProcessStatus.offer
        )

    @property
    def withdrawn(self) -> bool:
        return (
            self.process is not None
            and self.process.status is InterviewProcessStatus.withdrawn
        )

    @property
    def rejected(self) -> bool:
        """Employer rejection - never a withdrawal."""
        if self.withdrawn:
            return False
        return self.record.cycle.rejected or (
            self.process is not None
            and self.process.status is InterviewProcessStatus.rejected
        )

    @property
    def ongoing(self) -> bool:
        return self.process is not None and not self.process.is_closed

    @property
    def completed_rounds(self) -> list[InterviewRound]:
        return [r for r in self.rounds if r.status is InterviewRoundStatus.completed]

    def ended_after(self) -> InterviewRoundType | None:
        if self.process is None:
            return None
        if self.process.ended_after_round_type is not None:
            return self.process.ended_after_round_type
        completed = self.completed_rounds
        return completed[-1].round_type if completed else None

    # -- latency --------------------------------------------------------

    def hours_to_first_interview(self) -> float | None:
        times = [_as_utc(r.scheduled_at) for r in self.rounds if r.scheduled_at]
        if not times:
            return None
        return max(0.0, (min(times) - self.record.cycle.applied_at).total_seconds() / 3600.0)

    def hours_first_to_second(self) -> float | None:
        times = sorted(_as_utc(r.scheduled_at) for r in self.rounds if r.scheduled_at)
        if len(times) < 2:
            return None
        return max(0.0, (times[1] - times[0]).total_seconds() / 3600.0)

    def hours_last_round_to_decision(self) -> float | None:
        """Last completed round -> the process closing, either way."""
        if self.process is None or self.process.closed_at is None:
            return None
        completed = [_as_utc(r.completed_at) for r in self.completed_rounds if r.completed_at]
        if not completed:
            return None
        return max(
            0.0, (_as_utc(self.process.closed_at) - max(completed)).total_seconds() / 3600.0
        )


def load_processes_by_cycle(db: Session) -> dict[int, InterviewProcess]:
    """applied_event_id -> process. One bulk query, never one per job."""
    return {
        p.applied_event_id: p
        for p in db.scalars(
            select(InterviewProcess).options(selectinload(InterviewProcess.rounds))
        ).unique()
    }


def build_process_records(
    db: Session, filters: AnalyticsFilters, *, now: datetime | None = None
) -> list[ProcessRecord]:
    """Every application in the window, paired with its own cycle's process."""
    moment = now or datetime.now(timezone.utc)
    records = build_records(db, filters, now=moment)
    processes = load_processes_by_cycle(db)
    return [
        ProcessRecord(record, processes.get(record.cycle.applied_event_id))
        for record in records
    ]


# --------------------------------------------------------------------------
# funnel
# --------------------------------------------------------------------------


def _stat(successes: int, trials: int, cfg: Settings):
    return build_rate(
        successes,
        trials,
        min_sample=cfg.analytics_min_sample,
        recommend_sample=cfg.analytics_recommend_sample,
    )


def build_funnel(rows: list[ProcessRecord], *, cfg: Settings) -> InterviewFunnel:
    applications = len(rows)
    any_interview = sum(1 for r in rows if r.reached_any_interview)
    technical = sum(1 for r in rows if r.reached_technical)
    final = sum(1 for r in rows if r.reached_final)
    offers = sum(1 for r in rows if r.offered)

    # "Got past the first round" - the denominator is applications that had a
    # first round at all, not every application.
    multi_round = sum(1 for r in rows if len(r.rounds) >= 2)

    stages = [
        StageStat(
            key="applied",
            label="投递",
            reached=applications,
            eligible=applications,
            rate=_stat(applications, applications, cfg),
        ),
        StageStat(
            key="any_interview",
            label="进入面试",
            reached=any_interview,
            eligible=applications,
            rate=_stat(any_interview, applications, cfg),
        ),
        StageStat(
            key="technical",
            label="技术面",
            reached=technical,
            eligible=any_interview,
            rate=_stat(technical, any_interview, cfg),
        ),
        StageStat(
            key="final",
            label="终面",
            reached=final,
            eligible=technical,
            rate=_stat(final, technical, cfg),
        ),
        StageStat(
            key="offer",
            label="Offer",
            reached=offers,
            eligible=final,
            rate=_stat(offers, final, cfg),
        ),
    ]

    return InterviewFunnel(
        applications=applications,
        processes=sum(1 for r in rows if r.has_process),
        reached_any_interview=any_interview,
        reached_technical=technical,
        reached_final=final,
        offers=offers,
        rejected=sum(1 for r in rows if r.rejected),
        withdrawn=sum(1 for r in rows if r.withdrawn),
        ongoing=sum(1 for r in rows if r.ongoing),
        application_to_interview=_stat(any_interview, applications, cfg),
        first_to_next_round=_stat(multi_round, any_interview, cfg),
        technical_to_final=_stat(final, technical, cfg),
        final_to_offer=_stat(offers, final, cfg),
        stages=stages,
    )


# --------------------------------------------------------------------------
# round conversion + drop-off
# --------------------------------------------------------------------------


def build_round_conversion(
    rows: list[ProcessRecord], *, cfg: Settings
) -> list[RoundConversionStat]:
    """Per round type: entered, passed, failed, still pending.

    ``pass_rate`` deliberately excludes pending rounds from its denominator - a
    result that has not arrived yet is not a failure.
    """
    entered: Counter[str] = Counter()
    passed: Counter[str] = Counter()
    failed: Counter[str] = Counter()
    pending: Counter[str] = Counter()
    cancelled: Counter[str] = Counter()

    for row in rows:
        if row.process is None:
            continue
        for interview_round in row.process.rounds:
            key = interview_round.round_type.value
            if interview_round.status is InterviewRoundStatus.cancelled:
                cancelled[key] += 1
                continue
            entered[key] += 1
            if interview_round.outcome is InterviewOutcome.passed:
                passed[key] += 1
            elif interview_round.outcome is InterviewOutcome.failed:
                failed[key] += 1
            else:
                pending[key] += 1

    out: list[RoundConversionStat] = []
    for round_type in InterviewRoundType:
        key = round_type.value
        if not entered[key] and not cancelled[key]:
            continue
        decided = passed[key] + failed[key]
        out.append(
            RoundConversionStat(
                round_type=key,
                label=ROUND_TYPE_LABEL[round_type],
                entered=entered[key],
                passed=passed[key],
                failed=failed[key],
                pending=pending[key],
                cancelled=cancelled[key],
                pass_rate=_stat(passed[key], decided, cfg),
            )
        )
    return out


def build_drop_off(rows: list[ProcessRecord], *, cfg: Settings) -> list[DropOffStat]:
    """Where candidacies stopped.

    Rejections and withdrawals are reported in separate columns on purpose:
    "I took another offer after the final round" is not "they turned me down
    after the final round", and collapsing them would misread the user's own
    choices as failures.
    """
    rejected: Counter[str] = Counter()
    withdrawn: Counter[str] = Counter()

    closed = 0
    for row in rows:
        if not (row.rejected or row.withdrawn):
            continue
        closed += 1
        ended = row.ended_after()
        key = ended.value if ended else "other"
        if row.withdrawn:
            withdrawn[key] += 1
        else:
            rejected[key] += 1

    out: list[DropOffStat] = []
    for round_type in InterviewRoundType:
        key = round_type.value
        total = rejected[key] + withdrawn[key]
        if not total:
            continue
        out.append(
            DropOffStat(
                round_type=key,
                label=ROUND_TYPE_LABEL[round_type],
                rejected_after=rejected[key],
                withdrawn_after=withdrawn[key],
                share=_stat(total, closed, cfg),
            )
        )
    out.sort(key=lambda d: (d.rejected_after + d.withdrawn_after), reverse=True)
    return out


def build_interview_latency(rows: list[ProcessRecord]) -> InterviewLatency:
    scheduled_to_completed: list[float] = []
    for row in rows:
        for interview_round in row.completed_rounds:
            if interview_round.scheduled_at and interview_round.completed_at:
                delta = _as_utc(interview_round.completed_at) - _as_utc(
                    interview_round.scheduled_at
                )
                scheduled_to_completed.append(max(0.0, delta.total_seconds() / 3600.0))

    return InterviewLatency(
        application_to_first_interview=build_latency(
            r.hours_to_first_interview() for r in rows
        ),
        first_to_second_round=build_latency(r.hours_first_to_second() for r in rows),
        last_round_to_decision=build_latency(
            r.hours_last_round_to_decision() for r in rows
        ),
        scheduled_to_completed=build_latency(scheduled_to_completed),
    )


# --------------------------------------------------------------------------
# dimensions
# --------------------------------------------------------------------------


def _interview_cohort(
    key: str,
    label: str,
    rows: list[ProcessRecord],
    *,
    now: datetime,
    cfg: Settings,
    resume: Resume | None = None,
) -> InterviewCohortStat:
    base: CohortStat = _cohort(key, label, [r.record for r in rows], now=now, cfg=cfg)
    any_interview = sum(1 for r in rows if r.reached_any_interview)
    technical = sum(1 for r in rows if r.reached_technical)
    final = sum(1 for r in rows if r.reached_final)
    offers = sum(1 for r in rows if r.offered)

    return InterviewCohortStat(
        **base.model_dump(),
        processes=sum(1 for r in rows if r.has_process),
        reached_any_interview=any_interview,
        reached_technical=technical,
        reached_final=final,
        interview_offers=offers,
        interview_reach_rate=_stat(any_interview, len(rows), cfg),
        final_reach_rate=_stat(final, len(rows), cfg),
        interview_offer_rate=_stat(offers, len(rows), cfg),
        resume_id=resume.id if resume else None,
        resume_archived=bool(resume and resume.archived),
    )


def _rank(cohorts: list[InterviewCohortStat]) -> list[InterviewCohortStat]:
    """v0.6 rule, unchanged: confidence tier first, then the Wilson lower bound.

    Without the tier a lucky ``1/1`` tops the table while its own row says
    样本不足.
    """
    cohorts.sort(
        key=lambda c: (
            c.interview_reach_rate.confidence is not Confidence.insufficient,
            c.interview_reach_rate.ranking_score,
            c.applications,
        ),
        reverse=True,
    )
    return cohorts


def _group(
    rows: list[ProcessRecord],
    key_of: Callable[[ProcessRecord], str | None],
    *,
    label_of: Callable[[str], str],
    now: datetime,
    cfg: Settings,
    resumes: dict[int, Resume] | None = None,
) -> list[InterviewCohortStat]:
    buckets: dict[str, list[ProcessRecord]] = defaultdict(list)
    for row in rows:
        key = key_of(row)
        if key is not None:
            buckets[key].append(row)

    cohorts = [
        _interview_cohort(
            key,
            label_of(key),
            items,
            now=now,
            cfg=cfg,
            resume=(resumes or {}).get(int(key)) if resumes and key.isdigit() else None,
        )
        for key, items in buckets.items()
    ]
    return _rank(cohorts)


# --------------------------------------------------------------------------
# tags and reasons
# --------------------------------------------------------------------------


def _counts(pairs: Iterable[tuple[str, int]], labels: dict[str, str]) -> list[CountItem]:
    return [
        CountItem(key=key, label=labels.get(key, key), count=count)
        for key, count in sorted(pairs, key=lambda kv: kv[1], reverse=True)
    ]


def build_tag_counts(rows: list[ProcessRecord]) -> tuple[list[CountItem], list[CountItem], list[CountItem]]:
    """Aggregate only structured, user-chosen values.

    Nothing is inferred from ``feedback_text`` - a tag means the human picked
    it, and free text stays out of analytics entirely.
    """
    tags: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    withdrawals: Counter[str] = Counter()

    for row in rows:
        if row.process is None:
            continue
        for interview_round in row.process.rounds:
            for tag in interview_round.feedback_tags or []:
                tags[str(tag)] += 1
            if interview_round.failure_reason is not None:
                failures[interview_round.failure_reason.value] += 1
        if row.process.withdraw_reason is not None:
            withdrawals[row.process.withdraw_reason.value] += 1

    return (
        _counts(tags.items(), FEEDBACK_TAG_LABEL),
        _counts(failures.items(), FAILURE_REASON_LABEL),
        _counts(withdrawals.items(), WITHDRAW_REASON_LABEL),
    )


# --------------------------------------------------------------------------
# observations (templates, never an LLM)
# --------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


def build_observations(
    result: InterviewAnalyticsResult, *, cfg: Settings
) -> list[StrategyObservation]:
    observations: list[StrategyObservation] = []
    funnel = result.funnel

    if funnel.applications == 0:
        return [
            StrategyObservation(
                kind=ObservationKind.insufficient_data,
                dimension="interview",
                target="全部",
                text="当前时间窗内还没有投递记录，暂时无法分析面试流程。",
                metric="applications",
            )
        ]

    stat = funnel.application_to_interview
    if stat.denominator:
        observations.append(
            StrategyObservation(
                kind=ObservationKind.descriptive,
                dimension="interview",
                target="进入面试",
                text=(
                    f"{stat.denominator} 次投递中有 {stat.numerator} 次进入了面试"
                    f"（{_pct(stat.rate)}）。"
                ),
                metric="application_to_interview",
                numerator=stat.numerator,
                denominator=stat.denominator,
                confidence=stat.confidence,
            )
        )

    for row in result.round_conversion:
        if row.entered < cfg.analytics_min_sample:
            continue
        observations.append(
            StrategyObservation(
                kind=ObservationKind.descriptive,
                dimension="round_type",
                target=row.label,
                text=(
                    f"{row.label}共 {row.entered} 次，"
                    f"通过 {row.passed} 次、未通过 {row.failed} 次"
                    + (f"、{row.pending} 次结果待定。" if row.pending else "。")
                ),
                metric="round_pass_rate",
                numerator=row.passed,
                denominator=row.passed + row.failed,
                confidence=row.pass_rate.confidence,
            )
        )

    # Drop-off: named only when there is enough of it to be worth naming.
    for row in result.drop_off[:3]:
        total = row.rejected_after + row.withdrawn_after
        if row.rejected_after < cfg.analytics_min_sample:
            observations.append(
                StrategyObservation(
                    kind=ObservationKind.insufficient_data,
                    dimension="drop_off",
                    target=row.label,
                    text=(
                        f"「{row.label}」之后结束的流程只有 {total} 个"
                        f"（其中主动终止 {row.withdrawn_after} 个），样本不足，暂不做结论。"
                    ),
                    metric="drop_off",
                    numerator=row.rejected_after,
                    denominator=row.share.denominator,
                )
            )
            continue
        observations.append(
            StrategyObservation(
                kind=ObservationKind.descriptive,
                dimension="drop_off",
                target=row.label,
                text=(
                    f"最常在「{row.label}」之后结束：被拒 {row.rejected_after} 次"
                    f"，主动终止 {row.withdrawn_after} 次。"
                ),
                metric="drop_off",
                numerator=row.rejected_after,
                denominator=row.share.denominator,
                confidence=row.share.confidence,
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
                    "按简历比较面试表现的可信度有限。"
                ),
                metric="resume_attribution_coverage",
                numerator=coverage.covered,
                denominator=coverage.total,
            )
        )

    if result.legacy_interview_events:
        observations.append(
            StrategyObservation(
                kind=ObservationKind.insufficient_data,
                dimension="legacy",
                target="历史面试记录",
                text=(
                    f"有 {result.legacy_interview_events} 条旧版面试记录没有轮次详情，"
                    "不会被归入任何面试阶段。可以在岗位详情里手动补充。"
                ),
                metric="legacy_interview_events",
                numerator=result.legacy_interview_events,
            )
        )

    return observations


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def compute_interview_analytics(
    db: Session,
    filters: AnalyticsFilters | None = None,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> InterviewAnalyticsResult:
    """The whole interview analytics payload. No AI, no network."""
    cfg = settings or get_settings()
    active = filters or AnalyticsFilters()
    moment = now or datetime.now(timezone.utc)

    rows = build_process_records(db, active, now=moment)
    resumes = {r.id: r for r in db.scalars(select(Resume))}

    tags, failures, withdrawals = build_tag_counts(rows)
    attributed = sum(1 for r in rows if r.record.resume_attributed)

    result = InterviewAnalyticsResult(
        window=active.window,
        generated_at=moment,
        timezone=cfg.report_timezone,
        response_maturity_days=cfg.response_maturity_days,
        interview_maturity_days=cfg.interview_maturity_days,
        min_sample=cfg.analytics_min_sample,
        recommend_sample=cfg.analytics_recommend_sample,
        filters=active.as_dict(),
        funnel=build_funnel(rows, cfg=cfg),
        round_conversion=build_round_conversion(rows, cfg=cfg),
        drop_off=build_drop_off(rows, cfg=cfg),
        latency=build_interview_latency(rows),
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
        feedback_tags=tags,
        failure_reasons=failures,
        withdraw_reasons=withdrawals,
        resume_attribution_coverage=_coverage(attributed, len(rows)),
        legacy_interview_events=count_legacy_interview_events(db),
    )
    result.observations = build_observations(result, cfg=cfg)
    result.notes = _notes(result, cfg=cfg)
    return result


def count_legacy_interview_events(db: Session) -> int:
    """Pre-v0.8 interview events with no process behind them."""
    from app.services.interview_pipeline import legacy_interview_events

    return len(legacy_interview_events(db))


def _notes(result: InterviewAnalyticsResult, *, cfg: Settings) -> list[str]:
    notes: list[str] = []
    if result.funnel.applications == 0:
        notes.append("当前时间窗内还没有投递记录。")
    elif result.funnel.reached_any_interview == 0:
        notes.append("当前时间窗内还没有记录任何面试轮次。")
    elif result.funnel.reached_any_interview < cfg.analytics_min_sample:
        notes.append(
            f"进入面试的样本只有 {result.funnel.reached_any_interview} 个"
            f"（低于 {cfg.analytics_min_sample}），阶段转化率仅供参考。"
        )
    if result.funnel.withdrawn:
        notes.append(
            f"其中 {result.funnel.withdrawn} 个流程是你主动终止的，"
            "不计入「被拒」统计。"
        )
    return notes
