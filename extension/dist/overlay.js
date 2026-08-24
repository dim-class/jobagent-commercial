"use strict";
/**
 * M4b fixed session bar for the approved BOSS tab - task/caps/progress, a
 * Stop control, and (M4b, explicitly authorized - CLAUDE.md "Chrome
 * extension - M4 supervised navigation policy") a "下一位候选人" control that
 * opens one already-rendered search-result card at a time.
 *
 * This content script makes NO network calls and contains no backend URL:
 * MV3 content scripts cannot reliably cross-origin fetch a loopback address
 * from an `https://` page - that was the actual cause of a prior M4a bug.
 * All loopback HTTP lives in `background.ts`; this script only exchanges
 * narrow runtime messages with it and renders/removes UI based on the
 * answer. It never touches `chrome.storage.session` directly either.
 *
 * The navigation state machine is prepare-then-confirm, never click-then-
 * ask: `jobagent:navigate-prepare` must return `ok: true` *before* this
 * script clicks anything (a denial - cap reached, session stopped, wrong
 * origin - clicks nothing at all), and `jobagent:navigate-confirm` reports
 * the click's *real* outcome afterward - a failed click is confirmed as
 * `outcome: "failed"` and never advances any counter.
 *
 * Every applicable policy hard stop below - verification/CAPTCHA/login/
 * security/rate-limit, wrong origin/page shape, no candidates left/a
 * detected loop, a denied prepare, a failed click, or a confirm call that
 * itself failed - actually ends the session: it POSTs stop through
 * `background.ts` (which clears the pointer only after that stop is
 * confirmed), then removes the navigation controls so nothing can be
 * clicked again. None of them retry automatically; the human must start a
 * new session for another attempt. `user_stop` (the Stop button) and
 * `stale_tab` (M4a's reload/restart fail-closed path) are unchanged.
 *
 * No timer, no polling, no MutationObserver. It never scrolls, paginates,
 * searches, applies (立即沟通), messages, follows/collects, or reads
 * cookies/storage/forms - the only DOM mutation it ever performs is one
 * `.click()` on an already-rendered card link, done by `boss/extract.ts`'s
 * `openCandidateLink`, never a scroll to find one. See CLAUDE.md's "Chrome
 * extension - M4 supervised navigation policy" and
 * docs/orchestration/ROADMAP.md M4b.
 */
;
(function () {
    const REQUIRED_ORIGIN = 'https://www.zhipin.com';
    const BAR_ID = 'jobagent-session-bar';
    const STATUS_ID = 'jobagent-session-status-line';
    //: Cards already opened (success) or already tried and failed this page
    //: load - the loop guard.
    const handledUrls = new Set();
    const NEXT_BTN_ID = 'jobagent-next-candidate-btn';
    //: Synchronous in-flight guard against a rapid double-click starting a
    //: second concurrent `openNextCandidate()` - set/checked before any
    //: `await`, so two clicks in the same tick can never both pass it.
    let navigationInFlight = false;
    function setNextButtonDisabled(disabled) {
        const btn = document.getElementById(NEXT_BTN_ID);
        if (btn)
            btn.disabled = disabled;
    }
    function currentOrigin() {
        return document.location.protocol + '//' + document.location.host;
    }
    function askBackground(message) {
        return new Promise((resolve) => {
            try {
                chrome.runtime.sendMessage(message, (response) => resolve((response || {})));
            }
            catch {
                resolve({});
            }
        });
    }
    function removeBar() {
        const bar = document.getElementById(BAR_ID);
        if (bar)
            bar.remove();
        const status = document.getElementById(STATUS_ID);
        if (status)
            status.remove();
    }
    function setStatusLine(text) {
        let el = document.getElementById(STATUS_ID);
        if (!el) {
            el = document.createElement('div');
            el.id = STATUS_ID;
            el.setAttribute('style', 'position:fixed;top:34px;left:0;right:0;z-index:2147483646;' +
                'background:#2f6df6;color:#fff;font:11px/1.6 -apple-system,BlinkMacSystemFont,' +
                '"Segoe UI","Microsoft YaHei",sans-serif;padding:3px 12px;');
            document.documentElement.appendChild(el);
        }
        el.textContent = text;
    }
    /**
     * The one path every policy hard stop goes through: POST stop for real
     * (through background.ts, which clears the pointer only once the backend
     * confirms), remove the navigation controls so nothing is clickable
     * again, then show why - in that order, so the message is not wiped by
     * removing the bar.
     *
     * If the stop POST itself is not confirmed, the pointer (and likely the
     * backend session) are unchanged - claiming the session ended and pulling
     * the bar would strand the human with no Stop control at all. So on an
     * unconfirmed stop the bar stays, "下一位候选人" stays disabled (the
     * violation that triggered this is still real), and the status line says
     * so truthfully; only the still-present Stop button may retry - nothing
     * here retries automatically.
     */
    async function hardStop(reason, message) {
        const result = await askBackground({ type: 'jobagent:stop-session', reason });
        if (!result.ok) {
            setNextButtonDisabled(true);
            setStatusLine(message + '（后台未确认停止，会话可能仍在运行；请点击"停止会话"重试，不会自动重试。）');
            return;
        }
        const bar = document.getElementById(BAR_ID);
        if (bar)
            bar.remove();
        setStatusLine(message);
    }
    function progressText(session) {
        return (`JobAgent 受监督会话（M4b）· 任务「${session.approved_criteria.task_name}」· ` +
            `页面 ${session.pages_visited}/${session.page_cap} · ` +
            `候选人 ${session.candidates_extracted}/${session.candidate_cap} · ` +
            `不会翻页/滚动/投递/发消息`);
    }
    function renderBar(session, onStop, onNext) {
        removeBar();
        const bar = document.createElement('div');
        bar.id = BAR_ID;
        bar.setAttribute('style', 'position:fixed;top:0;left:0;right:0;z-index:2147483647;' +
            'background:#1a1d23;color:#fff;font:12px/1.6 -apple-system,BlinkMacSystemFont,' +
            '"Segoe UI","Microsoft YaHei",sans-serif;padding:6px 12px;display:flex;' +
            'align-items:center;gap:10px;box-shadow:0 1px 4px rgba(0,0,0,.3);');
        const label = document.createElement('span');
        label.style.flex = '1';
        label.textContent = progressText(session);
        bar.appendChild(label);
        const nextBtn = document.createElement('button');
        nextBtn.type = 'button';
        nextBtn.id = NEXT_BTN_ID;
        nextBtn.textContent = '下一位候选人';
        nextBtn.disabled = session.candidates_extracted >= session.candidate_cap;
        nextBtn.setAttribute('style', 'padding:3px 12px;border-radius:4px;border:1px solid #fff;background:transparent;' +
            'color:#fff;cursor:pointer;font:inherit;');
        nextBtn.addEventListener('click', () => void onNext());
        bar.appendChild(nextBtn);
        const stopBtn = document.createElement('button');
        stopBtn.type = 'button';
        stopBtn.textContent = '停止会话';
        stopBtn.setAttribute('style', 'padding:3px 12px;border-radius:4px;border:1px solid #fff;background:transparent;' +
            'color:#fff;cursor:pointer;font:inherit;');
        stopBtn.addEventListener('click', () => void onStop());
        bar.appendChild(stopBtn);
        document.documentElement.appendChild(bar);
    }
    async function stopFromBar() {
        const result = await askBackground({
            type: 'jobagent:stop-session',
            reason: 'user_stop',
        });
        if (result.ok)
            removeBar();
        // If not confirmed, the session (and the pointer) are unchanged in the
        // worker - leave the bar as-is rather than guessing at a new state.
    }
    /**
     * The one explicit, human-triggered "Continue" step: read the page fresh,
     * hard-stop the whole session on every applicable policy violation, ask
     * the worker to *authorize* one click, only then click, then report the
     * click's real outcome. No part of this repeats automatically - each call
     * is exactly one human click, and every hard stop below ends the session
     * rather than merely refusing this one step.
     */
    async function openNextCandidate() {
        // Synchronous guard, checked and set before any `await`: a second click
        // arriving while a step is already running (rapid double-click) does no
        // fetch and no click at all - it is simply dropped.
        if (navigationInFlight)
            return;
        navigationInFlight = true;
        setNextButtonDisabled(true);
        try {
            await openNextCandidateStep();
        }
        finally {
            navigationInFlight = false;
        }
    }
    async function openNextCandidateStep() {
        if (currentOrigin() !== REQUIRED_ORIGIN) {
            await hardStop('wrong_origin', '当前标签页已不是 https://www.zhipin.com，会话已结束。');
            return;
        }
        const detection = BossContentScript.handle({ type: BossContentScript.DETECT });
        if (!detection.ok || !detection.result) {
            // Inconclusive, not a confirmed violation - e.g. the page may still
            // be loading. Never treat "could not check" as a policy stop; the
            // human can just click again once the page has settled.
            setStatusLine('无法读取当前页面，请稍后重试。');
            setNextButtonDisabled(false);
            return;
        }
        const page = detection.result;
        // Hard stop: CAPTCHA / identity / security-risk interstitial, and the
        // same signal covers BOSS's "访问过于频繁" rate-limit wording - see
        // boss/selectors.ts VERIFICATION_HINTS.
        if (page.verification) {
            await hardStop('verification', 'BOSS 正在要求安全验证或提示访问过于频繁，请自行处理，会话已结束（不会重试）。');
            return;
        }
        // Hard stop: wrong page shape for this step.
        if (page.page_type !== 'search') {
            await hardStop('wrong_page', '当前不是搜索结果页，无法选择候选人，会话已结束。');
            return;
        }
        const next = page.candidates.findIndex((c) => c.title && c.source_url && !handledUrls.has(c.source_url));
        // Hard stop: nothing left to open without scrolling/paginating, which
        // this milestone never does.
        if (next === -1) {
            await hardStop('no_candidates', '当前页面上没有更多可打开的候选人（不会翻页或滚动查找更多），会话已结束。');
            return;
        }
        const targetUrl = page.candidates[next].source_url;
        // Hard stop / loop guard: never re-open a URL already handled.
        if (handledUrls.has(targetUrl)) {
            await hardStop('loop_detected', '检测到重复的候选人链接，会话已结束（不会重复打开）。');
            return;
        }
        setStatusLine('正在申请打开下一位候选人…');
        const prepared = await askBackground({
            type: 'jobagent:navigate-prepare',
            target: 'detail',
            pageUrl: targetUrl,
        });
        if (!prepared.ok) {
            // Hard stop: cap reached, session stopped, wrong origin, or this tab
            // does not own the session - click nothing at all.
            await hardStop('prepare_denied', '未获得授权，不会点击，会话已结束：' + (prepared.error || '未知原因'));
            return;
        }
        const openResult = BossContentScript.handle({
            type: BossContentScript.OPEN_CANDIDATE,
            index: next,
        });
        const clicked = !!openResult.ok && !!openResult.result?.ok;
        handledUrls.add(targetUrl);
        const confirmed = await askBackground({
            type: 'jobagent:navigate-confirm',
            target: 'detail',
            pageUrl: targetUrl,
            outcome: clicked ? 'success' : 'failed',
            error: clicked ? null : openResult.result?.error || 'unknown',
        });
        if (!clicked) {
            // Hard stop: selector ambiguity / out-of-range / not clickable - the
            // click was confirmed failed above (never counted); now end the
            // session rather than let the human keep guessing.
            await hardStop('click_failed', '无法唯一定位该候选人的链接，会话已结束（不会猜测点击）：' +
                (openResult.result?.error || 'unknown'));
            return;
        }
        if (!confirmed.ok) {
            // Hard stop: the click really happened, but the backend could not be
            // told, so the recorded candidate count can no longer be trusted -
            // stop rather than risk exceeding the approved cap unaccounted for.
            await hardStop('confirm_failed', '候选人已打开，但未能确认写入后台，会话已结束（避免上限计数失真）。');
            return;
        }
        if (confirmed.session) {
            renderBar(confirmed.session, () => void stopFromBar(), () => void openNextCandidate());
        }
        setStatusLine('已打开候选人详情页，请用"检测当前页面"确认并按需导入。');
    }
    async function init() {
        if (currentOrigin() !== REQUIRED_ORIGIN)
            return;
        const snapshot = await askBackground({ type: 'jobagent:session-snapshot' });
        if (snapshot.kind === 'session') {
            renderBar(snapshot.session, () => void stopFromBar(), () => void openNextCandidate());
        }
        // 'other_tab' | 'none' | 'unreachable' -> show nothing this pass.
    }
    // Guard against a double registration if this script is ever injected
    // twice into the same isolated world.
    const flag = '__jobagentSessionBarInstalled';
    const globals = window;
    if (!globals[flag]) {
        globals[flag] = true;
        chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
            const msg = (message || {});
            if (msg.type === 'jobagent:session-started' && msg.session) {
                const session = msg.session;
                renderBar(session, () => void stopFromBar(), () => void openNextCandidate());
                sendResponse({ ok: true });
                return false;
            }
            if (msg.type === 'jobagent:session-stopped') {
                removeBar();
                sendResponse({ ok: true });
                return false;
            }
            return false;
        });
    }
    void init();
})();
