"""Enumerations shared by models and API schemas."""

from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    """Pipeline state of a job. ``AI recommends, human approves.``"""

    new = "new"
    reviewed = "reviewed"
    saved = "saved"
    skipped = "skipped"
    applied = "applied"
    replied = "replied"
    interview = "interview"
    offer = "offer"
    rejected = "rejected"


class TaskMode(str, Enum):
    """Execution mode for a ``JobSearchTask``.

    Only one mode exists in M1. Anything that would let the system itself
    open, navigate, or interact with a recruitment page on a task's behalf
    requires an explicit ``CLAUDE.md`` policy change and separate
    authorization first - see ``docs/orchestration/ROADMAP.md`` (M4). Adding
    a member here without that happening first would be the policy change
    by accident.
    """

    manual_review_only = "manual_review_only"


class SupervisedSessionStatus(str, Enum):
    """M4a bounded-session state. See CLAUDE.md's "Chrome extension - M4
    supervised navigation policy". No navigation happens in M4a - this only
    tracks a human's explicit start/stop of a bounded, capped session.
    """

    running = "running"
    stopped = "stopped"


class SupervisedSessionEventType(str, Enum):
    """Append-only audit trail for a ``SupervisedSession``. M4a wrote only
    ``started``/``stopped``; M4b (explicitly authorized - see CLAUDE.md's
    "Chrome extension - M4 supervised navigation policy") adds
    ``navigated`` for one accounted, human-initiated navigation step
    (results-page visit or candidate-card open). No native DB enum backs
    this - it is a plain string column - so adding a member here needed no
    migration. M4c scroll/pagination event types remain for a later,
    separately-authorized milestone.
    """

    started = "started"
    stopped = "stopped"
    navigated = "navigated"
    #: A prepared navigation whose click did not complete (ambiguous
    #: selector, out-of-range index, etc.) - audited, but never counted
    #: against page_cap/candidate_cap. See ``services.supervised_sessions
    #: .navigate_prepare/navigate_confirm``.
    navigate_failed = "navigate_failed"


class OrchestrationEventType(str, Enum):
    """M3 console audit trail. Every value is an explicit action a human took
    inside the console - never inferred from a page view, a timer, or any
    other passive signal. See docs/orchestration/ROADMAP.md (M3).
    """

    opened = "opened"
    reviewed = "reviewed"
    dismissed = "dismissed"


class Verdict(str, Enum):
    strong_apply = "strong_apply"
    apply = "apply"
    maybe = "maybe"
    skip = "skip"


class JobSourceName(str, Enum):
    """Where a job came from. v0.1 only implements ``manual`` and ``demo``."""

    manual = "manual"
    demo = "demo"
    boss = "boss"
    liepin = "liepin"
    zhaopin = "zhaopin"
    job51 = "job51"


class EventType(str, Enum):
    """ApplicationEvent kinds. The trail is append-only - see CLAUDE.md.

    Every value here records something a *human* did (or that the system did on
    their behalf, like an analysis). Nothing here is ever produced by an AI
    verdict: the model recommends, the person decides.
    """

    viewed = "viewed"
    analyzed = "analyzed"
    saved = "saved"
    skipped = "skipped"
    status_changed = "status_changed"
    greeting_copied = "greeting_copied"
    applied = "applied"
    replied = "replied"
    interview = "interview"
    offer = "offer"
    rejected = "rejected"
    note = "note"
    # --- v0.4 -----------------------------------------------------------
    later = "later"
    status_reset = "status_reset"
    # --- v0.5 -----------------------------------------------------------
    #: The human confirmed they sent a reply themselves. JobAgent sends nothing.
    candidate_reply = "candidate_reply"
    # --- v0.7 -----------------------------------------------------------
    #: A human filled in which resume an *older* application actually used.
    #: Corrective, never inferred - see ResumeUsage.unknown.
    application_resume_attributed = "application_resume_attributed"
    #: A human corrected an attribution that was already recorded.
    application_resume_changed = "application_resume_changed"
    # --- v0.8 -----------------------------------------------------------
    #: Interview pipeline milestones. Detail lives in InterviewRound; these
    #: exist so the timeline still reads as a story. One per real milestone -
    #: editing a field emits nothing.
    interview_scheduled = "interview_scheduled"
    interview_completed = "interview_completed"
    interview_passed = "interview_passed"
    interview_failed = "interview_failed"
    interview_cancelled = "interview_cancelled"
    #: The candidate ended the process. Never an employer rejection.
    interview_withdrawn = "interview_withdrawn"
    #: A recorded outcome was re-recorded. The original event stays.
    interview_round_corrected = "interview_round_corrected"
    # --- v0.9 -----------------------------------------------------------
    #: Offer milestones. Compensation NEVER goes in the event payload - these
    #: carry offer_id / offer_revision_id references only. ``offer`` (v0.4)
    #: remains the workflow milestone that moves Job.status.
    offer_received = "offer_received"
    offer_countered = "offer_countered"
    offer_revised = "offer_revised"
    offer_accepted = "offer_accepted"
    offer_declined = "offer_declined"
    #: A revision's figures were entered wrongly and corrected. Append-only:
    #: the original revision row is never edited in place.
    offer_revision_corrected = "offer_revision_corrected"


#: Statuses that mean the human already dealt with this job, so it should not
#: sit in the daily queue any more.
DECIDED_STATUSES: frozenset[JobStatus] = frozenset(
    {
        JobStatus.applied,
        JobStatus.replied,
        JobStatus.interview,
        JobStatus.offer,
        JobStatus.rejected,
        JobStatus.skipped,
    }
)

#: Verdicts the queue treats as a recommendation to apply.
RECOMMENDED_VERDICTS: frozenset[Verdict] = frozenset({Verdict.strong_apply, Verdict.apply})


# --------------------------------------------------------------------------
# recruiter conversations (v0.5)
# --------------------------------------------------------------------------


class MessageDirection(str, Enum):
    """Who wrote a message. ``user`` rows are only ever written after the
    human confirms they sent the text themselves."""

    recruiter = "recruiter"
    user = "user"


class ConversationStatus(str, Enum):
    needs_reply = "needs_reply"
    waiting_recruiter = "waiting_recruiter"
    follow_up_due = "follow_up_due"
    closed = "closed"
    no_action = "no_action"


class RecruiterSource(str, Enum):
    """Where the conversation happened. Informational only - v0.5 connects to
    nothing and reads no inbox."""

    boss = "boss"
    liepin = "liepin"
    zhaopin = "zhaopin"
    job51 = "job51"
    linkedin = "linkedin"
    wechat = "wechat"
    email = "email"
    phone = "phone"
    other = "other"


#: Conversation states that still want something from the user.
OPEN_CONVERSATION_STATUSES: frozenset[ConversationStatus] = frozenset(
    {
        ConversationStatus.needs_reply,
        ConversationStatus.waiting_recruiter,
        ConversationStatus.follow_up_due,
    }
)


# --------------------------------------------------------------------------
# career strategy analytics (v0.6)
# --------------------------------------------------------------------------


class StrategyChangeSource(str, Enum):
    """Who initiated a strategy edit. Analytics never edits on its own."""

    manual = "manual"
    analytics_recommendation = "analytics_recommendation"


class RecommendationDecision(str, Enum):
    accepted = "accepted"
    dismissed = "dismissed"


# --------------------------------------------------------------------------
# resume variants (v0.7)
# --------------------------------------------------------------------------


class ResumeUsage(str, Enum):
    """Whether a resume was submitted for one application, and which.

    ``unknown`` is a real answer, not a missing value. Historical applications
    recorded before v0.7 are ``unknown`` forever unless a human says otherwise -
    guessing them from whichever resume is active today would fabricate history.
    """

    #: A specific resume was submitted; the cycle carries its id.
    used = "used"
    #: The user is sure no resume was submitted (a chat-only recruiter contact).
    no_resume = "no_resume"
    #: Not recorded. Never inferred.
    unknown = "unknown"


#: Event kinds that carry a resume attribution rather than a status change.
RESUME_ATTRIBUTION_EVENTS: frozenset[EventType] = frozenset(
    {EventType.application_resume_attributed, EventType.application_resume_changed}
)


# --------------------------------------------------------------------------
# interview pipeline (v0.8)
# --------------------------------------------------------------------------


class InterviewProcessStatus(str, Enum):
    """Where a whole interview process stands.

    ``rejected`` means the employer ended it; ``withdrawn`` means the candidate
    did. Collapsing the two would make every "drop-off" number wrong.
    """

    ongoing = "ongoing"
    completed = "completed"
    rejected = "rejected"
    withdrawn = "withdrawn"
    offer = "offer"


#: Process states that are no longer moving.
CLOSED_PROCESS_STATUSES: frozenset[InterviewProcessStatus] = frozenset(
    {
        InterviewProcessStatus.completed,
        InterviewProcessStatus.rejected,
        InterviewProcessStatus.withdrawn,
        InterviewProcessStatus.offer,
    }
)


class InterviewRoundType(str, Enum):
    hr = "hr"
    screening = "screening"
    technical = "technical"
    coding = "coding"
    system_design = "system_design"
    manager = "manager"
    culture = "culture"
    final = "final"
    other = "other"


#: Round types that count as a "technical" stage for funnel reporting.
TECHNICAL_ROUND_TYPES: frozenset[InterviewRoundType] = frozenset(
    {
        InterviewRoundType.technical,
        InterviewRoundType.coding,
        InterviewRoundType.system_design,
    }
)


class InterviewRoundStatus(str, Enum):
    planned = "planned"        # agreed in principle, no time yet
    scheduled = "scheduled"    # has a scheduled_at
    completed = "completed"
    cancelled = "cancelled"


class InterviewOutcome(str, Enum):
    """Result of one round. ``pending`` is "not yet known", not "failed"."""

    pending = "pending"
    passed = "passed"
    failed = "failed"
    unknown = "unknown"


class InterviewLocationType(str, Enum):
    online = "online"
    onsite = "onsite"
    phone = "phone"
    unknown = "unknown"


class InterviewFailureReason(str, Enum):
    """Optional, never forced - an unexplained rejection stays unexplained."""

    technical_depth = "technical_depth"
    experience_years = "experience_years"
    language = "language"
    role_fit = "role_fit"
    salary = "salary"
    visa = "visa"
    culture_fit = "culture_fit"
    position_cancelled = "position_cancelled"
    unknown = "unknown"
    other = "other"


class WithdrawReason(str, Enum):
    accepted_other_offer = "accepted_other_offer"
    salary = "salary"
    company = "company"
    location = "location"
    role_content = "role_content"
    personal = "personal"
    other = "other"


class FeedbackTag(str, Enum):
    """User-applied tags on interview feedback.

    v0.8 never infers these from free text - a tag means the human chose it.
    """

    technical_depth = "technical_depth"
    communication = "communication"
    language = "language"
    experience = "experience"
    cloud = "cloud"
    coding = "coding"
    system_design = "system_design"
    culture = "culture"
    salary = "salary"
    other = "other"


# --------------------------------------------------------------------------
# offers (v0.9)
# --------------------------------------------------------------------------


class OfferStatus(str, Enum):
    """Detailed offer truth. ``Job.status`` stays the coarse workflow status."""

    draft = "draft"
    received = "received"
    negotiating = "negotiating"
    accepted = "accepted"
    declined = "declined"
    withdrawn = "withdrawn"
    expired = "expired"


#: Offer states that are no longer moving.
CLOSED_OFFER_STATUSES: frozenset[OfferStatus] = frozenset(
    {
        OfferStatus.accepted,
        OfferStatus.declined,
        OfferStatus.withdrawn,
        OfferStatus.expired,
    }
)


class RevisionType(str, Enum):
    initial = "initial"
    company_revision = "company_revision"
    candidate_counter = "candidate_counter"
    final = "final"
    other = "other"


class RevisionSource(str, Enum):
    """Who this revision came from.

    Load-bearing: a candidate counter is what *you asked for*, not what the
    company is offering. Mixing the two would misreport every current offer.
    """

    company = "company"
    candidate = "candidate"
    manual = "manual"


#: Revisions that state what the company is actually offering.
COMPANY_REVISION_SOURCES: frozenset[RevisionSource] = frozenset(
    {RevisionSource.company, RevisionSource.manual}
)


class Currency(str, Enum):
    """Offers are compared within a currency, never across one.

    v0.9 fetches no FX rates, so two currencies are shown side by side in their
    native values rather than collapsed into a fake ranking.
    """

    CNY = "CNY"
    JPY = "JPY"
    USD = "USD"
    EUR = "EUR"
    HKD = "HKD"
    SGD = "SGD"
    GBP = "GBP"
    other = "other"


class EquityType(str, Enum):
    rsu = "rsu"
    options = "options"
    restricted_stock = "restricted_stock"
    unknown = "unknown"


class EmploymentType(str, Enum):
    full_time = "full_time"
    contract = "contract"
    dispatch = "dispatch"
    part_time = "part_time"
    internship = "internship"
    other = "other"


class RemotePolicy(str, Enum):
    onsite = "onsite"
    hybrid = "hybrid"
    remote = "remote"
    unknown = "unknown"


class DeclineReason(str, Enum):
    """Why the candidate turned an offer down. Never an employer rejection."""

    salary = "salary"
    role_content = "role_content"
    location = "location"
    remote_policy = "remote_policy"
    company = "company"
    growth = "growth"
    accepted_other_offer = "accepted_other_offer"
    visa = "visa"
    start_date = "start_date"
    personal = "personal"
    other = "other"


class BenefitKey(str, Enum):
    """Structured non-cash factors.

    Deliberately never assigned a monetary value: "company housing available"
    is not cash unless the user says what it is worth.
    """

    paid_leave = "paid_leave"
    remote_policy = "remote_policy"
    flex_time = "flex_time"
    housing_support = "housing_support"
    relocation_support = "relocation_support"
    transport_support = "transport_support"
    visa_support = "visa_support"
    education_budget = "education_budget"
    language_allowance = "language_allowance"
    healthcare = "healthcare"
    other = "other"


# --------------------------------------------------------------------------
# offer decision support (v1.0)
# --------------------------------------------------------------------------


class DecisionDimension(str, Enum):
    """What a decision profile can weigh.

    Every dimension is scored 0-1 and combined with user-entered weights.
    ``compensation`` is derived from the offer's own figures; the rest are the
    human's own 1-5 ratings. Nothing here is ever rated by a model.
    """

    compensation = "compensation"
    career_growth = "career_growth"
    role_fit = "role_fit"
    remote_work = "remote_work"
    location = "location"
    work_life_balance = "work_life_balance"
    company_stability = "company_stability"
    technology_fit = "technology_fit"
    language_environment = "language_environment"
    visa_support = "visa_support"
    brand_value = "brand_value"


#: Dimensions the system can derive from recorded offer facts. Everything else
#: is subjective and stays a human rating.
DERIVED_DIMENSIONS: frozenset[DecisionDimension] = frozenset(
    {
        DecisionDimension.compensation,
        DecisionDimension.remote_work,
        DecisionDimension.visa_support,
    }
)


class DealBreakerKind(str, Enum):
    """Hard constraints the user sets. Never auto-rejects an offer."""

    minimum_guaranteed_cash = "minimum_guaranteed_cash"
    requires_visa_support = "requires_visa_support"
    requires_remote_or_hybrid = "requires_remote_or_hybrid"
    required_location = "required_location"
    latest_start_date = "latest_start_date"


class DealBreakerResult(str, Enum):
    """``unknown`` is an answer, not a failure - the fact was never recorded."""

    passed = "passed"
    failed = "failed"
    unknown = "unknown"
