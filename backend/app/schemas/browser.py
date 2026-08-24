"""Request/response schemas for the visible-browser capture flow (v0.2).

Nothing here exposes cookies, storage state, page HTML or credentials. The URL
and title of the page the human is already looking at are returned so the UI
can show what will be captured.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.analysis import AnalysisResponse
from app.schemas.job import JobDetail


class BrowserStatusResponse(BaseModel):
    running: bool
    site: str | None = Field(default=None, description="识别到的招聘网站，例如 boss")
    current_url: str | None = None
    current_title: str | None = None
    page_count: int = 0
    supported_hosts: list[str] = Field(default_factory=list)
    profile_dir: str | None = Field(default=None, description="持久化浏览器配置目录")
    channel: str | None = Field(default=None, description="实际使用的浏览器：chromium/msedge/chrome")
    message: str = ""


class BrowserStartResponse(BrowserStatusResponse):
    already_running: bool = False


class CurrentPageResponse(BaseModel):
    site: str | None = None
    url: str | None = None
    title: str | None = None
    is_job_page: bool = Field(default=False, description="URL 是否为岗位详情页")
    external_id: str | None = None
    message: str = ""


class CapturedFields(BaseModel):
    """What the extractor actually read off the page."""

    title: str
    company: str | None = None
    city: str | None = None
    salary_text: str | None = None
    experience_text: str | None = None
    education_text: str | None = None
    external_id: str | None = None
    description_chars: int = 0
    fields_found: list[str] = Field(default_factory=list)
    fields_missing: list[str] = Field(default_factory=list)
    extra: dict[str, str] = Field(default_factory=dict)


class CaptureResponse(BaseModel):
    """Result of reading the currently open job page.

    A duplicate is a normal outcome, not a failure: ``duplicate`` is true and
    ``job_id`` points at the row already on file so the UI can open it.
    """

    job_id: int
    duplicate: bool
    site: str
    selected_url: str | None = None
    page_title: str | None = None
    job: JobDetail
    fields: CapturedFields
    analysis: AnalysisResponse | None = Field(
        default=None, description="仅在 analyze=true 时返回；默认不消耗 API 额度"
    )
    message: str = ""
