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


def test_status_in_groups_the_whole_post_application_pipeline(client):
    """The 已投递 tab must not lose a job the moment it progresses.

    Applying is a stage, not an endpoint. A job that got a reply or reached an
    interview is still one the user applied to, so a single-status filter is
    the wrong tool for answering "我投过哪些岗位".
    """
    applied = _create(client, company="投递公司").json()["job"]["id"]
    replied = _create(client, company="回复公司").json()["job"]["id"]
    interviewing = _create(client, company="面试公司").json()["job"]["id"]
    skipped = _create(client, company="跳过公司").json()["job"]["id"]

    # The workflow is a state machine, so each job walks the real path to its
    # stage rather than jumping straight there.
    for job_id, path in (
        (applied, ["applied"]),
        (replied, ["applied", "replied"]),
        (interviewing, ["applied", "replied", "interview"]),
        (skipped, ["skipped"]),
    ):
        for target in path:
            response = client.patch(f"/api/jobs/{job_id}", json={"status": target})
            assert response.status_code == 200, response.text

    pipeline = ["applied", "replied", "interview", "offer", "rejected"]
    grouped = client.get("/api/jobs", params=[("status_in", s) for s in pipeline]).json()
    assert {item["id"] for item in grouped["items"]} == {applied, replied, interviewing}
    assert grouped["total"] == 3

    # A single status still narrows within that group, which is what the stage
    # sub-filter does.
    narrowed = client.get(
        "/api/jobs",
        params=[("status_in", s) for s in pipeline] + [("status", "replied")],
    ).json()
    assert [item["id"] for item in narrowed["items"]] == [replied]

    # Facets ignore the status filter, so every tab keeps showing its real size
    # once one of them is selected.
    assert grouped["facets"]["statuses"]["skipped"] == 1

    assert client.get("/api/jobs", params={"status_in": "skipped"}).json()["total"] == 1
    assert client.get("/api/jobs").json()["total"] == 4


def test_list_pagination(client):
    for i in range(5):
        _create(client, company=f"公司{i}")
    page = client.get("/api/jobs", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 5
    assert len(page["items"]) == 2
    assert page["offset"] == 2


def test_early_career_cleanup_filter_reuses_deterministic_rule_and_only_returns_open_jobs(client):
    campus = _create(client, title="云平台工程师（校招）", company="校招公司").json()["job"]
    intern = _create(
        client,
        title="云平台工程师",
        company="实习公司",
        raw_description="负责云平台基础设施维护。面向2027届应届毕业生招聘，要求熟悉 Linux。" * 2,
    ).json()["job"]
    _create(
        client,
        title="云平台工程师",
        company="社招公司",
        raw_description="负责云平台基础设施维护。不限应届，有经验者优先，要求熟悉 Linux。" * 2,
    )
    decided = _create(client, title="运维实习生", company="已决定公司").json()["job"]
    assert client.post(f"/api/jobs/{decided['id']}/skip", json={"reason": "已处理"}).status_code == 200

    result = client.get("/api/jobs", params={"early_career_cleanup": True, "limit": 200}).json()
    assert result["total"] == 2
    assert {item["id"] for item in result["items"]} == {campus["id"], intern["id"]}
    assert all(item["status"] in {"new", "reviewed", "saved"} for item in result["items"])


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


def test_the_filter_menus_keep_listing_every_value_while_one_is_selected(client):
    """Same trap as the queue: counting facets over the filtered rows leaves each
    dropdown holding only the value already picked."""
    _create(client, company="北京公司", city="北京")
    _create(client, company="杭州公司", city="杭州")

    unfiltered = client.get("/api/jobs").json()["facets"]
    assert set(unfiltered["cities"]) == {"北京", "杭州"}

    filtered = client.get("/api/jobs", params={"city": "北京"}).json()
    assert [i["city"] for i in filtered["items"]] == ["北京"], "the rows are still filtered"
    assert set(filtered["facets"]["cities"]) == {"北京", "杭州"}
    assert set(filtered["facets"]["statuses"]) == {"new"}


def test_delete_removes_a_job_with_a_status_trail(client):
    job_id = _create(client).json()["job"]["id"]
    assert client.patch(f"/api/jobs/{job_id}", json={"status": "skipped"}).status_code == 200

    assert client.delete(f"/api/jobs/{job_id}").status_code == 200
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert all(item["id"] != job_id for item in client.get("/api/jobs").json()["items"])


def test_skip_moves_the_status_and_is_visible_in_the_list(client):
    """`跳过` must actually change the row the list returns, not just 200."""
    job_id = _create(client).json()["job"]["id"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "new"

    updated = client.patch(f"/api/jobs/{job_id}", json={"status": "skipped"})
    assert updated.status_code == 200
    assert updated.json()["status"] == "skipped"

    listed = next(i for i in client.get("/api/jobs").json()["items"] if i["id"] == job_id)
    assert listed["status"] == "skipped"

    # Re-sending the status the job already has is a documented no-op: the route
    # only calls the workflow when the target differs. It returns 200 and changes
    # nothing, which is indistinguishable from success - so the UI disables the
    # button for the status a job is already in rather than firing a request that
    # can only look like it did nothing.
    again = client.patch(f"/api/jobs/{job_id}", json={"status": "skipped"})
    assert again.status_code == 200
    assert again.json()["status"] == "skipped"
    events = client.get(f"/api/jobs/{job_id}/application-events").json()
    assert len([e for e in events if e["event_type"] == "status_changed"]) == 1


def test_dashboard_summary_with_no_analyses(client):
    _create(client)
    summary = client.get("/api/dashboard/summary").json()
    assert summary["total_jobs"] == 1
    assert summary["analyzed_jobs"] == 0
    assert summary["unanalyzed_jobs"] == 1
    assert summary["average_score"] is None
    assert summary["openai_configured"] is False
