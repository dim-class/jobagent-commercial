"""Bounded maintenance progress for filling missing salaries on existing Jobs.

These rows never duplicate job content. Every successful write still goes
through canonical ``job_intake``; this table only records a finite human-
confirmed maintenance run and its per-job outcome.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class SalaryBackfillRun(Base, TimestampMixin):
    __tablename__ = "salary_backfill_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    total_jobs: Mapped[int] = mapped_column(Integer, nullable=False)
    current_job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    processed_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    unavailable_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failed_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    session_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    session_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    paused_reason: Mapped[str | None] = mapped_column(String(64))
    last_action: Mapped[str | None] = mapped_column(String(128))
    last_error: Mapped[str | None] = mapped_column(String(256))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["SalaryBackfillItem"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="SalaryBackfillItem.position"
    )


class SalaryBackfillItem(Base):
    __tablename__ = "salary_backfill_items"
    __table_args__ = (UniqueConstraint("run_id", "job_id", name="uq_salary_backfill_run_job"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("salary_backfill_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    reason: Mapped[str | None] = mapped_column(String(128))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped[SalaryBackfillRun] = relationship(back_populates="items")
    job: Mapped["Job"] = relationship()  # noqa: F821
