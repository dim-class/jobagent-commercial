"use strict";
/**
 * M4a bounded-session scaffolding: explicit human approval, start/stop, an
 * always-visible stop control, and zero-valued progress counters. No
 * navigation, no scrolling, no clicking a job - see CLAUDE.md's "Chrome
 * extension - M4 supervised navigation policy" and
 * docs/orchestration/ROADMAP.md M4a.
 *
 * State lives in `chrome.storage.session` (this extension's own in-memory
 * session storage, cleared on browser restart) - never a recruitment
 * site's cookies/localStorage/sessionStorage. A browser restart or a tab
 * that no longer matches what was approved fails closed: the session is
 * stopped, never silently resumed.
 */
;
(function () {
    //: Immutable POC ceilings - CLAUDE.md "1a." A human's approved caps may be
    //: lower, never higher. Mirrors backend/app/services/supervised_sessions.py;
    //: the backend re-checks these independently and is the real enforcement.
    const CEILING = { pageCap: 3, candidateCap: 20, scrollCap: 5 };
    const REQUIRED_ORIGIN = 'https://www.zhipin.com';
    const STORAGE_KEY = 'jobagent_session_pointer';
    const $ = (id) => document.getElementById(id);
    const taskSelect = $('session-task');
    const criteriaEl = $('session-criteria');
    // BOSS's results are one continuous scroll list with no pagination
    // control (real logged-in Chrome verification) - there is no page-cap
    // input to show or edit. `page_cap: 1` is still sent to the backend below
    // for schema compatibility only; it is never presented as adjustable.
    const candidateCapInput = $('session-candidate-cap');
    const scrollCapInput = $('session-scroll-cap');
    const tabConfirmEl = $('session-tab-confirm');
    const startBtn = $('session-start');
    const stopBtn = $('session-stop');
    const sessionStatusEl = $('session-status');
    const progressEl = $('session-progress');
    const historyEl = $('session-history');
    let currentSessionId = null;
    function setSessionStatus(text, tone = 'info') {
        sessionStatusEl.textContent = text;
        sessionStatusEl.className = 'status status-' + tone;
    }
    /** One typed trust boundary: the network response is `unknown` until this
     * single cast, never an implicit or explicit `any` anywhere else. */
    async function request(path, init) {
        const response = await fetch(JobAgentConfig.BACKEND_BASE + path, {
            method: init?.method || 'GET',
            headers: init?.body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
            body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
        });
        const text = await response.text();
        const payload = text ? JSON.parse(text) : null;
        if (!response.ok) {
            const message = payload?.message;
            throw new Error(message || `请求失败（HTTP ${response.status}）`);
        }
        return payload;
    }
    async function activeTab() {
        const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
        return tabs.length ? tabs[0] : null;
    }
    function originOf(url) {
        try {
            const parsed = new URL(url);
            return parsed.protocol + '//' + parsed.host;
        }
        catch {
            return null;
        }
    }
    function clampCaps() {
        candidateCapInput.max = String(CEILING.candidateCap);
        scrollCapInput.max = String(CEILING.scrollCap);
        if (Number(candidateCapInput.value) > CEILING.candidateCap) {
            candidateCapInput.value = String(CEILING.candidateCap);
        }
        if (Number(scrollCapInput.value) > CEILING.scrollCap)
            scrollCapInput.value = String(CEILING.scrollCap);
    }
    async function loadTasks() {
        taskSelect.innerHTML = '';
        try {
            const response = await request(JobAgentConfig.TASKS_PATH);
            const tasks = response.items || [];
            if (!tasks.length) {
                taskSelect.appendChild(new Option('（还没有任务，请先在控制台创建）', ''));
                startBtn.disabled = true;
                return;
            }
            startBtn.disabled = false;
            for (const task of tasks)
                taskSelect.appendChild(new Option(task.name, String(task.id)));
            renderCriteria(tasks[0]);
            taskSelect.addEventListener('change', () => {
                const selected = tasks.find((t) => String(t.id) === taskSelect.value);
                if (selected)
                    renderCriteria(selected);
            });
        }
        catch (err) {
            setSessionStatus('无法加载任务列表：' + String(err?.message || err), 'error');
        }
    }
    function renderCriteria(task) {
        const parts = [task.keywords, task.city, task.experience_text, task.education_text]
            .filter(Boolean)
            .join(' · ');
        criteriaEl.textContent = parts || '未设置搜索条件';
    }
    async function confirmTab() {
        const tab = await activeTab();
        if (!tab || tab.id === undefined || !tab.url) {
            tabConfirmEl.textContent = '找不到当前标签页。';
            return { ok: false, origin: null, tabId: null };
        }
        const origin = originOf(tab.url);
        if (origin !== REQUIRED_ORIGIN) {
            tabConfirmEl.textContent =
                `当前标签页不是 ${REQUIRED_ORIGIN}（实际：${origin || '未知'}）。请切换到该页面后重试。`;
            return { ok: false, origin, tabId: tab.id };
        }
        tabConfirmEl.textContent = `已确认当前标签页：${REQUIRED_ORIGIN}`;
        return { ok: true, origin, tabId: tab.id };
    }
    async function storePointer(pointer) {
        if (pointer)
            await chrome.storage.session.set({ [STORAGE_KEY]: pointer });
        else
            await chrome.storage.session.remove(STORAGE_KEY);
    }
    async function loadPointer() {
        const stored = await chrome.storage.session.get(STORAGE_KEY);
        return stored[STORAGE_KEY] || null;
    }
    const EVENT_LABEL = { started: '已开始', stopped: '已停止' };
    function renderHistory(events) {
        historyEl.innerHTML = '';
        for (const event of events) {
            const line = document.createElement('div');
            line.className = 'sub';
            line.textContent =
                (EVENT_LABEL[event.event_type] || event.event_type) +
                    (event.reason ? `（${event.reason}）` : '') +
                    ` · ${event.created_at}`;
            historyEl.appendChild(line);
        }
    }
    function renderRunning(session) {
        startBtn.classList.add('hidden');
        stopBtn.classList.remove('hidden');
        taskSelect.disabled = true;
        candidateCapInput.disabled = true;
        scrollCapInput.disabled = true;
        progressEl.textContent =
            `会话进行中 · 连续滚动列表（无翻页）· ` +
                `候选人 ${session.candidates_extracted}/${session.candidate_cap} · ` +
                `本页滚动 ${session.scrolls_used}/${session.scroll_cap}`;
        renderHistory(session.events);
    }
    function renderIdle() {
        startBtn.classList.remove('hidden');
        stopBtn.classList.add('hidden');
        taskSelect.disabled = false;
        candidateCapInput.disabled = false;
        scrollCapInput.disabled = false;
        progressEl.textContent = '';
    }
    async function startSession() {
        clampCaps();
        const { ok, origin, tabId } = await confirmTab();
        if (!ok || tabId === null || !origin) {
            setSessionStatus('无法启动：请先切换到 BOSS 直聘页面（https://www.zhipin.com）。', 'error');
            return;
        }
        const taskId = Number(taskSelect.value);
        if (!taskId) {
            setSessionStatus('请先选择一个任务。', 'error');
            return;
        }
        startBtn.disabled = true;
        try {
            const session = await request(JobAgentConfig.SESSIONS_PATH, {
                method: 'POST',
                body: {
                    task_id: taskId,
                    // Always 1: BOSS's results are one continuous scroll list with no
                    // pagination control to bound - see the module docstring above.
                    page_cap: 1,
                    candidate_cap: Number(candidateCapInput.value),
                    scroll_cap: Number(scrollCapInput.value),
                    tab_origin: origin,
                },
            });
            currentSessionId = session.id;
            await storePointer({ sessionId: session.id, tabId });
            // Push it to the approved tab immediately - no reload needed, no
            // storage event content scripts cannot receive, just one message.
            try {
                await chrome.tabs.sendMessage(tabId, { type: 'jobagent:session-started', session });
            }
            catch {
                // The overlay will still pick this up on the tab's next load.
            }
            renderRunning(session);
            setSessionStatus('会话已开始。本阶段（M4a）不会翻页、滚动、点击岗位或读取任何岗位信息 —— 仅记录会话本身。', 'ok');
        }
        catch (err) {
            setSessionStatus('启动会话失败：' + String(err?.message || err), 'error');
        }
        finally {
            startBtn.disabled = false;
        }
    }
    async function stopSession(reason) {
        if (currentSessionId === null)
            return;
        const pointer = await loadPointer();
        stopBtn.disabled = true;
        try {
            const session = await request(`${JobAgentConfig.SESSIONS_PATH}/${currentSessionId}/stop`, { method: 'POST', body: { reason } });
            await storePointer(null);
            if (pointer) {
                try {
                    await chrome.tabs.sendMessage(pointer.tabId, { type: 'jobagent:session-stopped' });
                }
                catch {
                    // Tab may be gone or navigated away - nothing more to remove there.
                }
            }
            currentSessionId = null;
            renderIdle();
            renderHistory(session.events);
            setSessionStatus(reason === 'user_stop'
                ? '会话已停止。'
                : '检测到标签页已失效或浏览器已重启，会话已自动停止（不会继续任何操作）。', reason === 'user_stop' ? 'ok' : 'warn');
        }
        catch (err) {
            setSessionStatus('停止会话失败：' + String(err?.message || err), 'error');
        }
        finally {
            stopBtn.disabled = false;
        }
    }
    /**
     * On popup open: recover a running session only if the local pointer, the
     * backend, and the current active tab all agree.
     *
     * A mismatch fails closed - but only for a session this popup owns. A
     * session started from the local console has no pointer here, and stopping
     * it was a live bug: the console tells the user to click the toolbar icon
     * once (it is how salary OCR gets its `activeTab` grant), and that click
     * killed the run they had just started - `stale_tab` 0.6s after the session
     * was created, then "会话未在进行中" on its next navigation. Never stop what
     * this entry point did not start; the worker owns that session and its own
     * `resolveSessionForTab` already refuses to touch another owner's.
     */
    async function recoverOrReset() {
        const pointer = await loadPointer();
        let active = null;
        try {
            active = await request(JobAgentConfig.SESSIONS_ACTIVE_PATH);
        }
        catch {
            active = null;
        }
        if (!active) {
            if (pointer)
                await storePointer(null);
            renderIdle();
            return;
        }
        if (!pointer) {
            // Someone else's session - the console runner's, in practice. Report it
            // and leave it strictly alone: no stop, no adopt, no pointer written.
            renderIdle();
            setSessionStatus('另一个入口（本地控制台）正在运行一个会话，此处不做任何操作。', 'info');
            return;
        }
        const tab = await activeTab();
        const origin = tab && tab.url ? originOf(tab.url) : null;
        const tabMatches = !!tab && pointer.tabId === tab.id && origin === active.tab_origin;
        currentSessionId = active.id;
        if (!tabMatches) {
            await stopSession('stale_tab');
            return;
        }
        renderRunning(active);
        setSessionStatus('已恢复正在进行的会话。', 'info');
    }
    startBtn.addEventListener('click', () => void startSession());
    stopBtn.addEventListener('click', () => void stopSession('user_stop'));
    [candidateCapInput, scrollCapInput].forEach((input) => input.addEventListener('change', clampCaps));
    void (async function init() {
        clampCaps();
        await loadTasks();
        await recoverOrReset();
    })();
})();
