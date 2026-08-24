"""Request/response schemas for jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import JobStatus, Verdict


class JobCreate(BaseModel):
    """Payload of the 添加岗位 form."""

    title: str = Field(min_length=1, max_length=256)
    company: str = Field(min_length=1, max_length=256)
    raw_description: str = Field(min_length=20, description="粘贴的 JD 原文")
    city: str | None = Field(default=None, max_length=64)
    salary_text: str | None = Field(default=None, max_length=128)
    experience_text: str | None = Field(default=None, max_length=128)
    education_text: str | None = Field(default=None, max_length=128)
    source_url: str | None = Field(default=None, max_length=1024)
    source: str = Field(default="manual", max_length=32)
    external_id: str | None = Field(default=None, max_length=128)

    @field_validator("title", "company", "raw_description")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("不能为空")
        return v


class JobUpdate(BaseModel):
    """PATCH payload - every field optional."""

    title: str | None = Field(default=None, max_length=256)
    company: str | None = Field(default=None, max_length=256)
    city: str | None = Field(default=None, max_length=64)
    salary_text: str | None = Field(default=None, max_length=128)
    experience_text: str | None = Field(default=None, max_length=128)
    education_text: str | None = Field(default=None, max_length=128)
    source_url: str | None = Field(default=None, max_length=1024)
    status: JobStatus | None = None
    note: str | None = Field(default=None, max_length=1000, description="写入 ApplicationEvent")


class AnalysisSummary(BaseModel):
    """Compact analysis view embedded in job list rows."""

    model_config = ConfigDict(from_attributes=True)

    analysis_id: int
    overall_score: int
    verdict: Verdict
    model: str
    created_at: datetime
    reasoning_summary: str = ""


class JobListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    source_url: str | None = None
    company: str
    title: str
    city: str | None = None
    salary_text: str | None = None
    experience_text: str | None = None
    education_text: str | None = None
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    description_preview: str = ""
    latest_analysis: AnalysisSummary | None = None


class JobDetail(JobListItem):
    raw_description: str = ""
    normalized_description: str = ""
    content_hash: str = ""
    external_id: str | None = None
    events: list["JobEvent"] = Field(default_factory=list)


class JobEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    notes: str | None = None
    created_at: datetime


class JobListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[JobListItem]
    facets: dict[str, Any] = Field(default_factory=dict)


class JobCreateResponse(BaseModel):
    job: JobDetail
    duplicate: bool = False
    message: str = ""


JobDetail.model_rebuild()
