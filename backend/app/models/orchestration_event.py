"""OrchestrationEvent - append-only console audit trail (M3).

Every row records an explicit action a human took inside the console -
opening a task or one of its candidates, marking it reviewed, or dismissing
it. Nothing here is written automatically: no event on page load, route
navigation, or a timer - see docs/orchestration/ROADMAP.md (M3). Rows are
never updated or deleted, exactly like ``ApplicationEvent``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import OrchestrationEventType


class OrchestrationEvent(Base):
    __tablename__ = "orchestration_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("job_search_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: The candidate this event is about, or ``NULL`` for a task-level event
    #: (e.g. opening the task itself rather than one of its candidates).
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_type: Mapped[OrchestrationEventType] = mapped_column(
        SAEnum(OrchestrationEventType, native_enum=False, length=32), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    task: Mapped["JobSearchTask"] = relationship()  # noqa: F821
    job: Mapped["Job | None"] = relationship()  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<OrchestrationEvent id={self.id} task={self.task_id} "
            f"job={self.job_id} type={self.event_type}>"
        )
