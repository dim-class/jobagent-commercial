"""Deterministic city x keyword SearchPlan generation (M4e, explicitly
authorized - CLAUDE.md "Chrome extension - M4 supervised navigation policy",
M4e/M4f amendment).

Every SearchPlan row is a ``JobSearchTask`` - the same model M1 already uses
for a manual task, flagged ``is_search_plan=True`` - so nothing here is a
second task/dedup pipeline. Generation never calls OpenAI, never contacts
BOSS, and never creates a duplicate city x keyword pair: a combination
already present (checked by ``city_id`` + ``keywords`` + ``is_search_plan``)
is skipped, not recreated, so calling this twice with the same inputs is
idempotent.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.career_strategy import load_strategy
from app.core.errors import ValidationError
from app.models import JobSearchTask, SearchTaskRunStatus, TaskMode
from app.services.boss_cities import city_id_for
from app.services import boss_search_filters, direction_analysis
from app.services.job_matcher import get_active_resume
from app.services.search_direction_ranking import (
    DirectionRanking,
    rank as rank_search_directions,
)

#: The initial configured inputs (CLAUDE.md M4e/M4f amendment). Callers may
#: override either list; these are only the defaults.
DEFAULT_CITIES: list[str] = ["北京", "上海", "广州", "杭州"]
DEFAULT_KEYWORDS: list[str] = [
    "AWS",
    "Cloud Engineer",
    "DevOps",
    "SRE",
    "Platform Engineer",
    "Infrastructure Engineer",
    "云计算",
    "云平台",
    "运维开发",
    "基础设施",
]

QUICK_SEARCH_NOTE = "quick_resume_search:v1"


#: P3A keeps the broader personal search finite while allowing several resume
#: directions across several cities. The frontend bridge and MV3 worker enforce
#: this same ceiling before any browser side effect.
MAX_BATCH_TASKS = 16
MAX_SEARCH_DIRECTIONS = 8
MAX_SELECTED_CITIES = 4


def _is_chinese(text: str) -> bool:
    return any("一" <= char <= "鿿" for char in text)


def resume_search_keywords(strategy: dict, *, limit: int) -> list[str]:
    """The user's own role directions, ordered, deduped, capped at ``limit``.

    The strategy is already the local, editable summary of the active
    resume/career intent, so this needs no model and no network call.

    Ordering puts Chinese roles first because BOSS's search is a Chinese UI and
    an English keyword matches far fewer postings there: cloud roles lead, then
    other Chinese roles, then the rest in configured order. Returning a *list*
    rather than one keyword is what the M4e amendment already describes -
    "deterministically expands configurable city x keyword inputs" - and one
    keyword only ever searched a single slice of a 13-role strategy.
    """
    roles = [str(role).strip() for role in strategy.get("preferred_roles") or [] if str(role).strip()]
    if not roles:
        raise ValidationError("职业策略中没有岗位方向，请先在设置中添加目标岗位。")
    if limit < 1:
        raise ValidationError("岗位方向数量必须至少为 1。")
    cloud = [role for role in roles if _is_chinese(role) and "云" in role]
    chinese = [role for role in roles if _is_chinese(role) and role not in cloud]
    other = [role for role in roles if not _is_chinese(role)]
    return list(dict.fromkeys(cloud + chinese + other))[:limit]


def _resume_search_keyword(strategy: dict) -> str:
    """Single-keyword form, for callers that genuinely want just one."""
    return resume_search_keywords(strategy, limit=1)[0]


def prepare_resume_searches(
    db: Session,
    *,
    cities: list[str],
    target_count: int,
    filter_sets: list[dict[str, str]] | None = None,
) -> tuple[list[JobSearchTask], str, DirectionRanking]:
    """Create fresh pending tasks for the simplified multi-city workflow.

    Repeated searches intentionally create fresh task runs; canonical job
    intake remains the only job deduplication path.  This function performs
    no BOSS action and no AI call.

    ``filter_sets`` are BOSS result-page filters the human already chose in
    their own browser (see ``boss_search_filters``). Each set multiplies the
    plan: 「云计算工程师 + 北京」 with two salary bands becomes two units, each
    with its own candidate budget over a *different* top-of-list. That is the
    only way to search deeper without touching the immutable per-task ceilings
    - and because the batch ceiling does not move either, adding segments
    necessarily costs directions. The trade is made here, visibly, rather than
    by quietly overflowing the batch.
    """
    if isinstance(target_count, bool) or not isinstance(target_count, int) or not 1 <= target_count <= 20:
        raise ValidationError("岗位数量必须是 1–20 的整数。")
    normalized_cities = list(dict.fromkeys(city.strip() for city in cities if city.strip()))
    if not 1 <= len(normalized_cities) <= MAX_SELECTED_CITIES:
        raise ValidationError(f"请至少选择 1 个、最多选择 {MAX_SELECTED_CITIES} 个城市。")
    # Validate the whole request before writing anything, so one unsupported
    # city cannot leave a partially prepared batch behind.
    city_ids = {city: city_id_for(city) for city in normalized_cities}
    resume = get_active_resume(db)
    # city x keyword, bounded by one comprehensive portfolio. One or two cities
    # can cover eight directions; four cities cover four directions each.
    strategy = load_strategy()
    segments: list[dict[str, str]] = [dict(f) for f in (filter_sets or [{}])] or [{}]
    if len(segments) > MAX_BATCH_TASKS:
        raise ValidationError(f"筛选分段最多 {MAX_BATCH_TASKS} 组。")
    # Directions x cities x segments must still fit one batch, so segments come
    # out of the direction budget rather than out of the ceiling.
    limit = min(
        MAX_SEARCH_DIRECTIONS,
        max(1, MAX_BATCH_TASKS // (len(normalized_cities) * len(segments))),
    )
    # Ranked by what this resume actually says and by what each direction has
    # historically surfaced - not by the order they happen to sit in the
    # strategy file. The AI view is used only if it is already cached: this
    # path never triggers a paid call, so preparing a search stays free.
    ranking = rank_search_directions(
        db,
        resume=resume,
        strategy=strategy,
        ai=direction_analysis.cached_analysis(db),
    )
    keywords = ranking.top(limit) or resume_search_keywords(strategy, limit=limit)
    early_career_policy = str(strategy["early_career_policy"])
    previous = db.scalars(
        select(JobSearchTask)
        .where(JobSearchTask.notes == QUICK_SEARCH_NOTE)
        .order_by(JobSearchTask.id.asc())
    ).all()
    run_number = len(previous) + 1
    tasks: list[JobSearchTask] = []
    pairs = [
        (city, keyword, segment)
        for city in normalized_cities
        for keyword in keywords
        for segment in segments
    ]
    for offset, (city, keyword, segment) in enumerate(pairs):
        label = boss_search_filters.describe(segment) if segment else ""
        task = JobSearchTask(
            name=(
                f"{city} · {keyword}"
                + (f" · {label}" if segment else "")
                + f" · 简历匹配搜索 #{run_number + offset}"
            ),
            keywords=keyword,
            city=city,
            city_id=city_ids[city],
            is_search_plan=True,
            run_status=SearchTaskRunStatus.pending,
            mode=TaskMode.manual_review_only,
            resume_id=resume.id,
            max_candidates=target_count,
            notes=QUICK_SEARCH_NOTE,
            early_career_policy=early_career_policy,
            search_filters_json=segment,
        )
        db.add(task)
        tasks.append(task)
    db.commit()
    for task in tasks:
        db.refresh(task)
    return tasks, resume.display_name, ranking


def prepare_resume_search(db: Session, *, city: str, target_count: int) -> tuple[JobSearchTask, str]:
    """Backward-compatible single-city wrapper around the multi-city path."""
    tasks, resume_name, _ranking = prepare_resume_searches(
        db, cities=[city], target_count=target_count
    )
    return tasks[0], resume_name


def generate_search_plan(
    db: Session,
    *,
    cities: list[str] | None = None,
    keywords: list[str] | None = None,
) -> dict[str, int]:
    """Create one ``JobSearchTask`` per (city, keyword) pair not already
    present. Fails closed on any unknown city - and writes nothing at all in
    that case, checking every city up front before creating any row, so a
    typo in the *last* city of a long list cannot leave a partial plan
    behind.

    Returns ``{"created": n, "skipped": n, "total": n}``.
    """
    cities = cities if cities is not None else DEFAULT_CITIES
    keywords = keywords if keywords is not None else DEFAULT_KEYWORDS
    early_career_policy = str(load_strategy()["early_career_policy"])

    # Resolve (and validate) every city first - an unknown name must reject
    # the whole call, not silently generate a partial plan around it.
    city_ids = {city: city_id_for(city) for city in cities}

    existing = set(
        db.execute(
            select(JobSearchTask.city_id, JobSearchTask.keywords).where(
                JobSearchTask.is_search_plan.is_(True)
            )
        ).all()
    )

    created = 0
    skipped = 0
    for city in cities:
        city_id = city_ids[city]
        for keyword in keywords:
            if (city_id, keyword) in existing:
                skipped += 1
                continue
            db.add(
                JobSearchTask(
                    name=f"{city} · {keyword}",
                    keywords=keyword,
                    city=city,
                    city_id=city_id,
                    is_search_plan=True,
                    run_status=SearchTaskRunStatus.pending,
                    mode=TaskMode.manual_review_only,
                    early_career_policy=early_career_policy,
                )
            )
            existing.add((city_id, keyword))
            created += 1

    db.commit()
    return {"created": created, "skipped": skipped, "total": created + skipped}
