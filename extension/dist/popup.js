"use strict";
/**
 * The popup: the only place a detection can start.
 *
 * Flow, all of it human-triggered:
 *
 *   [检测当前页面]  -> ask the content script what is on screen
 *   [发送到 JobAgent 预览] -> POST the extracted fields, get new/duplicate back
 *   [导入]          -> explicit, one job at a time, confirmed=true
 *
 * Nothing is sent anywhere on open, and nothing is saved without a click.
 */
;
(function () {
    const PAGE_TYPE_LABEL = {
        search: '搜索结果页',
        detail: '职位详情页',
        unsupported: '不支持的页面',
    };
    const FIELD_LABEL = {
        title: '职位名称',
        company: '公司',
        salary_text: '薪资',
        city: '城市',
        experience_text: '经验',
        education_text: '学历',
        source_url: '职位链接',
        description: '职位描述',
    };
    const FIELD_ORDER = [
        'title',
        'company',
        'salary_text',
        'city',
        'experience_text',
        'education_text',
        'source_url',
        'description',
    ];
    let detection = null;
    const $ = (id) => document.getElementById(id);
    const statusEl = $('status');
    const resultEl = $('result');
    const detectBtn = $('detect');
    const previewBtn = $('preview');
    const devToggle = $('dev-mode');
    const diagnosticPanel = $('diagnostic-panel');
    const diagnoseBtn = $('diagnose');
    const diagnosticCopyBtn = $('diagnostic-copy');
    const diagnosticOutput = $('diagnostic-output');
    function devMode() {
        return devToggle.checked;
    }
    /** The diagnostic is developer-mode-only and detail-page-only, always. */
    function updateDiagnosticAvailability() {
        const isDev = devMode();
        diagnosticPanel.classList.toggle('hidden', !isDev);
        diagnoseBtn.disabled = !isDev || !detection || detection.page_type !== 'detail';
    }
    function setStatus(text, tone = 'info') {
        statusEl.textContent = text;
        statusEl.className = 'status status-' + tone;
    }
    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className)
            node.className = className;
        if (text !== undefined)
            node.textContent = text;
        return node;
    }
    function truncate(value, limit) {
        return value.length > limit ? value.slice(0, limit) + '…' : value;
    }
    // ------------------------------------------------------------- rendering
    function renderCandidate(candidate, index) {
        const card = el('section', 'card');
        card.appendChild(el('h3', undefined, candidate.title || `（第 ${index + 1} 项，无职位名称）`));
        card.appendChild(el('div', 'sub', candidate.company || '（未识别公司）'));
        const list = el('dl', 'fields');
        for (const field of FIELD_ORDER) {
            const raw = candidate[field];
            const value = raw ? truncate(String(raw).replace(/\s+/g, ' '), 160) : null;
            const term = el('dt', undefined, FIELD_LABEL[field] || field);
            const def = el('dd', value ? 'ok' : 'missing', value || '未提取到');
            if (devMode()) {
                const selector = candidate.matched_selectors[field];
                const hint = el('div', 'selector', selector ? `匹配选择器：${selector}` : '没有任何候选选择器命中');
                def.appendChild(hint);
            }
            list.appendChild(term);
            list.appendChild(def);
        }
        card.appendChild(list);
        if (devMode()) {
            const extras = Object.keys(candidate.matched_selectors).filter((key) => FIELD_ORDER.indexOf(key) === -1);
            if (extras.length) {
                card.appendChild(el('div', 'selector', extras.map((key) => `${key} ← ${candidate.matched_selectors[key]}`).join(' · ')));
            }
        }
        for (const warning of candidate.warnings) {
            card.appendChild(el('div', 'warn', warning));
        }
        return card;
    }
    function render() {
        resultEl.innerHTML = '';
        if (!detection)
            return;
        const summary = el('section', 'card summary');
        summary.appendChild(el('h3', undefined, PAGE_TYPE_LABEL[detection.page_type] || detection.page_type));
        summary.appendChild(el('div', 'sub mono', detection.url || '（无 URL）'));
        summary.appendChild(el('div', 'count', `识别到 ${detection.candidates.length} 个岗位`));
        if (detection.verification) {
            summary.appendChild(el('div', 'warn', 'BOSS 正在要求安全验证 —— 请你自己在浏览器里完成，本扩展不会处理验证。'));
        }
        for (const warning of detection.warnings)
            summary.appendChild(el('div', 'warn', warning));
        for (const error of detection.errors)
            summary.appendChild(el('div', 'error', error));
        resultEl.appendChild(summary);
        detection.candidates.forEach((candidate, index) => {
            resultEl.appendChild(renderCandidate(candidate, index));
        });
        previewBtn.disabled = detection.candidates.length === 0;
    }
    function renderPreview(response) {
        const card = el('section', 'card summary');
        card.appendChild(el('h3', undefined, '本地 JobAgent 预览结果'));
        card.appendChild(el('div', 'count', `检测 ${response.detected} · 新岗位 ${response.new_count} · 已存在 ${response.duplicate_count} · 信息不足 ${response.incomplete_count}`));
        card.appendChild(el('div', 'sub', response.message));
        for (const warning of response.warnings)
            card.appendChild(el('div', 'warn', warning));
        for (const row of response.rows) {
            const line = el('div', 'row');
            line.appendChild(el('span', 'badge badge-' + row.status, row.status_label));
            line.appendChild(el('span', 'row-title', `${row.company || '未知公司'} · ${row.title}`));
            if (row.status === 'new') {
                const button = el('button', 'btn-sm');
                button.textContent = '导入';
                button.addEventListener('click', () => void importOne(row.index, button));
                line.appendChild(button);
            }
            else if (row.existing_job_id !== null) {
                line.appendChild(el('span', 'sub', `已存在 #${row.existing_job_id}`));
            }
            for (const warning of row.warnings)
                line.appendChild(el('div', 'warn', warning));
            card.appendChild(line);
        }
        resultEl.insertBefore(card, resultEl.firstChild);
    }
    // ------------------------------------------------------------- detection
    async function activeTab() {
        const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
        return tabs.length ? tabs[0] : null;
    }
    /**
     * Ask the content script a question. If it is not there (the extension was
     * reloaded after the page was opened), inject it once and ask again.
     */
    async function callContentScript(tabId, request) {
        try {
            const response = (await chrome.tabs.sendMessage(tabId, request));
            if (response && response.ok && response.result !== undefined)
                return response.result;
            throw new Error(response?.error || 'empty_response');
        }
        catch {
            await chrome.scripting.executeScript({
                target: { tabId },
                files: ['dist/boss/selectors.js', 'dist/boss/extract.js', 'dist/content.js'],
            });
            const retry = (await chrome.tabs.sendMessage(tabId, request));
            if (retry && retry.ok && retry.result !== undefined)
                return retry.result;
            throw new Error(retry?.error || '内容脚本没有响应');
        }
    }
    async function detect() {
        detection = null;
        previewBtn.disabled = true;
        resultEl.innerHTML = '';
        diagnosticOutput.textContent = '';
        diagnosticCopyBtn.disabled = true;
        updateDiagnosticAvailability();
        setStatus('正在读取当前页面…');
        try {
            const tab = await activeTab();
            if (!tab || tab.id === undefined) {
                setStatus('找不到当前标签页。', 'error');
                return;
            }
            const url = tab.url || '';
            if (url.indexOf('https://www.zhipin.com/') !== 0 && url.indexOf('https://zhipin.com/') !== 0) {
                detection = {
                    page_type: 'unsupported',
                    url: url.split('?')[0],
                    verification: false,
                    candidates: [],
                    warnings: [],
                    errors: ['当前页面不是 BOSS 直聘（www.zhipin.com）。请先手动打开一个职位或搜索页面。'],
                };
                render();
                updateDiagnosticAvailability();
                setStatus('不支持的页面。', 'warn');
                return;
            }
            detection = await callContentScript(tab.id, { type: 'jobagent:detect' });
            render();
            updateDiagnosticAvailability();
            if (detection.errors.length)
                setStatus('检测完成，但有问题。', 'warn');
            else if (!detection.candidates.length)
                setStatus('页面上没有找到岗位。', 'warn');
            else
                setStatus(`检测完成：${detection.candidates.length} 个岗位。`, 'ok');
        }
        catch (err) {
            setStatus('检测失败：' + String(err?.message || err), 'error');
        }
    }
    // ---------------------------------------------------------- dev diagnostic
    async function diagnose() {
        if (!devMode())
            return;
        diagnosticOutput.textContent = '';
        diagnosticCopyBtn.disabled = true;
        setStatus('正在读取职位详情页附近的结构…');
        try {
            const tab = await activeTab();
            if (!tab || tab.id === undefined) {
                setStatus('找不到当前标签页。', 'error');
                return;
            }
            const result = await callContentScript(tab.id, {
                type: 'jobagent:diagnose',
            });
            diagnosticOutput.textContent = JSON.stringify(result, null, 2);
            diagnosticCopyBtn.disabled = false;
            if (result.errors.length)
                setStatus('结构诊断完成，但有问题。', 'warn');
            else if (result.truncated)
                setStatus('结构诊断完成（已达到节点上限，结果已截断）。', 'warn');
            else
                setStatus('结构诊断完成，可点击「复制诊断结果」。', 'ok');
        }
        catch (err) {
            setStatus('结构诊断失败：' + String(err?.message || err), 'error');
        }
    }
    async function copyDiagnosticOutput() {
        const value = diagnosticOutput.textContent || '';
        if (!value)
            return;
        try {
            await navigator.clipboard.writeText(value);
            setStatus('诊断结果已复制到剪贴板。', 'ok');
        }
        catch {
            setStatus('自动复制失败，请手动选中诊断结果文本复制。', 'error');
        }
    }
    // ------------------------------------------------------- backend requests
    async function post(path, body) {
        const response = await fetch(JobAgentConfig.BACKEND_BASE + path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const text = await response.text();
        let payload = null;
        try {
            payload = text ? JSON.parse(text) : null;
        }
        catch {
            payload = null;
        }
        if (!response.ok) {
            const message = payload?.message || `请求失败（HTTP ${response.status}）`;
            throw new Error(message);
        }
        return payload;
    }
    async function preview() {
        if (!detection)
            return;
        setStatus('正在发送到本地 JobAgent…');
        try {
            const response = (await post(JobAgentConfig.PREVIEW_PATH, {
                page_type: detection.page_type,
                page_url: detection.url,
                candidates: detection.candidates,
            }));
            renderPreview(response);
            setStatus(`预览完成：新 ${response.new_count} / 重复 ${response.duplicate_count}。什么都还没有保存。`, 'ok');
        }
        catch (err) {
            setStatus('无法连接本地 JobAgent：' +
                String(err?.message || err) +
                '（后端需要在 127.0.0.1:8000 运行）', 'error');
        }
    }
    async function importOne(index, button) {
        if (!detection)
            return;
        const candidate = detection.candidates[index];
        if (!candidate)
            return;
        button.disabled = true;
        setStatus('正在导入…');
        try {
            const response = (await post(JobAgentConfig.IMPORT_PATH, {
                confirmed: true,
                candidate,
            }));
            button.textContent = response.duplicate ? '已存在' : '已导入';
            setStatus(response.message, response.duplicate ? 'warn' : 'ok');
        }
        catch (err) {
            button.disabled = false;
            setStatus('导入失败：' + String(err?.message || err), 'error');
        }
    }
    // ------------------------------------------------------------------ wire
    detectBtn.addEventListener('click', () => void detect());
    previewBtn.addEventListener('click', () => void preview());
    diagnoseBtn.addEventListener('click', () => void diagnose());
    diagnosticCopyBtn.addEventListener('click', () => void copyDiagnosticOutput());
    devToggle.addEventListener('change', () => {
        render();
        updateDiagnosticAvailability();
    });
    updateDiagnosticAvailability();
    setStatus('打开一个 BOSS 页面后点「检测当前页面」。不会自动读取任何内容。');
})();
