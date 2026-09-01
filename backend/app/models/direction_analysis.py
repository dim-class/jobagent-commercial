"""Cached résumé -> search-direction analysis.

Its own table rather than a column on `resumes`: the verdict depends on the
strategy, the model and the prompt version as well as the résumé, so one row
per résumé could not hold two of them, and a cache miss would look like a
missing analysis rather than a different question.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ResumeDirectionAnalysis(Base, TimestampMixin):
    __tablename__ = "resume_direction_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: sha256(resume_hash, strategy_hash, candidates, model, prompt_version).
    #: Any of those changing is a different question, so it re-analyses instead
    #: of serving a stale answer.
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    resume_id: Mapped[int] = mapped_column(
        ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The agent's typed output, stored verbatim.
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
