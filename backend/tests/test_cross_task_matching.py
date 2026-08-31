"""M5b offline acceptance.  Every model path is mocked; no paid call exists."""

from __future__ import annotations

from typing import Any

from app.models import JobSearchTask, SearchTaskRunStatus, TaskCandidate
from tests.conftest import make_job_payload
from tests.test_analysis import build_result


def _completed_task(db, name: str, *, city: str = "北京", keyword: str = "云平台") -> JobSearchTask:
    task = JobSearchTask(
        name=name,
        city=city,
        city_id="101010100",
        keywords=keyword,
        is_search_plan=True,
        run_status=SearchTaskRunStatus.completed,
        max_candidates=20,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _job(client, title: str) -> int:
    response = client.post("/api/jobs", json=make_job_payload(title=title))
    assert response.status_code == 201
    return response.json()["job"]["id"]


def _associate(db, task_id: int, job_id: int) -> None:
    db.add(TaskCandidate(task_id=task_id, job_id=job_id))
    db.commit()


def _fake_model(monkeypatch, *, fail_titles: set[str] | None = None) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    fail_titles = fail_titles or set()

    async def fake(**kwargs):
        calls.append(kwargs)
        if kwargs["job"]["title"] in fail_titles:
            raise RuntimeError("private upstream detail")
        return build_result()

    monkeypatch.setattr("app.services.job_matcher.run_job_match", fake)
    return calls


def test_plan_deduplicates_one_job_across_completed_tasks_and_is_read_only(
    client, db, active_resume, monkeypatch
):
    calls = _fake_model(monkeypatch)
    first = _completed_task(db, "北京-云平台")
    second = _completed_task(db, "北京-DevOps", keyword="DevOps")
    job_id = _job(client, "共享岗位")
    _associate(db, first.id, job_id)
    _associate(db, second.id, job_id)

    response = client.post(
        "/api/task-match-batches/plan", json={"task_ids": [second.id, first.id]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["task_ids"] == sorted([first.id, second.id])
    assert body["unique_jobs"] == 1
    assert body["cached_jobs"] == 0
    assert body["pending_jobs"] == 1
    assert body["max_new_calls"] == 1
    assert {source["task_id"] for source in body["items"][0]["sources"]} == {
        first.id,
        second.id,
    }
    assert calls == []


def test_plan_rejects_non_completed_or_non_search_plan_tasks(client, db, active_resume):
    task = JobSearchTask(name="未完成", is_search_plan=True, run_status=SearchTaskRunStatus.pending)
    db.add(task)
    db.commit()
    response = client.post("/api/task-match-batches/plan", json={"task_ids": [task.id]})
    assert response.status_code == 422


def test_run_requires_exact_confirmation_and_rejects_a_stale_candidate_snapshot(
    client, db, active_resume, monkeypatch
):
    calls = _fake_model(monkeypatch)
    task = _completed_task(db, "北京-云平台")
    first_job = _job(client, "岗位一")
    _associate(db, task.id, first_job)
    plan = client.post("/api/task-match-batches/plan", json={"task_ids": [task.id]}).json()

    denied = client.post(
        "/api/task-match-batches/run",
        json={
            "task_ids": [task.id],
            "confirmed": False,
            "fingerprint": plan["fingerprint"],
            "max_new_calls": 1,
        },
    )
    assert denied.status_code == 422
    assert calls == []

    second_job = _job(client, "岗位二")
    _associate(db, task.id, second_job)
    stale = client.post(
        "/api/task-match-batches/run",
        json={
            "task_ids": [task.id],
            "confirmed": True,
            "fingerprint": plan["fingerprint"],
            "max_new_calls": 1,
        },
    )
    assert stale.status_code == 422
    assert calls == []


def test_active_resume_change_makes_the_confirmed_snapshot_stale(
    client, db, active_resume, monkeypatch
):
    from app.models import Resume

    calls = _fake_model(monkeypatch)
    task = _completed_task(db, "北京-云平台")
    _associate(db, task.id, _job(client, "岗位一"))
    plan = client.post("/api/task-match-batches/plan", json={"task_ids": [task.id]}).json()

    active_resume.is_active = False
    replacement = Resume(
        filename="replacement.txt",
        file_type="txt",
        content_hash="b" * 64,
        raw_text="replacement resume",
        parsed_profile_json={},
        is_active=True,
    )
    db.add(replacement)
    db.commit()

    response = client.post(
        "/api/task-match-batches/run",
        json={
            "task_ids": [task.id],
            "confirmed": True,
            "fingerprint": plan["fingerprint"],
            "max_new_calls": 1,
        },
    )
    assert response.status_code == 422
    assert calls == []


def test_one_batch_uses_at_most_three_new_calls_and_keeps_job_status(
    client, db, active_resume, monkeypatch
):
    calls = _fake_model(monkeypatch)
    task = _completed_task(db, "北京-云平台")
    job_ids = []
    for index in range(4):
        job_id = _job(client, f"岗位{index}")
        job_ids.append(job_id)
        _associate(db, task.id, job_id)
    plan = client.post("/api/task-match-batches/plan", json={"task_ids": [task.id]}).json()
    assert plan["pending_jobs"] == 4
    assert plan["max_new_calls"] == 3

    response = client.post(
        "/api/task-match-batches/run",
        json={
            "task_ids": [task.id],
            "confirmed": True,
            "fingerprint": plan["fingerprint"],
            "max_new_calls": 3,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert len(calls) == 3
    assert body["calls_used"] == 3
    assert body["analyzed"] == 3
    assert body["plan"]["cached_jobs"] == 3
    assert body["plan"]["pending_jobs"] == 1
    assert len(body["plan"]["items"]) == 4
    for job_id in job_ids:
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "new"


def test_failure_consumes_one_of_three_slots_and_does_not_abort_remaining_calls(
    client, db, active_resume, monkeypatch
):
    task = _completed_task(db, "北京-云平台")
    titles = ["失败岗位", "成功岗位一", "成功岗位二", "未运行岗位"]
    for title in titles:
        _associate(db, task.id, _job(client, title))
    calls = _fake_model(monkeypatch, fail_titles={"失败岗位"})
    plan = client.post("/api/task-match-batches/plan", json={"task_ids": [task.id]}).json()

    response = client.post(
        "/api/task-match-batches/run",
        json={
            "task_ids": [task.id],
            "confirmed": True,
            "fingerprint": plan["fingerprint"],
            "max_new_calls": 3,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert len(calls) == 3
    assert body["calls_used"] == 3
    assert body["analyzed"] == 2
    assert body["failed"] == 1
    failed = next(result for result in body["results"] if result["error"])
    assert "private upstream detail" not in failed["error"]
