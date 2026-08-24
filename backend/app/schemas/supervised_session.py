"""M4a/M4b supervised-session schemas - bounded navigation, no scroll/pagination.

Caps are bounded here by the immutable POC ceilings (page/candidate/scroll)
as a first line of defense; ``services/supervised_sessions.py`` re-checks
them independently so the ceiling holds even for a caller that bypasses this
schema. See CLAUDE.md's "Chrome extension - M4 supervised navigation
policy".
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import SupervisedSessionEventType, SupervisedSessionStatus, TaskMode

#: "user_stop": the human clicked the stop control.
#: "stale_tab": the popup reopened and could not confirm the approved tab is
#: still the active, matching one - or the backend had a running session
#: with no matching local pointer (e.g. after a browser restart). Either way
#: this fails closed: the session is stopped, never silently resumed.
#:
#: The rest are M4b policy hard stops (CLAUDE.md "Chrome extension - M4
#: supervised navigation policy" section 6) - each ends the whole session,
#: never just refuses one navigation step:
#: "verification": a CAPTCHA/login/security/risk-control interstitial
#: (also covers a rate-limit "too frequent" hint - the same detection).
#: "wrong_origin": the approved tab's current origin is no longer exactly
#: the required host.
#: "wrong_page": the current page is not the shape this step needed (e.g.
#: not a search-results page when selecting a candidate).
#: "no_candidates": nothing left on the page to open without scrolling or
#: paginating, which this milestone never does.
#: "loop_detected": the same candidate URL was about to be handled twice.
#: "prepare_denied": the backend refused to authorize the click (cap
#: reached, session already stopped, or a wrong origin caught server-side).
#: "click_failed": the authorized click did not resolve to exactly one
#: element (ambiguous or missing selector) and was confirmed failed.
#: "confirm_failed": the click happened but the backend could not be told,
#: so the session stops rather than risk an unaccounted-for navigation.
StopReason = Literal[
    "user_stop",
    "stale_tab",
    "verification",
    "wrong_origin",
    "wrong_page",
    "no_candidates",
    "loop_detected",
    "prepare_denied",
    "click_failed",
    "confirm_failed",
]


class SessionCreate(BaseModel):
    task_id: int
    page_cap: int = Field(ge=1, le=3)
    candidate_cap: int = Field(ge=1, le=20)
    scroll_cap: int = Field(ge=0, le=5)
    #: Must equal exactly "https://www.zhipin.com" - re-checked server-side,
    #: never trusted just because the client sent it.
    tab_origin: str = Field(max_length=64)


class SessionStopRequest(BaseModel):
    reason: StopReason = "user_stop"


#: "results": the human-approved tab navigated to (or is confirmed on) a
#: BOSS search-results page for this task - bounded by ``page_cap``.
#: "detail": the extension opened one candidate's detail page by clicking
#: its own already-rendered card link - bounded by ``candidate_cap``. M4b's
#: extension UI only ever sends "detail"; "results" exists for completeness
#: and for M4c (pagination) to reuse without a new endpoint.
NavigateTarget = Literal["results", "detail"]


class NavigatePrepareRequest(BaseModel):
    """Ask permission for exactly one upcoming click - before it happens."""

    target: NavigateTarget
    #: Canonical (query-stripped) URL of the page being navigated to, if
    #: known. Re-validated server-side against the exact required origin -
    #: never trusted just because the client sent it, and never a URL that
    #: could carry a session token (the caller strips the query first, the
    #: same discipline as ``extract.ts``'s ``cleanUrl`` /
    #: ``services.urls.canonical_url``).
    page_url: str | None = Field(default=None, max_length=512)


NavigateOutcome = Literal["success", "failed"]


class NavigateConfirmRequest(BaseModel):
    """Report what the already-attempted click actually did."""

    target: NavigateTarget
    page_url: str | None = Field(default=None, max_length=512)
    outcome: NavigateOutcome
    #: A short failure classification (e.g. "selector_ambiguous"), only
    #: meaningful when ``outcome="failed"``.
    error: str | None = Field(default=None, max_length=64)


class ApprovedCriteriaSnapshot(BaseModel):
    """Immutable copy of the task's criteria at the moment a session was
    approved - taken from the task row itself, never from client input, and
    never touched again after the session is created."""

    task_name: str
    keywords: str | None = None
    city: str | None = None
    experience_text: str | None = None
    education_text: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    exclusions: list[str] | None = None
    resume_id: int | None = None
    max_candidates: int | None = None
    min_score: int | None = None
    mode: TaskMode


class SessionEventOut(BaseModel):
    id: int
    event_type: SupervisedSessionEventType
    reason: str | None = None
    created_at: datetime


class SessionOut(BaseModel):
    id: int
    task_id: int
    status: SupervisedSessionStatus
    page_cap: int
    candidate_cap: int
    scroll_cap: int
    tab_origin: str
    approved_criteria: ApprovedCriteriaSnapshot
    #: Advanced only by a confirmed POST /navigate call - never guessed,
    #: never advanced by a read-only check. scrolls_used stays 0: M4b never
    #: scrolls (that is M4c, separately gated and unimplemented).
    pages_visited: int
    candidates_extracted: int
    scrolls_used: int
    started_at: datetime
    stopped_at: datetime | None = None
    stop_reason: str | None = None
    events: list[SessionEventOut] = Field(default_factory=list)
