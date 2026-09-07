"""M4e/M4f SearchPlan + bounded automatic runner schemas - loopback only,
same posture as ``schemas/extension.py`` and ``schemas/supervised_session.py``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SearchPlanGenerateRequest(BaseModel):
    #: Omitted -> ``services.search_plan.DEFAULT_CITIES`` /
    #: ``DEFAULT_KEYWORDS``. An unknown city rejects the whole call.
    cities: list[str] | None = None
    keywords: list[str] | None = None


class QuickSearchPrepareRequest(BaseModel):
    cities: list[str] = Field(min_length=1, max_length=4)
    #: How many NEW jobs each direction should collect before it stops early.
    #: Bounded by the opened-detail ceiling (60) rather than by a number of its
    #: own: asking for more than a run may open would be a target it could
    #: never reach, and anything at or below it adds no browsing at all - the
    #: ceiling is what limits the work either way.
    target_count: int = Field(ge=1, le=60, strict=True)
    #: BOSS search-page URLs the human built in their own browser. Only the
    #: whitelisted filter parameters are read; `city` and `query` are ignored,
    #: because those come from the cities chosen here and from the résumé
    #: ranking. Each URL becomes one segment, multiplying the plan.
    filter_urls: list[str] = Field(default_factory=list, max_length=8)
    #: BOSS's own salary codes, read off the filter menu on a page the human
    #: had open (never a table this project guessed). Each code becomes one
    #: segment, exactly like a pasted URL - this is the same mechanism with
    #: the copy-paste removed, not a second one.
    salary_codes: list[str] = Field(default_factory=list, max_length=8)
    #: One BOSS experience-band code, applied to every search rather than
    #: creating segments of its own - see `boss_search_filters.with_experience`.
    experience_code: str | None = Field(default=None, max_length=12)


class MatchApprovalRequest(BaseModel):
    confirmed: bool = Field(default=False, strict=True)
    cap: int = Field(ge=1, le=3, strict=True)
    fingerprint: str = Field(min_length=64, max_length=64)


class StartRunRequest(BaseModel):
    match_approval: MatchApprovalRequest | None = None


class MatchStepRequest(BaseModel):
    job_id: int = Field(gt=0)
    canonical_url: str = Field(max_length=512)


class AutoMatchReviewItem(BaseModel):
    job_id: int
    title: str
    company: str
    score: int | None
    verdict: str | None
    state: str
    cached: bool
    error: str | None
    bucket: str
    review_reasons: list[str]
    summary: str


class AutoMatchReview(BaseModel):
    task_id: int
    enabled: bool
    state: str | None = None
    cap: int = 0
    used: int = 0
    completed: int = 0
    failed: int = 0
    uncertain: int = 0
    resume_id: int | None = None
    model: str | None = None
    items: list[AutoMatchReviewItem]


class SearchPlanGenerateResponse(BaseModel):
    created: int
    skipped: int
    total: int


class SearchPlanTaskOut(BaseModel):
    id: int
    max_candidates: int | None = None
    name: str
    city: str | None = None
    city_id: str | None = None
    keywords: str | None = None
    early_career_policy: str
    #: Title fragments the career strategy excludes outright. Sent so the
    #: runner can skip such a card *before* opening it, exactly as it already
    #: does for an early-career title - an opened detail is the scarce thing.
    #: Measured on the library this was added against: 21 stored jobs had one
    #: of these in the title, and the AI judged 20 of them `skip` and none
    #: `apply`, so the opens they cost bought nothing.
    excluded_title_keywords: list[str] = Field(default_factory=list)
    #: The exact, deterministic, same-origin BOSS search URL for this task's
    #: (city_id, keyword) - ``services.boss_search_url.build_search_url`` -
    #: so the extension never re-implements city/keyword -> URL logic
    #: itself. ``None`` for a manual task (no city_id/keyword pair to build
    #: one from).
    search_url: str | None = None
    run_status: str | None = None
    run_started_at: datetime | None = None
    run_stopped_at: datetime | None = None
    observed_count: int
    new_count: int
    duplicate_count: int
    no_new_rounds: int
    last_error: str | None = None
    #: M4f runner observability - see ``services.search_task_runner.report_state``.
    current_url: str | None = None
    scroll_round: int
    visible_jobs: int
    imported_jobs: int
    current_candidate: str | None = None
    last_action: str | None = None
    paused_reason: str | None = None
    updated_at: datetime

    # --- Delta: exact public observability names requested alongside the
    # existing `id`/`run_status`/`*_count` fields above (kept for backward
    # compatibility) - never a second source of truth, just an alias
    # computed from the same task row. See CLAUDE.md M4f observability
    # delta item 3.
    task_id: int
    state: str | None = None
    keyword: str | None = None
    observed_jobs: int
    new_jobs: int
    duplicate_jobs: int


class SearchPlanTaskListResponse(BaseModel):
    items: list[SearchPlanTaskOut]


class DirectionChoiceOut(BaseModel):
    """One chosen direction and the evidence behind it."""

    keyword: str
    reasons: list[str] = Field(default_factory=list)
    jobs: int = 0
    recommended: int = 0
    recommend_rate: float | None = None
    has_evidence: bool = False
    #: 0-1. Read with `fit_source`: an AI judgement and a character-overlap
    #: guess are not the same claim and must not render identically.
    fit: float = 0.0
    fit_source: str = "text"
    #: True when the model proposed this keyword and the strategy file does not
    #: contain it. Used for this search only; the strategy is never auto-edited.
    suggested: bool = False


class DirectionAnalysisPlanOut(BaseModel):
    """What an AI direction analysis would cost right now. Reading is free."""

    resume_id: int | None = None
    resume_name: str = ""
    model: str
    candidates: list[str] = Field(default_factory=list)
    cached: bool = False
    #: 0 when cached, otherwise 1 - one call for the whole résumé, never one
    #: per direction.
    pending_calls: int = 0
    openai_configured: bool = True
    summary: str = ""
    directions: list[DirectionChoiceOut] = Field(default_factory=list)


class DirectionAnalysisRunRequest(BaseModel):
    """Spending money needs an explicit confirmation, as everywhere else."""

    confirmed: bool = False
    force: bool = False


class QuickSearchPrepareResponse(BaseModel):
    tasks: list[SearchPlanTaskOut]
    active_resume_name: str
    keyword_source: str = "career_strategy"
    #: Why these directions, in the order they were chosen. The user asked to
    #: search directly and be told afterwards, so this is an explanation, not a
    #: second confirmation step.
    directions: list[DirectionChoiceOut] = Field(default_factory=list)
    direction_notes: list[str] = Field(default_factory=list)
    #: True when too few directions have real outcome history. The UI may offer
    #: a paid AI pass; nothing here ever makes one.
    needs_more_evidence: bool = False


class SearchPlanOptionsResponse(BaseModel):
    """Public, non-secret capabilities for the local search setup UI."""

    supported_cities: list[str]
    max_selected_cities: int
    max_batch_tasks: int
    #: So the console can state, before a run, how many search units this plan
    #: will actually create. A backend that was not restarted after this number
    #: changed used to be invisible: the page said 16 while the process still
    #: meant 8.
    max_directions: int


class FailRunRequest(BaseModel):
    error: str = Field(max_length=256)


class PauseRunRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=64)


class RecordRoundRequest(BaseModel):
    observed: int = Field(ge=0)
    new: int = Field(ge=0)
    duplicate: int = Field(ge=0)
    no_new_round_threshold: int = Field(default=3, ge=1)


class ReportStateRequest(BaseModel):
    #: Every field optional and independently applied - see
    #: ``services.search_task_runner.report_state``.
    current_url: str | None = Field(default=None, max_length=512)
    scroll_round: int | None = Field(default=None, ge=0)
    visible_jobs: int | None = Field(default=None, ge=0)
    imported_jobs: int | None = Field(default=None, ge=0)
    current_candidate: str | None = Field(default=None, max_length=256)
    last_action: str | None = Field(default=None, max_length=128)
