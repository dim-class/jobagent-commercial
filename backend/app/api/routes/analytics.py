"""Career analytics endpoints (v0.6).

    GET  /api/analytics/career                  the whole payload
    GET  /api/analytics/career/dashboard        compact card for the dashboard
    GET  /api/analytics/strategy-recommendations
    POST /api/analytics/strategy-recommendations/{signature}/preview
    POST /api/analytics/strategy-recommendations/{signature}/apply
    POST /api/analytics/strategy-recommendations/{signature}/dismiss

Thin routes: every number is computed in ``services/application_analytics.py``
and every proposal in ``services/strategy_recommendations.py``. No route writes
YAML, and nothing here calls OpenAI.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.models import RecommendationDecision
from app.schemas.analytics import (
    KeywordCohortOut,
    SearchKeywordAnalyticsResult,
    ApplyProposalRequest,
    ApplyProposalResponse,
    CareerAnalyticsResult,
    DashboardAnalytics,
    DismissProposalResponse,
    InterviewAnalyticsResult,
    OfferAnalyticsResult,
    RecommendationsResponse,
    ResumeAnalyticsResult,
    TimeWindow,
    UnattributedApplication,
    UnattributedResponse,
)
from app.services import search_keyword_analytics as keyword_analytics
from app.services import application_analytics as analytics
from app.services import (
    interview_analytics,
    offer_analytics,
    resume_analytics,
    resume_variants,
)
from app.services import strategy_recommendations as recommendations
from app.services.application_analytics import AnalyticsFilters
from app.services.statistics import Confidence

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _filters(
    window: TimeWindow,
    city: str | None,
    role_family: str | None,
    source: str | None,
) -> AnalyticsFilters:
    return AnalyticsFilters(window=window, city=city, role_family=role_family, source=source)


@router.get("/career", response_model=CareerAnalyticsResult)
def career_analytics(
    db: Session = Depends(get_db),
    window: TimeWindow = Query(default=TimeWindow.d30),
    city: str | None = Query(default=None),
    role_family: str | None = Query(default=None),
    source: str | None = Query(default=None),
) -> CareerAnalyticsResult:
    """Deterministic outcome analytics. Zero OpenAI calls by design."""
    return analytics.compute_analytics(db, _filters(window, city, role_family, source))


@router.get("/career/dashboard", response_model=DashboardAnalytics)
def dashboard_card(db: Session = Depends(get_db)) -> DashboardAnalytics:
    """The compact 求职策略表现 card.

    Stays silent ("数据积累中") unless a cohort clears the sample threshold -
    a headline direction from three applications would be worse than nothing.
    """
    cfg = get_settings()
    result = analytics.compute_analytics(db, AnalyticsFilters(window=TimeWindow.d30))

    best = None
    for cohort in result.by_city_role or result.by_city:
        if cohort.mature_reply_rate.confidence is not Confidence.insufficient:
            best = cohort
            break

    if best is None:
        return DashboardAnalytics(
            applications=result.summary.applications,
            has_signal=False,
            message=(
                "数据积累中"
                if result.summary.applications
                else "还没有投递记录，先去投递队列处理几个岗位吧。"
            ),
        )

    return DashboardAnalytics(
        has_signal=True,
        best_direction=best.label,
        mature_reply_rate=best.mature_reply_rate,
        interview_rate=best.interview_rate,
        applications=result.summary.applications,
        message=f"近30天成熟样本 {best.mature_applications} 个（{cfg.report_timezone}）",
    )


# --------------------------------------------------------------------------
# recommendations
# --------------------------------------------------------------------------


@router.get("/search-keywords", response_model=SearchKeywordAnalyticsResult)
def search_keyword_analytics(db: Session = Depends(get_db)) -> SearchKeywordAnalyticsResult:
    """Which search directions surfaced well-matched postings.

    Pure read over scores that already exist - no model call, no network, no
    write. Reading this page costs nothing.
    """
    result = keyword_analytics.compute(db)
    return SearchKeywordAnalyticsResult(
        cohorts=[
            KeywordCohortOut(
                keyword=c.keyword,
                cities=c.cities,
                jobs=c.jobs,
                recommended=c.recommended,
                average_score=c.average_score,
                recommend_rate=c.recommend_rate,
                interval_low=c.interval.low if c.interval else None,
                interval_high=c.interval.high if c.interval else None,
                confidence=c.confidence.value,
                actionable=c.actionable,
            )
            for c in result.cohorts
        ],
        analyzed_jobs=result.analyzed_jobs,
        attributed_jobs=result.attributed_jobs,
        unattributed_jobs=result.unattributed_jobs,
        coverage=result.coverage,
        observations=result.observations,
    )


@router.get("/strategy-recommendations", response_model=RecommendationsResponse)
def strategy_recommendations(
    db: Session = Depends(get_db),
    window: TimeWindow = Query(default=TimeWindow.d30),
) -> RecommendationsResponse:
    """Proposals with their evidence. Nothing is applied by reading this."""
    result = analytics.compute_analytics(db, AnalyticsFilters(window=window))
    proposals, hidden = recommendations.visible_proposals(db, result)

    if not proposals:
        message = (
            "当前数据还不足以给出策略建议，继续积累投递与回复记录即可。"
            if result.summary.mature_applications < get_settings().analytics_recommend_sample
            else "暂时没有需要调整的方向。"
        )
    else:
        message = "以下建议基于已观察到的结果，仅供参考，需要你确认后才会生效。"

    return RecommendationsResponse(
        window=window, proposals=proposals, suppressed=hidden, message=message
    )


@router.post(
    "/strategy-recommendations/{signature}/preview", response_model=ApplyProposalResponse
)
def preview_proposal(
    signature: str,
    db: Session = Depends(get_db),
    window: TimeWindow = Query(default=TimeWindow.d30),
) -> ApplyProposalResponse:
    """Show the exact diff without writing anything."""
    from app.core.career_strategy import load_strategy

    result = analytics.compute_analytics(db, AnalyticsFilters(window=window))
    proposal = recommendations.find_proposal(db, result, signature)
    return ApplyProposalResponse(
        applied=False,
        signature=signature,
        diff=recommendations.build_diff(proposal),
        strategy=load_strategy(),
        message="以下是应用后会发生的修改，确认后才会写入。",
    )


@router.post("/strategy-recommendations/{signature}/apply", response_model=ApplyProposalResponse)
def apply_proposal(
    signature: str,
    payload: ApplyProposalRequest | None = Body(default=None),
    db: Session = Depends(get_db),
    window: TimeWindow = Query(default=TimeWindow.d30),
) -> ApplyProposalResponse:
    """Write the change - only with an explicit ``confirmed=true``.

    Goes through the existing career-strategy service and records an audit row.
    """
    from app.core.errors import ValidationError

    request = payload or ApplyProposalRequest()
    if not request.confirmed:
        raise ValidationError(
            "需要明确确认后才会修改求职策略。", detail={"field": "confirmed"}
        )

    result = analytics.compute_analytics(db, AnalyticsFilters(window=window))
    proposal = recommendations.find_proposal(db, result, signature)
    diff = recommendations.build_diff(proposal)
    strategy = recommendations.apply_proposal(db, proposal, note=request.note)

    return ApplyProposalResponse(
        applied=True,
        signature=signature,
        diff=diff,
        strategy=strategy,
        message="求职策略已更新，并记录了一条变更审计。",
    )


@router.post(
    "/strategy-recommendations/{signature}/dismiss", response_model=DismissProposalResponse
)
def dismiss_proposal(
    signature: str,
    payload: ApplyProposalRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> DismissProposalResponse:
    """Hide a proposal. Substantially new evidence can still bring it back."""
    request = payload or ApplyProposalRequest()
    recommendations.record_decision(
        db, signature, RecommendationDecision.dismissed, note=request.note
    )
    return DismissProposalResponse(
        signature=signature, decision="dismissed", message="已忽略该建议"
    )


# --------------------------------------------------------------------------
# resume variants (v0.7)
# --------------------------------------------------------------------------


@router.get("/resumes", response_model=ResumeAnalyticsResult)
def resume_analytics_endpoint(
    db: Session = Depends(get_db),
    window: TimeWindow = Query(default=TimeWindow.d30),
    city: str | None = Query(default=None),
    role_family: str | None = Query(default=None),
    source: str | None = Query(default=None),
) -> ResumeAnalyticsResult:
    """简历表现 - which variant actually converted better.

    Deterministic, zero OpenAI calls, same statistics as every other cohort.
    Outcome attribution comes from each application's own cycle, so switching
    the active analysis resume never moves a past result between variants.
    """
    filters = AnalyticsFilters(
        window=window, city=city, role_family=role_family, source=source
    )
    return resume_analytics.compute_resume_analytics(db, filters)


@router.get("/resumes/role-breakdown", response_model=ResumeAnalyticsResult)
def resume_role_breakdown(
    db: Session = Depends(get_db),
    window: TimeWindow = Query(default=TimeWindow.d90),
) -> ResumeAnalyticsResult:
    """Like-for-like slices only, over a longer default window.

    Comparing variants used on different kinds of job mostly measures the jobs;
    a wider window is what makes the within-role comparison viable at all.
    """
    return resume_analytics.compute_resume_analytics(db, AnalyticsFilters(window=window))


@router.get("/resumes/unattributed", response_model=UnattributedResponse)
def unattributed_applications(db: Session = Depends(get_db)) -> UnattributedResponse:
    """Applications whose resume was never recorded - the 补充历史简历 worklist.

    These are **not** guesses waiting to be confirmed. Nothing is pre-selected:
    v0.6 applications genuinely have no attribution, and only the user knows.
    """
    from app.services.application_analytics import load_jobs_for_analytics

    jobs = load_jobs_for_analytics(db)
    rows = resume_variants.unattributed_applications(db, jobs)
    return UnattributedResponse(
        items=[UnattributedApplication(**row) for row in rows],
        total=len(rows),
        message=(
            f"有 {len(rows)} 次历史投递未记录所用简历。"
            "这些记录不会被自动归属到任何简历版本。"
            if rows
            else "所有已记录的投递都已标注使用的简历。"
        ),
    )


# --------------------------------------------------------------------------
# interview pipeline (v0.8)
# --------------------------------------------------------------------------


@router.get("/interviews", response_model=InterviewAnalyticsResult)
def interview_analytics_endpoint(
    db: Session = Depends(get_db),
    window: TimeWindow = Query(default=TimeWindow.d90),
    city: str | None = Query(default=None),
    role_family: str | None = Query(default=None),
    source: str | None = Query(default=None),
    resume_id: int | None = Query(default=None),
) -> InterviewAnalyticsResult:
    """Stage funnel, round conversion, drop-off and latency.

    Deterministic and free - zero OpenAI calls, same statistics as every other
    cohort in v0.6/v0.7. Stages are derived from recorded interview rounds, not
    from ``Job.status``, and a candidate withdrawal is never counted as an
    employer rejection.

    Defaults to a 90-day window: interview processes run over weeks, so 30 days
    routinely cuts a candidacy in half.

    Carries no meeting URLs, interviewer names or feedback text.
    """
    filters = AnalyticsFilters(
        window=window,
        city=city,
        role_family=role_family,
        source=source,
        resume_id=resume_id,
    )
    return interview_analytics.compute_interview_analytics(db, filters)


# --------------------------------------------------------------------------
# offers (v0.9)
# --------------------------------------------------------------------------


@router.get("/offers", response_model=OfferAnalyticsResult)
def offer_analytics_endpoint(
    db: Session = Depends(get_db),
    window: TimeWindow = Query(default=TimeWindow.all_time),
    city: str | None = Query(default=None),
    role_family: str | None = Query(default=None),
    source: str | None = Query(default=None),
    resume_id: int | None = Query(default=None),
) -> OfferAnalyticsResult:
    """Offer funnel, compensation medians, negotiation uplift and decline reasons.

    Deterministic and free - zero OpenAI calls, same statistics as every other
    cohort since v0.6. Compensation is grouped strictly within each currency:
    v0.9 fetches no FX rates, so amounts are never converted or ranked across
    currencies. Accepted-offer figures come from the frozen
    ``accepted_revision_id``, so a later edit cannot rewrite a past decision.

    Defaults to all-time: offers are rare, and a 30-day window would usually be
    empty.
    """
    filters = AnalyticsFilters(
        window=window,
        city=city,
        role_family=role_family,
        source=source,
        resume_id=resume_id,
    )
    return offer_analytics.compute_offer_analytics(db, filters)
