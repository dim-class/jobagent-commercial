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

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from app.core.career_strategy import load_strategy
from app.core.errors import ValidationError
from app.models import (
    JobSearchTask,
    OrchestrationEvent,
    SearchTaskRunStatus,
    SupervisedSession,
    TaskCandidate,
    TaskMode,
)
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
#: Raised from 8 to 16 (user authorized 2026-09-05). A single-city run was
#: only ever filling half its own batch: 8 directions against a 16-unit
#: ceiling, while the résumé ranking had 18 directions to offer. This raises
#: no browsing ceiling - the batch is still 16 units, each still opens at most
#: 20 details over at most 5 scroll rounds.
MAX_SEARCH_DIRECTIONS = 16

#: The new-jobs target a direction may be asked for. Equal to the opened-detail
#: ceiling the extension enforces (`RUNNER_MAX_CANDIDATES`): a larger target
#: could never be met, and a smaller one only makes a direction stop earlier.
MAX_TARGET_COUNT = 60
MAX_SELECTED_CITIES = 4

#: How long a (city, direction, filters) combination whose last run came back
#: with nothing new is moved to the back of the plan.
#:
#: Measured on the user's own run history (2026-09-11), not chosen: after one
#: completed run that imported nothing, rerunning the same combination within
#: six hours found anything 33% of the time (93 runs) against 55% for any
#: completed run; past six hours it was 63% (35 runs). BOSS's list for a query
#: does not turn over within hours, so a same-day rerun mostly re-reads cards
#: the library already holds - which is what 111 of the 144 completed runs that
#: imported nothing were. Past the window the odds are ordinary again, so this
#: is a cooldown and never a ban.
SATURATION_WINDOW = timedelta(hours=6)


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


def _as_utc(value: datetime) -> datetime:
    """SQLite returns a naive datetime even for a timezone-aware column."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _filters_key(filters: dict[str, str] | None) -> str:
    return json.dumps(filters or {}, sort_keys=True, ensure_ascii=False)


def cooling_combinations(
    db: Session,
    *,
    cities: list[str],
    now: datetime | None = None,
) -> dict[tuple[str, str, str], datetime]:
    """Combinations whose latest completed run imported nothing, recently.

    Keyed by ``(city, keyword, filters)`` and mapped to when that run ended.
    Per city on purpose: the ranking's ``_barren_keywords`` is keyword-wide and
    fires only on zero cards *seen*, so it could see neither 「北京 · 云计算工程师」
    re-reading cards the library already held while the same keyword still
    produced elsewhere, nor any empty run that saw cards at all - 111 of 144.

    Only the LATEST completed run of a combination counts, so a productive run
    after an empty one ends the cooldown at once. A failed or cancelled run says
    nothing about the list and is ignored. No model call, no network.
    """

    since = (now or datetime.now(timezone.utc)) - SATURATION_WINDOW
    rows = db.execute(
        select(
            JobSearchTask.city,
            JobSearchTask.keywords,
            JobSearchTask.search_filters_json,
            JobSearchTask.imported_jobs,
            JobSearchTask.run_stopped_at,
        )
        .where(
            JobSearchTask.is_search_plan.is_(True),
            JobSearchTask.run_status == SearchTaskRunStatus.completed,
            JobSearchTask.city.in_(cities),
            JobSearchTask.run_stopped_at.is_not(None),
        )
        .order_by(JobSearchTask.run_stopped_at.asc(), JobSearchTask.id.asc())
    ).all()
    latest: dict[tuple[str, str, str], tuple[int, datetime]] = {}
    for city, keyword, filters, imported, stopped in rows:
        latest[(city, keyword, _filters_key(filters))] = (imported or 0, _as_utc(stopped))
    return {
        key: stopped
        for key, (imported, stopped) in latest.items()
        if imported == 0 and stopped >= since
    }


def _cooling_notes(deferred: list[str], ran_last: list[str]) -> list[str]:
    """Template sentences, like every other ranking note - re-derivable and checkable."""

    hours = int(SATURATION_WINDOW.total_seconds() // 3600)

    def listing(items: list[str]) -> str:
        head = "、".join(items[:6])
        return head if len(items) <= 6 else f"{head} 等 {len(items)} 个"

    notes: list[str] = []
    if deferred:
        notes.append(
            f"{listing(deferred)} 最近 {hours} 小时内搜过且没有新岗位，这次先不重复搜，"
            "名额顺延给了后面的方向。BOSS 的列表几小时内不太会换新，马上重搜多半只会看到库里已有的岗位。"
        )
    if ran_last:
        notes.append(
            f"{listing(ran_last)} 最近 {hours} 小时内搜过且没有新岗位，"
            "但其他方向不够填满名额，这次排在最后。"
        )
    return notes


def _prune_untouched_quick_tasks(db: Session, *, below_id: int) -> int:
    """Delete quick-search tasks that were prepared and never touched in any way.

    Every 开始搜索 prepares a fresh batch, and one dismissed at its confirmation
    used to stay behind for good: on 2026-09-11, 532 of 889 search-plan rows
    were exactly that - no start, no candidate, no session, no event - and the
    console downloaded every one of them on each refresh. Such a row has no
    history to lose, so it is removed rather than hidden.

    Anything with the slightest trace is kept: a start timestamp, a candidate,
    a supervised session, an orchestration event, and every completed, failed or
    cancelled run. So is every task the M4e generator made - the popup lists
    those for a human to start one at a time, and they were never this
    function's to clear.

    ``below_id`` is what makes deleting safe at all. ``job_search_tasks`` has no
    AUTOINCREMENT, so SQLite issues ``max(rowid) + 1`` and would hand a deleted
    id straight back out if the highest rows went - while the extension's batch
    pointer keeps a stopped batch's task ids. The caller inserts the new batch
    FIRST and passes its lowest id, so every deleted row sits below a row that
    survives and no deleted id can ever be issued again.
    """

    untouched = select(JobSearchTask.id).where(
        JobSearchTask.is_search_plan.is_(True),
        JobSearchTask.notes == QUICK_SEARCH_NOTE,
        JobSearchTask.run_status == SearchTaskRunStatus.pending,
        JobSearchTask.run_started_at.is_(None),
        JobSearchTask.id < below_id,
        ~exists().where(TaskCandidate.task_id == JobSearchTask.id),
        ~exists().where(SupervisedSession.task_id == JobSearchTask.id),
        ~exists().where(OrchestrationEvent.task_id == JobSearchTask.id),
    )
    doomed = list(db.scalars(untouched))
    if doomed:
        db.execute(delete(JobSearchTask).where(JobSearchTask.id.in_(doomed)))
    return len(doomed)


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
    if isinstance(target_count, bool) or not isinstance(target_count, int) or not 1 <= target_count <= MAX_TARGET_COUNT:
        raise ValidationError(f"岗位数量必须是 1–{MAX_TARGET_COUNT} 的整数。")
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
    # The whole usable order, not only the first `limit`: a city whose leading
    # directions are cooling refills from the ones after them. With nothing
    # cooling this is exactly `ranking.top(limit)`, as before.
    ordered = ranking.top(len(ranking.directions)) or resume_search_keywords(strategy, limit=limit)
    cooling = cooling_combinations(db, cities=normalized_cities)
    segment_keys = [_filters_key(segment) for segment in segments]
    early_career_policy = str(strategy["early_career_policy"])
    # From the highest id rather than a count of earlier quick runs: runs that
    # never started are pruned below, so a count would go backwards and hand the
    # same number out twice.
    run_number = (db.scalar(select(func.max(JobSearchTask.id))) or 0) + 1
    chosen_by_city: dict[str, list[str]] = {}
    deferred: list[str] = []
    ran_last: list[str] = []
    for city in normalized_cities:
        # Cooling moves a direction to the BACK of this city's order; it does
        # not remove it. In a multi-city batch, where four cities leave four
        # slots each, it simply falls off. In a single-city batch with fewer
        # directions than slots it still runs, last - a one-in-three chance
        # beats an empty slot. It cools only when EVERY segment of this plan is
        # cooling for it: a salary band not searched today is a different list.
        stale = [
            keyword
            for keyword in ordered
            if all((city, keyword, key) in cooling for key in segment_keys)
        ]
        fresh = [keyword for keyword in ordered if keyword not in stale]
        chosen = (fresh + stale)[:limit]
        chosen_by_city[city] = chosen
        deferred += [f"{city} · {keyword}" for keyword in stale if keyword not in chosen]
        ran_last += [f"{city} · {keyword}" for keyword in stale if keyword in chosen]
    ranking.notes.extend(_cooling_notes(deferred, ran_last))
    tasks: list[JobSearchTask] = []
    pairs = [
        (city, keyword, segment)
        for city in normalized_cities
        for keyword in chosen_by_city[city]
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
    # Insert first, prune second, in one transaction: the new rows take ids
    # above everything that exists, so every row the prune removes lies below a
    # survivor and SQLite can never reissue its id (see the prune's docstring).
    db.flush()
    if tasks:
        _prune_untouched_quick_tasks(db, below_id=min(task.id for task in tasks))
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
