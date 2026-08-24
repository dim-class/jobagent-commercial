"""Job CRUD, duplicate prevention, filtering, and status tracking."""

from __future__ import annotations

from tests.conftest import HELPDESK_JD, make_job_payload


def _create(client, **overrides):
    return client.post("/api/jobs", json=make_job_payload(**overrides))


def test_create_job_normalizes_fields(client):
    response = _create(client, city="北京市", raw_description="  职责  \n\n\n\n要求：熟悉 AWS 与 Linux。 ")
    assert response.status_code == 201

    job = response.json()["job"]
    assert job["city"] == "北京"
    assert job["normalized_description"] == "职责\n\n要求：熟悉 AWS 与 Linux。"
    assert job["raw_description"].startswith("  职责")  # original preserved
    assert job["status"] == "new"
    assert len(job["content_hash"]) == 64


def test_create_job_rejects_a_too_short_jd(client):
    assert _create(client, raw_description="太短了").status_code == 422


def test_duplicate_job_is_rejected_with_the_existing_id(client):
    first = _create(client)
    assert first.status_code == 201
    existing_id = first.json()["job"]["id"]

    second = _create(client)
    assert second.status_code == 409

    body = second.json()
    assert body["code"] == "duplicate"
    assert body["detail"]["existing_job_id"] == existing_id


def test_duplicate_detection_ignores_whitespace_differences(client):
    base = make_job_payload()["raw_description"]
    assert _create(client, raw_description=base).status_code == 201
    reflowed = base.replace("\n", "\n\n\n").replace("：", "：  ")
    assert _create(client, raw_description=reflowed).status_code == 409


def test_different_company_is_not_a_duplicate(client):
    assert _create(client).status_code == 201
    assert _create(client, company="另一家公司").status_code == 201


def test_duplicate_detection_by_source_and_external_id(client):
    assert _create(client, source="demo", external_id="abc-123").status_code == 201
    # Different JD text, same source + external id -> still the same posting.
    response = _create(
        client,
        source="demo",
        external_id="abc-123",
        raw_description="完全不同的岗位描述内容，负责数据库运维工作。" * 3,
    )
    assert response.status_code == 409


def test_list_and_filter_jobs(client):
    _create(client, city="北京", title="云计算工程师")
    _create(client, city="上海", title="DevOps Engineer", company="上海公司")
    _create(client, city="深圳", title="IT 桌面运维工程师", company="深圳公司", raw_description=HELPDESK_JD)

    assert client.get("/api/jobs").json()["total"] == 3
    assert client.get("/api/jobs", params={"city": "北京"}).json()["total"] == 1
    assert client.get("/api/jobs", params={"city": "北京市"}).json()["total"] == 1
    assert client.get("/api/jobs", params={"keyword": "DevOps"}).json()["total"] == 1
    assert client.get("/api/jobs", params={"keyword": "Helpdesk"}).json()["total"] == 1
    assert client.get("/api/jobs", params={"analyzed": False}).json()["total"] == 3
    assert client.get("/api/jobs", params={"analyzed": True}).json()["total"] == 0
    # No analysis yet, so a score filter excludes everything.
    assert client.get("/api/jobs", params={"min_score": 60}).json()["total"] == 0

    facets = client.get("/api/jobs").json()["facets"]
    assert facets["cities"]["北京"] == 1
    assert facets["statuses"]["new"] == 3


def test_list_pagination(client):
    for i in range(5):
        _create(client, company=f"公司{i}")
    page = client.get("/api/jobs", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 5
    assert len(page["items"]) == 2
    assert page["offset"] == 2


def test_get_job_detail_and_404(client):
    job_id = _create(client).json()["job"]["id"]
    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["id"] == job_id
    assert detail["events"], "creation should be recorded as an event"

    missing = client.get("/api/jobs/999999")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"


def test_status_tracking_writes_an_event(client):
    job_id = _create(client).json()["job"]["id"]

    response = client.patch(f"/api/jobs/{job_id}", json={"status": "saved", "note": "先收藏"})
    assert response.status_code == 200
    assert response.json()["status"] == "saved"

    events = client.get(f"/api/jobs/{job_id}").json()["events"]
    kinds = {e["event_type"] for e in events}
    assert "status_changed" in kinds
    assert any(e["notes"] == "先收藏" for e in events)

    assert client.get("/api/jobs", params={"status": "saved"}).json()["total"] == 1


def test_patch_normalizes_city(client):
    job_id = _create(client).json()["job"]["id"]
    updated = client.patch(f"/api/jobs/{job_id}", json={"city": "杭州市"}).json()
    assert updated["city"] == "杭州"


def test_delete_job(client):
    job_id = _create(client).json()["job"]["id"]
    assert client.delete(f"/api/jobs/{job_id}").status_code == 200
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert client.delete(f"/api/jobs/{job_id}").status_code == 404


def test_dashboard_summary_with_no_analyses(client):
    _create(client)
    summary = client.get("/api/dashboard/summary").json()
    assert summary["total_jobs"] == 1
    assert summary["analyzed_jobs"] == 0
    assert summary["unanalyzed_jobs"] == 1
    assert summary["average_score"] is None
    assert summary["openai_configured"] is False
