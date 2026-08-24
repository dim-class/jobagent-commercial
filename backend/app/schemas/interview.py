"""Interview pipeline schemas (v0.8).

``meeting_url`` appears on round detail responses only. It is never included in
anything analytics returns, because meeting links routinely embed an access
token and analytics payloads are the thing most likely to get shared.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import (
    FeedbackTag,
    InterviewFailureReason,
    InterviewLocationType,
    InterviewOutcome,
    InterviewProcessStatus,
    InterviewRoundStatus,
    InterviewRoundType,
    WithdrawReason,
)


# --------------------------------------------------------------------------
# requests
# --------------------------------------------------------------------------


class PreparationNotes(BaseModel):
    """User-entered prep/recall. v0.8 never generates or interprets these."""

    questions_asked: list[str] = Field(default_factory=list, max_length=50)
    weak_points: list[str] = Field(default_factory=list, max_length=50)
    follow_up_topics: list[str] = Field(default_factory=list, max_length=50)


class RoundCreateRequest(BaseModel):
    """Only the round type is really required - everything else can come later."""

    round_type: InterviewRoundType = InterviewRoundType.other
    custom_round_name: str | None = Field(default=None, max_length=128)
    round_index: int | None = Field(
        default=None, ge=1, description="留空则排在现有轮次之后"
    )
    scheduled_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    location_type: InterviewLocationType = InterviewLocationType.unknown
    meeting_url: str | None = Field(default=None, max_length=1024)
    interviewer_name: str | None = Field(default=None, max_length=128)
    interviewer_role: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=4000)


class RoundUpdateRequest(BaseModel):
    """Partial edit. Outcome is deliberately absent - use /complete."""

    round_type: InterviewRoundType | None = None
    custom_round_name: str | None = Field(default=None, max_length=128)
    round_index: int | None = Field(default=None, ge=1)
    scheduled_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    location_type: InterviewLocationType | None = None
    meeting_url: str | None = Field(default=None, max_length=1024)
    interviewer_name: str | None = Field(default=None, max_length=128)
    interviewer_role: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=4000)
    preparation: PreparationNotes | None = None
    #: Clearing a scheduled time needs to be distinguishable from "not edited".
    clear_scheduled_at: bool = False


class RoundCompleteRequest(BaseModel):
    """Record what happened. Requires an explicit confirmation."""

    outcome: InterviewOutcome = InterviewOutcome.pending
    confirmed: bool = Field(default=False, description="必须为 true —— 记录真实结果")
    completed_at: datetime | None = None
    feedback_text: str | None = Field(default=None, max_length=8000)
    notes: str | None = Field(default=None, max_length=4000)
    feedback_tags: list[FeedbackTag] = Field(default_factory=list, max_length=10)
    failure_reason: InterviewFailureReason | None = None
    preparation: PreparationNotes | None = None
    #: Only meaningful with outcome=failed. Records the employer's rejection
    #: through the existing workflow path - never implied.
    also_record_rejection: bool = False
    #: Set when re-recording an already-completed round. Without it, a second
    #: completion is refused rather than silently overwriting the first.
    correction: bool = False

    @model_validator(mode="after")
    def _check(self) -> "RoundCompleteRequest":
        if self.also_record_rejection and self.outcome is not InterviewOutcome.failed:
            raise ValueError("只有在结果为「未通过」时才能同时记录职位拒绝。")
        if self.failure_reason is not None and self.outcome is not InterviewOutcome.failed:
            raise ValueError("失败原因只适用于「未通过」的轮次。")
        return self


class RoundCancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class ProcessCreateRequest(BaseModel):
    """Open a process for one application cycle.

    ``applied_event_id`` names the cycle explicitly; omitting it uses the job's
    current effective cycle, which is what the UI does.
    """

    applied_event_id: int | None = None
    notes: str | None = Field(default=None, max_length=4000)
    #: Optionally open with a first round in one call.
    first_round: RoundCreateRequest | None = None


class ProcessUpdateRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=4000)


class ProcessWithdrawRequest(BaseModel):
    """The candidate stopped. Never counted as an employer rejection."""

    reason: WithdrawReason = WithdrawReason.other
    confirmed: bool = Field(default=False, description="必须为 true")
    notes: str | None = Field(default=None, max_length=4000)


# --------------------------------------------------------------------------
# responses
# --------------------------------------------------------------------------


class RoundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    interview_process_id: int
    round_index: int
    round_type: InterviewRoundType
    round_type_label: str = ""
    custom_round_name: str | None = None
    display_name: str = ""

    scheduled_at: datetime | None = None
    duration_minutes: int | None = None
    completed_at: datetime | None = None

    status: InterviewRoundStatus
    outcome: InterviewOutcome
    failure_reason: InterviewFailureReason | None = None

    interviewer_name: str | None = None
    interviewer_role: str | None = None
    location_type: InterviewLocationType
    #: Round detail only. Analytics never carries this.
    meeting_url: str | None = None

    feedback_text: str | None = None
    notes: str | None = None
    feedback_tags: list[str] = Field(default_factory=list)
    preparation: PreparationNotes = Field(default_factory=PreparationNotes)

    created_at: datetime
    updated_at: datetime


class ProcessOut(BaseModel):
    """One interview process with its rounds and its cycle context."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    applied_event_id: int
    status: InterviewProcessStatus
    ended_after_round_type: InterviewRoundType | None = None
    failure_reason: InterviewFailureReason | None = None
    withdraw_reason: WithdrawReason | None = None
    closed_at: datetime | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    rounds: list[RoundOut] = Field(default_factory=list)

    # --- context, resolved from the application cycle ------------------
    company: str = ""
    title: str = ""
    city: str | None = None
    applied_at: datetime | None = None
    #: The resume used for THIS cycle - frozen at application time (v0.7).
    resume_id: int | None = None
    resume_label: str | None = None
    resume_archived: bool = False

    #: The round the user is waiting on, if any.
    current_round: RoundOut | None = None
    next_scheduled_at: datetime | None = None
    rounds_passed: int = 0


class ProcessListResponse(BaseModel):
    items: list[ProcessOut] = Field(default_factory=list)
    total: int = 0


class UpcomingGroup(BaseModel):
    """Scheduled rounds bucketed by local (Asia/Tokyo) calendar day."""

    key: str
    label: str
    items: list[ProcessOut] = Field(default_factory=list)


class InterviewBoardResponse(BaseModel):
    """The 面试 page: upcoming, awaiting a result, and finished."""

    timezone: str = "Asia/Tokyo"
    generated_at: datetime
    upcoming: list[UpcomingGroup] = Field(default_factory=list)
    awaiting_result: list[ProcessOut] = Field(default_factory=list)
    completed: list[ProcessOut] = Field(default_factory=list)
    ongoing_without_schedule: list[ProcessOut] = Field(default_factory=list)
    #: Old `interview` ApplicationEvents with no process. Never auto-classified.
    legacy_milestones: list["LegacyInterviewMilestone"] = Field(default_factory=list)


class LegacyInterviewMilestone(BaseModel):
    """A pre-v0.8 interview event. Round detail is unknown, not guessed."""

    job_id: int
    event_id: int
    company: str = ""
    title: str = ""
    occurred_at: datetime
    note: str | None = None
    #: Whatever v0.4 stored, e.g. "一面". Not mapped onto a round type.
    legacy_round_label: str | None = None


class UpcomingInterviewItem(BaseModel):
    """Compact dashboard row. No meeting URL - it is not needed to glance."""

    process_id: int
    job_id: int
    round_id: int
    company: str = ""
    title: str = ""
    round_label: str = ""
    scheduled_at: datetime
    location_type: InterviewLocationType
    day_key: str = ""


class UpcomingInterviewsResponse(BaseModel):
    timezone: str = "Asia/Tokyo"
    items: list[UpcomingInterviewItem] = Field(default_factory=list)
    total: int = 0
    message: str = ""


class ProcessActionResponse(BaseModel):
    process: ProcessOut
    message: str = ""
    #: Set when the action also moved Job.status through application_workflow.
    job_status: str | None = None


class RoundActionResponse(BaseModel):
    process: ProcessOut
    round: RoundOut
    message: str = ""
    job_status: str | None = None


# --------------------------------------------------------------------------
# recruiter -> interview handoff (v0.5 integration)
# --------------------------------------------------------------------------


class InterviewSuggestion(BaseModel):
    """A *suggestion* extracted from a recruiter message.

    Producing one creates nothing. The human confirms, and only then does a
    round exist - AI detection never mutates workflow state.
    """

    conversation_id: int
    message_id: int
    job_id: int | None = None
    scheduled_at: datetime | None = None
    raw_text: str = ""
    is_ambiguous: bool = False
    suggested_round_type: InterviewRoundType | None = None
    can_add: bool = False
    reason: str = ""


class SuggestionListResponse(BaseModel):
    items: list[InterviewSuggestion] = Field(default_factory=list)
    total: int = 0
    message: str = ""


InterviewBoardResponse.model_rebuild()
