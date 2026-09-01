"""Chrome-extension intake schemas (POC).

The extension sends *structured fields it already extracted*, never a page
dump. There is deliberately no field here for raw HTML, cookies, storage or
headers - the shape of this payload is itself part of the privacy boundary.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PageType = Literal["search", "detail", "unsupported"]
CandidateStatus = Literal["new", "duplicate", "incomplete", "excluded"]


class ExtensionJobCandidate(BaseModel):
    """One job the extension read off the page the user had open."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = Field(default=None, max_length=256)
    company: str | None = Field(default=None, max_length=256)
    salary_text: str | None = Field(default=None, max_length=128)
    city: str | None = Field(default=None, max_length=64)
    experience_text: str | None = Field(default=None, max_length=64)
    education_text: str | None = Field(default=None, max_length=64)
    #: Already query-stripped by the extension; stripped again server-side.
    source_url: str | None = Field(default=None, max_length=1024)
    external_id: str | None = Field(default=None, max_length=128)
    #: The job description body only - never the whole document.
    description: str | None = Field(default=None, max_length=40000)

    #: Developer mode: field -> the selector that matched it. Diagnostics only,
    #: never persisted, and it cannot carry page content.
    matched_selectors: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PreviewRequest(BaseModel):
    page_type: PageType = "unsupported"
    page_url: str | None = Field(default=None, max_length=1024)
    candidates: list[ExtensionJobCandidate] = Field(default_factory=list, max_length=60)
    task_id: int | None = Field(default=None, gt=0)


class KnownJobsRequest(BaseModel):
    """Canonical detail URLs the runner is about to consider opening."""

    urls: list[str] = Field(default_factory=list, max_length=200)


class KnownJobsResponse(BaseModel):
    """Which of them the library already holds. Nothing is written."""

    known: list[str] = Field(default_factory=list)


class PreviewRow(BaseModel):
    """What the backend can say about one candidate without saving it."""

    index: int
    title: str = ""
    company: str = ""
    status: CandidateStatus
    status_label: str
    #: Set when this candidate is already in the database.
    existing_job_id: int | None = None
    #: Fields that would have to be filled in before an import can succeed.
    blocking_fields: list[str] = Field(default_factory=list)
    #: Missing optional fields that a confirmed duplicate import can safely
    #: fill without overwriting existing data.
    enrichable_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PreviewResponse(BaseModel):
    """Read-only. Nothing in this response was written to the database."""

    detected: int = 0
    new_count: int = 0
    duplicate_count: int = 0
    incomplete_count: int = 0
    excluded_count: int = 0
    rows: list[PreviewRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    message: str = ""


class ImportRequest(BaseModel):
    """One explicit save. There is no bulk version on purpose."""

    confirmed: bool = Field(default=False, description="必须为 true —— 导入是明确的人工动作")
    candidate: ExtensionJobCandidate
    task_id: int | None = Field(default=None, gt=0)


class ImportResponse(BaseModel):
    job_id: int
    duplicate: bool
    title: str = ""
    company: str = ""
    message: str = ""
