"""求职任务控制台 schemas (M1).

A task is stored search criteria + an explicit mode, never a trigger for
automated search or capture. See docs/orchestration/ROADMAP.md.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import JobStatus, TaskMode, Verdict


class TaskCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    keywords: str | None = Field(default=None, max_length=256)
    city: str | None = Field(default=None, max_length=64)
    experience_text: str | None = Field(default=None, max_length=64)
    education_text: str | None = Field(default=None, max_length=64)
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)
    #: ``None`` = this criterion was never set. ``[]`` = explicitly "no
    #: exclusions". Kept distinct all the way to storage.
    exclusions: list[str] | None = None
    resume_id: int | None = None
    max_candidates: int | None = Field(default=None, ge=1)
    min_score: int | None = Field(default=None, ge=0, le=100)
    mode: TaskMode = TaskMode.manual_review_only
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("不能为空")
        return v

    @model_validator(mode="after")
    def _check_salary_range(self) -> "TaskCreate":
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError("salary_min 不能大于 salary_max")
        return self


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    keywords: str | None = None
    city: str | None = None
    experience_text: str | None = None
    education_text: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    #: ``None`` when never set; ``[]`` when explicitly "no exclusions".
    exclusions: list[str] | None = None
    resume_id: int | None = None
    max_candidates: int | None = None
    min_score: int | None = None
    mode: TaskMode
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    items: list[TaskOut]
    total: int


class CandidateAssociationRequest(BaseModel):
    """Attach one already-captured job to a task. Never captures anything itself."""

    job_id: int


class CandidateAnalysisOut(BaseModel):
    overall_score: int
    verdict: Verdict


class TaskCandidateOut(BaseModel):
    job_id: int
    title: str
    company: str
    city: str | None = None
    salary_text: str | None = None
    status: JobStatus
    added_at: datetime
    #: The job's current (most recent) analysis, or absent if never analyzed.
    #: Never fabricated - a missing analysis stays ``None``.
    analysis: CandidateAnalysisOut | None = None


class TaskCandidateListResponse(BaseModel):
    items: list[TaskCandidateOut]
    total: int
