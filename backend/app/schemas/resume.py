"""Request/response schemas for resumes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResumeListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    file_type: str
    #: The **analysis** resume flag. Says nothing about what past applications
    #: used - that lives on the application cycle. See CLAUDE.md.
    is_active: bool
    created_at: datetime
    updated_at: datetime
    text_length: int = 0
    skills: list[str] = Field(default_factory=list)

    # --- variant metadata (v0.7) ----------------------------------------
    variant_name: str | None = None
    variant_group: str | None = None
    parent_resume_id: int | None = None
    notes: str | None = None
    archived_at: datetime | None = None
    archived: bool = False
    #: Display name = variant_name or filename.
    label: str = ""
    #: How many recorded applications used this variant. 0 is a real answer.
    applications: int = 0
    mature_applications: int = 0
    replies: int = 0
    interviews: int = 0
    offers: int = 0
    #: Distinct jobs with a stored analysis against this variant.
    analyzed_jobs: int = 0


class ResumeDetail(ResumeListItem):
    content_hash: str = ""
    raw_text_preview: str = ""
    parsed_profile: dict[str, Any] = Field(default_factory=dict)


class ResumeUploadResponse(BaseModel):
    resume: ResumeDetail
    reused_existing: bool = False
    message: str = ""


class ResumeUpdateRequest(BaseModel):
    """Rename / re-tag a variant. Content is never edited by JobAgent."""

    variant_name: str | None = Field(default=None, max_length=128)
    variant_group: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)


class ResumeCloneRequest(BaseModel):
    """Fork a variant. Metadata/versioning only - no content rewriting."""

    variant_name: str | None = Field(default=None, max_length=128)
    variant_group: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)
