"""M5b: one bounded match plan across completed SearchPlan tasks.

This is orchestration only.  Jobs, task associations, analyses, cache keys and
human review events remain owned by their existing M1/M3/M5a services.  A
plan is a pure read; a run requires the exact plan fingerprint and at most
three confirmed uncached fast-model calls for the whole selection.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AppError, ValidationError
from app.models import Job, JobAnalysis, JobSearchTask, SearchTaskRunStatus, TaskCandidate
from app.services import bounded_matching, task_matching
from app.services.ai_diagnostics import classify_openai_error
from app.services.hashing import hash_json
from app.services.scoring import extract_salary_range

MAX_SELECTED_TASKS = 20
MAX_NEW_CALLS = 3


@dataclass(slots=True)
class CrossTaskCandidate:
    plan: task_matching.CandidateMatchPlan
    source_tasks: list[JobSearchTask]


@dataclass(slots=True)
class CrossTaskPlan:
    tasks: list[JobSearchTask]
    resume_id: int
    resume_name: str
    model: str
    fingerprint: str
    candidates: list[CrossTaskCandidate]
    call_limit: int

    @property
    def cached_count(self) -> int:
        return sum(candidate.plan.cached is not None for candidate in self.candidates)

    @property
    def pending_count(self) -> int:
        return len(self.candidates) - self.cached_count

    @property
    def new_call_cap(self) -> int:
        return min(self.pending_count, self.call_limit)


@dataclass(slots=True)
class CrossTaskOutcome:
    job_id: int
    cached: bool
    analysis: JobAnalysis | None
    error: str | None = None
    category: str | None = None
    http_status: int | None = None


def _validate_task_ids(task_ids: list[int]) -> list[int]:
    if not task_ids or len(task_ids) > MAX_SELECTED_TASKS:
        raise ValidationError(f"请选择 1–{MAX_SELECTED_TASKS} 个已完成的搜索任务。")
    if len(set(task_ids)) != len(task_ids):
        raise ValidationError("任务列表包含重复项；请提交精确且不重复的任务集合。")
    if any(type(task_id) is not int or task_id <= 0 for task_id in task_ids):
        raise ValidationError("任务 ID 必须是正整数。")
    return sorted(task_ids)


def plan_cross_task_match(
    db: Session, task_ids: list[int], *, settings: Settings | None = None
) -> CrossTaskPlan:
    """Resolve an exact completed-task snapshot without writes/model calls."""
    cfg = settings or get_settings()
    ids = _validate_task_ids(task_ids)
    tasks = list(
        db.scalars(select(JobSearchTask).where(JobSearchTask.id.in_(ids))).all()
    )
    by_id = {task.id: task for task in tasks}
    missing = [task_id for task_id in ids if task_id not in by_id]
    if missing:
        raise ValidationError("所选任务不存在，请刷新后重新选择。", detail={"task_ids": missing})
    tasks = [by_id[task_id] for task_id in ids]
    invalid = [
        task.id
        for task in tasks
        if not task.is_search_plan or task.run_status != SearchTaskRunStatus.completed
    ]
    if invalid:
        raise ValidationError(
            "M5b 仅接受已完成的 SearchPlan 任务。", detail={"task_ids": invalid}
        )

    associations = list(
        db.scalars(
            select(TaskCandidate)
            .where(TaskCandidate.task_id.in_(ids))
            .order_by(TaskCandidate.job_id.asc(), TaskCandidate.task_id.asc())
        ).all()
    )
    source_ids_by_job: dict[int, list[int]] = {}
    jobs_by_id: dict[int, Job] = {}
    for association in associations:
        jobs_by_id[association.job_id] = association.job
        source_ids_by_job.setdefault(association.job_id, []).append(association.task_id)

    jobs = [jobs_by_id[job_id] for job_id in sorted(jobs_by_id)]
    resume, model, candidate_plans = task_matching.plan_jobs(db, jobs, settings=cfg)
    candidates = [
        CrossTaskCandidate(
            plan=candidate_plan,
            source_tasks=[by_id[task_id] for task_id in source_ids_by_job[candidate_plan.job.id]],
        )
        for candidate_plan in candidate_plans
    ]
    snapshot = {
        "task_ids": ids,
        "tasks": [
            [
                task.id,
                task.run_status.value if task.run_status else None,
                task.city,
                task.keywords,
                task.experience_text,
                task.education_text,
                task.salary_min,
                task.salary_max,
            ]
            for task in tasks
        ],
        "associations": [
            [candidate.plan.job.id, [task.id for task in candidate.source_tasks]]
            for candidate in candidates
        ],
        "resume": [resume.id, resume.content_hash],
        "model": model,
        "call_limit": min(MAX_NEW_CALLS, cfg.max_analyses_per_run),
        "cache": [
            [candidate.plan.cache_key, candidate.plan.cached.id if candidate.plan.cached else None]
            for candidate in candidates
        ],
    }
    return CrossTaskPlan(
        tasks=tasks,
        resume_id=resume.id,
        resume_name=resume.display_name,
        model=model,
        fingerprint=hash_json(snapshot),
        candidates=candidates,
        call_limit=min(MAX_NEW_CALLS, cfg.max_analyses_per_run),
    )


def review_reasons(candidate: CrossTaskCandidate) -> list[str]:
    """Conservative deterministic conflicts; no new scoring or conversion."""
    job = candidate.plan.job
    reasons = bounded_matching.review_reasons(job, candidate.plan.cached)
    for task in candidate.source_tasks:
        if task.city and job.city and task.city not in job.city and job.city not in task.city:
            reasons.append(f"任务「{task.name}」城市条件与岗位城市不一致，请确认。")
        if task.education_text and job.education_text:
            if task.education_text not in job.education_text and job.education_text not in task.education_text:
                reasons.append(f"任务「{task.name}」学历条件与岗位学历不一致，请确认。")
        if task.experience_text and job.experience_text:
            if task.experience_text not in job.experience_text and job.experience_text not in task.experience_text:
                reasons.append(f"任务「{task.name}」经验条件与岗位经验不一致，请确认。")
        low, high = extract_salary_range(job.salary_text)
        if task.salary_min is not None and high is not None and high < task.salary_min:
            reasons.append(f"任务「{task.name}」最低薪资条件高于岗位薪资上限，请确认。")
        if task.salary_max is not None and low is not None and low > task.salary_max:
            reasons.append(f"任务「{task.name}」最高薪资条件低于岗位薪资下限，请确认。")
    return list(dict.fromkeys(reasons))


async def run_cross_task_match(
    db: Session,
    task_ids: list[int],
    *,
    confirmed: bool,
    fingerprint: str,
    max_new_calls: int,
    settings: Settings | None = None,
) -> tuple[list[CrossTaskOutcome], CrossTaskPlan]:
    """Run the exact confirmed snapshot, with one whole-batch budget."""
    cfg = settings or get_settings()
    plan = plan_cross_task_match(db, task_ids, settings=cfg)
    expected = plan.new_call_cap
    if type(max_new_calls) is not int or max_new_calls != expected:
        raise ValidationError(
            f"本次必须确认准确的新调用数 {expected}（全批次最多 {plan.call_limit}）。"
        )
    if plan.pending_count:
        if confirmed is not True:
            raise ValidationError("存在未缓存岗位；请确认准确的本次费用上限后再继续。")
        if fingerprint != plan.fingerprint:
            raise ValidationError("任务、候选岗位、简历、模型或缓存已变化；请重新生成计划。")
    elif fingerprint != plan.fingerprint:
        raise ValidationError("任务、候选岗位、简历、模型或缓存已变化；请重新生成计划。")

    outcomes: list[CrossTaskOutcome] = []
    calls_used = 0
    for candidate in plan.candidates:
        item = candidate.plan
        if item.cached is not None:
            outcomes.append(CrossTaskOutcome(item.job.id, True, item.cached))
            continue
        if calls_used >= max_new_calls:
            outcomes.append(CrossTaskOutcome(item.job.id, False, None))
            continue
        calls_used += 1  # failures and uncertain outcomes permanently consume this run's slot
        try:
            result = await task_matching.analyze_job(
                db,
                item.job.id,
                use_smart_model=False,
                settings=cfg,
                resume_id=plan.resume_id,
                mark_reviewed=False,
                no_retries=True,
            )
            outcomes.append(CrossTaskOutcome(item.job.id, result.cached, result.analysis))
        except AppError as exc:
            detail = exc.detail or {}
            outcomes.append(
                CrossTaskOutcome(
                    item.job.id,
                    False,
                    None,
                    exc.message,
                    detail.get("category"),
                    detail.get("http_status"),
                )
            )
        except Exception as exc:  # noqa: BLE001 - safe classification, continue within the cap
            classification = classify_openai_error(exc)
            outcomes.append(
                CrossTaskOutcome(
                    item.job.id,
                    False,
                    None,
                    classification.message,
                    classification.category,
                    classification.http_status,
                )
            )
    return outcomes, plan_cross_task_match(db, task_ids, settings=cfg)
