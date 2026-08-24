"""编排会话记录 (M3) - service and API tests.

Covers the acceptance criteria from docs/orchestration/ROADMAP.md: an event
persists exactly the submitted type/note with a server timestamp, a
task-level event stores job_id=None, an event for a job that is not the
task's candidate is rejected and writes nothing, a missing task 404s,
listing is oldest-first and scoped to one task, and nothing here ever calls
the model or changes Job.status.
"""

from __future__ import annotations

from app.core.errors import NotFoundError, ValidationError
from app.models import Job, JobStatus, OrchestrationEvent, OrchestrationEventType
from app.services import orchestration_events, task_console

from tests.test_task_console import make_job


def make_task_with_candidate(db, **task_kwargs):
    task = task_console.create_task(db, name=task_kwargs.pop("name", "任务"), **task_kwargs)
    job = make_job(db)
    task_console.add_candidate(db, task.id, job.id)
    return task, job


# --------------------------------------------------------------------------
# service: append
# --------------------------------------------------------------------------


def test_appending_an_event_persists_the_submitted_type_and_note(db):
    task, job = make_task_with_candidate(db)

    event = orchestration_events.add_event(
        db, task.id, event_type=OrchestrationEventType.reviewed, job_id=job.id, note="看起来不错"
    )

    assert event.id is not None
    assert event.task_id == task.id
    assert event.job_id == job.id
    assert event.event_type is OrchestrationEventType.reviewed
    assert event.note == "看起来不错"
    assert event.created_at is not None


def test_a_task_level_event_stores_job_id_as_none(db):
    task = task_console.create_task(db, name="任务")

    event = orchestration_events.add_event(db, task.id, event_type=OrchestrationEventType.opened)

    assert event.job_id is None
    assert event.event_type is OrchestrationEventType.opened


def test_event_for_a_job_that_is_not_the_tasks_candidate_is_rejected(db):
    task = task_console.create_task(db, name="任务")
    other_job = make_job(db)

    before = db.query(OrchestrationEvent).count()
    try:
        orchestration_events.add_event(
            db, task.id, event_type=OrchestrationEventType.dismissed, job_id=other_job.id
        )
        raised = False
    except ValidationError:
        raised = True
    assert raised, "an event about a non-candidate job must be rejected"
    assert db.query(OrchestrationEvent).count() == before


def test_event_for_a_missing_task_404s(db):
    job = make_job(db)
    try:
        orchestration_events.add_event(
            db, 999999, event_type=OrchestrationEventType.opened, job_id=job.id
        )
        raised = False
    except NotFoundError:
        raised = True
    assert raised


def test_repeated_actions_on_the_same_candidate_accumulate_not_overwrite(db):
    task, job = make_task_with_candidate(db)

    orchestration_events.add_event(db, task.id, event_type=OrchestrationEventType.opened, job_id=job.id)
    orchestration_events.add_event(db, task.id, event_type=OrchestrationEventType.reviewed, job_id=job.id)

    events = orchestration_events.list_events(db, task.id)
    assert [e.event_type for e in events] == [
        OrchestrationEventType.opened,
        OrchestrationEventType.reviewed,
    ]


# --------------------------------------------------------------------------
# service: list ordering + scoping
# --------------------------------------------------------------------------


def test_list_events_is_oldest_first_and_scoped_to_one_task(db):
    task_a, job_a = make_task_with_candidate(db, name="任务A")
    task_b, job_b = make_task_with_candidate(db, name="任务B")

    orchestration_events.add_event(db, task_a.id, event_type=OrchestrationEventType.opened, job_id=job_a.id)
    orchestration_events.add_event(db, task_a.id, event_type=OrchestrationEventType.reviewed, job_id=job_a.id)
    orchestration_events.add_event(db, task_b.id, event_type=OrchestrationEventType.dismissed, job_id=job_b.id)

    events_a = orchestration_events.list_events(db, task_a.id)
    assert [e.event_type for e in events_a] == [
        OrchestrationEventType.opened,
        OrchestrationEventType.reviewed,
    ]
    assert all(e.task_id == task_a.id for e in events_a)
    # oldest first: ids assigned in insertion order
    assert events_a[0].id < events_a[1].id


def test_listing_events_for_a_missing_task_404s(db):
    try:
        orchestration_events.list_events(db, 999999)
        raised = False
    except NotFoundError:
        raised = True
    assert raised


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def test_api_appends_and_lists_events(client, db):
    task, job = make_task_with_candidate(db)

    created = client.post(
        f"/api/tasks/{task.id}/events",
        json={"event_type": "reviewed", "job_id": job.id, "note": "复核通过"},
    ).json()
    assert created["event_type"] == "reviewed"
    assert created["job_id"] == job.id
    assert created["note"] == "复核通过"

    body = client.get(f"/api/tasks/{task.id}/events").json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == created["id"]


def test_api_rejects_event_for_non_candidate_job(client, db):
    task = task_console.create_task(db, name="任务")
    other_job = make_job(db)

    resp = client.post(
        f"/api/tasks/{task.id}/events", json={"event_type": "opened", "job_id": other_job.id}
    )
    assert resp.status_code == 422


def test_api_missing_task_404s_on_both_routes(client):
    assert client.post("/api/tasks/999999/events", json={"event_type": "opened"}).status_code == 404
    assert client.get("/api/tasks/999999/events").status_code == 404


# --------------------------------------------------------------------------
# what it must never do
# --------------------------------------------------------------------------


def test_orchestration_events_make_no_openai_call(client, db, monkeypatch):
    import app.services.job_matcher as job_matcher

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("orchestration events must never call the model")

    monkeypatch.setattr(job_matcher, "run_job_match", _explode)

    task, job = make_task_with_candidate(db)
    client.post(f"/api/tasks/{task.id}/events", json={"event_type": "opened", "job_id": job.id})
    client.get(f"/api/tasks/{task.id}/events")


def test_orchestration_events_never_change_job_status(client, db):
    task, job = make_task_with_candidate(db)
    assert job.status == JobStatus.new

    client.post(f"/api/tasks/{task.id}/events", json={"event_type": "dismissed", "job_id": job.id})

    db.expire_all()
    assert db.get(Job, job.id).status == JobStatus.new


def test_api_created_at_carries_explicit_utc_offset(client, db):
    """Regression: a naive SQLite datetime must not be serialized without a
    UTC offset - the frontend would otherwise misread it as local time."""
    task, job = make_task_with_candidate(db)

    created = client.post(
        f"/api/tasks/{task.id}/events", json={"event_type": "opened", "job_id": job.id}
    ).json()

    raw = created["created_at"]
    assert raw.endswith("+00:00") or raw.endswith("Z"), raw
