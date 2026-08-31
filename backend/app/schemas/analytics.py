"""Career analytics schemas (v0.6).

Every rate carries its numerator, denominator, confidence interval and sample
confidence. A bare percentage is never returned - "100%" and "1 / 1" must
always travel together.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.statistics import Confidence


class TimeWindow(str, Enum):
    d7 = "7d"
    d30 = "30d"
    d90 = "90d"
    all_time = "all"


WINDOW_DAYS: dict[TimeWindow, int | None] = {
    TimeWindow.d7: 7,
    TimeWindow.d30: 30,
    TimeWindow.d90: 90,
    TimeWindow.all_time: None,
}


class RateStat(BaseModel):
    """A conversion rate with everything needed to judge how much to trust it."""

    rate: float | None = Field(default=None, description="numerator/denominator，无样本时为 null")
    numerator: int = 0
    denominator: int = 0
    ci_low: float | None = Field(default=None, description="Wilson 95% 区间下界")
    ci_high: float | None = None
    ranking_score: float = Field(
        default=0.0, description="Wilson 下界，用于排序（小样本会自动排后）"
    )
    confidence: Confidence = Confidence.insufficient


class LatencyStat(BaseModel):
    """Median-first, because one three-week reply should not move the typical."""

    sample: int = 0
    median_hours: float | None = None
    p25_hours: float | None = None
    p75_hours: float | None = None


class CohortStat(BaseModel):
    """One row of any breakdown table."""

    key: str
    label: str
    jobs: int = 0
    applications: int = 0
    mature_applications: int = 0
    mature_interview_applications: int = 0
    replies: int = 0
    interviews: int = 0
    offers: int = 0
    rejections: int = 0
    mature_no_response: int = 0

    raw_reply_rate: RateStat = Field(default_factory=RateStat)
    mature_reply_rate: RateStat = Field(default_factory=RateStat)
    interview_rate: RateStat = Field(default_factory=RateStat)
    offer_rate: RateStat = Field(default_factory=RateStat)
    reply_latency: LatencyStat = Field(default_factory=LatencyStat)


class ScoreBandStat(CohortStat):
    """Score bands also report how many analyzed jobs never got applied to."""

    analyzed_jobs: int = 0


class SummaryStat(BaseModel):
    applications: int = 0
    mature_applications: int = 0
    replies: int = 0
    interviews: int = 0
    offers: int = 0
    rejections: int = 0
    mature_no_response: int = 0

    raw_reply_rate: RateStat = Field(default_factory=RateStat)
    mature_reply_rate: RateStat = Field(default_factory=RateStat)
    interview_rate: RateStat = Field(default_factory=RateStat)
    offer_rate: RateStat = Field(default_factory=RateStat)
    reply_latency: LatencyStat = Field(default_factory=LatencyStat)
    interview_latency: LatencyStat = Field(default_factory=LatencyStat)


class CountItem(BaseModel):
    key: str
    label: str
    count: int


class CoverageStat(BaseModel):
    """How much of the data supports a given analysis."""

    covered: int = 0
    total: int = 0
    ratio: float | None = None


class DataQuality(BaseModel):
    applications: int = 0
    mature_applications: int = 0
    salary_coverage: CoverageStat = Field(default_factory=CoverageStat)
    conversation_coverage: CoverageStat = Field(default_factory=CoverageStat)
    recruiter_analysis_coverage: CoverageStat = Field(default_factory=CoverageStat)
    city_coverage: CoverageStat = Field(default_factory=CoverageStat)
    role_family_coverage: CoverageStat = Field(default_factory=CoverageStat)
    notes: list[str] = Field(default_factory=list)


class SkillOutcome(BaseModel):
    skill: str
    replied: int = 0
    interviewed: int = 0
    offered: int = 0
    applied: int = 0


class SkillOutcomes(BaseModel):
    matched_in_interviews: list[SkillOutcome] = Field(default_factory=list)
    matched_in_replies: list[SkillOutcome] = Field(default_factory=list)
    missing_in_high_score_jobs: list[CountItem] = Field(default_factory=list)


class RecruiterInsights(BaseModel):
    requests: list[CountItem] = Field(default_factory=list)
    sentiment: list[CountItem] = Field(default_factory=list)
    stage: list[CountItem] = Field(default_factory=list)
    analysis_coverage: CoverageStat = Field(default_factory=CoverageStat)


class ObservationKind(str, Enum):
    outperforming = "outperforming"
    underperforming = "underperforming"
    insufficient_data = "insufficient_data"
    descriptive = "descriptive"


class StrategyObservation(BaseModel):
    """A template-generated sentence with the evidence attached.

    No LLM is used to write these - they must be reproducible and checkable.
    """

    kind: ObservationKind
    dimension: str
    target: str
    text: str
    metric: str = ""
    numerator: int = 0
    denominator: int = 0
    comparison_rate: float | None = None
    confidence: Confidence = Confidence.insufficient


class ProposalType(str, Enum):
    increase_city_priority = "increase_city_priority"
    decrease_city_priority = "decrease_city_priority"
    increase_role_priority = "increase_role_priority"
    decrease_role_priority = "decrease_role_priority"
    prioritize_source = "prioritize_source"
    deprioritize_source = "deprioritize_source"
    consider_score_floor_change = "consider_score_floor_change"
    skill_learning_candidate = "skill_learning_candidate"
    collect_more_data = "collect_more_data"


class ProposalEvidence(BaseModel):
    metric: str
    numerator: int
    denominator: int
    rate: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    comparison_rate: float | None = None
    window: TimeWindow = TimeWindow.d30


class StrategyAdjustmentProposal(BaseModel):
    """A suggestion. Applying it always requires an explicit human action."""

    model_config = ConfigDict(use_enum_values=False)

    signature: str = Field(description="语义签名；证据明显变化时会产生新的签名")
    type: ProposalType
    target: str
    current_value: Any | None = None
    suggested_value: Any | None = None
    reason: str
    evidence: ProposalEvidence
    sample_size: int
    confidence: Confidence
    impact_description: str
    #: Only true for proposals that can be turned into a concrete YAML edit.
    applicable: bool = False
    decision: str | None = Field(default=None, description="accepted / dismissed / null")


class StrategyDiff(BaseModel):
    """Preview of exactly what applying a proposal would change."""

    signature: str
    field: str
    before: Any
    after: Any
    description: str


class KeywordCohortOut(BaseModel):
    """One search keyword's cohort. Every rate travels with its counts."""

    keyword: str
    cities: list[str] = Field(default_factory=list)
    jobs: int
    recommended: int
    average_score: float | None = None
    recommend_rate: float | None = None
    interval_low: float | None = None
    interval_high: float | None = None
    confidence: str
    actionable: bool


class SearchKeywordAnalyticsResult(BaseModel):
    """Which search directions surfaced well-matched postings. Zero AI calls."""

    cohorts: list[KeywordCohortOut] = Field(default_factory=list)
    analyzed_jobs: int = 0
    attributed_jobs: int = 0
    unattributed_jobs: int = 0
    coverage: float | None = None
    observations: list[str] = Field(default_factory=list)


class CareerAnalyticsResult(BaseModel):
    window: TimeWindow
    generated_at: datetime
    timezone: str = "Asia/Tokyo"
    response_maturity_days: int = 7
    interview_maturity_days: int = 14
    min_sample: int = 5
    recommend_sample: int = 8
    filters: dict[str, Any] = Field(default_factory=dict)

    summary: SummaryStat = Field(default_factory=SummaryStat)
    by_city: list[CohortStat] = Field(default_factory=list)
    by_role_family: list[CohortStat] = Field(default_factory=list)
    by_source: list[CohortStat] = Field(default_factory=list)
    by_score_band: list[ScoreBandStat] = Field(default_factory=list)
    by_verdict: list[CohortStat] = Field(default_factory=list)
    by_salary_band: list[CohortStat] = Field(default_factory=list)
    by_city_role: list[CohortStat] = Field(default_factory=list)

    latency_by_source: list[CohortStat] = Field(default_factory=list)
    recruiter: RecruiterInsights = Field(default_factory=RecruiterInsights)
    skills: SkillOutcomes = Field(default_factory=SkillOutcomes)
    data_quality: DataQuality = Field(default_factory=DataQuality)
    observations: list[StrategyObservation] = Field(default_factory=list)

    salary_parse_coverage: CoverageStat = Field(default_factory=CoverageStat)


class RecommendationsResponse(BaseModel):
    window: TimeWindow
    proposals: list[StrategyAdjustmentProposal] = Field(default_factory=list)
    suppressed: int = Field(default=0, description="因样本不足或已忽略而未展示的数量")
    message: str = ""


class ApplyProposalRequest(BaseModel):
    confirmed: bool = Field(default=False, description="必须为 true —— 人工确认后才会写入")
    note: str | None = Field(default=None, max_length=500)


class ApplyProposalResponse(BaseModel):
    applied: bool
    signature: str
    diff: list[StrategyDiff] = Field(default_factory=list)
    strategy: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class DismissProposalResponse(BaseModel):
    signature: str
    decision: str
    message: str = ""


class DashboardAnalytics(BaseModel):
    """The compact 求职策略表现 card. Silent unless the data supports it."""

    window: TimeWindow = TimeWindow.d30
    has_signal: bool = False
    best_direction: str | None = None
    mature_reply_rate: RateStat | None = None
    interview_rate: RateStat | None = None
    applications: int = 0
    message: str = "数据积累中"

# --------------------------------------------------------------------------
# resume variants (v0.7)
# --------------------------------------------------------------------------


class ResumeCohortStat(CohortStat):
    """One resume variant's real-world conversion.

    ``key`` is the resume id as a string; ``label`` is its current display name.
    The ``unknown`` bucket uses key ``"unknown"`` and is never presented as a
    variant that can be chosen - it is a measurement gap, not a resume.
    """

    resume_id: int | None = None
    variant_group: str | None = None
    archived: bool = False
    is_active_analysis_resume: bool = False


class ResumeBreakdownRow(BaseModel):
    """A resume within one slice, e.g. "DevOps版 within DevOps roles".

    Like-for-like comparison: comparing a variant used on SRE jobs against one
    used on helpdesk jobs mostly measures the jobs, not the resume.
    """

    dimension: str
    dimension_key: str
    dimension_label: str
    resumes: list[ResumeCohortStat] = Field(default_factory=list)


class ResumeFitCell(BaseModel):
    """One cached (job, resume) analysis score. Never computed on demand."""

    resume_id: int
    score: int | None = None
    verdict: str | None = None


class ResumeFitRow(BaseModel):
    job_id: int
    company: str
    title: str
    city: str | None = None
    cells: list[ResumeFitCell] = Field(default_factory=list)


class ResumeFitSummary(BaseModel):
    """Average AI score per variant over jobs analysed with several variants.

    This measures **model-judged fit**, not recruiter behaviour. It is kept
    apart from conversion on purpose: a variant can read better to an LLM and
    still get fewer replies.
    """

    resume_id: int
    label: str
    average_score: float | None = None
    jobs_scored: int = 0
    #: Jobs where this variant scored highest among those compared.
    best_on_jobs: int = 0


class ResumeFitComparison(BaseModel):
    #: Jobs analysed against at least two variants - the only fair comparison.
    comparable_jobs: int = 0
    resumes: list[ResumeFitSummary] = Field(default_factory=list)
    matrix: list[ResumeFitRow] = Field(default_factory=list)
    columns: list[CountItem] = Field(default_factory=list)


class ResumeAnalyticsResult(BaseModel):
    """The 简历表现 payload. Deterministic; zero OpenAI calls."""

    window: TimeWindow
    generated_at: datetime
    timezone: str = "Asia/Tokyo"
    response_maturity_days: int = 7
    interview_maturity_days: int = 14
    min_sample: int = 5
    recommend_sample: int = 8
    filters: dict[str, Any] = Field(default_factory=dict)

    summary: SummaryStat = Field(default_factory=SummaryStat)
    by_resume: list[ResumeCohortStat] = Field(default_factory=list)
    #: The applications whose resume was never recorded. Reported, never merged
    #: into a named variant.
    unattributed: ResumeCohortStat | None = None
    attribution_coverage: CoverageStat = Field(default_factory=CoverageStat)
    by_resume_role: list[ResumeBreakdownRow] = Field(default_factory=list)
    by_resume_city: list[ResumeBreakdownRow] = Field(default_factory=list)
    fit_comparison: ResumeFitComparison = Field(default_factory=ResumeFitComparison)
    observations: list[StrategyObservation] = Field(default_factory=list)
    #: Always populated. The UI must render it next to any comparison.
    observational_warning: str = (
        "不同简历可能被用于不同类型的岗位，因此这里是观察性数据，"
        "并非严格随机的 A/B 实验。"
    )


class UnattributedApplication(BaseModel):
    """One application awaiting a human to say which resume it used."""

    job_id: int
    applied_event_id: int | None = None
    company: str
    title: str
    city: str | None = None
    applied_at: datetime


class UnattributedResponse(BaseModel):
    items: list[UnattributedApplication] = Field(default_factory=list)
    total: int = 0
    message: str = ""


class ResumePerformance(BaseModel):
    """Per-variant card data for the 简历 page."""

    resume_id: int
    label: str
    variant_group: str | None = None
    archived: bool = False
    is_active_analysis_resume: bool = False
    stat: ResumeCohortStat | None = None
    analyzed_jobs: int = 0



# --------------------------------------------------------------------------
# interview pipeline (v0.8)
# --------------------------------------------------------------------------


class StageStat(BaseModel):
    """One stage of the interview funnel, with its own denominator."""

    key: str
    label: str
    reached: int = 0
    #: Denominator for `rate` - the cohort that could have reached this stage.
    eligible: int = 0
    rate: RateStat = Field(default_factory=RateStat)


class RoundConversionStat(BaseModel):
    """Per round type: who entered, and what happened next."""

    round_type: str
    label: str
    entered: int = 0
    passed: int = 0
    failed: int = 0
    pending: int = 0
    cancelled: int = 0
    #: passed / (passed + failed) - pending rounds are not failures.
    pass_rate: RateStat = Field(default_factory=RateStat)


class DropOffStat(BaseModel):
    """Where candidacies stopped. Withdrawals are counted separately."""

    round_type: str
    label: str
    rejected_after: int = 0
    withdrawn_after: int = 0
    #: Of all closed processes in the window.
    share: RateStat = Field(default_factory=RateStat)


class InterviewLatency(BaseModel):
    application_to_first_interview: LatencyStat = Field(default_factory=LatencyStat)
    first_to_second_round: LatencyStat = Field(default_factory=LatencyStat)
    last_round_to_decision: LatencyStat = Field(default_factory=LatencyStat)
    scheduled_to_completed: LatencyStat = Field(default_factory=LatencyStat)


class InterviewCohortStat(CohortStat):
    """A city / role / source / resume cohort, with interview reach added."""

    processes: int = 0
    reached_any_interview: int = 0
    reached_technical: int = 0
    reached_final: int = 0
    interview_offers: int = 0
    interview_reach_rate: RateStat = Field(default_factory=RateStat)
    final_reach_rate: RateStat = Field(default_factory=RateStat)
    interview_offer_rate: RateStat = Field(default_factory=RateStat)
    #: Only populated on resume cohorts.
    resume_id: int | None = None
    resume_archived: bool = False


class InterviewFunnel(BaseModel):
    applications: int = 0
    processes: int = 0
    reached_any_interview: int = 0
    reached_technical: int = 0
    reached_final: int = 0
    offers: int = 0
    rejected: int = 0
    withdrawn: int = 0
    ongoing: int = 0

    application_to_interview: RateStat = Field(default_factory=RateStat)
    first_to_next_round: RateStat = Field(default_factory=RateStat)
    technical_to_final: RateStat = Field(default_factory=RateStat)
    final_to_offer: RateStat = Field(default_factory=RateStat)
    stages: list[StageStat] = Field(default_factory=list)


class InterviewAnalyticsResult(BaseModel):
    """Deterministic interview analytics. Zero OpenAI calls.

    Never carries a meeting URL, interviewer name or feedback body - analytics
    payloads are the most likely thing to be shared, and a meeting link often
    embeds an access token.
    """

    window: TimeWindow
    generated_at: datetime
    timezone: str = "Asia/Tokyo"
    response_maturity_days: int = 7
    interview_maturity_days: int = 14
    min_sample: int = 5
    recommend_sample: int = 8
    filters: dict[str, Any] = Field(default_factory=dict)

    funnel: InterviewFunnel = Field(default_factory=InterviewFunnel)
    round_conversion: list[RoundConversionStat] = Field(default_factory=list)
    drop_off: list[DropOffStat] = Field(default_factory=list)
    latency: InterviewLatency = Field(default_factory=InterviewLatency)

    by_resume: list[InterviewCohortStat] = Field(default_factory=list)
    by_city: list[InterviewCohortStat] = Field(default_factory=list)
    by_role_family: list[InterviewCohortStat] = Field(default_factory=list)
    by_source: list[InterviewCohortStat] = Field(default_factory=list)

    feedback_tags: list[CountItem] = Field(default_factory=list)
    failure_reasons: list[CountItem] = Field(default_factory=list)
    withdraw_reasons: list[CountItem] = Field(default_factory=list)

    #: Applications whose resume was never recorded (v0.7 semantics).
    resume_attribution_coverage: CoverageStat = Field(default_factory=CoverageStat)
    #: Pre-v0.8 interview events with no process - never auto-classified.
    legacy_interview_events: int = 0
    observations: list[StrategyObservation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# offers (v0.9)
# --------------------------------------------------------------------------


class MoneyStat(BaseModel):
    """A compensation distribution within ONE currency.

    Currencies are never mixed: v0.9 fetches no FX rates, so 400K CNY and
    8M JPY are reported as two rows, never ranked against each other.
    """

    currency: str
    sample: int = 0
    median: float | None = None
    p25: float | None = None
    p75: float | None = None
    minimum: float | None = None
    maximum: float | None = None


class CurrencyCompensation(BaseModel):
    """Everything monetary for one currency."""

    currency: str
    offers: int = 0
    base_annual: MoneyStat | None = None
    first_year_guaranteed: MoneyStat | None = None
    first_year_target: MoneyStat | None = None
    estimated_total_comp: MoneyStat | None = None


class NegotiationUpliftStat(BaseModel):
    """Uplift across offers that actually recorded a negotiation sequence."""

    currency: str
    sample: int = 0
    median_base_uplift: float | None = None
    median_base_uplift_pct: float | None = None
    median_guaranteed_uplift: float | None = None
    offers_with_signing_gained: int = 0
    #: Offers with a counter AND a later company revision. Without both, a
    #: change is not evidence that negotiating caused it.
    with_full_sequence: int = 0


class OfferCohortStat(CohortStat):
    """A city / role / source / resume cohort, with offer reach added.

    ``offers_recorded`` counts structured Offer rows. The inherited ``offers``
    counts cycles whose *event trail* reached an offer, which also includes
    pre-v0.9 milestones that carry no compensation detail - the two are
    deliberately not the same number.
    """

    offers_recorded: int = 0
    accepted: int = 0
    declined: int = 0
    #: Structured offers over applications. Named to parallel v0.8's
    #: ``interview_reach_rate``; the inherited ``offer_rate`` is the v0.6
    #: event-trail measure over mature interview applications.
    offer_reach_rate: RateStat = Field(default_factory=RateStat)
    interview_to_offer_rate: RateStat = Field(default_factory=RateStat)
    resume_id: int | None = None
    resume_archived: bool = False


class OfferFunnel(BaseModel):
    applications: int = 0
    reached_any_interview: int = 0
    offers: int = 0
    accepted: int = 0
    declined: int = 0
    withdrawn: int = 0
    expired: int = 0
    pending: int = 0
    application_to_offer: RateStat = Field(default_factory=RateStat)
    interview_to_offer: RateStat = Field(default_factory=RateStat)
    offer_acceptance_rate: RateStat = Field(default_factory=RateStat)


class OfferAnalyticsResult(BaseModel):
    """Deterministic offer analytics. Zero OpenAI calls.

    Compensation appears here because the page needs it; it never appears in a
    log line. Accepted-offer figures come from ``accepted_revision_id``, so a
    later revision cannot rewrite a past decision.
    """

    window: TimeWindow
    generated_at: datetime
    timezone: str = "Asia/Tokyo"
    min_sample: int = 5
    recommend_sample: int = 8
    filters: dict[str, Any] = Field(default_factory=dict)

    funnel: OfferFunnel = Field(default_factory=OfferFunnel)
    #: One entry per currency present. Never merged.
    compensation: list[CurrencyCompensation] = Field(default_factory=list)
    accepted_compensation: list[CurrencyCompensation] = Field(default_factory=list)
    negotiation: list[NegotiationUpliftStat] = Field(default_factory=list)

    by_resume: list[OfferCohortStat] = Field(default_factory=list)
    by_city: list[OfferCohortStat] = Field(default_factory=list)
    by_role_family: list[OfferCohortStat] = Field(default_factory=list)
    by_source: list[OfferCohortStat] = Field(default_factory=list)

    decline_reasons: list[CountItem] = Field(default_factory=list)
    resume_attribution_coverage: CoverageStat = Field(default_factory=CoverageStat)
    #: Pre-v0.9 ``offer`` events with no structured Offer row.
    legacy_offer_events: int = 0
    observations: list[StrategyObservation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
