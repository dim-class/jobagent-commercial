"""The single path a job takes into the database.

Every source - manual paste (v0.1) and browser capture (v0.2) - funnels through
:func:`save_posting`, so normalization, hashing, duplicate detection and
persistence happen in exactly one place:

    RawJobPosting -> source.normalize_job() -> content_hash -> dedup -> Job

Callers decide how to *present* a duplicate. The manual-paste route raises a
409 (unchanged v0.1 semantics); browser capture returns the existing job so the
UI can offer to open it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger, log_event
from app.job_sources.base import JobSource, RawJobPosting
from app.models import ApplicationEvent, EventType, Job, JobAnalysis, JobStatus
from app.services.salary_text import is_valid_salary_text

logger = get_logger(__name__)


@dataclass(slots=True)
class IntakeResult:
    """Outcome of offering one posting to the database."""

    job: Job
    duplicate: bool
    enriched_fields: list[str] = field(default_factory=list)
    #: Cached analyses dropped because a scoring input they were computed from
    #: has since been filled in. Zero unless an enrichment actually happened.
    invalidated_analyses: int = 0

    @property
    def created(self) -> bool:
        return not self.duplicate


def find_duplicate(
    db: Session, *, content_hash: str, source: str, external_id: str | None
) -> Job | None:
    """Duplicate detection on both axes: content hash, then source + external id."""
    existing = db.scalar(select(Job).where(Job.content_hash == content_hash))
    if existing is None and external_id:
        existing = db.scalar(
            select(Job).where(Job.source == source, Job.external_id == external_id)
        )
    return existing


def save_posting(
    db: Session,
    posting: RawJobPosting,
    *,
    source: JobSource,
    source_name: str | None = None,
    note: str = "岗位已创建",
    enrich_missing_salary: bool = False,
) -> IntakeResult:
    """Normalize, de-duplicate and persist one posting.

    Returns the existing row with ``duplicate=True`` instead of raising, so the
    caller owns the HTTP semantics.
    """
    name = source_name or source.name
    normalized = source.normalize_job(posting)

    existing = find_duplicate(
        db,
        content_hash=normalized.content_hash,
        source=name,
        external_id=posting.external_id,
    )
    if existing is not None:
        enriched_fields: list[str] = []
        invalidated = 0
        # Canonical intake may fill one previously unreadable optional field
        # when the same stable posting is later revisited with stronger
        # evidence (for example local screenshot OCR). Never overwrite a salary
        # a human can already read; a stored obfuscated-font placeholder is not
        # one, so it may be replaced (see services/salary_text.py).
        if (
            enrich_missing_salary
            and not is_valid_salary_text(existing.salary_text)
            and normalized.salary_text
        ):
            existing.salary_text = normalized.salary_text
            enriched_fields.append("salary_text")
            # `analysis_cache_key` is built from `content_hash`, which covers
            # company + title + JD body only - a later salary never changes it.
            # Any analysis scored while the salary was unknown would therefore
            # be served from cache forever (scoring.py does read salary), so the
            # rows whose input just changed are dropped and the next explicit
            # 分析 recomputes. Only this job, and only on a real enrichment.
            stale = list(db.scalars(select(JobAnalysis).where(JobAnalysis.job_id == existing.id)))
            for analysis in stale:
                db.delete(analysis)
            invalidated = len(stale)
            db.add(
                ApplicationEvent(
                    job_id=existing.id,
                    event_type=EventType.note,
                    notes=(
                        "岗位薪资（此前缺失或无法识别）已由再次采集补充（需人工核对）"
                        + (f"；已作废 {invalidated} 条基于旧薪资的分析缓存，请重新分析" if invalidated else "")
                    ),
                )
            )
            db.commit()
            db.refresh(existing)
        log_event(
            logger,
            "job.duplicate_detected",
            existing_job_id=existing.id,
            hash=normalized.content_hash[:12],
            source=name,
            enriched_fields=enriched_fields,
            invalidated_analyses=invalidated,
        )
        return IntakeResult(
            job=existing,
            duplicate=True,
            enriched_fields=enriched_fields,
            invalidated_analyses=invalidated,
        )

    job = Job(
        source=name,
        external_id=posting.external_id,
        source_url=normalized.source_url,
        company=normalized.company,
        title=normalized.title,
        city=normalized.city,
        salary_text=normalized.salary_text,
        experience_text=normalized.experience_text,
        education_text=normalized.education_text,
        raw_description=normalized.raw_description,
        normalized_description=normalized.normalized_description,
        content_hash=normalized.content_hash,
        status=JobStatus.new,
    )
    db.add(job)
    db.flush()
    db.add(ApplicationEvent(job_id=job.id, event_type=EventType.note, notes=note))
    db.commit()
    db.refresh(job)

    log_event(
        logger,
        "job.created",
        job_id=job.id,
        source=job.source,
        city=job.city,
        jd_chars=len(job.normalized_description),
        hash=job.content_hash[:12],
    )
    return IntakeResult(job=job, duplicate=False)
