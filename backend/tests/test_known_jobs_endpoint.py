"""Answering "do I already have this?" from the URL alone.

A search run may open only a fixed number of job details. That budget was being
spent re-opening postings already stored - a repeat search of 「云计算工程师」
attempted its whole 20-candidate allowance and imported zero - so the runner
needs to know before it spends a slot, and a search card carries nothing but a
URL and a title.
"""

from __future__ import annotations

import pytest

from app.services.urls import boss_external_id

from tests.test_jobs_api import _create


@pytest.fixture
def boss_job(db):
    """One stored BOSS posting with a canonical detail URL."""
    from app.models import Job
    from app.models.enums import JobSourceName, JobStatus

    external_id = "storedjob0000000000000000AA~"
    job = Job(
        source=JobSourceName.boss,
        external_id=external_id,
        company="已入库公司",
        title="云计算工程师",
        raw_description="负责云平台运维。" * 5,
        normalized_description="负责云平台运维。" * 5,
        content_hash="a" * 64,
        status=JobStatus.new,
        source_url=f"https://www.zhipin.com/job_detail/{external_id}.html",
    )
    db.add(job)
    db.commit()
    return job


def test_known_reports_stored_urls_and_ignores_unseen_ones(client, boss_job):
    unseen = "https://www.zhipin.com/job_detail/neverseen00000000000000AAAA.html"
    body = client.post(
        "/api/extension/jobs/known", json={"urls": [boss_job.source_url, unseen]}
    ).json()
    assert body["known"] == [boss_job.source_url]


def test_a_session_token_in_the_query_still_matches(client, boss_job):
    """BOSS puts `lid`/`securityId` in the query; identity is the path."""
    dirty = boss_job.source_url + "?lid=abc&securityId=def"
    body = client.post("/api/extension/jobs/known", json={"urls": [dirty]}).json()
    assert body["known"] == [boss_job.source_url]


def test_it_writes_nothing(client, boss_job, db):
    from app.models import Job

    before = db.query(Job).count()
    client.post("/api/extension/jobs/known", json={"urls": [boss_job.source_url]})
    db.expire_all()
    assert db.query(Job).count() == before


def test_non_posting_urls_are_never_treated_as_an_identity():
    """A search page, another host or plain http is not a job identity."""
    assert boss_external_id("https://www.zhipin.com/web/geek/jobs?city=101010100") is None
    assert boss_external_id("https://evil.example/job_detail/abc.html") is None
    assert boss_external_id("http://www.zhipin.com/job_detail/abc.html") is None
    assert boss_external_id(None) is None


def test_the_id_pattern_accepts_every_character_boss_actually_uses():
    """Real ids are 28 chars over [A-Za-z0-9_~-].

    Omitting `~` made a stored job report as new - the one answer this endpoint
    must never give, because it sends the runner to re-open work already done.
    """
    for external_id in ("9edb85bc7c6a1f0f0HB409W-FVo~", "c0f82be86b0939631nV_29m4E1FQ"):
        url = f"https://www.zhipin.com/job_detail/{external_id}.html"
        assert boss_external_id(url) == external_id
