"""M5b loopback-only plan/run endpoints; no browser or application actions."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from app.api.routes.extension import require_loopback
from app.db.session import get_db
from app.schemas.cross_task_matching import (
    CrossTaskMatchOutcomeOut,
    CrossTaskMatchPlanOut,
    CrossTaskMatchRunRequest,
    CrossTaskMatchRunResponse,
    CrossTaskReviewItemOut,
    CrossTaskSelection,
    CrossTaskSourceOut,
)
from app.services import cross_task_matching

router = APIRouter(prefix="/api/task-match-batches", tags=["task-match-batches"])


def _plan_out(plan: cross_task_matching.CrossTaskPlan) -> CrossTaskMatchPlanOut:
    items: list[CrossTaskReviewItemOut] = []
    for candidate in plan.candidates:
        job = candidate.plan.job
        analysis = candidate.plan.cached
        reasons = cross_task_matching.review_reasons(candidate)
        if analysis is None:
            bucket = "待分析"
        elif reasons:
            bucket = "待确认"
        elif analysis.verdict.value in ("strong_apply", "apply"):
            bucket = "建议复核"
        else:
            bucket = "低匹配"
        items.append(
            CrossTaskReviewItemOut(
                job_id=job.id,
                title=job.title,
                company=job.company,
                city=job.city,
                salary_text=job.salary_text,
                experience_text=job.experience_text,
                education_text=job.education_text,
                sources=[
                    CrossTaskSourceOut(
                        task_id=task.id,
                        name=task.name,
                        city=task.city,
                        keyword=task.keywords,
                    )
                    for task in candidate.source_tasks
                ],
                cached=analysis is not None,
                score=analysis.overall_score if analysis else None,
                verdict=analysis.verdict if analysis else None,
                bucket=bucket,
                review_reasons=reasons,
                summary=(analysis.result_json or {}).get("reasoning_summary", "")
                if analysis
                else "",
            )
        )
    rank = {"建议复核": 0, "待确认": 1, "低匹配": 2, "待分析": 3}
    items.sort(
        key=lambda item: (
            rank[item.bucket],
            -(item.score if item.score is not None else -1),
            item.job_id,
        )
    )
    return CrossTaskMatchPlanOut(
        task_ids=[task.id for task in plan.tasks],
        task_count=len(plan.tasks),
        active_resume_id=plan.resume_id,
        active_resume_name=plan.resume_name,
        model=plan.model,
        fingerprint=plan.fingerprint,
        unique_jobs=len(plan.candidates),
        cached_jobs=plan.cached_count,
        pending_jobs=plan.pending_count,
        max_new_calls=plan.new_call_cap,
        items=items,
    )


@router.post("/plan", response_model=CrossTaskMatchPlanOut)
def plan(
    request: Request,
    payload: CrossTaskSelection = Body(...),
    db: Session = Depends(get_db),
) -> CrossTaskMatchPlanOut:
    require_loopback(request)
    return _plan_out(cross_task_matching.plan_cross_task_match(db, payload.task_ids))


@router.post("/run", response_model=CrossTaskMatchRunResponse)
async def run(
    request: Request,
    payload: CrossTaskMatchRunRequest = Body(...),
    db: Session = Depends(get_db),
) -> CrossTaskMatchRunResponse:
    require_loopback(request)
    outcomes, refreshed = await cross_task_matching.run_cross_task_match(
        db,
        payload.task_ids,
        confirmed=payload.confirmed,
        fingerprint=payload.fingerprint,
        max_new_calls=payload.max_new_calls,
    )
    results = [
        CrossTaskMatchOutcomeOut(
            job_id=outcome.job_id,
            cached=outcome.cached,
            analyzed=outcome.analysis is not None and not outcome.cached,
            error=outcome.error,
            category=outcome.category,
            http_status=outcome.http_status,
        )
        for outcome in outcomes
    ]
    return CrossTaskMatchRunResponse(
        plan=_plan_out(refreshed),
        results=results,
        calls_used=sum(
            1
            for outcome in outcomes
            if not outcome.cached and (outcome.analysis or outcome.error)
        ),
        analyzed=sum(outcome.analysis is not None and not outcome.cached for outcome in outcomes),
        failed=sum(outcome.error is not None for outcome in outcomes),
    )
