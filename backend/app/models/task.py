"""Job search task persistence (M1: 求职任务控制台).

``JobSearchTask`` is stored search criteria plus an explicit, currently
single-valued mode. Creating or reading one never searches, captures,
clicks, scrolls, or navigates anything - it is configuration and a grouping
key, nothing more. See ``docs/orchestration/ROADMAP.md`` for the full
milestone spec and the M4 gate on anything resembling automated execution.

``TaskCandidate`` is a many-to-many association between tasks and jobs, on
purpose rather than a ``task_id`` column on ``Job``: the repo globally
deduplicates ``Job`` by ``content_hash`` / ``(source, external_id)``, and
the exact same posting can legitimately be discovered under two different
tasks (different keywords or cities matching the same job). A nullable
``Job.task_id`` would force a single owning task per job and silently lose
provenance for every other task that also found it. This table preserves
per-task provenance without duplicating or re-owning the ``Job`` row, and a
unique constraint on ``(task_id, job_id)`` makes attaching the same job to
the same task twice a no-op rather than a duplicate row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import SearchTaskRunStatus, TaskMode


class JobSearchTask(Base, TimestampMixin):
    __tablename__ = "job_search_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    keywords: Mapped[str | None] = mapped_column(String(256), nullable=True)
    city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    experience_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    education_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Keywords/company names this task never wants surfaced. ``NULL`` means
    #: the human never set this criterion; an explicit ``[]`` means they set
    #: it to "no exclusions" - the two are kept distinguishable end-to-end
    #: rather than collapsed into the same empty list.
    exclusions_json: Mapped[list[Any] | None] = mapped_column(
        JSON, nullable=True, default=None
    )
    #: The resume variant to match candidates against. ``SET NULL`` on
    #: delete: a task outlives an archived/removed resume variant, it just
    #: loses the reference rather than being deleted itself.
    resume_id: Mapped[int | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True
    )
    max_candidates: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode: Mapped[TaskMode] = mapped_column(
        SAEnum(TaskMode, native_enum=False, length=32),
        nullable=False,
        default=TaskMode.manual_review_only,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Candidate-stage policy captured when this task is created. It must not
    #: follow later settings edits while a confirmed runner is in progress.
    early_career_policy: Mapped[str] = mapped_column(
        String(16), nullable=False, default="exclude", server_default="exclude"
    )

    #: BOSS result-page filters carried over from a URL the human built in
    #: their own browser (`services/boss_search_filters.py`). They narrow the
    #: search so a repeat run does not meet the same top results again. Stored
    #: per task and never re-derived: a task must keep searching what it was
    #: created to search, even if the console's paste box changes afterwards.
    search_filters_json: Mapped[dict[str, str]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )

    # --- M4e/M4f: SearchPlan + bounded automatic runner (explicitly
    # authorized - see CLAUDE.md's M4e/M4f amendment). All nullable/defaulted
    # so every pre-existing manual task is unaffected - see
    # ``services/search_plan.py`` and ``services/search_task_runner.py``.
    #: The structured BOSS city code (e.g. "101010100") a SearchPlan task was
    #: generated for - distinct from the free-text, human-entered ``city``
    #: above. ``None`` for a manual task.
    city_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: True only for a task ``services/search_plan.py`` generated - one
    #: city x keyword combination. A manual task (M1) is never flagged this
    #: way, even if its own ``keywords``/``city`` happen to match one.
    is_search_plan: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    run_status: Mapped[SearchTaskRunStatus | None] = mapped_column(
        SAEnum(SearchTaskRunStatus, native_enum=False, length=24), nullable=True
    )
    run_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Cumulative across the task's whole life (every run, not reset on
    #: pause/resume) - unique canonical URLs seen, newly seen, and already-
    #: known-duplicate, per ``services/search_task_runner.py``.
    observed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    new_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Consecutive rounds with zero new candidates in the *current* run only
    #: - reset to 0 by a resume and by any round that finds something new.
    #: Reaching the configured threshold (default 3) auto-completes the run.
    no_new_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Set only by ``fail_run`` - a short, human-readable reason. Never a
    #: stack trace, and never page content/tokens.
    last_error: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # --- M4f: runner observability (explicitly authorized). Narrow, current-
    # state-only fields the extension reports via ``report_state`` - never a
    # query string (BOSS session tokens live there), never cookies/storage,
    # never a full job description. See ``services/search_task_runner.py``.
    #: The approved tab's current, query-stripped URL - a results or detail
    #: page, never the raw address bar value.
    current_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: How many bounded scroll rounds this run has performed so far.
    scroll_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: How many candidate cards are currently rendered on the results page -
    #: a live snapshot, not cumulative.
    visible_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: How many candidates this run actually imported through
    #: ``job_intake`` - distinct from ``new_count`` (a scroll-round dedup
    #: tally) since a "new" card can still turn out already-known by the
    #: time its detail pane is captured.
    imported_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: The title of whichever candidate the runner is currently opening/
    #: reading, if any - a job title is not private data.
    current_candidate: Mapped[str | None] = mapped_column(String(256), nullable=True)
    #: A short, human-readable label for the runner's most recent step
    #: (e.g. "opened candidate", "scrolled", "imported"), for the status UI.
    last_action: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Set only while paused/verification-halted - why (e.g. "verification",
    #: "user_pause"), distinct from ``last_error`` (an actual failure).
    paused_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Opt-in cost approval and bounded claim ledger, not a second analysis/job store.
    match_run_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    match_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    resume: Mapped["Resume | None"] = relationship()  # noqa: F821
    candidates: Mapped[list["TaskCandidate"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskCandidate.created_at.desc()",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<JobSearchTask id={self.id} name={self.name!r} mode={self.mode}>"


class TaskCandidate(Base, TimestampMixin):
    """One (task, job) association. Provenance only - never a second Job row."""

    __tablename__ = "task_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("job_search_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    task: Mapped["JobSearchTask"] = relationship(back_populates="candidates")
    job: Mapped["Job"] = relationship()  # noqa: F821

    __table_args__ = (
        UniqueConstraint("task_id", "job_id", name="uq_task_candidates_task_job"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TaskCandidate task={self.task_id} job={self.job_id}>"
