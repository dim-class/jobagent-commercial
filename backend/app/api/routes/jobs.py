"""Job CRUD, filtering, and AI analysis endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.errors import DuplicateError, NotFoundError
from app.core.logging import get_logger, log_event
from app.db.session import get_db
from app.job_sources import manual_source
from app.models import (
    ApplicationEvent,
    EventType,
    Job,
    JobAnalysis,
    JobStatus,
    Resume,
    Verdict,
)
from app.schemas.analysis import (
    CompareResumesRequest,
    JobResumeAnalyses,
    ResumeAnalysisCell,
    ResumeComparisonResponse,
    AnalysisMeta,
    AnalysisResponse,
    AnalyzeRequest,
    BatchAnalyzeItem,
    BatchAnalyzePlanRequest,
    BatchAnalyzePlanResponse,
    BatchAnalyzeRequest,
    BatchAnalyzeResponse,
)
from app.schemas.common import MessageResponse
from app.schemas.job import (
    AnalysisSummary,
    JobCreate,
    JobCreateResponse,
    JobDetail,
    JobListItem,
    JobListResponse,
    JobUpdate,
)
from app.services import (
    application_workflow,
    job_intake,
    job_matcher,
    resume_comparison,
    task_matching,
)
from app.services.application_cycles import effective_cycle
from app.services.job_eligibility import classify_non_experienced_track
from app.services.job_normalizer import normalize_city
from app.services.scoring import extract_experience_requirement

logger = get_logger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

PREVIEW_CHARS = 220


# --------------------------------------------------------------------------
# serialisation helpers
# --------------------------------------------------------------------------


def _analysis_summary(analysis: JobAnalysis | None) -> AnalysisSummary | None:
    if analysis is None:
        return None
    payload = analysis.result_json or {}
    return AnalysisSummary(
        analysis_id=analysis.id,
        overall_score=analysis.overall_score,
        verdict=analysis.verdict,
        model=analysis.model,
        created_at=analysis.created_at,
        reasoning_summary=str(payload.get("reasoning_summary") or ""),
    )


def _pick_latest(job: Job) -> JobAnalysis | None:
    if not job.analyses:
        return None
    return max(job.analyses, key=lambda a: (a.created_at, a.id))


def _to_list_item(job: Job) -> JobListItem:
    preview = (job.normalized_description or "").strip().replace("\n", " ")
    return JobListItem(
        id=job.id,
        source=job.source,
        source_url=job.source_url,
        company=job.company,
        title=job.title,
        city=job.city,
        salary_text=job.salary_text,
        experience_text=job.experience_text,
        education_text=job.education_text,
        status=job.status,
        created_at=job.created_at,
        updated_at=job.updated_at,
        description_preview=preview[:PREVIEW_CHARS],
        latest_analysis=_analysis_summary(_pick_latest(job)),
    )


def _to_detail(job: Job) -> JobDetail:
    base = _to_list_item(job).model_dump()
    return JobDetail(
        **base,
        raw_description=job.raw_description,
        normalized_description=job.normalized_description,
        content_hash=job.content_hash,
        external_id=job.external_id,
        events=[
            {"id": e.id, "event_type": e.event_type.value, "notes": e.notes, "created_at": e.created_at}
            for e in job.events[:30]
        ],
    )


def _get_job_or_404(db: Session, job_id: int) -> Job:
    job = db.scalar(
        select(Job)
        .options(selectinload(Job.analyses), selectinload(Job.events))
        .where(Job.id == job_id)
    )
    if job is None:
        raise NotFoundError(f"岗位 {job_id} 不存在", detail={"job_id": job_id})
    return job


# --------------------------------------------------------------------------
# list / create
# --------------------------------------------------------------------------


@router.get("", response_model=JobListResponse)
def list_jobs(
    db: Session = Depends(get_db),
    city: str | None = Query(default=None, description="城市筛选"),
    min_score: int | None = Query(default=None, ge=0, le=100, description="最低匹配分"),
    verdict: Verdict | None = Query(default=None),
    job_status: JobStatus | None = Query(default=None, alias="status"),
    status_in: list[JobStatus] | None = Query(
        default=None,
        description="按一组状态筛选，可重复。「已投递」是一个阶段而不是终点，"
        "投递之后的 replied/interview/offer/rejected 仍然是投过的岗位。",
    ),
    keyword: str | None = Query(default=None, description="公司/职位/JD 关键词"),
    analyzed: bool | None = Query(default=None, description="是否已分析"),
    early_career_cleanup: bool = Query(
        default=False,
        description="只列出尚未决定、且被确定性规则识别为应届/校招/实习的历史岗位",
    ),
    max_required_years: int | None = Query(
        default=None,
        ge=0,
        le=20,
        description="只保留最低经验要求不超过该年数的岗位。"
        "无法识别经验要求的岗位一律保留——「读不出来」不是「要求很高」。",
    ),
    sort: str = Query(default="score", pattern="^(score|created_at|company)$"),
    # The cap is generous because the work is already O(all jobs): every
    # matching row is loaded and sorted in Python before this slice, so a
    # higher limit only adds serialization (~1.3 KB/row over loopback). A
    # local-first job hunt reaching 2000 rows is the point at which paging
    # earns its complexity - until then, showing the whole library is the
    # honest default, and 全选 covering only page one was a real trap.
    limit: int = Query(default=50, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> JobListResponse:
    """Filterable job list. Score filtering uses each job's latest analysis."""
    stmt = select(Job).options(selectinload(Job.analyses))
    # Facets drive the city/status dropdowns, so they are counted over the rows
    # matching everything *except* those two dimensions. Counting them over the
    # already-filtered rows leaves each menu holding only the value you picked,
    # with no way back to the others.
    facet_stmt = select(Job)

    if city:
        normalized = normalize_city(city)
        stmt = stmt.where(or_(Job.city == city, Job.city == normalized))
    if job_status:
        stmt = stmt.where(Job.status == job_status)
    if status_in:
        stmt = stmt.where(Job.status.in_(status_in))
    if keyword:
        like = f"%{keyword.strip()}%"
        matches_keyword = or_(
            Job.title.ilike(like),
            Job.company.ilike(like),
            Job.normalized_description.ilike(like),
        )
        stmt = stmt.where(matches_keyword)
        facet_stmt = facet_stmt.where(matches_keyword)

    jobs = list(db.scalars(stmt).unique())

    # Score/verdict/analyzed filters need the latest analysis per job, which is
    # cheap to resolve in Python at local-first data volumes (hundreds of rows).
    rows = [(job, _pick_latest(job)) for job in jobs]
    if early_career_cleanup:
        open_statuses = {JobStatus.new, JobStatus.reviewed, JobStatus.saved}
        rows = [
            (job, analysis)
            for job, analysis in rows
            if job.status in open_statuses
            and not classify_non_experienced_track(
                job.title, job.normalized_description
            ).eligible
        ]
    if max_required_years is not None:
        # Deterministic, free, and reusing the same extractor the scoring
        # pipeline already trusts - there is no second definition of "how many
        # years does this ask for".
        #
        # A job whose requirement cannot be read is kept. Excluding it would
        # treat "unknown" as "too senior", which is the mistake this codebase
        # avoids everywhere else: missing is not a value.
        kept: list[tuple[Job, JobAnalysis | None]] = []
        for job, analysis in rows:
            requirement = extract_experience_requirement(
                job.experience_text or "", job.normalized_description or ""
            )
            minimum = requirement.min_years
            if minimum is None or requirement.unlimited or minimum <= max_required_years:
                kept.append((job, analysis))
        rows = kept
    if analyzed is not None:
        rows = [(j, a) for j, a in rows if (a is not None) == analyzed]
    if min_score is not None:
        rows = [(j, a) for j, a in rows if a is not None and a.overall_score >= min_score]
    if verdict is not None:
        rows = [(j, a) for j, a in rows if a is not None and a.verdict == verdict]

    if sort == "score":
        rows.sort(key=lambda r: (r[1].overall_score if r[1] else -1, r[0].created_at), reverse=True)
    elif sort == "company":
        rows.sort(key=lambda r: (r[0].company or "", r[0].title or ""))
    else:
        rows.sort(key=lambda r: r[0].created_at, reverse=True)

    total = len(rows)
    page = rows[offset : offset + limit]

    cities: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for job in db.scalars(facet_stmt).unique():
        if job.city:
            cities[job.city] = cities.get(job.city, 0) + 1
        statuses[job.status.value] = statuses.get(job.status.value, 0) + 1

    return JobListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[_to_list_item(job) for job, _ in page],
        facets={
            "cities": dict(sorted(cities.items(), key=lambda kv: -kv[1])),
            "statuses": statuses,
        },
    )


@router.post("", response_model=JobCreateResponse, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreate, db: Session = Depends(get_db)) -> JobCreateResponse:
    """Create a job from a manually pasted JD.

    Duplicates are rejected with 409 and the id of the job already on file, so
    the UI can offer to open it instead of silently creating a twin.
    """
    posting = manual_source.from_form(
        title=payload.title,
        company=payload.company,
        raw_description=payload.raw_description,
        city=payload.city,
        salary_text=payload.salary_text,
        experience_text=payload.experience_text,
        education_text=payload.education_text,
        source_url=payload.source_url,
        external_id=payload.external_id,
    )
    # Shared intake path - identical to the one browser capture uses.
    outcome = job_intake.save_posting(
        db,
        posting,
        source=manual_source,
        source_name=payload.source or "manual",
        note="岗位已创建（手动粘贴 JD）",
    )
    if outcome.duplicate:
        existing = outcome.job
        raise DuplicateError(
            f"该岗位已存在（{existing.company} · {existing.title}）",
            detail={"existing_job_id": existing.id},
        )

    return JobCreateResponse(job=_to_detail(outcome.job), duplicate=False, message="岗位创建成功")


# --------------------------------------------------------------------------
# batch analysis (registered before /{job_id} so the path is not swallowed)
# --------------------------------------------------------------------------


@router.post("/analyze-batch/plan", response_model=BatchAnalyzePlanResponse)
def analyze_batch_plan(
    payload: BatchAnalyzePlanRequest, db: Session = Depends(get_db)
) -> BatchAnalyzePlanResponse:
    """What analyzing these exact jobs would cost. Pure read - spends nothing.

    Reuses ``task_matching.plan_jobs``: the same active resume, the same fast
    model and the same ``JobAnalysis`` cache key the batch run itself uses, so
    the numbers shown before the confirmation are the numbers that will apply.
    """
    settings = get_settings()
    limit = settings.max_analyses_per_run

    seen: set[int] = set()
    ordered: list[int] = []
    for job_id in payload.job_ids:
        if job_id not in seen:
            seen.add(job_id)
            ordered.append(job_id)

    found = {
        job.id: job
        for job in db.scalars(select(Job).where(Job.id.in_(ordered))).unique()
    } if ordered else {}
    jobs = [found[job_id] for job_id in ordered if job_id in found]
    missing = [job_id for job_id in ordered if job_id not in found]

    # ``analyze_batch`` truncates to the cap, so the plan reports cache state
    # for exactly the prefix that batch would process.
    in_batch = jobs[:limit]
    resume, model, plans = task_matching.plan_jobs(db, in_batch, settings=settings)
    pending = sum(1 for plan in plans if plan.needs_api_call)

    log_event(
        logger,
        "analysis.batch_planned",
        selected=len(jobs),
        in_batch=len(in_batch),
        cached=len(plans) - pending,
        pending=pending,
        limit=limit,
    )
    return BatchAnalyzePlanResponse(
        selected=len(jobs),
        limit=limit,
        in_batch=len(in_batch),
        deferred=len(jobs) - len(in_batch),
        cached=len(plans) - pending,
        pending=pending,
        model=model,
        resume_id=resume.id,
        resume_name=resume.display_name,
        missing_job_ids=missing,
    )


@router.post("/analyze-batch", response_model=BatchAnalyzeResponse)
async def analyze_batch(
    payload: BatchAnalyzeRequest, db: Session = Depends(get_db)
) -> BatchAnalyzeResponse:
    """Analyze many jobs, capped by ``MAX_ANALYSES_PER_RUN`` for cost control."""
    settings = get_settings()
    limit = settings.max_analyses_per_run

    if payload.job_ids:
        # Same de-duplication the plan endpoint applies, so the confirmed
        # numbers and the executed run describe the same set of jobs.
        job_ids = list(dict.fromkeys(payload.job_ids))
    else:
        candidates = list(db.scalars(select(Job).options(selectinload(Job.analyses))).unique())
        job_ids = [j.id for j in candidates if payload.force or _pick_latest(j) is None]

    requested = len(job_ids)
    job_ids = job_ids[:limit]

    items: list[BatchAnalyzeItem] = []
    analyzed = cached = failed = 0
    for job_id in job_ids:
        try:
            outcome = await job_matcher.analyze_job(
                db,
                job_id,
                force=payload.force,
                use_smart_model=payload.use_smart_model,
                settings=settings,
            )
        except Exception as exc:  # noqa: BLE001 - one bad job must not kill the run
            failed += 1
            message = getattr(exc, "message", None) or str(exc)
            items.append(BatchAnalyzeItem(job_id=job_id, ok=False, error=message))
            log_event(logger, "analysis.batch_item_failed", job_id=job_id, error=type(exc).__name__)
            continue
        if outcome.cached:
            cached += 1
        else:
            analyzed += 1
        items.append(
            BatchAnalyzeItem(
                job_id=job_id,
                ok=True,
                cached=outcome.cached,
                overall_score=outcome.result.overall_score,
                verdict=outcome.result.verdict,
            )
        )

    log_event(
        logger,
        "analysis.batch_completed",
        requested=requested,
        analyzed=analyzed,
        cached=cached,
        failed=failed,
        limit=limit,
    )
    return BatchAnalyzeResponse(
        requested=requested,
        analyzed=analyzed,
        cached=cached,
        failed=failed,
        limit=limit,
        items=items,
    )


# --------------------------------------------------------------------------
# single job
# --------------------------------------------------------------------------


@router.get("/{job_id}", response_model=JobDetail)
def get_job(job_id: int, db: Session = Depends(get_db)) -> JobDetail:
    job = _get_job_or_404(db, job_id)
    return _to_detail(job)


@router.patch("/{job_id}", response_model=JobDetail)
def update_job(job_id: int, payload: JobUpdate, db: Session = Depends(get_db)) -> JobDetail:
    """Update editable fields and/or move the job along the pipeline."""
    job = _get_job_or_404(db, job_id)
    data = payload.model_dump(exclude_unset=True)
    note = data.pop("note", None)
    new_status = data.pop("status", None)

    for field, value in data.items():
        if field == "city":
            value = normalize_city(value)
        setattr(job, field, value)

    if note:
        db.add(ApplicationEvent(job_id=job.id, event_type=EventType.note, notes=note))
    db.commit()

    # Status changes are never applied here: the workflow service owns every
    # transition and its audit trail (see CLAUDE.md).
    if new_status is not None and new_status != job.status:
        application_workflow.set_status(db, job.id, new_status)

    db.refresh(job)
    return _get_job_or_404(db, job_id)


@router.delete("/{job_id}", response_model=MessageResponse)
def delete_job(job_id: int, db: Session = Depends(get_db)) -> MessageResponse:
    job = _get_job_or_404(db, job_id)
    db.delete(job)
    db.commit()
    log_event(logger, "job.deleted", job_id=job_id)
    return MessageResponse(message="岗位已删除", detail={"job_id": job_id})


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------


def _analysis_response(outcome: job_matcher.AnalysisOutcome) -> AnalysisResponse:
    a = outcome.analysis
    return AnalysisResponse(
        meta=AnalysisMeta(
            analysis_id=a.id,
            job_id=a.job_id,
            resume_id=a.resume_id,
            model=a.model,
            prompt_version=a.prompt_version,
            cache_key=a.cache_key,
            cached=outcome.cached,
            created_at=a.created_at,
        ),
        result=outcome.result,
        pre_analysis=outcome.pre_analysis,
    )


@router.post("/{job_id}/analyze", response_model=AnalysisResponse)
async def analyze_job(
    job_id: int,
    payload: AnalyzeRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> AnalysisResponse:
    """Analyze with the fast (bulk) model. Cached unless ``force`` is set."""
    req = payload or AnalyzeRequest()
    outcome = await job_matcher.analyze_job(
        db, job_id, force=req.force, use_smart_model=req.use_smart_model
    )
    return _analysis_response(outcome)


@router.post("/{job_id}/analyze-with-resume/{resume_id}", response_model=AnalysisResponse)
async def analyze_with_resume(
    job_id: int,
    resume_id: int,
    payload: AnalyzeRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> AnalysisResponse:
    """按其他简历分析 - score this JD against one specific variant.

    Explicit, single-variant, and cache-aware: the cache key already covers
    resume content, so re-checking a variant that was analysed before costs
    nothing. This does not change the active analysis resume, and it says
    nothing about which resume the user will actually apply with.
    """
    req = payload or AnalyzeRequest()
    outcome = await job_matcher.analyze_job(
        db,
        job_id,
        force=req.force,
        use_smart_model=req.use_smart_model,
        resume_id=resume_id,
    )
    return _analysis_response(outcome)


@router.get("/{job_id}/resume-analyses", response_model=JobResumeAnalyses)
def job_resume_analyses(job_id: int, db: Session = Depends(get_db)) -> JobResumeAnalyses:
    """Existing scores for this job, one per variant. Never calls the model."""
    job = _get_job_or_404(db, job_id)
    resumes = {r.id: r for r in db.scalars(select(Resume))}
    latest = resume_comparison.latest_by_resume(resume_comparison.analyses_for_job(db, job_id))

    cells = [
        ResumeAnalysisCell(
            resume_id=resume_id,
            label=resumes[resume_id].display_name if resume_id in resumes else f"#{resume_id}",
            archived=bool(resume_id in resumes and resumes[resume_id].archived),
            overall_score=analysis.overall_score,
            verdict=analysis.verdict,
            analysis_id=analysis.id,
            created_at=analysis.created_at,
        )
        for resume_id, analysis in latest.items()
    ]
    cells.sort(key=lambda c: (c.overall_score or -1), reverse=True)

    cycle = effective_cycle(job)
    active = db.scalar(select(Resume).where(Resume.is_active.is_(True)).limit(1))
    return JobResumeAnalyses(
        job_id=job_id,
        cells=cells,
        applied_resume_id=cycle.resume_id if cycle else None,
        active_analysis_resume_id=active.id if active else None,
    )


@router.post("/{job_id}/compare-resumes", response_model=ResumeComparisonResponse)
async def compare_resumes(
    job_id: int,
    payload: CompareResumesRequest = Body(...),
    db: Session = Depends(get_db),
) -> ResumeComparisonResponse:
    """比较简历 - score several variants against one JD.

    **May spend OpenAI credits.** Only combinations with no cached analysis are
    sent to the model, and only when ``confirmed=true``. Sending
    ``confirmed=false`` first is the supported way to ask "what would this
    cost?": the error carries the pending count so the UI can warn before
    anything is spent.
    """
    _get_job_or_404(db, job_id)
    # Snapshot what is missing *before* running: run_comparison fills the
    # plans in, after which "did this cost anything?" is no longer visible.
    _, planned = resume_comparison.plan_comparison(
        db, job_id, payload.resume_ids, use_smart_model=payload.use_smart_model
    )
    pending_ids = {p.resume.id for p in planned if p.needs_api_call}
    pending = len(pending_ids)

    job, plans = await resume_comparison.run_comparison(
        db,
        job_id,
        payload.resume_ids,
        confirmed=payload.confirmed,
        use_smart_model=payload.use_smart_model,
    )

    cells = [
        ResumeAnalysisCell(
            resume_id=plan.resume.id,
            label=plan.resume.display_name,
            archived=plan.resume.archived,
            overall_score=plan.cached.overall_score if plan.cached else None,
            verdict=plan.cached.verdict if plan.cached else None,
            analysis_id=plan.cached.id if plan.cached else None,
            created_at=plan.cached.created_at if plan.cached else None,
            newly_analyzed=plan.resume.id in pending_ids,
        )
        for plan in plans
    ]
    cells.sort(key=lambda c: (c.overall_score or -1), reverse=True)

    return ResumeComparisonResponse(
        job_id=job_id,
        company=job.company,
        title=job.title,
        cells=cells,
        pending_analyses=0,
        api_calls_made=pending,
        message=(
            f"已完成 {len(cells)} 份简历的对比"
            + (f"，其中 {pending} 份是本次新分析的。" if pending else "，全部来自缓存，未产生费用。")
        ),
    )


@router.post("/{job_id}/reanalyze-smart", response_model=AnalysisResponse)
async def reanalyze_smart(
    job_id: int,
    force: bool = Query(default=False, description="连高质量模型的缓存也一并忽略"),
    db: Session = Depends(get_db),
) -> AnalysisResponse:
    """Re-analyze with the higher-quality model (``OPENAI_MODEL_SMART``).

    Never called automatically - the fast model handles bulk analysis and this
    is an explicit, user-initiated upgrade for jobs worth a second opinion.
    """
    outcome = await job_matcher.analyze_job(db, job_id, force=force, use_smart_model=True)
    return _analysis_response(outcome)


@router.get("/{job_id}/analysis", response_model=AnalysisResponse)
def get_analysis(job_id: int, db: Session = Depends(get_db)) -> AnalysisResponse:
    """Latest stored analysis for a job, or 404 if it has never been analyzed."""
    _get_job_or_404(db, job_id)
    analysis = job_matcher.latest_analysis(db, job_id)
    if analysis is None:
        raise NotFoundError("该岗位尚未进行 AI 分析", detail={"job_id": job_id})
    return AnalysisResponse(
        meta=AnalysisMeta(
            analysis_id=analysis.id,
            job_id=analysis.job_id,
            resume_id=analysis.resume_id,
            model=analysis.model,
            prompt_version=analysis.prompt_version,
            cache_key=analysis.cache_key,
            cached=True,
            created_at=analysis.created_at,
        ),
        result=job_matcher.result_from_row(analysis),
        pre_analysis=job_matcher.pre_analysis_from_row(analysis),
    )
