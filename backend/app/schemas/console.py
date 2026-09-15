"""Console attention subview schemas (M2).

Every section here is built from an existing domain schema - nothing is
invented for this view. See docs/orchestration/ROADMAP.md - M2.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.application import ApplicationProposal, QueueSummary
from app.schemas.interview import UpcomingInterviewsResponse
from app.schemas.job import JobListItem
from app.schemas.offer import OfferOut
from app.schemas.recruiter import ConversationSummaryOut, InboxSummary


class AnalysisPendingSection(BaseModel):
    #: Jobs with no ``JobAnalysis`` row yet, real count - never estimated.
    count: int
    items: list[JobListItem]


class QueueAttentionSection(BaseModel):
    summary: QueueSummary
    items: list[ApplicationProposal]


class RecruiterAttentionSection(BaseModel):
    summary: InboxSummary
    #: Conversations needing a reply or a due follow-up, newest first.
    items: list[ConversationSummaryOut]


class OfferAttentionSection(BaseModel):
    pending_count: int
    negotiating_count: int
    items: list[OfferOut]


class ConsoleAttentionOut(BaseModel):
    """Read-only cross-domain summary. No endpoint composed here writes anything."""

    generated_at: datetime
    analysis_pending: AnalysisPendingSection
    queue: QueueAttentionSection
    recruiter: RecruiterAttentionSection
    interviews: UpcomingInterviewsResponse
    offers: OfferAttentionSection


class ReadinessCheck(BaseModel):
    """One prerequisite, whether it is met, and what to do when it is not."""

    key: str
    label: str
    ok: bool
    detail: str
    #: Empty for a check that is informational rather than a blocker.
    fix: str = ""
    #: Whether an unmet check stops the user from searching at all. The API
    #: key is not: collection works without it, only AI analysis needs it, and
    #: a red banner over a working search would be a lie.
    blocking: bool = True


class ReadinessOut(BaseModel):
    """Everything a fresh installation needs, in the order to fix it."""

    ready: bool
    version: str
    checks: list[ReadinessCheck] = Field(default_factory=list)


class AiSettingsOut(BaseModel):
    """What the UI may know about the AI credentials - never the key itself."""

    configured: bool
    #: The last four characters, so two keys can be told apart when replacing
    #: one. Never enough to use.
    hint: str = ""
    base_url: str = ""
    model_fast: str
    model_smart: str
    #: The file `Settings` reads, which is the file a save writes.
    env_path: str
    #: Names set in the process environment, which outrank the file - a value
    #: saved for one of these is written and then ignored.
    overridden: list[str] = Field(default_factory=list)


class AiSettingsIn(BaseModel):
    """Only the fields the user may set. Absent means "leave unchanged"."""

    api_key: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=200)
    model_fast: str | None = Field(default=None, max_length=64)
    model_smart: str | None = Field(default=None, max_length=64)
