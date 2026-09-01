"""M4e/M4f route-level tests: the confirmed route-order bug
(``GET /api/tasks/search-plan`` matching ``tasks.router``'s
``/api/tasks/{task_id}`` first and 422ing) plan generation/listing, every
run transition over real HTTP, and non-loopback rejection.
"""

from __future__ import annotations

import pytest

from app.models import JobSearchTask


def test_search_plan_list_does_not_422_on_the_route_order_bug(client):
    """The confirmed regression: `/api/tasks/{task_id}` (an int path param)
    must never match the literal `/api/tasks/search-plan` segment first."""
    response = client.get("/api/tasks/search-plan")
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_search_plan_options_are_backend_owned_and_loopback_only(client):
    response = client.get("/api/tasks/search-plan/options")
    assert response.status_code == 200
    assert response.json() == {
        "supported_cities": ["北京", "上海", "广州", "杭州"],
        "max_selected_cities": 4,
        "max_batch_tasks": 16,
    }


def test_generate_creates_tasks_and_list_reflects_them(client):
    generated = client.post(
        "/api/tasks/search-plan/generate",
        json={"cities": ["北京"], "keywords": ["SRE", "DevOps"]},
    )
    assert generated.status_code == 200
    assert generated.json() == {"created": 2, "skipped": 0, "total": 2}

    listed = client.get("/api/tasks/search-plan").json()["items"]
    assert len(listed) == 2
    sre = next(t for t in listed if t["keywords"] == "SRE")
    assert sre["city_id"] == "101010100"
    assert sre["run_status"] == "pending"
    assert sre["search_url"] == "https://www.zhipin.com/web/geek/jobs?city=101010100&query=SRE"
    assert sre["observed_count"] == 0


def test_generate_is_idempotent_over_http(client):
    client.post("/api/tasks/search-plan/generate", json={"cities": ["上海"], "keywords": ["AWS"]})
    second = client.post(
        "/api/tasks/search-plan/generate", json={"cities": ["上海"], "keywords": ["AWS"]}
    )
    assert second.json() == {"created": 0, "skipped": 1, "total": 1}


def test_generate_rejects_an_unknown_city_with_422(client):
    response = client.post(
        "/api/tasks/search-plan/generate", json={"cities": ["苏州"], "keywords": ["SRE"]}
    )
    assert response.status_code == 422


def test_generate_with_an_empty_body_uses_the_defaults(client):
    response = client.post("/api/tasks/search-plan/generate", json={})
    assert response.status_code == 200
    assert response.json() == {"created": 40, "skipped": 0, "total": 40}


def test_quick_prepare_returns_a_fresh_resume_bound_bounded_task(client, active_resume):
    response = client.post(
        "/api/tasks/search-plan/quick-prepare",
        json={"cities": ["上海", "北京"], "target_count": 5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["active_resume_name"] == active_resume.display_name
    assert body["keyword_source"] == "career_strategy"
    # Two cities x eight of the strategy's role directions, under the bounded
    # comprehensive-search ceiling.
    assert [task["city"] for task in body["tasks"]] == ["上海"] * 8 + ["北京"] * 8
    assert [task["city_id"] for task in body["tasks"]] == ["101020100"] * 8 + ["101010100"] * 8
    # Which direction leads is decided by the résumé and past results, not by a
    # fixed role name. What must hold: a Chinese direction leads, and the
    # response explains the choice, since the search runs without a second
    # confirmation.
    from app.services.search_direction_ranking import _is_chinese

    assert _is_chinese(body["tasks"][0]["keywords"]), "a Chinese direction leads"
    used = [d["keyword"] for d in body["directions"]]
    assert used, "the response must say which directions it used"
    assert set(used) == {task["keywords"] for task in body["tasks"]}
    assert body["direction_notes"], "and why it chose them"
    assert len({task["keywords"] for task in body["tasks"]}) == 8
    assert all(task["max_candidates"] == 5 for task in body["tasks"])
    assert all(task["run_status"] == "pending" for task in body["tasks"])


def test_quick_prepare_validates_the_hard_candidate_bound(client, active_resume):
    response = client.post(
        "/api/tasks/search-plan/quick-prepare",
        json={"cities": ["北京"], "target_count": 21},
    )
    assert response.status_code == 422


def test_quick_prepare_requires_at_least_one_city(client, active_resume):
    response = client.post(
        "/api/tasks/search-plan/quick-prepare",
        json={"cities": [], "target_count": 3},
    )
    assert response.status_code == 422


def test_a_manual_task_is_never_listed_as_a_search_plan_task(client, db):
    from app.services import task_console

    task_console.create_task(db, name="手动任务")
    client.post("/api/tasks/search-plan/generate", json={"cities": ["北京"], "keywords": ["SRE"]})

    listed = client.get("/api/tasks/search-plan").json()["items"]
    assert all(item["name"] != "手动任务" for item in listed)


# --------------------------------------------------------------------------
# run transitions over real HTTP
# --------------------------------------------------------------------------


@pytest.fixture
def plan_task_id(client, db):
    client.post("/api/tasks/search-plan/generate", json={"cities": ["北京"], "keywords": ["SRE"]})
    return db.query(JobSearchTask).filter(JobSearchTask.is_search_plan.is_(True)).one().id


@pytest.mark.parametrize("cap", [None, 1, 3, 20])
def test_task_candidate_cap_is_exposed_in_list_and_detail(client, db, plan_task_id, cap):
    task = db.get(JobSearchTask, plan_task_id)
    task.max_candidates = cap
    db.commit()
    detail = client.get(f"/api/tasks/search-plan/{plan_task_id}")
    assert detail.status_code == 200
    assert detail.json()["max_candidates"] == cap
    listed = client.get("/api/tasks/search-plan").json()["items"]
    assert next(item for item in listed if item["id"] == plan_task_id)["max_candidates"] == cap


def test_start_pause_resume_round_trip_over_http(client, plan_task_id):
    started = client.post(f"/api/tasks/{plan_task_id}/run/start")
    assert started.status_code == 200
    assert started.json()["run_status"] == "running"

    paused = client.post(f"/api/tasks/{plan_task_id}/run/pause")
    assert paused.status_code == 200
    assert paused.json()["run_status"] == "paused"

    resumed = client.post(f"/api/tasks/{plan_task_id}/run/resume")
    assert resumed.status_code == 200
    assert resumed.json()["run_status"] == "running"


def test_verification_then_resume_over_http(client, plan_task_id):
    client.post(f"/api/tasks/{plan_task_id}/run/start")
    verified = client.post(f"/api/tasks/{plan_task_id}/run/verification")
    assert verified.json()["run_status"] == "paused_verification"

    resumed = client.post(f"/api/tasks/{plan_task_id}/run/resume")
    assert resumed.json()["run_status"] == "running"


def test_login_required_then_resume_over_http(client, plan_task_id):
    client.post(f"/api/tasks/{plan_task_id}/run/start")
    paused = client.post(f"/api/tasks/{plan_task_id}/run/login-required")
    assert paused.status_code == 200
    assert paused.json()["run_status"] == "paused_login_required"
    assert paused.json()["paused_reason"] == "login_required"

    resumed = client.post(f"/api/tasks/{plan_task_id}/run/resume")
    assert resumed.status_code == 200
    assert resumed.json()["run_status"] == "running"


def test_cancel_over_http_is_idempotent(client, plan_task_id):
    first = client.post(f"/api/tasks/{plan_task_id}/run/cancel")
    assert first.json()["run_status"] == "cancelled"
    second = client.post(f"/api/tasks/{plan_task_id}/run/cancel")
    assert second.status_code == 200
    assert second.json()["run_status"] == "cancelled"


def test_fail_over_http_records_the_error(client, plan_task_id):
    client.post(f"/api/tasks/{plan_task_id}/run/start")
    failed = client.post(f"/api/tasks/{plan_task_id}/run/fail", json={"error": "selector_ambiguous"})
    assert failed.status_code == 200
    assert failed.json()["run_status"] == "failed"
    assert failed.json()["last_error"] == "selector_ambiguous"


def test_round_over_http_accumulates_and_auto_completes(client, plan_task_id):
    client.post(f"/api/tasks/{plan_task_id}/run/start")
    for _ in range(3):
        response = client.post(
            f"/api/tasks/{plan_task_id}/run/round",
            json={"observed": 1, "new": 0, "duplicate": 1},
        )
    assert response.json()["run_status"] == "completed"
    assert response.json()["duplicate_count"] == 3


def test_round_over_http_rejects_a_threshold_below_one(client, plan_task_id):
    client.post(f"/api/tasks/{plan_task_id}/run/start")
    response = client.post(
        f"/api/tasks/{plan_task_id}/run/round",
        json={"observed": 1, "new": 0, "duplicate": 1, "no_new_round_threshold": 0},
    )
    assert response.status_code == 422


def test_starting_an_already_running_task_over_http_422s(client, plan_task_id):
    client.post(f"/api/tasks/{plan_task_id}/run/start")
    second = client.post(f"/api/tasks/{plan_task_id}/run/start")
    assert second.status_code == 422


def test_get_one_search_plan_task_over_http(client, plan_task_id):
    response = client.get(f"/api/tasks/search-plan/{plan_task_id}")
    assert response.status_code == 200
    assert response.json()["id"] == plan_task_id
    assert response.json()["search_url"] is not None


def test_get_one_search_plan_task_404s_for_a_manual_task(client, db):
    from app.services import task_console

    manual = task_console.create_task(db, name="手动任务3")
    response = client.get(f"/api/tasks/search-plan/{manual.id}")
    assert response.status_code == 404


def test_complete_over_http(client, plan_task_id):
    client.post(f"/api/tasks/{plan_task_id}/run/start")
    response = client.post(f"/api/tasks/{plan_task_id}/run/complete")
    assert response.status_code == 200
    assert response.json()["run_status"] == "completed"


def test_run_endpoints_404_for_a_missing_task(client):
    response = client.post("/api/tasks/999999/run/start")
    assert response.status_code == 404


def test_run_endpoints_reject_a_manual_task_over_http(client, db):
    from app.services import task_console

    manual = task_console.create_task(db, name="手动任务2")
    response = client.post(f"/api/tasks/{manual.id}/run/start")
    assert response.status_code == 422


# --------------------------------------------------------------------------
# non-loopback rejection - same guard every other extension-adjacent route
# already uses and is exhaustively unit-tested for in test_extension_api.py
# --------------------------------------------------------------------------


def test_search_plan_routes_use_the_shared_loopback_guard():
    from app.api.routes.extension import require_loopback as extension_guard
    from app.api.routes.search_plan import require_loopback as search_plan_guard

    assert search_plan_guard is extension_guard


class _Peer:
    def __init__(self, host: str) -> None:
        self.client = type("C", (), {"host": host})()


@pytest.mark.parametrize("host", ["203.0.113.7", "8.8.8.8", "2001:db8::1"])
def test_a_non_loopback_peer_is_refused_by_the_search_plan_guard(host):
    from app.api.routes.extension import ForbiddenError
    from app.api.routes.search_plan import require_loopback

    with pytest.raises(ForbiddenError) as excinfo:
        require_loopback(_Peer(host))
    assert excinfo.value.status_code == 403


# --------------------------------------------------------------------------
# M4f observability delta - POST /run/state and the exact public aliases
# --------------------------------------------------------------------------


def test_report_state_updates_observability_fields_without_touching_run_status(client, plan_task_id):
    client.post(f"/api/tasks/{plan_task_id}/run/start")
    response = client.post(
        f"/api/tasks/{plan_task_id}/run/state",
        json={
            "current_url": "https://www.zhipin.com/web/geek/jobs",
            "scroll_round": 2,
            "visible_jobs": 5,
            "imported_jobs": 1,
            "current_candidate": "云计算工程师",
            "last_action": "opening_candidate",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["run_status"] == "running"
    assert body["current_url"] == "https://www.zhipin.com/web/geek/jobs"
    assert body["scroll_round"] == 2
    assert body["visible_jobs"] == 5
    assert body["imported_jobs"] == 1
    assert body["current_candidate"] == "云计算工程师"
    assert body["last_action"] == "opening_candidate"


def test_report_state_rejects_a_current_url_carrying_a_query_string(client, plan_task_id):
    client.post(f"/api/tasks/{plan_task_id}/run/start")
    response = client.post(
        f"/api/tasks/{plan_task_id}/run/state",
        json={"current_url": "https://www.zhipin.com/web/geek/jobs?city=101010100&query=SRE"},
    )
    assert response.status_code == 422


def test_the_task_response_exposes_the_exact_public_observability_names(client, plan_task_id):
    client.post(f"/api/tasks/{plan_task_id}/run/start")
    body = client.get(f"/api/tasks/search-plan/{plan_task_id}").json()

    assert body["task_id"] == body["id"] == plan_task_id
    assert body["state"] == body["run_status"] == "running"
    assert body["keyword"] == body["keywords"] == "SRE"
    assert body["observed_jobs"] == body["observed_count"] == 0
    assert body["new_jobs"] == body["new_count"] == 0
    assert body["duplicate_jobs"] == body["duplicate_count"] == 0
    assert "updated_at" in body
    assert "paused_reason" in body


def test_pause_accepts_an_optional_reason_and_reports_it_back(client, plan_task_id):
    client.post(f"/api/tasks/{plan_task_id}/run/start")
    response = client.post(f"/api/tasks/{plan_task_id}/run/pause", json={"reason": "user_pause"})
    assert response.status_code == 200
    assert response.json()["paused_reason"] == "user_pause"
