"""AI analysis: caching, guardrails, model selection, and missing-key behaviour.

Every test here patches ``app.services.job_matcher.run_job_match``. No test in
this file (or anywhere in the suite) makes a real OpenAI call.
"""

from __future__ import annotations

import pytest

from app.agents.job_match_agent import MissingApiKeyError
from app.core.errors import UpstreamError
from app.schemas.analysis import JobMatchResult
from tests.conftest import HELPDESK_JD, make_job_payload


def build_result(**overrides) -> JobMatchResult:
    payload = {
        "overall_score": 86,
        "verdict": "apply",
        "role_fit_score": 88,
        "skill_fit_score": 84,
        "experience_fit_score": 80,
        "location_fit_score": 100,
        "salary_fit_score": 78,
        "matched_skills": ["AWS", "Linux", "Terraform", "AWS"],
        "missing_skills": ["Kubernetes"],
        "strengths": ["有 AWS 生产环境运维经验", "熟悉 Terraform 基础设施即代码"],
        "gaps": ["Kubernetes 生产经验相对有限"],
        "risk_flags": [],
        "experience_gap": "岗位要求 1-3 年，候选人 5 年，无差距",
        "role_summary": "负责 AWS 云平台日常运维与自动化",
        "reasoning_summary": "技能与岗位高度重合，城市匹配，建议投递。",
        "greeting_message": "您好，我有 5 年云计算与系统运维经验，熟悉 AWS 与 Terraform，期待进一步沟通。",
    }
    payload.update(overrides)
    return JobMatchResult.model_validate(payload)


@pytest.fixture
def fake_agent(monkeypatch):
    """Patch the agent call and record how many times the model was invoked."""
    calls: list[dict] = []
    result_box = {"result": build_result()}

    async def _fake_run_job_match(**kwargs):
        calls.append(kwargs)
        return result_box["result"]

    monkeypatch.setattr("app.services.job_matcher.run_job_match", _fake_run_job_match)
    return type("FakeAgent", (), {"calls": calls, "box": result_box})


def _create_job(client, **overrides) -> int:
    response = client.post("/api/jobs", json=make_job_payload(**overrides))
    assert response.status_code == 201
    return response.json()["job"]["id"]


# --------------------------------------------------------------------------
# missing API key
# --------------------------------------------------------------------------


def test_analyze_without_api_key_returns_a_config_error(client, active_resume):
    """The app must start and browse fine; only AI endpoints refuse."""
    job_id = _create_job(client)
    response = client.post(f"/api/jobs/{job_id}/analyze", json={})

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "configuration_error"
    assert "OPENAI_API_KEY" in body["message"]
    assert body["detail"]["env_var"] == "OPENAI_API_KEY"


def test_require_openai_raises_when_unset(settings):
    from app.agents.job_match_agent import require_openai

    with pytest.raises(MissingApiKeyError):
        require_openai(settings)


def test_other_endpoints_still_work_without_a_key(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/jobs").status_code == 200
    assert client.get("/api/dashboard/summary").status_code == 200


def test_analyze_without_a_resume_explains_what_to_do(client):
    job_id = _create_job(client)
    response = client.post(f"/api/jobs/{job_id}/analyze", json={})
    assert response.status_code == 422
    assert response.json()["detail"]["action"] == "upload_resume"


# --------------------------------------------------------------------------
# happy path + caching
# --------------------------------------------------------------------------


def test_analyze_stores_the_result(client, active_resume, fake_agent):
    job_id = _create_job(client)
    body = client.post(f"/api/jobs/{job_id}/analyze", json={}).json()

    assert body["meta"]["cached"] is False
    assert body["meta"]["model"] == "test-model-fast"
    assert body["meta"]["prompt_version"]
    assert len(body["meta"]["cache_key"]) == 64

    result = body["result"]
    assert result["overall_score"] == 86
    assert result["verdict"] == "apply"
    assert result["matched_skills"] == ["AWS", "Linux", "Terraform"]  # de-duplicated
    assert result["greeting_message"]

    # the deterministic feature block travels with the response
    assert body["pre_analysis"]["city_match"] is True
    assert isinstance(body["pre_analysis"]["heuristic_score"], int)

    stored = client.get(f"/api/jobs/{job_id}/analysis").json()
    assert stored["result"]["overall_score"] == 86
    assert stored["meta"]["cached"] is True


def test_second_analyze_is_a_cache_hit(client, active_resume, fake_agent):
    job_id = _create_job(client)
    client.post(f"/api/jobs/{job_id}/analyze", json={})
    second = client.post(f"/api/jobs/{job_id}/analyze", json={}).json()

    assert second["meta"]["cached"] is True
    assert len(fake_agent.calls) == 1, "a cache hit must not call the model"


def test_force_bypasses_the_cache(client, active_resume, fake_agent):
    job_id = _create_job(client)
    client.post(f"/api/jobs/{job_id}/analyze", json={})

    fake_agent.box["result"] = build_result(overall_score=91, verdict="strong_apply")
    forced = client.post(f"/api/jobs/{job_id}/analyze", json={"force": True}).json()

    assert len(fake_agent.calls) == 2
    assert forced["meta"]["cached"] is False
    assert forced["result"]["overall_score"] == 91
    # the forced run replaces the cached row rather than piling up duplicates
    assert client.get(f"/api/jobs/{job_id}/analysis").json()["result"]["overall_score"] == 91


def test_cache_key_changes_with_the_model(client, active_resume, fake_agent):
    job_id = _create_job(client)
    fast = client.post(f"/api/jobs/{job_id}/analyze", json={}).json()
    smart = client.post(f"/api/jobs/{job_id}/reanalyze-smart").json()

    assert smart["meta"]["model"] == "test-model-smart"
    assert smart["meta"]["cached"] is False
    assert smart["meta"]["cache_key"] != fast["meta"]["cache_key"]
    assert len(fake_agent.calls) == 2

    # ...and the smart result is itself cached from then on
    again = client.post(f"/api/jobs/{job_id}/reanalyze-smart").json()
    assert again["meta"]["cached"] is True
    assert len(fake_agent.calls) == 2


def test_cache_key_changes_with_the_career_strategy(client, active_resume, fake_agent):
    job_id = _create_job(client)
    first = client.post(f"/api/jobs/{job_id}/analyze", json={}).json()

    strategy = client.get("/api/settings/career-strategy").json()["strategy"]
    strategy["target_cities"] = ["成都"]
    assert client.put("/api/settings/career-strategy", json=strategy).status_code == 200

    second = client.post(f"/api/jobs/{job_id}/analyze", json={}).json()
    assert second["meta"]["cache_key"] != first["meta"]["cache_key"]
    assert second["meta"]["cached"] is False
    assert len(fake_agent.calls) == 2


def test_analysis_marks_the_job_reviewed_and_logs_an_event(client, active_resume, fake_agent):
    job_id = _create_job(client)
    client.post(f"/api/jobs/{job_id}/analyze", json={})

    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["status"] == "reviewed"
    assert any(e["event_type"] == "analyzed" for e in detail["events"])
    assert detail["latest_analysis"]["overall_score"] == 86


# --------------------------------------------------------------------------
# guardrails through the API
# --------------------------------------------------------------------------


def test_excluded_role_is_capped_end_to_end(client, active_resume, fake_agent):
    job_id = _create_job(
        client,
        title="IT 桌面运维工程师（Helpdesk）",
        company="示例商贸",
        city="深圳",
        salary_text="8k-11k",
        raw_description=HELPDESK_JD,
    )
    fake_agent.box["result"] = build_result(overall_score=88, verdict="apply")
    result = client.post(f"/api/jobs/{job_id}/analyze", json={}).json()["result"]

    assert result["overall_score"] <= 45
    assert result["verdict"] == "skip"
    assert any("排除关键词" in flag for flag in result["risk_flags"])


def test_out_of_range_model_scores_are_clamped(client, active_resume, fake_agent):
    job_id = _create_job(client)
    fake_agent.box["result"] = build_result(overall_score=180, role_fit_score=-5)
    result = client.post(f"/api/jobs/{job_id}/analyze", json={}).json()["result"]
    assert result["overall_score"] == 100
    assert result["role_fit_score"] == 0


def test_upstream_failure_becomes_a_502(client, active_resume, monkeypatch):
    async def _boom(**kwargs):
        raise UpstreamError("调用 OpenAI 失败：TimeoutError")

    monkeypatch.setattr("app.services.job_matcher.run_job_match", _boom)
    job_id = _create_job(client)
    response = client.post(f"/api/jobs/{job_id}/analyze", json={})
    assert response.status_code == 502
    assert response.json()["code"] == "upstream_error"


# --------------------------------------------------------------------------
# batch + dashboard + filtering on scores
# --------------------------------------------------------------------------


def test_batch_analysis_respects_max_analyses_per_run(client, active_resume, fake_agent, settings):
    assert settings.max_analyses_per_run == 3
    for i in range(5):
        _create_job(client, company=f"公司{i}")

    body = client.post("/api/jobs/analyze-batch", json={}).json()
    assert body["requested"] == 5
    assert body["limit"] == 3
    assert body["analyzed"] == 3
    assert len(fake_agent.calls) == 3


def test_score_and_verdict_filters_use_the_latest_analysis(client, active_resume, fake_agent):
    high = _create_job(client, company="高分公司")
    low = _create_job(client, company="低分公司")

    client.post(f"/api/jobs/{high}/analyze", json={})
    fake_agent.box["result"] = build_result(overall_score=42, verdict="skip")
    client.post(f"/api/jobs/{low}/analyze", json={})

    assert client.get("/api/jobs", params={"min_score": 80}).json()["total"] == 1
    assert client.get("/api/jobs", params={"verdict": "skip"}).json()["total"] == 1
    assert client.get("/api/jobs", params={"analyzed": True}).json()["total"] == 2

    summary = client.get("/api/dashboard/summary").json()
    assert summary["analyzed_jobs"] == 2
    assert summary["recommended"] == 1
    assert summary["skip"] == 1
    assert summary["average_score"] == 64.0
    assert summary["top_jobs"][0]["company"] == "高分公司"
