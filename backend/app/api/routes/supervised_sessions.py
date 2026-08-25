"""M4a/M4b/M4c supervised-session endpoints - bounded navigation, scroll and pagination.

    POST /api/extension/sessions              start a session (one at a time)
    GET  /api/extension/sessions/active        the currently running session, or null
    GET  /api/extension/sessions/{id}          one session + its audit trail
    POST /api/extension/sessions/{id}/stop     stop it (human click, or fail-closed)
    POST /api/extension/sessions/{id}/navigate record one already-performed step
                                                (results/detail/scroll)

Loopback only, the same guard the rest of ``/api/extension`` uses. No route
here navigates, clicks, scrolls, paginates, searches, applies, or messages
itself - the extension's own content script performs the one real scroll or
click, and only afterward asks this module to account for it against the
approved caps. See CLAUDE.md's "Chrome extension - M4 supervised navigation
policy" and docs/orchestration/ROADMAP.md M4b/M4c.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from app.api.routes.extension import require_loopback
from app.db.session import get_db
from app.models import SupervisedSession
from app.schemas.supervised_session import (
    ApprovedCriteriaSnapshot,
    NavigateConfirmRequest,
    NavigatePrepareRequest,
    SessionCreate,
    SessionEventOut,
    SessionOut,
    SessionStopRequest,
)
from app.services import supervised_sessions

router = APIRouter(prefix="/api/extension/sessions", tags=["supervised-sessions"])


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _session_out(session: SupervisedSession) -> SessionOut:
    return SessionOut(
        id=session.id,
        task_id=session.task_id,
        status=session.status,
        page_cap=session.page_cap,
        candidate_cap=session.candidate_cap,
        scroll_cap=session.scroll_cap,
        tab_origin=session.tab_origin,
        approved_criteria=ApprovedCriteriaSnapshot(**session.approved_criteria_json),
        pages_visited=session.pages_visited,
        candidates_extracted=session.candidates_extracted,
        scrolls_used=session.scrolls_used,
        started_at=_as_utc(session.started_at),
        stopped_at=_as_utc(session.stopped_at) if session.stopped_at else None,
        stop_reason=session.stop_reason,
        events=[
            SessionEventOut(
                id=e.id,
                event_type=e.event_type,
                reason=e.reason,
                created_at=_as_utc(e.created_at),
            )
            for e in session.events
        ],
    )


@router.post("", response_model=SessionOut)
def start_session(
    request: Request, payload: SessionCreate = Body(...), db: Session = Depends(get_db)
) -> SessionOut:
    """Start a bounded session. Rejects a missing task, an above-ceiling
    cap, a wrong tab origin, or a second concurrent session - and writes
    nothing when it does."""
    require_loopback(request)
    session = supervised_sessions.create_session(
        db,
        task_id=payload.task_id,
        page_cap=payload.page_cap,
        candidate_cap=payload.candidate_cap,
        scroll_cap=payload.scroll_cap,
        tab_origin=payload.tab_origin,
    )
    return _session_out(session)


@router.get("/active", response_model=SessionOut | None)
def active_session(request: Request, db: Session = Depends(get_db)) -> SessionOut | None:
    """The one running session, or null. Lets the popup recover its state
    after a service-worker restart without trusting local storage alone."""
    require_loopback(request)
    session = supervised_sessions.get_active_session(db)
    return _session_out(session) if session else None


@router.get("/{session_id}", response_model=SessionOut)
def get_session(request: Request, session_id: int, db: Session = Depends(get_db)) -> SessionOut:
    require_loopback(request)
    return _session_out(supervised_sessions.get_session(db, session_id))


@router.post("/{session_id}/stop", response_model=SessionOut)
def stop_session(
    request: Request,
    session_id: int,
    payload: SessionStopRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> SessionOut:
    """Stop a session - a human click (``user_stop``) or a fail-closed
    ``stale_tab`` detection. Idempotent on an already-stopped session."""
    require_loopback(request)
    session = supervised_sessions.stop_session(
        db, session_id, reason=(payload or SessionStopRequest()).reason
    )
    return _session_out(session)


@router.post("/{session_id}/navigate/prepare", response_model=SessionOut)
def navigate_prepare(
    request: Request,
    session_id: int,
    payload: NavigatePrepareRequest = Body(...),
    db: Session = Depends(get_db),
) -> SessionOut:
    """Authorize exactly one upcoming click. Writes nothing - the extension
    may only click after this succeeds."""
    require_loopback(request)
    session = supervised_sessions.navigate_prepare(
        db, session_id, target=payload.target, page_url=payload.page_url
    )
    return _session_out(session)


@router.post("/{session_id}/navigate/confirm", response_model=SessionOut)
def navigate_confirm(
    request: Request,
    session_id: int,
    payload: NavigateConfirmRequest = Body(...),
    db: Session = Depends(get_db),
) -> SessionOut:
    """Account for what the click actually did. A failed click is audited
    but never counted against any cap."""
    require_loopback(request)
    session = supervised_sessions.navigate_confirm(
        db,
        session_id,
        target=payload.target,
        page_url=payload.page_url,
        outcome=payload.outcome,
        error=payload.error,
    )
    return _session_out(session)
