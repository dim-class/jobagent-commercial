"""M5a: task-scoped candidate matching + human review.

Every test here patches ``app.services.job_matcher.run_job_match``. No test
makes a real OpenAI call.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import make_job_payload
from tests.test_analysis import build_result


def _fake_run_job_match(monkeypatch, calls: list[dict[str, Any]] | None = None, *, boom: bool = False):
    calls = calls if calls is not None else []

    async def _fake(**kwargs):
        calls.append(kwargs)
        if boom:
            raise RuntimeError("调用 OpenAI 失败：TimeoutError")
        return build_result()

    monkeypatch.setattr("app.services.job_matcher.run_job_match", _fake)
    return calls


def _create_task(client) -> int:
    response = client.post("/api/tasks", json={"name": "云计算-北京"})
    assert response.status_code == 200
    return response.json()["id"]


def _create_job_candidate(client, task_id: int, **overrides) -> int:
    job_response = client.post("/api/jobs", json=make_job_payload(**overrides))
    assert job_response.status_code == 201
    job_id = job_response.json()["job"]["id"]
    assoc = client.post(f"/api/tasks/{task_id}/candidates", json={"job_id": job_id})
    assert assoc.status_code == 200
    return job_id


def test_match_plan_is_read_only_and_reports_pending_count(client, active_resume, monkeypatch):
    calls = _fake_run_job_match(monkeypatch)
    task_id = _create_task(client)
    job_id = _create_job_candidate(client, task_id)

    plan = client.get(f"/api/tasks/{task_id}/match-plan")
    assert plan.status_code == 200
    body = plan.json()
    assert body["total_candidates"] == 1
    assert body["pending_analyses"] == 1
    assert body["pending_total"] == 1
    assert body["candidates"][0]["job_id"] == job_id
    assert body["candidates"][0]["cached"] is False
    assert body["model"]
    assert body["active_resume_id"] == active_resume.id
    # Read-only: no call was made just by planning.
    assert calls == []


def test_match_run_without_confirmation_is_rejected_and_spends_nothing(client, active_resume, monkeypatch):
    calls = _fake_run_job_match(monkeypatch)
    task_id = _create_task(client)
    _create_job_candidate(client, task_id)

    response = client.post(f"/api/tasks/{task_id}/match-run", json={"confirmed": False})
    assert response.status_code == 422
    assert calls == []


def test_confirmed_match_run_analyzes_and_the_cache_makes_a_repeat_run_free(client, active_resume, monkeypatch):
    calls = _fake_run_job_match(monkeypatch)
    task_id = _create_task(client)
    job_id = _create_job_candidate(client, task_id)

    response = client.post(f"/api/tasks/{task_id}/match-run", json={"confirmed": True})
    assert response.status_code == 200
    body = response.json()
    assert body["analyzed"] == 1
    assert body["failed"] == 0
    assert body["results"][0]["job_id"] == job_id
    assert body["results"][0]["overall_score"] == 86
    assert len(calls) == 1

    # A second run needs no confirmation and makes no further API call - the
    # candidate is now cached.
    second = client.post(f"/api/tasks/{task_id}/match-run", json={"confirmed": False})
    assert second.status_code == 200
    assert second.json()["analyzed"] == 0
    assert len(calls) == 1

    plan = client.get(f"/api/tasks/{task_id}/match-plan").json()
    assert plan["pending_total"] == 0
    assert plan["candidates"][0]["cached"] is True
    assert plan["candidates"][0]["overall_score"] == 86


def test_one_failing_candidate_never_aborts_the_rest_of_the_run(client, active_resume, monkeypatch):
    task_id = _create_task(client)
    first_job = _create_job_candidate(client, task_id, title="职位A")
    second_job = _create_job_candidate(client, task_id, title="职位B")

    async def _mixed(**kwargs):
        # `job` is passed by keyword in `run_job_match` - matched on title.
        if kwargs["job"]["title"] == "职位A":
            raise RuntimeError("调用 OpenAI 失败：TimeoutError")
        return build_result()

    monkeypatch.setattr("app.services.job_matcher.run_job_match", _mixed)

    response = client.post(f"/api/tasks/{task_id}/match-run", json={"confirmed": True})
    assert response.status_code == 200
    body = response.json()
    assert body["analyzed"] == 1
    assert body["failed"] == 1
    by_job = {r["job_id"]: r for r in body["results"]}
    assert by_job[first_job]["error"]
    assert by_job[first_job]["overall_score"] is None
    assert by_job[second_job]["overall_score"] == 86
    assert by_job[second_job]["error"] is None


def test_end_to_end_route_never_leaks_canary_error_metadata(client, active_resume, monkeypatch):
    """A real SDK `RateLimitError` whose `code`/`request_id` carry canary
    private strings (the exact acceptance reproduction) must never reach the
    `/match-run` JSON response, no matter which layer catches it."""
    import httpx
    import openai

    canary_code = "SENSITIVE_CODE_CANARY_e2e"
    canary_request_id = "SENSITIVE_REQID_CANARY_e2e"

    async def _boom(**kwargs):
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        response = httpx.Response(
            429,
            request=request,
            json={"error": {"message": "irrelevant private detail", "code": canary_code}},
            headers={"x-request-id": canary_request_id},
        )
        raise openai.RateLimitError(
            "Error code: 429 - irrelevant private detail",
            response=response,
            body={"code": canary_code},
        )

    monkeypatch.setattr("app.services.job_matcher.run_job_match", _boom)
    task_id = _create_task(client)
    _create_job_candidate(client, task_id)

    response = client.post(f"/api/tasks/{task_id}/match-run", json={"confirmed": True})
    assert response.status_code == 200
    assert canary_code not in response.text
    assert canary_request_id not in response.text

    result = response.json()["results"][0]
    assert result["category"] == "rate_limit_exceeded"
    assert result["http_status"] == 429
    assert result["error_code"] is None
    assert result["request_id"] is None


def test_upstream_error_surfaces_safe_classification_fields(client, active_resume, monkeypatch):
    """`job_match_agent.run_job_match` wraps a classified OpenAI failure in an
    `UpstreamError` carrying category/http_status/error_code/request_id in
    `detail` - the per-candidate outcome must reuse those fields as-is rather
    than falling back to a generic reclassification, and must never leak the
    exception's own raw text."""
    from app.core.errors import UpstreamError

    async def _boom(**kwargs):
        raise UpstreamError(
            "OpenAI 鉴权失败，请检查 API Key 是否正确、未过期。",
            detail={
                "model": "gpt-test",
                "category": "authentication",
                "http_status": 401,
                "error_code": "invalid_api_key",
                "request_id": "req_abc123",
            },
        )

    monkeypatch.setattr("app.services.job_matcher.run_job_match", _boom)
    task_id = _create_task(client)
    job_id = _create_job_candidate(client, task_id)

    response = client.post(f"/api/tasks/{task_id}/match-run", json={"confirmed": True})
    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == 1
    result = body["results"][0]
    assert result["job_id"] == job_id
    assert result["category"] == "authentication"
    assert result["http_status"] == 401
    assert result["error_code"] == "invalid_api_key"
    assert result["request_id"] == "req_abc123"
    assert result["error"] == "OpenAI 鉴权失败，请检查 API Key 是否正确、未过期。"


def test_unclassified_exception_still_gets_a_safe_generic_classification(
    client, active_resume, monkeypatch
):
    """A raw, non-`AppError` exception (never expected in practice, but must
    never abort the run or leak its own text) still gets a safe "unknown"
    classification rather than `str(exc)`."""

    async def _boom(**kwargs):
        raise RuntimeError("some raw internal detail that must never reach the UI")

    monkeypatch.setattr("app.services.job_matcher.run_job_match", _boom)
    task_id = _create_task(client)
    job_id = _create_job_candidate(client, task_id)

    response = client.post(f"/api/tasks/{task_id}/match-run", json={"confirmed": True})
    assert response.status_code == 200
    body = response.json()
    result = body["results"][0]
    assert result["job_id"] == job_id
    assert result["category"] == "unknown"
    assert "raw internal detail" not in result["error"]


def test_match_plan_404s_for_a_missing_task(client, active_resume):
    response = client.get("/api/tasks/999999/match-plan")
    assert response.status_code == 404


def test_plan_and_run_honor_a_lowered_configured_cap(client, active_resume, monkeypatch):
    """CLAUDE.md: 'Read from config, never hardcoded.' Lowering
    `settings.max_analyses_per_run` must genuinely lower both the plan's
    reported pending/cap and how many candidates one confirmed run scores -
    never a module-level constant that ignores it."""
    from app.core.config import Settings

    lowered = Settings(max_analyses_per_run=1)
    monkeypatch.setattr("app.services.task_matching.get_settings", lambda: lowered)
    calls = _fake_run_job_match(monkeypatch)

    task_id = _create_task(client)
    first_job = _create_job_candidate(client, task_id, title="职位A")
    second_job = _create_job_candidate(client, task_id, title="职位B")

    plan = client.get(f"/api/tasks/{task_id}/match-plan").json()
    assert plan["cap"] == 1
    assert plan["pending_total"] == 2
    assert plan["pending_analyses"] == 1  # capped, even though 2 are actually pending

    # Confirming the capped count (not the full pending total) must succeed.
    response = client.post(f"/api/tasks/{task_id}/match-run", json={"confirmed": True})
    assert response.status_code == 200
    body = response.json()
    assert body["analyzed"] == 1
    assert len(calls) == 1  # only one API call was actually made this run

    by_job = {r["job_id"]: r for r in body["results"]}
    scored = [jid for jid in (first_job, second_job) if by_job[jid]["overall_score"] == 86]
    unscored = [jid for jid in (first_job, second_job) if by_job[jid]["overall_score"] is None]
    assert len(scored) == 1
    assert len(unscored) == 1
    assert by_job[unscored[0]]["error"]


# --------------------------------------------------------------------------
# M5a must preserve human Job.status - see CLAUDE.md and task_matching.py's
# module docstring ("`Job.status` is never touched here"). `job_matcher._persist`
# is shared with the standalone `/api/jobs/{id}/analyze` family, which *does*
# flip a fresh job to `reviewed` on purpose (a human is looking directly at
# it); task-scoped matching must opt out of that shared side effect.
# --------------------------------------------------------------------------


def _job_status(client, job_id: int) -> str:
    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    return response.json()["status"]


def test_fresh_successful_task_match_keeps_job_status_new(client, active_resume, monkeypatch):
    _fake_run_job_match(monkeypatch)
    task_id = _create_task(client)
    job_id = _create_job_candidate(client, task_id)
    assert _job_status(client, job_id) == "new"

    response = client.post(f"/api/tasks/{task_id}/match-run", json={"confirmed": True})
    assert response.status_code == 200
    assert response.json()["analyzed"] == 1
    assert _job_status(client, job_id) == "new"


def test_task_match_never_changes_an_existing_non_new_status(client, active_resume, monkeypatch):
    """A candidate the human already acted on (e.g. `saved`) must stay exactly
    where the human left it - matching must never move it back toward `new`
    or forward toward `reviewed`."""
    _fake_run_job_match(monkeypatch)
    task_id = _create_task(client)
    job_id = _create_job_candidate(client, task_id)

    saved = client.patch(f"/api/jobs/{job_id}", json={"status": "saved"})
    assert saved.status_code == 200
    assert _job_status(client, job_id) == "saved"

    response = client.post(f"/api/tasks/{task_id}/match-run", json={"confirmed": True})
    assert response.status_code == 200
    assert response.json()["analyzed"] == 1
    assert _job_status(client, job_id) == "saved"


def test_cached_repeat_task_match_stays_free_and_never_touches_status(
    client, active_resume, monkeypatch
):
    calls = _fake_run_job_match(monkeypatch)
    task_id = _create_task(client)
    job_id = _create_job_candidate(client, task_id)

    first = client.post(f"/api/tasks/{task_id}/match-run", json={"confirmed": True})
    assert first.status_code == 200
    assert first.json()["analyzed"] == 1
    assert len(calls) == 1
    assert _job_status(client, job_id) == "new"

    second = client.post(f"/api/tasks/{task_id}/match-run", json={"confirmed": False})
    assert second.status_code == 200
    assert second.json()["analyzed"] == 0
    assert len(calls) == 1  # still cached - no further API call
    assert _job_status(client, job_id) == "new"


def test_failed_task_match_never_touches_status(client, active_resume, monkeypatch):
    _fake_run_job_match(monkeypatch, boom=True)
    task_id = _create_task(client)
    job_id = _create_job_candidate(client, task_id)

    response = client.post(f"/api/tasks/{task_id}/match-run", json={"confirmed": True})
    assert response.status_code == 200
    assert response.json()["failed"] == 1
    assert _job_status(client, job_id) == "new"


def test_task_match_still_saves_score_and_analyzed_event_despite_no_status_change(
    client, active_resume, monkeypatch
):
    """Suppressing the `Job.status` side effect must not suppress anything
    else `_persist` does: the analysis row and its `analyzed` event are still
    written exactly as before."""
    _fake_run_job_match(monkeypatch)
    task_id = _create_task(client)
    job_id = _create_job_candidate(client, task_id)

    response = client.post(f"/api/tasks/{task_id}/match-run", json={"confirmed": True})
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["overall_score"] == 86
    assert result["verdict"]

    events = client.get(f"/api/jobs/{job_id}").json()["events"]
    assert any(e["event_type"] == "analyzed" for e in events)


def test_standalone_analyze_endpoint_still_marks_reviewed(client, active_resume, monkeypatch):
    """Regression guard: the legacy single-job path this delta must NOT
    change keeps flipping a fresh job to `reviewed`."""
    _fake_run_job_match(monkeypatch)
    job_response = client.post("/api/jobs", json=make_job_payload())
    assert job_response.status_code == 201
    job_id = job_response.json()["job"]["id"]
    assert _job_status(client, job_id) == "new"

    response = client.post(f"/api/jobs/{job_id}/analyze")
    assert response.status_code == 200
    assert _job_status(client, job_id) == "reviewed"


def test_human_status_change_during_awaited_task_match_is_preserved(
    client, active_resume, monkeypatch
):
    """The model call is awaited inside `analyze_job`, after `job` is loaded
    but before `_persist` runs. If a human changes the job's real status in
    that window - through an independent DB session, exactly like a second,
    concurrent request would - task-scoped matching must not clobber it
    afterwards. This only holds because M5a never reads-then-writes
    `Job.status` at all (`mark_reviewed=False`); there is no stale snapshot to
    restore and nothing to race."""
    from app.db.session import SessionLocal
    from app.schemas.application import SkipRequest
    from app.services import application_workflow

    task_id = _create_task(client)
    job_id = _create_job_candidate(client, task_id)

    async def _fake_with_concurrent_human_skip(**kwargs):
        # Simulate a human's own request - a different DB session - acting on
        # this exact job while this call is "awaiting" the model.
        session = SessionLocal()
        try:
            application_workflow.skip(session, job_id, SkipRequest())
        finally:
            session.close()
        return build_result()

    monkeypatch.setattr(
        "app.services.job_matcher.run_job_match", _fake_with_concurrent_human_skip
    )

    response = client.post(f"/api/tasks/{task_id}/match-run", json={"confirmed": True})
    assert response.status_code == 200
    assert response.json()["analyzed"] == 1
    # The human's concurrent decision survives untouched - not reverted to
    # `new`, and not forced to `reviewed`.
    assert _job_status(client, job_id) == "skipped"
