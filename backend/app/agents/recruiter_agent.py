"""RecruiterConversationAgent - reads recruiter messages, drafts replies (v0.5).

Separate from ``JobMatchAgent`` (which scores jobs) and ``JobImportAgent``
(which extracts postings). Overloading either would blur three different jobs
into one prompt.

The agent only ever sees content the human pasted or uploaded. It has no tools,
cannot reach an inbox, and its ``suggested_reply`` is a **draft** - JobAgent
never sends anything.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from agents import Agent, AsyncOpenAI, OpenAIResponsesModel, RunConfig, Runner
from pydantic import BaseModel, ConfigDict, Field

from app.agents.job_match_agent import require_openai
from app.agents.recruiter_prompts import (
    RECRUITER_PROMPT_VERSION,
    SYSTEM_PROMPT,
    VISION_SYSTEM_PROMPT,
    VISION_USER_PROMPT,
    build_user_prompt,
    language_instruction,
)
from app.core.config import Settings, get_settings
from app.core.errors import UpstreamError
from app.core.logging import get_logger, log_event
from app.schemas.recruiter import RecruiterMessageAnalysisResult

logger = get_logger(__name__)

AGENT_NAME = "RecruiterConversationAgent"


class VisionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: str = Field(default="recruiter", description="recruiter 或 user")
    text: str = ""
    time_text: str | None = None


class VisionTranscript(BaseModel):
    """Typed output of the screenshot reader."""

    model_config = ConfigDict(extra="forbid")

    messages: list[VisionMessage] = Field(default_factory=list)
    partial: bool = False
    notes: list[str] = Field(default_factory=list)


def _client(cfg: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=cfg.openai_api_key, timeout=cfg.openai_timeout_seconds)


def _build_agent(model_name: str, instructions: str, output_type, cfg: Settings) -> Agent:
    return Agent(
        name=AGENT_NAME,
        instructions=instructions,
        model=OpenAIResponsesModel(model=model_name, openai_client=_client(cfg)),
        output_type=output_type,
    )


async def _run(agent: Agent, prompt_input, *, model_name: str, kind: str, output_type):
    started = time.perf_counter()
    try:
        result = await Runner.run(
            agent,
            prompt_input,
            max_turns=1,
            run_config=RunConfig(tracing_disabled=True, workflow_name="recruiter-conversation"),
        )
    except Exception as exc:  # noqa: BLE001 - normalised for the UI
        log_event(
            logger,
            "recruiter.upstream_failed",
            kind=kind,
            model=model_name,
            error=type(exc).__name__,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
        raise UpstreamError(
            "AI 分析暂时不可用，请稍后重试；你仍然可以手动阅读并回复这条消息。",
            detail={"model": model_name, "error_type": type(exc).__name__, "kind": kind},
        ) from exc

    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    # Message bodies are never logged - only sizes, ids and timings.
    log_event(
        logger,
        "recruiter.model_returned",
        kind=kind,
        model=model_name,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
    )

    output = result.final_output_as(output_type, raise_if_incorrect_type=False)
    if not isinstance(output, output_type):
        raise UpstreamError(
            "AI 返回的格式不正确，请重试。", detail={"model": model_name, "kind": kind}
        )
    return output


async def run_message_analysis(
    *,
    model_name: str,
    current_message: str,
    conversation_summary: str | None,
    prior_messages: list[dict[str, str]],
    job: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    strategy: dict[str, Any] | None,
    signals: dict[str, Any],
    language_preference: str,
    detected_language: str,
    timezone_name: str,
    today: str,
    settings: Settings | None = None,
) -> RecruiterMessageAnalysisResult:
    """Analyze one recruiter message and draft a reply."""
    cfg = require_openai(settings or get_settings())
    prompt = build_user_prompt(
        current_message=current_message,
        conversation_summary=conversation_summary,
        prior_messages=prior_messages,
        job=job,
        profile=profile,
        strategy=strategy,
        signals=signals,
        language_instruction=language_instruction(language_preference, detected_language),
        timezone_name=timezone_name,
        today=today,
    )
    agent = _build_agent(model_name, SYSTEM_PROMPT, RecruiterMessageAnalysisResult, cfg)
    return await _run(
        agent,
        prompt,
        model_name=model_name,
        kind="analysis",
        output_type=RecruiterMessageAnalysisResult,
    )


async def run_vision_transcript(
    image_bytes: bytes, mime_type: str, *, settings: Settings | None = None
) -> tuple[VisionTranscript, str]:
    """Read a conversation screenshot. The image is never written to disk."""
    cfg = require_openai(settings or get_settings())
    model_name = cfg.openai_model_fast
    agent = _build_agent(model_name, VISION_SYSTEM_PROMPT, VisionTranscript, cfg)

    encoded = base64.b64encode(image_bytes).decode("ascii")
    prompt_input = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": VISION_USER_PROMPT},
                {
                    "type": "input_image",
                    "detail": "auto",
                    "image_url": f"data:{mime_type};base64,{encoded}",
                },
            ],
        }
    ]
    transcript = await _run(
        agent, prompt_input, model_name=model_name, kind="vision", output_type=VisionTranscript
    )
    return transcript, model_name


__all__ = [
    "RECRUITER_PROMPT_VERSION",
    "VisionMessage",
    "VisionTranscript",
    "run_message_analysis",
    "run_vision_transcript",
]
