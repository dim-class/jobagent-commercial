"""Quick Capture schemas (v0.3).

The human copies job content out of their own browser and hands it to us. We
turn that into a :class:`JobImportCandidate` - a *proposal*, never a Job. Only
after the user reviews and confirms it (possibly with edits) does it go through
the existing ``job_intake`` pipeline.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.job import JobDetail


class Confidence(str, Enum):
    """How much we trust one extracted field.

    ``low`` also covers "not found at all" - either way the user should look.
    """

    high = "high"
    medium = "medium"
    low = "low"


class ExtractionMethod(str, Enum):
    deterministic = "deterministic"
    ai_text = "ai_text"
    ai_vision = "ai_vision"
    hybrid = "hybrid"


class FieldConfidence(BaseModel):
    """Per-field confidence, surfaced subtly in the preview UI."""

    overall: Confidence = Confidence.low
    company: Confidence = Confidence.low
    title: Confidence = Confidence.low
    city: Confidence = Confidence.low
    salary: Confidence = Confidence.low
    experience: Confidence = Confidence.low
    description: Confidence = Confidence.low


class JobImportCandidate(BaseModel):
    """A parsed-but-unsaved job. Nothing here is persisted until confirmed."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(default="manual", description="由 URL 推断，未提供则为 manual")
    source_url: str | None = Field(default=None, description="仅作元数据，后端不会访问")

    company: str | None = None
    title: str | None = None
    city: str | None = None
    salary_text: str | None = None
    experience_text: str | None = None
    education_text: str | None = None

    raw_description: str = ""

    confidence: FieldConfidence = Field(default_factory=FieldConfidence)
    warnings: list[str] = Field(default_factory=list)
    extraction_method: ExtractionMethod = ExtractionMethod.deterministic

    def is_saveable(self) -> bool:
        """A title and a description are the minimum a Job needs."""
        return bool((self.title or "").strip()) and bool((self.raw_description or "").strip())


class AIExtractedJob(BaseModel):
    """Typed structured output for both the text and the vision extractor.

    Deliberately flat and nullable: the prompt tells the model to return null
    rather than guess, so every field here must accept null.
    """

    model_config = ConfigDict(extra="forbid")

    company: str | None = Field(default=None, description="公司名称，未出现则为 null")
    title: str | None = Field(default=None, description="职位名称，未出现则为 null")
    city: str | None = Field(default=None, description="工作城市，未出现则为 null")
    salary_text: str | None = Field(default=None, description="薪资原文，未出现则为 null")
    experience_text: str | None = Field(default=None, description="经验要求原文")
    education_text: str | None = Field(default=None, description="学历要求原文")
    raw_description: str = Field(default="", description="职位描述正文，去掉导航与推荐位")
    partial_description: bool = Field(
        default=False, description="只看到部分 JD 时为 true"
    )
    notes: list[str] = Field(default_factory=list, description="提取过程中的提示，可为空")


# --------------------------------------------------------------------------
# requests / responses
# --------------------------------------------------------------------------


class TextParseRequest(BaseModel):
    text: str = Field(description="用户粘贴的职位内容原文")
    source_url: str | None = Field(default=None, max_length=1024)
    allow_ai: bool = Field(
        default=True, description="允许在确定性解析不足时调用 AI 补充提取"
    )


class ParseResponse(BaseModel):
    candidate: JobImportCandidate
    ai_used: bool = False
    ai_available: bool = True
    ai_error: str | None = Field(
        default=None, description="AI 提取失败时的说明；确定性结果仍然可用"
    )
    message: str = ""


class ConfirmRequest(BaseModel):
    """The user-edited candidate. Whatever they typed wins."""

    candidate: JobImportCandidate


class ConfirmResponse(BaseModel):
    job_id: int
    duplicate: bool
    job: JobDetail
    message: str = ""
