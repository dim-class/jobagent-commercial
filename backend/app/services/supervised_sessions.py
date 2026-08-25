"""M4a/M4b/M4c supervised-session scaffolding and bounded-navigation accounting.

Every function here only creates/stops a ``SupervisedSession`` row, or
records that one already-performed, human-initiated navigation step
happened, appending a ``SupervisedSessionEvent`` each time. Nothing in this
module navigates a tab, reads a page, scrolls, paginates, calls a model, or
touches ``Job``/``Job.status`` - the extension performs the one real
scroll/click (via its own content script, never Playwright/CDP), and only
*afterward* tells this service what it did so the caps can be enforced and
the trail audited. See CLAUDE.md's "Chrome extension - M4 supervised
navigation policy" and docs/orchestration/ROADMAP.md M4b/M4c.

Three navigation targets, three independent budgets: ``"results"`` (a
results-page visit, bounded by ``page_cap`` and counting the session's
starting page exactly once - see ``create_session``), ``"scroll"`` (one
bounded scroll step on the current results page, bounded by ``scroll_cap``
and reset to 0 on every confirmed ``"results"`` navigation - never
cumulative across the whole session), and ``"detail"`` (one candidate card
opened, bounded by ``candidate_cap``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFoundError, ValidationError
from app.models import (
    JobSearchTask,
    SupervisedSession,
    SupervisedSessionEvent,
    SupervisedSessionEventType,
    SupervisedSessionStatus,
)
from app.services import task_console

#: Immutable POC ceilings (CLAUDE.md "1a. Immutable POC ceilings"). A
#: human's approved caps may be lower, never higher - these are never read
#: from a config file or a client-supplied value, only hardcoded here.
MAX_CONCURRENT_SESSIONS = 1
MAX_PAGE_CAP = 3
MAX_CANDIDATE_CAP = 20
MAX_SCROLL_CAP = 5

#: The exact host `extension/manifest.json`'s `content_scripts.matches`
#: declares. Fail closed on anything else - no subdomain, no other scheme,
#: no path or query (a session never stores a URL that could carry a token).
REQUIRED_TAB_ORIGIN = "https://www.zhipin.com"


def _origin_of(url: str) -> str | None:
    """``scheme://host`` only, or ``None`` if unparseable - never guessed."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _as_utc(moment: datetime) -> datetime:
    """SQLite hands back naive datetimes - see ``application_cycles._as_utc``."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _snapshot_criteria(task: JobSearchTask) -> dict:
    """An immutable copy of the task's criteria, read from the task row
    itself - never from client input, and never touched again after this
    call. Search criteria only: no URL, no token, nothing session-related."""
    return {
        "task_name": task.name,
        "keywords": task.keywords,
        "city": task.city,
        "experience_text": task.experience_text,
        "education_text": task.education_text,
        "salary_min": task.salary_min,
        "salary_max": task.salary_max,
        "exclusions": task.exclusions_json,
        "resume_id": task.resume_id,
        "max_candidates": task.max_candidates,
        "min_score": task.min_score,
        "mode": task.mode.value,
    }


def create_session(
    db: Session,
    *,
    task_id: int,
    page_cap: int,
    candidate_cap: int,
    scroll_cap: int,
    tab_origin: str,
) -> SupervisedSession:
    """Start a session. Rejects (and writes nothing) on any of:

    - a missing task,
    - a cap above its immutable ceiling,
    - a tab origin that is not exactly ``REQUIRED_TAB_ORIGIN``,
    - another session already running (MAX_CONCURRENT_SESSIONS == 1).
    """
    task = task_console.get_task(db, task_id)  # 404s if the task itself is missing

    if tab_origin != REQUIRED_TAB_ORIGIN:
        raise ValidationError(
            f"标签页来源必须是 {REQUIRED_TAB_ORIGIN}，已拒绝启动会话。",
            detail={"tab_origin": tab_origin, "required": REQUIRED_TAB_ORIGIN},
        )
    if not (1 <= page_cap <= MAX_PAGE_CAP):
        raise ValidationError(
            f"页数上限必须在 1-{MAX_PAGE_CAP} 之间。",
            detail={"field": "page_cap", "max": MAX_PAGE_CAP},
        )
    if not (1 <= candidate_cap <= MAX_CANDIDATE_CAP):
        raise ValidationError(
            f"候选人上限必须在 1-{MAX_CANDIDATE_CAP} 之间。",
            detail={"field": "candidate_cap", "max": MAX_CANDIDATE_CAP},
        )
    if not (0 <= scroll_cap <= MAX_SCROLL_CAP):
        raise ValidationError(
            f"每页滚动次数上限必须在 0-{MAX_SCROLL_CAP} 之间。",
            detail={"field": "scroll_cap", "max": MAX_SCROLL_CAP},
        )

    running = db.scalar(
        select(SupervisedSession).where(
            SupervisedSession.status == SupervisedSessionStatus.running
        )
    )
    if running is not None:
        raise ValidationError(
            "已有一个正在进行的会话，同一时间只能有一个会话，请先结束它。",
            detail={"running_session_id": running.id},
        )

    session = SupervisedSession(
        task_id=task_id,
        status=SupervisedSessionStatus.running,
        page_cap=page_cap,
        candidate_cap=candidate_cap,
        scroll_cap=scroll_cap,
        tab_origin=tab_origin,
        approved_criteria_json=_snapshot_criteria(task),
        # The human is already looking at the starting results page the
        # moment they approve a session - M4c (CLAUDE.md "Chrome extension -
        # M4 supervised navigation policy", explicitly authorized) counts it
        # exactly once, here, rather than waiting for a first pagination
        # click that may never come. `page_cap=1` therefore means "the
        # starting page only, no pagination" - not "one pagination click
        # allowed on top of it".
        pages_visited=1,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    db.add(
        SupervisedSessionEvent(
            session_id=session.id, event_type=SupervisedSessionEventType.started
        )
    )
    db.commit()
    db.refresh(session)
    return session


def _check_navigable(
    session: SupervisedSession, *, target: str, page_url: str | None
) -> None:
    """Shared gate for both prepare and confirm - re-run in confirm too, so
    nothing is ever counted on the strength of an earlier check alone."""
    if session.status is not SupervisedSessionStatus.running:
        raise ValidationError(
            "会话未在进行中，无法导航。",
            detail={"session_id": session.id, "status": session.status.value},
        )
    if page_url is not None and _origin_of(page_url) != REQUIRED_TAB_ORIGIN:
        raise ValidationError(
            f"目标页面来源必须是 {REQUIRED_TAB_ORIGIN}，已拒绝导航。",
            detail={"page_url": page_url, "required": REQUIRED_TAB_ORIGIN},
        )
    if target == "results":
        if session.pages_visited >= session.page_cap:
            raise ValidationError(
                f"已达到本次会话的页数上限（{session.page_cap}）。",
                detail={"field": "page_cap", "cap": session.page_cap},
            )
    elif target == "detail":
        if session.candidates_extracted >= session.candidate_cap:
            raise ValidationError(
                f"已达到本次会话的候选人上限（{session.candidate_cap}）。",
                detail={"field": "candidate_cap", "cap": session.candidate_cap},
            )
    elif target == "scroll":
        # Per results page, reset to 0 on every confirmed pagination below -
        # never cumulative across the whole session, only within one page.
        if session.scrolls_used >= session.scroll_cap:
            raise ValidationError(
                f"已达到本页的滚动次数上限（{session.scroll_cap}）。",
                detail={"field": "scroll_cap", "cap": session.scroll_cap},
            )
    else:
        raise ValidationError("未知的导航目标。", detail={"target": target})


def navigate_prepare(
    db: Session, session_id: int, *, target: str, page_url: str | None
) -> SupervisedSession:
    """Authorize exactly one upcoming navigation attempt - session running,
    cap not yet reached, origin exact. Writes nothing and never increments a
    counter: the extension may only click *after* this succeeds, and the
    click's real outcome is what ``navigate_confirm`` accounts for. This is
    the fix for a click being falsely counted as a completed navigation -
    the counter no longer moves until the click is confirmed to have
    happened.
    """
    session = get_session(db, session_id)  # 404s if the session itself is missing
    _check_navigable(session, target=target, page_url=page_url)
    return session


def navigate_confirm(
    db: Session,
    session_id: int,
    *,
    target: str,
    page_url: str | None,
    outcome: str,
    error: str | None = None,
) -> SupervisedSession:
    """Account for what the extension's click actually did.

    ``outcome="success"``: re-validates (never trusts the earlier prepare
    call alone), then increments the matching counter and appends a
    ``navigated`` event. ``outcome="failed"``: appends a ``navigate_failed``
    audit event carrying the failure reason and never advances any counter
    - an ambiguous selector, an out-of-range index, or any other click
    failure must never look like a completed step.
    """
    session = get_session(db, session_id)

    if outcome not in ("success", "failed"):
        raise ValidationError("未知的导航结果。", detail={"outcome": outcome})

    if outcome == "failed":
        db.add(
            SupervisedSessionEvent(
                session_id=session.id,
                event_type=SupervisedSessionEventType.navigate_failed,
                reason=f"{target}:{error}"[:32] if error else target[:32],
            )
        )
        db.commit()
        db.refresh(session)
        return session

    _check_navigable(session, target=target, page_url=page_url)

    if target == "results":
        session.pages_visited += 1
        # A confirmed pagination lands on a fresh results page - the scroll
        # budget is per page, not per session, so it starts over here and
        # only here (never on a mere prepare, and never on a failed click).
        session.scrolls_used = 0
    elif target == "scroll":
        session.scrolls_used += 1
    else:
        session.candidates_extracted += 1

    db.add(
        SupervisedSessionEvent(
            session_id=session.id,
            event_type=SupervisedSessionEventType.navigated,
            reason=target,
        )
    )
    db.commit()
    db.refresh(session)
    return session


def get_session(db: Session, session_id: int) -> SupervisedSession:
    session = db.scalar(
        select(SupervisedSession)
        .options(selectinload(SupervisedSession.events))
        .where(SupervisedSession.id == session_id)
    )
    if session is None:
        raise NotFoundError(f"会话 {session_id} 不存在", detail={"session_id": session_id})
    return session


def get_active_session(db: Session) -> SupervisedSession | None:
    return db.scalar(
        select(SupervisedSession)
        .options(selectinload(SupervisedSession.events))
        .where(SupervisedSession.status == SupervisedSessionStatus.running)
    )


def stop_session(db: Session, session_id: int, *, reason: str = "user_stop") -> SupervisedSession:
    """Stop a session. Idempotent: stopping an already-stopped session is a
    no-op rather than a second event or an error."""
    session = get_session(db, session_id)
    if session.status is SupervisedSessionStatus.stopped:
        return session

    session.status = SupervisedSessionStatus.stopped
    session.stopped_at = datetime.now(timezone.utc)
    session.stop_reason = reason
    db.add(
        SupervisedSessionEvent(
            session_id=session.id,
            event_type=SupervisedSessionEventType.stopped,
            reason=reason,
        )
    )
    db.commit()
    db.refresh(session)
    return session
