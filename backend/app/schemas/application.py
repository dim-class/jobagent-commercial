"""Application queue + workflow schemas (v0.4).

Two ideas that must never be conflated:

* ``verdict``       - what the AI *recommends*
* ``job_status``    - what the *human* actually did

And since v0.7, two more:

* the **active analysis resume** - what a new job is matched against
* ``MarkAppliedRequest.resume_id`` - what the human actually submitted

Nothing in this module lets a model write a human status.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import JobStatus, ResumeUsage, Verdict


class ProposalState(str, Enum):
    """Where a job sits in the human's daily queue."""

    pending = "pending"      # analyzed, recommended, waiting for a decision
    ready = "ready"          # strong_apply - do this one first
    later = "later"          # deferred until review_after
    dismissed = "dismissed"  # the human skipped it
    completed = "completed"  # applied or further along the funnel


class ResponseType(str, Enum):
    positive = "positive"
    neutral = "neutral"
    negative = "negative"


class InterviewRound(str, Enum):
    hr = "HR"
    first = "一面"
    second = "二面"
    technical = "技术面"
    final = "终面"
    other = "其他"


class LaterPreset(str, Enum):
    today = "today"        # later today
    tomorrow = "tomorrow"
    custom = "custom"      # uses review_after


SKIP_REASONS: tuple[str, ...] = (
    "薪资太低",
    "经验要求过高",
    "技术方向不符",
    "外包/驻场",
    "地点不合适",
    "公司不感兴趣",
    "已在其他渠道投递",
    "其他",
)


# --------------------------------------------------------------------------
# proposal
# --------------------------------------------------------------------------


class ApplicationEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    notes: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ApplicationProposal(BaseModel):
    """Derived from Job + latest JobAnalysis + latest ApplicationEvent.

    Nothing here is stored: the queue is a *view*, so a re-analysis or a status
    change is reflected immediately with no extra table to keep in sync.
    """

    job_id: int

    company: str
    title: str
    city: str | None = None
    salary_text: str | None = None
    source: str = "manual"
    #: The posting's own page, already query-stripped by `canonical_url()` at
    #: intake. The queue offers it so applying is "open, paste, send" instead of
    #: hunting for the job again - opening a page decides nothing. The separate
    #: M6 gate is the only code path that may attempt one confirmed application.
    source_url: str | None = None

    overall_score: int
    verdict: Verdict
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""
    greeting_message: str = ""

    #: Whether the posting explicitly targets an early-career cohort
    #: (应届 / 校招 / 实习). Computed deterministically from title + JD by the
    #: same `job_eligibility` classifier the intake uses - never a model.
    early_career: bool = False

    job_status: JobStatus
    proposal_state: ProposalState
    review_after: datetime | None = None
    latest_application_event: ApplicationEventOut | None = None

    created_at: datetime
    analyzed_at: datetime | None = None


class QueueSummary(BaseModel):
    pending: int = 0
    strong_apply: int = 0
    apply: int = 0
    later: int = 0
    #: How many otherwise-eligible proposals the candidate-stage policy hid.
    #: Reported, never silent: the UI says so and can show them on request.
    early_career_hidden: int = 0
    early_career_policy: str = "include"
    applied_today: int = 0
    replied_today: int = 0
    interview_today: int = 0
    skipped_today: int = 0
    daily_target: int = 10
    timezone: str = "Asia/Tokyo"


class QueueResponse(BaseModel):
    items: list[ApplicationProposal]
    total: int
    summary: QueueSummary
    facets: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# workflow requests
# --------------------------------------------------------------------------


class MarkAppliedRequest(BaseModel):
    """Recorded only after the user confirms they applied *outside* JobAgent."""

    confirmed: bool = Field(
        default=False,
        description="必须为 true —— 表示用户已在招聘平台完成真实投递",
    )
    note: str | None = Field(default=None, max_length=1000)
    applied_at: datetime | None = None

    #: Which resume the human actually submitted (v0.7). This is *not* the
    #: active analysis resume: the UI defaults to it but the user may change it,
    #: and a job analysed with one variant may well be applied to with another.
    resume_id: int | None = Field(
        default=None, description="本次实际投递使用的简历 ID；未提交简历时为 null"
    )
    resume_usage: ResumeUsage = Field(
        default=ResumeUsage.unknown,
        description="used 时必须提供 resume_id；no_resume 表示确实没投简历；unknown 表示不确定",
    )

    @model_validator(mode="after")
    def _check_resume(self) -> "MarkAppliedRequest":
        """A stated usage and the id must agree - we never fill one in for the
        other, because a wrong attribution is worse than a missing one."""
        if self.resume_id is not None and self.resume_usage is not ResumeUsage.used:
            self.resume_usage = ResumeUsage.used
        if self.resume_usage is ResumeUsage.used and self.resume_id is None:
            raise ValueError("选择了「使用简历」但没有指定是哪一份。")
        return self


# --------------------------------------------------------------------------
# M6 per-job confirmation gate
# --------------------------------------------------------------------------


class ApplicationApprovalCreate(BaseModel):
    """One readable human decision for one already-named job."""

    model_config = ConfigDict(extra="forbid")

    resume_id: int = Field(gt=0)
    #: Empty for `boss_dynamic_unverified` (BOSS decides, unverifiable); the
    #: exact text JobAgent will type for `boss_typed_greeting`. The service
    #: enforces which goes with which - the schema only bounds the size.
    answers_text: str = Field(default="", max_length=1000)
    answers_source: Literal["boss_dynamic_unverified", "boss_typed_greeting"]
    confirmed: bool = False


class ApplicationApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    company: str
    title: str
    canonical_url: str
    external_id: str
    resume_id: int
    resume_hash: str
    answers_text: str
    answers_hash: str
    answers_source: str
    state: str
    invalidated_reason: str | None = None
    outcome: str | None = None
    outcome_detail: str | None = None
    consumed_at: datetime | None = None
    attempt_started_at: datetime | None = None
    applied_event_id: int | None = None
    created_at: datetime
    updated_at: datetime


class ApplicationApprovalCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_url: str = Field(min_length=1, max_length=1024)
    observed_external_id: str = Field(min_length=1, max_length=128)


class ApplicationApprovalCheckOut(BaseModel):
    ok: bool
    reason: str | None = None
    approval: ApplicationApprovalOut


class ApplicationApprovalAbandonRequest(BaseModel):
    """Human closes out an attempt whose result never came back.

    There is deliberately no ``outcome`` field: this can only ever record
    ``unknown``, so no caller can turn a lost attempt into a success.
    """

    confirmed: bool = Field(default=False, description="必须明确确认")


class ApplicationApprovalOutcomeRequest(ApplicationApprovalCheckRequest):
    outcome: str = Field(pattern="^(applied|unknown|failed)$")
    detail: str | None = Field(default=None, max_length=256)


class AttributeResumeRequest(BaseModel):
    """Fill in (or correct) which resume an already-recorded application used.

    Explicit human editing only - see CLAUDE.md. Nothing infers this.
    """

    #: Which cycle to attribute, identified by its ``applied`` event id.
    #: Omitted means the job's latest application cycle.
    applied_event_id: int | None = None
    resume_id: int | None = None
    resume_usage: ResumeUsage = ResumeUsage.unknown
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _check_resume(self) -> "AttributeResumeRequest":
        if self.resume_id is not None:
            self.resume_usage = ResumeUsage.used
        if self.resume_usage is ResumeUsage.used and self.resume_id is None:
            raise ValueError("选择了「使用简历」但没有指定是哪一份。")
        return self


class SkipRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=1000)


class LaterRequest(BaseModel):
    preset: LaterPreset = LaterPreset.tomorrow
    review_after: datetime | None = Field(
        default=None, description="preset=custom 时使用"
    )
    note: str | None = Field(default=None, max_length=1000)


class ResetRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class ReplyRequest(BaseModel):
    response_type: ResponseType = ResponseType.neutral
    replied_at: datetime | None = None
    note: str | None = Field(default=None, max_length=1000)


class InterviewRequest(BaseModel):
    round: InterviewRound | None = None
    interview_at: datetime | None = None
    note: str | None = Field(default=None, max_length=1000)


class OfferRequest(BaseModel):
    salary_text: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=1000)


class RejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=1000)


class WorkflowResponse(BaseModel):
    job_id: int
    status: JobStatus
    previous_status: JobStatus
    event: ApplicationEventOut
    message: str = ""


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


class FunnelCounts(BaseModel):
    total_jobs: int = 0
    analyzed_jobs: int = 0
    recommended_jobs: int = 0
    applied_jobs: int = 0
    replied_jobs: int = 0
    interview_jobs: int = 0
    offer_jobs: int = 0
    rejected_jobs: int = 0


class FunnelRates(BaseModel):
    """Rates are ``None`` when the denominator is zero - never 0.0."""

    application_response_rate: float | None = None
    application_interview_rate: float | None = None
    response_interview_rate: float | None = None


class ApplicationMetrics(BaseModel):
    counts: FunnelCounts
    rates: FunnelRates
    by_city: dict[str, dict[str, int]] = Field(default_factory=dict)
    by_role_family: dict[str, dict[str, int]] = Field(default_factory=dict)
    by_source: dict[str, dict[str, int]] = Field(default_factory=dict)


class AppliedBackfillPlanRequest(BaseModel):
    """Text the human copied from BOSS's own 沟通过的职位 list."""

    text: str = Field(min_length=1, max_length=200_000)


class AppliedBackfillMatchOut(BaseModel):
    job_id: int
    company: str
    title: str
    status: str
    title_matched: bool
    can_apply: bool
    reason: str = ""


class AppliedBackfillPlanResponse(BaseModel):
    """Which stored jobs appear in that text. Reading it records nothing."""

    confident: list[AppliedBackfillMatchOut] = Field(default_factory=list)
    needs_review: list[AppliedBackfillMatchOut] = Field(default_factory=list)
    already_applied: list[AppliedBackfillMatchOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AppliedBackfillConfirmRequest(BaseModel):
    """One explicit confirmation, carrying the count the human was shown."""

    job_ids: list[int] = Field(min_length=1, max_length=500)
    confirmed: bool = Field(default=False)
    #: What the dialog displayed. A mismatch cancels rather than recording a
    #: different set than the one that was read.
    expected_count: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=1000)


class AppliedBackfillConfirmResponse(BaseModel):
    recorded: list[int] = Field(default_factory=list)
    skipped: list[dict] = Field(default_factory=list)
