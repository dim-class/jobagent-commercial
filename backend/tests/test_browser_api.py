"""Browser capture API: lifecycle, page selection, capture, dedup.

No test here launches the real recruitment browser or contacts zhipin.com.

  * lifecycle/error paths use ``FakeBrowserSession``;
  * the capture path uses a real Playwright page loaded with local fixture
    HTML, driven through the ASGI app on the same event loop.
"""

from __future__ import annotations

import httpx
import pytest

from app.api.routes import browser as browser_routes
from app.main import app
from app.services.browser_session import (
    BrowserNotRunningError,
    BrowserStatus,
    NoSupportedPageError,
    canonical_url,
    is_blocked_url,
    matches_supported_host,
    redact_url,
    site_for_url,
)
from tests.conftest import RESUME_TEXT

JOB_URL = "https://www.zhipin.com/job_detail/a1b2c3d4e5f6~.html?lid=abc&securityId=xyz"
SEARCH_URL = "https://www.zhipin.com/web/geek/job?query=cloud"

SUPPORTED = ["zhipin.com"]


# --------------------------------------------------------------------------
# pure URL helpers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (JOB_URL, True),
        ("https://zhipin.com/job_detail/x.html", True),
        ("https://sub.zhipin.com/anything", True),
        ("https://www.liepin.com/job/1.html", False),
        ("https://evil-zhipin.com.attacker.net/job_detail/x.html", False),
        ("chrome://settings", False),
        ("chrome-extension://abc/page.html", False),
        ("about:blank", False),
        ("devtools://devtools/bundled/inspector.html", False),
        ("", False),
    ],
)
def test_matches_supported_host(url, expected):
    assert matches_supported_host(url, SUPPORTED) is expected


@pytest.mark.parametrize(
    "url",
    ["chrome://settings", "about:blank", "chrome-extension://a/b.html", "view-source:https://x", ""],
)
def test_browser_internal_pages_are_never_capturable(url):
    assert is_blocked_url(url) is True


def test_site_for_url():
    assert site_for_url(JOB_URL, SUPPORTED) == "boss"
    assert site_for_url("https://www.liepin.com/job/1.html", SUPPORTED) is None


def test_redact_url_drops_session_tokens():
    """BOSS puts securityId/lid in the query string - never log or store them."""
    redacted = redact_url(JOB_URL)
    assert redacted == "https://www.zhipin.com/job_detail/a1b2c3d4e5f6~.html"
    assert "securityId" not in redacted
    assert "lid" not in redacted


def test_canonical_url_is_stable_across_visits():
    a = canonical_url("https://www.zhipin.com/job_detail/x.html?lid=1&securityId=aaa")
    b = canonical_url("https://www.zhipin.com/job_detail/x.html?lid=2&securityId=bbb")
    assert a == b == "https://www.zhipin.com/job_detail/x.html"


# --------------------------------------------------------------------------
# fake session for lifecycle tests
# --------------------------------------------------------------------------


class FakeBrowserSession:
    """Stands in for the real Playwright session in lifecycle tests."""

    def __init__(self) -> None:
        self.running = False
        self.page = None
        self.start_calls = 0
        self.stop_calls = 0
        self.closed_externally = False

    @property
    def supported_hosts(self) -> list[str]:
        return list(SUPPORTED)

    async def start(self, landing_url: str = ""):
        self.start_calls += 1
        already = self.running
        self.running = True
        self.closed_externally = False
        return await self.status(), already

    async def stop(self) -> bool:
        self.stop_calls += 1
        was = self.running
        self.running = False
        self.page = None
        return was

    async def status(self) -> BrowserStatus:
        if not self.running:
            return BrowserStatus(
                running=False,
                supported_hosts=self.supported_hosts,
                profile_dir="/tmp/profile",
                message="浏览器已被手动关闭" if self.closed_externally else "浏览器未启动",
            )
        url = self.page.url if self.page is not None else None
        return BrowserStatus(
            running=True,
            site=site_for_url(url, SUPPORTED),
            current_url=url,
            current_title="示例职位" if url else None,
            page_count=1 if url else 0,
            supported_hosts=self.supported_hosts,
            profile_dir="/tmp/profile",
            message="浏览器运行中",
        )

    async def active_page(self):
        if not self.running:
            message = (
                "浏览器已被手动关闭，请重新点击「打开浏览器」。"
                if self.closed_externally
                else "浏览器尚未启动，请先点击「打开浏览器」。"
            )
            raise BrowserNotRunningError(message, detail={"running": False})
        if self.page is None:
            raise NoSupportedPageError(
                "当前页面不是受支持的招聘网站。请在浏览器中打开 BOSS 直聘（zhipin.com）后重试。",
                detail={"supported_hosts": SUPPORTED},
            )
        return self.page


@pytest.fixture
def fake_session(monkeypatch) -> FakeBrowserSession:
    session = FakeBrowserSession()
    monkeypatch.setattr(browser_routes, "get_browser_session", lambda: session)
    return session


# --------------------------------------------------------------------------
# lifecycle (sync TestClient + fake session)
# --------------------------------------------------------------------------


def test_status_when_not_running(client, fake_session):
    body = client.get("/api/browser/status").json()
    assert body["running"] is False
    assert body["current_url"] is None
    assert body["supported_hosts"] == SUPPORTED
    assert "未启动" in body["message"]


def test_start_browser(client, fake_session):
    body = client.post("/api/browser/start").json()
    assert body["running"] is True
    assert body["already_running"] is False
    assert fake_session.start_calls == 1


def test_starting_twice_reports_already_running(client, fake_session):
    """A second click must not launch a second browser or raise."""
    client.post("/api/browser/start")
    body = client.post("/api/browser/start").json()

    assert body["already_running"] is True
    assert body["running"] is True
    assert "已在运行" in body["message"]
    assert fake_session.start_calls == 2  # called, but reported as a no-op


def test_stop_browser(client, fake_session):
    client.post("/api/browser/start")
    body = client.post("/api/browser/stop").json()
    assert body["detail"]["was_running"] is True
    assert client.get("/api/browser/status").json()["running"] is False


def test_stop_when_not_running_is_not_an_error(client, fake_session):
    body = client.post("/api/browser/stop")
    assert body.status_code == 200
    assert body.json()["detail"]["was_running"] is False


def test_capture_without_browser_returns_409(client, fake_session):
    response = client.post("/api/browser/capture-current-job")
    assert response.status_code == 409

    body = response.json()
    assert body["code"] == "browser_not_running"
    assert "打开浏览器" in body["message"]


def test_capture_reports_browser_closed_by_hand(client, fake_session):
    client.post("/api/browser/start")
    # The human closed the window; the session notices on next use.
    fake_session.running = False
    fake_session.closed_externally = True

    response = client.post("/api/browser/capture-current-job")
    assert response.status_code == 409
    assert "已被手动关闭" in response.json()["message"]

    status = client.get("/api/browser/status").json()
    assert status["running"] is False
    assert "已被手动关闭" in status["message"]


def test_capture_without_a_supported_page_returns_422(client, fake_session):
    client.post("/api/browser/start")  # running, but no supported page open
    response = client.post("/api/browser/capture-current-job")

    assert response.status_code == 422
    assert response.json()["code"] == "no_supported_page"
    assert "BOSS" in response.json()["message"]


def test_current_page_without_supported_page(client, fake_session):
    client.post("/api/browser/start")
    body = client.get("/api/browser/current-page").json()
    assert body["is_job_page"] is False
    assert body["url"] is None


# --------------------------------------------------------------------------
# capture against real fixture HTML, over the ASGI app
# --------------------------------------------------------------------------


@pytest.fixture
async def api(monkeypatch, fake_session):
    """Async client so Playwright and the app share one event loop."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _open_job_page(fixture_page, fake_session, name="boss_job_standard.html", url=JOB_URL):
    page = await fixture_page(name, url=url)
    fake_session.running = True
    fake_session.page = page
    return page


@pytest.mark.asyncio
async def test_current_page_recognises_a_job_page(api, fake_session, fixture_page):
    await _open_job_page(fixture_page, fake_session)
    body = (await api.get("/api/browser/current-page")).json()

    assert body["site"] == "boss"
    assert body["is_job_page"] is True
    assert body["external_id"] == "a1b2c3d4e5f6~"
    assert body["title"]


@pytest.mark.asyncio
async def test_current_page_rejects_the_search_list(api, fake_session, fixture_page):
    await _open_job_page(fixture_page, fake_session, "boss_not_job.html", SEARCH_URL)
    body = (await api.get("/api/browser/current-page")).json()

    assert body["site"] == "boss"
    assert body["is_job_page"] is False
    assert "未识别到职位详情" in body["message"]


@pytest.mark.asyncio
async def test_capture_saves_the_job(api, fake_session, fixture_page):
    await _open_job_page(fixture_page, fake_session)
    response = await api.post("/api/browser/capture-current-job")
    assert response.status_code == 200

    body = response.json()
    assert body["duplicate"] is False
    assert body["site"] == "boss"
    assert body["analysis"] is None, "capture must not spend API credit by default"

    fields = body["fields"]
    assert fields["title"] == "云计算工程师"
    assert fields["company"] == "示例科技（虚构公司）"
    assert fields["city"] == "北京"
    assert fields["salary_text"] == "20-30K·13薪"
    assert fields["external_id"] == "a1b2c3d4e5f6~"
    assert fields["description_chars"] > 100

    # session token stripped from what we store and return
    assert body["selected_url"] == "https://www.zhipin.com/job_detail/a1b2c3d4e5f6~.html"
    assert "securityId" not in body["selected_url"]


@pytest.mark.asyncio
async def test_captured_job_enters_the_existing_pipeline(api, fake_session, fixture_page):
    """It must be an ordinary Job row: same list, same filters, same schema."""
    await _open_job_page(fixture_page, fake_session)
    captured = (await api.post("/api/browser/capture-current-job")).json()

    listing = (await api.get("/api/jobs")).json()
    assert listing["total"] == 1
    row = listing["items"][0]
    assert row["id"] == captured["job_id"]
    assert row["source"] == "boss"
    assert row["city"] == "北京"

    detail = (await api.get(f"/api/jobs/{captured['job_id']}")).json()
    assert detail["content_hash"]
    assert detail["normalized_description"]
    assert detail["raw_description"]
    assert "\n\n\n" not in detail["normalized_description"]
    assert any("采集" in (e["notes"] or "") for e in detail["events"])

    # city filtering works on captured jobs too
    filtered = (await api.get("/api/jobs", params={"city": "北京"})).json()
    assert filtered["total"] == 1


@pytest.mark.asyncio
async def test_capturing_the_same_job_twice_is_a_duplicate_not_a_failure(
    api, fake_session, fixture_page
):
    await _open_job_page(fixture_page, fake_session)
    first = (await api.post("/api/browser/capture-current-job")).json()

    second_response = await api.post("/api/browser/capture-current-job")
    assert second_response.status_code == 200, "duplicate is a normal outcome"

    second = second_response.json()
    assert second["duplicate"] is True
    assert second["job_id"] == first["job_id"]
    assert "已存在" in second["message"]

    listing = (await api.get("/api/jobs")).json()
    assert listing["total"] == 1, "no twin row was created"


@pytest.mark.asyncio
async def test_capture_tolerates_missing_optional_fields(api, fake_session, fixture_page):
    await _open_job_page(fixture_page, fake_session, "boss_job_missing_optional.html")
    body = (await api.post("/api/browser/capture-current-job")).json()

    assert body["duplicate"] is False
    assert body["fields"]["title"] == "运维开发工程师"
    assert body["fields"]["company"] is None
    assert body["fields"]["city"] is None
    assert "company" in body["fields"]["fields_missing"]
    assert body["job"]["id"]


@pytest.mark.asyncio
async def test_capture_rejects_a_non_job_page(api, fake_session, fixture_page):
    await _open_job_page(fixture_page, fake_session, "boss_not_job.html", SEARCH_URL)
    response = await api.post("/api/browser/capture-current-job")

    assert response.status_code == 422
    assert "未识别到职位详情" in response.json()["message"]
    assert (await api.get("/api/jobs")).json()["total"] == 0, "nothing was saved"


@pytest.mark.asyncio
async def test_capture_surfaces_verification_without_solving_it(api, fake_session, fixture_page):
    await _open_job_page(fixture_page, fake_session, "boss_verification.html")
    response = await api.post("/api/browser/capture-current-job")

    assert response.status_code == 422
    assert "验证" in response.json()["message"]
    assert "手动完成" in response.json()["message"]
    assert (await api.get("/api/jobs")).json()["total"] == 0


@pytest.mark.asyncio
async def test_captured_job_can_be_analyzed_by_the_existing_pipeline(
    api, fake_session, fixture_page, db, monkeypatch
):
    """The AI path must not care that the JD came from a browser."""
    from app.models import Resume
    from app.schemas.analysis import JobMatchResult
    from app.services.resume_parser import parse_resume

    parsed = parse_resume(RESUME_TEXT.encode("utf-8"), "resume.txt")
    db.add(
        Resume(
            filename="resume.txt",
            file_type=parsed.file_type,
            content_hash=parsed.content_hash,
            raw_text=parsed.raw_text,
            parsed_profile_json=parsed.profile,
            is_active=True,
        )
    )
    db.commit()

    calls: list[dict] = []

    async def _fake_run_job_match(**kwargs):
        calls.append(kwargs)
        return JobMatchResult.model_validate(
            {
                "overall_score": 84,
                "verdict": "apply",
                "role_fit_score": 86,
                "skill_fit_score": 82,
                "experience_fit_score": 80,
                "location_fit_score": 100,
                "salary_fit_score": 78,
                "matched_skills": ["AWS", "Terraform"],
                "missing_skills": [],
                "strengths": ["云运维经验匹配"],
                "gaps": [],
                "risk_flags": [],
                "experience_gap": "无明显差距",
                "role_summary": "AWS 云平台运维",
                "reasoning_summary": "技能高度重合。",
                "greeting_message": "您好，我有云运维经验，期待沟通。",
            }
        )

    monkeypatch.setattr("app.services.job_matcher.run_job_match", _fake_run_job_match)

    await _open_job_page(fixture_page, fake_session)
    captured = (await api.post("/api/browser/capture-current-job")).json()
    job_id = captured["job_id"]

    analysis = (await api.post(f"/api/jobs/{job_id}/analyze", json={})).json()
    assert analysis["result"]["overall_score"] == 84
    assert analysis["meta"]["cached"] is False

    # the agent received an ordinary JD, with no browser-specific keys
    assert len(calls) == 1
    assert set(calls[0]) == {
        "model_name",
        "resume_profile",
        "resume_excerpt",
        "strategy",
        "job",
        "pre_analysis",
        "settings",
    }
    assert "Terraform" in calls[0]["job"]["normalized_description"]

    cached = (await api.post(f"/api/jobs/{job_id}/analyze", json={})).json()
    assert cached["meta"]["cached"] is True
    assert len(calls) == 1, "cache must work for captured jobs too"


@pytest.mark.asyncio
async def test_capture_with_analyze_true_runs_one_analysis(
    api, fake_session, fixture_page, db, monkeypatch
):
    from app.models import Resume
    from app.schemas.analysis import JobMatchResult
    from app.services.resume_parser import parse_resume

    parsed = parse_resume(RESUME_TEXT.encode("utf-8"), "resume.txt")
    db.add(
        Resume(
            filename="resume.txt",
            file_type=parsed.file_type,
            content_hash=parsed.content_hash,
            raw_text=parsed.raw_text,
            parsed_profile_json=parsed.profile,
            is_active=True,
        )
    )
    db.commit()

    async def _fake_run_job_match(**kwargs):
        return JobMatchResult.model_validate(
            {
                "overall_score": 77,
                "verdict": "apply",
                "role_fit_score": 80,
                "skill_fit_score": 75,
                "experience_fit_score": 70,
                "location_fit_score": 100,
                "salary_fit_score": None,
                "matched_skills": [],
                "missing_skills": [],
                "strengths": [],
                "gaps": [],
                "risk_flags": [],
                "experience_gap": "",
                "role_summary": "",
                "reasoning_summary": "",
                "greeting_message": "您好。",
            }
        )

    monkeypatch.setattr("app.services.job_matcher.run_job_match", _fake_run_job_match)

    await _open_job_page(fixture_page, fake_session)
    body = (await api.post("/api/browser/capture-current-job", params={"analyze": "true"})).json()

    assert body["analysis"] is not None
    assert body["analysis"]["result"]["overall_score"] == 77


# --------------------------------------------------------------------------
# multi-tab selection
# --------------------------------------------------------------------------


class _StubPage:
    """Minimal stand-in for a Playwright Page in selection tests."""

    def __init__(self, url: str) -> None:
        self.url = url

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<StubPage {self.url}>"


def test_pick_active_page_single_candidate():
    from app.services.browser_session import pick_active_page

    only = _StubPage(JOB_URL)
    assert pick_active_page([only], {}) is only


def test_pick_active_page_falls_back_to_newest():
    """With no recorded activity, context.pages order means newest is last."""
    from app.services.browser_session import pick_active_page

    older, newer = _StubPage("https://www.zhipin.com/job_detail/a.html"), _StubPage(
        "https://www.zhipin.com/job_detail/b.html"
    )
    assert pick_active_page([older, newer], {}) is newer


def test_pick_active_page_prefers_the_most_recently_navigated_tab():
    """The tab the user last opened a job in wins, even if it is not newest.

    document.visibilityState cannot be used for this: Playwright disables
    background throttling, so every tab reports 'visible'.
    """
    from app.services.browser_session import pick_active_page

    first = _StubPage("https://www.zhipin.com/job_detail/a.html")
    second = _StubPage("https://www.zhipin.com/job_detail/b.html")
    third = _StubPage("https://www.zhipin.com/job_detail/c.html")

    # user opened them in order, then went back to the first tab and navigated
    activity = {first: 10, second: 5, third: 7}
    assert pick_active_page([first, second, third], activity) is first

    activity[second] = 20
    assert pick_active_page([first, second, third], activity) is second


def test_pick_active_page_ignores_untracked_tabs_when_others_are_active():
    from app.services.browser_session import pick_active_page

    tracked = _StubPage("https://www.zhipin.com/job_detail/a.html")
    untracked = _StubPage("https://www.zhipin.com/job_detail/b.html")
    assert pick_active_page([tracked, untracked], {tracked: 3}) is tracked


@pytest.mark.asyncio
async def test_capture_explains_a_blank_page_without_bypassing_anything(
    api, fake_session, fixture_page
):
    await _open_job_page(fixture_page, fake_session, "boss_job_blank.html")
    response = await api.post("/api/browser/capture-current-job")

    assert response.status_code == 422
    body = response.json()
    assert body["detail"]["reason"] == "page_empty"
    assert "空白" in body["message"]
    assert "手动粘贴" in body["message"], "must point at the manual fallback"
    assert (await api.get("/api/jobs")).json()["total"] == 0
