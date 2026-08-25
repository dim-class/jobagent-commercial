"use strict";
/**
 * M4b/M4c fixed session bar for the approved BOSS tab - task/caps/progress,
 * a Stop control, and three explicitly-authorized human-click controls
 * (CLAUDE.md "Chrome extension - M4 supervised navigation policy"):
 * "下一位候选人" opens one already-rendered search-result card; "向下滚动"
 * performs one bounded scroll step on the results container; "下一页"
 * activates one same-origin pagination control. Every one of the three is
 * exactly one human click - none of them chain, retry, or wait for another.
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
 * script clicks or scrolls anything (a denial - cap reached, session
 * stopped, wrong origin - performs nothing at all), and
 * `jobagent:navigate-confirm` reports the step's *real* outcome afterward -
 * a failed step is confirmed as `outcome: "failed"` and never advances any
 * counter.
 *
 * Every applicable policy hard stop below - verification/CAPTCHA/login/
 * security/rate-limit, wrong origin/page shape, no candidates left/a
 * detected loop (candidate or results-page), a denied prepare, a failed
 * step, or a confirm call that itself failed - actually ends the session: it
 * POSTs stop through `background.ts` (which clears the pointer only after
 * that stop is confirmed), then removes the navigation controls so nothing
 * can be clicked again. None of them retry automatically; the human must
 * start a new session for another attempt. `user_stop` (the Stop button)
 * and `stale_tab` (M4a's reload/restart fail-closed path) are unchanged.
 *
 * No timer, no polling, no MutationObserver, no auto-scroll/page/chain. It
 * never searches, applies (立即沟通), messages, follows/collects, or reads
 * cookies/storage/forms - the only DOM mutations it ever performs are one
 * bounded scroll (`boss/extract.ts`'s `scrollResultsContainer`) and one
 * `.click()` on an already-rendered card link or pagination control
 * (`openCandidateLink`/`activateNextPage`, the same single click primitive).
 * See CLAUDE.md's "Chrome extension - M4 supervised navigation policy" and
 * docs/orchestration/ROADMAP.md M4b/M4c.
 */
;
(function () {
    const REQUIRED_ORIGIN = 'https://www.zhipin.com';
    const BAR_ID = 'jobagent-session-bar';
    const STATUS_ID = 'jobagent-session-status-line';
    //: Cards already opened (success) or already tried and failed this page
    //: load - the loop guard.
    const handledUrls = new Set();
    //: M4c's own loop guard, the same shape as `handledUrls` above but for
    //: results pages: the URL seen right before each pagination *attempt*.
    //: If that URL is already in the set, the tab is back on a page already
    //: paginated from - a client-side pager that silently failed to advance,
    //: or a genuine loop - either way, refused rather than paginated again.
    //: This Set is wiped by a full-page pagination navigation (a fresh script
    //: instance starts empty), so it only protects one unbroken script
    //: lifetime - `goToNextPageStep`'s `document.referrer` check is the
    //: reload-safe complement, catching a same-page bounce-back even then.
    const handledPageUrls = new Set();
    const NEXT_BTN_ID = 'jobagent-next-candidate-btn';
    const CAPTURE_BTN_ID = 'jobagent-capture-detail-btn';
    const SCROLL_BTN_ID = 'jobagent-scroll-step-btn';
    const PAGE_BTN_ID = 'jobagent-next-page-btn';
    //: Synchronous in-flight guards against a rapid double-click starting a
    //: second concurrent step - each set/checked before any `await`, so two
    //: clicks in the same tick can never both pass. `anyStepInFlight()` below
    //: additionally makes the four steps mutually exclusive: only one of
    //: opening a candidate, capturing, scrolling or paginating may run at a
    //: time, never two different steps interleaved.
    let navigationInFlight = false;
    let captureInFlight = false;
    let scrollInFlight = false;
    let paginateInFlight = false;
    function anyStepInFlight() {
        return navigationInFlight || captureInFlight || scrollInFlight || paginateInFlight;
    }
    //: The exact card phase one (`openNextCandidate`) just opened - the only
    //: thing phase two (`captureSelectedDetail`) is allowed to verify against
    //: and merge with. Cleared the moment it is consumed (sent, or the
    //: session/page it belongs to is no longer valid) so a stale capture can
    //: never fire against a candidate that is no longer the one on screen.
    //:
    //: While this is set, "下一位候选人" must stay disabled - a real logged-in
    //: Chrome run found that cap math alone let repeated Next clicks overwrite
    //: this before it was ever captured, silently dropping every candidate but
    //: the last. `nextAllowed()` below is the one place that decision is made;
    //: every render and every state change after a capture attempt goes
    //: through it, and `openNextCandidateStep` also refuses outright - as
    //: defense in depth, not just a disabled attribute - to open a second
    //: candidate while one is still pending.
    let pendingCapture = null;
    //: The most recently known session, kept in step with `pendingCapture` so
    //: `nextAllowed()` can be recomputed after a capture attempt without a
    //: full bar re-render (which would otherwise be the only place cap state
    //: is read).
    let lastSession = null;
    /** Whether "下一位候选人" may open another candidate right now: never while
     * one is still pending capture, and never at or above the approved cap. */
    function nextAllowed(session) {
        if (!session)
            return false;
        if (pendingCapture)
            return false;
        return session.candidates_extracted < session.candidate_cap;
    }
    /** Whether "向下滚动" may scroll right now: same pending-capture rule as
     * every other action button, and never at or above the per-page cap -
     * that cap resets to 0 only on a confirmed pagination (see
     * `services/supervised_sessions.py`'s `navigate_confirm`). */
    function scrollAllowed(session) {
        if (!session)
            return false;
        if (pendingCapture)
            return false;
        return session.scrolls_used < session.scroll_cap;
    }
    /** Whether "下一页" may paginate right now: same pending-capture rule,
     * never at or above the page cap (which already counts the session's
     * starting page - see `create_session`). */
    function nextPageAllowed(session) {
        if (!session)
            return false;
        if (pendingCapture)
            return false;
        return session.pages_visited < session.page_cap;
    }
    function setNextButtonDisabled(disabled) {
        const btn = document.getElementById(NEXT_BTN_ID);
        if (btn)
            btn.disabled = disabled;
    }
    function setCaptureButtonDisabled(disabled) {
        const btn = document.getElementById(CAPTURE_BTN_ID);
        if (btn)
            btn.disabled = disabled;
    }
    function setScrollButtonDisabled(disabled) {
        const btn = document.getElementById(SCROLL_BTN_ID);
        if (btn)
            btn.disabled = disabled;
    }
    function setPageButtonDisabled(disabled) {
        const btn = document.getElementById(PAGE_BTN_ID);
        if (btn)
            btn.disabled = disabled;
    }
    /** Re-derive every action button's disabled state from current
     * `pendingCapture` + `lastSession`, for the moments (a capture attempt
     * finishing, a scroll/page confirm) that change one of those without
     * re-rendering the whole bar. The single source of truth for all three is
     * `nextAllowed`/`scrollAllowed`/`nextPageAllowed` above - nothing here
     * re-derives the rule a second, possibly-drifting way. */
    function refreshActionButtons() {
        setNextButtonDisabled(!nextAllowed(lastSession));
        setScrollButtonDisabled(!scrollAllowed(lastSession));
        setPageButtonDisabled(!nextPageAllowed(lastSession));
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
        // Whatever was pending is no longer safe to send under any outcome
        // below - a confirmed stop ends the session outright, and even an
        // unconfirmed one means the violation that triggered this is still real.
        pendingCapture = null;
        setCaptureButtonDisabled(true);
        const result = await askBackground({ type: 'jobagent:stop-session', reason });
        if (!result.ok) {
            setNextButtonDisabled(true);
            setScrollButtonDisabled(true);
            setPageButtonDisabled(true);
            setStatusLine(message + '（后台未确认停止，会话可能仍在运行；请点击"停止会话"重试，不会自动重试。）');
            return;
        }
        const bar = document.getElementById(BAR_ID);
        if (bar)
            bar.remove();
        setStatusLine(message);
    }
    function progressText(session) {
        return (`JobAgent 受监督会话（M4b/M4c）· 任务「${session.approved_criteria.task_name}」· ` +
            `页面 ${session.pages_visited}/${session.page_cap} · ` +
            `本页滚动 ${session.scrolls_used}/${session.scroll_cap} · ` +
            `候选人 ${session.candidates_extracted}/${session.candidate_cap} · ` +
            `不会自动翻页/滚动/投递/发消息`);
    }
    function renderBar(session, onStop, onNext, onCapture, onScroll, onNextPage) {
        lastSession = session;
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
        // Cap alone is not the whole story: a candidate still waiting on
        // "捕获详情" must keep this disabled too - see `nextAllowed`.
        nextBtn.disabled = !nextAllowed(session);
        nextBtn.setAttribute('style', 'padding:3px 12px;border-radius:4px;border:1px solid #fff;background:transparent;' +
            'color:#fff;cursor:pointer;font:inherit;');
        nextBtn.addEventListener('click', () => void onNext());
        bar.appendChild(nextBtn);
        const captureBtn = document.createElement('button');
        captureBtn.type = 'button';
        captureBtn.id = CAPTURE_BTN_ID;
        captureBtn.textContent = '捕获详情';
        // Only enabled once phase one has actually opened a candidate on this
        // page - a fresh bar render (session start, or a reconnect after this
        // script re-injects) never has anything pending to capture yet.
        captureBtn.disabled = !pendingCapture;
        captureBtn.setAttribute('style', 'padding:3px 12px;border-radius:4px;border:1px solid #fff;background:transparent;' +
            'color:#fff;cursor:pointer;font:inherit;');
        captureBtn.addEventListener('click', () => void onCapture());
        bar.appendChild(captureBtn);
        const scrollBtn = document.createElement('button');
        scrollBtn.type = 'button';
        scrollBtn.id = SCROLL_BTN_ID;
        scrollBtn.textContent = '向下滚动';
        scrollBtn.disabled = !scrollAllowed(session);
        scrollBtn.setAttribute('style', 'padding:3px 12px;border-radius:4px;border:1px solid #fff;background:transparent;' +
            'color:#fff;cursor:pointer;font:inherit;');
        scrollBtn.addEventListener('click', () => void onScroll());
        bar.appendChild(scrollBtn);
        const pageBtn = document.createElement('button');
        pageBtn.type = 'button';
        pageBtn.id = PAGE_BTN_ID;
        pageBtn.textContent = '下一页';
        pageBtn.disabled = !nextPageAllowed(session);
        pageBtn.setAttribute('style', 'padding:3px 12px;border-radius:4px;border:1px solid #fff;background:transparent;' +
            'color:#fff;cursor:pointer;font:inherit;');
        pageBtn.addEventListener('click', () => void onNextPage());
        bar.appendChild(pageBtn);
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
        if (result.ok) {
            pendingCapture = null;
            lastSession = null;
            removeBar();
        }
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
        // arriving while a step is already running (rapid double-click, or a
        // click on a different action button) does no fetch and no click at
        // all - it is simply dropped. See `anyStepInFlight`.
        if (anyStepInFlight())
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
        // Defense in depth, not just the disabled attribute: a real logged-in
        // Chrome run showed repeated Next clicks reaching here while a candidate
        // still awaited "捕获详情", silently overwriting it. Refuse outright -
        // no page read, no prepare, no click - rather than trust only the button
        // state a stray or racing click might bypass.
        if (pendingCapture) {
            setStatusLine('还有候选人等待"捕获详情"，请先完成捕获再打开下一位。');
            refreshActionButtons();
            return;
        }
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
        // Phase one is done: cache exactly the card just opened (never anything
        // re-derived later) so phase two has one unambiguous identity to verify
        // the pane against.
        pendingCapture = page.candidates[next];
        if (confirmed.session) {
            renderBar(confirmed.session, () => void stopFromBar(), () => void openNextCandidate(), () => void captureSelectedDetail(), () => void scrollOnePage(), () => void goToNextPage());
        }
        setCaptureButtonDisabled(false);
        setStatusLine('已打开候选人详情页，请等右侧详情加载后点击"捕获详情"。');
    }
    /**
     * M4b phase two, the human's second click: read the pane the previous
     * click opened, verify it is really `pendingCapture` (never guessed - see
     * `boss/extract.ts`'s `captureAndMerge`), merge, and send only to the
     * existing loopback preview path. Nothing here imports; the human still
     * reviews and imports separately, exactly as `popup.ts` already works.
     */
    async function captureSelectedDetail() {
        if (anyStepInFlight())
            return;
        captureInFlight = true;
        setCaptureButtonDisabled(true);
        try {
            await captureSelectedDetailStep();
        }
        finally {
            captureInFlight = false;
        }
    }
    async function captureSelectedDetailStep() {
        const cached = pendingCapture;
        if (!cached || !cached.source_url) {
            setStatusLine('没有待捕获的候选人，请先点击"下一位候选人"。');
            return;
        }
        if (currentOrigin() !== REQUIRED_ORIGIN) {
            await hardStop('wrong_origin', '当前标签页已不是 https://www.zhipin.com，会话已结束。');
            return;
        }
        // Confirm this tab still owns a running session before sending anything
        // - a stopped/reassigned session must never receive a capture.
        const snapshot = await askBackground({ type: 'jobagent:session-snapshot' });
        if (snapshot.kind !== 'session') {
            pendingCapture = null;
            setCaptureButtonDisabled(true);
            setStatusLine('会话已结束或不属于当前标签页，捕获已取消（不会发送）。');
            return;
        }
        // Keep cap numbers fresh for `nextAllowed` even though capture itself
        // never changes them - the session-snapshot answer is the most current
        // one available.
        lastSession = snapshot.session;
        const captured = BossContentScript.handle({
            type: BossContentScript.CAPTURE_DETAIL,
            canonicalUrl: cached.source_url,
            cachedCard: cached,
        });
        if (!captured.ok || !captured.result) {
            setStatusLine('读取详情面板失败，请重试。');
            setCaptureButtonDisabled(false);
            return;
        }
        const result = captured.result;
        if (result.status === 'verification') {
            await hardStop('verification', 'BOSS 正在要求安全验证或提示访问过于频繁，请自行处理，会话已结束（不会重试）。');
            return;
        }
        if (result.status === 'not_loaded') {
            // Inconclusive, not a confirmed violation - the pane may still be
            // loading. Let the human simply try again.
            setStatusLine('详情面板还没有加载完成，请稍候再点击"捕获详情"。');
            setCaptureButtonDisabled(false);
            return;
        }
        if (result.status === 'identity_mismatch') {
            await hardStop('identity_mismatch', '捕获到的详情与刚打开的候选人不一致，会话已结束（不会猜测匹配，也不会发送）。');
            return;
        }
        if (!result.candidate) {
            setStatusLine('捕获失败，请重试。');
            setCaptureButtonDisabled(false);
            return;
        }
        setStatusLine('正在发送预览…');
        const sent = await askBackground({
            type: 'jobagent:send-preview',
            pageType: 'detail',
            pageUrl: result.candidate.source_url,
            candidate: result.candidate,
        });
        if (!sent.ok) {
            // Recoverable - the pane is presumably still visible, so the human
            // may just click "捕获详情" again; nothing here retries by itself.
            setStatusLine('发送预览失败：' + (sent.error || '未知错误') + '（不会自动重试，可再次点击"捕获详情"）。');
            setCaptureButtonDisabled(false);
            return;
        }
        pendingCapture = null;
        setCaptureButtonDisabled(true);
        // Only now - capture actually sent - may Next/scroll/page unlock again,
        // and then only if their own approved cap has not been reached.
        refreshActionButtons();
        const counts = sent.preview;
        setStatusLine(counts
            ? `已发送预览：新 ${counts.new_count} / 重复 ${counts.duplicate_count}。什么都还没有保存，如需导入请另行打开弹窗操作。`
            : '已发送预览。什么都还没有保存。');
    }
    /**
     * M4c (CLAUDE.md "Chrome extension - M4 supervised navigation policy",
     * explicitly authorized). One bounded scroll step, gated exactly like
     * `openNextCandidateStep`: refuse outright with a candidate pending,
     * re-check origin/page-shape/verification fresh, ask the backend to
     * *authorize* before performing anything, then report the real outcome.
     */
    async function scrollOnePage() {
        if (anyStepInFlight())
            return;
        scrollInFlight = true;
        setScrollButtonDisabled(true);
        try {
            await scrollOnePageStep();
        }
        finally {
            scrollInFlight = false;
        }
    }
    async function scrollOnePageStep() {
        if (pendingCapture) {
            setStatusLine('还有候选人等待"捕获详情"，请先完成捕获再滚动。');
            refreshActionButtons();
            return;
        }
        if (currentOrigin() !== REQUIRED_ORIGIN) {
            await hardStop('wrong_origin', '当前标签页已不是 https://www.zhipin.com，会话已结束。');
            return;
        }
        const detection = BossContentScript.handle({ type: BossContentScript.DETECT });
        if (!detection.ok || !detection.result) {
            setStatusLine('无法读取当前页面，请稍后重试。');
            setScrollButtonDisabled(false);
            return;
        }
        const page = detection.result;
        if (page.verification) {
            await hardStop('verification', 'BOSS 正在要求安全验证或提示访问过于频繁，请自行处理，会话已结束（不会重试）。');
            return;
        }
        if (page.page_type !== 'search') {
            await hardStop('wrong_page', '当前不是搜索结果页，无法滚动，会话已结束。');
            return;
        }
        setStatusLine('正在申请滚动…');
        const prepared = await askBackground({
            type: 'jobagent:navigate-prepare',
            target: 'scroll',
            pageUrl: null,
        });
        if (!prepared.ok) {
            // Hard stop: this page's scroll cap reached, session stopped, wrong
            // origin, or this tab does not own the session - scroll nothing.
            await hardStop('prepare_denied', '未获得授权，不会滚动，会话已结束：' + (prepared.error || '未知原因'));
            return;
        }
        const scrolled = BossContentScript.handle({
            type: BossContentScript.SCROLL_STEP,
        });
        const succeeded = !!scrolled.ok && !!scrolled.result?.ok;
        const confirmed = await askBackground({
            type: 'jobagent:navigate-confirm',
            target: 'scroll',
            pageUrl: null,
            outcome: succeeded ? 'success' : 'failed',
            error: succeeded ? null : scrolled.result?.error || 'unknown',
        });
        if (!succeeded) {
            // Hard stop: no unique scrollable container - the scroll was
            // confirmed failed above (never counted); end the session rather than
            // let the human keep guessing at a page that cannot be scrolled.
            await hardStop('scroll_failed', '无法唯一定位可滚动的结果容器，会话已结束（不会猜测滚动）：' +
                (scrolled.result?.error || 'unknown'));
            return;
        }
        if (!confirmed.ok) {
            await hardStop('confirm_failed', '已滚动，但未能确认写入后台，会话已结束（避免上限计数失真）。');
            return;
        }
        if (confirmed.session) {
            renderBar(confirmed.session, () => void stopFromBar(), () => void openNextCandidate(), () => void captureSelectedDetail(), () => void scrollOnePage(), () => void goToNextPage());
        }
        setStatusLine('已滚动一步，新出现的候选人可用"下一位候选人"打开。');
    }
    /**
     * M4c's other action: activate one same-origin "next page" control. Same
     * gating shape as `scrollOnePageStep`, plus its own loop guard
     * (`handledPageUrls`) - the M4b candidate loop guard has no bearing on
     * page identity, so pagination needs its own.
     */
    async function goToNextPage() {
        if (anyStepInFlight())
            return;
        paginateInFlight = true;
        setPageButtonDisabled(true);
        try {
            await goToNextPageStep();
        }
        finally {
            paginateInFlight = false;
        }
    }
    async function goToNextPageStep() {
        if (pendingCapture) {
            setStatusLine('还有候选人等待"捕获详情"，请先完成捕获再翻页。');
            refreshActionButtons();
            return;
        }
        if (currentOrigin() !== REQUIRED_ORIGIN) {
            await hardStop('wrong_origin', '当前标签页已不是 https://www.zhipin.com，会话已结束。');
            return;
        }
        const detection = BossContentScript.handle({ type: BossContentScript.DETECT });
        if (!detection.ok || !detection.result) {
            setStatusLine('无法读取当前页面，请稍后重试。');
            setPageButtonDisabled(false);
            return;
        }
        const page = detection.result;
        if (page.verification) {
            await hardStop('verification', 'BOSS 正在要求安全验证或提示访问过于频繁，请自行处理，会话已结束（不会重试）。');
            return;
        }
        if (page.page_type !== 'search') {
            await hardStop('wrong_page', '当前不是搜索结果页，无法翻页，会话已结束。');
            return;
        }
        const currentUrl = document.location.href;
        // Reload-safe loop guard, checked first: a full-page pagination
        // navigation destroys this whole script instance and re-injects a
        // fresh one, wiping `handledPageUrls` below along with every other
        // in-memory Set here - but `document.referrer` is set by the browser
        // itself from the actual previous page, so it survives that reload
        // untouched. A page whose own referrer is itself means the navigation
        // this session just performed went nowhere - a bounce-back loop caught
        // even when no in-memory state carried over. Privacy-safe: only the
        // URL shape already read elsewhere, never a token or page content.
        if (document.referrer && document.referrer === currentUrl) {
            await hardStop('loop_detected', '检测到翻页后又回到同一结果页，会话已结束（不会重复翻页）。');
            return;
        }
        // Same-instance loop guard: a page URL this session has already
        // paginated *from* being reached again (client-side pagination that
        // never actually reloads, so this Set persists across the attempt)
        // means either a pager that silently failed to advance, or a genuine
        // loop - refuse rather than paginate into the same content twice.
        if (handledPageUrls.has(currentUrl)) {
            await hardStop('loop_detected', '检测到重复的搜索结果页，会话已结束（不会重复翻页）。');
            return;
        }
        setStatusLine('正在申请翻页…');
        const prepared = await askBackground({
            type: 'jobagent:navigate-prepare',
            target: 'results',
            pageUrl: null,
        });
        if (!prepared.ok) {
            await hardStop('prepare_denied', '未获得授权，不会翻页，会话已结束：' + (prepared.error || '未知原因'));
            return;
        }
        handledPageUrls.add(currentUrl);
        const paged = BossContentScript.handle({
            type: BossContentScript.NEXT_PAGE,
        });
        const succeeded = !!paged.ok && !!paged.result?.ok;
        const confirmed = await askBackground({
            type: 'jobagent:navigate-confirm',
            target: 'results',
            pageUrl: null,
            outcome: succeeded ? 'success' : 'failed',
            error: succeeded ? null : paged.result?.error || 'unknown',
        });
        if (!succeeded) {
            // Hard stop: no unique/enabled/same-origin next-page control - the
            // click was confirmed failed above (never counted).
            await hardStop('click_failed', '无法唯一定位可用的翻页控件，会话已结束（不会猜测点击）：' +
                (paged.result?.error || 'unknown'));
            return;
        }
        if (!confirmed.ok) {
            await hardStop('confirm_failed', '已翻页，但未能确认写入后台，会话已结束（避免上限计数失真）。');
            return;
        }
        if (confirmed.session) {
            renderBar(confirmed.session, () => void stopFromBar(), () => void openNextCandidate(), () => void captureSelectedDetail(), () => void scrollOnePage(), () => void goToNextPage());
        }
        setStatusLine('已翻页，本页滚动次数已重置，可用"下一位候选人"打开新出现的候选人。');
    }
    async function init() {
        if (currentOrigin() !== REQUIRED_ORIGIN)
            return;
        const snapshot = await askBackground({ type: 'jobagent:session-snapshot' });
        if (snapshot.kind === 'session') {
            renderBar(snapshot.session, () => void stopFromBar(), () => void openNextCandidate(), () => void captureSelectedDetail(), () => void scrollOnePage(), () => void goToNextPage());
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
                renderBar(session, () => void stopFromBar(), () => void openNextCandidate(), () => void captureSelectedDetail(), () => void scrollOnePage(), () => void goToNextPage());
                sendResponse({ ok: true });
                return false;
            }
            if (msg.type === 'jobagent:session-stopped') {
                pendingCapture = null;
                lastSession = null;
                removeBar();
                sendResponse({ ok: true });
                return false;
            }
            return false;
        });
    }
    void init();
})();
