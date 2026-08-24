"""Health and root endpoints."""

from __future__ import annotations


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["version"]
    assert body["auto_apply"] is False
    assert body["models"] == {"fast": "test-model-fast", "smart": "test-model-smart"}


def test_health_never_leaks_the_api_key(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-appear-in-a-response")
    raw = client.get("/health").text
    assert "sk-should-never-appear" not in raw


def test_root_reports_auto_apply_disabled(client):
    body = client.get("/").json()
    assert body["auto_apply"] is False
    assert body["health"] == "/health"


def test_settings_endpoint_hides_the_key(client):
    body = client.get("/api/settings").json()
    assert body["openai_configured"] is False
    assert body["auto_apply"] is False
    assert "openai_api_key" not in {k.lower() for k in body}


def test_a_validator_error_stays_a_422(client):
    """Pydantic stores the raised exception object in ``ctx``, which is not
    JSON-serializable. Without encoding it the handler would 500 instead."""
    response = client.post(
        "/api/jobs/1/mark-applied", json={"confirmed": True, "resume_usage": "used"}
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["detail"]["errors"], "the offending field is still reported"
