"use strict";
/**
 * M4e/M4f popup controls: pick or generate a SearchPlan task, then start /
 * pause / resume / cancel the bounded automatic runner that lives entirely
 * in `background.ts`. This module only sends messages and renders whatever
 * comes back - it holds no runner state of its own and never touches a
 * BOSS tab directly. See CLAUDE.md's "Chrome extension - M4 supervised
 * navigation policy" M4e/M4f amendment.
 *
 * No polling: the status shown here is refreshed only on popup open and
 * after an explicit action (start/pause/resume/cancel/refresh click) -
 * exactly the session.ts (M4a) pattern, never a timer.
 */
;
(function () {
    const $ = (id) => document.getElementById(id);
    const taskSelect = $('runner-task');
    const candidateCapInput = $('runner-candidate-cap');
    const autoMatchInput = $('runner-auto-match');
    const generateBtn = $('runner-generate');
    const refreshBtn = $('runner-refresh');
    const startBtn = $('runner-start');
    const pauseBtn = $('runner-pause');
    const resumeBtn = $('runner-resume');
    const cancelBtn = $('runner-cancel');
    const retryBtn = $('runner-retry-terminal');
    const statusEl = $('runner-status');
    const countersEl = $('runner-counters');
    let tasks = [];
    function setStatus(text, tone = 'info') {
        statusEl.textContent = text;
        statusEl.className = 'status status-' + tone;
    }
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
    /** The only place this module reaches into the background worker - every
     * runner action and status read is a message, never a direct tab call. */
    function sendToBackground(message) {
        return new Promise((resolve) => {
            chrome.runtime.sendMessage(message, (response) => resolve(response));
        });
    }
    function selectedTask() {
        const id = Number(taskSelect.value);
        return tasks.find((t) => t.id === id) || null;
    }
    function renderCounters(task, runner) {
        if (!task) {
            countersEl.textContent = '';
            return;
        }
        const parts = [
            `任务ID(task_id) ${task.task_id}`,
            `状态(state) ${task.state || task.run_status || '未开始'}`,
            `城市(city) ${task.city || '（未设置）'}`,
            `关键词(keyword) ${task.keyword || '（未设置）'}`,
            `当前网址(current_url) ${task.current_url || '（尚未导航）'}`,
            `滚动轮次(scroll_round) ${task.scroll_round}`,
            `可见岗位(visible_jobs) ${task.visible_jobs}`,
            `观察(observed_jobs) ${task.observed_jobs}`,
            `新增(new_jobs) ${task.new_jobs}`,
            `重复(duplicate_jobs) ${task.duplicate_jobs}`,
            `已导入(imported_jobs) ${task.imported_jobs}`,
            `连续无新增(no_new_rounds) ${task.no_new_rounds}`,
            `当前候选人(current_candidate) ${task.current_candidate || '（无）'}`,
            `最近动作(last_action) ${task.last_action || '（无）'}`,
            `暂停原因(paused_reason) ${task.paused_reason || '（无）'}`,
            `更新于(updated_at) ${task.updated_at}`,
        ];
        if (runner) {
            parts.push(`滚动 ${runner.scrollsUsed}/30`, `已用候选名额 ${runner.candidatesAttempted ?? '未知'}/${runner.candidateCap ?? '未知'}`, `已处理候选 ${runner.candidatesProcessed}`);
            if (runner.lastError)
                parts.push(`浏览器侧错误：${runner.lastError}`);
            if (runner.pendingTerminal)
                parts.push(`有未确认的结束操作（${runner.pendingTerminal.outcome}），可点"重试结束"`);
        }
        if (task.last_error)
            parts.push(`最近错误(last_error)：${task.last_error}`);
        countersEl.textContent = parts.join(' · ');
    }
    function renderButtons(task, runner) {
        const status = task?.run_status || null;
        candidateCapInput.disabled = status !== 'pending' || !!runner;
        autoMatchInput.disabled = status !== 'pending' || !!runner;
        candidateCapInput.max = String(Math.min(20, task?.max_candidates ?? 20));
        if (runner)
            candidateCapInput.value = Number.isInteger(runner.candidateCap) ? String(runner.candidateCap) : '';
        startBtn.disabled = !task || status !== 'pending';
        pauseBtn.disabled = !task || status !== 'running' || !!runner?.pauseRequested;
        resumeBtn.disabled = !task || !['paused', 'paused_verification', 'paused_login_required'].includes(status || '');
        cancelBtn.disabled = !task || status === null || ['completed', 'failed', 'cancelled'].indexOf(status) !== -1;
        taskSelect.disabled = !!task && status !== null && ['pending'].indexOf(status) === -1 &&
            ['completed', 'failed', 'cancelled'].indexOf(status) === -1;
        retryBtn.disabled = !runner || !runner.pendingTerminal;
    }
    async function refreshSelected() {
        const task = selectedTask();
        if (!task) {
            renderCounters(null, null);
            renderButtons(null, null);
            return;
        }
        try {
            const fresh = await request(`${JobAgentConfig.SEARCH_PLAN_PATH}/${task.id}`);
            const index = tasks.findIndex((t) => t.id === fresh.id);
            if (index !== -1)
                tasks[index] = fresh;
            const runnerResponse = await sendToBackground({
                type: 'jobagent:runner-status',
            });
            const runner = runnerResponse?.runner && runnerResponse.runner.taskId === fresh.id
                ? runnerResponse.runner
                : null;
            renderCounters(fresh, runner);
            renderButtons(fresh, runner);
        }
        catch (err) {
            setStatus('刷新任务状态失败：' + String(err?.message || err), 'error');
        }
    }
    async function loadTasks(preserveSelection) {
        const previous = taskSelect.value;
        try {
            const response = await request(JobAgentConfig.SEARCH_PLAN_PATH);
            tasks = response.items || [];
            taskSelect.innerHTML = '';
            if (!tasks.length) {
                taskSelect.appendChild(new Option('（还没有搜索计划任务，请先生成）', ''));
                renderCounters(null, null);
                renderButtons(null, null);
                return;
            }
            for (const task of tasks) {
                const label = `${task.name}（${task.run_status || 'pending'}）`;
                taskSelect.appendChild(new Option(label, String(task.id)));
            }
            if (preserveSelection && previous && tasks.some((t) => String(t.id) === previous)) {
                taskSelect.value = previous;
            }
            await refreshSelected();
        }
        catch (err) {
            setStatus('无法加载搜索计划任务：' + String(err?.message || err), 'error');
        }
    }
    async function generatePlan() {
        generateBtn.disabled = true;
        setStatus('正在生成搜索计划（不会发起任何网络请求到 BOSS）…');
        try {
            const result = await request(JobAgentConfig.SEARCH_PLAN_GENERATE_PATH, { method: 'POST', body: {} });
            setStatus(`搜索计划已生成：新增 ${result.created} / 已存在 ${result.skipped} / 共 ${result.total}`, 'ok');
            await loadTasks(false);
        }
        catch (err) {
            setStatus('生成搜索计划失败：' + String(err?.message || err), 'error');
        }
        finally {
            generateBtn.disabled = false;
        }
    }
    // Resolve the explicit click's tab in the popup, not implicit worker scope.
    async function popupTarget() {
        const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
        if (tabs.length !== 1 || tabs[0].id === undefined || tabs[0].windowId === undefined) {
            throw new Error('无法绑定当前标签页，请从已登录的 BOSS 标签页打开扩展。');
        }
        return { tabId: tabs[0].id, windowId: tabs[0].windowId };
    }
    async function startRun() {
        const task = selectedTask();
        if (!task)
            return;
        const candidateCap = candidateCapInput.valueAsNumber;
        if (!Number.isInteger(candidateCap) || candidateCap < 1 || candidateCap > Math.min(20, task.max_candidates ?? 20)) {
            setStatus(`请输入 1–${Math.min(20, task.max_candidates ?? 20)} 的整数候选上限。`, 'error');
            return;
        }
        startBtn.disabled = true;
        setStatus('正在启动自动运行（一个前台标签页，有上限）…');
        try {
            let matchApproval;
            if (autoMatchInput.checked) {
                if (candidateCap > 3)
                    throw new Error('自动匹配本轮最多 3 个岗位，请降低候选上限。');
                const quote = await request(`/api/tasks/${task.id}/auto-match/quote?cap=${candidateCap}`);
                if (!window.confirm(`确认搜索并匹配？\n任务：${task.name}\n简历：${quote.resume_name}\n模型：${quote.model}\n最多 ${quote.cap} 个岗位、最多 ${quote.cap} 次付费调用（缓存免费）。\n实际金额按模型用量计费，当前无法预估；失败也占名额，不自动重试。\n简历及岗位文本将发送给模型服务。暂停/取消不能撤回已发出的调用。\n不会投递、收藏或发消息。`)) {
                    setStatus('已取消费用确认，未启动任务。', 'info');
                    startBtn.disabled = false;
                    return;
                }
                matchApproval = { confirmed: true, cap: quote.cap, fingerprint: quote.fingerprint };
            }
            const result = await sendToBackground({
                type: 'jobagent:runner-start',
                taskId: task.id,
                candidateCap,
                ...(matchApproval ? { matchApproval } : {}),
                popupTarget: await popupTarget(),
            });
            if (!result?.ok) {
                const code = result?.error || '未知错误';
                const reasons = {
                    'window_id_missing': 'Chrome 未返回窗口编号',
                    'window_not_normal': '当前不是普通浏览器窗口',
                    'window_not_focused': 'Chrome 报告窗口未聚焦（扩展弹窗也可能影响此状态）',
                    'active_tab_missing': '该窗口没有返回活动标签页',
                    'active_tab_ambiguous': '该窗口返回多个活动标签页',
                    'tab_not_active': '标签页已不处于活动状态',
                    'tab_window_mismatch': '标签页与所选窗口不一致',
                    'window_focus_changed': '读取标签期间窗口失去焦点',
                    'window_query_failed': 'Chrome 窗口查询失败',
                    'tab_query_failed': 'Chrome 标签查询失败',
                    'focus_recheck_failed': 'Chrome 窗口焦点复查失败',
                    'tab_id_missing': 'Chrome 未返回标签编号',
                    'tab_url_unavailable': 'Chrome 未提供标签网址，请核对扩展对 BOSS 页的访问权限',
                    'popup_binding_failed': '无法确认本次弹窗与活动标签页，请刷新 BOSS 后从该页重新打开扩展',
                    'handoff_tab_changed': '启动或恢复的标签页已变化，请回到原 BOSS 标签页',
                };
                const detail = /^start-v[34]\//.test(code) ? reasons[code.slice('start-v3/'.length)] : null;
                setStatus('启动失败：' + (detail ? detail + ' [' + code + ']' : code), 'error');
                startBtn.disabled = false;
                return;
            }
            setStatus('启动已接受，关闭弹窗后等待原标签页恢复前台。', 'ok');
            // On Windows an open action popup can leave its browser window reporting
            // focused=false. Release our own popup after acceptance, before further
            // status requests. The worker/overlay own progress and cancellation.
            // Do not force-focus Chrome or relax the worker's screenshot focus guard.
            window.close();
        }
        catch (err) {
            setStatus('启动失败：' + String(err?.message || err), 'error');
            startBtn.disabled = false;
        }
    }
    async function pauseRun() {
        pauseBtn.disabled = true;
        setStatus('正在请求暂停…');
        await sendToBackground({ type: 'jobagent:runner-pause' });
        setStatus('已请求暂停，将在下一个检查点停止。', 'ok');
        await refreshSelected();
    }
    async function resumeRun() {
        resumeBtn.disabled = true;
        setStatus('正在恢复…');
        try {
            const result = await sendToBackground({
                type: 'jobagent:runner-resume',
                popupTarget: await popupTarget(),
            });
            if (!result?.ok) {
                setStatus('恢复失败：' + (result?.error || '未知错误'), 'error');
            }
            else {
                setStatus('已恢复。', 'ok');
                window.close();
                return;
            }
        }
        catch (err) {
            setStatus('恢复失败：' + String(err?.message || err), 'error');
        }
        await refreshSelected();
    }
    async function cancelRun() {
        cancelBtn.disabled = true;
        setStatus('正在取消…');
        await sendToBackground({ type: 'jobagent:runner-cancel' });
        setStatus('已请求取消。', 'ok');
        await refreshSelected();
    }
    /** CLAUDE.md M4f review item 3's recovery path - re-attempts a terminal
     * transition/session-stop the background worker could not confirm last
     * time, instead of the run silently getting stuck. */
    async function retryTerminal() {
        retryBtn.disabled = true;
        setStatus('正在重试结束操作…');
        try {
            const result = await sendToBackground({
                type: 'jobagent:runner-retry-terminal',
            });
            setStatus(result?.ok ? '已结束。' : '仍未成功：' + (result?.error || '未知错误'), result?.ok ? 'ok' : 'error');
        }
        finally {
            await refreshSelected();
        }
    }
    taskSelect.addEventListener('change', () => void refreshSelected());
    generateBtn.addEventListener('click', () => void generatePlan());
    refreshBtn.addEventListener('click', () => void refreshSelected());
    startBtn.addEventListener('click', () => void startRun());
    pauseBtn.addEventListener('click', () => void pauseRun());
    resumeBtn.addEventListener('click', () => void resumeRun());
    cancelBtn.addEventListener('click', () => void cancelRun());
    retryBtn.addEventListener('click', () => void retryTerminal());
    void loadTasks(false);
})();
