"""Demo seed data and the JobSource abstraction."""

from __future__ import annotations

from app.job_sources import SOURCES, ManualJobSource, get_source, manual_source
from app.job_sources.base import JobSearchQuery, JobSource
from app.models import Job
from app.services.seed import DEMO_JOBS, DEMO_MARKER, seed_demo_jobs


# --------------------------------------------------------------------------
# seed
# --------------------------------------------------------------------------


def test_seed_creates_the_demo_set(db):
    stats = seed_demo_jobs(db)
    assert stats["created"] == len(DEMO_JOBS) == 6
    assert db.query(Job).count() == 6


def test_seed_is_idempotent(db):
    seed_demo_jobs(db)
    stats = seed_demo_jobs(db)
    assert stats["created"] == 0
    assert stats["skipped"] == 6
    assert db.query(Job).count() == 6


def test_seed_reset_replaces_demo_jobs(db):
    seed_demo_jobs(db)
    stats = seed_demo_jobs(db, reset=True)
    assert stats["created"] == 6
    assert db.query(Job).count() == 6


def test_demo_jobs_are_clearly_marked_as_not_real(db):
    seed_demo_jobs(db)
    for job in db.query(Job).all():
        assert job.source == "demo"
        assert DEMO_MARKER in job.raw_description
        assert "非真实招聘信息" in job.raw_description


def test_demo_set_covers_the_required_scenarios(db):
    seed_demo_jobs(db)
    jobs = db.query(Job).all()

    cities = {job.city for job in jobs}
    assert {"北京", "上海", "杭州", "广州"} <= cities

    titles = " ".join(job.title for job in jobs)
    assert "Cloud Engineer" in titles
    assert "DevOps" in titles
    assert "SRE" in titles
    assert "Infrastructure Engineer" in titles
    assert "Helpdesk" in titles

    # one posting must demand significantly more experience than the target
    assert any("8" in (job.experience_text or "") for job in jobs)


def test_seeded_jobs_show_up_through_the_api(client, db):
    seed_demo_jobs(db)
    body = client.get("/api/jobs").json()
    assert body["total"] == 6
    assert body["facets"]["cities"]["北京"] == 2


# --------------------------------------------------------------------------
# job sources
# --------------------------------------------------------------------------


def test_manual_source_is_registered_and_implemented():
    assert isinstance(manual_source, JobSource)
    assert get_source("manual") is manual_source
    assert manual_source.implemented is True


def test_registry_holds_exactly_the_implemented_sources():
    """v0.1 shipped manual; v0.2 adds boss. 猎聘/智联/51job stay unregistered
    until a real, terms-respecting adapter exists."""
    assert set(SOURCES) == {"manual", "boss"}


def test_manual_source_has_no_collection_behaviour():
    """v0.1 does not scrape anything - the human is the collector."""
    assert manual_source.search_jobs(JobSearchQuery(keywords=["cloud"])) == []
    assert manual_source.get_job("anything") is None


def test_manual_source_normalizes_like_every_other_source():
    posting = manual_source.from_form(
        title=" 云计算工程师 ",
        company=" 示例科技 ",
        raw_description="职责\n\n\n\n要求：熟悉 AWS",
        city="北京市",
    )
    normalized = manual_source.normalize_job(posting)
    assert normalized.title == "云计算工程师"
    assert normalized.city == "北京"
    assert normalized.normalized_description == "职责\n\n要求：熟悉 AWS"
    assert len(normalized.content_hash) == 64


def test_job_source_is_an_abstract_seam():
    """Future adapters must implement search_jobs/get_job to instantiate."""

    class Incomplete(JobSource):
        name = "incomplete"

    try:
        Incomplete()  # type: ignore[abstract]
    except TypeError:
        return
    raise AssertionError("JobSource must not be instantiable without its methods")


def test_manual_source_subclasses_the_base():
    assert issubclass(ManualJobSource, JobSource)
