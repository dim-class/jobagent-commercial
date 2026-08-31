"""The extension's backend surface (POC).

Two things this module is really about:

* **preview writes nothing.** It answers "new or duplicate?" using the same
  normalization and hash a save would use, and leaves the database untouched;
* **import has no private path.** A job that arrives from the extension is
  indistinguishable downstream from a pasted JD or a browser capture, because
  it goes through ``job_intake.save_posting`` like everything else.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.errors import ValidationError
from app.models import ApplicationEvent, EventType, Job, JobSearchTask, JobStatus
from app.schemas.extension import ExtensionJobCandidate
from app.services import extension_intake

DESCRIPTION = (
    "岗位职责：\n"
    "1. 负责多云环境的建设与运维，覆盖 AWS 与阿里云；\n"
    "2. 使用 Terraform 维护基础设施即代码；\n"
    "3. 维护 Kubernetes 集群并建设监控告警体系。\n"
    "任职要求：本科及以上学历，3 年以上云计算相关经验。"
)

DETAIL_URL = "https://www.zhipin.com/job_detail/aaa111bbb222~.html"


def candidate(**overrides) -> dict:
    payload = {
        "title": "云平台工程师",
        "company": "示例云科技（虚构公司）",
        "salary_text": "25-40K·14薪",
        "city": "上海",
        "experience_text": "3-5年",
        "education_text": "本科",
        "source_url": DETAIL_URL,
        "external_id": "aaa111bbb222~",
        "description": DESCRIPTION,
        "matched_selectors": {"title": ".job-banner .name h1"},
        "missing_fields": [],
        "warnings": [],
    }
    payload.update(overrides)
    return payload


def preview(client, *candidates, page_type: str = "detail", task_id: int | None = None) -> dict:
    response = client.post(
        "/api/extension/jobs/preview",
        json={
            "page_type": page_type,
            "page_url": DETAIL_URL,
            "candidates": list(candidates),
            "task_id": task_id,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def import_one(client, payload: dict, *, confirmed: bool = True, task_id: int | None = None):
    return client.post(
        "/api/extension/jobs/import",
        json={"confirmed": confirmed, "candidate": payload, "task_id": task_id},
    )


# --------------------------------------------------------------------------
# preview
# --------------------------------------------------------------------------


def test_preview_reports_a_new_job(client):
    body = preview(client, candidate())

    assert body["detected"] == 1
    assert body["new_count"] == 1
    assert body["duplicate_count"] == 0
    assert body["rows"][0]["status"] == "new"
    assert body["rows"][0]["status_label"] == "新岗位"
    assert body["rows"][0]["existing_job_id"] is None


@pytest.mark.parametrize("title", ["2027届云计算工程师", "云平台工程师（校招）", "运维实习生"])
def test_preview_excludes_early_career_tracks(client, title):
    body = preview(client, candidate(title=title))
    assert body["new_count"] == 0
    assert body["excluded_count"] == 1
    assert body["rows"][0]["status"] == "excluded"
    assert "应届生" in body["rows"][0]["warnings"][0]


def test_import_cannot_bypass_early_career_exclusion(client, db):
    response = import_one(client, candidate(title="云计算校招工程师"))
    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "early_career_track"
    assert db.scalars(select(Job)).all() == []


def test_non_restrictive_fresh_graduate_mention_is_not_excluded(client):
    body = preview(client, candidate(description=DESCRIPTION + "\n经验不限，应届生亦可，有经验者优先。"))
    assert body["rows"][0]["status"] == "new"


def _plan_task(db, policy: str) -> JobSearchTask:
    task = JobSearchTask(
        name=f"policy-{policy}", is_search_plan=True, early_career_policy=policy
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_task_snapshot_can_include_early_career_jobs(client, db):
    task = _plan_task(db, "include")
    body = preview(client, candidate(title="云平台工程师（校招）"), task_id=task.id)
    assert body["rows"][0]["status"] == "new"


def test_task_snapshot_can_keep_only_early_career_jobs(client, db):
    task = _plan_task(db, "only")
    early = preview(client, candidate(title="运维实习生"), task_id=task.id)
    experienced = preview(client, candidate(title="高级云平台工程师"), task_id=task.id)
    assert early["rows"][0]["status"] == "new"
    assert experienced["rows"][0]["status"] == "excluded"
    assert "只保留" in experienced["rows"][0]["warnings"][0]


def test_task_snapshot_controls_import_and_unknown_task_fails_closed(client, db):
    task = _plan_task(db, "include")
    accepted = import_one(client, candidate(title="云计算校招工程师"), task_id=task.id)
    assert accepted.status_code == 200
    rejected = client.post(
        "/api/extension/jobs/preview",
        json={"page_type": "detail", "page_url": DETAIL_URL,
              "candidates": [candidate()], "task_id": 999999},
    )
    assert rejected.status_code == 404


def test_preview_saves_nothing(client, db):
    """The whole point of a preview: it is read-only."""
    preview(client, candidate())
    assert db.scalars(select(Job)).all() == []
    assert db.scalars(select(ApplicationEvent)).all() == []


def test_preview_detects_a_duplicate_of_an_imported_job(client, db):
    assert import_one(client, candidate()).status_code == 200

    body = preview(client, candidate())
    row = body["rows"][0]

    assert body["duplicate_count"] == 1
    assert row["status"] == "duplicate"
    assert row["existing_job_id"] == db.scalars(select(Job)).one().id


def test_preview_detects_a_duplicate_by_external_id_alone(client):
    """Same job, edited description: the source id still says it is the same."""
    assert import_one(client, candidate()).status_code == 200

    body = preview(client, candidate(description=DESCRIPTION + "\n补充说明：需要出差。"))
    assert body["rows"][0]["status"] == "duplicate"


def test_duplicate_preview_marks_a_missing_salary_as_safely_enrichable(client, db):
    assert import_one(client, candidate(salary_text=None)).status_code == 200

    body = preview(client, candidate(salary_text="25-40K·14薪"))

    assert body["rows"][0]["status"] == "duplicate"
    assert body["rows"][0]["enrichable_fields"] == ["salary_text"]
    assert db.scalars(select(Job)).one().salary_text is None, "preview must remain read-only"


def test_a_card_without_a_description_is_incomplete_not_new(client):
    body = preview(client, candidate(description=None), page_type="search")
    row = body["rows"][0]

    assert row["status"] == "incomplete"
    assert "description" in row["blocking_fields"]
    assert body["incomplete_count"] == 1
    assert any("详情页" in warning for warning in row["warnings"])


def test_a_search_page_of_cards_is_explained(client):
    body = preview(
        client,
        candidate(description=None),
        candidate(description=None, title="Kubernetes 运维工程师"),
        page_type="search",
    )
    assert body["detected"] == 2
    assert body["incomplete_count"] == 2
    assert any("职位描述" in warning for warning in body["warnings"])


def test_a_teaser_length_description_is_not_a_job_description(client):
    body = preview(client, candidate(description="五险一金，弹性工作"))
    assert body["rows"][0]["status"] == "incomplete"


def test_preview_of_nothing_is_not_an_error(client):
    body = preview(client, page_type="unsupported")
    assert body["detected"] == 0
    assert body["rows"] == []
    assert any("BOSS" in warning for warning in body["warnings"])


def test_preview_message_says_nothing_was_saved(client):
    assert "还没有保存" in preview(client, candidate())["message"]


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------


def test_import_requires_confirmation(client, db):
    response = import_one(client, candidate(), confirmed=False)
    assert response.status_code == 422
    assert db.scalars(select(Job)).all() == []


def test_import_creates_a_job_through_the_normal_intake(client, db):
    response = import_one(client, candidate())
    assert response.status_code == 200, response.text
    body = response.json()

    job = db.scalars(select(Job)).one()
    assert body["job_id"] == job.id
    assert body["duplicate"] is False
    assert job.title == "云平台工程师"
    assert job.company == "示例云科技（虚构公司）"
    assert job.city == "上海"
    assert job.salary_text == "25-40K·14薪"
    assert job.status is JobStatus.new

    # The v0.1 intake contract: a hash and an event trail, same as every source.
    assert job.content_hash
    assert job.normalized_description
    event = db.scalars(select(ApplicationEvent)).one()
    assert event.job_id == job.id
    assert event.event_type is EventType.note


def test_an_imported_job_is_labelled_boss(client, db):
    import_one(client, candidate())
    assert db.scalars(select(Job)).one().source == "boss"


def test_a_job_with_no_url_falls_back_to_manual(client, db):
    import_one(client, candidate(source_url=None, external_id=None))
    assert db.scalars(select(Job)).one().source == "manual"


def test_importing_twice_returns_the_existing_job(client, db):
    first = import_one(client, candidate()).json()
    second = import_one(client, candidate()).json()

    assert second["job_id"] == first["job_id"]
    assert second["duplicate"] is True
    assert "已存在" in second["message"]
    assert len(db.scalars(select(Job)).all()) == 1


def test_confirmed_duplicate_import_fills_only_a_previously_missing_salary(client, db):
    first = import_one(client, candidate(salary_text=None)).json()
    second = import_one(client, candidate(salary_text="25-40K·14薪")).json()

    job = db.get(Job, first["job_id"])
    assert second["duplicate"] is True
    assert job.salary_text == "25-40K·14薪"
    assert len(db.scalars(select(Job)).all()) == 1


def test_duplicate_import_never_overwrites_an_existing_salary(client, db):
    first = import_one(client, candidate(salary_text="20-30K")).json()
    import_one(client, candidate(salary_text="40-50K"))

    assert db.get(Job, first["job_id"]).salary_text == "20-30K"


def test_importing_an_incomplete_candidate_is_refused(client, db):
    response = import_one(client, candidate(description=None))
    assert response.status_code == 422
    assert "详情页" in response.text or "不完整" in response.text
    assert db.scalars(select(Job)).all() == []


def test_a_candidate_with_no_title_is_refused(client):
    response = import_one(client, candidate(title=None))
    assert response.status_code == 422


def test_the_stored_url_carries_no_query_string(client, db):
    """BOSS puts a session token in the query; it must not reach the database."""
    import_one(
        client,
        candidate(source_url=DETAIL_URL + "?lid=track&securityId=secret-token"),
    )
    job = db.scalars(select(Job)).one()

    assert job.source_url == DETAIL_URL
    assert "securityId" not in (job.source_url or "")


def test_diagnostic_selectors_are_accepted_but_never_stored(client, db):
    """Developer-mode data is for the popup, not for the database."""
    import_one(client, candidate(matched_selectors={"title": ".job-banner .name h1"}))
    job = db.scalars(select(Job)).one()

    assert ".job-banner" not in job.normalized_description
    assert ".job-banner" not in (job.raw_description or "")


def test_an_extension_job_looks_like_any_other_job(client, db):
    """No parallel persistence path: it shows up in the normal job list."""
    import_one(client, candidate())
    listing = client.get("/api/jobs").json()

    assert listing["total"] == 1
    assert listing["items"][0]["title"] == "云平台工程师"


# --------------------------------------------------------------------------
# the service layer directly
# --------------------------------------------------------------------------


def test_inspect_is_read_only(db):
    verdict = extension_intake.inspect(db, ExtensionJobCandidate(**candidate()))
    assert verdict.status == "new"
    assert db.scalars(select(Job)).all() == []


def test_import_one_refuses_an_incomplete_candidate(db):
    with pytest.raises(ValidationError):
        extension_intake.import_one(
            db, ExtensionJobCandidate(**candidate(description="太短"))
        )


def test_source_detection_uses_the_url_only(db):
    boss = ExtensionJobCandidate(**candidate())
    unknown = ExtensionJobCandidate(**candidate(source_url="https://example.com/x"))

    assert extension_intake.source_name_for(boss) == "boss"
    assert extension_intake.source_name_for(unknown) == "manual"


def test_the_extension_never_calls_openai(client, monkeypatch):
    """Detection and import are deterministic - there is no model in the path."""
    import app.services.job_matcher as job_matcher

    def _explode(*args, **kwargs):  # pragma: no cover
        raise AssertionError("the extension path must never call the model")

    monkeypatch.setattr(job_matcher, "run_job_match", _explode)

    preview(client, candidate())
    assert import_one(client, candidate()).status_code == 200


# --------------------------------------------------------------------------
# who may call this
# --------------------------------------------------------------------------


class _Peer:
    """The only part of a request the loopback guard looks at."""

    def __init__(self, host: str) -> None:
        self.client = type("C", (), {"host": host})()


@pytest.mark.parametrize("host", ["203.0.113.7", "8.8.8.8", "2001:db8::1"])
def test_a_non_loopback_client_is_refused(host):
    """The server binds to 127.0.0.1; this makes that an assertion, not a hope."""
    from app.api.routes.extension import ForbiddenError, require_loopback

    with pytest.raises(ForbiddenError) as excinfo:
        require_loopback(_Peer(host))
    assert excinfo.value.status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "127.0.0.53"])
def test_a_loopback_address_passes_the_guard(host):
    from app.api.routes.extension import require_loopback

    require_loopback(_Peer(host))  # does not raise


def test_a_loopback_client_is_accepted(client):
    response = client.post(
        "/api/extension/jobs/preview", json={"page_type": "detail", "candidates": []}
    )
    assert response.status_code == 200


def test_the_extension_origin_is_allowed_by_pattern(settings):
    """An unpacked extension's id is machine-specific, so it is matched by shape."""
    import re

    pattern = re.compile(settings.cors_origin_regex)
    assert pattern.match("chrome-extension://" + "a" * 32)
    assert not pattern.match("https://evil.example.com")
    assert not pattern.match("chrome-extension://short")
