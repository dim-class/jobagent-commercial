"""Quick Capture orchestration (v0.3).

    pasted text  -> deterministic parse -> (maybe) one AI pass -> candidate
    screenshot   -> validate + hash     -> vision AI          -> candidate

The candidate is a *proposal*. It is returned to the UI for review and is only
persisted when the user confirms it, via the existing ``job_intake`` pipeline -
there is no second persistence path.

Cost control: extraction and matching are separate AI calls. Quick Capture
never triggers ``JobMatchAgent``. Extraction results are cached in-process on
``sha256(content, prompt_version, model)`` so re-parsing the same paste is free.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass

from app.agents.import_prompts import IMPORT_PROMPT_VERSION
from app.core.config import Settings, get_settings
from app.core.errors import AppError, ValidationError
from app.core.logging import get_logger, log_event
from app.job_sources import manual_source
from app.job_sources.base import RawJobPosting
from app.schemas.quick_capture import (
    AIExtractedJob,
    Confidence,
    ExtractionMethod,
    JobImportCandidate,
)
from app.services import job_intake
from app.services.job_import_parser import (
    needs_ai_extraction,
    parse_job_text,
    score_confidence,
)
from app.services.urls import canonical_url, detect_source

logger = get_logger(__name__)

MIN_TEXT_CHARS = 20

#: MIME types we accept, mapped to their magic-byte signature check.
ALLOWED_IMAGE_TYPES: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/webp": "webp",
}

_EXTENSION_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class ImageTooLargeError(ValidationError):
    code = "image_too_large"


class UnsupportedImageError(AppError):
    status_code = 415
    code = "unsupported_image"


class AIExtractionDisabledError(AppError):
    status_code = 503
    code = "ai_extraction_disabled"


# --------------------------------------------------------------------------
# extraction cache (in-process; extraction is cheap and idempotent)
# --------------------------------------------------------------------------

_CACHE_MAX = 64
_cache: "OrderedDict[str, AIExtractedJob]" = OrderedDict()


def extraction_cache_key(*, content_hash: str, model: str) -> str:
    payload = "\x1f".join((content_hash, IMPORT_PROMPT_VERSION, model))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_get(key: str) -> AIExtractedJob | None:
    value = _cache.get(key)
    if value is not None:
        _cache.move_to_end(key)
    return value


def cache_put(key: str, value: AIExtractedJob) -> None:
    _cache[key] = value
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


def cache_clear() -> None:
    _cache.clear()


def hash_content(payload: bytes | str) -> str:
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# image validation
# --------------------------------------------------------------------------


def sniff_image_type(payload: bytes) -> str | None:
    """Identify the format from magic bytes, ignoring what the client claimed."""
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "webp"
    return None


def validate_image(
    payload: bytes,
    *,
    content_type: str | None,
    filename: str | None = None,
    settings: Settings | None = None,
) -> tuple[str, str]:
    """Validate an uploaded screenshot. Returns ``(mime_type, sha256)``.

    The declared content type is only a hint - the format is confirmed from the
    file's magic bytes so a mislabelled or hostile upload is rejected.
    """
    cfg = settings or get_settings()

    if not payload:
        raise ValidationError("图片内容为空，请重新选择文件。")

    limit = cfg.quick_capture_max_image_bytes
    if len(payload) > limit:
        raise ImageTooLargeError(
            f"图片过大（上限 {cfg.quick_capture_max_image_mb} MB），请压缩或截取关键区域后重试。",
            detail={"size_bytes": len(payload), "limit_bytes": limit},
        )

    sniffed = sniff_image_type(payload)
    if sniffed is None:
        raise UnsupportedImageError(
            "图片格式不支持，仅支持 PNG / JPG / JPEG / WEBP。",
            detail={"declared_content_type": content_type},
        )

    declared = (content_type or "").split(";")[0].strip().lower()
    if declared and declared not in ALLOWED_IMAGE_TYPES:
        # Fall back to the extension before rejecting - some clients send
        # application/octet-stream for a perfectly good PNG.
        suffix = "" if not filename else filename.lower().rsplit(".", 1)[-1]
        if f".{suffix}" not in _EXTENSION_TO_MIME and declared != "application/octet-stream":
            raise UnsupportedImageError(
                "图片格式不支持，仅支持 PNG / JPG / JPEG / WEBP。",
                detail={"declared_content_type": declared},
            )

    mime = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}[sniffed]
    digest = hash_content(payload)
    # Only the hash and the size are ever logged - never the bytes.
    log_event(logger, "quick_capture.image_validated", mime=mime, bytes=len(payload), hash=digest[:12])
    return mime, digest


# --------------------------------------------------------------------------
# merging AI output into a candidate
# --------------------------------------------------------------------------


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def merge_ai_result(
    base: JobImportCandidate,
    ai: AIExtractedJob,
    *,
    method: ExtractionMethod,
) -> JobImportCandidate:
    """Overlay AI output on the deterministic result.

    The AI fills gaps and corrects free-text fields; the deterministic parser
    keeps the token-shaped fields (salary/experience/education) it matched with
    an unambiguous regex, because those are the ones a model is most likely to
    silently rewrite.
    """
    conf = base.confidence

    company = _clean(ai.company) or base.company
    title = _clean(ai.title) or base.title
    city = _clean(ai.city) or base.city

    salary = base.salary_text if conf.salary is Confidence.high else (
        _clean(ai.salary_text) or base.salary_text
    )
    experience = base.experience_text if conf.experience is Confidence.high else (
        _clean(ai.experience_text) or base.experience_text
    )
    education = base.education_text or _clean(ai.education_text)

    description = _clean(ai.raw_description) or base.raw_description

    warnings = [w for w in base.warnings if "未能识别职位标题" not in w or not title]
    for note in ai.notes:
        note = str(note).strip()
        if note and note not in warnings:
            warnings.append(note)
    if ai.partial_description:
        warnings.append("截图可能仅包含部分职位描述")

    merged = JobImportCandidate(
        source=base.source,
        source_url=base.source_url,
        company=company,
        title=title,
        city=city,
        salary_text=salary,
        experience_text=experience,
        education_text=education,
        raw_description=description,
        confidence=score_confidence(
            company=company,
            title=title,
            city=city,
            salary=salary,
            experience=experience,
            description=description or "",
            found_heading=True,  # the model was asked to isolate the JD body
        ),
        warnings=_dedupe(warnings),
        extraction_method=method,
    )
    return merged


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


# --------------------------------------------------------------------------
# text parsing
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ParseOutcome:
    candidate: JobImportCandidate
    ai_used: bool = False
    ai_available: bool = True
    ai_error: str | None = None
    message: str = ""


async def parse_text(
    text: str,
    *,
    source_url: str | None = None,
    allow_ai: bool = True,
    settings: Settings | None = None,
) -> ParseOutcome:
    """Deterministic parse first; one AI pass only when it is not good enough."""
    cfg = settings or get_settings()

    if not (text or "").strip():
        raise ValidationError("没有检测到职位文本，请粘贴职位内容后再解析。")
    if len((text or "").strip()) < MIN_TEXT_CHARS:
        raise ValidationError("职位文本太短，请复制完整的职位信息后再解析。")

    candidate, trace = parse_job_text(text, source_url=source_url)
    log_event(
        logger,
        "quick_capture.text_parsed",
        chars=len(text),
        nav_removed=trace.nav_lines_removed,
        confidence=candidate.confidence.overall.value,
        heading=trace.description_heading or "-",
    )

    ai_enabled = cfg.quick_capture_ai_extraction and cfg.openai_configured
    if not needs_ai_extraction(candidate):
        return ParseOutcome(
            candidate=candidate,
            ai_available=ai_enabled,
            message="已通过本地规则解析，未调用 AI。",
        )

    if not allow_ai:
        return ParseOutcome(
            candidate=candidate,
            ai_available=ai_enabled,
            message="解析结果可能不完整，请手动补充后保存。",
        )
    if not ai_enabled:
        reason = (
            "AI 提取已在设置中关闭"
            if not cfg.quick_capture_ai_extraction
            else "OpenAI API 未配置，可继续使用基础文字解析"
        )
        return ParseOutcome(
            candidate=candidate,
            ai_available=False,
            message=f"{reason}。请检查并手动补充下面的字段后保存。",
        )

    # One AI pass, cached on the exact input.
    from app.agents.job_import_agent import run_text_extraction

    key = extraction_cache_key(content_hash=hash_content(text), model=cfg.openai_model_fast)
    cached = cache_get(key)
    if cached is not None:
        log_event(logger, "quick_capture.ai_cache_hit", kind="text")
        return ParseOutcome(
            candidate=merge_ai_result(candidate, cached, method=ExtractionMethod.hybrid),
            ai_used=True,
            ai_available=True,
            message="已使用缓存的 AI 提取结果（未消耗额度）。",
        )

    try:
        ai_result, _model = await run_text_extraction(text, settings=cfg)
    except AppError as exc:
        log_event(logger, "quick_capture.ai_failed", kind="text", code=exc.code)
        return ParseOutcome(
            candidate=candidate,
            ai_available=True,
            ai_error=exc.message,
            message="AI 提取失败，已回退到本地解析结果，请手动检查后保存。",
        )

    cache_put(key, ai_result)
    return ParseOutcome(
        candidate=merge_ai_result(candidate, ai_result, method=ExtractionMethod.hybrid),
        ai_used=True,
        ai_available=True,
        message="已结合 AI 提取补全字段，请确认后保存。",
    )


# --------------------------------------------------------------------------
# image parsing
# --------------------------------------------------------------------------


async def parse_image(
    payload: bytes,
    *,
    content_type: str | None,
    filename: str | None = None,
    source_url: str | None = None,
    settings: Settings | None = None,
) -> ParseOutcome:
    """Extract a job from a screenshot. The image is never written to disk."""
    cfg = settings or get_settings()
    mime, digest = validate_image(
        payload, content_type=content_type, filename=filename, settings=cfg
    )

    if not cfg.quick_capture_ai_extraction:
        raise AIExtractionDisabledError(
            "图片识别需要 AI 提取，但它已在设置中关闭（QUICK_CAPTURE_AI_EXTRACTION=false）。"
            "请改用文字粘贴。",
            detail={"setting": "QUICK_CAPTURE_AI_EXTRACTION"},
        )
    if not cfg.openai_configured:
        raise AIExtractionDisabledError(
            "图片识别需要 OpenAI API，但尚未配置 OPENAI_API_KEY。请改用文字粘贴。",
            detail={"env_var": "OPENAI_API_KEY"},
        )

    from app.agents.job_import_agent import run_vision_extraction

    key = extraction_cache_key(content_hash=digest, model=cfg.openai_model_fast)
    cached = cache_get(key)
    if cached is not None:
        log_event(logger, "quick_capture.ai_cache_hit", kind="vision")
        ai_result = cached
        message = "已使用缓存的截图识别结果（未消耗额度）。"
    else:
        ai_result, _model = await run_vision_extraction(payload, mime, settings=cfg)
        cache_put(key, ai_result)
        message = "已从截图识别职位信息，请仔细核对后保存。"

    empty = JobImportCandidate(
        source=detect_source(source_url),
        source_url=canonical_url(source_url),
        warnings=["截图识别结果请务必人工核对"],
        extraction_method=ExtractionMethod.ai_vision,
    )
    candidate = merge_ai_result(empty, ai_result, method=ExtractionMethod.ai_vision)

    # The bytes go out of scope here; nothing is persisted but the hash.
    log_event(logger, "quick_capture.image_parsed", hash=digest[:12], has_title=bool(candidate.title))
    return ParseOutcome(candidate=candidate, ai_used=True, ai_available=True, message=message)


# --------------------------------------------------------------------------
# confirm -> the existing intake pipeline
# --------------------------------------------------------------------------


def confirm_candidate(db, candidate: JobImportCandidate) -> job_intake.IntakeResult:
    """Persist a user-confirmed candidate through the normal intake path.

    No normalization, hashing or duplicate detection happens here - that is all
    ``job_intake.save_posting`` / the v0.1 normalizer, exactly as for a pasted
    form or a browser capture.
    """
    title = (candidate.title or "").strip()
    description = (candidate.raw_description or "").strip()

    if not title:
        raise ValidationError("无法识别职位标题，请手动补充后保存。", detail={"field": "title"})
    if len(description) < MIN_TEXT_CHARS:
        raise ValidationError(
            "职位描述内容太少，请补充后再保存。", detail={"field": "raw_description"}
        )

    source = (candidate.source or "manual").strip() or "manual"
    posting = RawJobPosting(
        title=title,
        company=(candidate.company or "").strip(),
        raw_description=description,
        city=_clean(candidate.city),
        salary_text=_clean(candidate.salary_text),
        experience_text=_clean(candidate.experience_text),
        education_text=_clean(candidate.education_text),
        source_url=canonical_url(candidate.source_url),
        external_id=None,
    )

    outcome = job_intake.save_posting(
        db,
        posting,
        # normalize_job is the shared base implementation, so which source
        # instance we pass does not change the result - only the label does.
        source=manual_source,
        source_name=source,
        note=f"岗位已创建（快速采集 · {candidate.extraction_method.value}）",
    )
    log_event(
        logger,
        "quick_capture.confirmed",
        job_id=outcome.job.id,
        duplicate=outcome.duplicate,
        source=source,
        method=candidate.extraction_method.value,
    )
    return outcome
