"""Resume upload, listing, activation, and variants (v0.7).

``is_active`` here means **the resume new jobs are analysed against**. It is
not "the resume I apply with": what a given application actually used lives on
that application's cycle and is never inferred from this flag.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.career_strategy import load_strategy
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger, log_event
from app.db.session import get_db
from app.models import Resume
from app.schemas.analytics import ResumeCohortStat
from app.schemas.resume import (
    ResumeCloneRequest,
    ResumeDetail,
    ResumeListItem,
    ResumeUpdateRequest,
    ResumeUploadResponse,
)
from app.services import resume_analytics, resume_variants
from app.services.resume_parser import parse_resume

logger = get_logger(__name__)
router = APIRouter(prefix="/api/resumes", tags=["resumes"])

PREVIEW_CHARS = 4000


def _to_list_item(
    resume: Resume,
    *,
    stat: ResumeCohortStat | None = None,
    analyzed_jobs: int = 0,
) -> ResumeListItem:
    profile = resume.parsed_profile_json or {}
    return ResumeListItem(
        id=resume.id,
        filename=resume.filename,
        file_type=resume.file_type,
        is_active=resume.is_active,
        created_at=resume.created_at,
        updated_at=resume.updated_at,
        text_length=len(resume.raw_text or ""),
        skills=list(profile.get("skills") or [])[:40],
        variant_name=resume.variant_name,
        variant_group=resume.variant_group,
        parent_resume_id=resume.parent_resume_id,
        notes=resume.notes,
        archived_at=resume.archived_at,
        archived=resume.archived,
        label=resume.display_name,
        applications=stat.applications if stat else 0,
        mature_applications=stat.mature_applications if stat else 0,
        replies=stat.replies if stat else 0,
        interviews=stat.interviews if stat else 0,
        offers=stat.offers if stat else 0,
        analyzed_jobs=analyzed_jobs,
    )


def _to_detail(
    resume: Resume,
    *,
    stat: ResumeCohortStat | None = None,
    analyzed_jobs: int = 0,
) -> ResumeDetail:
    base = _to_list_item(resume, stat=stat, analyzed_jobs=analyzed_jobs).model_dump()
    return ResumeDetail(
        **base,
        content_hash=resume.content_hash,
        raw_text_preview=(resume.raw_text or "")[:PREVIEW_CHARS],
        parsed_profile=resume.parsed_profile_json or {},
    )


def _get_or_404(db: Session, resume_id: int) -> Resume:
    resume = db.get(Resume, resume_id)
    if resume is None:
        raise NotFoundError(f"简历 {resume_id} 不存在", detail={"resume_id": resume_id})
    return resume


def _activate(db: Session, resume: Resume) -> None:
    """Exactly one resume is active at a time."""
    for other in db.scalars(select(Resume).where(Resume.is_active.is_(True))):
        if other.id != resume.id:
            other.is_active = False
    resume.is_active = True


@router.get("", response_model=list[ResumeListItem])
def list_resumes(
    db: Session = Depends(get_db),
    include_archived: bool = Query(default=False, description="是否包含已归档版本"),
) -> list[ResumeListItem]:
    """Resume variants, each with how it actually performed.

    The performance numbers are deterministic counts over recorded outcomes -
    no AI call, so listing resumes stays free.
    """
    resumes = resume_variants.list_resumes(db, include_archived=include_archived)
    performance = resume_analytics.resume_performance_map(db)
    analyzed = resume_analytics.analyzed_job_counts(db)
    return [
        _to_list_item(r, stat=performance.get(r.id), analyzed_jobs=analyzed.get(r.id, 0))
        for r in resumes
    ]


@router.get("/active", response_model=ResumeDetail)
def get_active_resume(db: Session = Depends(get_db)) -> ResumeDetail:
    resume = db.scalar(select(Resume).where(Resume.is_active.is_(True)).limit(1))
    if resume is None:
        resume = db.scalar(select(Resume).order_by(Resume.created_at.desc()).limit(1))
    if resume is None:
        raise NotFoundError("尚未上传简历", detail={"action": "upload_resume"})
    return _to_detail(resume)


@router.post("/upload", response_model=ResumeUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_resume(
    file: UploadFile = File(..., description="PDF / DOCX / TXT 简历文件"),
    db: Session = Depends(get_db),
) -> ResumeUploadResponse:
    """Parse a resume locally (no AI) and make it the active resume."""
    payload = await file.read()
    if not payload:
        raise ValidationError("上传的文件为空")

    strategy = load_strategy()
    parsed = parse_resume(
        payload,
        file.filename or "resume",
        strategy_skills=list(strategy.get("relevant_skills") or []),
    )

    existing = db.scalar(select(Resume).where(Resume.content_hash == parsed.content_hash).limit(1))
    if existing is not None:
        # Same bytes as a resume already on file - re-activate instead of
        # creating a duplicate (and keep the analysis cache valid).
        _activate(db, existing)
        db.commit()
        db.refresh(existing)
        log_event(logger, "resume.duplicate_reactivated", resume_id=existing.id)
        return ResumeUploadResponse(
            resume=_to_detail(existing),
            reused_existing=True,
            message="该简历已存在，已重新设为当前简历",
        )

    resume = Resume(
        filename=file.filename or "resume",
        file_type=parsed.file_type,
        content_hash=parsed.content_hash,
        raw_text=parsed.raw_text,
        parsed_profile_json=parsed.profile,
        is_active=False,
    )
    db.add(resume)
    db.flush()
    _activate(db, resume)
    db.commit()
    db.refresh(resume)

    log_event(
        logger,
        "resume.uploaded",
        resume_id=resume.id,
        file_type=resume.file_type,
        chars=len(resume.raw_text),
    )
    return ResumeUploadResponse(
        resume=_to_detail(resume), reused_existing=False, message="简历上传并解析成功"
    )


@router.get("/{resume_id}", response_model=ResumeDetail)
def get_resume(resume_id: int, db: Session = Depends(get_db)) -> ResumeDetail:
    return _to_detail(_get_or_404(db, resume_id))


@router.post("/{resume_id}/activate", response_model=ResumeDetail)
def activate_resume(resume_id: int, db: Session = Depends(get_db)) -> ResumeDetail:
    """Switch the active resume. Analyses are cached per resume, so switching
    back and forth never re-spends tokens."""
    resume = _get_or_404(db, resume_id)
    _activate(db, resume)
    db.commit()
    db.refresh(resume)
    log_event(logger, "resume.activated", resume_id=resume.id)
    return _to_detail(resume)


# --------------------------------------------------------------------------
# variants (v0.7)
# --------------------------------------------------------------------------


@router.patch("/{resume_id}", response_model=ResumeDetail)
def update_resume(
    resume_id: int,
    payload: ResumeUpdateRequest = Body(...),
    db: Session = Depends(get_db),
) -> ResumeDetail:
    """Rename or re-tag a variant.

    Old events keep the name they recorded at the time - renaming a variant
    does not rewrite history, it just changes what we call it from now on.
    """
    resume = resume_variants.rename(
        db,
        resume_id,
        variant_name=payload.variant_name,
        variant_group=payload.variant_group,
        notes=payload.notes,
    )
    return _to_detail(resume)


@router.post("/{resume_id}/clone", response_model=ResumeDetail, status_code=status.HTTP_201_CREATED)
def clone_resume(
    resume_id: int,
    payload: ResumeCloneRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> ResumeDetail:
    """复制为新版本 - a metadata fork, not a resume editor.

    The clone is deliberately *not* activated: which resume analysis runs
    against stays an explicit choice.
    """
    request = payload or ResumeCloneRequest()
    resume = resume_variants.clone(
        db,
        resume_id,
        variant_name=request.variant_name,
        variant_group=request.variant_group,
        notes=request.notes,
    )
    return _to_detail(resume)


@router.post("/{resume_id}/archive", response_model=ResumeDetail)
def archive_resume(resume_id: int, db: Session = Depends(get_db)) -> ResumeDetail:
    """Retire a variant from new applications, keeping every historical number.

    There is no delete endpoint on purpose: a resume that history references
    must not disappear, or its applications would lose their attribution.
    """
    resume = resume_variants.archive(db, resume_id)
    return _to_detail(resume)


@router.post("/{resume_id}/unarchive", response_model=ResumeDetail)
def unarchive_resume(resume_id: int, db: Session = Depends(get_db)) -> ResumeDetail:
    resume = resume_variants.unarchive(db, resume_id)
    return _to_detail(resume)


@router.get("/{resume_id}/performance", response_model=ResumeDetail)
def resume_performance(resume_id: int, db: Session = Depends(get_db)) -> ResumeDetail:
    """One variant's real-world conversion. Deterministic, zero OpenAI calls."""
    resume = resume_variants.get_or_404(db, resume_id)
    performance = resume_analytics.resume_performance_map(db)
    analyzed = resume_analytics.analyzed_job_counts(db)
    return _to_detail(
        resume, stat=performance.get(resume.id), analyzed_jobs=analyzed.get(resume.id, 0)
    )
