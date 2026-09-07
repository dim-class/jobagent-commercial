"""The daily "what should I apply to today?" queue (v0.4).

The queue is **derived**, not stored: a proposal is just Job + its latest
JobAnalysis + its latest ApplicationEvent, assembled on read. That means a
re-analysis, a status change or an edit shows up immediately, and there is no
second table to keep in sync (and no way for the two to disagree).

Eligibility is driven by the AI *verdict*, not by a score threshold - a job the
model says to apply to stays in the queue even if its number is modest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import ApplicationEvent, EventType, Job, JobAnalysis, JobStatus, Verdict
from app.models.enums import DECIDED_STATUSES, RECOMMENDED_VERDICTS
from app.schemas.application import (
    ApplicationEventOut,
    ApplicationProposal,
    ProposalState,
    QueueSummary,
)
from app.services.timezones import is_local_today, local_now
from app.services.job_eligibility import is_early_career_track
from app.services.scoring import extract_experience_requirement
from app.services.urls import canonical_url, is_openable_posting_url

#: Sort keys the UI offers.
SORT_KEYS = ("recommended", "score", "newest", "salary")

_VERDICT_RANK: dict[Verdict, int] = {
    Verdict.strong_apply: 0,
    Verdict.apply: 1,
    Verdict.maybe: 2,
    Verdict.skip: 3,
}


@dataclass(slots=True)
class QueueFilters:
    city: str | None = None
    verdict: Verdict | None = None
    min_score: int | None = None
    keyword: str | None = None
    status: JobStatus | None = None
    source: str | None = None
    include_maybe: bool = False
    include_decided: bool = False
    #: The candidate-stage policy this view applies (from the live strategy).
    early_career_policy: str = "include"
    #: "Show me only what I could actually meet": drops postings whose *minimum*
    #: requirement is above this. A posting that never said is kept - unknown is
    #: not a reason to hide a job the user might well be right for.
    max_required_years: int | None = None
    #: Explicit "show me what the policy hid". Never on by default, so the
    #: policy actually takes effect, and never silent - `build_summary`
    #: reports the count either way.
    include_early_career: bool = False
    sort: str = "recommended"
    limit: int = 50
    offset: int = 0


def latest_analysis_of(job: Job) -> JobAnalysis | None:
    if not job.analyses:
        return None
    return max(job.analyses, key=lambda a: (a.created_at, a.id))


def latest_event_of(job: Job) -> ApplicationEvent | None:
    if not job.events:
        return None
    return max(job.events, key=lambda e: (e.created_at, e.id))


def proposal_state(job: Job, *, now: datetime | None = None) -> ProposalState:
    """Where this job sits for the human right now."""
    if job.status is JobStatus.skipped:
        return ProposalState.dismissed
    if job.status in DECIDED_STATUSES:
        return ProposalState.completed

    moment = now or local_now()
    if job.review_after is not None:
        review_after = job.review_after
        if review_after.tzinfo is None:
            from datetime import timezone as _tz

            review_after = review_after.replace(tzinfo=_tz.utc)
        if review_after > moment:
            return ProposalState.later

    analysis = latest_analysis_of(job)
    if analysis is not None and analysis.verdict is Verdict.strong_apply:
        return ProposalState.ready
    return ProposalState.pending


#: Statuses that mean "a conversation with this company already exists".
#: Applying is a stage rather than an endpoint, so everything downstream of it
#: counts - a job that went to interview was still contacted.
_CONTACTED_STATUSES = frozenset(
    {
        JobStatus.applied,
        JobStatus.replied,
        JobStatus.interview,
        JobStatus.offer,
        JobStatus.rejected,
    }
)


def contacted_companies(jobs: list[Job]) -> dict[str, Job]:
    """The one already-contacted job per company, if any.

    Normalised on whitespace only: 「阿里云」 and 「阿里云 」 are the same
    employer, but nothing further is collapsed - trimming punctuation would
    start merging companies that merely look alike.
    """
    contacted: dict[str, Job] = {}
    for job in jobs:
        if job.status not in _CONTACTED_STATUSES or not job.company:
            continue
        key = "".join((job.company or "").split())
        # Keep the most recent, so the heads-up names the freshest contact.
        previous = contacted.get(key)
        if previous is None or job.updated_at >= previous.updated_at:
            contacted[key] = job
    return contacted


def build_proposal(
    job: Job,
    *,
    now: datetime | None = None,
    contacted: dict[str, Job] | None = None,
) -> ApplicationProposal | None:
    """None when the job has never been analyzed - it cannot be proposed yet."""
    analysis = latest_analysis_of(job)
    if analysis is None:
        return None

    sibling = (contacted or {}).get("".join((job.company or "").split()))
    if sibling is not None and sibling.id == job.id:
        sibling = None  # a job never flags itself

    result = analysis.result_json or {}
    event = latest_event_of(job)

    return ApplicationProposal(
        job_id=job.id,
        company=job.company,
        title=job.title,
        city=job.city,
        salary_text=job.salary_text,
        source=job.source,
        # Rows created before intake canonicalised every path may still carry a
        # query; never hand one to a link the user is about to click - and only
        # offer the link at all when the stored URL is backed by the row's own
        # id, so a leftover fixture URL is not presented as the posting.
        source_url=(
            canonical_url(job.source_url)
            if is_openable_posting_url(canonical_url(job.source_url), external_id=job.external_id)
            else None
        ),
        overall_score=analysis.overall_score,
        verdict=analysis.verdict,
        matched_skills=[str(s) for s in (result.get("matched_skills") or [])],
        missing_skills=[str(s) for s in (result.get("missing_skills") or [])],
        reasoning_summary=str(result.get("reasoning_summary") or ""),
        greeting_message=str(result.get("greeting_message") or ""),
        company_applied_title=sibling.title if sibling else None,
        company_applied_job_id=sibling.id if sibling else None,
        early_career=is_early_career_track(job.title, job.normalized_description),
        experience_min_years=extract_experience_requirement(
            job.experience_text or "", job.normalized_description or ""
        ).min_years,
        experience_text=job.experience_text,
        job_status=job.status,
        proposal_state=proposal_state(job, now=now),
        review_after=job.review_after,
        latest_application_event=(
            ApplicationEventOut(
                id=event.id,
                event_type=event.event_type.value,
                notes=event.notes,
                metadata_json=event.metadata_json or {},
                created_at=event.created_at,
            )
            if event is not None
            else None
        ),
        created_at=job.created_at,
        analyzed_at=analysis.created_at,
    )


def is_eligible(proposal: ApplicationProposal, filters: QueueFilters) -> bool:
    """Queue membership. Verdict is the primary signal, never the raw score."""
    allowed = set(RECOMMENDED_VERDICTS)
    if filters.include_maybe:
        allowed.add(Verdict.maybe)
    if proposal.verdict not in allowed:
        return False

    # A job the human already decided on is out, unless explicitly asked for.
    if not filters.include_decided and proposal.job_status in DECIDED_STATUSES:
        return False

    if filters.city and proposal.city != filters.city:
        return False
    if filters.verdict and proposal.verdict != filters.verdict:
        return False
    if filters.min_score is not None and proposal.overall_score < filters.min_score:
        return False
    if filters.status and proposal.job_status != filters.status:
        return False
    if filters.source and proposal.source != filters.source:
        return False
    if (
        filters.max_required_years is not None
        and proposal.experience_min_years is not None
        and proposal.experience_min_years > filters.max_required_years
    ):
        return False

    # The candidate-stage policy is a *view* rule: it hides rows and never
    # touches `Job.status`. `proposal.early_career` was classified from title +
    # full JD when the proposal was built, so re-deriving it here from the
    # title alone would be a second, weaker classifier. `include` is a no-op,
    # and `include_early_career` is the visible escape hatch.
    if not filters.include_early_career:
        if filters.early_career_policy == "exclude" and proposal.early_career:
            return False
        if filters.early_career_policy == "only" and not proposal.early_career:
            return False
    if filters.keyword:
        needle = filters.keyword.strip().lower()
        haystack = " ".join(
            [proposal.title, proposal.company, " ".join(proposal.matched_skills)]
        ).lower()
        if needle not in haystack:
            return False
    return True


def _salary_sort_value(proposal: ApplicationProposal) -> int:
    """Upper bound of a parsed salary, or -1 when it cannot be parsed.

    Unparseable salaries sort last rather than being given an invented number.
    """
    from app.services.scoring import extract_salary_range

    low, high = extract_salary_range(proposal.salary_text)
    return high or low or -1


def sort_proposals(items: list[ApplicationProposal], sort: str) -> list[ApplicationProposal]:
    # job_id breaks ties: SQLite timestamps have second granularity, so two
    # jobs analyzed in the same second would otherwise order arbitrarily.
    if sort == "score":
        return sorted(
            items,
            key=lambda p: (p.overall_score, p.analyzed_at or p.created_at, p.job_id),
            reverse=True,
        )
    if sort == "newest":
        return sorted(
            items, key=lambda p: (p.analyzed_at or p.created_at, p.job_id), reverse=True
        )
    if sort == "salary":
        return sorted(
            items,
            key=lambda p: (_salary_sort_value(p), p.overall_score, p.job_id),
            reverse=True,
        )
    # recommended (default): strong_apply first, then apply, then score, then recency
    return sorted(
        items,
        key=lambda p: (
            _VERDICT_RANK.get(p.verdict, 9),
            -p.overall_score,
            -(p.analyzed_at or p.created_at).timestamp(),
            -p.job_id,
        ),
    )


def load_jobs(db: Session) -> list[Job]:
    return list(
        db.scalars(
            select(Job).options(selectinload(Job.analyses), selectinload(Job.events))
        ).unique()
    )


def all_proposals(db: Session, *, now: datetime | None = None) -> list[ApplicationProposal]:
    moment = now or local_now()
    jobs = load_jobs(db)
    # Computed once over the whole set rather than per proposal: the answer is
    # about the library, not about the row being built.
    contacted = contacted_companies(jobs)
    built = (build_proposal(job, now=moment, contacted=contacted) for job in jobs)
    return [p for p in built if p is not None]


def build_summary(
    db: Session,
    proposals: list[ApplicationProposal],
    *,
    daily_target: int,
    timezone_name: str,
    early_career_policy: str = "include",
    include_maybe: bool = False,
) -> QueueSummary:
    """Counts for the header cards. Daily numbers use the local calendar day.

    ``early_career_hidden`` is counted over the same active set the queue draws
    from, so a policy that hides rows always says how many. Filtering the queue
    without saying so would be exactly the kind of silent exclusion this project
    refuses everywhere else.
    """
    active = [
        p
        for p in proposals
        if p.verdict in RECOMMENDED_VERDICTS and p.job_status not in DECIDED_STATUSES
    ]
    pending = [p for p in active if p.proposal_state is not ProposalState.later]

    today_counts = {
        EventType.applied: 0,
        EventType.replied: 0,
        EventType.interview: 0,
        EventType.skipped: 0,
    }
    for event in db.scalars(select(ApplicationEvent)):
        if event.event_type in today_counts and is_local_today(event.created_at):
            today_counts[event.event_type] += 1

    # Counted over the same verdict set the queue itself is showing: with
    # `include_maybe` on, a hidden `maybe` row is one the user would otherwise
    # have seen, so leaving it out made the notice under-report what vanished.
    stage_allowed = set(RECOMMENDED_VERDICTS) | ({Verdict.maybe} if include_maybe else set())
    stage_scope = [
        p
        for p in proposals
        if p.verdict in stage_allowed and p.job_status not in DECIDED_STATUSES
    ]
    if early_career_policy == "exclude":
        hidden = sum(1 for p in stage_scope if p.early_career)
    elif early_career_policy == "only":
        hidden = sum(1 for p in stage_scope if not p.early_career)
    else:
        hidden = 0

    return QueueSummary(
        early_career_hidden=hidden,
        early_career_policy=early_career_policy,
        pending=len(pending),
        strong_apply=sum(1 for p in pending if p.verdict is Verdict.strong_apply),
        apply=sum(1 for p in pending if p.verdict is Verdict.apply),
        later=sum(1 for p in active if p.proposal_state is ProposalState.later),
        applied_today=today_counts[EventType.applied],
        replied_today=today_counts[EventType.replied],
        interview_today=today_counts[EventType.interview],
        skipped_today=today_counts[EventType.skipped],
        daily_target=daily_target,
        timezone=timezone_name,
    )


def build_facets(proposals: list[ApplicationProposal]) -> dict[str, dict[str, int]]:
    cities: dict[str, int] = {}
    sources: dict[str, int] = {}
    for p in proposals:
        if p.city:
            cities[p.city] = cities.get(p.city, 0) + 1
        sources[p.source] = sources.get(p.source, 0) + 1
    return {
        "cities": dict(sorted(cities.items(), key=lambda kv: -kv[1])),
        "sources": dict(sorted(sources.items(), key=lambda kv: -kv[1])),
    }
