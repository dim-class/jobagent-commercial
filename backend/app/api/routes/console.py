"""求职任务控制台 - attention subview (M2).

One thin, read-only aggregate endpoint. It composes existing services/routes
(application queue, recruiter inbox, interview board, offer board, job
analysis state) and writes nothing - no ``Job.status`` change, no AI call, no
new persistence. See docs/orchestration/ROADMAP.md - M2.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.console import ConsoleAttentionOut
from app.services import console_attention

router = APIRouter(prefix="/api/console", tags=["console"])


@router.get("/attention", response_model=ConsoleAttentionOut)
def get_attention(db: Session = Depends(get_db)) -> ConsoleAttentionOut:
    return console_attention.build_attention(db)
