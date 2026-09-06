"""M4a supervised-session scaffolding - service and API tests.

Covers the acceptance criteria from CLAUDE.md's "Chrome extension - M4
supervised navigation policy" and docs/orchestration/ROADMAP.md M4a: a
session persists exactly the approved caps and the exact tab origin, caps
above the immutable ceilings are rejected and write nothing, a wrong tab
origin is rejected (fail closed), only one session may run at a time,
stopping is idempotent and audited, listing is oldest-first, and nothing
here ever calls the model or touches Job/Job.status.
"""

from __future__ import annotations

from app.core.errors import NotFoundError, ValidationError
from app.models import (
    Job,
    JobStatus,
    SupervisedSession,
    SupervisedSessionEvent,
    SupervisedSessionEventType,
    SupervisedSessionStatus,
)
from app.services import supervised_sessions, task_console

from tests.test_task_console import make_job

VALID_ORIGIN = "https://www.zhipin.com"


def make_task(db, name: str = "任务"):
    return task_console.create_task(db, name=name)


# --------------------------------------------------------------------------
# service: create
# --------------------------------------------------------------------------


def test_creating_a_session_persists_the_approved_caps_and_origin(db):
    task = make_task(db)

    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=2, candidate_cap=10, scroll_cap=3, tab_origin=VALID_ORIGIN
    )

    assert session.id is not None
    assert session.task_id == task.id
    assert session.status is SupervisedSessionStatus.running
    assert session.page_cap == 2
    assert session.candidate_cap == 10
    assert session.scroll_cap == 3
    assert session.tab_origin == VALID_ORIGIN
    # The starting results page is counted exactly once, at creation - see
    # `test_the_starting_page_is_counted_exactly_once_at_creation` below.
    assert session.pages_visited == 1
    assert session.candidates_extracted == 0
    assert session.scrolls_used == 0
    assert session.approved_criteria_json["task_name"] == task.name
    assert session.approved_criteria_json["mode"] == "manual_review_only"


def test_the_criteria_snapshot_matches_the_task_at_approval_time_and_is_immutable(db):
    task = task_console.create_task(
        db, name="云计算运维-北京", keywords="云计算 运维", city="北京", max_candidates=10
    )
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=1, candidate_cap=1, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    assert session.approved_criteria_json == {
        "task_name": "云计算运维-北京",
        "keywords": "云计算 运维",
        "city": "北京",
        "experience_text": None,
        "education_text": None,
        "salary_min": None,
        "salary_max": None,
        "exclusions": None,
        "resume_id": None,
        "max_candidates": 10,
        "min_score": None,
        "mode": "manual_review_only",
    }

    # Editing the task afterwards must never rewrite what was approved.
    task.keywords = "完全不同的关键词"
    task.city = "上海"
    db.commit()

    db.expire_all()
    reloaded = supervised_sessions.get_session(db, session.id)
    assert reloaded.approved_criteria_json["keywords"] == "云计算 运维"
    assert reloaded.approved_criteria_json["city"] == "北京"


def test_creating_a_session_appends_a_started_event(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=1, candidate_cap=1, scroll_cap=0, tab_origin=VALID_ORIGIN
    )

    events = db.query(SupervisedSessionEvent).filter_by(session_id=session.id).all()
    assert len(events) == 1
    assert events[0].event_type is SupervisedSessionEventType.started


def test_creating_a_session_for_a_missing_task_404s(db):
    before = db.query(SupervisedSession).count()
    try:
        supervised_sessions.create_session(
            db, task_id=999999, page_cap=1, candidate_cap=1, scroll_cap=0, tab_origin=VALID_ORIGIN
        )
        raised = False
    except NotFoundError:
        raised = True
    assert raised
    assert db.query(SupervisedSession).count() == before


# --------------------------------------------------------------------------
# immutable ceilings and exact host - fail closed, write nothing
# --------------------------------------------------------------------------


def test_a_cap_above_its_ceiling_is_rejected_and_writes_nothing(db):
    task = make_task(db)
    before = db.query(SupervisedSession).count()

    for kwargs in (
        {"page_cap": 4, "candidate_cap": 1, "scroll_cap": 0},
        {"page_cap": 1, "candidate_cap": 61, "scroll_cap": 0},
        {"page_cap": 1, "candidate_cap": 1, "scroll_cap": 31},
    ):
        try:
            supervised_sessions.create_session(db, task_id=task.id, tab_origin=VALID_ORIGIN, **kwargs)
            raised = False
        except ValidationError:
            raised = True
        assert raised, kwargs

    assert db.query(SupervisedSession).count() == before


def test_ceiling_values_themselves_are_accepted(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=20, scroll_cap=5, tab_origin=VALID_ORIGIN
    )
    assert session.page_cap == 3
    assert session.candidate_cap == 20
    assert session.scroll_cap == 5


def test_a_wrong_tab_origin_is_rejected_and_writes_nothing(db):
    task = make_task(db)
    before = db.query(SupervisedSession).count()

    for bad_origin in (
        "http://www.zhipin.com",  # wrong scheme
        "https://zhipin.com",  # missing subdomain
        "https://www.zhipin.com.evil.com",  # lookalike host
        "https://www.zhipin.com/",  # trailing slash - not an exact match
        "https://sub.zhipin.com",
    ):
        try:
            supervised_sessions.create_session(
                db, task_id=task.id, page_cap=1, candidate_cap=1, scroll_cap=0,
                tab_origin=bad_origin,
            )
            raised = False
        except ValidationError:
            raised = True
        assert raised, bad_origin

    assert db.query(SupervisedSession).count() == before


def test_only_one_session_may_run_at_a_time(db):
    task = make_task(db)
    first = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=1, candidate_cap=1, scroll_cap=0, tab_origin=VALID_ORIGIN
    )

    before = db.query(SupervisedSession).count()
    try:
        supervised_sessions.create_session(
            db, task_id=task.id, page_cap=1, candidate_cap=1, scroll_cap=0, tab_origin=VALID_ORIGIN
        )
        raised = False
    except ValidationError:
        raised = True
    assert raised
    assert db.query(SupervisedSession).count() == before

    # Stopping the first frees the slot for a genuinely new session.
    supervised_sessions.stop_session(db, first.id)
    second = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=1, candidate_cap=1, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    assert second.id != first.id


# --------------------------------------------------------------------------
# stop: idempotent, audited
# --------------------------------------------------------------------------


def test_stopping_a_session_records_the_reason_and_timestamp(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=1, candidate_cap=1, scroll_cap=0, tab_origin=VALID_ORIGIN
    )

    stopped = supervised_sessions.stop_session(db, session.id, reason="stale_tab")

    assert stopped.status is SupervisedSessionStatus.stopped
    assert stopped.stop_reason == "stale_tab"
    assert stopped.stopped_at is not None

    events = supervised_sessions.get_session(db, session.id).events
    assert [e.event_type for e in events] == [
        SupervisedSessionEventType.started,
        SupervisedSessionEventType.stopped,
    ]
    assert events[-1].reason == "stale_tab"


def test_stopping_an_already_stopped_session_is_idempotent(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=1, candidate_cap=1, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    supervised_sessions.stop_session(db, session.id, reason="user_stop")
    supervised_sessions.stop_session(db, session.id, reason="stale_tab")  # second call: no-op

    events = supervised_sessions.get_session(db, session.id).events
    stop_events = [e for e in events if e.event_type is SupervisedSessionEventType.stopped]
    assert len(stop_events) == 1
    assert stop_events[0].reason == "user_stop"  # the first stop reason wins, not overwritten


def test_get_active_session_reflects_running_state(db):
    task = make_task(db)
    assert supervised_sessions.get_active_session(db) is None

    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=1, candidate_cap=1, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    active = supervised_sessions.get_active_session(db)
    assert active is not None and active.id == session.id

    supervised_sessions.stop_session(db, session.id)
    assert supervised_sessions.get_active_session(db) is None


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def test_api_starts_lists_and_stops_a_session(client, db):
    task = make_task(db)

    created = client.post(
        "/api/extension/sessions",
        json={"task_id": task.id, "page_cap": 2, "candidate_cap": 5, "scroll_cap": 2,
              "tab_origin": VALID_ORIGIN},
    ).json()
    assert created["status"] == "running"
    assert created["events"][0]["event_type"] == "started"
    assert created["approved_criteria"]["task_name"] == task.name

    active = client.get("/api/extension/sessions/active").json()
    assert active["id"] == created["id"]

    stopped = client.post(
        f"/api/extension/sessions/{created['id']}/stop", json={"reason": "user_stop"}
    ).json()
    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "user_stop"

    assert client.get("/api/extension/sessions/active").json() is None


def test_api_rejects_cap_above_ceiling_with_422(client, db):
    task = make_task(db)
    resp = client.post(
        "/api/extension/sessions",
        json={"task_id": task.id, "page_cap": 4, "candidate_cap": 5, "scroll_cap": 2,
              "tab_origin": VALID_ORIGIN},
    )
    assert resp.status_code == 422


def test_api_rejects_wrong_tab_origin(client, db):
    task = make_task(db)
    resp = client.post(
        "/api/extension/sessions",
        json={"task_id": task.id, "page_cap": 1, "candidate_cap": 1, "scroll_cap": 0,
              "tab_origin": "https://www.zhipin.com.evil.com"},
    )
    assert resp.status_code == 422


def test_api_rejects_a_second_concurrent_session(client, db):
    task = make_task(db)
    ok = client.post(
        "/api/extension/sessions",
        json={"task_id": task.id, "page_cap": 1, "candidate_cap": 1, "scroll_cap": 0,
              "tab_origin": VALID_ORIGIN},
    )
    assert ok.status_code == 200

    blocked = client.post(
        "/api/extension/sessions",
        json={"task_id": task.id, "page_cap": 1, "candidate_cap": 1, "scroll_cap": 0,
              "tab_origin": VALID_ORIGIN},
    )
    assert blocked.status_code == 422


def test_api_missing_session_404s(client):
    assert client.get("/api/extension/sessions/999999").status_code == 404
    assert client.post("/api/extension/sessions/999999/stop", json={}).status_code == 404


# --------------------------------------------------------------------------
# what it must never do
# --------------------------------------------------------------------------


def test_supervised_sessions_make_no_openai_call(client, db, monkeypatch):
    import app.services.job_matcher as job_matcher

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("supervised sessions must never call the model")

    monkeypatch.setattr(job_matcher, "run_job_match", _explode)

    task = make_task(db)
    created = client.post(
        "/api/extension/sessions",
        json={"task_id": task.id, "page_cap": 1, "candidate_cap": 1, "scroll_cap": 0,
              "tab_origin": VALID_ORIGIN},
    ).json()
    client.get(f"/api/extension/sessions/{created['id']}")
    client.post(f"/api/extension/sessions/{created['id']}/stop", json={})


def test_supervised_sessions_never_touch_any_job(client, db):
    job = make_job(db)
    assert job.status == JobStatus.new
    jobs_before = db.query(Job).count()

    task = make_task(db)
    created = client.post(
        "/api/extension/sessions",
        json={"task_id": task.id, "page_cap": 1, "candidate_cap": 1, "scroll_cap": 0,
              "tab_origin": VALID_ORIGIN},
    ).json()
    client.post(f"/api/extension/sessions/{created['id']}/stop", json={})

    db.expire_all()
    assert db.query(Job).count() == jobs_before
    assert db.get(Job, job.id).status == JobStatus.new


def test_api_created_at_carries_explicit_utc_offset(client, db):
    """Regression, same class of bug the M3 fix addressed: a naive SQLite
    datetime must not be serialized without a UTC offset."""
    task = make_task(db)
    created = client.post(
        "/api/extension/sessions",
        json={"task_id": task.id, "page_cap": 1, "candidate_cap": 1, "scroll_cap": 0,
              "tab_origin": VALID_ORIGIN},
    ).json()

    raw = created["started_at"]
    assert raw.endswith("+00:00") or raw.endswith("Z"), raw


# --------------------------------------------------------------------------
# M4b: navigate_prepare / navigate_confirm - authorize before click,
# account only a confirmed outcome
# --------------------------------------------------------------------------


def test_prepare_detail_writes_nothing(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=5, scroll_cap=0, tab_origin=VALID_ORIGIN
    )

    prepared = supervised_sessions.navigate_prepare(
        db, session.id, target="detail", page_url="https://www.zhipin.com/job_detail/abc.html"
    )

    assert prepared.candidates_extracted == 0
    assert prepared.pages_visited == 1  # the starting page, counted at creation
    events = supervised_sessions.get_session(db, session.id).events
    assert [e.event_type for e in events] == [SupervisedSessionEventType.started]


def test_prepare_rejects_when_the_candidate_cap_is_already_reached(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=1, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    supervised_sessions.navigate_confirm(
        db, session.id, target="detail", page_url=None, outcome="success"
    )

    try:
        supervised_sessions.navigate_prepare(db, session.id, target="detail", page_url=None)
        raised = False
    except ValidationError:
        raised = True
    assert raised
    assert supervised_sessions.get_session(db, session.id).candidates_extracted == 1


def test_prepare_rejects_the_page_cap_the_same_way(db):
    task = make_task(db)
    # page_cap=2: the starting page (1, counted at creation) plus one
    # confirmed pagination click reaches it exactly.
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=2, candidate_cap=5, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    supervised_sessions.navigate_confirm(
        db, session.id, target="results", page_url=None, outcome="success"
    )

    try:
        supervised_sessions.navigate_prepare(db, session.id, target="results", page_url=None)
        raised = False
    except ValidationError:
        raised = True
    assert raised


def test_prepare_rejects_the_page_cap_immediately_when_it_only_covers_the_starting_page(db):
    """`page_cap=1` means the starting page only - no pagination click is
    ever allowed, not even a first one."""
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=1, candidate_cap=5, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    try:
        supervised_sessions.navigate_prepare(db, session.id, target="results", page_url=None)
        raised = False
    except ValidationError:
        raised = True
    assert raised


def test_prepare_rejects_a_wrong_origin_page_url(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=5, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    for bad_url in (
        "http://www.zhipin.com/job_detail/abc.html",
        "https://www.zhipin.com.evil.com/job_detail/abc.html",
        "https://sub.zhipin.com/job_detail/abc.html",
    ):
        try:
            supervised_sessions.navigate_prepare(db, session.id, target="detail", page_url=bad_url)
            raised = False
        except ValidationError:
            raised = True
        assert raised, bad_url
    assert supervised_sessions.get_session(db, session.id).candidates_extracted == 0


def test_prepare_rejects_once_the_session_is_stopped(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=5, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    supervised_sessions.stop_session(db, session.id)
    try:
        supervised_sessions.navigate_prepare(db, session.id, target="detail", page_url=None)
        raised = False
    except ValidationError:
        raised = True
    assert raised


def test_prepare_for_a_missing_session_404s(db):
    try:
        supervised_sessions.navigate_prepare(db, 999999, target="detail", page_url=None)
        raised = False
    except NotFoundError:
        raised = True
    assert raised


def test_confirm_success_increments_the_matching_counter_and_appends_navigated(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=5, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    supervised_sessions.navigate_prepare(db, session.id, target="detail", page_url=None)

    updated = supervised_sessions.navigate_confirm(
        db, session.id, target="detail", page_url=None, outcome="success"
    )

    assert updated.candidates_extracted == 1
    assert updated.pages_visited == 1  # the starting page, unaffected by a detail confirm
    events = supervised_sessions.get_session(db, session.id).events
    assert [e.event_type for e in events] == [
        SupervisedSessionEventType.started,
        SupervisedSessionEventType.navigated,
    ]
    assert events[-1].reason == "detail"


def test_confirm_results_success_increments_pages_visited(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=5, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    supervised_sessions.navigate_prepare(db, session.id, target="results", page_url=None)
    updated = supervised_sessions.navigate_confirm(
        db, session.id, target="results", page_url=None, outcome="success"
    )
    assert updated.pages_visited == 2  # starting page (1) + this confirmed pagination
    assert updated.candidates_extracted == 0


def test_the_starting_page_is_counted_exactly_once_at_creation(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=5, scroll_cap=2, tab_origin=VALID_ORIGIN
    )
    assert session.pages_visited == 1
    events = supervised_sessions.get_session(db, session.id).events
    # No extra event for it - it is implied by the session row itself, and
    # `started` already marks the session's creation.
    assert [e.event_type for e in events] == [SupervisedSessionEventType.started]


def test_confirm_scroll_success_increments_scrolls_used(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=5, scroll_cap=2, tab_origin=VALID_ORIGIN
    )
    supervised_sessions.navigate_prepare(db, session.id, target="scroll", page_url=None)
    updated = supervised_sessions.navigate_confirm(
        db, session.id, target="scroll", page_url=None, outcome="success"
    )
    assert updated.scrolls_used == 1
    assert updated.pages_visited == 1
    assert updated.candidates_extracted == 0
    events = supervised_sessions.get_session(db, session.id).events
    assert events[-1].event_type == SupervisedSessionEventType.navigated
    assert events[-1].reason == "scroll"


def test_prepare_rejects_scroll_once_the_per_page_cap_is_reached(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=5, scroll_cap=1, tab_origin=VALID_ORIGIN
    )
    supervised_sessions.navigate_prepare(db, session.id, target="scroll", page_url=None)
    supervised_sessions.navigate_confirm(
        db, session.id, target="scroll", page_url=None, outcome="success"
    )
    try:
        supervised_sessions.navigate_prepare(db, session.id, target="scroll", page_url=None)
        raised = False
    except ValidationError:
        raised = True
    assert raised


def test_scroll_cap_zero_rejects_every_scroll(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=5, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    try:
        supervised_sessions.navigate_prepare(db, session.id, target="scroll", page_url=None)
        raised = False
    except ValidationError:
        raised = True
    assert raised


def test_a_confirmed_pagination_resets_the_per_page_scroll_count(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=5, scroll_cap=2, tab_origin=VALID_ORIGIN
    )
    # Use up the whole scroll budget on the starting page.
    supervised_sessions.navigate_prepare(db, session.id, target="scroll", page_url=None)
    supervised_sessions.navigate_confirm(
        db, session.id, target="scroll", page_url=None, outcome="success"
    )
    supervised_sessions.navigate_prepare(db, session.id, target="scroll", page_url=None)
    supervised_sessions.navigate_confirm(
        db, session.id, target="scroll", page_url=None, outcome="success"
    )
    exhausted = supervised_sessions.get_session(db, session.id)
    assert exhausted.scrolls_used == 2

    # Paginating resets it - the new page's scroll budget starts fresh.
    supervised_sessions.navigate_prepare(db, session.id, target="results", page_url=None)
    paginated = supervised_sessions.navigate_confirm(
        db, session.id, target="results", page_url=None, outcome="success"
    )
    assert paginated.scrolls_used == 0
    assert paginated.pages_visited == 2

    # And scrolling is allowed again, up to the same per-page cap.
    supervised_sessions.navigate_prepare(db, session.id, target="scroll", page_url=None)
    rescrolled = supervised_sessions.navigate_confirm(
        db, session.id, target="scroll", page_url=None, outcome="success"
    )
    assert rescrolled.scrolls_used == 1


def test_a_failed_pagination_does_not_reset_the_scroll_count(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=5, scroll_cap=2, tab_origin=VALID_ORIGIN
    )
    supervised_sessions.navigate_prepare(db, session.id, target="scroll", page_url=None)
    supervised_sessions.navigate_confirm(
        db, session.id, target="scroll", page_url=None, outcome="success"
    )
    supervised_sessions.navigate_prepare(db, session.id, target="results", page_url=None)
    failed = supervised_sessions.navigate_confirm(
        db, session.id, target="results", page_url=None, outcome="failed", error="no_link"
    )
    assert failed.scrolls_used == 1  # unchanged - the pagination never actually happened
    assert failed.pages_visited == 1


def test_confirm_scroll_failed_never_counts(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=5, scroll_cap=2, tab_origin=VALID_ORIGIN
    )
    supervised_sessions.navigate_prepare(db, session.id, target="scroll", page_url=None)
    updated = supervised_sessions.navigate_confirm(
        db, session.id, target="scroll", page_url=None, outcome="failed", error="not_scrollable"
    )
    assert updated.scrolls_used == 0
    events = supervised_sessions.get_session(db, session.id).events
    assert events[-1].event_type == SupervisedSessionEventType.navigate_failed
    assert events[-1].reason == "scroll:not_scrollable"


def test_confirm_failed_appends_navigate_failed_and_never_counts(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=5, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    supervised_sessions.navigate_prepare(db, session.id, target="detail", page_url=None)

    updated = supervised_sessions.navigate_confirm(
        db, session.id, target="detail", page_url=None, outcome="failed",
        error="selector_ambiguous",
    )

    assert updated.candidates_extracted == 0
    events = supervised_sessions.get_session(db, session.id).events
    assert [e.event_type for e in events] == [
        SupervisedSessionEventType.started,
        SupervisedSessionEventType.navigate_failed,
    ]
    assert events[-1].reason == "detail:selector_ambiguous"


def test_confirm_failed_does_not_consume_the_cap(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=1, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    supervised_sessions.navigate_prepare(db, session.id, target="detail", page_url=None)
    supervised_sessions.navigate_confirm(
        db, session.id, target="detail", page_url=None, outcome="failed", error="no_link"
    )

    supervised_sessions.navigate_prepare(db, session.id, target="detail", page_url=None)
    updated = supervised_sessions.navigate_confirm(
        db, session.id, target="detail", page_url=None, outcome="success"
    )
    assert updated.candidates_extracted == 1


def test_confirm_success_re_validates_the_cap_independently_of_prepare(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=1, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    supervised_sessions.navigate_prepare(db, session.id, target="detail", page_url=None)
    supervised_sessions.navigate_confirm(
        db, session.id, target="detail", page_url=None, outcome="success"
    )

    try:
        supervised_sessions.navigate_confirm(
            db, session.id, target="detail", page_url=None, outcome="success"
        )
        raised = False
    except ValidationError:
        raised = True
    assert raised
    assert supervised_sessions.get_session(db, session.id).candidates_extracted == 1


def test_confirm_rejects_an_unknown_outcome(db):
    task = make_task(db)
    session = supervised_sessions.create_session(
        db, task_id=task.id, page_cap=3, candidate_cap=5, scroll_cap=0, tab_origin=VALID_ORIGIN
    )
    try:
        supervised_sessions.navigate_confirm(
            db, session.id, target="detail", page_url=None, outcome="maybe"
        )
        raised = False
    except ValidationError:
        raised = True
    assert raised


def test_api_prepare_then_confirm_success(client, db):
    task = make_task(db)
    created = client.post(
        "/api/extension/sessions",
        json={"task_id": task.id, "page_cap": 1, "candidate_cap": 1, "scroll_cap": 0,
              "tab_origin": VALID_ORIGIN},
    ).json()
    session_id = created["id"]

    prep = client.post(
        f"/api/extension/sessions/{session_id}/navigate/prepare",
        json={"target": "detail", "page_url": "https://www.zhipin.com/job_detail/abc.html"},
    )
    assert prep.status_code == 200
    assert prep.json()["candidates_extracted"] == 0

    conf = client.post(
        f"/api/extension/sessions/{session_id}/navigate/confirm",
        json={"target": "detail", "page_url": "https://www.zhipin.com/job_detail/abc.html",
              "outcome": "success"},
    )
    assert conf.status_code == 200
    assert conf.json()["candidates_extracted"] == 1

    blocked = client.post(
        f"/api/extension/sessions/{session_id}/navigate/prepare",
        json={"target": "detail", "page_url": None},
    )
    assert blocked.status_code == 422


def test_api_confirm_failed_never_counts(client, db):
    task = make_task(db)
    created = client.post(
        "/api/extension/sessions",
        json={"task_id": task.id, "page_cap": 3, "candidate_cap": 5, "scroll_cap": 0,
              "tab_origin": VALID_ORIGIN},
    ).json()
    session_id = created["id"]
    client.post(
        f"/api/extension/sessions/{session_id}/navigate/prepare",
        json={"target": "detail", "page_url": None},
    )
    conf = client.post(
        f"/api/extension/sessions/{session_id}/navigate/confirm",
        json={"target": "detail", "page_url": None, "outcome": "failed", "error": "selector_ambiguous"},
    )
    assert conf.status_code == 200
    assert conf.json()["candidates_extracted"] == 0


def test_api_prepare_rejects_wrong_origin(client, db):
    task = make_task(db)
    created = client.post(
        "/api/extension/sessions",
        json={"task_id": task.id, "page_cap": 1, "candidate_cap": 1, "scroll_cap": 0,
              "tab_origin": VALID_ORIGIN},
    ).json()
    session_id = created["id"]
    resp = client.post(
        f"/api/extension/sessions/{session_id}/navigate/prepare",
        json={"target": "detail", "page_url": "https://www.zhipin.com.evil.com/x"},
    )
    assert resp.status_code == 422


def test_navigate_makes_no_openai_call_and_never_touches_job(client, db, monkeypatch):
    import app.services.job_matcher as job_matcher

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("navigate must never call the model")

    monkeypatch.setattr(job_matcher, "run_job_match", _explode)

    job = make_job(db)
    jobs_before = db.query(Job).count()
    task = make_task(db)
    created = client.post(
        "/api/extension/sessions",
        json={"task_id": task.id, "page_cap": 3, "candidate_cap": 5, "scroll_cap": 0,
              "tab_origin": VALID_ORIGIN},
    ).json()
    session_id = created["id"]
    client.post(
        f"/api/extension/sessions/{session_id}/navigate/prepare",
        json={"target": "detail", "page_url": None},
    )
    client.post(
        f"/api/extension/sessions/{session_id}/navigate/confirm",
        json={"target": "detail", "page_url": None, "outcome": "success"},
    )

    db.expire_all()
    assert db.query(Job).count() == jobs_before
    assert db.get(Job, job.id).status == JobStatus.new



def test_schema_bounds_match_the_service_ceilings():
    """The request schema and the service must agree on every cap.

    They are two copies of the same number (the service imports the schema, so
    the schema cannot import the service). When they drifted, the run failed
    with a generic 「请求参数不合法」 that named no field - the cap was raised in
    one place and silently enforced at the old value in the other.
    """
    from app.schemas.supervised_session import SessionCreate

    bounds = {
        name: (field.metadata[0].ge, field.metadata[1].le)
        for name, field in SessionCreate.model_fields.items()
        if name in {"page_cap", "candidate_cap", "scroll_cap"}
    }
    assert bounds["page_cap"][1] == supervised_sessions.MAX_PAGE_CAP
    assert bounds["candidate_cap"][1] == supervised_sessions.MAX_CANDIDATE_CAP
    assert bounds["scroll_cap"][1] == supervised_sessions.MAX_SCROLL_CAP
