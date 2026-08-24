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

from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import TaskMode


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
