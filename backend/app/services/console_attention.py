"""Cross-domain attention subview (M2).

Composes existing read paths - the application queue, the recruiter inbox,
the interview board, the offer board, and job analysis state - into one
read-only summary for the console. Every number and item here is produced by
calling the same functions the dedicated pages already use; nothing is
recomputed or re-scored here. This module never calls an AI model, never
writes ``Job.status`` or any other row, and never performs a site action.
See docs/orchestration/ROADMAP.md - M2.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.routes.interviews import upcoming_interviews
from app.api.routes.jobs import _to_list_item
from app.api.routes.offers import offer_board
from app.api.routes.recruiter import list_conversations
from app.core.config import get_settings
from app.models import ConversationStatus, Job
from app.schemas.application import ProposalState
from app.schemas.console import (
    AnalysisPendingSection,
    ConsoleAttentionOut,
    OfferAttentionSection,
    QueueAttentionSection,
    RecruiterAttentionSection,
)
from app.services import application_queue

#: How many sample items each section shows. The counts above each list are
#: always the real total, never estimated from this cap.
ITEM_LIMIT = 5

#: Conversation states that need a human action, same statuses the recruiter
#: inbox itself sorts to the top.
_RECRUITER_PRIORITY_STATUSES = {ConversationStatus.needs_reply, ConversationStatus.follow_up_due}


def _analysis_pending(db: Session, *, limit: int) -> AnalysisPendingSection:
    """Jobs with no analysis yet, reusing the jobs list serializer verbatim."""
    jobs = list(db.scalars(select(Job).options(selectinload(Job.analyses))).unique())
    pending = [job for job in jobs if not job.analyses]
    pending.sort(key=lambda job: job.created_at, reverse=True)
    return AnalysisPendingSection(
        count=len(pending), items=[_to_list_item(job) for job in pending[:limit]]
    )


def _queue_attention(db: Session, *, limit: int) -> QueueAttentionSection:
    settings = get_settings()
    proposals = application_queue.all_proposals(db)
    summary = application_queue.build_summary(
        db,
        proposals,
        daily_target=settings.daily_application_target,
        timezone_name=settings.report_timezone,
    )
    due = [
        p
        for p in proposals
        if p.proposal_state in (ProposalState.ready, ProposalState.pending)
    ]
    ordered = application_queue.sort_proposals(due, "recommended")
    return QueueAttentionSection(summary=summary, items=ordered[:limit])


def _recruiter_attention(db: Session, *, limit: int) -> RecruiterAttentionSection:
    """Reuses the inbox route function directly - identical filtering/sorting."""
    response = list_conversations(db=db, status=None, job_id=None, keyword=None)
    priority = [i for i in response.items if i.status in _RECRUITER_PRIORITY_STATUSES]
    return RecruiterAttentionSection(summary=response.summary, items=priority[:limit])


def _offer_attention(db: Session, *, limit: int) -> OfferAttentionSection:
    board = offer_board(db=db)
    items = (board.pending + board.negotiating)[:limit]
    return OfferAttentionSection(
        pending_count=len(board.pending),
        negotiating_count=len(board.negotiating),
        items=items,
    )


def build_attention(db: Session, *, item_limit: int = ITEM_LIMIT) -> ConsoleAttentionOut:
    return ConsoleAttentionOut(
        generated_at=datetime.now(timezone.utc),
        analysis_pending=_analysis_pending(db, limit=item_limit),
        queue=_queue_attention(db, limit=item_limit),
        recruiter=_recruiter_attention(db, limit=item_limit),
        interviews=upcoming_interviews(db=db, limit=item_limit),
        offers=_offer_attention(db, limit=item_limit),
    )
