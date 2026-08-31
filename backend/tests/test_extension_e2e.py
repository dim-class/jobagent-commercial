"""Actual unpacked MV3, fixture-only Chromium and the isolated conftest database.

No installed Chrome/profile, live recruitment traffic or production :8000 writes.
A non-forwarding proxy is the backstop before Playwright routes are installed.
Chrome APIs and the production manifest are NOT replaced by test doubles.
Opt-in only: the ordinary backend suite must not start this experimental harness.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import http.client
import json
import mimetypes
import os
import re
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
EXTENSION = ROOT / "extension"
SEARCH = "https://www.zhipin.com/web/geek/jobs"
DETAIL = "https://www.zhipin.com/job_detail/e2e-one.html"
pytestmark = pytest.mark.skipif(
    os.environ.get("JOBAGENT_MV3_E2E") != "1",
    reason="Opt-in isolated MV3 harness: scripts/test-extension-e2e.ps1",
)


def extension_hashes():
    return {str(p.relative_to(EXTENSION)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in (EXTENSION / "src", EXTENSION / "dist")
            for p in folder.rglob("*") if p.is_file()} | {
                "manifest": hashlib.sha256((EXTENSION / "manifest.json").read_bytes()).hexdigest()}


class DenyProxy(BaseHTTPRequestHandler):
    def do_CONNECT(self):
        try:
            self.send_error(403)
        except ConnectionError:
            pass

    def do_GET(self):
        url = urlsplit(self.path)
        harness = self.server.harness
        if not harness.allowed_api(url):
            self.do_CONNECT()
            return
        length = int(self.headers.get("Content-Length", 0))
        if length > 360000:
            self.send_error(413)
            return
        # Preserve only the narrow quote's numeric cap, never recruitment query tokens.
        path = url.path
        if path.endswith('/auto-match/quote') and re.fullmatch(r'cap=[1-3]', url.query):
            path += '?' + url.query
        response = harness.api_response(self.command, path, self.rfile.read(length), self.headers)
        self.send_response(response.status_code)
        for key, value in response.headers.items():
            if key.lower() not in ("content-length", "content-encoding", "transfer-encoding", "connection"):
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(response.content)))
        self.end_headers()
        try:
            self.wfile.write(response.content)
        except ConnectionError:
            pass

    do_POST = do_GET
    do_OPTIONS = do_GET

    def log_message(self, *args):
        pass  # Never log URLs/headers or payloads.


class Harness:
    def __init__(self, client):
        self.client = client
        self.api_calls = []
        self.blocked = []
        self.gate_path = None
        self.gate_entered = threading.Event()
        self.gate_release = threading.Event()
        self.crop_dimensions = []
        self.console_ui = False
        self.console_failure = None
        self.html = (ROOT / "backend/tests/fixtures/boss_job_standard.html").read_text("utf-8")

    @staticmethod
    def allowed_api(url):
        return url.scheme == "http" and url.netloc in ("127.0.0.1:8000", "localhost:8000") and bool(re.fullmatch(
            r"/api/(?:tasks(?:/search-plan(?:/(?:generate|\d+))?|/\d+/(?:candidates|auto-match/(?:quote|step|review)|run/(?:start|pause|resume|cancel|complete|fail|verification|round|state)))?"
            r"|extension/(?:salary-ocr|jobs/(?:preview|import)|sessions(?:/active|/\d+/(?:stop|navigate/(?:prepare|confirm)))?))", url.path))

    def api_response(self, method, path, body, headers):
        if path == self.gate_path:
            self.gate_entered.set()
            assert self.gate_release.wait(12), "Test must release its bounded response gate"
        if path == '/api/extension/salary-ocr' and method == 'POST':
            payload = json.loads(body)
            assert set(payload) == {'image'}
            crop = base64.b64decode(payload['image'], validate=True)
            assert crop[:8] == b'\x89PNG\r\n\x1a\n'
            width, height = struct.unpack('>II', crop[16:24])
            assert width <= 800 and height <= 160 and len(crop) <= 256 * 1024
            self.crop_dimensions.append((width, height))
        response = self.client.request(method, path, content=body,
            headers={k: v for k, v in headers.items() if k.lower() in ("content-type", "origin")})
        self.api_calls.append((method, path, response.status_code))
        return response

    async def route(self, route):
        request = route.request
        url = urlsplit(request.url)
        if self.console_ui and url.scheme == 'http' and url.netloc == '127.0.0.1:5173':
            if ((self.console_failure == 'backend_down' and (url.path.startswith('/api/') or url.path == '/health'))
                or (self.console_failure == 'plan_error' and url.path == '/api/tasks/search-plan')):
                await route.fulfill(status=503, content_type='application/json', body='{"message":"fixture unavailable"}')
                return
            if url.path.startswith('/api/') or url.path == '/health':
                # Actual React UI, isolated TestClient only; NEVER production :5173.
                response = await asyncio.to_thread(self.api_response, request.method,
                    url.path + ('?' + url.query if url.query else ''), request.post_data_buffer or b'', request.headers)
                await route.fulfill(status=response.status_code, content_type='application/json', body=response.content)
                return
            root = (ROOT / 'frontend/dist').resolve()
            path = (root / ('index.html' if url.path == '/' else url.path.lstrip('/'))).resolve()
            if path.is_relative_to(root) and path.is_file():
                await route.fulfill(status=200, content_type=mimetypes.guess_type(str(path))[0] or 'application/octet-stream', body=path.read_bytes())
                return
        if url.scheme == "https" and url.netloc == "www.zhipin.com":
            if request.resource_type == "document" and (
                url.path == "/web/geek/jobs" or url.path.startswith("/job_detail/")
            ):
                await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=self.html)
                return
        elif self.allowed_api(url):
            # Browser HTTP goes to our loopback proxy -> in-process TestClient.
            # No forwarding socket is ever opened, including to production :8000.
            await route.continue_()
            return
        self.blocked.append((url.scheme, url.hostname, url.path))
        await route.abort("blockedbyclient")

    async def content(self, message):
        return await self.worker.evaluate(
            "async ({id, message}) => chrome.tabs.sendMessage(id, message)",
            {"id": self.tab_id, "message": message},
        )

    async def wait_task(self, task_id, states, timeout=40):
        try:
            async with asyncio.timeout(timeout):
                while True:
                    task = self.client.get(f"/api/tasks/search-plan/{task_id}").json()
                    if task["run_status"] in states:
                        # Terminal transition and fire-and-forget telemetry are separate
                        # real HTTP requests. Wait for the in-flight report, not a fake state.
                        await asyncio.sleep(.3)
                        task = self.client.get(f"/api/tasks/search-plan/{task_id}").json()
                        return task
                    await asyncio.sleep(.1)
        except TimeoutError:
            pytest.fail(f"Task deadline: state={task['run_status']}, action={task['last_action']}, error={task['last_error']}")

    async def wait_gate(self):
        try:
            async with asyncio.timeout(10):
                while not self.gate_entered.is_set():
                    await asyncio.sleep(.05)
        except TimeoutError:
            status = await self.last_popup.evaluate("document.querySelector('#runner-status').textContent")
            pytest.fail(f"Request gate not reached: {status}; recent API={self.api_calls[-8:]}")

    async def wait_popup_closed(self):
        try:
            async with asyncio.timeout(5):
                while await self.worker.evaluate("chrome.runtime.getContexts({contextTypes:['POPUP']})"):
                    await asyncio.sleep(.1)
        except TimeoutError:
            status = await self.last_popup.evaluate("document.querySelector('#runner-status').textContent")
            tabs = await self.worker.evaluate("""async()=>({
                current:await chrome.tabs.query({active:true,currentWindow:true}),
                windows:await chrome.windows.getAll()})""")
            pytest.fail(f"Popup did not close: {status}; fixture tabs={tabs}; recent API={self.api_calls[-6:]}")

    async def start(self, cap=1, hold_popup=False, auto_match=False, task_id=None):
        popup = await self.popup()
        if task_id is None:
            await popup.wait("document.querySelector('#runner-task').options.length === 1")
        else:
            await popup.wait(f"[...document.querySelector('#runner-task').options].some(o => o.value === '{task_id}')")
            await popup.evaluate(f"""const select = document.querySelector('#runner-task');
                select.value = '{task_id}'; select.dispatchEvent(new Event('change'));""")
        await popup.wait("document.querySelector('#runner-counters').textContent.includes('task_id')")
        await popup.evaluate(f"document.querySelector('#runner-candidate-cap').value='{cap}'")
        if auto_match:
            # Simulated explicit cost approval in isolated fixture popup only.
            # The real quote/approval/claim routes still execute against the test DB.
            await popup.evaluate("""document.querySelector('#runner-auto-match').checked = true;
                window.confirm = text => text.includes('test-model-fast') && text.includes('次付费调用');""")
        # Resolve the user's target in the real popup. An out-of-band debugger
        # evaluation in a service worker has no caller window and is NOT the
        # runtime.onMessage context used by the product's start handler.
        try:
            async with asyncio.timeout(5):
                while True:
                    tabs = await popup.evaluate("chrome.tabs.query({active:true,currentWindow:true})")
                    if len(tabs) == 1 and tabs[0].get('id') == self.tab_id and tabs[0].get('url') == self.page.url:
                        break
                    await asyncio.sleep(.1)
        except TimeoutError:
            detail = await self.worker.evaluate("""async()=>({
                all:await chrome.tabs.query({}), last:await chrome.tabs.query({active:true,lastFocusedWindow:true}),
                windows:await chrome.windows.getAll(), current:await chrome.windows.getCurrent(),
                lastNormal:await chrome.windows.getLastFocused({windowTypes:['normal']})})""")
            detail['popup'] = await popup.evaluate("""(async()=>({
                current:await chrome.windows.getCurrent(),
                tabs:await chrome.tabs.query({active:true,currentWindow:true})}))()""")
            pytest.fail(f"Fixture foreground precondition unavailable: {detail}")
        if hold_popup:
            # Fault injection inside this isolated native popup only. Actual
            # runtime.getContexts still sees it, even if Chrome says focused=true.
            await popup.evaluate('window.close = () => {}')
        await popup.click('#runner-start')
        return popup

    def create_task(self, keyword="E2E"):
        response = self.client.post("/api/tasks/search-plan/generate", json={"cities": ["北京"], "keywords": [keyword]})
        assert response.status_code == 200
        return self.client.get("/api/tasks/search-plan").json()["items"][-1]["id"]

    def search_fixture(self, scenario="normal", salary_mode="opaque"):
        self.html = (ROOT / "backend/tests/fixtures/mv3_runner.html").read_text("utf-8").replace(
            'const scenario = "normal";', f'const scenario = "{scenario}";').replace(
            'const salaryMode = "opaque";', f'const salaryMode = "{salary_mode}";')

    async def popup(self, grant=True):
        # Arrange a foreground TEST window before simulating toolbar invocation.
        # These are actual Chrome APIs, not overridden focus/tab getters. Nothing
        # re-focuses a window during the runner or its negative safety cases.
        await self.page.bring_to_front()
        await self.worker.evaluate("""async id => {
            const tab = await chrome.tabs.get(id);
            await chrome.windows.update(tab.windowId, {focused:true});
            await chrome.tabs.update(id, {active:true});
        }""", self.tab_id)
        cdp = await self.context.browser.new_browser_cdp_session()
        if grant:
            targets = (await cdp.send("Target.getTargets", {"filter": [{"type": "tab", "exclude": False}]}))["targetInfos"]
            target = next(t for t in targets if t["url"] == self.page.url and t["type"] == "tab")
            await cdp.send("Extensions.triggerAction", {"id": self.extension_id, "targetId": target["targetId"]})
        else:
            await self.worker.evaluate("chrome.action.openPopup()")
        for _ in range(40):
            targets = (await cdp.send("Target.getTargets"))["targetInfos"]
            found = [t for t in targets if t["url"] == f"chrome-extension://{self.extension_id}/popup.html"]
            if found:
                break
            await asyncio.sleep(.1)
        assert len(found) == 1, "Must attach actual action popup, never a popup.html tab"
        attached = await cdp.send("Target.attachToTarget", {"targetId": found[0]["targetId"], "flatten": False})
        await self.worker.evaluate("""async id => {
            const tab = await chrome.tabs.get(id);
            await chrome.windows.update(tab.windowId, {focused:true});
        }""", self.tab_id)
        self.last_popup = Popup(cdp, attached["sessionId"])
        return self.last_popup


class Popup:
    """CDP adapter only for the native action popup Playwright does not list as a Page."""
    def __init__(self, cdp, session_id):
        self.cdp, self.session_id = cdp, session_id
        self.pending = {}
        self.sequence = 0
        cdp.on("Target.receivedMessageFromTarget", self.receive)

    def receive(self, event):
        if event.get("sessionId") != self.session_id:
            return
        message = json.loads(event["message"])
        future = self.pending.get(message.get("id"))
        if future and not future.done():
            future.set_result(message)

    async def send(self, method, params=None):
        self.sequence += 1
        sequence = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[sequence] = future
        try:
            await self.cdp.send("Target.sendMessageToTarget", {"sessionId": self.session_id,
                "message": json.dumps({"id": sequence, "method": method, "params": params or {}})})
            result = await asyncio.wait_for(future, 10)
            assert "error" not in result, result.get("error")
            return result["result"]
        finally:
            self.pending.pop(sequence, None)

    async def evaluate(self, expression):
        result = await self.send("Runtime.evaluate", {"expression": expression, "awaitPromise": True,
                                                      "returnByValue": True})
        assert "exceptionDetails" not in result, result.get("exceptionDetails")
        return result["result"].get("value")

    async def wait(self, expression, timeout=10):
        async with asyncio.timeout(timeout):
            while not await self.evaluate(expression):
                await asyncio.sleep(.1)

    async def click(self, selector):
        selector_json = json.dumps(selector)
        await self.wait(f"!!document.querySelector({selector_json}) && !document.querySelector({selector_json}).disabled")
        # Activate the real popup's DOM control/handler. Headless native popup mouse
        # coordinates are not a reliable Windows desktop-input/focus test. This is
        # NOT the activeTab grant: only Extensions.triggerAction supplies that above.
        await self.evaluate(f"document.querySelector({selector_json}).click()")


@pytest_asyncio.fixture
async def mv3(client, tmp_path, request):
    before = extension_hashes()
    harness = Harness(client)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), DenyProxy)
    proxy.harness = harness
    harness.proxy_port = proxy.server_port
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                str(tmp_path / "chromium-profile"), channel="chromium",
                headless=os.environ.get('JOBAGENT_E2E_HEADED') != '1',
                args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}",
                      "--proxy-bypass-list=<-loopback>"],
                proxy={"server": f"http://127.0.0.1:{proxy.server_port}"},
                viewport={"width": 1280, "height": 900}, timeout=20000,
            )
            try:
                await context.route("**/*", harness.route)
                harness.context = context
                harness.chromium_version = context.browser.version
                request.node.user_properties.extend([
                    ('chromium_version', harness.chromium_version),
                    ('mode', 'headed' if os.environ.get('JOBAGENT_E2E_HEADED') == '1' else 'headless'),
                    ('live_site', 'false'),
                ])
                harness.worker = (context.service_workers or [await context.wait_for_event("serviceworker")])[0]
                harness.extension_id = urlsplit(harness.worker.url).netloc
                harness.page = context.pages[0]
                await harness.page.goto(DETAIL)
                harness.tab_id = await harness.worker.evaluate(
                    "async () => (await chrome.tabs.query({active:true, currentWindow:true}))[0].id")
                for _ in range(30):
                    try:
                        if (await harness.content({"type": "jobagent:ping"}))["ready"]:
                            break
                    except Exception:
                        await asyncio.sleep(.1)
                else:
                    pytest.fail("Real MV3 content script did not initialize")
                yield harness
            finally:
                await context.close()
    finally:
        harness.gate_release.set()
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=2)
        assert extension_hashes() == before, "E2E must not modify extension code/manifest"


@pytest.mark.asyncio
async def test_real_mv3_boot_and_content_transport(mv3):
    result = await mv3.content({"type": "jobagent:detect"})
    assert result["ok"] and result["result"]["page_type"] == "detail"
    assert len(result["result"]["candidates"]) == 1
    assert result["result"]["candidates"][0]["source_url"] == DETAIL
    assert await mv3.page.evaluate("typeof BossExtract") == "undefined", "Must be isolated-world injection"
    manifest = await mv3.worker.evaluate("chrome.runtime.getManifest()")
    assert manifest["manifest_version"] == 3
    assert manifest["permissions"] == ["activeTab", "scripting", "storage"]


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['normal', 'verification'])
async def test_console_ui_starts_free_collection_without_popup(mv3, db, scenario):
    """Real built React -> real content bridge -> real MV3 -> fixture intake."""
    from app.models import Job, JobAnalysis, TaskCandidate
    from playwright.async_api import expect
    mv3.console_ui = True
    mv3.search_fixture(scenario=scenario, salary_mode='plain')
    ui = mv3.page
    await ui.goto('http://127.0.0.1:5173/#/console')
    await mv3.worker.evaluate('async id => { const t=await chrome.tabs.get(id); await chrome.windows.update(t.windowId,{focused:true}); }', mv3.tab_id)
    await ui.get_by_role('button', name='刷新状态', exact=True).click()
    await expect(ui.get_by_text('连接：JobAgent 扩展已连接', exact=False)).to_be_visible()
    version = json.loads((EXTENSION / 'manifest.json').read_text(encoding='utf-8'))['version']
    await expect(ui.get_by_text(f'版本 {version} · 协议 1 · 免费采集能力已就绪', exact=False)).to_be_visible()
    await expect(ui.get_by_text('本机服务：后端和数据库正常。', exact=False)).to_be_visible()
    assert not any('/run/start' in path for _, path, _ in mv3.api_calls)
    await ui.get_by_role('button', name='准备搜索计划（不执行）').click()
    await expect(ui.get_by_text('搜索计划已准备；', exact=False)).to_be_visible()
    task_id = mv3.client.get('/api/tasks/search-plan').json()['items'][0]['id']
    assert not any('/run/start' in path for _, path, _ in mv3.api_calls)
    native_dialogs = []
    ui.on('dialog', lambda dialog: native_dialogs.append(dialog.type))
    await ui.get_by_role('button', name='开始免费采集', exact=True).click()
    confirmation = ui.get_by_role('dialog', name='确认免费采集')
    await expect(confirmation).to_contain_text('最多 3 个候选尝试、5 次滚动')
    assert len(mv3.context.pages) == 1
    assert not any('/run/start' in path for _, path, _ in mv3.api_calls)
    await confirmation.get_by_role('button', name='暂不执行').click()
    await expect(confirmation).not_to_be_visible()
    await ui.get_by_role('button', name='开始免费采集', exact=True).click()
    await ui.keyboard.press('Escape')
    await expect(confirmation).not_to_be_visible()
    assert not native_dialogs
    assert not any('/run/start' in path for _, path, _ in mv3.api_calls)
    await ui.get_by_role('button', name='开始免费采集', exact=True).click()
    await ui.evaluate("window.consoleReplies=[]; window.addEventListener('message', e=>{if(e.data?.channel==='jobagent-console-response')window.consoleReplies.push(e.data.result)})")
    async with mv3.context.expect_page() as created:
        await confirmation.get_by_role('button', name='确认开始免费采集', exact=True).click()
    boss = await created.value
    await ui.wait_for_function('window.consoleReplies.length > 0')
    replies = await ui.evaluate('window.consoleReplies')
    assert replies[0]['ok'], replies
    outcome = await mv3.wait_task(task_id, {'completed', 'failed', 'paused_verification'})
    assert outcome['state'] == ('completed' if scenario == 'normal' else 'paused_verification'), outcome
    assert outcome['imported_jobs'] == (3 if scenario == 'normal' else 0)
    assert outcome['scroll_round'] == (2 if scenario == 'normal' else 0)
    assert boss.url.startswith(SEARCH)
    assert len(mv3.context.pages) == 2
    assert not native_dialogs
    assert sum('/run/start' in path for _, path, _ in mv3.api_calls) == 1
    assert not await mv3.worker.evaluate("chrome.runtime.getContexts({contextTypes:['POPUP']})")
    # Verification fixture appears AFTER the first card opens, then must stop.
    assert await boss.evaluate('window.fixtureClicks') == ([1, 2, 3] if scenario == 'normal' else [1])
    db.expire_all()
    assert db.query(Job).count() == db.query(TaskCandidate).count() == (3 if scenario == 'normal' else 0)
    assert db.query(JobAnalysis).count() == 0
    assert not any('/auto-match/step' in path or '/auto-match/quote' in path for _, path, _ in mv3.api_calls)
    if scenario == 'verification':
        # A verification PAUSE retains the bounded session for explicit recovery.
        # The console can still cancel without revisiting the extension popup.
        pointer = await mv3.worker.evaluate("async()=> (await chrome.storage.session.get('jobagent_runner_pointer')).jobagent_runner_pointer")
        assert pointer['pauseRequested'] and pointer['pausedReason'] == 'verification'
        await ui.bring_to_front()
        await ui.get_by_role('button', name='刷新状态', exact=True).click()
        await ui.get_by_role('button', name='取消', exact=True).click()
        await mv3.wait_task(task_id, {'cancelled'})
    assert mv3.client.get('/api/extension/sessions/active').json() is None


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['state', 'keyword'])
async def test_console_confirmation_rejects_changed_task(mv3, db, changed):
    """A confirmation binds the shown task, not whatever a refresh replaces it with."""
    from app.models import Job, JobAnalysis, JobSearchTask
    from app.models.enums import SearchTaskRunStatus
    from playwright.async_api import expect
    mv3.console_ui = True
    task_id = mv3.create_task()
    ui = mv3.page
    await ui.goto('http://127.0.0.1:5173/#/console')
    await expect(ui.get_by_text('连接：JobAgent 扩展已连接', exact=False)).to_be_visible()
    await ui.get_by_role('combobox', name='选择搜索任务', exact=True).select_option(str(task_id))
    await ui.get_by_role('button', name='开始免费采集', exact=True).click()
    confirmation = ui.get_by_role('dialog', name='确认免费采集')
    await expect(confirmation).to_be_visible()
    # Isolated fixture DB only. A new status read must invalidate the old approval.
    task = db.get(JobSearchTask, task_id)
    if changed == 'state':
        task.run_status = SearchTaskRunStatus.failed
    else:
        task.keywords = 'different-fixture-keyword'
    db.commit()
    await ui.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    await expect(ui.get_by_role('button', name='确认开始免费采集', exact=True)).to_be_enabled()
    await confirmation.get_by_role('button', name='确认开始免费采集', exact=True).click()
    await expect(ui.get_by_role('alert')).to_contain_text('任务或运行状态已变化')
    assert len(mv3.context.pages) == 1
    assert not any('/run/start' in path for _, path, _ in mv3.api_calls)
    assert db.query(Job).count() == db.query(JobAnalysis).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,expected', [
    ('backend_down', '健康检查失败或超时'),
    ('plan_error', '任务 API 不可用'),
    ('bridge_absent', 'bridge_no_response'),
    ('worker_timeout', 'worker_timeout'),
    ('legacy_response', '旧版扩展已响应'),
])
async def test_console_connection_diagnostics_fail_closed(mv3, db, failure, expected):
    """Fault-injected transport/API; actual UI and MV3. Never real Chrome/BOSS."""
    from app.models import Job, JobAnalysis
    from playwright.async_api import expect
    mv3.console_ui = True
    mv3.console_failure = failure
    task_id = mv3.create_task()
    # Simulate a missing/stale transport at the document boundary without changing
    # installed extension source or Chrome APIs. No mutation command is issued.
    if failure in ('bridge_absent', 'worker_timeout', 'legacy_response'):
        await mv3.context.add_init_script("""(() => {
          const failure = %s;
          window.addEventListener('message', e => {
            if (e.source !== window || e.origin !== location.origin) return;
            const d = e.data;
            if (d?.channel === 'jobagent-console-receipt' && failure === 'bridge_absent') e.stopImmediatePropagation();
            if (d?.channel !== 'jobagent-console-response') return;
            if (failure === 'bridge_absent' || failure === 'worker_timeout') e.stopImmediatePropagation();
            if (failure === 'legacy_response' && d.result?.extensionVersion) {
              e.stopImmediatePropagation();
              window.postMessage({channel:d.channel, id:d.id, result:{ok:true, protocol:1, runner:null}}, location.origin);
            }
          });
        })();""" % json.dumps(failure))
    ui = mv3.page
    await ui.goto('http://127.0.0.1:5173/#/console')
    await expect(ui.get_by_text(expected, exact=False)).to_be_visible(timeout=10000)
    if failure != 'backend_down' and failure != 'plan_error':
        await ui.get_by_role('combobox', name='选择搜索任务', exact=True).select_option(str(task_id))
    await expect(ui.get_by_role('button', name='开始免费采集', exact=True)).to_be_disabled()
    await expect(ui.get_by_role('button', name='恢复免费采集', exact=True)).to_be_disabled()
    assert len(mv3.context.pages) == 1
    assert not any(method == 'POST' for method, _, _ in mv3.api_calls)
    assert mv3.client.get(f'/api/tasks/search-plan/{task_id}').json()['state'] == 'pending'
    assert mv3.client.get('/api/extension/sessions/active').json() is None
    db.expire_all()
    assert db.query(Job).count() == db.query(JobAnalysis).count() == 0


@pytest.mark.asyncio
async def test_real_toolbar_action_grants_capture_permission(mv3):
    popup = await mv3.popup()
    await popup.wait("document.readyState === 'complete'")
    contexts = await mv3.worker.evaluate("chrome.runtime.getContexts({contextTypes:['POPUP']})")
    assert len(contexts) == 1 and contexts[0]["tabId"] == -1
    assert len(mv3.context.pages) == 1  # not a popup.html normal tab
    assert await mv3.worker.evaluate("""async id => {
        const tab = await chrome.tabs.get(id);
        return (await chrome.tabs.captureVisibleTab(tab.windowId)).startsWith('data:image/');
    }""", mv3.tab_id)
    assert await popup.evaluate("typeof chrome.runtime.sendMessage") == "function"


@pytest.mark.asyncio
async def test_programmatic_open_popup_does_not_fake_active_tab_grant(mv3):
    await mv3.popup(grant=False)
    error = await mv3.worker.evaluate("""async id => {
        const tab = await chrome.tabs.get(id);
        try { await chrome.tabs.captureVisibleTab(tab.windowId); return null; }
        catch (e) { return e.message; }
    }""", mv3.tab_id)
    assert "activeTab" in error


@pytest.mark.asyncio
async def test_popup_start_to_real_local_ocr_and_canonical_intake(mv3, db):
    from app.models import Job, ApplicationEvent
    task_id = mv3.create_task()
    mv3.search_fixture()
    await mv3.start()
    await mv3.wait_popup_closed()
    task = await mv3.wait_task(task_id, {"completed", "failed", "paused_verification"})
    assert task["run_status"] == "completed", task.get("last_error")
    assert task["imported_jobs"] == 1, {k: task[k] for k in ('run_status', 'last_action', 'last_error', 'current_candidate')}
    db.expire_all()
    jobs = db.query(Job).all()
    assert len(jobs) == 1
    assert jobs[0].salary_text == "8-13K"
    assert jobs[0].external_id == "e2e-1"
    assert jobs[0].raw_description and "Linux" in jobs[0].raw_description
    events = db.query(ApplicationEvent).all()
    assert any("OCR" in (e.notes or "") and "人工核对" in (e.notes or "") for e in events)
    assert any(path == '/api/extension/salary-ocr' and code == 200 for _, path, code in mv3.api_calls)
    assert len(mv3.crop_dimensions) == 1
    assert await mv3.page.evaluate('window.fixtureClicks') == [1]


@pytest.mark.asyncio
async def test_one_start_collects_three_then_matches_for_human_review(mv3, db, active_resume, monkeypatch):
    from tests.test_analysis import build_result
    from app.models import Job
    calls = []
    async def mock_model(**kwargs):
        calls.append(kwargs)
        return build_result(risk_flags=['[演示数据] 薪资币种需要人工确认'])
    monkeypatch.setattr('app.services.job_matcher.run_job_match', mock_model)
    task_id = mv3.create_task()
    mv3.search_fixture(salary_mode='plain')
    await mv3.start(cap=3, auto_match=True)
    await mv3.wait_popup_closed()
    final = await mv3.wait_task(task_id, {'completed', 'failed', 'paused_verification'})
    assert final['state'] == 'completed', final['last_error']
    assert final['imported_jobs'] == 3 and len(calls) == 3
    assert all(c['no_retries'] for c in calls)
    review = mv3.client.get(f'/api/tasks/{task_id}/auto-match/review').json()
    assert review['used'] == review['completed'] == 3
    assert all(item['bucket'] == '待确认' for item in review['items'])
    db.expire_all()
    assert all(job.status.value == 'new' for job in db.query(Job).all())
    assert sum(path.endswith('/auto-match/step') for _, path, _ in mv3.api_calls) == 3


@pytest.mark.asyncio
async def test_popup_waits_for_real_backend_ack(mv3):
    task_id = mv3.create_task()
    mv3.search_fixture(salary_mode='plain')
    mv3.gate_path = f'/api/tasks/{task_id}/run/start'
    await mv3.start()
    await mv3.wait_gate()
    assert len(await mv3.worker.evaluate("chrome.runtime.getContexts({contextTypes:['POPUP']})")) == 1
    assert mv3.page.url == DETAIL
    mv3.gate_release.set()
    await mv3.wait_popup_closed()
    task = await mv3.wait_task(task_id, {'completed', 'failed'})
    assert task['run_status'] == 'completed', task.get('last_error')


@pytest.mark.asyncio
@pytest.mark.parametrize('interruption', ['timeout', 'cancel'])
async def test_unclosed_native_popup_never_navigates_or_captures(mv3, db, interruption):
    from app.models import Job
    task_id = mv3.create_task()
    popup = await mv3.start(hold_popup=True)
    await popup.wait("document.querySelector('#runner-status').textContent.includes('启动已接受')")
    assert len(await mv3.worker.evaluate("chrome.runtime.getContexts({contextTypes:['POPUP']})")) == 1
    assert mv3.page.url == DETAIL
    if interruption == 'cancel':
        # Explicit user action via the same real popup; no automatic retry.
        await popup.click('#runner-cancel')
    task = await mv3.wait_task(task_id, {'failed', 'cancelled'})
    assert task['run_status'] == ('cancelled' if interruption == 'cancel' else 'failed')
    if interruption == 'timeout':
        assert task['last_error'] == 'start-v4/handoff_focus_timeout'
    assert mv3.page.url == DETAIL
    assert not any(path in ('/api/extension/salary-ocr', '/api/extension/jobs/import') for _, path, _ in mv3.api_calls)
    db.expire_all()
    assert db.query(Job).count() == 0
    assert await mv3.worker.evaluate('getRunnerPointer()') is None


@pytest.mark.asyncio
async def test_invalid_cap_keeps_native_popup_open_without_start(mv3):
    task_id = mv3.create_task()
    popup = await mv3.start(cap=21)
    await popup.wait("document.querySelector('#runner-status').textContent.includes('上限')")
    assert len(await mv3.worker.evaluate("chrome.runtime.getContexts({contextTypes:['POPUP']})")) == 1
    assert mv3.client.get(f'/api/tasks/search-plan/{task_id}').json()['run_status'] == 'pending'
    assert not any(path.endswith('/run/start') for _, path, _ in mv3.api_calls)


@pytest.mark.asyncio
async def test_runner_scroll_discovery_dedup_and_bounded_stop(mv3, db):
    from app.models import Job, TaskCandidate
    task_id = mv3.create_task()
    mv3.search_fixture(salary_mode='plain')
    await mv3.start(cap=3)
    await mv3.wait_popup_closed()
    task = await mv3.wait_task(task_id, {'completed', 'failed', 'paused_verification'})
    assert task['run_status'] == 'completed', task.get('last_error')
    assert task['scroll_round'] == 2
    assert (task['observed_jobs'], task['new_jobs'], task['duplicate_jobs']) == (5, 2, 3)
    assert task['imported_jobs'] == 3
    assert task['visible_jobs'] == 3
    db.expire_all()
    assert {j.external_id for j in db.query(Job)} == {'e2e-1', 'e2e-2', 'e2e-3'}
    assert db.query(TaskCandidate).count() == 3
    assert await mv3.page.evaluate('window.fixtureClicks') == [1, 2, 3]
    assert mv3.crop_dimensions == []  # reliable DOM salaries never invoke OCR
    assert mv3.client.get('/api/extension/sessions/active').json() is None


@pytest.mark.asyncio
async def test_two_explicit_tasks_reuse_canonical_job_and_matching_cache(mv3, db, active_resume, monkeypatch):
    """Two human starts, not an automatic cross-task scheduler; no real AI/network."""
    from tests.test_analysis import build_result
    from app.models import Job, JobAnalysis, TaskCandidate
    calls = []
    async def mock_model(**kwargs):
        calls.append(kwargs)
        return build_result()
    monkeypatch.setattr('app.services.job_matcher.run_job_match', mock_model)
    mv3.search_fixture(salary_mode='plain')
    first = mv3.create_task(keyword='[Fixture] cloud')
    await mv3.start(cap=1, auto_match=True)
    await mv3.wait_popup_closed()
    first_result = await mv3.wait_task(first, {'completed', 'failed'})
    assert first_result['state'] == 'completed', first_result['last_error']
    assert first_result['imported_jobs'] == 1
    second = mv3.create_task(keyword='[Fixture] platform')
    await mv3.start(cap=1, auto_match=True, task_id=second)
    await mv3.wait_popup_closed()
    second_result = await mv3.wait_task(second, {'completed', 'failed'})
    assert second_result['state'] == 'completed', second_result['last_error']
    assert second_result['imported_jobs'] == 0
    review = mv3.client.get(f'/api/tasks/{second}/auto-match/review').json()
    assert review['used'] == review['completed'] == review['cap'] == 1
    assert review['items'][0]['cached'] is True
    db.expire_all()
    assert db.query(Job).count() == db.query(JobAnalysis).count() == len(calls) == 1
    associations = db.query(TaskCandidate).all()
    assert len(associations) == 2 and len({row.job_id for row in associations}) == 1
    assert {row.task_id for row in associations} == {first, second}
    assert sum(path == '/api/extension/jobs/import' for _, path, _ in mv3.api_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['verification', 'mismatch'])
async def test_verification_or_wrong_detail_never_imports(mv3, db, scenario):
    from app.models import Job
    task_id = mv3.create_task()
    mv3.search_fixture(scenario=scenario, salary_mode='plain')
    await mv3.start()
    task = await mv3.wait_task(task_id, {'completed', 'failed', 'paused_verification'})
    assert task['run_status'] == ('paused_verification' if scenario == 'verification' else 'completed'), task.get('last_error')
    assert task['imported_jobs'] == 0
    assert db.query(Job).count() == 0 and not mv3.crop_dimensions
    assert await mv3.page.evaluate('window.fixtureClicks') == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize('interruption', ['cancel', 'identity', 'verification', 'background'])
async def test_real_capture_result_discarded_on_context_change(mv3, db, interruption):
    from app.models import Job
    task_id = mv3.create_task()
    mv3.search_fixture()
    mv3.gate_path = '/api/extension/salary-ocr'
    await mv3.start()
    await mv3.wait_popup_closed()
    await mv3.wait_gate()  # real capture/crop occurred; do not fake the OCR result
    if interruption == 'cancel':
        popup = await mv3.popup()
        await popup.click('#runner-cancel')
        await popup.wait("document.querySelector('#runner-status').textContent.includes('已请求取消')")
    elif interruption == 'identity':
        await mv3.page.locator('.job-card-wrapper a').evaluate("e=>e.href='/job_detail/different.html'")
    elif interruption == 'verification':
        await mv3.page.evaluate("document.body.insertAdjacentHTML('beforeend','<div class=verify-wrapper>请完成安全验证</div>')")
    else:
        other = await mv3.context.new_page()
        await other.bring_to_front()
    mv3.gate_release.set()
    task = await mv3.wait_task(task_id, {'cancelled', 'failed', 'paused_verification'})
    expected = {'cancel': 'cancelled', 'verification': 'paused_verification'}.get(interruption, 'failed')
    assert task['run_status'] == expected, task.get('last_error')
    assert task['imported_jobs'] == 0 and db.query(Job).count() == 0
    assert not any(path == '/api/extension/jobs/import' for _, path, _ in mv3.api_calls)
    assert len(mv3.crop_dimensions) == 1


@pytest.mark.asyncio
async def test_pause_resume_preserves_spent_budget_and_closes_popup(mv3, db):
    from app.models import Job
    task_id = mv3.create_task()
    mv3.search_fixture()
    mv3.gate_path = '/api/extension/salary-ocr'
    await mv3.start(cap=2)
    await mv3.wait_popup_closed()
    await mv3.wait_gate()
    popup = await mv3.popup()
    await popup.click('#runner-pause')
    await popup.wait("document.querySelector('#runner-status').textContent.includes('已请求暂停')")
    mv3.gate_release.set()
    assert (await mv3.wait_task(task_id, {'paused', 'failed'}))['run_status'] == 'paused'
    assert db.query(Job).count() == 0
    # Closing/reopening this test popup is an explicit user-like action. Resume
    # uses the same real worker pointer/session; no checkpoint or budget mocks.
    await popup.evaluate('window.close()')
    await mv3.wait_popup_closed()
    popup = await mv3.popup()
    await popup.click('#runner-resume')
    await mv3.wait_popup_closed()
    task = await mv3.wait_task(task_id, {'completed', 'failed'})
    assert task['run_status'] == 'completed', task.get('last_error')
    db.expire_all()
    assert [job.external_id for job in db.query(Job)] == ['e2e-2']
    assert task['imported_jobs'] == 1
    assert await mv3.page.evaluate('window.fixtureClicks') == [1, 2]


@pytest.mark.asyncio
async def test_three_no_new_rounds_stop_without_manual_scrolling(mv3):
    task_id = mv3.create_task()
    mv3.search_fixture(scenario='no-new', salary_mode='plain')
    await mv3.start(cap=3)
    task = await mv3.wait_task(task_id, {'completed', 'failed'})
    assert task['run_status'] == 'completed', task.get('last_error')
    assert task['scroll_round'] == task['no_new_rounds'] == 3
    assert task['new_jobs'] == 0 and task['duplicate_jobs'] == 3
    assert task['imported_jobs'] == 1
    assert await mv3.page.evaluate('window.fixtureClicks') == [1]


@pytest.mark.asyncio
async def test_network_backstop_never_forwards_production_or_external_urls(mv3):
    def through_proxy(url):
        connection = http.client.HTTPConnection('127.0.0.1', mv3.proxy_port, timeout=2)
        try:
            connection.request('GET', url)
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()
    for url in ['http://127.0.0.1:8000/api/jobs', 'http://example.invalid/',
                'http://127.0.0.1:8000/api/tasks/1/match-run']:
        assert await asyncio.to_thread(through_proxy, url) == 403
    # Prove *allowed* traffic is also intercepted and sees the empty test DB.
    assert await asyncio.to_thread(through_proxy, 'http://127.0.0.1:8000/api/tasks/search-plan') == 200
    assert mv3.client.get('/api/tasks/search-plan').json() == {'items': []}
