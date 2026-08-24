"""Recruiter conversation schemas (v0.5).

The typed contract for :class:`RecruiterConversationAgent`, plus the request /
response shapes for the HR沟通 pages.

Two boundaries encoded here:

* a suggested reply is a **draft**. Copying it changes nothing; only an
  explicit confirmation records that the human sent it.
* an analysis is an **interpretation**. It never writes ``Job.status``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ConversationStatus, MessageDirection, RecruiterSource
from app.schemas.application import ApplicationEventOut


class Sentiment(str, Enum):
    positive = "positive"
    neutral = "neutral"
    negative = "negative"
    unclear = "unclear"


class ConversationStage(str, Enum):
    initial_contact = "initial_contact"
    screening = "screening"
    interview_scheduling = "interview_scheduling"
    document_request = "document_request"
    salary_discussion = "salary_discussion"
    offer_discussion = "offer_discussion"
    rejection = "rejection"
    follow_up = "follow_up"
    other = "other"


class Urgency(str, Enum):
    low = "low"
    normal = "normal"
    high = "high"


class ReplyLanguage(str, Enum):
    """``auto`` means "match the recruiter"; the model reports what it used."""

    zh = "zh"
    ja = "ja"
    en = "en"
    other = "other"


class LanguagePreference(str, Enum):
    auto = "auto"
    zh = "zh"
    ja = "ja"
    en = "en"


class RequestType(str, Enum):
    """What the recruiter is asking for."""

    interview_availability = "interview_availability"
    expected_salary = "expected_salary"
    current_salary = "current_salary"
    start_date = "start_date"
    notice_period = "notice_period"
    resume = "resume"
    resume_update = "resume_update"
    work_location = "work_location"
    remote_preference = "remote_preference"
    visa_status = "visa_status"
    visa_expiry = "visa_expiry"
    sponsorship = "sponsorship"
    language_skill = "language_skill"
    technical_experience = "technical_experience"
    years_of_experience = "years_of_experience"
    certification = "certification"
    motivation = "motivation"
    reason_for_change = "reason_for_change"
    other = "other"


class InputMode(str, Enum):
    auto = "auto"
    single_message = "single_message"
    conversation = "conversation"


class CloseReason(str, Enum):
    interviewing = "已进入面试"
    rejected = "已拒绝"
    position_closed = "职位关闭"
    no_follow_up = "无后续"
    other = "其他"


class FollowUpPreset(str, Enum):
    tomorrow = "tomorrow"
    in_3_days = "in_3_days"
    in_1_week = "in_1_week"
    custom = "custom"


# --------------------------------------------------------------------------
# the agent's typed output
# --------------------------------------------------------------------------


class RecruiterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: RequestType
    summary: str = Field(description="用一句话说明 HR 在问什么")
    required: bool = Field(default=True, description="是否必须回复")
    answer_found_in_profile: bool = Field(
        default=False, description="简历/求职策略里是否已有可直接引用的答案"
    )
    suggested_answer: str | None = Field(
        default=None,
        description="仅当资料中确实存在时才填写；不得编造。找不到时返回 null",
    )


class ActionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="用户需要做的一件事")
    blocking: bool = Field(default=False, description="缺少它就无法回复")


class DateTimeMention(BaseModel):
    """A time the recruiter mentioned. Ambiguity is reported, never guessed."""

    model_config = ConfigDict(extra="forbid")

    raw_text: str = Field(description="原文片段，例如「下周三下午」")
    normalized_at: datetime | None = Field(default=None, description="能确定到具体时刻时填写")
    normalized_date: str | None = Field(default=None, description="YYYY-MM-DD，能确定日期时填写")
    is_ambiguous: bool = Field(default=True, description="无法确定就必须为 true")
    context: str = Field(default="", description="这个时间用来做什么，例如「面试」")


class RecruiterMessageAnalysisResult(BaseModel):
    """Structured reading of one recruiter message."""

    model_config = ConfigDict(extra="forbid")

    sentiment: Sentiment = Sentiment.unclear
    conversation_stage: ConversationStage = ConversationStage.other
    needs_reply: bool = True
    urgency: Urgency = Urgency.normal

    summary: str = Field(default="", description="1-2 句中文摘要")
    recruiter_requests: list[RecruiterRequest] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    dates_times: list[DateTimeMention] = Field(default_factory=list)
    missing_information: list[str] = Field(
        default_factory=list, description="回复所需但资料中没有的信息"
    )
    risk_flags: list[str] = Field(default_factory=list)

    suggested_reply: str | None = Field(default=None, description="草稿，用户可编辑")
    suggested_reply_language: ReplyLanguage = ReplyLanguage.zh
    confidence: int = Field(default=0, description="0-100")


# --------------------------------------------------------------------------
# API shapes
# --------------------------------------------------------------------------


class RecruiterMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    direction: MessageDirection
    raw_text: str
    source_message_time_text: str | None = None
    captured_at: datetime
    created_at: datetime
    analysis: RecruiterMessageAnalysisResult | None = None
    analyzed_at: datetime | None = None
    analysis_model: str | None = None


class ConversationSummaryOut(BaseModel):
    """List-row view. The analysis shown is the latest recruiter message's."""

    id: int
    job_id: int | None = None
    job_title: str | None = None
    job_company: str | None = None
    source: RecruiterSource
    recruiter_name: str | None = None
    company: str | None = None
    title: str | None = None
    status: ConversationStatus
    close_reason: str | None = None
    last_message_at: datetime | None = None
    next_action_at: datetime | None = None
    created_at: datetime

    message_count: int = 0
    latest_preview: str = ""
    latest_direction: MessageDirection | None = None
    sentiment: Sentiment | None = None
    stage: ConversationStage | None = None
    needs_reply: bool = False
    action_item_count: int = 0
    summary: str = ""


class ConversationDetailOut(ConversationSummaryOut):
    messages: list[RecruiterMessageOut] = Field(default_factory=list)
    latest_analysis: RecruiterMessageAnalysisResult | None = None
    job_events: list[ApplicationEventOut] = Field(default_factory=list)
    #: True when the newest message came from the recruiter and the linked job
    #: is not yet marked replied. The UI offers a button; nothing is automatic.
    suggests_recruiter_reply_event: bool = False


class InboxSummary(BaseModel):
    needs_reply: int = 0
    received_today: int = 0
    replied_today: int = 0
    interview_scheduling: int = 0
    follow_up_due: int = 0
    waiting_recruiter: int = 0
    closed: int = 0
    timezone: str = "Asia/Tokyo"


class InboxResponse(BaseModel):
    items: list[ConversationSummaryOut]
    total: int
    summary: InboxSummary


class ConversationCreate(BaseModel):
    job_id: int | None = None
    source: RecruiterSource = RecruiterSource.other
    recruiter_name: str | None = Field(default=None, max_length=128)
    company: str | None = Field(default=None, max_length=256)
    title: str | None = Field(default=None, max_length=256)


class ConversationUpdate(BaseModel):
    job_id: int | None = None
    source: RecruiterSource | None = None
    recruiter_name: str | None = Field(default=None, max_length=128)
    company: str | None = Field(default=None, max_length=256)
    title: str | None = Field(default=None, max_length=256)
    status: ConversationStatus | None = None


class MessageTextIn(BaseModel):
    text: str = Field(min_length=1, description="粘贴的 HR 消息或整段对话")
    direction: MessageDirection = MessageDirection.recruiter
    input_mode: InputMode = InputMode.auto
    source_message_time_text: str | None = Field(default=None, max_length=128)
    analyze: bool = Field(default=True, description="保存后立即分析（一次 AI 调用）")
    language: LanguagePreference = LanguagePreference.auto


class MessageCreatedOut(BaseModel):
    message: RecruiterMessageOut
    duplicate: bool = False
    parsed_message_count: int = 1
    warnings: list[str] = Field(default_factory=list)
    ai_used: bool = False
    ai_available: bool = True
    ai_error: str | None = None
    conversation: ConversationDetailOut


class AnalyzeRequest(BaseModel):
    force: bool = False
    language: LanguagePreference = LanguagePreference.auto


class AnalysisOut(BaseModel):
    message_id: int
    result: RecruiterMessageAnalysisResult
    model: str
    prompt_version: str
    cached: bool = False
    created_at: datetime
    deterministic_signals: dict = Field(default_factory=dict)


class MarkSentRequest(BaseModel):
    """Records that the human sent a reply *themselves*, outside JobAgent."""

    confirmed: bool = Field(
        default=False, description="必须为 true —— 表示用户已在外部渠道发送"
    )
    final_text: str = Field(min_length=1, description="用户最终发出的文本（可能已编辑）")
    record_job_event: bool = Field(
        default=True, description="若已关联岗位，追加一条 candidate_reply 事件"
    )


class FollowUpRequest(BaseModel):
    preset: FollowUpPreset = FollowUpPreset.tomorrow
    next_action_at: datetime | None = None
    note: str | None = Field(default=None, max_length=500)


class CloseRequest(BaseModel):
    reason: CloseReason = CloseReason.no_follow_up
    note: str | None = Field(default=None, max_length=500)
    #: Off by default. Rejecting a *conversation* is not rejecting a *job*;
    #: the job status only moves through application_workflow.
    also_record_job_rejection: bool = False


class ConversationActionOut(BaseModel):
    conversation: ConversationDetailOut
    message: str = ""
