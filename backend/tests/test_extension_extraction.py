"""The Chrome extension's DOM extraction, against local fixtures only.

The *built* extension JavaScript is injected into a headless Chromium page that
has one of `extension/tests/fixtures/*.html` loaded, and the real
``BossExtract.detect()`` is called on it. So this tests the code that actually
ships, not a Python re-implementation of it - there is deliberately no second
extractor to drift.

Nothing here contacts zhipin.com, and nothing here loads the extension into a
real browser profile. Headless is a test-harness detail; the extension itself
only ever runs in the browser the human is already using.

A passing run proves the extractor handles the shapes these fixtures were
written for. It does **not** prove live BOSS compatibility - only the manual
check in ``extension/README.md`` can do that.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

EXTENSION = Path(__file__).resolve().parents[2] / "extension"
FIXTURES = EXTENSION / "tests" / "fixtures"
DIST = EXTENSION / "dist"

#: Injected in the same order ``manifest.json`` lists them.
BUNDLE = (DIST / "boss" / "selectors.js", DIST / "boss" / "extract.js")

SEARCH_URL = "https://www.zhipin.com/web/geek/job?query=%E4%BA%91%E8%AE%A1%E7%AE%97&city=101020100"
DETAIL_URL = "https://www.zhipin.com/job_detail/aaa111bbb222~.html?lid=trackme&securityId=secret-token"
USER_URL = "https://www.zhipin.com/web/user/resume"


@pytest.fixture(scope="session")
def extension_bundle() -> str:
    """The built extension sources, concatenated for injection.

    Skips (never fails) when the extension has not been built, mirroring how
    the suite treats a missing Playwright browser: a fresh clone can run
    everything else before ``npm run build`` has been executed in
    ``extension/``.
    """
    missing = [path.name for path in BUNDLE if not path.exists()]
    if missing:
        pytest.skip(
            "extension not built - run 'npm install && npm run build' in "
            f"extension/ (missing: {', '.join(missing)})"
        )
    return "\n;\n".join(path.read_text(encoding="utf-8") for path in BUNDLE)


@pytest.fixture(scope="session")
def extension_code(extension_bundle: str) -> str:
    """The bundle with comments stripped.

    The doc comments name the APIs the extractor must never touch, so a
    forbidden-API scan has to read code rather than prose.
    """
    without_blocks = re.sub(r"/\*.*?\*/", "", extension_bundle, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_blocks, flags=re.M)


async def _load_fixture(browser_page, extension_bundle: str, fixture: str, *, url: str) -> None:
    body = (FIXTURES / fixture).read_text(encoding="utf-8")

    async def _handler(route):
        await route.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=body
        )

    # Serve only this URL; every other request is aborted, so no test can
    # escape to the network even if a fixture grew a remote asset.
    await browser_page.route("**/*", lambda route: route.abort())
    await browser_page.route(url, _handler)
    await browser_page.goto(url, wait_until="domcontentloaded")

    # A real <script> element, not ``evaluate``: the bundle declares its
    # entry points with top-level ``var``, which only reaches the global
    # scope when the code runs as a script rather than inside a function.
    await browser_page.add_script_tag(content=extension_bundle)


@pytest.fixture
async def detect(browser_page, extension_bundle):
    """Load a fixture at a faked BOSS URL and run the real extractor on it."""

    async def _detect(fixture: str, *, url: str) -> dict:
        await _load_fixture(browser_page, extension_bundle, fixture, url=url)
        return await browser_page.evaluate(
            "() => BossExtract.detect(document, document.location.href)"
        )

    return _detect


@pytest.fixture
async def read_salary_filter(browser_page, extension_bundle):
    """Load a fixture at a faked BOSS URL and run the real filter reader."""

    async def _read(fixture: str, *, url: str) -> dict:
        await _load_fixture(browser_page, extension_bundle, fixture, url=url)
        return await browser_page.evaluate(
            "() => BossExtract.readSalaryFilterOptions(document)"
        )

    return _read


@pytest.fixture
async def diagnose(browser_page, extension_bundle):
    """Load a fixture at a faked BOSS URL and run the real structural diagnostic."""

    async def _diagnose(fixture: str, *, url: str) -> dict:
        await _load_fixture(browser_page, extension_bundle, fixture, url=url)
        return await browser_page.evaluate(
            "() => BossExtract.diagnoseDetail(document, document.location.href)"
        )

    return _diagnose


# --------------------------------------------------------------------------
# page type
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_job_detail_page_is_recognised(detect):
    result = await detect("boss_job_detail.html", url=DETAIL_URL)
    assert result["page_type"] == "detail"
    assert len(result["candidates"]) == 1


@pytest.mark.asyncio
async def test_a_search_page_is_recognised(detect):
    result = await detect("boss_search.html", url=SEARCH_URL)
    assert result["page_type"] == "search"
    assert len(result["candidates"]) == 3


@pytest.mark.asyncio
async def test_a_boss_page_with_no_job_is_unsupported(detect):
    """It must say "I cannot read this", not invent a posting."""
    result = await detect("unsupported.html", url=USER_URL)
    assert result["page_type"] == "unsupported"
    assert result["candidates"] == []
    assert result["errors"], "an unsupported page should explain itself"


@pytest.mark.asyncio
async def test_a_non_boss_host_is_unsupported(detect):
    result = await detect(
        "boss_job_detail.html", url="https://www.example.com/job_detail/abc.html"
    )
    assert result["page_type"] == "unsupported"
    assert result["candidates"] == []


# --------------------------------------------------------------------------
# detail extraction
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_fields_are_extracted(detect):
    result = await detect("boss_job_detail.html", url=DETAIL_URL)
    job = result["candidates"][0]

    assert job["title"] == "云平台工程师"
    assert job["company"] == "示例云科技（虚构公司）"
    assert job["salary_text"] == "25-40K·14薪"
    assert job["city"] == "上海"
    assert job["experience_text"] == "3-5年"
    assert job["education_text"] == "本科"
    assert job["external_id"] == "aaa111bbb222~"
    assert "Terraform" in job["description"]
    assert "任职要求" in job["description"]


@pytest.mark.asyncio
async def test_the_description_excludes_page_furniture(detect):
    """Nav, footer, inline script and 相似职位 must not land in the JD."""
    result = await detect("boss_job_detail.html", url=DETAIL_URL)
    description = result["candidates"][0]["description"]

    assert "相似职位" not in description
    assert "should_not_be_captured" not in description
    assert "__tracking" not in description
    assert "虚构页面" not in description


@pytest.mark.asyncio
async def test_a_missing_salary_is_reported_not_invented(detect):
    result = await detect("boss_job_no_salary.html", url=DETAIL_URL)
    job = result["candidates"][0]

    assert job["title"] == "SRE 工程师"
    assert job["salary_text"] is None
    assert "salary_text" in job["missing_fields"]
    assert any("薪资" in warning for warning in job["warnings"])
    # Everything else still comes through - one missing field is not a failure.
    assert job["city"] == "深圳"
    assert job["education_text"] == "硕士"
    assert job["description"]


# --------------------------------------------------------------------------
# search extraction
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_rendered_card_is_returned(detect):
    result = await detect("boss_search.html", url=SEARCH_URL)
    titles = [c["title"] for c in result["candidates"]]
    assert titles == ["云计算工程师", "Kubernetes 运维工程师", "DevOps 工程师"]


@pytest.mark.asyncio
async def test_card_fields_are_extracted(detect):
    result = await detect("boss_search.html", url=SEARCH_URL)
    first = result["candidates"][0]

    assert first["company"] == "示例云科技（虚构公司）"
    assert first["salary_text"] == "25-40K·14薪"
    assert first["city"] == "上海"
    assert first["experience_text"] == "3-5年"
    assert first["education_text"] == "本科"
    assert first["external_id"] == "aaa111bbb222~"


@pytest.mark.asyncio
async def test_a_card_carries_no_job_description(detect):
    """A card teaser is marketing copy, not a JD - and must not be sent as one."""
    result = await detect("boss_search.html", url=SEARCH_URL)
    for card in result["candidates"]:
        assert card["description"] is None
        assert "description" in card["missing_fields"]
        assert any("详情页" in warning for warning in card["warnings"])


@pytest.mark.asyncio
async def test_a_card_without_a_salary_still_yields_the_rest(detect):
    result = await detect("boss_search.html", url=SEARCH_URL)
    third = result["candidates"][2]

    assert third["title"] == "DevOps 工程师"
    assert third["salary_text"] is None
    assert third["experience_text"] == "经验不限"
    assert third["education_text"] == "大专"


# --------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tracking_and_security_parameters_are_stripped(detect):
    """BOSS puts a session-scoped securityId in the query. It must not survive."""
    result = await detect("boss_job_detail.html", url=DETAIL_URL)

    assert result["url"] == "https://www.zhipin.com/job_detail/aaa111bbb222~.html"
    assert "securityId" not in result["url"]
    assert "lid" not in result["url"]
    assert any("securityId" in warning for warning in result["warnings"])

    job_url = result["candidates"][0]["source_url"]
    assert job_url == "https://www.zhipin.com/job_detail/aaa111bbb222~.html"
    assert "?" not in job_url


@pytest.mark.asyncio
async def test_card_links_are_cleaned_and_absolute(detect):
    result = await detect("boss_search.html", url=SEARCH_URL)
    for card in result["candidates"]:
        assert card["source_url"].startswith("https://www.zhipin.com/job_detail/")
        assert "?" not in card["source_url"]
        assert "securityId" not in card["source_url"]


# --------------------------------------------------------------------------
# developer mode
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_field_records_the_selector_that_matched_it(detect):
    result = await detect("boss_job_detail.html", url=DETAIL_URL)
    matched = result["candidates"][0]["matched_selectors"]

    assert matched["title"] == ".job-banner .name h1"
    assert matched["salary_text"] == ".job-banner .salary"
    assert matched["description"].startswith(".job-detail-section")
    # Diagnostics only: selectors, never page content.
    for value in matched.values():
        assert "示例" not in value


@pytest.mark.asyncio
async def test_a_field_with_no_matching_selector_is_listed_as_missing(detect):
    result = await detect("boss_job_no_salary.html", url=DETAIL_URL)
    job = result["candidates"][0]

    assert "salary_text" not in job["matched_selectors"]
    assert "salary_text" in job["missing_fields"]


@pytest.mark.asyncio
async def test_an_obfuscated_salary_keeps_selector_diagnostics_but_no_value(detect):
    result = await detect("boss_job_pua_salary.html", url=DETAIL_URL)
    job = result["candidates"][0]

    assert job["salary_text"] is None
    assert job["matched_selectors"]["salary_text"] == ".job-detail-box .job-salary"
    assert "salary_text" in job["missing_fields"]
    assert any("薪资显示异常" in warning for warning in job["warnings"])


@pytest.mark.asyncio
async def test_live_standalone_detail_info_child_classes_are_extracted(detect):
    result = await detect("boss_job_detail_live_shape.html", url=DETAIL_URL)
    job = result["candidates"][0]

    assert job["city"] == "北京"
    assert job["company"] == "纳新电子"
    assert job["experience_text"] == "1-3年"
    assert job["education_text"] == "大专"
    assert job["description"] == "负责云平台日常运维与专业自动化建设。"
    assert job["matched_selectors"]["city"] == ".job-banner .info-primary .text-city"
    assert job["matched_selectors"]["company"] == ".job-boss-info .boss-info-attr"
    assert (
        job["matched_selectors"]["experience_text"]
        == ".job-banner .info-primary .text-experiece"
    )
    assert (
        job["matched_selectors"]["education_text"]
        == ".job-banner .info-primary .text-degree"
    )


@pytest.mark.asyncio
async def test_a_geek_jobs_listing_is_search_not_a_selected_detail_pane(detect):
    """Real live-site failure: `/web/geek/jobs` was misreported as one job
    (popup: "职位详情页", "识别到 1 个岗位"), with salary/city/experience/
    education/URL all missing - because classification tried to treat the
    selected-card detail pane BOSS renders beside the list as the one job on
    the page, then correlate it back to a single list card. It must classify
    as `search` and read the already-rendered list cards directly instead.
    """
    result = await detect("boss_search_split_pane_live_shape.html", url=SEARCH_URL)

    assert result["page_type"] == "search"
    assert len(result["candidates"]) == 3
    first, second, third = result["candidates"]

    assert first["title"] == "云计算运维工程师"
    assert first["company"] == "纳新电子"
    assert first["salary_text"] == "8-12K"
    assert first["city"] == "北京"
    assert first["experience_text"] == "1-3年"
    assert first["education_text"] == "大专"
    assert first["source_url"] == "https://www.zhipin.com/job_detail/live-card-1.html"
    assert first["external_id"] == "live-card-1"

    assert third["title"] == "云计算工程师"
    assert third["company"] == "信通院"
    assert third["salary_text"] == "16-19K"
    assert third["city"] == "北京"
    assert third["experience_text"] == "1年以内"
    assert third["education_text"] == "硕士"
    assert third["source_url"] == "https://www.zhipin.com/job_detail/live-card-3.html"


@pytest.mark.asyncio
async def test_a_geek_jobs_listing_never_mixes_fields_across_duplicate_title_cards(detect):
    """The second card shares its title *and* company with the first - two
    separate postings, exactly the shape that once let a shared-ancestor bug
    silently overwrite one card's salary with another's (see git history).
    Each card's own fields must stay its own."""
    result = await detect("boss_search_split_pane_live_shape.html", url=SEARCH_URL)
    first, second, _third = result["candidates"]

    assert second["title"] == first["title"] == "云计算运维工程师"
    assert second["company"] == first["company"] == "纳新电子"
    assert second["salary_text"] == "18-25K"
    assert second["city"] == "北京"
    assert second["experience_text"] == "3-5年"
    assert second["education_text"] == "本科"
    assert second["source_url"] == "https://www.zhipin.com/job_detail/live-card-2.html"
    assert second["salary_text"] != first["salary_text"]
    assert second["source_url"] != first["source_url"]


@pytest.mark.asyncio
async def test_a_geek_jobs_listing_never_leaks_the_selected_detail_pane(detect):
    """The pane's own title/company/salary/description are deliberately
    distinct from every list card, so any leak into a candidate is caught."""
    result = await detect("boss_search_split_pane_live_shape.html", url=SEARCH_URL)

    rendered = str(result)
    assert "详情面板" not in rendered
    assert "99-99K" not in rendered
    assert all(c["description"] is None for c in result["candidates"])


# --------------------------------------------------------------------------
# fallback card discovery - fixed BossSelectors.CARD selectors miss entirely
# --------------------------------------------------------------------------
#
# Confirmed live failure: on real `/web/geek/jobs`, the fixed card-root
# selectors (`.job-card-wrapper`, `.job-list-box .job-card-box`, ...) return
# zero matches even though several cards are visibly rendered. `extractSearch`
# and `openCandidateLink` then fall back to discovering cards from their own
# canonical `/job_detail/<id>.html` anchors - never from `.job-detail-box`,
# the selected-pane's own, unrelated link.


@pytest.mark.asyncio
async def test_fallback_discovery_finds_cards_when_fixed_selectors_miss(detect):
    result = await detect("boss_search_fallback_card_discovery.html", url=SEARCH_URL)

    assert result["page_type"] == "search"
    # Exactly the two real cards - not three (the pane's own link excluded)
    # and not more from the duplicate in-card anchor being double-counted.
    assert len(result["candidates"]) == 2
    assert any("回退识别候选人卡片" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_fallback_discovery_dedupes_two_anchors_on_the_same_card(detect):
    """The first card has two `/job_detail/` anchors pointing at the same
    canonical URL - a title link and a "查看详情" link. That must produce one
    candidate, never two."""
    result = await detect("boss_search_fallback_card_discovery.html", url=SEARCH_URL)
    urls = [c["source_url"] for c in result["candidates"]]

    assert urls.count("https://www.zhipin.com/job_detail/fallback-card-1.html") == 1
    assert "https://www.zhipin.com/job_detail/fallback-card-2.html" in urls


@pytest.mark.asyncio
async def test_fallback_discovery_never_leaks_the_selected_pane_as_a_card(detect):
    result = await detect("boss_search_fallback_card_discovery.html", url=SEARCH_URL)
    urls = [c["source_url"] for c in result["candidates"]]

    assert "https://www.zhipin.com/job_detail/should-not-be-a-card.html" not in urls
    rendered = str(result)
    assert "详情面板" not in rendered


@pytest.mark.asyncio
async def test_fallback_discovery_never_mixes_fields_across_duplicate_title_cards(detect):
    """Both fallback-discovered cards share a title and company - the same
    shape that once let a shared-ancestor bug overwrite one card's salary
    with another's. Each card's own fields must stay its own."""
    result = await detect("boss_search_fallback_card_discovery.html", url=SEARCH_URL)
    first, second = result["candidates"]

    assert first["title"] == second["title"] == "云计算运维工程师"
    assert first["company"] == second["company"] == "纳新电子"
    assert first["salary_text"] == "10-15K"
    assert first["city"] == "北京"
    assert first["experience_text"] == "3-5年"
    assert first["education_text"] == "本科"
    assert first["external_id"] == "fallback-card-1"

    assert second["salary_text"] == "20-30K"
    assert second["city"] == "北京"
    assert second["experience_text"] == "5-10年"
    assert second["education_text"] == "硕士"
    assert second["external_id"] == "fallback-card-2"

    assert first["salary_text"] != second["salary_text"]
    assert first["source_url"] != second["source_url"]


# --------------------------------------------------------------------------
# structural diagnostic (dev mode) - live-detail-diagnostic milestone
# --------------------------------------------------------------------------
#
# `BossExtract.diagnoseDetail()` is a separate, explicit-click, dev-mode-only
# entry point (never part of `detect()`'s output). It exists to help tune
# `selectors.ts` for company / city / experience / education / description on
# a *live* page, by showing bounded, sanitized structure near the anchors
# (title, salary, the detail root) that already extract reliably.


@pytest.mark.asyncio
async def test_diagnostic_finds_all_three_anchors_on_a_normal_detail_page(diagnose):
    result = await diagnose("boss_job_detail.html", url=DETAIL_URL)

    assert result["page_type"] == "detail"
    anchors = {a["anchor"]: a for a in result["anchors"]}
    assert set(anchors) == {"title", "salary", "detail_root"}
    for anchor in anchors.values():
        assert anchor["found"] is True
        assert anchor["anchor_selector"]
        assert anchor["nodes"], f"anchor {anchor['anchor']} reported no nearby nodes"


@pytest.mark.asyncio
async def test_diagnostic_nodes_carry_only_tag_class_and_a_short_sample(diagnose):
    result = await diagnose("boss_job_detail.html", url=DETAIL_URL)

    for anchor in result["anchors"]:
        for node in anchor["nodes"]:
            assert set(node) == {"relation", "tag", "classes", "sample"}
            assert isinstance(node["classes"], list)
            if node["sample"] is not None:
                assert len(node["sample"]) <= 41  # 40 chars + an ellipsis


@pytest.mark.asyncio
async def test_diagnostic_surfaces_company_info_near_the_title_anchor(diagnose):
    """The whole point: company/city/etc structure should show up near title."""
    result = await diagnose("boss_job_detail.html", url=DETAIL_URL)

    title_anchor = next(a for a in result["anchors"] if a["anchor"] == "title")
    classes_seen = {cls for node in title_anchor["nodes"] for cls in node["classes"]}
    assert "company-info" in classes_seen


@pytest.mark.asyncio
async def test_live_shape_diagnostic_prioritizes_job_boss_info(diagnose):
    result = await diagnose("boss_job_detail_live_shape.html", url=DETAIL_URL)

    company_anchor = next(
        anchor for anchor in result["anchors"] if anchor["anchor"] == "company_root"
    )
    assert company_anchor["anchor_selector"] == ".job-boss-info"
    assert any("job-boss-info" in node["classes"] for node in company_anchor["nodes"])


@pytest.mark.asyncio
async def test_diagnostic_never_reaches_nav_chat_or_footer(diagnose):
    result = await diagnose("boss_job_detail.html", url=DETAIL_URL)

    for anchor in result["anchors"]:
        for node in anchor["nodes"]:
            assert node["tag"] not in {"nav", "header", "footer", "script"}
            for cls in node["classes"]:
                assert "nav" not in cls
                assert "chat" not in cls
                assert "footer" not in cls


@pytest.mark.asyncio
async def test_diagnostic_is_bounded_not_a_page_dump(diagnose):
    result = await diagnose("boss_job_detail.html", url=DETAIL_URL)

    total_nodes = sum(len(a["nodes"]) for a in result["anchors"])
    assert 0 < total_nodes <= 80  # BossExtract.MAX_DIAGNOSTIC_NODES

    for anchor in result["anchors"]:
        for node in anchor["nodes"]:
            if node["sample"]:
                assert "岗位职责" not in node["sample"]
                assert "任职要求" not in node["sample"]


@pytest.mark.asyncio
async def test_diagnostic_only_supports_detail_pages(diagnose):
    result = await diagnose("boss_search.html", url=SEARCH_URL)

    assert result["page_type"] == "search"
    assert result["anchors"] == []
    assert result["errors"], "a non-detail page should explain itself, not return partial data"


@pytest.mark.asyncio
async def test_diagnostic_reports_a_missing_anchor_rather_than_guessing(diagnose):
    result = await diagnose("boss_job_no_salary.html", url=DETAIL_URL)

    salary_anchor = next(a for a in result["anchors"] if a["anchor"] == "salary")
    assert salary_anchor["found"] is False
    assert salary_anchor["anchor_selector"] is None
    assert salary_anchor["nodes"] == []


@pytest.mark.asyncio
async def test_diagnostic_is_not_part_of_the_regular_detect_call(detect):
    """Explicit-click only: a plain detection must never carry diagnostic data."""
    result = await detect("boss_job_detail.html", url=DETAIL_URL)
    assert "anchors" not in result


@pytest.mark.asyncio
async def test_diagnostic_redacts_identifiers_and_never_inherits_excluded_descendant_text(
    browser_page, extension_bundle
):
    await _load_fixture(browser_page, extension_bundle, "boss_job_detail.html", url=DETAIL_URL)
    await browser_page.evaluate(
        """() => {
          const title = document.querySelector('.job-banner .name h1');
          const probe = document.createElement('span');
          probe.className = 'diagnostic-probe';
          probe.textContent = 'hr@example.test 13812345678 www.example.test token=abc123def456ghi789';
          title.parentElement.appendChild(probe);
          const wrapper = document.createElement('div');
          wrapper.className = 'diagnostic-wrapper';
          const privateChild = document.createElement('span');
          privateChild.className = 'chat-private';
          privateChild.textContent = 'PRIVATE_CHAT_TEXT';
          wrapper.appendChild(privateChild);
          title.parentElement.appendChild(wrapper);
        }"""
    )

    result = await browser_page.evaluate(
        "() => BossExtract.diagnoseDetail(document, document.location.href)"
    )
    rendered = str(result)
    assert "hr@example.test" not in rendered
    assert "13812345678" not in rendered
    assert "www.example.test" not in rendered
    assert "abc123def456ghi789" not in rendered
    assert "PRIVATE_CHAT_TEXT" not in rendered
    assert "【邮箱】" in rendered
    assert "【电话】" in rendered
    assert "【链接】" in rendered
    assert "【令牌】" in rendered


def test_popup_diagnostic_copy_never_touches_the_document_body():
    popup_source = (EXTENSION / "src" / "popup.ts").read_text(encoding="utf-8")
    assert "document.body" not in popup_source
    assert "execCommand" not in popup_source


# --------------------------------------------------------------------------
# what it must never do
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detection_does_not_modify_the_page(detect, browser_page):
    """Reading is not editing: the description is cleaned on a clone."""
    await detect("boss_job_detail.html", url=DETAIL_URL)
    still_there = await browser_page.evaluate(
        "() => document.querySelectorAll('.job-similar').length"
    )
    assert still_there == 1


@pytest.mark.asyncio
async def test_no_cookies_storage_or_forms_are_read(extension_code):
    """Asserted against the shipped source, so it cannot regress quietly."""
    forbidden = (
        "document.cookie",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "XMLHttpRequest",
        "navigator.credentials",
    )
    for needle in forbidden:
        assert needle not in extension_code, f"extraction must not touch {needle}"


@pytest.mark.asyncio
async def test_the_extractor_never_submits_or_navigates(extension_code):
    """No native form submit or self-navigation is hidden in extraction.

    The separately pinned test below permits exactly the M4 navigation click
    and the M6 per-approval application click. Scroll calls remain pinned by
    the following test rather than a bare absence assertion.
    """
    forbidden = (
        ".submit(",
        "location.assign",
        "location.replace",
        "window.open",
        "setInterval",
    )
    for needle in forbidden:
        assert needle not in extension_code, f"extraction must not call {needle}"


@pytest.mark.asyncio
async def test_only_the_named_m4_and_m6_click_primitives_exist(extension_code):
    """Pin the three and only three explicitly authorized click call sites.

    M4 uses `clickAnchor` for supervised card traversal. M6 has one call for
    the application itself, after the background worker has atomically claimed
    one per-job human approval and the content script has repeated exact
    identity preflight. M6 has a second call - the send control of the chat
    composer - added under the 2026-09-03 authorization: BOSS turned out to
    send its own greeting sometimes and not others, so typing the confirmed
    greeting is its own action rather than the application click's other half.
    It fires only into a composer that resolved unambiguously and was already
    empty. The suspended M7 chat scanner has no runtime click or parser
    surface.

    The count is pinned, not just the presence: a fourth click site has to be
    a decision someone made on purpose, which is exactly how this one arrived.
    """
    assert extension_code.count(".click(") == 3, (
        "authorized: M4 navigation, M6 application, M6 confirmed greeting"
    )
    assert "anchor.click()" in extension_code
    assert "control.node.click()" in extension_code
    assert "composer.send.click()" in extension_code


@pytest.mark.asyncio
async def test_the_only_scroll_is_the_authorized_m4c_bounded_step(extension_code):
    """M4c (CLAUDE.md "Chrome extension - M4 supervised navigation policy",
    explicitly authorized) permits exactly one scroll call site:
    `scrollResultsContainer`'s single, bounded `scrollBy`. No `scrollTo` or
    `scrollIntoView` call anywhere, and no second `scrollBy` site either -
    this pins the count, not just the presence."""
    assert extension_code.count("scrollBy(") == 1, (
        "exactly one scroll site is authorized (scrollResultsContainer); "
        "found a different count"
    )
    assert "container.scrollBy(" in extension_code
    assert "scrollTo(" not in extension_code
    assert "scrollIntoView(" not in extension_code


@pytest.mark.asyncio
async def test_a_verification_page_is_reported_and_never_solved(detect):
    result = await detect(
        "unsupported.html", url="https://www.zhipin.com/web/common/security-check"
    )
    assert result["verification"] is True
    assert any("安全验证" in warning for warning in result["warnings"])
    assert result["candidates"] == []


# --------------------------------------------------------------------------
# the seam: what the extension emits is what the backend accepts
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_extractor_output_feeds_the_backend_unchanged(detect, client):
    """The one integration that matters: no hand-editing between the two halves.

    Real extraction in a real DOM, then the exact JSON the popup would POST.
    If either side changes shape, this fails instead of failing in Chrome.
    """
    result = await detect("boss_job_detail.html", url=DETAIL_URL)

    preview = client.post(
        "/api/extension/jobs/preview",
        json={
            "page_type": result["page_type"],
            "page_url": result["url"],
            "candidates": result["candidates"],
        },
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["detected"] == 1
    assert body["new_count"] == 1

    saved = client.post(
        "/api/extension/jobs/import",
        json={"confirmed": True, "candidate": result["candidates"][0]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["title"] == "云平台工程师"

    # And now the same page reads as a duplicate.
    again = client.post(
        "/api/extension/jobs/preview",
        json={"page_type": result["page_type"], "candidates": result["candidates"]},
    )
    assert again.json()["duplicate_count"] == 1


@pytest.mark.asyncio
async def test_search_cards_are_refused_by_the_backend(detect, client):
    """Cards carry no JD, so the backend must call them incomplete, not new."""
    result = await detect("boss_search.html", url=SEARCH_URL)

    body = client.post(
        "/api/extension/jobs/preview",
        json={
            "page_type": "search",
            "page_url": result["url"],
            "candidates": result["candidates"],
        },
    ).json()

    assert body["detected"] == 3
    assert body["incomplete_count"] == 3
    assert body["new_count"] == 0


# --------------------------------------------------------------------------
# salary integrity - PUA glyph text must never be persisted as a real salary,
# on a search card exactly as already enforced on a detail pane, and a
# usable pane salary may fill in for an unusable/missing card salary during
# the two-phase capture merge.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_search_card_with_an_unusable_salary_never_reports_it_as_real(detect):
    result = await detect("boss_search_card_pua_salary.html", url=SEARCH_URL)
    card = result["candidates"][0]

    assert card["salary_text"] is None
    # Requirement 3: a clear selector diagnostic is retained even though the
    # value itself is rejected - the node was found, just unusable.
    assert card["matched_selectors"]["salary_text"] == ".salary"
    assert "salary_text" in card["missing_fields"]
    assert any("特殊字体" in w for w in card["warnings"])


@pytest.mark.asyncio
async def test_a_search_card_finds_the_company_in_the_live_boss_info_shape(detect):
    """Live logged-in Chrome (2026-08): a real search card had no
    `.company-name` anywhere - the company lived in
    `a.boss-info > span.boss-name` instead."""
    result = await detect("boss_search_card_boss_info_company.html", url=SEARCH_URL)
    card = result["candidates"][0]

    assert card["title"] == "云计算运维工程师"
    assert card["company"] == "纳新电子"
    assert card["matched_selectors"]["company"] == "a.boss-info span.boss-name"
    # The accepted salary-integrity behavior must stay intact alongside this
    # selector addition - a readable salary, never PUA/placeholder text.
    assert card["salary_text"] == "20-35K"


@pytest.mark.asyncio
async def test_merge_fills_an_unusable_card_salary_from_a_usable_pane_salary(browser_page, extension_bundle):
    await _load_fixture(browser_page, extension_bundle, "boss_job_detail.html", url=DETAIL_URL)
    cached_card = {
        "title": "云平台工程师",
        "company": "示例云科技（虚构公司）",
        "salary_text": "-K",
        "city": None,
        "experience_text": None,
        "education_text": None,
        "source_url": None,
        "external_id": None,
        "matched_selectors": {"salary_text": ".stale-card-salary-node"},
    }
    result = await browser_page.evaluate(
        "([url, canonicalUrl, cachedCard]) => "
        "BossExtract.captureAndMerge(document, url, canonicalUrl, cachedCard)",
        [DETAIL_URL, DETAIL_URL, cached_card],
    )

    assert result["status"] == "ok"
    assert result["candidate"]["salary_text"] == "25-40K·14薪"
    # Requirement 3 - the rejected cached selector must not linger and
    # misleadingly point at the (unusable) card value once the pane's own
    # usable value has won.
    assert result["candidate"]["matched_selectors"]["salary_text"] != ".stale-card-salary-node"


@pytest.mark.asyncio
async def test_merge_reports_a_missing_salary_when_both_card_and_pane_are_unusable(browser_page, extension_bundle):
    await _load_fixture(browser_page, extension_bundle, "boss_job_no_salary.html", url=DETAIL_URL)
    cached_card = {
        "title": "SRE 工程师",
        "company": None,
        "salary_text": "-K",
        "city": None,
        "experience_text": None,
        "education_text": None,
        "source_url": None,
        "external_id": None,
    }
    result = await browser_page.evaluate(
        "([url, canonicalUrl, cachedCard]) => "
        "BossExtract.captureAndMerge(document, url, canonicalUrl, cachedCard)",
        [DETAIL_URL, DETAIL_URL, cached_card],
    )

    assert result["status"] == "ok"
    assert result["candidate"]["salary_text"] is None
    assert "salary_text" in result["candidate"]["missing_fields"]


# --------------------------------------------------------------------------
# M6: the application control, against attribute shapes captured read-only
# from a real logged-in BOSS page (2026-08-31). These are behavioural tests
# against the built bundle, not source greps.
# --------------------------------------------------------------------------


EXPECTED = {
    "canonical_url": "https://www.zhipin.com/job_detail/aaa111bbb222~.html",
    "external_id": "aaa111bbb222~",
    "title": "云计算运维工程师",
    "company": "纳新电子",
}


@pytest.fixture
async def preflight(browser_page, extension_bundle):
    async def _preflight(fixture: str, *, url: str = DETAIL_URL, expected=None) -> dict:
        await _load_fixture(browser_page, extension_bundle, fixture, url=url)
        return await browser_page.evaluate(
            "(expected) => BossExtract.preflightConfirmedApplication("
            "document, document.location.href, expected)",
            expected or EXPECTED,
        )

    return _preflight


async def test_a_fresh_job_passes_preflight_with_exactly_one_visible_control(preflight):
    """Live shape: `.btn-startchat` matches 2 nodes, exactly 1 visible."""
    result = await preflight("boss_job_detail_live_shape.html")
    assert result["status"] == "ok"
    assert result["observed_external_id"] == "aaa111bbb222~"
    assert "securityId" not in result["observed_url"]
    assert "lid" not in result["observed_url"]


async def test_an_already_chatted_job_is_refused_and_never_clicked(preflight, browser_page):
    """THE boundary: on a job already in conversation the control reads
    `data-isfriend="true"` / 继续沟通. Clicking it would send a follow-up
    message, which M6 forbids outright. It must refuse, not apply."""
    result = await preflight("boss_job_detail_already_chatted.html")
    assert result["status"] == "control_wrong_state"
    assert "observed_url" not in result or not result.get("observed_url")

    # And the execute primitive refuses too - preflight is not the only guard.
    executed = await browser_page.evaluate(
        "(expected) => BossExtract.executeConfirmedApplication("
        "document, document.location.href, expected)",
        EXPECTED,
    )
    assert executed["status"] == "control_wrong_state"
    assert executed["status"] != "clicked"


async def test_a_job_whose_identity_does_not_match_is_refused(preflight):
    result = await preflight(
        "boss_job_detail_live_shape.html",
        expected={**EXPECTED, "external_id": "some-other-job"},
    )
    assert result["status"] == "identity_mismatch"


async def test_the_search_split_pane_is_not_an_application_surface(preflight):
    """M6 executes only on a job_detail page, never the search results pane."""
    result = await preflight(
        "boss_search_split_pane_live_shape.html", url=SEARCH_URL
    )
    assert result["status"] != "ok"


# --------------------------------------------------------------------------
# the confirmed greeting (authorized 2026-09-03)
# --------------------------------------------------------------------------

GREETING = "您好，我有近2年云基础设施经验，希望进一步沟通。"


@pytest.mark.asyncio
async def test_the_greeting_is_refused_once_the_tab_has_left_the_job_page(
    browser_page, extension_bundle
):
    """BOSS answered 立即沟通 by navigating the whole tab to `/web/geek/chat`
    on four consecutive applications on 2026-09-06, instead of opening its
    usual in-page panel.

    The composer resolves there perfectly well - it just belongs to whichever
    conversation BOSS happened to select. Typing into it is a message to a
    real person who may not be the one this approval names, so the page is
    checked before anything is typed at all.
    """
    await _load_fixture(
        browser_page, extension_bundle, "boss_job_detail_chat_open.html", url=DETAIL_URL
    )
    await browser_page.evaluate(
        "() => { window.__sent = 0;"
        "  document.querySelectorAll('.chat-panel .btn-send').forEach("
        "    (b) => b.addEventListener('click', () => { window.__sent += 1 })) }"
    )
    outcome = await browser_page.evaluate(
        """(text) => {
          const chat = 'https://www.zhipin.com/web/geek/chat'
          const other = 'https://www.zhipin.com/job_detail/zzz999~.html'
          return {
            chat: BossExtract.sendConfirmedGreeting(document, text, chat, 'aaa111bbb222~').status,
            other: BossExtract.sendConfirmedGreeting(document, text, other, 'aaa111bbb222~').status,
            missing: BossExtract.sendConfirmedGreeting(
              document, text, document.location.href, '').status,
            clicks: window.__sent,
            typed: document.querySelector('.chat-panel .chat-input').value,
          }
        }""",
        GREETING,
    )
    assert outcome["chat"] == "left_job_page", "the chat page is not this job's page"
    assert outcome["other"] == "wrong_job", "another job's page is not this job's page"
    assert outcome["missing"] == "wrong_job", "no bound id means no send"
    assert outcome["clicks"] == 0, "nothing was sent"
    assert outcome["typed"] == "", "and nothing was even typed"


@pytest.fixture
async def composer(browser_page, extension_bundle):
    """Load a fixture and run the real composer resolver on it."""

    async def _composer(fixture: str = "boss_job_detail_chat_open.html", *, prepare: str = "") -> dict:
        await _load_fixture(browser_page, extension_bundle, fixture, url=DETAIL_URL)
        if prepare:
            await browser_page.evaluate(prepare)
        return await browser_page.evaluate("() => BossExtract.greetingComposer(document).status")

    return _composer


@pytest.fixture
async def send_greeting(browser_page, extension_bundle):
    """Load a fixture and attempt one real send on it."""

    async def _send(fixture: str = "boss_job_detail_chat_open.html", *, prepare: str = "",
                    greeting: str = GREETING) -> dict:
        await _load_fixture(browser_page, extension_bundle, fixture, url=DETAIL_URL)
        await browser_page.evaluate(
            "() => { window.__sent = 0;"
            "  document.querySelectorAll('.chat-panel .btn-send').forEach("
            "    (b) => b.addEventListener('click', () => { window.__sent += 1 })) }"
        )
        if prepare:
            await browser_page.evaluate(prepare)
        status = await browser_page.evaluate(
            "(text) => BossExtract.sendConfirmedGreeting("
            "  document, text, document.location.href, 'aaa111bbb222~').status",
            greeting,
        )
        return {
            "status": status,
            "clicks": await browser_page.evaluate("() => window.__sent"),
            "value": await browser_page.evaluate(
                "() => document.querySelector('.chat-panel .chat-input').value"
            ),
        }

    return _send


async def test_an_empty_visible_composer_resolves(composer):
    """The hidden second composer must not make this ambiguous."""
    assert await composer() == "ok"


async def test_a_composer_that_already_has_text_is_refused(composer):
    """BOSS greets on its own sometimes and not others - both were observed on
    the live site. Typing into a box that is not empty would append a second
    message to whatever is already there."""
    assert await composer(
        prepare="() => { document.querySelector('.chat-panel .chat-input').value = 'BOSS 已发的招呼语' }"
    ) == "input_not_empty"


async def test_two_visible_composers_are_ambiguous_and_refused(composer):
    assert await composer(
        prepare="() => { document.querySelector('.hidden-composer').style.display = 'block' }"
    ) == "ambiguous_composer"


async def test_a_page_with_no_composer_is_refused(composer):
    assert await composer("boss_job_detail.html") in {"no_composer", "no_send_control"}


async def test_sending_types_the_exact_text_and_clicks_once(send_greeting):
    result = await send_greeting()
    assert result["status"] == "sent"
    assert result["value"] == GREETING, "the bound text is typed verbatim"
    assert result["clicks"] == 1, "exactly one send"


async def test_nothing_is_typed_or_sent_when_the_box_is_not_empty(send_greeting):
    existing = "BOSS 已经发过的招呼语"
    result = await send_greeting(
        prepare=f"() => {{ document.querySelector('.chat-panel .chat-input').value = {existing!r} }}"
    )
    assert result["status"] == "input_not_empty"
    assert result["clicks"] == 0, "no message was sent"
    assert result["value"] == existing, "and the box was left alone"


async def test_an_empty_greeting_sends_nothing(send_greeting):
    for blank in ("", "   "):
        result = await send_greeting(greeting=blank)
        assert result["status"] == "empty_greeting"
        assert result["clicks"] == 0


async def test_the_composer_is_the_only_page_mutation_besides_the_click(extension_code):
    """No follow-up message primitive exists: one send, and nothing after it."""
    for forbidden in ("setInterval", "MutationObserver"):
        assert forbidden not in extension_code


@pytest.fixture
async def greeting_shape(browser_page, extension_bundle):
    async def _shape(fixture: str) -> str:
        await _load_fixture(browser_page, extension_bundle, fixture, url=DETAIL_URL)
        return await browser_page.evaluate("() => BossExtract.greetingDiagnostic(document)")

    return _shape


async def test_the_diagnostic_distinguishes_an_iframe_from_a_missing_panel(greeting_shape):
    """Three live runs failed three ways with the same code. The next fix has
    to come from what the page contains, not another guess."""
    shape = await greeting_shape("boss_job_detail_chat_iframe.html")
    assert "ta=0/0" in shape, "no textarea in this document"
    assert "www.zhipin.com" in shape, "and the reason is an iframe, which it names"

    resolved = await greeting_shape("boss_job_detail_chat_open.html")
    # visible/total: the fixture also carries a hidden composer, and reporting
    # both numbers is the point - "one of two" reads very differently from
    # "one of one" when a live page refuses as ambiguous.
    assert "ta=1/2" in resolved
    assert "ifr=0" in resolved
    assert "div.btn-send" in resolved, "it names the send control it did find"


async def test_the_diagnostic_reports_shape_and_never_content(greeting_shape):
    """A chat panel is full of a real person's messages, and a job page carries
    URLs with session tokens. Neither may leave the page."""
    shape = await greeting_shape("boss_job_detail_chat_open.html")
    assert "负责云平台" not in shape, "no page text"
    assert "请简短描述" not in shape, "no placeholder text"
    assert "http" not in shape, "no URLs - iframe hosts only"
    assert len(shape) <= 180, "bounded, so it fits the recorded attempt detail"


# --------------------------------------------------------------------------
# a closed posting (auto-skip on discovery, user asked 2026-09-04)
# --------------------------------------------------------------------------


async def test_a_closed_posting_is_reported_as_closed_not_as_a_missing_button(preflight):
    """These need different handling: only one is a reason to retire the job.

    Identity is still checked first, and deliberately so: a *different* job
    that happens to be closed must never retire the one being confirmed.
    """
    expected = {**EXPECTED, "title": "中间件运维工程师", "company": "示例公司"}
    result = await preflight("boss_job_detail_closed.html", expected=expected)
    assert result["status"] == "posting_closed"


async def test_a_closed_page_for_a_different_job_never_retires_this_one(preflight):
    result = await preflight("boss_job_detail_closed.html")  # default identity
    assert result["status"] == "identity_mismatch"


async def test_a_missing_apply_button_is_never_read_as_closed(browser_page, extension_bundle):
    """The control is also absent while a page loads, and it reads 继续沟通 on a
    job already contacted. Inferring closure from either would retire a job the
    user still wants, so closure needs BOSS's own words and nothing less."""
    for fixture in (
        "boss_job_detail_already_chatted.html",
        "boss_job_detail_chat_iframe.html",
        "boss_job_detail.html",
    ):
        await _load_fixture(browser_page, extension_bundle, fixture, url=DETAIL_URL)
        closed = await browser_page.evaluate("() => BossExtract.postingClosed(document)")
        assert closed is False, f"{fixture} was wrongly read as closed"


async def test_the_phrase_inside_a_job_description_does_not_close_the_job(browser_page, extension_bundle):
    """The fixture's description contains 职位已关闭 in a sentence. Only a node
    whose entire text is the marker counts, so prose cannot retire a job."""
    await _load_fixture(browser_page, extension_bundle, "boss_job_detail_live_shape.html", url=DETAIL_URL)
    assert await browser_page.evaluate("() => BossExtract.postingClosed(document)") is False


# --------------------------------------------------------------------------
# salary on the page the runner actually captures from
# --------------------------------------------------------------------------


async def test_the_detail_panel_on_a_results_page_yields_its_own_salary(browser_page, extension_bundle):
    """111 of 148 captures had no salary while the page plainly showed one.

    Every other detail fixture is a standalone /job_detail/ page - the shape the
    *backfill* visits, and it succeeds 334 times out of 334. The runner captures
    from a results page with the posting open beside the list, where each card
    carries a salary of its own. Nothing tested that, so this pins it: the
    panel's own salary is what comes back, and the cards beside it must not turn
    into a conflict that yields nothing.
    """
    await _load_fixture(
        browser_page, extension_bundle, "boss_search_with_detail_panel.html",
        url="https://www.zhipin.com/web/geek/jobs?query=&city=101210100",
    )
    # `captureAndMerge` is what the runner calls, so the test exercises the
    # same entry point rather than a convenient inner one.
    outcome = await browser_page.evaluate(
        """() => BossExtract.captureAndMerge(
             document,
             document.location.href,
             'https://www.zhipin.com/job_detail/panel111aaa~.html',
             { title: '云驻场运维工程师', company: '杭州亮通', salary_text: null },
           )"""
    )
    assert outcome["status"] == "ok", outcome
    candidate = outcome["candidate"]
    assert candidate["salary_text"] == "11-16K", (
        f"read {candidate['salary_text']!r}; warnings {candidate.get('warnings')}"
    )


async def test_a_failed_salary_read_records_the_category_not_the_figure(browser_page, extension_bundle):
    """Compensation values are never written to a note or a log - but the
    *reason* has to be, or the intake note can only report that the OCR
    fallback was refused, which explains nothing about the DOM read."""
    await _load_fixture(browser_page, extension_bundle, "boss_job_pua_salary.html", url=DETAIL_URL)
    result = await browser_page.evaluate(
        "() => BossExtract.detect(document, document.location.href)"
    )
    candidate = result["candidates"][0]
    assert candidate["salary_text"] is None
    reasons = [w for w in candidate.get("warnings", []) if "薪资未读到" in w]
    assert reasons, f"no reason recorded; warnings {candidate.get('warnings')}"
    assert "unreadable" in reasons[0]


# --------------------------------------------------------------------------
# the results-page salary filter, read (never clicked)
#
# These fixtures are AUTHORED, not captured - no save of the BOSS filter bar
# existed, and no automated test may visit zhipin.com to make one. They pin the
# reader's logic. Whether the live bar matches stays a manual claim, which is
# why the console shows every label with its code and asks the human to check
# one against BOSS's own URL.
# --------------------------------------------------------------------------

SEARCH_URL = "https://www.zhipin.com/web/geek/jobs?city=101010100&query=%E8%BF%90%E7%BB%B4"


async def test_salary_bands_are_read_with_their_codes(read_salary_filter):
    result = await read_salary_filter("boss_search_salary_filter.html", url=SEARCH_URL)
    assert result["reason"] is None
    assert result["options"] == [
        {"label": "不限", "code": "401"},
        {"label": "3K以下", "code": "402"},
        {"label": "10-20K", "code": "405"},
        {"label": "20-50K", "code": "406"},
        {"label": "50K以上", "code": "407"},
    ]


async def test_the_neighbouring_menus_are_never_read_as_salary(read_salary_filter):
    """区域 and 经验 sit in identical markup right beside it.

    Anchoring on BOSS's own label is the whole reason this does not read the
    wrong menu - and a district code offered as a salary band would produce a
    search nobody asked for.
    """
    result = await read_salary_filter("boss_search_salary_filter.html", url=SEARCH_URL)
    codes = {row["code"] for row in result["options"]}
    assert not codes & {"110105", "110108", "110106", "104", "105"}


async def test_a_code_on_the_option_itself_is_read(read_salary_filter):
    result = await read_salary_filter(
        "boss_search_salary_filter_data_attr.html", url=SEARCH_URL
    )
    assert [row["code"] for row in result["options"]] == ["401", "405", "406", "407"]


async def test_bands_without_codes_are_reported_not_paired_with_a_guess(read_salary_filter):
    result = await read_salary_filter(
        "boss_search_salary_filter_no_codes.html", url=SEARCH_URL
    )
    assert result["options"] == []
    assert result["labels_without_code"] == 3
    assert result["reason"] == "codes_not_found"


async def test_a_detail_page_is_not_read_for_filters(read_salary_filter):
    result = await read_salary_filter(
        "boss_job_detail.html", url="https://www.zhipin.com/job_detail/abc123.html"
    )
    assert result["options"] == []
    assert result["reason"] == "not_a_search_page"


async def test_a_search_page_without_a_filter_bar_says_so(read_salary_filter):
    result = await read_salary_filter("boss_search.html", url=SEARCH_URL)
    assert result["options"] == []
    assert result["reason"] == "menu_not_found"


@pytest.mark.asyncio
async def test_one_scroll_round_reaches_the_end_of_the_list(browser_page, extension_bundle):
    """BOSS loads its next batch when the list's end comes into view.

    A fixed one-viewport step reached the end of the first 15 cards, pulled in
    15 more, and then fell behind forever: every later scroll landed in the
    middle of a list whose end had moved away. Measured on 2026-09-05 as
    "exactly 30 observed cards, then nothing" on every task in every city -
    which read like a BOSS limit and was ours.
    """
    await _load_fixture(
        browser_page, extension_bundle, "boss_search_tall_list.html", url=SEARCH_URL
    )
    reached = await browser_page.evaluate(
        """() => {
          const box = document.querySelector('.job-list-box')
          const before = box.scrollTop
          const result = BossExtract.scrollResultsContainer(document)
          return {
            ok: result.ok,
            before,
            after: box.scrollTop,
            end: box.scrollHeight - box.clientHeight,
          }
        }"""
    )
    assert reached["ok"] is True
    assert reached["before"] == 0
    # The whole point: one round ends at the bottom, where the loader lives.
    assert reached["after"] == reached["end"] > 0


@pytest.mark.asyncio
async def test_a_scroll_reports_whether_the_browser_painted_since_the_last_one(
    browser_page, extension_bundle
):
    """A tab Chrome has stopped painting still scrolls - and loads nothing.

    Measured in a real Chrome tab on 2026-09-06: with the tab backgrounded,
    `scrollTop` advanced 700 -> 1600 while the frame counter, the scroll-event
    counter and the `IntersectionObserver` counter all stayed put and the list
    stayed at its first batch. BOSS loads from those callbacks, so a
    backgrounded search reported fifteen cards per direction and called the
    list finished.

    One `requestAnimationFrame` per scroll step tells the two apart. The first
    scroll has nothing to compare against and reports `undefined`.
    """
    await _load_fixture(
        browser_page, extension_bundle, "boss_search_tall_list.html", url=SEARCH_URL
    )
    painting = await browser_page.evaluate(
        """async () => {
          const first = BossExtract.scrollResultsContainer(document)
          await new Promise(r => requestAnimationFrame(() => r()))
          const second = BossExtract.scrollResultsContainer(document)
          return { first: first.rendered, second: second.rendered }
        }"""
    )
    assert painting["first"] is None, "the first scroll has no previous frame to judge"
    assert painting["second"] is True, "a painting tab reports rendered"

    frozen = await browser_page.evaluate(
        """async () => {
          // Let the frame the previous scroll armed land first; replacing
          // `requestAnimationFrame` does not cancel an already-queued callback.
          await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))
          // Exactly what a non-rendering tab does: script runs, the callback
          // never arrives. Nothing else about the page changes.
          const real = window.requestAnimationFrame
          window.requestAnimationFrame = () => 0
          BossExtract.scrollResultsContainer(document)
          await new Promise(r => setTimeout(r, 50))
          const next = BossExtract.scrollResultsContainer(document)
          window.requestAnimationFrame = real
          const box = document.querySelector('.job-list-box')
          return { rendered: next.rendered, ok: next.ok, scrollTop: box.scrollTop }
        }"""
    )
    assert frozen["ok"] is True, "the scroll itself still succeeds - that is the trap"
    assert frozen["scrollTop"] > 0, "and scrollTop still moves"
    assert frozen["rendered"] is False, "but nothing was painted, so nothing can load"


@pytest.mark.asyncio
async def test_the_frame_probe_arms_exactly_one_callback_per_scroll(
    browser_page, extension_bundle
):
    """Bounded, single-shot - never a poll loop. CLAUDE.md M4f forbids
    unbounded polling, and a frame counter is the obvious way to write this
    wrong."""
    await _load_fixture(
        browser_page, extension_bundle, "boss_search_tall_list.html", url=SEARCH_URL
    )
    armed = await browser_page.evaluate(
        """async () => {
          const real = window.requestAnimationFrame
          let calls = 0
          window.requestAnimationFrame = cb => { calls += 1; return real(cb) }
          BossExtract.scrollResultsContainer(document)
          BossExtract.scrollResultsContainer(document)
          BossExtract.scrollResultsContainer(document)
          await new Promise(r => setTimeout(r, 80))
          window.requestAnimationFrame = real
          return calls
        }"""
    )
    assert armed == 3, "one callback per scroll step, and no self-rescheduling loop"


@pytest.mark.asyncio
async def test_a_list_container_that_does_not_scroll_moves_the_page_instead(
    browser_page, extension_bundle
):
    """`scrollBy` on a non-scrolling container is a silent no-op.

    No error, no movement, no lazy load - and that is what pinned eleven of
    sixteen directions to BOSS's first 15 cards on 2026-09-05 while two others
    reached 60. The two that worked were the ones that opened many details:
    clicking a card low in the list makes the browser scroll it into view,
    which loaded the next batch by accident rather than by design.
    """
    await _load_fixture(
        browser_page, extension_bundle, "boss_search_page_scroller.html", url=SEARCH_URL
    )
    moved = await browser_page.evaluate(
        """() => {
          const box = document.querySelector('.job-list-box')
          const doc = document.scrollingElement
          const before = doc.scrollTop
          const result = BossExtract.scrollResultsContainer(document)
          return {
            ok: result.ok,
            before,
            after: doc.scrollTop,
            end: doc.scrollHeight - doc.clientHeight,
            boxScrolled: box.scrollTop,
          }
        }"""
    )
    assert moved["ok"] is True
    assert moved["before"] == 0
    assert moved["after"] == moved["end"] > 0, "the page reached the end of the list"
    assert moved["boxScrolled"] == 0, "the container itself never scrolls - it cannot"
