"""Career outcome analytics (v0.6).

Deterministic end to end - **zero OpenAI calls**. Every number here is a count
or a ratio over rows the human created, so the page is cheap, reproducible and
testable.

Denominator semantics, stated once and applied everywhere:

``applications``
    Jobs with an *effective application cycle* (see ``application_cycles``)
    whose ``applied`` event falls inside the selected window. A cycle undone by
    ``status_reset`` is not an application.

``mature_applications``
    Applications that already got a reply, **or** are at least
    ``RESPONSE_MATURITY_DAYS`` old. This is the denominator for reply rates:
    something applied to this morning has not failed, it has not had time.

``interview_rate``
    Interviews over applications mature by ``INTERVIEW_MATURITY_DAYS``.

Comparison baseline is always the overall cohort *in the same window and under
the same filters* - never every job ever collected.

Nothing in this module infers causality. Cohorts that convert well are reported
as converting well; why they do is not something conversion counts can answer.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.career_strategy import load_strategy
from app.core.config import Settings, get_settings
from app.models import (
    Job,
    RecruiterConversation,
    RecruiterMessage,
    Resume,
    ResumeUsage,
    Verdict,
)
from app.schemas.analytics import (
    CareerAnalyticsResult,
    CohortStat,
    CountItem,
    CoverageStat,
    DataQuality,
    LatencyStat,
    RateStat,
    RecruiterInsights,
    ScoreBandStat,
    SkillOutcome,
    SkillOutcomes,
    SummaryStat,
    TimeWindow,
    WINDOW_DAYS,
)
from app.services.application_cycles import (
    ApplicationCycle,
    effective_cycle,
    in_window,
    window_start,
)
from app.services.application_metrics import role_family, source_bucket
from app.services.application_queue import latest_analysis_of
from app.services.scoring import extract_salary_range
from app.services.statistics import (
    Confidence,
    classify_confidence,
    percentiles,
    rate,
    wilson_interval,
    wilson_lower_bound,
)

#: Score bands, high to low.
SCORE_BANDS: tuple[tuple[str, int, int], ...] = (
    ("90-100", 90, 101),
    ("80-89", 80, 90),
    ("70-79", 70, 80),
    ("60-69", 60, 70),
    ("<60", 0, 60),
)

#: Monthly CNY salary bands. Only jobs whose salary text actually parses land
#: here - 面议 / DOE never becomes a number.
SALARY_BANDS: tuple[tuple[str, int, int], ...] = (
    ("<15K", 0, 15_000),
    ("15-20K", 15_000, 20_000),
    ("20-30K", 20_000, 30_000),
    ("30-40K", 30_000, 40_000),
    ("40K+", 40_000, 10_000_000),
)

VERDICT_LABEL: dict[str, str] = {
    "strong_apply": "强烈推荐",
    "apply": "建议投递",
    "maybe": "可以考虑",
    "skip": "不建议",
}

SOURCE_LABEL: dict[str, str] = {
    "boss": "BOSS直聘",
    "liepin": "猎聘",
    "zhaopin": "智联",
    "job51": "51job",
    "linkedin": "LinkedIn",
    "wechat": "微信",
    "email": "邮件",
    "manual": "手动录入",
    "demo": "演示数据",
    "other": "其他",
}

REQUEST_LABEL: dict[str, str] = {
    "interview_availability": "面试时间",
    "expected_salary": "期望薪资",
    "current_salary": "当前薪资",
    "start_date": "到岗时间",
    "notice_period": "离职周期",
    "resume": "简历",
    "resume_update": "简历更新",
    "work_location": "工作地点",
    "remote_preference": "远程意向",
    "visa_status": "签证状态",
    "visa_expiry": "签证到期",
    "sponsorship": "签证支持",
    "language_skill": "语言能力",
    "technical_experience": "技术经验",
    "years_of_experience": "工作年限",
    "certification": "证书",
    "motivation": "求职动机",
    "reason_for_change": "离职原因",
    "other": "其他",
}

SENTIMENT_LABEL = {"positive": "积极", "neutral": "中性", "negative": "消极", "unclear": "不明确"}

STAGE_LABEL = {
    "initial_contact": "初次联系",
    "screening": "初步筛选",
    "interview_scheduling": "面试安排",
    "document_request": "材料索取",
    "salary_discussion": "薪资沟通",
    "offer_discussion": "Offer 沟通",
    "rejection": "未通过",
    "follow_up": "跟进",
    "other": "其他",
}

#: A city×role or salary cohort is only worth a table row above this.
MIN_INTERSECTION_SAMPLE = 2
TOP_N = 12


# --------------------------------------------------------------------------
# the unit of analysis
# --------------------------------------------------------------------------


@dataclass(slots=True)
class AppliedRecord:
    """One application, plus everything needed to slice it."""

    job: Job
    cycle: ApplicationCycle
    city: str | None
    role: str
    source: str
    score: int | None
    verdict: str | None
    salary_min: int | None
    salary_max: int | None
    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)

    @property
    def resume_id(self) -> int | None:
        """The resume the human submitted, or None when it was never recorded.

        Read straight off the cycle, i.e. off the ``applied`` event. Never the
        active analysis resume, and never the resume the AI happened to score
        this job against - those are different questions.
        """
        return self.cycle.resume_id if self.cycle.resume_usage is ResumeUsage.used else None

    @property
    def resume_attributed(self) -> bool:
        return self.cycle.resume_attributed

    @property
    def salary_band(self) -> str | None:
        value = self.salary_max or self.salary_min
        if value is None:
            return None
        for label, low, high in SALARY_BANDS:
            if low <= value < high:
                return label
        return None

    @property
    def score_band(self) -> str | None:
        if self.score is None:
            return None
        for label, low, high in SCORE_BANDS:
            if low <= self.score < high:
                return label
        return None


@dataclass(slots=True)
class AnalyticsFilters:
    window: TimeWindow = TimeWindow.d30
    city: str | None = None
    role_family: str | None = None
    source: str | None = None
    #: Restrict to applications made with one resume variant (v0.7).
    resume_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "window": self.window.value,
            "city": self.city,
            "role_family": self.role_family,
            "source": self.source,
            "resume_id": self.resume_id,
        }


def load_jobs_for_analytics(db: Session) -> list[Job]:
    """One bulk query with eager loads - never a query per job."""
    return list(
        db.scalars(
            select(Job).options(selectinload(Job.analyses), selectinload(Job.events))
        ).unique()
    )


def build_records(
    db: Session, filters: AnalyticsFilters, *, now: datetime | None = None
) -> list[AppliedRecord]:
    """Every application in the window that passes the filters."""
    moment = now or datetime.now(timezone.utc)
    since = window_start(WINDOW_DAYS[filters.window], now=moment)
    strategy = load_strategy()

    records: list[AppliedRecord] = []
    for job in load_jobs_for_analytics(db):
        cycle = effective_cycle(job)
        if cycle is None or not in_window(cycle, since=since):
            continue

        analysis = latest_analysis_of(job)
        result = (analysis.result_json or {}) if analysis else {}
        low, high = extract_salary_range(job.salary_text)

        record = AppliedRecord(
            job=job,
            cycle=cycle,
            city=job.city,
            role=role_family(job.title, strategy=strategy),
            source=source_bucket(job.source),
            score=analysis.overall_score if analysis else None,
            verdict=analysis.verdict.value if analysis else None,
            salary_min=low,
            salary_max=high,
            matched_skills=[str(s) for s in (result.get("matched_skills") or [])],
            missing_skills=[str(s) for s in (result.get("missing_skills") or [])],
        )

        if filters.city and record.city != filters.city:
            continue
        if filters.role_family and record.role != filters.role_family:
            continue
        if filters.source and record.source != filters.source:
            continue
        if filters.resume_id is not None and record.resume_id != filters.resume_id:
            continue
        records.append(record)

    return records


# --------------------------------------------------------------------------
# stat builders
# --------------------------------------------------------------------------


def build_rate(
    successes: int, trials: int, *, min_sample: int, recommend_sample: int
) -> RateStat:
    interval = wilson_interval(successes, trials)
    return RateStat(
        rate=rate(successes, trials),
        numerator=successes,
        denominator=trials,
        ci_low=interval.low if interval else None,
        ci_high=interval.high if interval else None,
        ranking_score=wilson_lower_bound(successes, trials),
        confidence=classify_confidence(
            trials, min_sample=min_sample, recommend_sample=recommend_sample
        ),
    )


def build_latency(values: Iterable[float]) -> LatencyStat:
    collected = [v for v in values if v is not None]
    p25, median, p75 = percentiles(collected)
    return LatencyStat(
        sample=len(collected), median_hours=median, p25_hours=p25, p75_hours=p75
    )


def _cohort(
    key: str,
    label: str,
    records: list[AppliedRecord],
    *,
    now: datetime,
    cfg: Settings,
) -> CohortStat:
    response_days = cfg.response_maturity_days
    interview_days = cfg.interview_maturity_days

    mature = [
        r
        for r in records
        if r.cycle.is_response_mature(now=now, maturity_days=response_days)
    ]
    mature_interview = [
        r
        for r in records
        if r.cycle.is_interview_mature(now=now, maturity_days=interview_days)
    ]
    replies = sum(1 for r in mature if r.cycle.replied)
    interviews = sum(1 for r in mature_interview if r.cycle.interviewed)
    offers = sum(1 for r in records if r.cycle.offered)

    def build(successes: int, trials: int) -> RateStat:
        return build_rate(
            successes,
            trials,
            min_sample=cfg.analytics_min_sample,
            recommend_sample=cfg.analytics_recommend_sample,
        )

    return CohortStat(
        key=key,
        label=label,
        jobs=len(records),
        applications=len(records),
        mature_applications=len(mature),
        mature_interview_applications=len(mature_interview),
        replies=replies,
        interviews=interviews,
        offers=offers,
        rejections=sum(1 for r in records if r.cycle.rejected),
        mature_no_response=sum(
            1
            for r in records
            if r.cycle.is_mature_no_response(now=now, maturity_days=response_days)
        ),
        raw_reply_rate=build(sum(1 for r in records if r.cycle.replied), len(records)),
        mature_reply_rate=build(replies, len(mature)),
        interview_rate=build(interviews, len(mature_interview)),
        offer_rate=build(offers, len(mature_interview)),
        reply_latency=build_latency(
            r.cycle.hours_to_reply() for r in records if r.cycle.replied
        ),
    )


def _group(
    records: list[AppliedRecord],
    key_of: Callable[[AppliedRecord], str | None],
    *,
    label_of: Callable[[str], str],
    now: datetime,
    cfg: Settings,
    min_sample: int = 1,
) -> list[CohortStat]:
    """Group, build stats, and rank conservatively.

    Two rules, in this order:

    1. a cohort the page already labels 样本不足 sorts *below* every cohort with
       an adequate sample, whatever its rate. The Wilson lower bound alone is
       not enough here: a perfect ``2/2`` has a lower bound of 0.34, which beats
       a solid ``5/12`` at 0.19. Presenting 广州 2/2 as the top direction while
       simultaneously calling it 样本不足 would contradict the page itself;
    2. within each tier, rank by the Wilson lower bound, so ``1/1`` never
       outranks a well-evidenced cohort just for being lucky.

    The raw rate is still reported honestly on every row - this orders the
    table, it does not hide anything.
    """
    buckets: dict[str, list[AppliedRecord]] = defaultdict(list)
    for record in records:
        key = key_of(record)
        if key is not None:
            buckets[key].append(record)

    cohorts = [
        _cohort(key, label_of(key), items, now=now, cfg=cfg)
        for key, items in buckets.items()
        if len(items) >= min_sample
    ]
    cohorts.sort(
        key=lambda c: (
            c.mature_reply_rate.confidence is not Confidence.insufficient,
            c.mature_reply_rate.ranking_score,
            c.mature_applications,
            c.applications,
        ),
        reverse=True,
    )
    return cohorts


# --------------------------------------------------------------------------
# recruiter + skill sections
# --------------------------------------------------------------------------


def _recruiter_insights(db: Session, cfg: Settings) -> RecruiterInsights:
    """Aggregate stored recruiter analyses. Only analyzed messages count."""
    conversations = list(
        db.scalars(
            select(RecruiterConversation).options(
                selectinload(RecruiterConversation.messages).selectinload(
                    RecruiterMessage.analyses
                )
            )
        ).unique()
    )

    requests: Counter[str] = Counter()
    sentiment: Counter[str] = Counter()
    stage: Counter[str] = Counter()
    recruiter_messages = 0
    analyzed = 0

    for conversation in conversations:
        for message in conversation.messages:
            if message.direction.value != "recruiter":
                continue
            recruiter_messages += 1
            if not message.analyses:
                continue
            analyzed += 1
            latest = max(message.analyses, key=lambda a: (a.created_at, a.id))
            payload = latest.result_json or {}
            for item in payload.get("recruiter_requests") or []:
                kind = str(item.get("type") or "other")
                requests[kind] += 1
            if payload.get("sentiment"):
                sentiment[str(payload["sentiment"])] += 1
            if payload.get("conversation_stage"):
                stage[str(payload["conversation_stage"])] += 1

    return RecruiterInsights(
        requests=[
            CountItem(key=k, label=REQUEST_LABEL.get(k, k), count=v)
            for k, v in requests.most_common(TOP_N)
        ],
        sentiment=[
            CountItem(key=k, label=SENTIMENT_LABEL.get(k, k), count=v)
            for k, v in sentiment.most_common()
        ],
        stage=[
            CountItem(key=k, label=STAGE_LABEL.get(k, k), count=v)
            for k, v in stage.most_common()
        ],
        analysis_coverage=_coverage(analyzed, recruiter_messages),
    )


def _coverage(covered: int, total: int) -> CoverageStat:
    return CoverageStat(covered=covered, total=total, ratio=rate(covered, total))


def _skill_outcomes(records: list[AppliedRecord]) -> SkillOutcomes:
    """Descriptive only: which skills appear on jobs that converted.

    This is emphatically *not* evidence that learning a skill causes an
    interview - the same skills appear on the jobs the user targets anyway.
    """
    applied: Counter[str] = Counter()
    replied: Counter[str] = Counter()
    interviewed: Counter[str] = Counter()
    offered: Counter[str] = Counter()
    missing_high_score: Counter[str] = Counter()

    for record in records:
        for skill in set(record.matched_skills):
            applied[skill] += 1
            if record.cycle.replied:
                replied[skill] += 1
            if record.cycle.interviewed:
                interviewed[skill] += 1
            if record.cycle.offered:
                offered[skill] += 1
        if record.score is not None and record.score >= 80:
            for skill in set(record.missing_skills):
                missing_high_score[skill] += 1

    def top(counter: Counter[str]) -> list[SkillOutcome]:
        return [
            SkillOutcome(
                skill=skill,
                applied=applied.get(skill, 0),
                replied=replied.get(skill, 0),
                interviewed=interviewed.get(skill, 0),
                offered=offered.get(skill, 0),
            )
            for skill, _count in counter.most_common(TOP_N)
        ]

    return SkillOutcomes(
        matched_in_interviews=top(interviewed),
        matched_in_replies=top(replied),
        missing_in_high_score_jobs=[
            CountItem(key=skill, label=skill, count=count)
            for skill, count in missing_high_score.most_common(TOP_N)
        ],
    )


def _data_quality(
    db: Session, records: list[AppliedRecord], recruiter: RecruiterInsights, *, now: datetime, cfg: Settings
) -> DataQuality:
    total = len(records)
    mature = sum(
        1
        for r in records
        if r.cycle.is_response_mature(now=now, maturity_days=cfg.response_maturity_days)
    )
    with_salary = sum(1 for r in records if r.salary_band is not None)
    with_city = sum(1 for r in records if r.city)
    with_role = sum(1 for r in records if r.role != "Other")

    linked_job_ids = {
        c.job_id
        for c in db.scalars(select(RecruiterConversation))
        if c.job_id is not None
    }
    with_conversation = sum(1 for r in records if r.job.id in linked_job_ids)

    notes: list[str] = []
    if total == 0:
        notes.append("当前时间窗内还没有投递记录，先去投递队列处理几个岗位吧。")
    elif mature < cfg.analytics_min_sample:
        notes.append(
            f"成熟样本只有 {mature} 个（低于 {cfg.analytics_min_sample}），"
            "结论仅供参考，请继续积累数据。"
        )
    if total and with_salary / total < 0.5:
        notes.append("超过一半的已投岗位没有可解析的薪资，薪资分析覆盖有限。")

    return DataQuality(
        applications=total,
        mature_applications=mature,
        salary_coverage=_coverage(with_salary, total),
        conversation_coverage=_coverage(with_conversation, total),
        recruiter_analysis_coverage=recruiter.analysis_coverage,
        city_coverage=_coverage(with_city, total),
        role_family_coverage=_coverage(with_role, total),
        notes=notes,
    )


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def compute_analytics(
    db: Session,
    filters: AnalyticsFilters | None = None,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> CareerAnalyticsResult:
    """Build the whole analytics payload. No AI, no network."""
    cfg = settings or get_settings()
    active = filters or AnalyticsFilters()
    moment = now or datetime.now(timezone.utc)

    records = build_records(db, active, now=moment)
    overall = _cohort("overall", "全部", records, now=moment, cfg=cfg)

    summary = SummaryStat(
        applications=overall.applications,
        mature_applications=overall.mature_applications,
        replies=overall.replies,
        interviews=overall.interviews,
        offers=overall.offers,
        rejections=overall.rejections,
        mature_no_response=overall.mature_no_response,
        raw_reply_rate=overall.raw_reply_rate,
        mature_reply_rate=overall.mature_reply_rate,
        interview_rate=overall.interview_rate,
        offer_rate=overall.offer_rate,
        reply_latency=overall.reply_latency,
        interview_latency=build_latency(
            r.cycle.hours_to_interview() for r in records if r.cycle.interviewed
        ),
    )

    group = lambda key_of, label_of, min_sample=1: _group(  # noqa: E731
        records, key_of, label_of=label_of, now=moment, cfg=cfg, min_sample=min_sample
    )

    by_score_band = _score_bands(db, records, active, now=moment, cfg=cfg)
    recruiter = _recruiter_insights(db, cfg)

    result = CareerAnalyticsResult(
        window=active.window,
        generated_at=moment,
        timezone=cfg.report_timezone,
        response_maturity_days=cfg.response_maturity_days,
        interview_maturity_days=cfg.interview_maturity_days,
        min_sample=cfg.analytics_min_sample,
        recommend_sample=cfg.analytics_recommend_sample,
        filters=active.as_dict(),
        summary=summary,
        by_city=group(lambda r: r.city, lambda k: k),
        by_role_family=group(lambda r: r.role, lambda k: k),
        by_source=group(lambda r: r.source, lambda k: SOURCE_LABEL.get(k, k)),
        by_score_band=by_score_band,
        by_verdict=group(lambda r: r.verdict, lambda k: VERDICT_LABEL.get(k, k)),
        by_salary_band=group(lambda r: r.salary_band, lambda k: k),
        by_city_role=group(
            lambda r: f"{r.city} · {r.role}" if r.city else None,
            lambda k: k,
            MIN_INTERSECTION_SAMPLE,
        ),
        latency_by_source=group(lambda r: r.source, lambda k: SOURCE_LABEL.get(k, k)),
        recruiter=recruiter,
        skills=_skill_outcomes(records),
        salary_parse_coverage=_coverage(
            sum(1 for r in records if r.salary_band is not None), len(records)
        ),
    )
    result.data_quality = _data_quality(db, records, recruiter, now=moment, cfg=cfg)
    result.observations = build_observations(result, cfg=cfg)
    return result


def _score_bands(
    db: Session,
    records: list[AppliedRecord],
    filters: AnalyticsFilters,
    *,
    now: datetime,
    cfg: Settings,
) -> list[ScoreBandStat]:
    """Score bands also count analyzed-but-never-applied jobs.

    That column is what makes the band table answer "does the score predict
    outcomes?" rather than just "what did I apply to?".
    """
    analyzed_counts: Counter[str] = Counter()
    strategy = load_strategy()
    for job in load_jobs_for_analytics(db):
        analysis = latest_analysis_of(job)
        if analysis is None:
            continue
        if filters.city and job.city != filters.city:
            continue
        if filters.role_family and role_family(job.title, strategy=strategy) != filters.role_family:
            continue
        if filters.source and source_bucket(job.source) != filters.source:
            continue
        for label, low, high in SCORE_BANDS:
            if low <= analysis.overall_score < high:
                analyzed_counts[label] += 1
                break

    buckets: dict[str, list[AppliedRecord]] = defaultdict(list)
    for record in records:
        band = record.score_band
        if band is not None:
            buckets[band].append(record)

    out: list[ScoreBandStat] = []
    for label, _low, _high in SCORE_BANDS:
        items = buckets.get(label, [])
        if not items and not analyzed_counts.get(label):
            continue
        base = _cohort(label, label, items, now=now, cfg=cfg)
        out.append(
            ScoreBandStat(**base.model_dump(), analyzed_jobs=analyzed_counts.get(label, 0))
        )
    return out


# --------------------------------------------------------------------------
# observations (templates, never an LLM)
# --------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


def _cohorts_worth_mentioning(
    cohorts: list[CohortStat], *, baseline: float | None, top: int = 3, extra: int = 3
) -> list[CohortStat]:
    """The top-ranked cohorts, plus any small cohort that *looks* impressive.

    Ranking deliberately pushes 样本不足 cohorts to the bottom of the table, so
    a striking ``2/2 = 100%`` would otherwise fall outside the top rows and get
    no comment at all. That is precisely the row a reader is most likely to
    over-read, so it earns an explicit "样本不足，暂不做结论" instead of silence.
    """
    chosen = list(cohorts[:top])
    seen = {c.key for c in chosen}

    for cohort in cohorts:
        if len(chosen) >= top + extra:
            break
        stat = cohort.mature_reply_rate
        if cohort.key in seen or stat.denominator == 0:
            continue
        if stat.confidence is not Confidence.insufficient:
            continue
        if baseline is not None and (stat.rate or 0) <= baseline:
            continue
        chosen.append(cohort)
        seen.add(cohort.key)

    return chosen


def build_observations(
    result: CareerAnalyticsResult, *, cfg: Settings
) -> list["StrategyObservationT"]:
    """Template sentences with the evidence attached.

    Deliberately not model-written: an observation must be reproducible from
    the same data, and every claim must carry its numerator and denominator.
    """
    from app.schemas.analytics import ObservationKind, StrategyObservation

    baseline = result.summary.mature_reply_rate.rate
    observations: list[StrategyObservation] = []

    if result.summary.applications == 0:
        return [
            StrategyObservation(
                kind=ObservationKind.insufficient_data,
                dimension="overall",
                target="全部",
                text="当前时间窗内还没有投递记录，暂时无法分析。",
                metric="applications",
            )
        ]

    dimensions: tuple[tuple[str, list[CohortStat]], ...] = (
        ("city", result.by_city),
        ("role_family", result.by_role_family),
        ("city_role", result.by_city_role),
        ("source", result.by_source),
    )

    for dimension, cohorts in dimensions:
        for cohort in _cohorts_worth_mentioning(cohorts, baseline=baseline):
            stat = cohort.mature_reply_rate
            if stat.denominator == 0:
                continue

            if stat.confidence is Confidence.insufficient:
                observations.append(
                    StrategyObservation(
                        kind=ObservationKind.insufficient_data,
                        dimension=dimension,
                        target=cohort.label,
                        text=(
                            f"目前关于「{cohort.label}」只有 {stat.denominator} 个成熟样本，"
                            "样本不足，暂不做结论。"
                        ),
                        metric="mature_reply_rate",
                        numerator=stat.numerator,
                        denominator=stat.denominator,
                        comparison_rate=baseline,
                        confidence=stat.confidence,
                    )
                )
                continue

            from app.services.statistics import is_meaningfully_better, is_meaningfully_worse

            if is_meaningfully_better(stat.numerator, stat.denominator, baseline_rate=baseline):
                observations.append(
                    StrategyObservation(
                        kind=ObservationKind.outperforming,
                        dimension=dimension,
                        target=cohort.label,
                        text=(
                            f"「{cohort.label}」的成熟回复率为 {_pct(stat.rate)}"
                            f"（{stat.numerator}/{stat.denominator}），"
                            f"高于整体的 {_pct(baseline)}。"
                        ),
                        metric="mature_reply_rate",
                        numerator=stat.numerator,
                        denominator=stat.denominator,
                        comparison_rate=baseline,
                        confidence=stat.confidence,
                    )
                )
            elif is_meaningfully_worse(stat.numerator, stat.denominator, baseline_rate=baseline):
                observations.append(
                    StrategyObservation(
                        kind=ObservationKind.underperforming,
                        dimension=dimension,
                        target=cohort.label,
                        text=(
                            f"「{cohort.label}」的成熟回复率为 {_pct(stat.rate)}"
                            f"（{stat.numerator}/{stat.denominator}），"
                            f"低于整体的 {_pct(baseline)}。"
                        ),
                        metric="mature_reply_rate",
                        numerator=stat.numerator,
                        denominator=stat.denominator,
                        comparison_rate=baseline,
                        confidence=stat.confidence,
                    )
                )

    for band in result.by_score_band:
        stat = band.mature_reply_rate
        if stat.denominator >= cfg.analytics_min_sample:
            observations.append(
                StrategyObservation(
                    kind=ObservationKind.descriptive,
                    dimension="score_band",
                    target=band.label,
                    text=(
                        f"{band.label} 分岗位共 {stat.denominator} 个成熟投递，"
                        f"HR 回复 {stat.numerator} 次（{_pct(stat.rate)}），"
                        f"面试 {band.interviews} 次。"
                    ),
                    metric="mature_reply_rate",
                    numerator=stat.numerator,
                    denominator=stat.denominator,
                    comparison_rate=baseline,
                    confidence=stat.confidence,
                )
            )

    if result.recruiter.requests:
        top = result.recruiter.requests[0]
        observations.append(
            StrategyObservation(
                kind=ObservationKind.descriptive,
                dimension="recruiter_request",
                target=top.label,
                text=f"招聘方最常问的是「{top.label}」，共出现 {top.count} 次，建议提前准备好答案。",
                metric="recruiter_request_count",
                numerator=top.count,
                denominator=result.recruiter.analysis_coverage.covered,
            )
        )

    return observations


# Local alias so the annotation above stays readable without a circular import.
StrategyObservationT = Any


def analytics_signature(result: CareerAnalyticsResult) -> str:
    """Cheap fingerprint of the evidence, used for caching and signatures."""
    payload = "|".join(
        [
            result.window.value,
            str(result.summary.applications),
            str(result.summary.mature_applications),
            str(result.summary.replies),
            str(result.summary.interviews),
            str(result.summary.offers),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
