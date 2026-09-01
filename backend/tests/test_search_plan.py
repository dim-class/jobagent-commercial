"""M4e/M4f: SearchPlan generation, city mapping, URL construction, and the
bounded automatic-runner task lifecycle. Deterministic, no OpenAI, no
network - see CLAUDE.md's "Chrome extension - M4 supervised navigation
policy" M4e/M4f amendment.
"""

from __future__ import annotations

import pytest

from app.core.errors import NotFoundError, ValidationError
from app.models import JobSearchTask, SearchTaskRunStatus
from app.services import boss_cities, boss_search_url, search_plan, search_task_runner, task_console


def make_manual_task(db, name: str = "手动任务"):
    return task_console.create_task(db, name=name)


# --------------------------------------------------------------------------
# boss_cities
# --------------------------------------------------------------------------


def test_known_cities_resolve_to_their_documented_codes(db):
    assert boss_cities.city_id_for("北京") == "101010100"
    assert boss_cities.city_id_for("上海") == "101020100"
    assert boss_cities.city_id_for("广州") == "101280100"
    assert boss_cities.city_id_for("杭州") == "101210100"


def test_an_unknown_city_fails_explicitly_never_a_default(db):
    with pytest.raises(ValidationError):
        boss_cities.city_id_for("苏州")


# --------------------------------------------------------------------------
# boss_search_url
# --------------------------------------------------------------------------


def test_build_search_url_is_same_origin_and_carries_both_params():
    url = boss_search_url.build_search_url("101010100", "SRE")
    assert url.startswith("https://www.zhipin.com/web/geek/jobs?")
    assert "city=101010100" in url
    assert "query=SRE" in url


def test_build_search_url_encodes_a_chinese_keyword():
    url = boss_search_url.build_search_url("101020100", "云计算")
    assert "query=" in url
    assert "云计算" not in url  # must be percent-encoded, not raw


# --------------------------------------------------------------------------
# search_plan.generate_search_plan
# --------------------------------------------------------------------------


def test_generate_search_plan_creates_one_task_per_city_keyword_pair(db):
    result = search_plan.generate_search_plan(
        db, cities=["北京", "上海"], keywords=["SRE", "DevOps"]
    )
    assert result == {"created": 4, "skipped": 0, "total": 4}

    tasks = db.query(JobSearchTask).filter(JobSearchTask.is_search_plan.is_(True)).all()
    assert len(tasks) == 4
    pairs = {(t.city_id, t.keywords) for t in tasks}
    assert pairs == {
        ("101010100", "SRE"),
        ("101010100", "DevOps"),
        ("101020100", "SRE"),
        ("101020100", "DevOps"),
    }
    assert all(t.run_status == SearchTaskRunStatus.pending for t in tasks)
    assert all(t.mode.value == "manual_review_only" for t in tasks)
    assert all(t.early_career_policy == "exclude" for t in tasks)


def test_generate_search_plan_is_idempotent(db):
    search_plan.generate_search_plan(db, cities=["北京"], keywords=["SRE"])
    second = search_plan.generate_search_plan(db, cities=["北京"], keywords=["SRE"])
    assert second == {"created": 0, "skipped": 1, "total": 1}
    assert db.query(JobSearchTask).filter(JobSearchTask.is_search_plan.is_(True)).count() == 1


def test_generate_search_plan_rejects_an_unknown_city_and_writes_nothing(db):
    with pytest.raises(ValidationError):
        search_plan.generate_search_plan(db, cities=["北京", "苏州"], keywords=["SRE"])
    # Fails closed *before* creating anything - not even the known city.
    assert db.query(JobSearchTask).filter(JobSearchTask.is_search_plan.is_(True)).count() == 0


def test_generate_search_plan_never_flags_a_manual_task(db):
    make_manual_task(db, "我的手动任务")
    search_plan.generate_search_plan(db, cities=["北京"], keywords=["SRE"])
    manual = db.query(JobSearchTask).filter(JobSearchTask.is_search_plan.is_(False)).all()
    assert len(manual) == 1
    assert manual[0].city_id is None
    assert manual[0].run_status is None


def test_default_cities_and_keywords_produce_forty_tasks(db):
    result = search_plan.generate_search_plan(db)
    assert result == {"created": 40, "skipped": 0, "total": 40}


# --------------------------------------------------------------------------
# simplified resume-bound search preparation
# --------------------------------------------------------------------------


def test_prepare_resume_search_creates_a_fresh_bounded_resume_task(db, active_resume):
    first, resume_name = search_plan.prepare_resume_search(
        db, city="北京", target_count=7
    )
    second, _ = search_plan.prepare_resume_search(db, city="北京", target_count=7)

    assert first.id != second.id
    assert first.resume_id == active_resume.id
    assert first.city == "北京"
    assert first.city_id == "101010100"
    assert first.keywords == "云计算工程师"
    assert first.max_candidates == 7
    assert first.run_status == SearchTaskRunStatus.pending
    assert first.mode.value == "manual_review_only"
    assert first.notes == search_plan.QUICK_SEARCH_NOTE
    assert first.early_career_policy == "exclude"
    assert resume_name == active_resume.display_name


def test_prepare_resume_searches_creates_one_fresh_task_per_unique_city(db, active_resume):
    tasks, resume_name = search_plan.prepare_resume_searches(
        db, cities=["北京", "上海", "北京"], target_count=4
    )

    # Two cities cover eight configured directions, still under the portfolio ceiling.
    assert len(tasks) == 16
    assert [task.city for task in tasks] == ["北京"] * 8 + ["上海"] * 8
    assert [task.city_id for task in tasks] == ["101010100"] * 8 + ["101020100"] * 8
    assert all(task.resume_id == active_resume.id for task in tasks)
    assert all(task.max_candidates == 4 for task in tasks)
    assert all(task.run_status == SearchTaskRunStatus.pending for task in tasks)
    assert resume_name == active_resume.display_name


def test_quick_search_expands_one_city_across_several_role_directions(db, active_resume):
    """One keyword only ever searched a slice of a multi-role strategy."""
    tasks, _ = search_plan.prepare_resume_searches(db, cities=["杭州"], target_count=20)

    keywords = [task.keywords for task in tasks]
    assert len(tasks) == search_plan.MAX_SEARCH_DIRECTIONS
    assert len(set(keywords)) == len(keywords), "no city x keyword pair repeats"
    assert all(task.city == "杭州" for task in tasks)
    assert keywords[0] == "云计算工程师", "a Chinese cloud role still leads"


@pytest.mark.parametrize(
    "cities, expected_tasks",
    [(["北京"], 8), (["北京", "上海"], 16), (["北京", "上海", "广州"], 15),
     (["北京", "上海", "广州", "杭州"], 16)],
)
def test_quick_search_never_exceeds_the_comprehensive_batch_ceiling(
    db, active_resume, cities, expected_tasks
):
    tasks, _ = search_plan.prepare_resume_searches(db, cities=cities, target_count=3)
    assert len(tasks) == expected_tasks
    assert len(tasks) <= search_plan.MAX_BATCH_TASKS


def test_quick_search_directions_remain_bounded_even_with_many_configured_roles(
    db, active_resume, monkeypatch
):
    monkeypatch.setattr(search_plan, "load_strategy", lambda: {
        "preferred_roles": [f"云方向{i}" for i in range(20)],
        "early_career_policy": "exclude",
    })
    tasks, _ = search_plan.prepare_resume_searches(db, cities=["北京"], target_count=8)
    assert len(tasks) == search_plan.MAX_SEARCH_DIRECTIONS == 8


def test_resume_keywords_are_deterministic_chinese_first_and_deduplicated():
    strategy = {
        "preferred_roles": [
            "Cloud Engineer", "运维开发工程师", "云计算工程师", "SRE",
            "云平台工程师", "运维开发工程师",
        ]
    }
    assert search_plan.resume_search_keywords(strategy, limit=10) == [
        "云计算工程师", "云平台工程师", "运维开发工程师", "Cloud Engineer", "SRE",
    ]
    assert search_plan.resume_search_keywords(strategy, limit=2) == [
        "云计算工程师", "云平台工程师",
    ]


def test_resume_keywords_need_a_configured_role():
    with pytest.raises(ValidationError):
        search_plan.resume_search_keywords({"preferred_roles": []}, limit=3)


def test_prepare_resume_searches_validates_all_cities_before_writing(db, active_resume):
    with pytest.raises(ValidationError):
        search_plan.prepare_resume_searches(
            db, cities=["北京", "苏州"], target_count=3
        )
    assert db.query(JobSearchTask).filter(JobSearchTask.notes == search_plan.QUICK_SEARCH_NOTE).count() == 0


@pytest.mark.parametrize("target_count", [0, 21, True, 1.5])
def test_prepare_resume_search_rejects_an_invalid_target_count(db, active_resume, target_count):
    with pytest.raises(ValidationError):
        search_plan.prepare_resume_search(db, city="北京", target_count=target_count)


def test_prepare_resume_search_requires_an_active_resume(db):
    with pytest.raises(ValidationError):
        search_plan.prepare_resume_search(db, city="北京", target_count=3)


@pytest.mark.parametrize("cities", [[], ["北京", "上海", "广州", "杭州", "深圳"]])
def test_prepare_resume_searches_rejects_an_invalid_city_count(db, active_resume, cities):
    with pytest.raises(ValidationError):
        search_plan.prepare_resume_searches(db, cities=cities, target_count=3)


# --------------------------------------------------------------------------
# search_task_runner: state machine
# --------------------------------------------------------------------------


def make_plan_task(db):
    search_plan.generate_search_plan(db, cities=["北京"], keywords=["SRE"])
    return db.query(JobSearchTask).filter(JobSearchTask.is_search_plan.is_(True)).one()


def test_start_run_moves_pending_to_running_and_stamps_started_at(db):
    task = make_plan_task(db)
    started = search_task_runner.start_run(db, task.id)
    assert started.run_status == SearchTaskRunStatus.running
    assert started.run_started_at is not None
    assert started.run_stopped_at is None


def test_start_run_rejects_a_task_that_is_already_running(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    with pytest.raises(ValidationError):
        search_task_runner.start_run(db, task.id)


def test_start_run_for_a_missing_task_404s(db):
    with pytest.raises(NotFoundError):
        search_task_runner.start_run(db, 999999)


def test_pause_then_resume_round_trips_through_running(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    paused = search_task_runner.pause_run(db, task.id)
    assert paused.run_status == SearchTaskRunStatus.paused

    resumed = search_task_runner.resume_run(db, task.id)
    assert resumed.run_status == SearchTaskRunStatus.running


def test_pause_rejects_a_task_that_is_not_running(db):
    task = make_plan_task(db)
    with pytest.raises(ValidationError):
        search_task_runner.pause_run(db, task.id)


def test_enter_verification_and_resume(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    stopped = search_task_runner.enter_verification(db, task.id)
    assert stopped.run_status == SearchTaskRunStatus.paused_verification

    resumed = search_task_runner.resume_run(db, task.id)
    assert resumed.run_status == SearchTaskRunStatus.running


def test_enter_login_required_and_resume(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    stopped = search_task_runner.enter_login_required(db, task.id)
    assert stopped.run_status == SearchTaskRunStatus.paused_login_required
    assert stopped.paused_reason == "login_required"

    resumed = search_task_runner.resume_run(db, task.id)
    assert resumed.run_status == SearchTaskRunStatus.running
    assert resumed.paused_reason is None


def test_resume_rejects_a_task_that_is_running(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    with pytest.raises(ValidationError):
        search_task_runner.resume_run(db, task.id)


def test_cancel_run_from_pending_running_or_paused(db):
    for keyword, state_setup in (("SRE", "pending"), ("DevOps", "running"), ("AWS", "paused")):
        search_plan.generate_search_plan(db, cities=["北京"], keywords=[keyword])
        task = (
            db.query(JobSearchTask)
            .filter(JobSearchTask.is_search_plan.is_(True), JobSearchTask.keywords == keyword)
            .one()
        )
        if state_setup in ("running", "paused"):
            search_task_runner.start_run(db, task.id)
        if state_setup == "paused":
            search_task_runner.pause_run(db, task.id)
        cancelled = search_task_runner.cancel_run(db, task.id)
        assert cancelled.run_status == SearchTaskRunStatus.cancelled
        assert cancelled.run_stopped_at is not None


def test_cancel_run_is_idempotent(db):
    task = make_plan_task(db)
    search_task_runner.cancel_run(db, task.id)
    twice = search_task_runner.cancel_run(db, task.id)
    assert twice.run_status == SearchTaskRunStatus.cancelled


def test_cancel_run_rejects_an_already_completed_task(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    search_task_runner.record_round(db, task.id, observed=1, new=0, duplicate=1)
    search_task_runner.record_round(db, task.id, observed=1, new=0, duplicate=1)
    completed = search_task_runner.record_round(db, task.id, observed=1, new=0, duplicate=1)
    assert completed.run_status == SearchTaskRunStatus.completed
    with pytest.raises(ValidationError):
        search_task_runner.cancel_run(db, task.id)


def test_complete_run_marks_a_running_task_completed(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    completed = search_task_runner.complete_run(db, task.id)
    assert completed.run_status == SearchTaskRunStatus.completed
    assert completed.run_stopped_at is not None


def test_complete_run_rejects_a_task_that_is_not_running(db):
    task = make_plan_task(db)
    with pytest.raises(ValidationError):
        search_task_runner.complete_run(db, task.id)


def test_complete_run_rejects_a_manual_task(db):
    manual = make_manual_task(db)
    with pytest.raises(ValidationError):
        search_task_runner.complete_run(db, manual.id)


def test_fail_run_records_a_short_error_and_stops(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    failed = search_task_runner.fail_run(db, task.id, error="selector_ambiguous")
    assert failed.run_status == SearchTaskRunStatus.failed
    assert failed.last_error == "selector_ambiguous"
    assert failed.run_stopped_at is not None


# --------------------------------------------------------------------------
# search_task_runner: round accounting / URL-delta / auto-complete
# --------------------------------------------------------------------------


def test_record_round_accumulates_cumulative_counters(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    search_task_runner.record_round(db, task.id, observed=5, new=5, duplicate=0)
    after = search_task_runner.record_round(db, task.id, observed=3, new=2, duplicate=1)
    assert after.observed_count == 8
    assert after.new_count == 7
    assert after.duplicate_count == 1


def test_record_round_rejects_a_task_that_is_not_running(db):
    task = make_plan_task(db)
    with pytest.raises(ValidationError):
        search_task_runner.record_round(db, task.id, observed=1, new=1, duplicate=0)


def test_record_round_rejects_negative_counts(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    with pytest.raises(ValidationError):
        search_task_runner.record_round(db, task.id, observed=-1, new=0, duplicate=0)


def test_a_round_with_new_candidates_resets_the_no_new_streak(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    search_task_runner.record_round(db, task.id, observed=1, new=0, duplicate=1)
    search_task_runner.record_round(db, task.id, observed=1, new=0, duplicate=1)
    revived = search_task_runner.record_round(db, task.id, observed=1, new=1, duplicate=0)
    assert revived.no_new_rounds == 0
    assert revived.run_status == SearchTaskRunStatus.running


def test_three_consecutive_no_new_rounds_auto_completes_the_default_threshold(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    search_task_runner.record_round(db, task.id, observed=2, new=0, duplicate=2)
    still_running = search_task_runner.record_round(db, task.id, observed=2, new=0, duplicate=2)
    assert still_running.run_status == SearchTaskRunStatus.running
    completed = search_task_runner.record_round(db, task.id, observed=2, new=0, duplicate=2)
    assert completed.run_status == SearchTaskRunStatus.completed
    assert completed.run_stopped_at is not None


def test_a_lower_no_new_round_threshold_may_be_configured_per_call(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    completed = search_task_runner.record_round(
        db, task.id, observed=1, new=0, duplicate=1, no_new_round_threshold=1
    )
    assert completed.run_status == SearchTaskRunStatus.completed


def test_no_more_rounds_are_counted_once_a_run_is_paused(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    search_task_runner.pause_run(db, task.id)
    with pytest.raises(ValidationError):
        search_task_runner.record_round(db, task.id, observed=1, new=1, duplicate=0)


# --------------------------------------------------------------------------
# search_task_runner: hardening - reject manual tasks, validate threshold
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda db, task_id: search_task_runner.start_run(db, task_id),
        lambda db, task_id: search_task_runner.pause_run(db, task_id),
        lambda db, task_id: search_task_runner.enter_verification(db, task_id),
        lambda db, task_id: search_task_runner.resume_run(db, task_id),
        lambda db, task_id: search_task_runner.cancel_run(db, task_id),
        lambda db, task_id: search_task_runner.fail_run(db, task_id, error="x"),
        lambda db, task_id: search_task_runner.record_round(db, task_id, observed=1, new=1, duplicate=0),
    ],
)
def test_every_run_state_operation_rejects_a_manual_task(db, call):
    manual = make_manual_task(db)
    with pytest.raises(ValidationError):
        call(db, manual.id)
    # Nothing about the manual task changed - it was refused outright.
    db.refresh(manual)
    assert manual.run_status is None
    assert manual.is_search_plan is False


def test_cancel_run_rejects_a_manual_task_even_though_cancel_is_otherwise_idempotent(db):
    manual = make_manual_task(db)
    with pytest.raises(ValidationError):
        search_task_runner.cancel_run(db, manual.id)


def test_record_round_rejects_a_non_integer_threshold(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    with pytest.raises(ValidationError):
        search_task_runner.record_round(
            db, task.id, observed=1, new=0, duplicate=1, no_new_round_threshold=1.5
        )


def test_record_round_rejects_a_threshold_below_one(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    with pytest.raises(ValidationError):
        search_task_runner.record_round(
            db, task.id, observed=1, new=0, duplicate=1, no_new_round_threshold=0
        )
    with pytest.raises(ValidationError):
        search_task_runner.record_round(
            db, task.id, observed=1, new=0, duplicate=1, no_new_round_threshold=-1
        )


def test_record_round_accepts_the_smallest_valid_threshold(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    completed = search_task_runner.record_round(
        db, task.id, observed=1, new=0, duplicate=1, no_new_round_threshold=1
    )
    assert completed.run_status == SearchTaskRunStatus.completed


def test_resuming_a_run_resets_the_no_new_streak(db):
    task = make_plan_task(db)
    search_task_runner.start_run(db, task.id)
    search_task_runner.record_round(db, task.id, observed=1, new=0, duplicate=1)
    search_task_runner.record_round(db, task.id, observed=1, new=0, duplicate=1)
    search_task_runner.pause_run(db, task.id)
    resumed = search_task_runner.resume_run(db, task.id)
    assert resumed.no_new_rounds == 0
    # One more no-new round should not complete it yet (streak was reset).
    still_running = search_task_runner.record_round(db, task.id, observed=1, new=0, duplicate=1)
    assert still_running.run_status == SearchTaskRunStatus.running
