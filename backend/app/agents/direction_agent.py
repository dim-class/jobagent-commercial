"""ResumeDirectionAgent - decides what to search for, not what to apply to.

The second agent in this codebase, and only because there is a genuine second
job: `JobMatchAgent` answers "does this JD fit this résumé", which cannot
answer "what should I be searching for at all". One turn, one typed output, no
tools - the same shape as the first agent.
"""

from __future__ import annotations

import time
from typing import Any

from agents import Agent, OpenAIResponsesModel, RunConfig, Runner

from app.agents.direction_prompts import (
    RESUME_EXCERPT_CHARS,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.agents.job_match_agent import require_openai
from app.agents.openai_client import build_client
from app.core.config import Settings
from app.core.errors import UpstreamError
from app.core.logging import get_logger, log_event
from app.schemas.direction import ResumeDirectionAnalysis
from app.services.ai_diagnostics import classify_openai_error

logger = get_logger(__name__)

AGENT_NAME = "ResumeDirectionAgent"


def build_agent(model_name: str, settings: Settings | None = None) -> Agent:
    cfg = require_openai(settings)
    client = build_client(cfg)
    return Agent(
        name=AGENT_NAME,
        instructions=SYSTEM_PROMPT,
        model=OpenAIResponsesModel(model=model_name, openai_client=client),
        output_type=ResumeDirectionAnalysis,
    )


async def run_direction_analysis(
    *,
    model_name: str,
    resume_profile: dict[str, Any],
    resume_excerpt: str,
    strategy: dict[str, Any],
    candidates: list[str],
    history: dict[str, dict[str, Any]] | None = None,
    settings: Settings | None = None,
) -> ResumeDirectionAnalysis:
    """Run one direction analysis. Returns typed output or raises ``AppError``."""
    cfg = require_openai(settings)
    prompt = build_user_prompt(
        resume_profile=resume_profile,
        resume_excerpt=(resume_excerpt or "")[:RESUME_EXCERPT_CHARS],
        strategy=strategy,
        candidates=candidates,
        history=history or {},
    )

    agent = build_agent(model_name, cfg)
    started = time.perf_counter()
    try:
        result = await Runner.run(
            agent,
            prompt,
            max_turns=1,
            # Local-first: no traces leave this machine.
            run_config=RunConfig(tracing_disabled=True, workflow_name="resume-direction"),
        )
    except Exception as exc:  # noqa: BLE001 - normalised into a 502 for the UI
        classification = classify_openai_error(exc)
        log_event(
            logger,
            "direction.upstream_failed",
            model=model_name,
            error=type(exc).__name__,
            category=classification.category,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
        raise UpstreamError(
            classification.message,
            detail={"model": model_name, "category": classification.category},
        ) from exc

    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    log_event(
        logger,
        "direction.model_returned",
        model=model_name,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        candidates=len(candidates),
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
    )

    output = result.final_output_as(ResumeDirectionAnalysis, raise_if_incorrect_type=False)
    if not isinstance(output, ResumeDirectionAnalysis):
        raise UpstreamError(
            "模型未按结构化格式返回方向分析结果，请重试或切换模型。",
            detail={"model": model_name},
        )
    return output
