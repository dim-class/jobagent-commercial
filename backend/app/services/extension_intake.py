"""Turning what the Chrome extension read into JobAgent's own intake (POC).

Two operations, and the difference between them is the whole point:

``preview``  reads. It normalizes each candidate exactly as a real save would,
             computes the same content hash and asks the same duplicate
             question - and then writes nothing at all.

``import_``  writes, once, for one candidate, and only through
             :func:`app.services.job_intake.save_posting`. There is no second
             persistence path: normalization, hashing, dedup and the
             ``ApplicationEvent`` trail are the v0.1 ones, unchanged.

Nothing here fetches a URL or contacts a recruitment site. The extension hands
over fields a human already had on screen; a supplied URL is metadata, cleaned
by ``canonical_url()`` before it is stored.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.career_strategy import load_strategy
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.job_sources.base import RawJobPosting
from app.job_sources.manual import manual_source
from app.services import job_intake
from app.models import JobSearchTask
from app.services.job_eligibility import evaluate_early_career_policy
from app.services.salary_text import is_valid_salary_text, sanitize_salary_text
from app.services.urls import canonical_url, detect_source

logger = get_logger(__name__)

#: Below this, a "description" is a card teaser or a nav crumb, not a JD.
#: Matches the Quick Capture threshold so both intakes agree on what counts.
MIN_DESCRIPTION_CHARS = 60

#: What an import cannot proceed without.
REQUIRED_FIELDS = ("title", "description")


@dataclass(slots=True)
class CandidateVerdict:
    """A read-only judgement about one candidate."""

    status: str  # new | duplicate | incomplete | excluded
    existing_job_id: int | None
    blocking_fields: list[str]
    warnings: list[str]
    enrichable_fields: list[str]


def _clean(value: str | None, *, limit: int = 256) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split()).strip()
    if not text:
        return None
    return text[:limit]


def to_posting(candidate) -> RawJobPosting:
    """Map an extension candidate onto the shape every source produces.

    ``external_id`` is kept because BOSS puts a stable job id in the URL path,
    which gives duplicate detection a second axis beyond the content hash.
    """
    return RawJobPosting(
        title=_clean(candidate.title) or "",
        company=_clean(candidate.company) or "",
        raw_description=(candidate.description or "").strip(),
        city=_clean(candidate.city, limit=64),
        # Obfuscated-font placeholders (BOSS Private Use Area glyphs, shown
        # as boxes) are not a salary: dropped here so the candidate is treated
        # as missing a salary and stays eligible for OCR / backfill.
        salary_text=sanitize_salary_text(_clean(candidate.salary_text, limit=128)),
        experience_text=_clean(candidate.experience_text, limit=64),
        education_text=_clean(candidate.education_text, limit=64),
        source_url=canonical_url(candidate.source_url),
        external_id=_clean(candidate.external_id, limit=128),
    )


#: What the extension prefixes onto a salary-OCR miss. The reason is computed
#: in `supplementSalary` (extension/src/background.ts) and travels on the
#: candidate; it used to be dropped here, leaving a job with no salary and no
#: explanation anywhere.
SALARY_OCR_WARNING_PREFIX = "本地薪资 OCR 未采用："


def salary_ocr_note(candidate) -> str | None:
    """The extension's own reason for not filling a missing salary, if any."""
    for warning in getattr(candidate, "warnings", None) or []:
        text = str(warning)
        if text.startswith(SALARY_OCR_WARNING_PREFIX):
            return text[: 200]
    return None


def source_name_for(candidate) -> str:
    """``boss`` for a zhipin.com URL, ``manual`` when the URL says nothing."""
    return detect_source(canonical_url(candidate.source_url))


def blocking_fields_of(posting: RawJobPosting) -> list[str]:
    """Fields an import would refuse without. Everything else is optional."""
    missing: list[str] = []
    if not posting.title:
        missing.append("title")
    if len(posting.raw_description) < MIN_DESCRIPTION_CHARS:
        missing.append("description")
    return missing


def early_career_policy_for_task(db: Session, task_id: int | None) -> str:
    """Resolve the immutable task snapshot, or current policy for manual intake."""
    if task_id is None:
        return str(load_strategy()["early_career_policy"])
    task = db.get(JobSearchTask, task_id)
    if task is None or not task.is_search_plan:
        raise NotFoundError(f"搜索任务 {task_id} 不存在。", detail={"task_id": task_id})
    return task.early_career_policy


def inspect(db: Session, candidate, *, early_career_policy: str | None = None) -> CandidateVerdict:
    """Judge one candidate without touching the database's contents.

    Normalization and hashing run exactly as they would on save, so a
    ``new`` verdict here means the same thing a save would mean - but the
    session is only ever read from.
    """
    posting = to_posting(candidate)
    policy = early_career_policy or early_career_policy_for_task(db, None)
    eligibility = evaluate_early_career_policy(posting.title, posting.raw_description, policy)
    if not eligibility.eligible:
        return CandidateVerdict(
            "excluded", None, [], [eligibility.reason or "岗位不符合当前求职身份。"], []
        )
    blocking = blocking_fields_of(posting)
    warnings: list[str] = []

    if not posting.company:
        warnings.append("没有识别到公司名称，导入后需要手动补充。")
    ocr_note = salary_ocr_note(candidate)
    if ocr_note:
        warnings.append(ocr_note)
    if not posting.salary_text:
        warnings.append(
            "页面上没有可读的薪资（缺失或被站点字体混淆），可由本机截图 OCR 补充。"
            if _clean(candidate.salary_text, limit=128)
            else "页面上没有可见的薪资。"
        )

    if blocking:
        if "description" in blocking:
            warnings.append(
                "职位描述太短或缺失。搜索结果卡片没有描述 —— 请打开职位详情页再检测。"
            )
        return CandidateVerdict("incomplete", None, blocking, warnings, [])

    normalized = manual_source.normalize_job(posting)
    existing = job_intake.find_duplicate(
        db,
        content_hash=normalized.content_hash,
        source=source_name_for(candidate),
        external_id=posting.external_id,
    )
    if existing is not None:
        enrichable = (
            ["salary_text"]
            if not is_valid_salary_text(existing.salary_text) and posting.salary_text
            else []
        )
        return CandidateVerdict("duplicate", existing.id, [], warnings, enrichable)

    return CandidateVerdict("new", None, [], warnings, [])


def _creation_note(candidate) -> str:
    """Why this job's salary looks the way it does, recorded on its own trail.

    A missing salary with no stated reason is what made "为什么这批岗位没有薪资"
    unanswerable without reading the database.
    """
    selectors = getattr(candidate, "matched_selectors", {}) or {}
    if str(selectors.get("salary_text", "")).startswith("local_screenshot_ocr"):
        return "岗位已创建（Chrome 扩展检测；薪资由本机截图 OCR 补充，需人工核对）"
    ocr_note = salary_ocr_note(candidate)
    if ocr_note:
        return f"岗位已创建（Chrome 扩展检测；{ocr_note}，薪资待回填）"
    return "岗位已创建（Chrome 扩展检测）"


def import_one(
    db: Session, candidate, *, early_career_policy: str | None = None
) -> job_intake.IntakeResult:
    """Save one candidate through the normal intake path.

    No normalization, hashing or duplicate detection happens here - it all
    belongs to ``job_intake.save_posting``, exactly as for a pasted form, a
    Quick Capture confirmation or a browser capture.
    """
    posting = to_posting(candidate)
    policy = early_career_policy or early_career_policy_for_task(db, None)
    eligibility = evaluate_early_career_policy(posting.title, posting.raw_description, policy)
    if not eligibility.eligible:
        raise ValidationError(
            eligibility.reason or "岗位不符合当前求职身份，未导入。",
            detail={"reason": "early_career_track"},
        )
    blocking = blocking_fields_of(posting)
    if blocking:
        raise ValidationError(
            "这条岗位信息不完整，无法导入。请打开职位详情页重新检测，或手动补充后再保存。",
            detail={"fields": blocking},
        )

    source = source_name_for(candidate)
    outcome = job_intake.save_posting(
        db,
        posting,
        # ``normalize_job`` is the shared base implementation, so which source
        # instance is passed changes the label, never the result.
        source=manual_source,
        source_name=source,
        enrich_missing_salary=True,
        note=_creation_note(candidate),
    )
    # Ids, counts and lengths only. No title, no company, no URL with a query,
    # and never the description body.
    log_event(
        logger,
        "extension.job_imported",
        job_id=outcome.job.id,
        source=source,
        duplicate=outcome.duplicate,
        jd_chars=len(posting.raw_description),
    )
    return outcome
