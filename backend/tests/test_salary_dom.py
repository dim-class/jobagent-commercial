"""Real DOM and local synthetic salary screenshots. All requests fulfilled locally."""
import base64
import os
from pathlib import Path

import pytest

from app.services.salary_ocr import recognize_salary

ROOT = Path(__file__).resolve().parents[2]
SEARCH = 'https://www.zhipin.com/web/geek/jobs'
DETAIL = 'https://www.zhipin.com/job_detail/ocr-test.html'


async def load(page, html, url=SEARCH):
    await page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=html))
    await page.goto(url)
    for path in ['boss/selectors.js', 'boss/extract.js']:
        await page.add_script_tag(content=(ROOT / 'extension/dist' / path).read_text(encoding='utf-8'))


def card(id='ocr-test', salary='\ue039-\ue032\ue033K'):
    return f'<li class="job-card-wrapper"><a class="job-name" href="/job_detail/{id}.html">同名工程师</a><span class="salary">{salary}</span><span class="company-name">公司</span></li>'


async def frame(page, url=DETAIL):
    return await page.evaluate('(url) => BossExtract.salaryFrame(document, location.href, url, "同名工程师")', url)


@pytest.mark.asyncio
async def test_fallback_stays_within_same_card(browser_page):
    await load(browser_page, card().replace('</li>', '<span class="job-salary">8-13K</span></li>') + card('other', '99-100K'))
    result = await browser_page.evaluate('BossExtract.detect(document, location.href)')
    assert result['candidates'][0]['salary_text'] == '8-13K'
    assert result['candidates'][1]['salary_text'] == '99-100K'


@pytest.mark.asyncio
async def test_hidden_and_conflicting_text_are_not_selected(browser_page):
    await load(browser_page, card().replace('</li>', '<span class="job-salary" style="display:none">88-99K</span></li>'))
    assert (await browser_page.evaluate('BossExtract.detect(document, location.href)'))['candidates'][0]['salary_text'] is None
    await browser_page.locator('.job-salary').evaluate('(e) => e.style.display="inline"')
    await browser_page.locator('.salary').evaluate('(e) => e.textContent="8-13K"')
    assert (await browser_page.evaluate('BossExtract.detect(document, location.href)'))['candidates'][0]['salary_text'] is None


@pytest.mark.asyncio
async def test_detail_salary_never_leaks_from_other_card(browser_page):
    await load(browser_page, '<div class="job-banner"><div class="name"><h1>同名工程师</h1></div><span class="salary">\ue039-\ue032\ue033K</span><span class="salary-text">8-13K</span></div>' + card('other', '99-100K'), DETAIL)
    result = await browser_page.evaluate('BossExtract.detect(document, location.href)')
    assert result['candidates'][0]['salary_text'] == '8-13K'
    await browser_page.locator('.salary-text').evaluate('(e) => e.remove()')
    assert (await browser_page.evaluate('BossExtract.detect(document, location.href)'))['candidates'][0]['salary_text'] is None


@pytest.mark.asyncio
async def test_frame_proves_exact_url_not_same_title(browser_page):
    await load(browser_page, card() + card('other', '15-25K'))
    first = await frame(browser_page)
    assert first['status'] == 'ok'
    assert first['frame']['canonicalUrl'] == DETAIL and first['frame']['raw'].endswith('K')
    second = await frame(browser_page, DETAIL.replace('ocr-test', 'other'))
    assert second['status'] == 'ok' and second['frame']['raw'] == '15-25K'
    assert first['frame']['nodeId'] != second['frame']['nodeId']


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', [
    "document.querySelector('.salary').style.display='none'",
    "document.querySelector('.salary').style.marginLeft='2000px'",
    "document.querySelector('.job-name').href='/job_detail/changed.html'",
    "document.querySelector('.salary').style.cssText='display:inline-block;width:5px;overflow:hidden'",
    "document.body.insertAdjacentHTML('beforeend','<div style=\"position:fixed;inset:0;z-index:999;background:white\"></div>')",
])
async def test_unsafe_region_refused(browser_page, mutation):
    await load(browser_page, card())
    assert (await frame(browser_page))['status'] == 'ok'
    await browser_page.evaluate(mutation)
    assert (await frame(browser_page))['status'] != 'ok'


@pytest.mark.asyncio
async def test_ambiguous_identity_and_verification_refused(browser_page):
    await load(browser_page, card() + card())
    assert (await frame(browser_page))['status'] == 'ambiguous'
    await load(browser_page, '<div class="verify-wrap">请完成验证</div>')
    result = await frame(browser_page)
    assert result['status'] != 'ok'


@pytest.mark.asyncio
@pytest.mark.parametrize('hidden', [False, True])
async def test_late_verification_container_is_detected_without_hidden_template_false_positive(browser_page, hidden):
    # Regression found by the real MV3 E2E: verification appended after a long
    # listing/JD must not fall outside the old first-400-character text check.
    style = 'display:none' if hidden else ''
    await load(browser_page, '<p>' + 'ordinary fixture text ' * 60 + '</p>' + card() +
               f'<div class="verify-wrapper" style="{style}">请完成安全验证</div>')
    result = await browser_page.evaluate('BossExtract.detect(document, location.href)')
    assert result['verification'] is (not hidden)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != 'nt', reason='Real built-in Windows OCR')
@pytest.mark.parametrize('color', ['#ff5a40', '#999999', '#000000'])
async def test_real_local_ocr_colored_salary(browser_page, color):
    await load(browser_page, f'<span style="display:inline-block;background:white;color:{color};font:24px Arial;padding:5px">8-13K</span>')
    image = await browser_page.locator('span').screenshot()
    result = recognize_salary(base64.b64encode(image).decode())
    assert result['salary_text'] == '8-13K', result
