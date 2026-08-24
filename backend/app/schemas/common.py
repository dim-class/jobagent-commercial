"""Shared response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    database: str
    openai_configured: bool
    auto_apply: bool = False
    models: dict[str, str] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class MessageResponse(BaseModel):
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ScoreBucket(BaseModel):
    label: str
    count: int


class TopJob(BaseModel):
    job_id: int
    company: str
    title: str
    city: str | None = None
    salary_text: str | None = None
    overall_score: int
    verdict: str


class DashboardSummary(BaseModel):
    total_jobs: int
    analyzed_jobs: int
    unanalyzed_jobs: int
    recommended: int          # strong_apply + apply
    strong_apply: int
    apply: int
    maybe: int
    skip: int
    average_score: float | None = None
    by_status: dict[str, int] = Field(default_factory=dict)
    by_city: dict[str, int] = Field(default_factory=dict)
    score_buckets: list[ScoreBucket] = Field(default_factory=list)
    top_jobs: list[TopJob] = Field(default_factory=list)
    active_resume_id: int | None = None
    openai_configured: bool = False
    # v0.4: application funnel, computed by services/application_metrics.py
    funnel: dict[str, int] = Field(default_factory=dict)
    rates: dict[str, float | None] = Field(default_factory=dict)
    daily_target: int = 10
    applied_today: int = 0


class SettingsResponse(BaseModel):
    openai_configured: bool
    model_fast: str
    model_smart: str
    database_url: str
    max_analyses_per_run: int
    auto_apply: bool
    prompt_version: str
    strategy_path: str
    version: str
