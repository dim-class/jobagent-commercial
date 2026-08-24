"""Resume variant analytics (v0.7).

Answers "which resume actually performed better?" - and refuses to answer it
when the data cannot.

**Zero OpenAI calls**, like all of v0.6. Every number is a count over rows the
human created. The statistics are not re-invented here: maturity windows,
Wilson intervals, confidence tiers and conservative ranking all come from
``application_analytics`` and ``statistics``, so a resume cohort and a city
cohort are judged by exactly the same yardstick.

Two things this module keeps deliberately apart:

``by_resume`` - real outcome
    what actually happened after applications that *used* that variant. The
    attribution comes from the application cycle, i.e. from the ``applied``
    event, never from whichever resume is active now.

``fit_comparison`` - AI fit
    how the model scored each variant against the same JDs. That is a judgement
    about wording and match, not evidence about recruiters. A variant can read
    better to a model and still get fewer replies.

Nothing here is causal. Different variants get used on different jobs, so a
difference in reply rate is confounded with the jobs themselves - see
``observational_warning``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import Resume
from app.schemas.analytics import (
    CountItem,
    CoverageStat,
    ObservationKind,
    ResumeAnalyticsResult,
    ResumeBreakdownRow,
    ResumeCohortStat,
    ResumeFitCell,
    ResumeFitComparison,
    ResumeFitRow,
    ResumeFitSummary,
    StrategyObservation,
    SummaryStat,
    TimeWindow,
)
from app.services.application_analytics import (
    AnalyticsFilters,
    AppliedRecord,
    _cohort,
    _coverage,
    build_latency,
    build_records,
    load_jobs_for_analytics,
)
from app.services.statistics import Confidence

#: A resume needs at least this many applications inside a slice before that
#: slice is worth a row. Like-for-like tables explode otherwise.
MIN_BREAKDOWN_SAMPLE = 3
#: Only the busiest slices get a like-for-like table.
MAX_BREAKDOWN_SLICES = 4
#: Below this, attribution is too patchy to rank variants against each other.
MIN_ATTRIBUTION_COVERAGE = 0.6

UNKNOWN_KEY = "unknown"
UNKNOWN_LABEL = "未记录简历"


def _rank(cohorts: list[ResumeCohortStat]) -> list[ResumeCohortStat]:
    """Same rule as v0.6: confidence tier first, then the Wilson lower bound.

    Without the tier, a lucky ``1/1`` would outrank a solid ``8/18`` - and the
    row would be labelled 样本不足 while sitting at the top of the table.
    """
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


def _resume_cohort(
    resume: Resume | None,
    records: list[AppliedRecord],
    *,
    now: datetime,
    cfg: Settings,
    active_resume_id: int | None,
) -> ResumeCohortStat:
    key = str(resume.id) if resume else UNKNOWN_KEY
    label = resume.display_name if resume else UNKNOWN_LABEL
    base = _cohort(key, label, records, now=now, cfg=cfg)
    return ResumeCohortStat(
        **base.model_dump(),
        resume_id=resume.id if resume else None,
        variant_group=resume.variant_group if resume else None,
        archived=bool(resume and resume.archived),
        is_active_analysis_resume=bool(resume and resume.id == active_resume_id),
    )


def _group_by_resume(
    records: list[AppliedRecord],
    resumes: dict[int, Resume],
    *,
    now: datetime,
    cfg: Settings,
    active_resume_id: int | None,
) -> tuple[list[ResumeCohortStat], ResumeCohortStat | None]:
    """Split applications by the variant they used.

    Unattributed applications are returned separately, never folded into a
    named variant and never dropped: they still count in overall totals, and
    hiding them would make the variant denominators look better than they are.
    """
    buckets: dict[int, list[AppliedRecord]] = defaultdict(list)
    unknown: list[AppliedRecord] = []

    for record in records:
        resume_id = record.resume_id
        if resume_id is None or resume_id not in resumes:
            unknown.append(record)
        else:
            buckets[resume_id].append(record)

    cohorts = [
        _resume_cohort(
            resumes[resume_id], items, now=now, cfg=cfg, active_resume_id=active_resume_id
        )
        for resume_id, items in buckets.items()
    ]
    unattributed = (
        _resume_cohort(None, unknown, now=now, cfg=cfg, active_resume_id=active_resume_id)
        if unknown
        else None
    )
    return _rank(cohorts), unattributed


def _breakdown(
    records: list[AppliedRecord],
    resumes: dict[int, Resume],
    *,
    dimension: str,
    key_of,
    label_of,
    now: datetime,
    cfg: Settings,
    active_resume_id: int | None,
) -> list[ResumeBreakdownRow]:
    """Like-for-like: compare variants *within* the same kind of job.

    "DevOps版 on DevOps roles vs Cloud版 on DevOps roles" is a far more useful
    question than comparing two variants used on entirely different job
    populations. Slices with too little data are dropped rather than shown thin.
    """
    slices: dict[str, list[AppliedRecord]] = defaultdict(list)
    for record in records:
        key = key_of(record)
        if key is not None and record.resume_id is not None:
            slices[key].append(record)

    ranked_slices = sorted(slices.items(), key=lambda kv: len(kv[1]), reverse=True)
    rows: list[ResumeBreakdownRow] = []

    for key, items in ranked_slices[:MAX_BREAKDOWN_SLICES]:
        by_resume: dict[int, list[AppliedRecord]] = defaultdict(list)
        for record in items:
            if record.resume_id in resumes:
                by_resume[record.resume_id].append(record)

        cohorts = [
            _resume_cohort(
                resumes[rid], group, now=now, cfg=cfg, active_resume_id=active_resume_id
            )
            for rid, group in by_resume.items()
            if len(group) >= MIN_BREAKDOWN_SAMPLE
        ]
        # A "comparison" of one variant compares nothing.
        if len(cohorts) < 2:
            continue
        rows.append(
            ResumeBreakdownRow(
                dimension=dimension,
                dimension_key=key,
                dimension_label=label_of(key),
                resumes=_rank(cohorts),
            )
        )
    return rows


# --------------------------------------------------------------------------
# AI fit (cached analyses only - never an API call)
# --------------------------------------------------------------------------


def build_fit_comparison(
    db: Session, resumes: dict[int, Resume], *, limit: int = 40
) -> ResumeFitComparison:
    """Score matrix over analyses that already exist.

    Empty cells stay empty. Filling them would mean calling the model, and this
    function is reached by merely *opening a page* - so it never does.
    """
    jobs = load_jobs_for_analytics(db)

    per_job: dict[int, dict[int, tuple[int, str]]] = {}
    for job in jobs:
        latest: dict[int, tuple[int, str]] = {}
        # Newest analysis wins per resume, matching queue semantics.
        for analysis in sorted(
            job.analyses, key=lambda a: (a.created_at, a.id), reverse=True
        ):
            if analysis.resume_id in resumes and analysis.resume_id not in latest:
                latest[analysis.resume_id] = (analysis.overall_score, analysis.verdict.value)
        if latest:
            per_job[job.id] = latest

    comparable = {jid: cells for jid, cells in per_job.items() if len(cells) >= 2}

    totals: dict[int, list[int]] = defaultdict(list)
    best_counts: dict[int, int] = defaultdict(int)
    for cells in comparable.values():
        for resume_id, (score, _verdict) in cells.items():
            totals[resume_id].append(score)
        top = max(cells.values(), key=lambda v: v[0])[0]
        winners = [rid for rid, (score, _v) in cells.items() if score == top]
        # A tie credits every variant that tied - no arbitrary winner.
        for rid in winners:
            best_counts[rid] += 1

    summaries = [
        ResumeFitSummary(
            resume_id=resume_id,
            label=resumes[resume_id].display_name,
            average_score=round(sum(scores) / len(scores), 1) if scores else None,
            jobs_scored=len(scores),
            best_on_jobs=best_counts.get(resume_id, 0),
        )
        for resume_id, scores in totals.items()
    ]
    summaries.sort(key=lambda s: (s.average_score or 0.0, s.jobs_scored), reverse=True)

    columns = [
        CountItem(
            key=str(resume_id),
            label=resumes[resume_id].display_name,
            count=len(totals.get(resume_id, [])),
        )
        for resume_id in sorted(totals, key=lambda r: -len(totals[r]))
    ]

    job_by_id = {job.id: job for job in jobs}
    matrix: list[ResumeFitRow] = []
    for job_id in sorted(comparable, key=lambda j: -len(comparable[j]))[:limit]:
        job = job_by_id[job_id]
        matrix.append(
            ResumeFitRow(
                job_id=job.id,
                company=job.company,
                title=job.title,
                city=job.city,
                cells=[
                    ResumeFitCell(
                        resume_id=int(column.key),
                        score=comparable[job_id].get(int(column.key), (None, None))[0],
                        verdict=comparable[job_id].get(int(column.key), (None, None))[1],
                    )
                    for column in columns
                ],
            )
        )

    return ResumeFitComparison(
        comparable_jobs=len(comparable), resumes=summaries, matrix=matrix, columns=columns
    )


# --------------------------------------------------------------------------
# observations (templates, never an LLM)
# --------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


def build_resume_observations(
    result: ResumeAnalyticsResult, *, cfg: Settings
) -> list[StrategyObservation]:
    """Template sentences with their evidence attached. No model involved."""
    observations: list[StrategyObservation] = []

    coverage = result.attribution_coverage
    if coverage.total and (coverage.ratio or 0) < 1.0:
        missing = coverage.total - coverage.covered
        observations.append(
            StrategyObservation(
                kind=ObservationKind.insufficient_data,
                dimension="resume_attribution",
                target="未记录简历",
                text=(
                    f"目前有 {missing}/{coverage.total} 次投递没有记录实际使用的简历"
                    f"（{_pct(1 - (coverage.ratio or 0))}），简历比较的可信度有限。"
                ),
                metric="resume_attribution_coverage",
                numerator=coverage.covered,
                denominator=coverage.total,
            )
        )

    for cohort in result.by_resume:
        stat = cohort.mature_reply_rate
        if stat.denominator == 0:
            continue
        if stat.confidence is Confidence.insufficient:
            observations.append(
                StrategyObservation(
                    kind=ObservationKind.insufficient_data,
                    dimension="resume",
                    target=cohort.label,
                    text=(
                        f"「{cohort.label}」目前只有 {stat.denominator} 个成熟样本，"
                        "暂不足以下结论。"
                    ),
                    metric="mature_reply_rate",
                    numerator=stat.numerator,
                    denominator=stat.denominator,
                    confidence=stat.confidence,
                )
            )
            continue
        observations.append(
            StrategyObservation(
                kind=ObservationKind.descriptive,
                dimension="resume",
                target=cohort.label,
                text=(
                    f"「{cohort.label}」已有 {stat.denominator} 个成熟投递，"
                    f"其中 {stat.numerator} 个收到 HR 回复（{_pct(stat.rate)}），"
                    f"面试 {cohort.interviews} 次。"
                ),
                metric="mature_reply_rate",
                numerator=stat.numerator,
                denominator=stat.denominator,
                confidence=stat.confidence,
            )
        )

    # Like-for-like: the only comparison worth naming out loud.
    for row in result.by_resume_role + result.by_resume_city:
        usable = [
            c
            for c in row.resumes
            if c.mature_reply_rate.confidence is not Confidence.insufficient
        ]
        if len(usable) < 2:
            continue
        best, second = usable[0], usable[1]
        if best.mature_reply_rate.rate == second.mature_reply_rate.rate:
            continue
        observations.append(
            StrategyObservation(
                kind=ObservationKind.descriptive,
                dimension=row.dimension,
                target=f"{row.dimension_label} · {best.label}",
                text=(
                    f"在「{row.dimension_label}」这一类岗位中，"
                    f"「{best.label}」的成熟回复率为 {_pct(best.mature_reply_rate.rate)}"
                    f"（{best.mature_reply_rate.numerator}/{best.mature_reply_rate.denominator}），"
                    f"高于「{second.label}」的 {_pct(second.mature_reply_rate.rate)}"
                    f"（{second.mature_reply_rate.numerator}/{second.mature_reply_rate.denominator}）。"
                ),
                metric="mature_reply_rate",
                numerator=best.mature_reply_rate.numerator,
                denominator=best.mature_reply_rate.denominator,
                comparison_rate=second.mature_reply_rate.rate,
                confidence=best.mature_reply_rate.confidence,
            )
        )

    fit = result.fit_comparison
    if fit.comparable_jobs >= 2 and len(fit.resumes) >= 2:
        top = fit.resumes[0]
        observations.append(
            StrategyObservation(
                kind=ObservationKind.descriptive,
                dimension="resume_fit",
                target=top.label,
                text=(
                    f"在同时用多份简历分析过的 {fit.comparable_jobs} 个岗位中，"
                    f"「{top.label}」的平均匹配分最高（{top.average_score}）。"
                    "这只反映 AI 对简历与 JD 的匹配判断，不代表 HR 的真实反馈。"
                ),
                metric="average_fit_score",
                numerator=top.best_on_jobs,
                denominator=fit.comparable_jobs,
            )
        )

    return observations


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def compute_resume_analytics(
    db: Session,
    filters: AnalyticsFilters | None = None,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> ResumeAnalyticsResult:
    """The whole 简历表现 payload. Deterministic, no AI, no network."""
    cfg = settings or get_settings()
    active = filters or AnalyticsFilters()
    moment = now or datetime.now(timezone.utc)

    resumes = {r.id: r for r in db.scalars(select(Resume))}
    active_resume = db.scalar(select(Resume).where(Resume.is_active.is_(True)).limit(1))
    active_resume_id = active_resume.id if active_resume else None

    records = build_records(db, active, now=moment)
    overall = _cohort("overall", "全部", records, now=moment, cfg=cfg)

    by_resume, unattributed = _group_by_resume(
        records, resumes, now=moment, cfg=cfg, active_resume_id=active_resume_id
    )

    attributed = sum(1 for r in records if r.resume_attributed)
    result = ResumeAnalyticsResult(
        window=active.window,
        generated_at=moment,
        timezone=cfg.report_timezone,
        response_maturity_days=cfg.response_maturity_days,
        interview_maturity_days=cfg.interview_maturity_days,
        min_sample=cfg.analytics_min_sample,
        recommend_sample=cfg.analytics_recommend_sample,
        filters=active.as_dict(),
        summary=SummaryStat(
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
        ),
        by_resume=by_resume,
        unattributed=unattributed,
        attribution_coverage=_coverage(attributed, len(records)),
        by_resume_role=_breakdown(
            records,
            resumes,
            dimension="role_family",
            key_of=lambda r: r.role,
            label_of=lambda k: k,
            now=moment,
            cfg=cfg,
            active_resume_id=active_resume_id,
        ),
        by_resume_city=_breakdown(
            records,
            resumes,
            dimension="city",
            key_of=lambda r: r.city,
            label_of=lambda k: k,
            now=moment,
            cfg=cfg,
            active_resume_id=active_resume_id,
        ),
        fit_comparison=build_fit_comparison(db, resumes),
    )
    result.observations = build_resume_observations(result, cfg=cfg)
    return result


def rankable(result: ResumeAnalyticsResult) -> bool:
    """Whether variants may be presented as better/worse at all.

    Two independent gates: enough of the applications must be attributed, and
    at least two variants must clear the sample threshold. Failing either, the
    page reports numbers but names no winner.
    """
    coverage = result.attribution_coverage.ratio
    if coverage is None or coverage < MIN_ATTRIBUTION_COVERAGE:
        return False
    usable = [
        c
        for c in result.by_resume
        if c.mature_reply_rate.confidence is not Confidence.insufficient
    ]
    return len(usable) >= 2


def resume_performance_map(
    db: Session, *, now: datetime | None = None, settings: Settings | None = None
) -> dict[int, ResumeCohortStat]:
    """All-time per-variant stats, keyed by resume id - for the 简历 page cards."""
    result = compute_resume_analytics(
        db, AnalyticsFilters(window=TimeWindow.all_time), now=now, settings=settings
    )
    return {c.resume_id: c for c in result.by_resume if c.resume_id is not None}


def analyzed_job_counts(db: Session) -> dict[int, int]:
    """How many jobs each variant has a stored analysis for. No API calls."""
    counts: dict[int, int] = defaultdict(int)
    for job in load_jobs_for_analytics(db):
        seen: set[int] = set()
        for analysis in job.analyses:
            if analysis.resume_id not in seen:
                seen.add(analysis.resume_id)
                counts[analysis.resume_id] += 1
    return dict(counts)
