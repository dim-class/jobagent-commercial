"""Structured output contract for JobMatchAgent, plus API response shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Verdict


class JobMatchResult(BaseModel):
    """The typed structured output the agent must return.

    Field order matters: the model fills fields in declaration order, so the
    evidence (scores, matched/missing skills) is produced before the summary
    and the greeting that depend on it.
    """

    model_config = ConfigDict(extra="forbid")

    overall_score: int = Field(description="总体匹配分 0-100")
    verdict: Verdict = Field(description="strong_apply / apply / maybe / skip")

    role_fit_score: int = Field(description="岗位方向匹配分 0-100")
    skill_fit_score: int = Field(description="技能匹配分 0-100")
    experience_fit_score: int = Field(description="经验匹配分 0-100")
    location_fit_score: int = Field(description="地点匹配分 0-100")
    salary_fit_score: int | None = Field(
        default=None, description="薪资匹配分 0-100；JD 未写薪资时返回 null"
    )

    matched_skills: list[str] = Field(default_factory=list, description="JD 要求且候选人具备的技能")
    missing_skills: list[str] = Field(default_factory=list, description="JD 要求但候选人缺失的技能")
    strengths: list[str] = Field(default_factory=list, description="投递该岗位的优势，2-5 条")
    gaps: list[str] = Field(default_factory=list, description="不足之处，1-4 条")
    risk_flags: list[str] = Field(default_factory=list, description="风险提示，可为空")

    experience_gap: str = Field(default="", description="一句话说明经验年限差距")
    role_summary: str = Field(default="", description="一句话概括这个岗位在做什么")
    reasoning_summary: str = Field(default="", description="2-4 句简短结论理由，不要输出思考过程")
    greeting_message: str = Field(default="", description="中文招呼语，60-140 字")


class AnalysisMeta(BaseModel):
    """Everything about *how* a result was produced."""

    analysis_id: int
    job_id: int
    resume_id: int
    model: str
    prompt_version: str
    cache_key: str
    cached: bool = False
    created_at: datetime


class AnalysisResponse(BaseModel):
    meta: AnalysisMeta
    result: JobMatchResult
    pre_analysis: dict[str, Any] | None = None


class AnalyzeRequest(BaseModel):
    force: bool = Field(default=False, description="忽略缓存，强制重新分析")
    use_smart_model: bool = Field(default=False, description="使用高质量模型重新分析")


class BatchAnalyzeRequest(BaseModel):
    job_ids: list[int] | None = Field(
        default=None, description="留空则分析所有尚未分析的岗位（受 MAX_ANALYSES_PER_RUN 限制）"
    )
    force: bool = False
    use_smart_model: bool = False


class BatchAnalyzePlanRequest(BaseModel):
    """Ask what analyzing an explicit set of jobs would cost. Reads only."""

    job_ids: list[int] = Field(description="要分析的岗位 id（顺序即批次处理顺序）")


class BatchAnalyzePlanResponse(BaseModel):
    """The exact numbers a human must see before confirming any spend."""

    selected: int = Field(description="选中的岗位数量（去重并去掉不存在的 id 后）")
    limit: int = Field(description="当前批次上限 MAX_ANALYSES_PER_RUN")
    in_batch: int = Field(description="本批次实际会处理的岗位数量")
    deferred: int = Field(description="超出批次上限、本次不会处理的岗位数量")
    cached: int = Field(description="本批次中已命中缓存、不消耗 API 的数量")
    pending: int = Field(description="本批次中预计新增的 AI 调用数量")
    model: str = Field(description="将要使用的模型（始终是快速模型）")
    resume_id: int
    resume_name: str
    missing_job_ids: list[int] = Field(default_factory=list, description="数据库中不存在的 id")


class BatchAnalyzeItem(BaseModel):
    job_id: int
    ok: bool
    cached: bool = False
    overall_score: int | None = None
    verdict: Verdict | None = None
    error: str | None = None


class BatchAnalyzeResponse(BaseModel):
    requested: int
    analyzed: int
    cached: int
    failed: int
    limit: int
    items: list[BatchAnalyzeItem]


# --------------------------------------------------------------------------
# resume comparison (v0.7)
# --------------------------------------------------------------------------


class CompareResumesRequest(BaseModel):
    """Analyze one JD against several resume variants.

    This is the *only* place in v0.7 that may spend OpenAI credits on the user's
    behalf, and only for combinations that are not already cached. ``confirmed``
    is required whenever at least one such call would happen - the UI shows the
    exact count first.
    """

    resume_ids: list[int] = Field(default_factory=list, max_length=5)
    confirmed: bool = Field(
        default=False, description="有未分析组合时必须为 true —— 可能产生 API 费用"
    )
    use_smart_model: bool = False


class ResumeAnalysisCell(BaseModel):
    """One variant's score on this job, plus whether it cost anything."""

    resume_id: int
    label: str
    archived: bool = False
    overall_score: int | None = None
    verdict: Verdict | None = None
    analysis_id: int | None = None
    created_at: datetime | None = None
    #: False means the score came from cache; nothing was spent for it.
    newly_analyzed: bool = False


class ResumeComparisonResponse(BaseModel):
    job_id: int
    company: str = ""
    title: str = ""
    cells: list[ResumeAnalysisCell] = Field(default_factory=list)
    #: How many combinations still need an API call. 0 means free to run.
    pending_analyses: int = 0
    api_calls_made: int = 0
    message: str = ""


class JobResumeAnalyses(BaseModel):
    """Every stored analysis of one job, one row per resume variant."""

    job_id: int
    cells: list[ResumeAnalysisCell] = Field(default_factory=list)
    #: The resume the human actually applied with, when recorded.
    applied_resume_id: int | None = None
    active_analysis_resume_id: int | None = None
