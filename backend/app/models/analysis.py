"""JobAnalysis ORM model - one row per (job, resume, strategy, model, prompt)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import Verdict


class JobAnalysis(Base, TimestampMixin):
    __tablename__ = "job_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resume_id: Mapped[int] = mapped_column(
        ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False, index=True
    )

    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")

    # sha256 over resume + strategy + normalized JD + model + prompt version.
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)

    overall_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    verdict: Mapped[Verdict] = mapped_column(
        SAEnum(Verdict, native_enum=False, length=16), nullable=False, default=Verdict.maybe
    )

    # Full JobMatchResult payload, exactly as returned (after guardrails).
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    job: Mapped["Job"] = relationship(back_populates="analyses")  # noqa: F821
    resume: Mapped["Resume"] = relationship(back_populates="analyses")  # noqa: F821

    __table_args__ = (
        UniqueConstraint("cache_key", name="uq_job_analyses_cache_key"),
        Index("ix_job_analyses_job_created", "job_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<JobAnalysis id={self.id} job={self.job_id} "
            f"score={self.overall_score} verdict={self.verdict}>"
        )
