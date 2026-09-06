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


# ---------------------------------------------------------------------------
# A re-listing under a NEW job id - the URL check cannot see it
# ---------------------------------------------------------------------------


@pytest.fixture
def carded_job(db):
    """A stored posting with every field a search card also shows."""
    from app.models import Job
    from app.models.enums import JobSourceName, JobStatus

    external_id = "cardedjob0000000000000000BB~"
    job = Job(
        source=JobSourceName.boss,
        external_id=external_id,
        company="重复公司",
        title="中间件运维工程师",
        salary_text="20-40K",
        experience_text="5-10年",
        city="北京",
        raw_description="维护中间件集群。" * 5,
        normalized_description="维护中间件集群。" * 5,
        content_hash="b" * 64,
        status=JobStatus.new,
        source_url=f"https://www.zhipin.com/job_detail/{external_id}.html",
    )
    db.add(job)
    db.commit()
    return job


RELISTED = "https://www.zhipin.com/job_detail/relisted00000000000000CCCC.html"


def _card(**overrides):
    card = {
        "url": RELISTED,
        "company": "重复公司",
        "title": "中间件运维工程师",
        "salary_text": "20-40K",
        "experience_text": "5-10年",
        "city": "北京",
    }
    card.update(overrides)
    return card


def _known(client, card):
    return client.post(
        "/api/extension/jobs/known", json={"urls": [card["url"]], "cards": [card]}
    ).json()["known"]


def test_a_relisting_under_a_new_id_is_recognised_from_the_card(client, carded_job):
    """The whole point: a new `job_detail` id, the same posting.

    Opening it would spend a candidate slot and import nothing, because the
    content hash reports a duplicate once the description is read.
    """
    assert _known(client, _card()) == [RELISTED]


@pytest.mark.parametrize(
    "field, value",
    [
        ("company", "另一家公司"),
        ("title", "中间件开发工程师"),
        ("salary_text", "30-50K"),
        ("experience_text", "3-5年"),
        ("city", "上海"),
    ],
)
def test_one_differing_field_is_never_called_known(client, carded_job, field, value):
    """The claim is "every field a card shows agrees", not "close enough".

    A posting the library has never seen must be opened - the description,
    which decides the real duplicate, is not on the card.
    """
    assert _known(client, _card(**{field: value})) == []


def test_a_card_without_company_or_title_makes_no_claim(client, carded_job):
    assert _known(client, _card(company=None)) == []
    assert _known(client, _card(title=None)) == []


def test_whitespace_does_not_decide_identity(client, carded_job):
    assert _known(client, _card(title=" 中间件运维 工程师 ")) == [RELISTED]


def test_a_url_match_is_not_reported_twice(client, carded_job):
    """The same posting, matched both ways, is still one entry."""
    card = _card(url=carded_job.source_url)
    assert _known(client, card) == [carded_job.source_url]


def test_cards_are_optional_so_an_older_extension_still_works(client, carded_job):
    body = client.post(
        "/api/extension/jobs/known", json={"urls": [RELISTED]}
    ).json()
    assert body["known"] == []


def test_card_matching_writes_nothing(client, carded_job, db):
    from app.models import Job

    before = db.query(Job).count()
    _known(client, _card())
    db.expire_all()
    assert db.query(Job).count() == before


def test_a_card_whose_salary_boss_obfuscated_still_matches(client, carded_job):
    """BOSS hides most card salaries behind a private-use font.

    The card then reads empty while the stored job carries a figure the OCR or
    the salary backfill supplied later - 409 of 632 jobs in the library this
    was written against were created that way. Requiring the salary to agree
    sent the run to open those postings again on every single run.
    """
    assert _known(client, _card(salary_text=None)) == [RELISTED]
    assert _known(client, _card(salary_text="")) == [RELISTED]


def test_the_mirror_case_matches_too(client, db):
    """A job stored before the backfill ran has no salary; the card now shows one."""
    from app.models import Job
    from app.models.enums import JobSourceName, JobStatus

    external_id = "nosalaryjob00000000000000DD~"
    db.add(
        Job(
            source=JobSourceName.boss,
            external_id=external_id,
            company="待回填公司",
            title="平台工程师",
            salary_text=None,
            experience_text="5-10年",
            city="北京",
            raw_description="平台建设。" * 5,
            normalized_description="平台建设。" * 5,
            content_hash="c" * 64,
            status=JobStatus.new,
            source_url=f"https://www.zhipin.com/job_detail/{external_id}.html",
        )
    )
    db.commit()
    card = _card(company="待回填公司", title="平台工程师", salary_text="30-50K")
    assert _known(client, card) == [RELISTED]


def test_a_missing_salary_still_does_not_excuse_the_other_fields(client, carded_job):
    """Only the salary becomes a wildcard - never company, title, experience or city."""
    for field, value in [
        ("company", "另一家公司"),
        ("title", "中间件开发工程师"),
        ("experience_text", "3-5年"),
        ("city", "上海"),
    ]:
        assert _known(client, _card(salary_text=None, **{field: value})) == []
