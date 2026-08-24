"""Console audit trail schemas (M3).

Every event is an explicit human action - opening a task or one of its
candidates, marking it reviewed, or dismissing it - never inferred from a
page view or a timer. See docs/orchestration/ROADMAP.md (M3).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import OrchestrationEventType


class OrchestrationEventCreate(BaseModel):
    event_type: OrchestrationEventType
    #: The candidate this event is about. Omit for a task-level event
    #: (e.g. "opened the task itself").
    job_id: int | None = None
    note: str | None = Field(default=None, max_length=1000)


class OrchestrationEventOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    task_id: int
    job_id: int | None = None
    event_type: OrchestrationEventType
    note: str | None = None
    created_at: datetime


class OrchestrationEventListResponse(BaseModel):
    items: list[OrchestrationEventOut]
    total: int
