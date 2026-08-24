"""Resume variants: naming, cloning, archiving, per-variant performance (v0.7).

A Resume row *is* a variant. This module owns the small amount of lifecycle
logic that goes with that, and the one query analytics needs: how each variant
actually performed on real applications.

Two rules the rest of the codebase depends on:

* **archiving is not deleting.** An archived variant stays in every historical
  number forever; it merely stops being offered for new applications. A resume
  that any application references must never be deleted - the FK on
  ``application_events.resume_id`` is ``RESTRICT`` precisely so that a mistake
  here fails loudly instead of silently erasing attribution;
* **nothing here reads ``is_active`` to decide what a past application used.**
  Attribution comes from the event trail, always.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.models import ApplicationEvent, Job, Resume, ResumeUsage
from app.services.application_cycles import ApplicationCycle, build_cycles, effective_cycle

logger = get_logger(__name__)

MAX_VARIANT_NAME = 128


def get_or_404(db: Session, resume_id: int) -> Resume:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise NotFoundError(f"简历 {resume_id} 不存在", detail={"resume_id": resume_id})
    return resume


def list_resumes(db: Session, *, include_archived: bool = False) -> list[Resume]:
    stmt = select(Resume)
    if not include_archived:
        stmt = stmt.where(Resume.archived_at.is_(None))
    return list(
        db.scalars(stmt.order_by(Resume.is_active.desc(), Resume.created_at.desc()))
    )


def clean_variant_name(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > MAX_VARIANT_NAME:
        raise ValidationError(
            f"简历名称最长 {MAX_VARIANT_NAME} 个字符。", detail={"field": "variant_name"}
        )
    return text


def rename(
    db: Session,
    resume_id: int,
    *,
    variant_name: str | None = None,
    variant_group: str | None = None,
    notes: str | None = None,
) -> Resume:
    """Rename / re-tag a variant. Content is never touched.

    Renaming does not rewrite the name snapshots stored on old events - those
    record what the variant was called at the time, which is the point.
    """
    resume = get_or_404(db, resume_id)
    if variant_name is not None:
        resume.variant_name = clean_variant_name(variant_name)
    if variant_group is not None:
        resume.variant_group = (variant_group or "").strip() or None
    if notes is not None:
        resume.notes = (notes or "").strip() or None
    db.commit()
    db.refresh(resume)
    log_event(logger, "resume.renamed", resume_id=resume.id)
    return resume


def clone(
    db: Session,
    resume_id: int,
    *,
    variant_name: str | None = None,
    variant_group: str | None = None,
    notes: str | None = None,
) -> Resume:
    """Fork a variant into a new Resume row.

    Metadata/versioning only - this copies the parsed content as-is so the user
    can track a new variant. v0.7 does not edit resume content, and nothing
    here rewrites a single word of it.

    The clone is **not** activated: choosing the analysis resume stays an
    explicit action, and the clone is not yet a different document.
    """
    source = get_or_404(db, resume_id)
    name = clean_variant_name(variant_name) or f"{source.display_name} 副本"

    clone_row = Resume(
        filename=source.filename,
        file_type=source.file_type,
        # Same bytes, so the same content hash - which is exactly right: the
        # analysis cache keys off content, and identical content must reuse it
        # rather than re-spend tokens.
        content_hash=source.content_hash,
        raw_text=source.raw_text,
        parsed_profile_json=dict(source.parsed_profile_json or {}),
        is_active=False,
        variant_name=name,
        variant_group=(variant_group or "").strip() or source.variant_group,
        parent_resume_id=source.id,
        notes=(notes or "").strip() or None,
    )
    db.add(clone_row)
    db.commit()
    db.refresh(clone_row)
    log_event(logger, "resume.cloned", resume_id=clone_row.id, parent_id=source.id)
    return clone_row


def archive(db: Session, resume_id: int) -> Resume:
    """Retire a variant from new applications, keeping all of its history.

    The active analysis resume cannot be archived - something has to be
    analysed against, and silently reassigning that is not this function's
    call to make.
    """
    resume = get_or_404(db, resume_id)
    if resume.archived:
        return resume
    if resume.is_active:
        raise ValidationError(
            "不能归档当前的 AI 分析简历。请先把另一份简历设为「当前AI分析简历」。",
            detail={"resume_id": resume_id, "is_active": True},
        )
    resume.archived_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(resume)
    log_event(logger, "resume.archived", resume_id=resume.id)
    return resume


def unarchive(db: Session, resume_id: int) -> Resume:
    resume = get_or_404(db, resume_id)
    resume.archived_at = None
    db.commit()
    db.refresh(resume)
    log_event(logger, "resume.unarchived", resume_id=resume.id)
    return resume


def referencing_application_count(db: Session, resume_id: int) -> int:
    """How many recorded events point at this resume. Guards deletion."""
    return len(
        list(db.scalars(select(ApplicationEvent).where(ApplicationEvent.resume_id == resume_id)))
    )


# --------------------------------------------------------------------------
# per-variant performance
# --------------------------------------------------------------------------


def effective_cycles_by_resume(jobs: list[Job]) -> dict[int | None, list[ApplicationCycle]]:
    """Group each job's effective cycle by the resume it actually used.

    The ``None`` bucket holds unattributed applications. They are deliberately
    kept - dropping them would quietly inflate every variant's denominator, and
    assigning them to the active resume would be a lie.
    """
    buckets: dict[int | None, list[ApplicationCycle]] = {}
    for job in jobs:
        cycle = effective_cycle(job)
        if cycle is None:
            continue
        key = cycle.resume_id if cycle.resume_usage is ResumeUsage.used else None
        buckets.setdefault(key, []).append(cycle)
    return buckets


def resume_label_map(db: Session) -> dict[int, str]:
    """id -> current display name, for labelling analytics rows."""
    return {r.id: r.display_name for r in db.scalars(select(Resume))}


def unattributed_applications(db: Session, jobs: list[Job]) -> list[dict[str, Any]]:
    """Applications whose resume was never recorded, for the backfill UI.

    Returns the facts a human needs to remember what they did - company, title,
    when - and nothing more. Never a suggested answer.
    """
    rows: list[dict[str, Any]] = []
    for job in jobs:
        for cycle in build_cycles(job):
            if cycle.superseded or cycle.resume_attributed:
                continue
            rows.append(
                {
                    "job_id": job.id,
                    "applied_event_id": cycle.applied_event_id,
                    "company": job.company,
                    "title": job.title,
                    "city": job.city,
                    "applied_at": cycle.applied_at,
                }
            )
    rows.sort(key=lambda r: (r["applied_at"], r["job_id"]), reverse=True)
    return rows
