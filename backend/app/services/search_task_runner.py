"""Bounded automatic-runner task lifecycle (M4e/M4f, explicitly authorized -
CLAUDE.md "Chrome extension - M4 supervised navigation policy", M4e/M4f
amendment).

Every function here only reads/writes one ``JobSearchTask`` row's
``run_status``/timestamps/counters. Nothing here navigates a tab, reads a
page, scrolls, clicks, or calls a model - the extension performs every real
browser step (still bounded by its own ``SupervisedSession``, exactly as
M4b/M4c already require), and only *afterward* tells this service what
happened so the task-level lifecycle and cumulative inventory stay accurate
across possibly many sessions (a paused task resumes as a new bounded
session, never the same one revived).

State machine::

    pending --------> running <-------> paused
       |                 |   `--------> paused_verification
       |                 |
       |                 +--> completed  (auto: no_new_rounds hits threshold)
       |                 +--> failed     (explicit: fail_run)
       +-----------------+--> cancelled  (explicit: cancel_run, from anywhere non-terminal)

No path ever leaves ``completed``/``failed``/``cancelled`` - see
``CLOSED_SEARCH_TASK_RUN_STATUSES``. No automatic resume, ever: only
``resume_run``, an explicit human-triggered call, moves out of
``paused``/``paused_verification``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.models import CLOSED_SEARCH_TASK_RUN_STATUSES, JobSearchTask, SearchTaskRunStatus
from app.services import task_console

#: Default consecutive-no-new-round threshold (CLAUDE.md M4e/M4f amendment:
#: "consecutive no-new threshold (default 3)"). A caller may lower it, never
#: raise it past what the human approved for a given run.
DEFAULT_NO_NEW_ROUND_THRESHOLD = 3


def _require_search_plan(task: JobSearchTask, action: str) -> None:
    """Every run-state operation is scoped to a SearchPlan-generated task
    only (``is_search_plan=True``, from ``services/search_plan.py``) - a
    manual (M1) task was never designated for automatic running, and this
    runner must never drive one just because a caller passed its id."""
    if not task.is_search_plan:
        raise ValidationError(
            f"任务 {task.id} 不是自动搜索计划任务（手动任务），无法{action}。",
            detail={"task_id": task.id, "action": action, "is_search_plan": task.is_search_plan},
        )


def _require_status(task: JobSearchTask, allowed: set[SearchTaskRunStatus], action: str) -> None:
    if task.run_status not in allowed:
        raise ValidationError(
            f"任务当前状态为 {task.run_status.value if task.run_status else '未运行'}，无法{action}。",
            detail={
                "task_id": task.id,
                "run_status": task.run_status.value if task.run_status else None,
                "action": action,
            },
        )


def start_run(db: Session, task_id: int, *, match_approval: dict | None = None) -> JobSearchTask:
    """Start (or restart from ``pending``) a bounded run. Only ``pending``
    may start - a task already running/paused must be resumed, not started
    again, and a terminal task must not be silently reused."""
    task = task_console.get_task(db, task_id)
    _require_search_plan(task, "开始")
    _require_status(task, {SearchTaskRunStatus.pending}, "开始")
    values = dict(run_status=SearchTaskRunStatus.running,
                  run_started_at=datetime.now(timezone.utc), run_stopped_at=None,
                  no_new_rounds=0, last_error=None)
    if match_approval is not None:
        values['match_run_json'] = match_approval
    # Two concurrent starts must never overwrite an already claimed paid budget.
    changed = db.execute(update(JobSearchTask).where(
        JobSearchTask.id == task_id,
        JobSearchTask.run_status == SearchTaskRunStatus.pending,
    ).values(**values).execution_options(synchronize_session=False)).rowcount
    if changed != 1:
        db.rollback()
        raise ValidationError("任务已被启动；不能重复授权或重置额度。")
    db.commit()
    db.refresh(task)
    return task


def pause_run(db: Session, task_id: int, *, reason: str | None = None) -> JobSearchTask:
    """Explicit human pause - always available while running."""
    task = task_console.get_task(db, task_id)
    _require_search_plan(task, "暂停")
    _require_status(task, {SearchTaskRunStatus.running}, "暂停")
    task.run_status = SearchTaskRunStatus.paused
    task.paused_reason = (reason or "user_pause")[:64]
    db.commit()
    db.refresh(task)
    return task


def enter_verification(db: Session, task_id: int) -> JobSearchTask:
    """A verification/CAPTCHA/rate-limit signal stopped the run. Never
    automatic bypass or retry - only ``resume_run`` (an explicit human
    action, presumably after they resolved it themselves) moves on."""
    task = task_console.get_task(db, task_id)
    _require_search_plan(task, "标记为需要验证")
    _require_status(task, {SearchTaskRunStatus.running}, "标记为需要验证")
    task.run_status = SearchTaskRunStatus.paused_verification
    task.paused_reason = "verification"
    db.commit()
    db.refresh(task)
    return task


def enter_login_required(db: Session, task_id: int) -> JobSearchTask:
    """Pause because the visible BOSS page is logged out.

    This records no account/session data and never attempts login. Only the
    existing explicit human resume transition can continue the task.
    """
    task = task_console.get_task(db, task_id)
    _require_search_plan(task, "标记为需要登录")
    _require_status(task, {SearchTaskRunStatus.running}, "标记为需要登录")
    task.run_status = SearchTaskRunStatus.paused_login_required
    task.paused_reason = "login_required"
    db.commit()
    db.refresh(task)
    return task


def resume_run(db: Session, task_id: int) -> JobSearchTask:
    """The one and only way out of a paused human-gated state -
    always an explicit human action, never automatic."""
    task = task_console.get_task(db, task_id)
    _require_search_plan(task, "恢复")
    _require_status(
        task,
        {
            SearchTaskRunStatus.paused,
            SearchTaskRunStatus.paused_verification,
            SearchTaskRunStatus.paused_login_required,
        },
        "恢复",
    )
    task.run_status = SearchTaskRunStatus.running
    task.no_new_rounds = 0
    task.paused_reason = None
    db.commit()
    db.refresh(task)
    return task


def cancel_run(db: Session, task_id: int) -> JobSearchTask:
    """Explicit human cancel, from any non-terminal state. Idempotent on an
    already-cancelled task - matches ``supervised_sessions.stop_session``'s
    idempotent-stop precedent."""
    task = task_console.get_task(db, task_id)
    _require_search_plan(task, "取消")
    if task.run_status == SearchTaskRunStatus.cancelled:
        return task
    if task.run_status in CLOSED_SEARCH_TASK_RUN_STATUSES:
        raise ValidationError(
            f"任务已结束（{task.run_status.value}），无法取消。",
            detail={"task_id": task.id, "run_status": task.run_status.value},
        )
    task.run_status = SearchTaskRunStatus.cancelled
    task.run_stopped_at = datetime.now(timezone.utc)
    task.paused_reason = None
    db.commit()
    db.refresh(task)
    return task


def complete_run(db: Session, task_id: int) -> JobSearchTask:
    """Explicit, successful end of a run - the candidate cap or the scroll-
    round cap was reached with no policy violation, so this is a normal
    finish, never a failure. (The other completion path, reaching the
    consecutive-no-new-round threshold, is decided automatically inside
    ``record_round`` - this function is for every *other* clean end
    condition the runner itself recognizes.)"""
    task = task_console.get_task(db, task_id)
    _require_search_plan(task, "标记为完成")
    _require_status(task, {SearchTaskRunStatus.running}, "标记为完成")
    task.run_status = SearchTaskRunStatus.completed
    task.run_stopped_at = datetime.now(timezone.utc)
    task.paused_reason = None
    db.commit()
    db.refresh(task)
    return task


def fail_run(db: Session, task_id: int, *, error: str) -> JobSearchTask:
    """Explicit failure - an unrecoverable error the runner itself
    detected (never a policy hard stop, which is ``enter_verification`` or a
    human ``cancel_run``)."""
    task = task_console.get_task(db, task_id)
    _require_search_plan(task, "标记为失败")
    _require_status(task, {SearchTaskRunStatus.running}, "标记为失败")
    task.run_status = SearchTaskRunStatus.failed
    task.run_stopped_at = datetime.now(timezone.utc)
    task.last_error = error[:256]
    task.paused_reason = None
    db.commit()
    db.refresh(task)
    return task


def report_state(
    db: Session,
    task_id: int,
    *,
    current_url: str | None = None,
    scroll_round: int | None = None,
    visible_jobs: int | None = None,
    imported_jobs: int | None = None,
    current_candidate: str | None = None,
    last_action: str | None = None,
) -> JobSearchTask:
    """Pure observability - never touches ``run_status``, timestamps, or the
    cumulative ``observed_count``/``new_count``/``duplicate_count``/
    ``no_new_rounds`` counters ``record_round`` owns. Allowed in any
    non-terminal state (running, paused, or verification-halted) - the
    extension may legitimately still report where the tab is right after a
    pause takes effect. Every argument is optional and only the ones
    actually supplied are written, so a caller reporting just the current
    URL never blanks out an already-known candidate/action.

    ``current_url`` is rejected outright if it carries a query string or
    fragment - BOSS puts session tokens there, and this is observability,
    never a place to persist one (CLAUDE.md - "Persist only appropriate task
    progress; do not store browser/session/private data or query tokens.").
    """
    task = task_console.get_task(db, task_id)
    _require_search_plan(task, "更新运行状态")
    if task.run_status is None or task.run_status in CLOSED_SEARCH_TASK_RUN_STATUSES:
        raise ValidationError(
            f"任务当前状态为 {task.run_status.value if task.run_status else '未运行'}，无法更新运行状态。",
            detail={
                "task_id": task.id,
                "run_status": task.run_status.value if task.run_status else None,
            },
        )

    if current_url is not None:
        parsed = urlsplit(current_url)
        if parsed.query or parsed.fragment:
            raise ValidationError(
                "运行状态中的当前网址不能包含查询字符串或片段（可能携带会话令牌）。",
                detail={"current_url": current_url},
            )
        task.current_url = current_url[:512]
    if scroll_round is not None:
        if scroll_round < 0:
            raise ValidationError("滚动轮次不能为负数。", detail={"scroll_round": scroll_round})
        task.scroll_round = scroll_round
    if visible_jobs is not None:
        if visible_jobs < 0:
            raise ValidationError("可见岗位数量不能为负数。", detail={"visible_jobs": visible_jobs})
        task.visible_jobs = visible_jobs
    if imported_jobs is not None:
        if imported_jobs < 0:
            raise ValidationError("已导入岗位数量不能为负数。", detail={"imported_jobs": imported_jobs})
        task.imported_jobs = imported_jobs
    if current_candidate is not None:
        task.current_candidate = current_candidate[:256]
    if last_action is not None:
        task.last_action = last_action[:128]

    db.commit()
    db.refresh(task)
    return task


def record_round(
    db: Session,
    task_id: int,
    *,
    observed: int,
    new: int,
    duplicate: int,
    no_new_round_threshold: int = DEFAULT_NO_NEW_ROUND_THRESHOLD,
) -> JobSearchTask:
    """Account for one already-performed bounded scroll round's inventory
    delta (see ``docs/research/BOSS_OPEN_SOURCE_ANALYSIS.md`` section B) -
    the browser step already happened; this only records it. Never advances
    if the task is not ``running`` - a paused/completed/cancelled task must
    not silently keep accumulating.

    Cumulative counters never reset. ``no_new_rounds`` counts *consecutive*
    zero-new rounds within this run and resets the moment any round finds
    something new; reaching ``no_new_round_threshold`` auto-completes the
    run - a zero-new result is an end-of-current-batch signal, never a
    reason to scroll/retry/switch keyword automatically (that decision is
    exactly what this function makes, once, deterministically).
    """
    if observed < 0 or new < 0 or duplicate < 0:
        raise ValidationError(
            "滚动轮次的观察/新增/重复数量不能为负数。",
            detail={"observed": observed, "new": new, "duplicate": duplicate},
        )
    if not isinstance(no_new_round_threshold, int) or isinstance(no_new_round_threshold, bool):
        raise ValidationError(
            "连续无新增轮次阈值必须是整数。",
            detail={"no_new_round_threshold": no_new_round_threshold},
        )
    if no_new_round_threshold < 1:
        raise ValidationError(
            "连续无新增轮次阈值必须大于等于 1。",
            detail={"no_new_round_threshold": no_new_round_threshold},
        )
    task = task_console.get_task(db, task_id)
    _require_search_plan(task, "记录滚动轮次")
    _require_status(task, {SearchTaskRunStatus.running}, "记录滚动轮次")

    task.observed_count += observed
    task.new_count += new
    task.duplicate_count += duplicate
    task.no_new_rounds = 0 if new > 0 else task.no_new_rounds + 1

    if task.no_new_rounds >= no_new_round_threshold:
        task.run_status = SearchTaskRunStatus.completed
        task.run_stopped_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(task)
    return task
