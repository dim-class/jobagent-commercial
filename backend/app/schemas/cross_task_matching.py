"""M5b bounded cross-task matching and unified human review schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.models.enums import Verdict


class CrossTaskSelection(BaseModel):
    task_ids: list[int] = Field(min_length=1, max_length=20)

    @field_validator("task_ids")
    @classmethod
    def exact_unique_positive_ids(cls, value: list[int]) -> list[int]:
        if any(type(task_id) is not int or task_id <= 0 for task_id in value):
            raise ValueError("任务 ID 必须是正整数")
        if len(set(value)) != len(value):
            raise ValueError("任务列表不能包含重复项")
        return value


class CrossTaskMatchRunRequest(CrossTaskSelection):
    confirmed: bool = Field(default=False, strict=True)
    fingerprint: str = Field(min_length=64, max_length=64)
    max_new_calls: int = Field(ge=0, le=3, strict=True)


class CrossTaskSourceOut(BaseModel):
    task_id: int
    name: str
    city: str | None = None
    keyword: str | None = None


class CrossTaskReviewItemOut(BaseModel):
    job_id: int
    title: str
    company: str
    city: str | None = None
    salary_text: str | None = None
    experience_text: str | None = None
    education_text: str | None = None
    sources: list[CrossTaskSourceOut]
    cached: bool
    score: int | None = None
    verdict: Verdict | None = None
    bucket: str
    review_reasons: list[str]
    summary: str = ""


class CrossTaskMatchPlanOut(BaseModel):
    task_ids: list[int]
    task_count: int
    active_resume_id: int
    active_resume_name: str
    model: str
    fingerprint: str
    unique_jobs: int
    cached_jobs: int
    pending_jobs: int
    max_new_calls: int
    items: list[CrossTaskReviewItemOut]


class CrossTaskMatchOutcomeOut(BaseModel):
    job_id: int
    cached: bool
    analyzed: bool
    error: str | None = None
    category: str | None = None
    http_status: int | None = None


class CrossTaskMatchRunResponse(BaseModel):
    plan: CrossTaskMatchPlanOut
    results: list[CrossTaskMatchOutcomeOut]
    calls_used: int
    analyzed: int
    failed: int
