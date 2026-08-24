"""Analysis of one recruiter message: context, cache, grounding (v0.5).

    deterministic signals -> bounded context -> RecruiterConversationAgent
                                             -> cached RecruiterMessageAnalysis

Grounding rule: the agent is given the active resume profile, the career
strategy and (when linked) the job - and nothing else is treated as fact. If an
answer is not in there, the result must say so in ``missing_information``
instead of inventing a number. :func:`enforce_grounding` is the belt-and-braces
check that strips a fabricated answer if the model produces one anyway.

Cache key covers message content, job context, resume, strategy, model, prompt
version **and reply language** - so re-linking a job or switching language
re-analyses, while an unrelated application event does not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.recruiter_prompts import MAX_CONTEXT_MESSAGES, RECRUITER_PROMPT_VERSION
from app.core.career_strategy import load_strategy, strategy_hash
from app.core.config import Settings, get_settings
from app.core.logging import get_logger, log_event
from app.models import (
    Job,
    MessageDirection,
    RecruiterConversation,
    RecruiterMessage,
    RecruiterMessageAnalysis,
    Resume,
)
from app.schemas.recruiter import (
    LanguagePreference,
    RecruiterMessageAnalysisResult,
    ReplyLanguage,
)
from app.services.hashing import hash_json, hash_parts, hash_text
from app.services.recruiter_parser import analyze_signals
from app.services.timezones import local_now, report_timezone

logger = get_logger(__name__)

#: Placeholder the prompt tells the model to use for unknown values.
PLACEHOLDER_RE = re.compile(r"【[^】]*】")


@dataclass(slots=True)
class AnalysisOutcome:
    row: RecruiterMessageAnalysis
    result: RecruiterMessageAnalysisResult
    cached: bool
    signals: dict[str, Any]


# --------------------------------------------------------------------------
# context assembly
# --------------------------------------------------------------------------


def active_resume(db: Session) -> Resume | None:
    resume = db.scalar(select(Resume).where(Resume.is_active.is_(True)).limit(1))
    if resume is None:
        resume = db.scalar(select(Resume).order_by(Resume.created_at.desc()).limit(1))
    return resume


def profile_context(resume: Resume | None) -> dict[str, Any] | None:
    """The only facts the agent may treat as true about the candidate."""
    if resume is None:
        return None
    profile = resume.parsed_profile_json or {}
    return {
        "summary": profile.get("summary"),
        "years_of_experience": profile.get("years_of_experience"),
        "skills": profile.get("skills"),
        "certifications": profile.get("certifications"),
        "languages": profile.get("languages"),
        "work_experience": (profile.get("work_experience") or [])[:6],
        "projects": (profile.get("projects") or [])[:4],
        "education": (profile.get("education") or [])[:3],
    }


def strategy_context(strategy: dict[str, Any]) -> dict[str, Any]:
    salary = strategy.get("salary") or {}
    return {
        "target_cities": strategy.get("target_cities"),
        "remote_ok": strategy.get("remote_ok"),
        "preferred_roles": strategy.get("preferred_roles"),
        # Deliberately included so the model can answer a salary question when
        # the user HAS configured one - and flag it as missing when they have not.
        "min_monthly_cny": salary.get("min_monthly_cny"),
        "ideal_monthly_cny": salary.get("ideal_monthly_cny"),
        "notes": (strategy.get("preferences") or {}).get("notes"),
    }


def job_context(job: Job | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "company": job.company,
        "title": job.title,
        "city": job.city,
        "salary_text": job.salary_text,
        "experience_text": job.experience_text,
        "status": job.status.value,
    }


def prior_messages(
    conversation: RecruiterConversation, *, exclude_id: int | None = None
) -> list[dict[str, str]]:
    """The last few turns before the one being analyzed.

    Bounded by ``MAX_CONTEXT_MESSAGES`` so a long-running thread never grows
    the prompt without limit.
    """
    ordered = sorted(conversation.messages, key=lambda m: (m.created_at, m.id))
    out: list[dict[str, str]] = []
    for message in ordered:
        if exclude_id is not None and message.id == exclude_id:
            continue
        out.append({"direction": message.direction.value, "text": message.raw_text})
    return out[-MAX_CONTEXT_MESSAGES:]


def conversation_summary(conversation: RecruiterConversation) -> str | None:
    """Latest stored summary, derived from the newest analysis - no extra table."""
    best: RecruiterMessageAnalysis | None = None
    for message in conversation.messages:
        for analysis in message.analyses:
            if best is None or (analysis.created_at, analysis.id) > (best.created_at, best.id):
                best = analysis
    if best is None:
        return None
    return str((best.result_json or {}).get("summary") or "") or None


# --------------------------------------------------------------------------
# cache key
# --------------------------------------------------------------------------


def build_cache_key(
    *,
    message: RecruiterMessage,
    job: Job | None,
    resume: Resume | None,
    strategy: dict[str, Any],
    model: str,
    language: str,
    context_signature: str,
) -> str:
    return hash_parts(
        message.content_hash,
        hash_json(job_context(job)) if job else "no-job",
        (resume.content_hash if resume else "no-resume"),
        strategy_hash(strategy),
        model,
        RECRUITER_PROMPT_VERSION,
        language,
        context_signature,
    )


def context_signature(prior: list[dict[str, str]]) -> str:
    """Hash of the bounded history, so appending a turn re-analyses."""
    if not prior:
        return "no-context"
    return hash_text("\n".join(f"{m['direction']}:{m['text']}" for m in prior))


# --------------------------------------------------------------------------
# grounding guard
# --------------------------------------------------------------------------

#: Numbers with an optional Chinese/English magnitude suffix.
_SALARY_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([kK千万]?)")

#: Two values are "the same figure" within this relative tolerance.
_SALARY_TOLERANCE = 0.01


#: "45-55K" writes the unit once, at the end - it applies to both numbers.
_SALARY_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-~～至到]\s*(\d+(?:\.\d+)?)\s*([kK千万])")


def salary_values(text: str) -> list[int]:
    """Monthly CNY figures mentioned in ``text``.

    ``45-55K`` -> [45000, 55000]; ``1.5万`` -> [15000]; ``15000`` -> [15000].
    """
    # Expand ranges first so the shared unit reaches the lower bound too.
    expanded = _SALARY_RANGE_RE.sub(
        lambda m: f"{m.group(1)}{m.group(3)} {m.group(2)}{m.group(3)}", text or ""
    )
    out: list[int] = []
    for raw, suffix in _SALARY_NUMBER_RE.findall(expanded):
        try:
            value = float(raw)
        except ValueError:  # pragma: no cover - regex guarantees a number
            continue
        if suffix in ("k", "K", "千"):
            value *= 1_000
        elif suffix == "万":
            value *= 10_000
        elif value < 1_000:
            # A bare small number ("13薪", "3年") is not a salary figure.
            continue
        out.append(int(value))
    return out


def _matches_configured(values: list[int], allowed: list[int]) -> bool:
    return any(
        abs(value - target) <= max(1.0, target * _SALARY_TOLERANCE)
        for value in values
        for target in allowed
    )


def enforce_grounding(
    result: RecruiterMessageAnalysisResult,
    *,
    strategy: dict[str, Any],
    resume: Resume | None,
) -> RecruiterMessageAnalysisResult:
    """Strip answers the model could not have known.

    The prompt already forbids invention; this is the mechanical backstop for
    the case with real consequences - quoting a salary to a recruiter.

    A suggested salary survives only when its figures actually match what the
    user configured. Merely *having* a salary in the career strategy is not
    enough: the model must have quoted that number, not one of its own.
    """
    payload = result.model_dump(mode="json")
    salary_cfg = strategy.get("salary") or {}
    configured = [
        int(v)
        for v in (salary_cfg.get("min_monthly_cny"), salary_cfg.get("ideal_monthly_cny"))
        if v
    ]
    missing = list(payload.get("missing_information") or [])

    for request in payload.get("recruiter_requests") or []:
        if request.get("type") not in ("expected_salary", "current_salary"):
            continue
        answer = request.get("suggested_answer") or ""
        if not answer or PLACEHOLDER_RE.search(answer):
            continue  # a placeholder is exactly what we want the model to do

        # Current salary is never in our data, so any figure is invented.
        strip = request["type"] == "current_salary"
        if not strip:
            quoted = salary_values(answer)
            strip = bool(quoted) and not _matches_configured(quoted, configured)

        if strip:
            request["suggested_answer"] = None
            request["answer_found_in_profile"] = False
            note = (
                "当前薪资未在资料中记录"
                if request["type"] == "current_salary"
                else "期望薪资尚未配置"
            )
            if note not in missing:
                missing.append(note)

    if not resume:
        note = "尚未上传简历，回复中不应引用具体经历"
        if note not in missing:
            missing.append(note)

    payload["missing_information"] = missing
    return RecruiterMessageAnalysisResult.model_validate(payload)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def latest_analysis(message: RecruiterMessage) -> RecruiterMessageAnalysis | None:
    if not message.analyses:
        return None
    return max(message.analyses, key=lambda a: (a.created_at, a.id))


def result_of(row: RecruiterMessageAnalysis) -> RecruiterMessageAnalysisResult:
    return RecruiterMessageAnalysisResult.model_validate(row.result_json or {})


async def analyze_message(
    db: Session,
    message: RecruiterMessage,
    *,
    language: LanguagePreference = LanguagePreference.auto,
    force: bool = False,
    use_smart_model: bool = False,
    settings: Settings | None = None,
) -> AnalysisOutcome:
    """Analyze one recruiter message, reusing a cached result when possible."""
    cfg = settings or get_settings()
    conversation = message.conversation
    job = conversation.job
    resume = active_resume(db)
    strategy = load_strategy()

    signals = analyze_signals(message.raw_text)
    prior = prior_messages(conversation, exclude_id=message.id)
    model = cfg.openai_model_smart if use_smart_model else cfg.openai_model_fast

    cache_key = build_cache_key(
        message=message,
        job=job,
        resume=resume,
        strategy=strategy,
        model=model,
        language=language.value,
        context_signature=context_signature(prior),
    )

    if not force:
        cached = db.scalar(
            select(RecruiterMessageAnalysis).where(
                RecruiterMessageAnalysis.cache_key == cache_key
            )
        )
        if cached is not None:
            log_event(
                logger,
                "recruiter.analysis_cache_hit",
                message_id=message.id,
                conversation_id=conversation.id,
                model=model,
            )
            return AnalysisOutcome(
                row=cached, result=result_of(cached), cached=True, signals=signals.to_dict()
            )

    from app.agents.recruiter_agent import run_message_analysis

    log_event(
        logger,
        "recruiter.analysis_started",
        message_id=message.id,
        conversation_id=conversation.id,
        model=model,
        chars=len(message.raw_text or ""),
        signals=",".join(signals.request_types) or "-",
    )

    raw_result = await run_message_analysis(
        model_name=model,
        current_message=message.raw_text,
        conversation_summary=conversation_summary(conversation),
        prior_messages=prior,
        job=job_context(job),
        profile=profile_context(resume),
        strategy=strategy_context(strategy),
        signals=signals.to_dict(),
        language_preference=language.value,
        detected_language=signals.language,
        timezone_name=cfg.report_timezone,
        today=local_now().date().isoformat(),
        settings=cfg,
    )
    result = enforce_grounding(raw_result, strategy=strategy, resume=resume)

    row = db.scalar(
        select(RecruiterMessageAnalysis).where(RecruiterMessageAnalysis.cache_key == cache_key)
    )
    if row is None:
        row = RecruiterMessageAnalysis(
            message_id=message.id, cache_key=cache_key, created_at=datetime.now(timezone.utc)
        )
        db.add(row)

    row.job_id = job.id if job else None
    row.resume_id = resume.id if resume else None
    row.model = model
    row.prompt_version = RECRUITER_PROMPT_VERSION
    row.result_json = result.model_dump(mode="json")
    db.commit()
    db.refresh(row)

    log_event(
        logger,
        "recruiter.analysis_completed",
        message_id=message.id,
        model=model,
        sentiment=result.sentiment.value,
        stage=result.conversation_stage.value,
        requests=len(result.recruiter_requests),
        needs_reply=result.needs_reply,
    )
    return AnalysisOutcome(row=row, result=result, cached=False, signals=signals.to_dict())


def default_reply_language(text: str) -> ReplyLanguage:
    mapping = {"zh": ReplyLanguage.zh, "ja": ReplyLanguage.ja, "en": ReplyLanguage.en}
    return mapping.get(analyze_signals(text).language, ReplyLanguage.other)


def timezone_name() -> str:
    return str(report_timezone())
