"""Console audit trail (M3): append-only human review events.

Every row records an explicit action a human took in the console - opening a
task or one of its candidates, marking it reviewed, or dismissing it.
Nothing here is inferred from a page load, a timer, or any other passive
signal, and rows are never updated or deleted. This module never touches
``Job.status`` or any other lifecycle record - see
docs/orchestration/ROADMAP.md (M3).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.models import OrchestrationEvent, OrchestrationEventType, TaskCandidate
from app.services import task_console


def add_event(
    db: Session,
    task_id: int,
    *,
    event_type: OrchestrationEventType,
    job_id: int | None = None,
    note: str | None = None,
) -> OrchestrationEvent:
    """Append one event. Rejects a ``job_id`` that is not this task's candidate."""
    task = task_console.get_task(db, task_id)  # 404s if the task itself is missing

    if job_id is not None:
        association = db.scalar(
            select(TaskCandidate).where(
                TaskCandidate.task_id == task_id, TaskCandidate.job_id == job_id
            )
        )
        if association is None:
            raise ValidationError(
                f"岗位 {job_id} 不是任务「{task.name}」下的候选人，无法记录事件",
                detail={"task_id": task_id, "job_id": job_id},
            )

    event = OrchestrationEvent(
        task_id=task_id, job_id=job_id, event_type=event_type, note=note
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def list_events(db: Session, task_id: int) -> list[OrchestrationEvent]:
    """Oldest first - the same convention as ``application_workflow.list_events``."""
    task_console.get_task(db, task_id)  # 404s if the task itself is missing
    stmt = (
        select(OrchestrationEvent)
        .where(OrchestrationEvent.task_id == task_id)
        .order_by(OrchestrationEvent.created_at.asc(), OrchestrationEvent.id.asc())
    )
    return list(db.scalars(stmt).all())
