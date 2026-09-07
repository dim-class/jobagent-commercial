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
    #: The free, deterministic pre-analysis score, filled in **only for jobs
    #: with no analysis yet** - once a real one exists it is the better answer
    #: and this would just be a second number to confuse it with.
    #:
    #: It is a stand-in, not a verdict: on 442 analysed jobs it correlated 0.74
    #: with the paid score, and the recommend rate ran 0% below 30 against 33%
    #: above 65. Good enough to decide what is worth paying to analyse; not
    #: good enough to decide anything about a job on its own.
    heuristic_score: int | None = None

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


class JobCleanupPlanOut(BaseModel):
    """What a cleanup would remove. Every number a human needs before deleting."""

    threshold: int = Field(description="低于这个匹配分的岗位会被清理")
    analyzed: int = Field(description="库里已分析过的岗位总数")
    deletable: int = Field(description="低于阈值且从未被人工处理过 - 会被删除")
    protected: int = Field(description="低于阈值但有人工决定记录 - 保留，不论分数")
    unscored: int = Field(description="从未分析过、没有分数可判断 - 永远不动")


class JobCleanupRequest(BaseModel):
    threshold: int = Field(default=40, ge=0, le=100)
    #: Must equal what the plan reports at execution time, so a set that moved
    #: between reading the dialog and pressing the button cancels instead.
    expected_count: int = Field(ge=0)
    confirmed: bool = False
