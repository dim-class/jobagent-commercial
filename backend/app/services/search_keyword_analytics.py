"""Which search directions are worth running again - from scores you already paid for.

**Zero OpenAI calls.** Every number is a count or an average over rows that
already exist, so opening this page is free and reproducible. A test asserts it.

What it measures, and the distinction matters:

    这些数字说的是「这个关键词带回来的岗位，模型觉得怎么样」,
    不是「这个关键词能帮你拿到面试」.

An AI recommend-rate is about the *fit of what the search surfaced*. Real
outcomes live in `application_analytics` and need actual applications. A
keyword can surface well-matched postings that never reply.

One count here is not the model's alone: `useful` lets a human decision
override the verdict - an application the model did not recommend counts, a
recommendation the user skipped does not. The direction ranking weighs that
count, because what the user did with a search's results is what a search
exists to produce. It is still not a reply rate.

Attribution is the existing `TaskCandidate` association - the same one the
console review uses. Jobs imported outside a SearchPlan task (manual paste,
Quick Capture) simply have no keyword and are reported as unattributed rather
than blamed on whichever search happened to run that day.

Confidence reuses `statistics.py` wholesale: same Wilson interval, same
sample-size bands, same "tier first, then lower bound" ranking as v0.6. There
is no second definition of confidence here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import Job, JobAnalysis, JobSearchTask, TaskCandidate, Verdict
from app.models.enums import DECIDED_STATUSES, RECOMMENDED_VERDICTS, JobStatus
from app.services.statistics import (
    Confidence,
    Interval,
    classify_confidence,
    rate,
    wilson_interval,
    wilson_lower_bound,
)

#: Ranking order for the confidence bands - strongest evidence first. Matches
#: `application_analytics`: a perfect 2/2 must never outrank a solid 5/12.
_TIER_RANK: dict[Confidence, int] = {
    Confidence.strong: 0,
    Confidence.moderate: 1,
    Confidence.low: 2,
    Confidence.insufficient: 3,
}

#: A human decision settles whether a surfaced job was worth it, whatever the
#: model said: applied or anything after it, or saved, is a yes; skipped is a
#: no. Every other status is undecided, and the model's verdict stands in.
_HUMAN_NO: frozenset[JobStatus] = frozenset({JobStatus.skipped})
_HUMAN_YES: frozenset[JobStatus] = (DECIDED_STATUSES - _HUMAN_NO) | {JobStatus.saved}


@dataclass(slots=True)
class KeywordCohort:
    """One search keyword and what it surfaced. Never a recommendation by itself."""

    keyword: str
    cities: list[str] = field(default_factory=list)
    #: Distinct analyzed jobs this keyword's tasks brought in.
    jobs: int = 0
    recommended: int = 0
    #: Of `jobs`, how many turned out worth applying to: the user applied to or
    #: saved them, or the model recommended them and the user has not decided
    #: yet. A human decision always overrides the verdict, and an undecided
    #: recommendation still counts, so a freshly searched keyword is not
    #: penalised for a queue nobody has read. The direction ranking weighs this.
    useful: int = 0
    average_score: float | None = None
    recommend_rate: float | None = None
    interval: Interval | None = None
    confidence: Confidence = Confidence.insufficient

    @property
    def actionable(self) -> bool:
        """Whether this cohort carries enough evidence to act on at all."""
        return self.confidence is not Confidence.insufficient


@dataclass(slots=True)
class KeywordAnalytics:
    cohorts: list[KeywordCohort] = field(default_factory=list)
    analyzed_jobs: int = 0
    attributed_jobs: int = 0
    unattributed_jobs: int = 0
    observations: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float | None:
        if not self.analyzed_jobs:
            return None
        return self.attributed_jobs / self.analyzed_jobs


def _latest_analysis_by_job(db: Session) -> dict[int, JobAnalysis]:
    """One analysis per job - the latest, matching every other surface."""
    latest: dict[int, JobAnalysis] = {}
    for analysis in db.scalars(select(JobAnalysis)):
        current = latest.get(analysis.job_id)
        if current is None or (analysis.created_at, analysis.id) > (
            current.created_at,
            current.id,
        ):
            latest[analysis.job_id] = analysis
    return latest


def _useful(status: JobStatus | None, verdict: Verdict | None) -> bool:
    """The human's decision where one exists, the model's recommendation where not."""
    if status in _HUMAN_YES:
        return True
    if status in _HUMAN_NO:
        return False
    return verdict in RECOMMENDED_VERDICTS


def compute(db: Session, *, settings: Settings | None = None) -> KeywordAnalytics:
    """Aggregate analysis scores by the keyword whose task surfaced the job.

    Pure read. No model call, no network, no write.
    """
    cfg = settings or get_settings()
    latest = _latest_analysis_by_job(db)
    statuses: dict[int, JobStatus] = {
        job_id: status for job_id, status in db.execute(select(Job.id, Job.status))
    }

    # keyword -> {job_id: analysis}. A job found by two tasks with the same
    # keyword counts once; a job found by two *different* keywords counts for
    # each, because each search genuinely surfaced it.
    by_keyword: dict[str, dict[int, JobAnalysis]] = {}
    cities: dict[str, set[str]] = {}
    attributed: set[int] = set()

    rows = db.execute(
        select(JobSearchTask.keywords, JobSearchTask.city, TaskCandidate.job_id)
        .join(TaskCandidate, TaskCandidate.task_id == JobSearchTask.id)
    ).all()
    for keyword, city, job_id in rows:
        analysis = latest.get(job_id)
        if analysis is None:
            continue  # never analyzed: it has no score to average
        key = (keyword or "").strip()
        if not key:
            continue
        by_keyword.setdefault(key, {})[job_id] = analysis
        if city:
            cities.setdefault(key, set()).add(city)
        attributed.add(job_id)

    cohorts: list[KeywordCohort] = []
    for keyword, jobs in by_keyword.items():
        analyses = list(jobs.values())
        n = len(analyses)
        recommended = sum(1 for a in analyses if a.verdict in RECOMMENDED_VERDICTS)
        cohorts.append(
            KeywordCohort(
                keyword=keyword,
                cities=sorted(cities.get(keyword, set())),
                jobs=n,
                recommended=recommended,
                useful=sum(
                    1 for job_id, a in jobs.items() if _useful(statuses.get(job_id), a.verdict)
                ),
                average_score=round(sum(a.overall_score for a in analyses) / n, 1),
                recommend_rate=rate(recommended, n),
                interval=wilson_interval(recommended, n),
                confidence=classify_confidence(
                    n,
                    min_sample=cfg.analytics_min_sample,
                    recommend_sample=cfg.analytics_recommend_sample,
                ),
            )
        )

    # Tier first, then the Wilson lower bound. Sorting by the bound alone would
    # let a lucky 2/2 outrank a solid 5/12 - see application_analytics.
    cohorts.sort(
        key=lambda c: (
            _TIER_RANK[c.confidence],
            -wilson_lower_bound(c.recommended, c.jobs),
            -(c.average_score or 0),
        )
    )

    result = KeywordAnalytics(
        cohorts=cohorts,
        analyzed_jobs=len(latest),
        attributed_jobs=len(attributed),
        unattributed_jobs=len(latest) - len(attributed),
    )
    result.observations = build_observations(result)
    return result


def build_observations(result: KeywordAnalytics) -> list[str]:
    """Template sentences, never model-written, so they can be re-derived.

    They describe what the scores say about what a search surfaced. They never
    claim a keyword *causes* interviews, and they never let a thin cohort pass
    as a finding.
    """
    lines: list[str] = []
    actionable = [c for c in result.cohorts if c.actionable]
    if not actionable:
        lines.append("样本还不足以比较搜索方向；继续采集后再看。")
        return lines

    best = actionable[0]
    if best.recommend_rate is not None:
        lines.append(
            f"在已采集样本中，「{best.keyword}」的推荐率最高"
            f"（{best.recommended}/{best.jobs}，{best.recommend_rate:.0%}），"
            f"平均分 {best.average_score}。"
        )

    # A direction that surfaced nothing worth applying to is the actionable
    # finding here: it is where the analysis budget went with no return.
    for cohort in actionable:
        if cohort.recommended == 0:
            lines.append(
                f"「{cohort.keyword}」带回 {cohort.jobs} 个岗位，没有一个被判定为推荐投递"
                f"（平均分 {cohort.average_score}）。可以考虑不再搜这个方向。"
            )

    thin = [c for c in result.cohorts if not c.actionable]
    if thin:
        names = "、".join(f"「{c.keyword}」" for c in thin[:5])
        lines.append(
            f"{names} 等 {len(thin)} 个方向样本不足（各自少于 "
            f"{max(c.jobs for c in thin) + 1} 个岗位），暂不做结论。"
        )

    if result.unattributed_jobs:
        lines.append(
            f"另有 {result.unattributed_jobs} 个已分析岗位不是通过搜索任务采集的"
            f"（手动粘贴或快速采集），不计入任何关键词。"
        )
    lines.append("以上是模型对搜索结果的评分，不代表这些岗位更容易收到回复。")
    return lines
