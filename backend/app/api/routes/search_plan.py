"""M4e/M4f SearchPlan + bounded automatic runner endpoints - loopback only,
same guard ``/api/extension`` and ``/api/extension/sessions`` already use.

    POST /api/tasks/search-plan/generate       deterministic city x keyword generation
    GET  /api/tasks/search-plan                list SearchPlan tasks + run state/counters
    GET  /api/tasks/search-plan/{task_id}      one SearchPlan task + run state/counters
    POST /api/tasks/{task_id}/run/start        pending -> running
    POST /api/tasks/{task_id}/run/pause        running -> paused
    POST /api/tasks/{task_id}/run/verification running -> paused_verification
    POST /api/tasks/{task_id}/run/resume       paused(_verification) -> running
    POST /api/tasks/{task_id}/run/cancel       -> cancelled (idempotent)
    POST /api/tasks/{task_id}/run/fail         running -> failed
    POST /api/tasks/{task_id}/run/complete     running -> completed (cap/end, no violation)
    POST /api/tasks/{task_id}/run/round        record one already-performed scroll round
    POST /api/tasks/{task_id}/run/state        report observability fields (no state-machine change)

No route here navigates, scrolls, or clicks anything - the extension performs every real browser
step and only afterward tells this module what happened. See CLAUDE.md's "Chrome extension - M4
supervised navigation policy" M4e/M4f amendment.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.extension import require_loopback
from app.core.errors import NotFoundError
from app.db.session import get_db
from app.models import JobSearchTask
from app.schemas.search_plan import (
    DirectionAnalysisPlanOut,
    DirectionAnalysisRunRequest,
    DirectionChoiceOut,
    FailRunRequest,
    PauseRunRequest,
    RecordRoundRequest,
    ReportStateRequest,
    SearchPlanGenerateRequest,
    SearchPlanGenerateResponse,
    SearchPlanTaskListResponse,
    SearchPlanTaskOut,
    QuickSearchPrepareRequest,
    QuickSearchPrepareResponse,
    SearchPlanOptionsResponse,
    StartRunRequest,
    MatchStepRequest,
    AutoMatchReview,
)
from app.services.boss_cities import BOSS_CITY_IDS
from app.services import (
    boss_search_url,
    bounded_matching,
    direction_analysis,
    search_plan,
    search_task_runner,
    task_console,
)

router = APIRouter(prefix="/api/tasks", tags=["search-plan"])


def _task_out(task: JobSearchTask) -> SearchPlanTaskOut:
    search_url = (
        boss_search_url.build_search_url(task.city_id, task.keywords)
        if task.city_id and task.keywords
        else None
    )
    return SearchPlanTaskOut(
        id=task.id,
        max_candidates=task.max_candidates,
        name=task.name,
        city=task.city,
        city_id=task.city_id,
        keywords=task.keywords,
        early_career_policy=task.early_career_policy,
        search_url=search_url,
        run_status=task.run_status.value if task.run_status else None,
        run_started_at=task.run_started_at,
        run_stopped_at=task.run_stopped_at,
        observed_count=task.observed_count,
        new_count=task.new_count,
        duplicate_count=task.duplicate_count,
        no_new_rounds=task.no_new_rounds,
        last_error=task.last_error,
        current_url=task.current_url,
        scroll_round=task.scroll_round,
        visible_jobs=task.visible_jobs,
        imported_jobs=task.imported_jobs,
        current_candidate=task.current_candidate,
        last_action=task.last_action,
        paused_reason=task.paused_reason,
        updated_at=task.updated_at,
        task_id=task.id,
        state=task.run_status.value if task.run_status else None,
        keyword=task.keywords,
        observed_jobs=task.observed_count,
        new_jobs=task.new_count,
        duplicate_jobs=task.duplicate_count,
    )


@router.get("/search-plan/options", response_model=SearchPlanOptionsResponse)
def search_plan_options(request: Request) -> SearchPlanOptionsResponse:
    """Return only locally validated search capabilities; no browser action."""
    require_loopback(request)
    return SearchPlanOptionsResponse(
        supported_cities=list(BOSS_CITY_IDS),
        max_selected_cities=search_plan.MAX_SELECTED_CITIES,
        max_batch_tasks=search_plan.MAX_BATCH_TASKS,
    )


@router.post("/search-plan/generate", response_model=SearchPlanGenerateResponse)
def generate(
    request: Request,
    payload: SearchPlanGenerateRequest = Body(default=SearchPlanGenerateRequest()),
    db: Session = Depends(get_db),
) -> SearchPlanGenerateResponse:
    require_loopback(request)
    result = search_plan.generate_search_plan(db, cities=payload.cities, keywords=payload.keywords)
    return SearchPlanGenerateResponse(**result)


@router.post("/search-plan/quick-prepare", response_model=QuickSearchPrepareResponse)
def quick_prepare(
    request: Request,
    payload: QuickSearchPrepareRequest,
    db: Session = Depends(get_db),
) -> QuickSearchPrepareResponse:
    """Prepare one fresh, resume-bound bounded search. No BOSS or AI action."""
    require_loopback(request)
    tasks, resume_name, ranking = search_plan.prepare_resume_searches(
        db, cities=payload.cities, target_count=payload.target_count
    )
    return QuickSearchPrepareResponse(
        tasks=[_task_out(task) for task in tasks],
        active_resume_name=resume_name,
        # Chosen directions only, in the order they were used - the tail of the
        # ranking was not searched and would only be noise here.
        directions=[
            DirectionChoiceOut(
                keyword=d.keyword,
                reasons=d.reasons,
                jobs=d.jobs,
                recommended=d.recommended,
                recommend_rate=d.recommend_rate,
                has_evidence=d.has_evidence,
                fit=d.fit,
                fit_source=d.fit_source,
                suggested=d.suggested,
            )
            for d in ranking.directions
            if d.keyword in {task.keywords for task in tasks}
        ],
        direction_notes=ranking.notes,
        needs_more_evidence=ranking.needs_more_evidence,
    )


def _direction_plan_out(plan: direction_analysis.DirectionPlan) -> DirectionAnalysisPlanOut:
    result = plan.result
    return DirectionAnalysisPlanOut(
        resume_id=plan.resume_id,
        resume_name=plan.resume_name,
        model=plan.model,
        candidates=plan.candidates,
        cached=plan.cached,
        pending_calls=plan.pending_calls,
        openai_configured=plan.openai_configured,
        summary=result.summary if result else "",
        directions=[
            DirectionChoiceOut(
                keyword=item.keyword,
                reasons=[item.reason],
                fit=item.fit / 100,
                fit_source="ai",
                suggested=suggested,
            )
            for suggested, items in ((False, result.directions), (True, result.suggested))
            for item in items
        ]
        if result
        else [],
    )


@router.get("/search-plan/direction-plan", response_model=DirectionAnalysisPlanOut)
def direction_plan(request: Request, db: Session = Depends(get_db)) -> DirectionAnalysisPlanOut:
    """Pure read: what an AI direction analysis would cost, plus the cached one.

    Reading this never calls a model, so the console may load it on open.
    """
    require_loopback(request)
    return _direction_plan_out(direction_analysis.plan(db))


@router.post("/search-plan/direction-analyze", response_model=DirectionAnalysisPlanOut)
def direction_analyze(
    request: Request,
    payload: DirectionAnalysisRunRequest,
    db: Session = Depends(get_db),
) -> DirectionAnalysisPlanOut:
    """The one endpoint here that spends money, and only with `confirmed=true`.

    Exactly one call covering the whole résumé. A cached answer is returned
    without spending unless `force` is set.
    """
    require_loopback(request)
    return _direction_plan_out(
        direction_analysis.run(db, confirmed=payload.confirmed, force=payload.force)
    )


@router.get("/search-plan", response_model=SearchPlanTaskListResponse)
def list_search_plan(request: Request, db: Session = Depends(get_db)) -> SearchPlanTaskListResponse:
    require_loopback(request)
    tasks = db.scalars(
        select(JobSearchTask)
        .where(JobSearchTask.is_search_plan.is_(True))
        .order_by(JobSearchTask.id.asc())
    ).all()
    return SearchPlanTaskListResponse(items=[_task_out(t) for t in tasks])


@router.get("/search-plan/{task_id}", response_model=SearchPlanTaskOut)
def get_search_plan_task(
    request: Request, task_id: int, db: Session = Depends(get_db)
) -> SearchPlanTaskOut:
    require_loopback(request)
    task = task_console.get_task(db, task_id)
    if not task.is_search_plan:
        raise NotFoundError(
            f"任务 {task_id} 不是搜索计划任务", detail={"task_id": task_id}
        )
    return _task_out(task)


@router.post("/{task_id}/run/start", response_model=SearchPlanTaskOut)
def start(request: Request, task_id: int, payload: StartRunRequest = Body(default=StartRunRequest()),
          db: Session = Depends(get_db)) -> SearchPlanTaskOut:
    require_loopback(request)
    approval = bounded_matching.approve(db, task_id, **payload.match_approval.model_dump()) if payload.match_approval else None
    return _task_out(search_task_runner.start_run(db, task_id, match_approval=approval))


@router.get("/{task_id}/auto-match/quote")
def match_quote(request: Request, task_id: int, cap: int = Query(ge=1, le=3), db: Session = Depends(get_db)):
    require_loopback(request)
    return bounded_matching.quote(db, task_id, cap)


@router.post("/{task_id}/auto-match/step")
async def match_step(request: Request, task_id: int, payload: MatchStepRequest, db: Session = Depends(get_db)):
    require_loopback(request)
    return await bounded_matching.step(db, task_id, **payload.model_dump())


@router.get("/{task_id}/auto-match/review", response_model=AutoMatchReview)
def match_review(request: Request, task_id: int, db: Session = Depends(get_db)):
    require_loopback(request)
    return bounded_matching.review(db, task_id)


@router.post("/{task_id}/run/pause", response_model=SearchPlanTaskOut)
def pause(
    request: Request,
    task_id: int,
    payload: PauseRunRequest = Body(default=PauseRunRequest()),
    db: Session = Depends(get_db),
) -> SearchPlanTaskOut:
    require_loopback(request)
    return _task_out(search_task_runner.pause_run(db, task_id, reason=payload.reason))


@router.post("/{task_id}/run/verification", response_model=SearchPlanTaskOut)
def verification(request: Request, task_id: int, db: Session = Depends(get_db)) -> SearchPlanTaskOut:
    require_loopback(request)
    return _task_out(search_task_runner.enter_verification(db, task_id))


@router.post("/{task_id}/run/login-required", response_model=SearchPlanTaskOut)
def login_required(request: Request, task_id: int, db: Session = Depends(get_db)) -> SearchPlanTaskOut:
    require_loopback(request)
    return _task_out(search_task_runner.enter_login_required(db, task_id))


@router.post("/{task_id}/run/resume", response_model=SearchPlanTaskOut)
def resume(request: Request, task_id: int, db: Session = Depends(get_db)) -> SearchPlanTaskOut:
    require_loopback(request)
    return _task_out(search_task_runner.resume_run(db, task_id))


@router.post("/{task_id}/run/cancel", response_model=SearchPlanTaskOut)
def cancel(request: Request, task_id: int, db: Session = Depends(get_db)) -> SearchPlanTaskOut:
    require_loopback(request)
    return _task_out(search_task_runner.cancel_run(db, task_id))


@router.post("/{task_id}/run/fail", response_model=SearchPlanTaskOut)
def fail(
    request: Request,
    task_id: int,
    payload: FailRunRequest = Body(...),
    db: Session = Depends(get_db),
) -> SearchPlanTaskOut:
    require_loopback(request)
    return _task_out(search_task_runner.fail_run(db, task_id, error=payload.error))


@router.post("/{task_id}/run/complete", response_model=SearchPlanTaskOut)
def complete(request: Request, task_id: int, db: Session = Depends(get_db)) -> SearchPlanTaskOut:
    require_loopback(request)
    return _task_out(search_task_runner.complete_run(db, task_id))


@router.post("/{task_id}/run/round", response_model=SearchPlanTaskOut)
def round_(
    request: Request,
    task_id: int,
    payload: RecordRoundRequest = Body(...),
    db: Session = Depends(get_db),
) -> SearchPlanTaskOut:
    require_loopback(request)
    return _task_out(
        search_task_runner.record_round(
            db,
            task_id,
            observed=payload.observed,
            new=payload.new,
            duplicate=payload.duplicate,
            no_new_round_threshold=payload.no_new_round_threshold,
        )
    )


@router.post("/{task_id}/run/state", response_model=SearchPlanTaskOut)
def state(
    request: Request,
    task_id: int,
    payload: ReportStateRequest = Body(default=ReportStateRequest()),
    db: Session = Depends(get_db),
) -> SearchPlanTaskOut:
    require_loopback(request)
    return _task_out(
        search_task_runner.report_state(
            db,
            task_id,
            current_url=payload.current_url,
            scroll_round=payload.scroll_round,
            visible_jobs=payload.visible_jobs,
            imported_jobs=payload.imported_jobs,
            current_candidate=payload.current_candidate,
            last_action=payload.last_action,
        )
    )
