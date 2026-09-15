"""Is this installation actually ready to do anything?

Every failure this project produced for a new pair of hands looked the same
from the page - a button that did nothing. The cause was always one missing
prerequisite (no API key, no active résumé, no career direction, a backend
running older code than the page) and never anything the user could see.

Read-only and free: counts and configuration, no model call, no browser work,
nothing written. Ordered so the first unmet item is the one to fix first.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.career_strategy import load_strategy
from app import __version__
from app.core.config import get_settings
from app.models import Job, Resume
from app.schemas.console import ReadinessCheck, ReadinessOut


def build_readiness(db: Session) -> ReadinessOut:
    settings = get_settings()
    checks: list[ReadinessCheck] = []

    resume = db.scalars(
        select(Resume).where(Resume.is_active.is_(True), Resume.archived_at.is_(None))
    ).first()
    checks.append(
        ReadinessCheck(
            key="resume",
            label="当前分析简历",
            ok=resume is not None,
            detail=resume.display_name if resume else "未设置",
            fix="到「简历」页上传一份并设为当前分析简历。",
        )
    )

    try:
        roles = [r for r in (load_strategy().get("preferred_roles") or []) if str(r).strip()]
    except Exception:
        roles = []
    checks.append(
        ReadinessCheck(
            key="directions",
            label="岗位方向",
            ok=bool(roles),
            detail=f"{len(roles)} 个" if roles else "未配置",
            fix="到「个人设置」填写你想搜的岗位方向。",
        )
    )

    checks.append(
        ReadinessCheck(
            key="openai",
            label="OpenAI API Key",
            ok=settings.openai_configured,
            detail="已配置" if settings.openai_configured else "未配置",
            # It used to say "fill in backend/.env and restart": a file the app
            # never read, and a step the settings page had already made
            # unnecessary.
            fix="到「设置」填写 API Key，保存后立即生效。搜索和采集不需要它，"
            "AI 分析和简历方向分析需要。",
            blocking=False,
        )
    )

    jobs = db.scalar(select(func.count(Job.id))) or 0
    unanalysed = db.scalar(
        select(func.count(Job.id)).where(
            ~Job.id.in_(select(Job.id).join(Job.analyses).distinct())
        )
    ) or 0
    checks.append(
        ReadinessCheck(
            key="library",
            label="岗位库",
            ok=True,
            detail=f"{jobs} 个岗位，其中 {unanalysed} 个还没分析",
            fix="",
            blocking=False,
        )
    )

    checks.append(
        ReadinessCheck(
            key="apply",
            label="逐个确认投递（M6）",
            ok=True,
            detail="已开启" if settings.human_confirmed_apply_enabled else "未开启（默认）",
            blocking=False,
            fix="需要时在 .env（位置见「设置」）设 HUMAN_CONFIRMED_APPLY_ENABLED=true 并重启后端。"
            "开启只是让功能可见，每个岗位仍需你单独确认。",
        )
    )

    return ReadinessOut(
        # Only what actually stops a search counts as "not ready".
        ready=all(c.ok for c in checks if c.blocking),
        version=__version__,
        checks=checks,
    )
