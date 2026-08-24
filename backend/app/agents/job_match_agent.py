"""JobMatchAgent - the single agent used in v0.1.

One agent, one turn, one typed structured output. There are deliberately no
tools and no handoffs: adding agents before there is a second job to do just
buys latency and cost.

The OpenAI client is constructed per call from :mod:`app.core.config`, so the
API key never leaves the backend process and a key added to ``.env`` after
startup is picked up on the next request without a restart.
"""

from __future__ import annotations

import time
from typing import Any

from agents import Agent, AsyncOpenAI, OpenAIResponsesModel, RunConfig, Runner

from app.agents.prompts import SYSTEM_PROMPT, build_user_prompt
from app.core.config import Settings, get_settings
from app.core.errors import ConfigurationError, UpstreamError
from app.core.logging import get_logger, log_event
from app.schemas.analysis import JobMatchResult

logger = get_logger(__name__)

AGENT_NAME = "JobMatchAgent"

# How much raw resume text to include alongside the structured profile.
RESUME_EXCERPT_CHARS = 6000
# Guard against a pathological JD blowing up the request.
MAX_JD_CHARS = 12000


class MissingApiKeyError(ConfigurationError):
    """Raised when an AI endpoint is hit without OPENAI_API_KEY configured."""


def require_openai(settings: Settings | None = None) -> Settings:
    """Fail fast with an actionable message instead of a 500 deep in the SDK."""
    cfg = settings or get_settings()
    if not cfg.openai_configured:
        raise MissingApiKeyError(
            "未配置 OPENAI_API_KEY，AI 分析功能不可用。"
            "请在项目根目录的 .env 文件中设置 OPENAI_API_KEY 后重启后端。",
            detail={"env_var": "OPENAI_API_KEY", "env_file": ".env"},
        )
    return cfg


def build_agent(model_name: str, settings: Settings | None = None) -> Agent:
    """Construct the agent bound to a specific model."""
    cfg = require_openai(settings)
    client = AsyncOpenAI(api_key=cfg.openai_api_key, timeout=cfg.openai_timeout_seconds)
    return Agent(
        name=AGENT_NAME,
        instructions=SYSTEM_PROMPT,
        model=OpenAIResponsesModel(model=model_name, openai_client=client),
        output_type=JobMatchResult,
    )


async def run_job_match(
    *,
    model_name: str,
    resume_profile: dict[str, Any],
    resume_excerpt: str,
    strategy: dict[str, Any],
    job: dict[str, Any],
    pre_analysis: dict[str, Any],
    settings: Settings | None = None,
) -> JobMatchResult:
    """Run one analysis. Returns the typed result or raises an ``AppError``."""
    cfg = require_openai(settings)

    job_for_prompt = dict(job)
    jd = job_for_prompt.get("normalized_description") or ""
    if len(jd) > MAX_JD_CHARS:
        job_for_prompt["normalized_description"] = jd[:MAX_JD_CHARS] + "\n...[JD 过长，已截断]"

    prompt = build_user_prompt(
        resume_profile=resume_profile,
        resume_excerpt=(resume_excerpt or "")[:RESUME_EXCERPT_CHARS],
        strategy=strategy,
        job=job_for_prompt,
        pre_analysis=pre_analysis,
    )

    agent = build_agent(model_name, cfg)
    started = time.perf_counter()
    try:
        result = await Runner.run(
            agent,
            prompt,
            max_turns=1,
            # Local-first: do not ship traces to OpenAI's tracing backend.
            run_config=RunConfig(tracing_disabled=True, workflow_name="job-match"),
        )
    except Exception as exc:  # noqa: BLE001 - normalised into a 502 for the UI
        log_event(
            logger,
            "analysis.upstream_failed",
            model=model_name,
            error=type(exc).__name__,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
        raise UpstreamError(
            f"调用 OpenAI 失败：{type(exc).__name__}。请检查网络、模型名称与配额后重试。",
            detail={"model": model_name, "error_type": type(exc).__name__},
        ) from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    log_event(
        logger,
        "analysis.model_returned",
        model=model_name,
        elapsed_ms=elapsed_ms,
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
    )

    output = result.final_output_as(JobMatchResult, raise_if_incorrect_type=False)
    if not isinstance(output, JobMatchResult):
        raise UpstreamError(
            "模型未按结构化格式返回结果，请重试或切换模型。",
            detail={"model": model_name},
        )
    return output
