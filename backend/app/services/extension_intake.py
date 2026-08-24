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

from app.core.errors import ValidationError
from app.core.logging import get_logger, log_event
from app.job_sources.base import RawJobPosting
from app.job_sources.manual import manual_source
from app.services import job_intake
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

    status: str  # new | duplicate | incomplete
    existing_job_id: int | None
    blocking_fields: list[str]
    warnings: list[str]


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
        salary_text=_clean(candidate.salary_text, limit=128),
        experience_text=_clean(candidate.experience_text, limit=64),
        education_text=_clean(candidate.education_text, limit=64),
        source_url=canonical_url(candidate.source_url),
        external_id=_clean(candidate.external_id, limit=128),
    )


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


def inspect(db: Session, candidate) -> CandidateVerdict:
    """Judge one candidate without touching the database's contents.

    Normalization and hashing run exactly as they would on save, so a
    ``new`` verdict here means the same thing a save would mean - but the
    session is only ever read from.
    """
    posting = to_posting(candidate)
    blocking = blocking_fields_of(posting)
    warnings: list[str] = []

    if not posting.company:
        warnings.append("没有识别到公司名称，导入后需要手动补充。")
    if not posting.salary_text:
        warnings.append("页面上没有可见的薪资。")

    if blocking:
        if "description" in blocking:
            warnings.append(
                "职位描述太短或缺失。搜索结果卡片没有描述 —— 请打开职位详情页再检测。"
            )
        return CandidateVerdict("incomplete", None, blocking, warnings)

    normalized = manual_source.normalize_job(posting)
    existing = job_intake.find_duplicate(
        db,
        content_hash=normalized.content_hash,
        source=source_name_for(candidate),
        external_id=posting.external_id,
    )
    if existing is not None:
        return CandidateVerdict("duplicate", existing.id, [], warnings)

    return CandidateVerdict("new", None, [], warnings)


def import_one(db: Session, candidate) -> job_intake.IntakeResult:
    """Save one candidate through the normal intake path.

    No normalization, hashing or duplicate detection happens here - it all
    belongs to ``job_intake.save_posting``, exactly as for a pasted form, a
    Quick Capture confirmation or a browser capture.
    """
    posting = to_posting(candidate)
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
        note="岗位已创建（Chrome 扩展检测）",
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
