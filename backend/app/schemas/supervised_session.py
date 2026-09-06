"""M4a/M4b/M4c supervised-session schemas - bounded navigation, scroll and
pagination.

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
#: "identity_mismatch": M4b's captured detail pane did not match the exact
#: candidate card that was opened - never guessed, never sent.
#:
#: M4c (scroll/pagination, explicitly authorized) adds:
#: "scroll_failed": the results container did not resolve to exactly one
#: scrollable element (ambiguous or missing) and no scroll was performed.
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
    "identity_mismatch",
    "scroll_failed",
]


class SessionCreate(BaseModel):
    """The approved caps for one session.

    These bounds must stay equal to the ceilings in
    ``services/supervised_sessions.py`` - they are repeated here, rather than
    imported, only because that service imports this module. A mismatch is not
    a harmless duplicate: Pydantic rejects the request before the service is
    ever reached, and the run fails with the generic 「请求参数不合法」 that says
    nothing about which number was wrong. That is exactly what happened on
    2026-09-05 when the scroll ceiling rose to 30 and this bound stayed at 5,
    so `test_schema_bounds_match_the_service_ceilings` now pins the pair.
    """

    task_id: int
    page_cap: int = Field(ge=1, le=3)
    candidate_cap: int = Field(ge=1, le=60)
    scroll_cap: int = Field(ge=0, le=30)
    #: Must equal exactly "https://www.zhipin.com" - re-checked server-side,
    #: never trusted just because the client sent it.
    tab_origin: str = Field(max_length=64)


class SessionStopRequest(BaseModel):
    reason: StopReason = "user_stop"


#: "results": one results-page visit - the session's starting page (counted
#: once at creation, never via this endpoint) plus every confirmed
#: pagination click after it - bounded by ``page_cap``.
#: "detail": the extension opened one candidate's detail page by clicking
#: its own already-rendered card link - bounded by ``candidate_cap``.
#: "scroll": one bounded scroll step on the current results page - bounded
#: by ``scroll_cap``, reset to 0 on every confirmed "results" navigation
#: (M4c, CLAUDE.md "Chrome extension - M4 supervised navigation policy",
#: explicitly authorized).
NavigateTarget = Literal["results", "detail", "scroll"]


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
    #: `pages_visited` starts at 1 (the session's starting page, counted
    #: exactly once at creation - see `create_session`) and, like
    #: `scrolls_used` and `candidates_extracted`, is advanced only by a
    #: confirmed POST /navigate call afterward - never guessed, never
    #: advanced by a read-only check. `scrolls_used` resets to 0 on every
    #: confirmed "results" navigation (M4c, explicitly authorized).
    pages_visited: int
    candidates_extracted: int
    scrolls_used: int
    started_at: datetime
    stopped_at: datetime | None = None
    stop_reason: str | None = None
    events: list[SessionEventOut] = Field(default_factory=list)
