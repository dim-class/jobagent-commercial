"""Console attention subview schemas (M2).

Every section here is built from an existing domain schema - nothing is
invented for this view. See docs/orchestration/ROADMAP.md - M2.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

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
