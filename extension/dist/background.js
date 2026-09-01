"use strict";
/**
 * M4a's background worker: the trusted owner of both the session pointer in
 * `chrome.storage.session` and every loopback call to the local JobAgent
 * backend. MV3 content scripts cannot reliably make cross-origin fetches to
 * `http://127.0.0.1:8000` from an `https://` page - that turned out to be
 * the actual cause of a prior bug (reload-time fetches from `overlay.ts`
 * silently failed and the fallback handling still cleared state). All
 * loopback HTTP now lives here, where `manifest.json`'s `host_permissions`
 * actually apply reliably; the overlay content script never fetches
 * anything and never even sees the backend URL.
 *
 * `storage.session`'s default access level (TRUSTED_CONTEXTS) already keeps
 * content scripts out, and this worker never widens that -
 * `storage.session.setAccessLevel` is never called. The overlay only ever
 * gets two narrow, high-level answers - a session snapshot, and a stop
 * result - never the raw pointer or session id belonging to a different
 * tab. Later authorized M4f code below owns bounded navigation; the console
 * amendment adds one explicit visible-tab handoff, never a background scheduler.
 * See CLAUDE.md's "Chrome extension - M4 supervised navigation policy".
 */
const STORAGE_KEY = 'jobagent_session_pointer';
const BACKEND_BASE = 'http://127.0.0.1:8000';
const REQUIRED_ORIGIN = 'https://www.zhipin.com';
async function getGlobalPointer() {
    const stored = await chrome.storage.session.get(STORAGE_KEY);
    return stored[STORAGE_KEY] || null;
}
async function clearGlobalPointer() {
    await chrome.storage.session.remove(STORAGE_KEY);
}
async function fetchJson(path, init) {
    const response = await fetch(BACKEND_BASE + path, {
        method: init?.method || 'GET',
        headers: init?.body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
        body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    });
    const text = await response.text();
    const payload = text ? JSON.parse(text) : null;
    if (!response.ok) {
        const message = payload?.message;
        const err = new Error(message || `HTTP ${response.status}`);
        err.detail = payload?.detail;
        throw err;
    }
    return payload;
}
function fetchActive() {
    return fetchJson('/api/extension/sessions/active');
}
async function postStop(sessionId, reason) {
    await fetchJson(`/api/extension/sessions/${sessionId}/stop`, {
        method: 'POST',
        body: { reason },
    });
}
/** Best effort - a failed stop attempt here must never surface as a thrown
 * error to the caller; the pointer is simply left untouched either way. */
async function tryStopIfRunning(active, reason) {
    if (!active || active.status !== 'running')
        return;
    try {
        await postStop(active.id, reason);
    }
    catch {
        // Nothing more this worker can do right now.
    }
}
async function buildSnapshot(tabId) {
    if (tabId === undefined)
        return { kind: 'none' };
    const pointer = await getGlobalPointer();
    if (pointer && pointer.tabId !== tabId) {
        // A session is approved for a different tab - silent, no backend call
        // at all, and the response never carries that tab's pointer or session id.
        return { kind: 'other_tab' };
    }
    if (!pointer) {
        // No pointer anywhere. Only a *confirmed* still-running backend session
        // is a genuine zombie (browser restart, or storage.session was
        // otherwise cleared) - an unreachable backend proves nothing and must
        // not be treated as confirmation of anything.
        let active;
        try {
            active = await fetchActive();
        }
        catch {
            return { kind: 'none' };
        }
        await tryStopIfRunning(active, 'stale_tab');
        return { kind: 'none' };
    }
    // The pointer belongs to this tab.
    let active;
    try {
        active = await fetchActive();
    }
    catch {
        // Unreachable - preserve the pointer and the backend session untouched;
        // the caller shows nothing this pass and gets a fair chance next time.
        return { kind: 'unreachable' };
    }
    const matches = !!active &&
        active.status === 'running' &&
        active.id === pointer.sessionId &&
        active.tab_origin === REQUIRED_ORIGIN;
    if (matches) {
        return { kind: 'session', session: active };
    }
    // The backend was reachable and confirmed a disagreement - only now is it
    // safe to fail closed for real.
    await tryStopIfRunning(active, 'stale_tab');
    await clearGlobalPointer();
    releasePrepareLock(pointer.tabId);
    return { kind: 'none' };
}
async function stopForTab(tabId, reason = 'user_stop') {
    if (tabId === undefined)
        return { ok: false };
    const pointer = await getGlobalPointer();
    if (!pointer || pointer.tabId !== tabId)
        return { ok: false }; // sender does not own the pointer
    try {
        await postStop(pointer.sessionId, reason);
    }
    catch {
        return { ok: false }; // never clear the pointer on an unconfirmed stop
    }
    await clearGlobalPointer();
    releasePrepareLock(tabId);
    return { ok: true };
}
function isNavigateTarget(value) {
    return value === 'results' || value === 'detail' || value === 'scroll';
}
//: In-memory (never `chrome.storage`) single-flight guard, keyed by owning
//: tabId: a rapid double-click can fire two `jobagent:navigate-prepare`
//: messages before the first `await` in either has a chance to run, so the
//: overlay's own in-flight guard is not the only line of defense - this
//: worker is the trusted source of truth and must independently refuse to
//: double-fetch the backend. Set synchronously (no `await` in between check
//: and set) right before the one real fetch this call may make; released
//: only when the matching `navigate-confirm` arrives or a stop for that tab
//: is confirmed - never on a timer, and never by widening storage access.
const preparePending = new Set();
function releasePrepareLock(tabId) {
    if (tabId !== undefined)
        preparePending.delete(tabId);
}
/**
 * M4b (CLAUDE.md "Chrome extension - M4 supervised navigation policy",
 * explicitly authorized). Authorizes exactly one upcoming click for the
 * *owning* tab only - the same per-tab pointer-ownership check every other
 * handler here uses. Writes nothing on the backend; a non-owning tab (or a
 * tab with no session) is rejected with no fetch at all.
 */
async function navigatePrepareForTab(tabId, target, pageUrl) {
    if (tabId === undefined)
        return { ok: false, error: 'no_tab' };
    const pointer = await getGlobalPointer();
    if (!pointer || pointer.tabId !== tabId)
        return { ok: false, error: 'not_owner' };
    if (preparePending.has(tabId)) {
        // A prepare for this tab is already outstanding (or already succeeded
        // and is still waiting on its matching confirm) - a duplicate call from
        // a rapid double-click gets no second fetch, ever.
        return { ok: false, error: 'prepare_in_flight' };
    }
    preparePending.add(tabId);
    try {
        const session = await fetchJson(`/api/extension/sessions/${pointer.sessionId}/navigate/prepare`, { method: 'POST', body: { target, page_url: pageUrl } });
        return { ok: true, session };
    }
    catch (err) {
        // A denied/failed prepare never gets a matching confirm - the overlay
        // hard-stops and calls stop-session instead, so the lock is released
        // there (once that stop is actually confirmed), not here. Leaving it
        // held is intentional: it is exactly the "release only on confirm or
        // confirmed stop" rule, and it means a second prepare for this tab
        // stays blocked until the session is genuinely known to be over.
        return {
            ok: false,
            error: String(err?.message || err),
            detail: err?.detail,
        };
    }
}
/**
 * Accounts for what the click the extension already attempted actually
 * did. Same per-tab ownership check as prepare/stop - a non-owning tab can
 * never confirm (or fabricate) another tab's navigation.
 */
async function navigateConfirmForTab(tabId, target, pageUrl, outcome, error) {
    if (tabId === undefined)
        return { ok: false, error: 'no_tab' };
    const pointer = await getGlobalPointer();
    if (!pointer || pointer.tabId !== tabId)
        return { ok: false, error: 'not_owner' };
    // The prepare/confirm window is closing either way - release the lock
    // before the fetch so an unrelated later prepare for this tab is never
    // blocked by this call's own outcome.
    releasePrepareLock(tabId);
    try {
        const session = await fetchJson(`/api/extension/sessions/${pointer.sessionId}/navigate/confirm`, { method: 'POST', body: { target, page_url: pageUrl, outcome, error } });
        return { ok: true, session };
    }
    catch (err) {
        return {
            ok: false,
            error: String(err?.message || err),
            detail: err?.detail,
        };
    }
}
/**
 * M4b phase two's one and only backend call: the existing, unchanged
 * `/api/extension/jobs/preview` path - writes nothing, same as every other
 * use of it (`popup.ts`'s manual "检测/预览" flow). Never counts against any
 * session cap and never imports - the human still reviews and imports
 * separately, exactly as before. Same per-tab ownership check as every
 * other handler here: only the tab a running session is approved for may
 * send anything.
 */
async function sendPreviewForTab(tabId, pageType, pageUrl, candidate) {
    if (tabId === undefined)
        return { ok: false, error: 'no_tab' };
    const pointer = await getGlobalPointer();
    if (!pointer || pointer.tabId !== tabId)
        return { ok: false, error: 'not_owner' };
    try {
        const preview = await fetchJson('/api/extension/jobs/preview', {
            method: 'POST',
            body: { page_type: pageType, page_url: pageUrl, candidates: [candidate] },
        });
        return { ok: true, preview };
    }
    catch (err) {
        return { ok: false, error: String(err?.message || err) };
    }
}
// ==========================================================================
// M4e/M4f: bounded automatic runner (CLAUDE.md "Chrome extension - M4
// supervised navigation policy", M4e/M4f amendment - explicitly authorized).
//
// Everything above this section is unchanged M4a-M4c: one human click, one
// bounded backend-authorized step, never a loop. This section is the one
// carve-out from that rule: after a human explicitly starts one approved
// SearchTask, this worker may sequentially navigate the one approved
// foreground tab, wait with *bounded* stabilization polling (a
// configuration-owned timeout and attempt counter - never unbounded), read
// the continuous result list, open/capture/import new candidates one at a
// time, and repeat a bounded number of scroll rounds - all without a
// further click per step. It still reuses every M4a-M4c primitive
// (`navigatePrepareForTab`/`navigateConfirmForTab`, the same
// `SupervisedSession` pointer and caps) rather than inventing a second
// navigation/accounting path, and it still never applies, favorites,
// messages, reads a token/cookie, or uses anything but this extension's own
// content script.
//
// Durable state, not a JS closure, is the source of truth. Every earlier
// version of this section held one mutable `RunnerPointer` object in a
// closure across the whole run and mutated it in place; `requestRunnerPause`/
// `requestRunnerCancel` instead read a *fresh, separate* deserialization of
// `chrome.storage.session` and wrote to that - two different JS objects, so
// the running loop's closure never saw the flag change and pause/cancel were
// silently invisible. Every function below instead takes `(taskId, runToken)`
// and re-reads `chrome.storage.session` at every checkpoint
// (`readCurrent`/`checkpoint`); every write is a fresh-read-then-merge
// (`persistPatch`) so it never blindly overwrites a flag set concurrently.
// `runToken` is a monotonic per-run-attempt id: a fresh `resumeRunner()`
// mints a new one, so any stale/superseded execution's next checkpoint
// reads `readCurrent` -> `null` and quietly stops rather than continuing a
// second concurrent loop. `activeRunToken` is an in-memory single-flight
// guard, checked and claimed synchronously (no `await` in between) at the
// very top of `startRunner`/`resumeRunner`, so two rapid clicks can never
// both start a loop even before either has written anything durable.
const RUNNER_STORAGE_KEY = 'jobagent_runner_pointer';
const BATCH_STORAGE_KEY = 'jobagent_runner_batch';
const BATCH_MAX_TASKS = 16;
function validBatchTaskIds(value) {
    return Array.isArray(value) && value.length >= 1 && value.length <= BATCH_MAX_TASKS
        && value.every(id => Number.isSafeInteger(id) && id > 0)
        && new Set(value).size === value.length;
}
function validBatchPointer(value) {
    if (!value || typeof value !== 'object')
        return false;
    const pointer = value;
    return validBatchTaskIds(pointer.taskIds) && validCandidateCap(pointer.candidateCap)
        && Number.isInteger(pointer.currentIndex) && pointer.currentIndex >= 0
        && pointer.currentIndex < pointer.taskIds.length
        && ['running', 'paused', 'completed', 'stopped'].includes(pointer.state)
        && (pointer.tabId === null || (Number.isSafeInteger(pointer.tabId) && pointer.tabId > 0))
        && (pointer.lastError === null || (typeof pointer.lastError === 'string' && pointer.lastError.length <= 120))
        && typeof pointer.updatedAt === 'string';
}
async function getBatchPointer() {
    const stored = await chrome.storage.session.get(BATCH_STORAGE_KEY);
    const pointer = stored[BATCH_STORAGE_KEY];
    return validBatchPointer(pointer) ? pointer : null;
}
async function setBatchPointer(pointer) {
    if (pointer)
        await chrome.storage.session.set({ [BATCH_STORAGE_KEY]: pointer });
    else
        await chrome.storage.session.remove(BATCH_STORAGE_KEY);
}
async function patchBatchForTask(taskId, patch) {
    const current = await getBatchPointer();
    if (!current || current.taskIds[current.currentIndex] !== taskId)
        return null;
    const updated = { ...current, ...patch, updatedAt: new Date().toISOString() };
    await setBatchPointer(updated);
    return updated;
}
let salaryOcrBusy = false;
let lastSalaryScreenshot = 0;
const SALARY_BACKFILL_KEY = 'jobagent_salary_backfill_pointer';
let salaryBackfillBusy = false;
async function getSalaryBackfillPointer() {
    const stored = await chrome.storage.session.get(SALARY_BACKFILL_KEY);
    const value = stored[SALARY_BACKFILL_KEY];
    return value && Number.isSafeInteger(value.runId) && Number.isSafeInteger(value.tabId) ? value : null;
}
async function setSalaryBackfillPointer(value) {
    if (value)
        await chrome.storage.session.set({ [SALARY_BACKFILL_KEY]: value });
    else
        await chrome.storage.session.remove(SALARY_BACKFILL_KEY);
}
function salaryBounded(operation, timeout = 8000) {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('salary_timeout')), timeout);
        operation.then((value) => { clearTimeout(timer); resolve(value); }, (error) => { clearTimeout(timer); reject(error); });
    });
}
async function salaryForeground(tabId) {
    const tab = await salaryBounded(chrome.tabs.get(tabId));
    if (!tab.active || tab.windowId === undefined || !tab.url?.startsWith(REQUIRED_ORIGIN + '/'))
        throw new Error('salary_tab_changed');
    if (!(await salaryBounded(chrome.windows.get(tab.windowId))).focused)
        throw new Error('salary_not_foreground');
    return tab.windowId;
}
/** Decode/crop wholly in worker memory. The full visible-tab bitmap is never sent,
 * saved, logged or persisted. Only <=800x160 PNG pixels reach localhost. */
async function salaryCrop(screenshot, f) {
    const values = [f.x, f.y, f.width, f.height, f.viewportWidth, f.viewportHeight];
    if (!values.every(Number.isFinite) || f.x < 0 || f.y < 0 || f.width < 6 || f.width > 400 || f.height < 4 || f.height > 80 ||
        f.viewportWidth <= 0 || f.viewportHeight <= 0 || f.x + f.width > f.viewportWidth || f.y + f.height > f.viewportHeight)
        throw new Error('salary_bad_region');
    if (!screenshot.startsWith('data:image/png;base64,') || screenshot.length > 32000000)
        throw new Error('salary_bad_image');
    const bytes = Uint8Array.from(atob(screenshot.slice(22)), (c) => c.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
    try {
        const sx = bitmap.width / f.viewportWidth, sy = bitmap.height / f.viewportHeight;
        if (Math.abs(sx - sy) > 0.03 || sx < 0.5 || sx > 4)
            throw new Error('salary_viewport_changed');
        const canvas = new OffscreenCanvas(Math.ceil(f.width * 2), Math.ceil(f.height * 2));
        const ctx = canvas.getContext('2d');
        if (!ctx)
            throw new Error('salary_canvas_unavailable');
        ctx.drawImage(bitmap, f.x * sx, f.y * sy, f.width * sx, f.height * sy, 0, 0, canvas.width, canvas.height);
        const blob = await canvas.convertToBlob({ type: 'image/png' });
        if (blob.size > 262144)
            throw new Error('salary_crop_too_large');
        const cropped = new Uint8Array(await blob.arrayBuffer());
        let binary = '';
        for (const value of cropped)
            binary += String.fromCharCode(value);
        return btoa(binary);
    }
    finally {
        bitmap.close();
    }
}
/** OCR may fill only a missing salary. It never changes candidate identity or imports. */
async function supplementSalary(tabId, candidate, allowed) {
    if (candidate.salary_text || !candidate.title || !canonicalizeJobDetailUrl(candidate.source_url) || salaryOcrBusy)
        return candidate;
    if (!chrome.tabs.captureVisibleTab || !chrome.windows?.get)
        return candidate;
    salaryOcrBusy = true;
    let contextChanged = false;
    const changed = () => { contextChanged = true; };
    const updated = (id, change) => {
        if (id === tabId && (change.url || change.status === 'loading'))
            changed();
    };
    chrome.tabs.onActivated?.addListener(changed);
    chrome.tabs.onUpdated?.addListener(updated);
    chrome.windows.onFocusChanged?.addListener(changed);
    const note = (reason) => ({ ...candidate, warnings: [...(candidate.warnings || []), '本地薪资 OCR 未采用：' + reason] });
    try {
        const message = { type: 'jobagent:salary-frame', canonicalUrl: candidate.source_url, expectedTitle: candidate.title };
        const read = async () => {
            if (!await allowed())
                throw new Error('salary_cancelled');
            // Opening our popup to pause/cancel can itself change window focus.
            // Honor the durable stop first; neither outcome may consume this image.
            if (contextChanged)
                throw new Error('salary_tab_changed');
            await salaryForeground(tabId);
            const response = await salaryBounded(chrome.tabs.sendMessage(tabId, message));
            if (!response.ok || !response.result)
                throw new Error('salary_frame_unavailable');
            if (response.result.status === 'login_required')
                throw new Error('salary_login_required');
            if (response.result.status === 'verification')
                throw new Error('salary_verification');
            return response.result;
        };
        const first = await read();
        if (first.status !== 'ok' || !first.frame)
            return note(first.status);
        const frame = first.frame;
        const stable = async () => {
            const next = await read();
            if (next.status !== 'ok' || JSON.stringify(next.frame) !== JSON.stringify(frame))
                throw new Error('salary_identity_or_region_changed');
        };
        // Never retry or raise capture frequency when the last attempt was recent.
        if (Date.now() - lastSalaryScreenshot < 600)
            return note('rate_limited');
        const windowId = await salaryForeground(tabId);
        if (!await allowed())
            throw new Error('salary_cancelled');
        lastSalaryScreenshot = Date.now();
        let screenshot = await salaryBounded(chrome.tabs.captureVisibleTab(windowId, { format: 'png' }));
        await stable();
        const image = await salaryBounded(salaryCrop(screenshot, frame));
        screenshot = '';
        await stable(); // check BEFORE transmitting even the crop
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 22000);
        let result;
        try {
            const response = await fetch(BACKEND_BASE + '/api/extension/salary-ocr', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ image }), signal: controller.signal,
            });
            result = response.ok ? await response.json() : { salary_text: null, reason: 'backend_unavailable' };
        }
        finally {
            clearTimeout(timer);
        }
        await stable();
        const salary = result.salary_text || '';
        // The visible text still tells us its units and whether annual-month suffix exists.
        // Do not silently lose “15薪”, convert 万 to K, or accept an unbounded OCR string.
        if (!/^\d{1,3}(?:\.\d{1,2})?-\d{1,3}(?:\.\d{1,2})?[K万](?:·\d{2}薪)?$/.test(salary) ||
            salary.includes('薪') !== frame.raw.includes('薪') || salary.includes('万') !== frame.raw.includes('万'))
            return note('uncertain');
        return { ...candidate, salary_text: salary,
            matched_selectors: { ...(candidate.matched_selectors || {}), salary_text: 'local_screenshot_ocr (two-scale agreement)' },
            missing_fields: (candidate.missing_fields || []).filter((field) => field !== 'salary_text'),
            warnings: [...(candidate.warnings || []).filter((warning) => !warning.includes('薪资')), '薪资由本机截图 OCR 补充，请在人工复核时核对。'],
        };
    }
    catch (error) {
        const reason = error instanceof Error ? error.message : '';
        // Identity/verification/cancellation is not an ordinary OCR miss: prevent import.
        if (['salary_login_required', 'salary_verification', 'salary_cancelled', 'salary_identity_or_region_changed', 'salary_tab_changed', 'salary_not_foreground'].includes(reason))
            throw error;
        return note('unavailable');
    }
    finally {
        chrome.tabs.onActivated?.removeListener(changed);
        chrome.tabs.onUpdated?.removeListener(updated);
        chrome.windows.onFocusChanged?.removeListener(changed);
        salaryOcrBusy = false;
    }
}
const RUNNER_REQUIRED_ORIGIN = 'https://www.zhipin.com';
//: Immutable ceilings (CLAUDE.md M4e/M4f amendment - "Existing immutable
//: ceilings remain: ... at most 20 processed candidates and at most 5
//: scroll rounds per SearchTask... Consecutive no-new rounds is
//: configurable and bounded (default 3)"). Never read from a config file or
//: a caller-supplied value higher than these.
const RUNNER_MAX_SCROLL_ROUNDS = 5;
const RUNNER_MAX_CANDIDATES = 20;
function validCandidateCap(value) {
    return typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= RUNNER_MAX_CANDIDATES;
}
function validRunnerBudget(pointer) {
    return validCandidateCap(pointer.candidateCap) &&
        Number.isInteger(pointer.scrollsUsed) && pointer.scrollsUsed >= 0 &&
        pointer.scrollsUsed <= RUNNER_MAX_SCROLL_ROUNDS &&
        Number.isInteger(pointer.candidatesAttempted) && pointer.candidatesAttempted >= 0 &&
        pointer.candidatesAttempted <= pointer.candidateCap &&
        Number.isInteger(pointer.candidatesProcessed) && pointer.candidatesProcessed >= 0 &&
        pointer.candidatesProcessed <= pointer.candidatesAttempted;
}
const RUNNER_DEFAULT_NO_NEW_ROUND_THRESHOLD = 3;
//: Bounded DOM-stabilization waits - a fixed poll interval and a hard
//: attempt ceiling, never an unbounded loop. Only this section of the
//: worker may use `setTimeout`; `overlay.ts`/`content.ts` still never do.
const RUNNER_STABILIZE_MAX_ATTEMPTS = 10;
const RUNNER_STABILIZE_INTERVAL_MS = 500;
const RUNNER_CAPTURE_MAX_ATTEMPTS = 10;
const RUNNER_CAPTURE_INTERVAL_MS = 500;
//: The one path a candidate identity may take: BOSS's own detail-page
//: shape, scheme+host+path only. CLAUDE.md M4f review - "Canonicalize every
//: candidate identity to query-free `/job_detail/<id>.html` before
//: inventory/handled/delta. Reject invalid/cross-origin identities." Every
//: inventory/handled-set/delta computation below goes through this, never a
//: raw `source_url` from a DETECT result - independent of whatever
//: canonicalization the content script already did (`boss/extract.ts`'s
//: `cleanUrl`), which the background worker cannot call (it runs in a
//: separate execution context with no DOM).
const JOB_DETAIL_PATH = /^\/job_detail\/([A-Za-z0-9_~-]+)\.html$/;
function canonicalizeJobDetailUrl(raw) {
    if (!raw)
        return null;
    let parsed;
    try {
        parsed = new URL(raw);
    }
    catch {
        return null;
    }
    if (parsed.origin !== RUNNER_REQUIRED_ORIGIN)
        return null;
    if (!JOB_DETAIL_PATH.test(parsed.pathname))
        return null;
    return parsed.origin + parsed.pathname;
}
async function getRunnerPointer() {
    const stored = await chrome.storage.session.get(RUNNER_STORAGE_KEY);
    return stored[RUNNER_STORAGE_KEY] || null;
}
let runnerWriteQueue = Promise.resolve();
function setRunnerPointer(pointer) {
    const write = runnerWriteQueue.then(async () => {
        if (!pointer) {
            await chrome.storage.session.remove(RUNNER_STORAGE_KEY);
            return;
        }
        const stored = await getRunnerPointer();
        // A concurrent pause/status write may carry an older snapshot. It must not
        // refund reserved candidate slots or replace the original approved cap.
        if (stored?.taskId === pointer.taskId && validRunnerBudget(stored)) {
            pointer.candidateCap = stored.candidateCap;
            pointer.candidatesAttempted = Math.max(stored.candidatesAttempted, pointer.candidatesAttempted);
            pointer.candidatesProcessed = Math.max(stored.candidatesProcessed, pointer.candidatesProcessed);
            pointer.handledUrls = Array.from(new Set([...stored.handledUrls, ...pointer.handledUrls]));
            pointer.scrollsUsed = Math.max(stored.scrollsUsed, pointer.scrollsUsed);
            pointer.seenCandidateUrls = Array.from(new Set([
                ...(stored.seenCandidateUrls ?? stored.handledUrls),
                ...(pointer.seenCandidateUrls ?? pointer.handledUrls),
            ]));
            pointer.cancelRequested = pointer.cancelRequested || stored.cancelRequested;
            if (stored.runToken === pointer.runToken) {
                pointer.pauseRequested = pointer.pauseRequested || stored.pauseRequested;
            } // only an explicit resume with a new token may clear a pause
        }
        await chrome.storage.session.set({ [RUNNER_STORAGE_KEY]: pointer });
    });
    runnerWriteQueue = write.catch(() => { });
    return write;
}
function sleepMs(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
function isRunnerNavOrigin(url) {
    try {
        return new URL(url).origin === RUNNER_REQUIRED_ORIGIN;
    }
    catch {
        return false;
    }
}
/** Prefer an existing ordinary BOSS tab for console runs.
 * This preserves Chrome's narrow, per-tab `activeTab` grant after the user
 * has invoked JobAgent once on that tab, allowing local salary OCR without
 * requesting all-sites screenshot access - a tab the worker opens itself has
 * no such grant, so OCR would silently be unavailable there.
 * The BOSS home page is adopted too: it is the same exact origin, the run
 * navigates the tab to its own target immediately anyway, and the login /
 * verification preflight still fails closed after that navigation. Chat,
 * login and verification pages are deliberately never adopted. */
async function reusableBossTab(windowId) {
    const tabs = await chrome.tabs.query({ windowId });
    return tabs.find((tab) => {
        if (tab.id === undefined || !tab.url || !isRunnerNavOrigin(tab.url))
            return false;
        try {
            const path = new URL(tab.url).pathname;
            // Exact '/' only: a prefix match here would also adopt /web/user/ (login)
            // and /web/geek/chat, which must never be driven.
            return path === '/' || path.startsWith('/web/geek/job')
                || path.startsWith('/web/geek/recommend') || path.startsWith('/job_detail/');
        }
        catch {
            return false;
        }
    });
}
//: In-memory single-flight guard for the whole run-loop lifecycle - see the
//: section banner above. `null` whenever no loop is actually executing
//: (including while paused/verification-halted, waiting for an explicit
//: resume). Reset only by a worker restart, which durable-state checkpoints
//: already tolerate (a resumed loop always re-reads storage, never trusts
//: this).
let activeRunToken = null;
let runTokenCounter = Date.now();
let batchAdmission = false;
function nextRunToken() {
    runTokenCounter += 1;
    return runTokenCounter;
}
function claimRunnerLoop(runToken) {
    if (activeRunToken !== null)
        return false;
    activeRunToken = runToken;
    return true;
}
function releaseRunnerLoop(runToken) {
    if (activeRunToken === runToken)
        activeRunToken = null;
}
/**
 * CLAUDE.md M4f review item 2 - "Before every navigation, detect, click,
 * capture, import and scroll, verify the same tab still exists, is
 * active/foreground, and remains exact BOSS origin. Fail/pause closed on
 * loss; never continue hidden." Read-only (`chrome.tabs.get`); never itself
 * changes anything about the tab.
 */
async function verifyRunnerTab(tabId) {
    let tab;
    try {
        tab = await chrome.tabs.get(tabId);
    }
    catch {
        return { ok: false, reason: 'tab_lost' };
    }
    if (!tab || !tab.url)
        return { ok: false, reason: 'tab_lost' };
    if (!isRunnerNavOrigin(tab.url))
        return { ok: false, reason: 'wrong_origin' };
    if (tab.active !== true)
        return { ok: false, reason: 'not_foreground' };
    try {
        if (tab.windowId === undefined || !(await chrome.windows.get(tab.windowId)).focused)
            return { ok: false, reason: 'not_foreground' };
    }
    catch {
        return { ok: false, reason: 'not_foreground' };
    }
    return { ok: true };
}
/** Ask the content script in `tabId` something - first re-verifying the tab
 * (see `verifyRunnerTab`). Chrome may not have attached declarative content
 * scripts to the first page opened immediately after an unpacked-extension
 * Reload. The popup already repairs that exact case by injecting the packaged
 * scripts once; the runner now has the same fail-closed recovery. It never
 * injects into another origin and re-verifies foreground/origin immediately
 * before injection and before the single retry. The caller still owns the
 * existing bounded stabilization attempts; this adds no unbounded wait. */
async function askTab(tabId, message, allowPackagedInjection = false) {
    const alive = await verifyRunnerTab(tabId);
    if (!alive.ok)
        return { ok: false, error: alive.reason };
    try {
        const response = await chrome.tabs.sendMessage(tabId, message);
        return (response || { ok: false });
    }
    catch {
        if (!allowPackagedInjection)
            return { ok: false, error: 'content_unavailable' };
        const beforeInjection = await verifyRunnerTab(tabId);
        if (!beforeInjection.ok)
            return { ok: false, error: beforeInjection.reason };
        try {
            await chrome.scripting.executeScript({
                target: { tabId },
                files: ['dist/boss/selectors.js', 'dist/boss/extract.js', 'dist/content.js', 'dist/overlay.js'],
            });
        }
        catch {
            return { ok: false, error: 'content_unavailable' };
        }
        const afterInjection = await verifyRunnerTab(tabId);
        if (!afterInjection.ok)
            return { ok: false, error: afterInjection.reason };
        try {
            const retry = await chrome.tabs.sendMessage(tabId, message);
            return (retry || { ok: false });
        }
        catch {
            return { ok: false, error: 'content_unavailable' };
        }
    }
}
async function fetchRunnerTask(taskId) {
    return fetchJson(`/api/tasks/search-plan/${taskId}`);
}
async function postTaskRun(taskId, action, body) {
    return fetchJson(`/api/tasks/${taskId}/run/${action}`, {
        method: 'POST',
        body: body ?? {},
    });
}
//: Scheme+host+path only - BOSS puts session tokens (`lid`, `securityId`)
//: in the query string, so `report_state` on the backend rejects a
//: `current_url` carrying one outright. Stripped here too, defense in
//: depth, before it is ever sent.
function queryFreeUrl(url) {
    try {
        const parsed = new URL(url);
        return parsed.origin + parsed.pathname;
    }
    catch {
        return null;
    }
}
/**
 * CLAUDE.md M4f observability delta - reports live progress to
 * `POST /api/tasks/{id}/run/state` so the localhost API and, through it,
 * the popup/overlay stay current. Pure observability: never awaited by a
 * caller that would otherwise block on it, and a failure here (network
 * blip, task already terminal) is swallowed - it must never interrupt or
 * fail a bounded step that already succeeded. `current_url`, when present,
 * is always query/fragment-stripped first.
 */
function reportState(taskId, report) {
    const body = { ...report };
    if (body.current_url !== undefined && body.current_url !== null) {
        body.current_url = queryFreeUrl(body.current_url);
    }
    void fetchJson(`/api/tasks/${taskId}/run/state`, { method: 'POST', body }).catch(() => {
        /* best-effort observability only - never blocks or fails the caller */
    });
}
//: CLAUDE.md M4f review item 3 - "Do not map every prepare failure to
//: normal cap completion; distinguish denial/error." Only
//: `supervised_sessions._check_navigable`'s own cap-denial ValidationErrors
//: carry `detail.field === capField`; every other prepare/confirm failure
//: (not-owner, unreachable backend, session not running, wrong origin,
//: prepare-in-flight, ...) does not, and must be treated as a real error.
function isCapDenied(result, capField) {
    const detail = result.detail;
    return detail?.field === capField;
}
//: CLAUDE.md M4f review item 5 - "Initial stabilization must not accept the
//: old search DOM after `tabs.update`: require the tab URL to match the
//: exact approved SearchTask path + city/query before creating the
//: session." Path (not just origin) plus the `city`/`query` params
//: `boss_search_url.build_search_url` always sets - a DETECT that still
//: happens to report `page_type: 'search'` on the *previous* results page
//: (a different city/keyword) must never be accepted as this run's stable
//: page.
function sameSearchTarget(currentUrl, targetUrl) {
    let current;
    let target;
    try {
        current = new URL(currentUrl);
        target = new URL(targetUrl);
    }
    catch {
        return false;
    }
    if (current.origin !== target.origin || current.pathname !== target.pathname)
        return false;
    return (current.searchParams.get('city') === target.searchParams.get('city') &&
        current.searchParams.get('query') === target.searchParams.get('query'));
}
function toStateMessage(pointer) {
    return {
        taskId: pointer.taskId,
        phase: pointer.phase,
        scrollsUsed: pointer.scrollsUsed,
        candidateCap: pointer.candidateCap,
        candidatesAttempted: pointer.candidatesAttempted,
        candidatesProcessed: pointer.candidatesProcessed,
        observedCount: pointer.observedCount,
        newCount: pointer.newCount,
        duplicateCount: pointer.duplicateCount,
        noNewRounds: pointer.noNewRounds,
        currentUrl: pointer.currentUrl,
        visibleJobs: pointer.visibleJobs,
        importedJobs: pointer.importedJobs,
        currentCandidate: pointer.currentCandidate,
        lastAction: pointer.lastAction,
        pausedReason: pointer.pausedReason,
        city: pointer.city,
        keyword: pointer.keyword,
        updatedAt: pointer.updatedAt,
        pauseRequested: pointer.pauseRequested,
        pauseConfirmed: pointer.pauseConfirmed,
        cancelRequested: pointer.cancelRequested,
        lastError: pointer.lastError,
        pendingTerminal: pointer.pendingTerminal,
    };
}
/** Pushes the current runner state to the approved tab's overlay - never
 * polled, only ever sent right after a durable write (CLAUDE.md M4f review
 * item 7: "visible runner task state/counters ... to the existing page
 * overlay as well as popup. No timer polling."). Best-effort: the overlay
 * may not be injected yet, and the popup can always read
 * `jobagent:runner-status` on its own open instead. */
function broadcastRunnerState(pointer) {
    chrome.tabs
        .sendMessage(pointer.tabId, { type: 'jobagent:runner-state', state: toStateMessage(pointer) })
        .catch(() => {
        /* overlay not present/injected - nothing more to do here */
    });
}
function broadcastRunnerCleared(tabId) {
    chrome.tabs.sendMessage(tabId, { type: 'jobagent:runner-state', state: null }).catch(() => {
        /* overlay not present/injected - nothing more to do here */
    });
}
/** Fresh read, returning the pointer only if it still belongs to this exact
 * run attempt - `null` for "cleared" or "superseded by a newer runToken"
 * alike, both of which mean the caller must stop touching the browser. */
async function readCurrent(taskId, runToken) {
    const pointer = await getRunnerPointer();
    if (!pointer || pointer.taskId !== taskId || pointer.runToken !== runToken)
        return null;
    return pointer;
}
/** Read-fresh-then-merge-write for the fields the running loop itself owns
 * (phase/counters/handled set/session id/error). Never touches
 * `pauseRequested`/`cancelRequested` - those are `requestRunnerPause`/
 * `requestRunnerCancel`'s own fresh-read-then-merge-write, so a concurrent
 * pause/cancel is never clobbered by a progress write built from a stale
 * snapshot. Returns `null` (nothing written) if the pointer was cleared or
 * superseded since the caller's last read. */
async function persistPatch(taskId, runToken, patch) {
    const fresh = await readCurrent(taskId, runToken);
    if (!fresh)
        return null;
    // Every durable write refreshes `updatedAt` - the one place this happens,
    // so the popup/overlay can show genuine liveness (CLAUDE.md M4f
    // observability delta item 2).
    const merged = { ...fresh, ...patch, updatedAt: new Date().toISOString() };
    await setRunnerPointer(merged);
    await patchBatchForTask(taskId, { state: 'paused', lastError: 'verification' });
    broadcastRunnerState(merged);
    return merged;
}
/**
 * `persistPatch` (durable pointer + overlay push) plus a best-effort
 * `reportState` for whichever of those same fields the backend also tracks
 * - the one place this section wires the extension-owned runner to
 * `POST /run/state` (CLAUDE.md M4f observability delta, item 1). Never
 * awaited by its caller beyond the local write; the backend report races
 * ahead in the background and its failure is swallowed by `reportState`
 * itself.
 */
async function persistAndReport(taskId, runToken, patch) {
    const updated = await persistPatch(taskId, runToken, patch);
    if (!updated)
        return null;
    const report = {};
    if (patch.currentUrl !== undefined)
        report.current_url = patch.currentUrl;
    if (patch.scrollsUsed !== undefined)
        report.scroll_round = patch.scrollsUsed;
    if (patch.visibleJobs !== undefined)
        report.visible_jobs = patch.visibleJobs;
    if (patch.importedJobs !== undefined)
        report.imported_jobs = patch.importedJobs;
    if (patch.currentCandidate !== undefined)
        report.current_candidate = patch.currentCandidate;
    if (patch.lastAction)
        report.last_action = patch.lastAction;
    if (Object.keys(report).length)
        reportState(taskId, report);
    return updated;
}
function detectedCandidateUrls(result) {
    return new Set(result.candidates.map(c => canonicalizeJobDetailUrl(c.source_url))
        .filter((url) => !!url));
}
/** Every accepted inventory refreshes telemetry, including initially empty SPA pages.
 * Keep history separate from handledUrls: seeing a card must not suppress processing it. */
async function rememberRenderedCandidates(taskId, runToken, result) {
    const fresh = await readCurrent(taskId, runToken);
    if (!fresh)
        return null;
    return persistAndReport(taskId, runToken, {
        visibleJobs: result.candidates.length,
        seenCandidateUrls: Array.from(new Set([
            ...(fresh.seenCandidateUrls ?? fresh.handledUrls), ...detectedCandidateUrls(result),
        ])),
    });
}
/** The one place every bounded step checks for a stop request - always a
 * fresh read of durable state, never a closed-over flag. */
async function checkpoint(taskId, runToken) {
    const fresh = await readCurrent(taskId, runToken);
    if (!fresh)
        return 'superseded';
    if (fresh.cancelRequested)
        return 'cancel';
    if (fresh.pauseRequested)
        return 'pause';
    return 'continue';
}
async function stopRunnerSession(sessionId) {
    if (sessionId === null)
        return true; // never created - nothing to stop
    try {
        const active = await fetchActive();
        if (active?.status === 'running') {
            if (active.id !== sessionId)
                return false;
            await postStop(sessionId, 'user_stop');
        }
        const pointer = await getGlobalPointer();
        if (pointer?.sessionId === sessionId)
            await clearGlobalPointer();
        return true;
    }
    catch {
        return false;
    }
}
/**
 * Ends browser activity for this run **for good** - only for a genuinely
 * terminal outcome (completed/failed/cancelled). CLAUDE.md M4f review item
 * 3 - "Do not clear runner/session pointers when backend terminal
 * transition or stop confirmation fails. Preserve recoverable state and
 * surface the error." So the pointer is cleared *only* once both the
 * backend task-run transition and the session stop are actually confirmed;
 * on either failure it stays, marked with `lastError`/`pendingTerminal` so
 * `jobagent:runner-retry-terminal` can finish the job later instead of the
 * run silently vanishing. Never called for a verification or a plain human
 * pause - see `pauseForHumanGate`/`haltForStop` below, both of which
 * *keep* the pointer so an explicit resume can continue this same run.
 *
 * `skipTransition` (review item 1): the backend's own `record_round` can
 * auto-complete a task (its consecutive-no-new-round threshold) *before*
 * this is ever called - `complete_run`/`fail_run`/`cancel_run` all require
 * the task to still be `running`, so replaying the transition here would
 * 422. Passed `true` for that case, and also derived from
 * `pendingTerminal.transitionDone` on a retry (review item 2) - either way,
 * a transition already known-confirmed is never replayed, only the session
 * stop is (re)attempted.
 */
async function endRunner(taskId, runToken, outcome, error, options) {
    const fresh = await readCurrent(taskId, runToken);
    if (!fresh) {
        releaseRunnerLoop(runToken);
        return; // superseded/cleared already - whoever did that owns termination
    }
    const transitionAlreadyDone = !!options?.skipTransition || fresh.pendingTerminal?.transitionDone === true;
    let transitionOk = true;
    if (!transitionAlreadyDone) {
        try {
            if (outcome === 'completed')
                await postTaskRun(taskId, 'complete');
            else if (outcome === 'failed')
                await postTaskRun(taskId, 'fail', { error: error || 'unknown' });
            else
                await postTaskRun(taskId, 'cancel');
        }
        catch {
            transitionOk = false;
        }
    }
    const sessionOk = await stopRunnerSession(fresh.sessionId);
    if (!transitionOk || !sessionOk) {
        await persistPatch(taskId, runToken, {
            phase: 'stopped',
            lastError: 'terminal_transition_incomplete:' + outcome,
            pendingTerminal: { outcome, error, transitionDone: transitionOk || transitionAlreadyDone },
        });
        releaseRunnerLoop(runToken);
        return;
    }
    await setRunnerPointer(null);
    broadcastRunnerCleared(fresh.tabId);
    releaseRunnerLoop(runToken);
    await finishBatchTask(fresh, outcome, error);
}
/** Advance only after a fully confirmed normal completion. All other outcomes
 * stop the approved batch before another task can start. */
async function finishBatchTask(finished, outcome, error) {
    const batch = await getBatchPointer();
    if (!batch || batch.taskIds[batch.currentIndex] !== finished.taskId
        || batch.tabId !== finished.tabId || !['running', 'paused'].includes(batch.state))
        return;
    if (outcome !== 'completed') {
        await setBatchPointer({ ...batch, state: 'stopped',
            lastError: (error || `task_${outcome}`).slice(0, 120), updatedAt: new Date().toISOString() });
        return;
    }
    const nextIndex = batch.currentIndex + 1;
    if (nextIndex >= batch.taskIds.length) {
        await setBatchPointer({ ...batch, state: 'completed', lastError: null,
            updatedAt: new Date().toISOString() });
        return;
    }
    const nextBatch = { ...batch, currentIndex: nextIndex, state: 'running',
        lastError: null, updatedAt: new Date().toISOString() };
    await setBatchPointer(nextBatch);
    const started = await startRunner(nextBatch.taskIds[nextIndex], nextBatch.candidateCap, undefined, undefined, undefined, true, finished.tabId);
    if (!started.ok) {
        await setBatchPointer({ ...nextBatch, state: 'stopped',
            lastError: ('next_task_start_failed:' + (started.error || 'unknown')).slice(0, 120),
            updatedAt: new Date().toISOString() });
        return;
    }
    await patchBatchForTask(nextBatch.taskIds[nextIndex], { tabId: finished.tabId, state: 'running' });
}
/**
 * A verification/CAPTCHA/rate-limit signal - stops all pending browser
 * activity immediately (the caller simply returns right after this) but
 * **keeps** the pointer and the `SupervisedSession` alive, marked
 * `pauseRequested` so nothing resumes it automatically: only an explicit
 * `resumeRunner()` call, after the human has resolved it themselves, may
 * continue. Never an automatic bypass or retry.
 */
async function pauseForHumanGate(taskId, runToken, reason) {
    // `pauseRequested` is not in `persistPatch`'s patch type on purpose - see
    // its docstring - so it is set here via its own dedicated fresh-read-
    // write, exactly like `requestRunnerPause`.
    const fresh = await readCurrent(taskId, runToken);
    if (!fresh) {
        releaseRunnerLoop(runToken);
        return;
    }
    const merged = {
        ...fresh,
        pauseRequested: true,
        phase: 'stopped',
        pausedReason: reason,
        lastAction: reason === 'verification' ? 'paused_verification' : 'paused_login_required',
        updatedAt: new Date().toISOString(),
    };
    await setRunnerPointer(merged);
    await patchBatchForTask(merged.taskId, { state: 'paused', lastError: null });
    broadcastRunnerState(merged);
    try {
        await postTaskRun(taskId, reason === 'verification' ? 'verification' : 'login-required');
    }
    catch (err) {
        await persistPatch(taskId, runToken, {
            lastError: `${reason}_transition_failed:` + String(err?.message || err),
        });
    }
    releaseRunnerLoop(runToken);
}
/**
 * The loop hit a `checkpoint()` other than `'continue'`. A cancel is
 * terminal (`endRunner`); a plain pause is not - it stops here, keeps the
 * pointer, and waits for an explicit `resumeRunner()` call.
 */
async function haltForStop(taskId, runToken) {
    const fresh = await readCurrent(taskId, runToken);
    if (!fresh) {
        releaseRunnerLoop(runToken);
        return;
    }
    if (fresh.cancelRequested) {
        await endRunner(taskId, runToken, 'cancelled');
        return;
    }
    await persistPatch(taskId, runToken, { phase: 'stopped' });
    releaseRunnerLoop(runToken);
}
/** Bounded DOM-stabilization wait for the search page to be ready - polls
 * DETECT up to `RUNNER_STABILIZE_MAX_ATTEMPTS` times, a fresh `checkpoint()`
 * before every attempt so a stop request is honored between polls, never
 * mid-poll. Never unbounded. Reused for the post-navigation wait and (via
 * `runScrollRound`) the post-scroll wait - CLAUDE.md M4f review item 5. */
async function waitForStableSearchPage(taskId, runToken, tabId, targetUrl) {
    let pendingReason = 'target_mismatch';
    for (let attempt = 0; attempt < RUNNER_STABILIZE_MAX_ATTEMPTS; attempt++) {
        const cp = await checkpoint(taskId, runToken);
        if (cp === 'superseded')
            return { ok: false, reason: 'stopped' };
        if (cp === 'cancel' || cp === 'pause')
            return { ok: false, reason: 'stopped' };
        // CLAUDE.md M4f review item 5 - never accept the tab's *previous* page
        // DOM (a different city/keyword) just because it also happens to look
        // like a search page. `chrome.tabs.get` is the source of truth for
        // "where is this tab actually now", independent of whatever DETECT's
        // page-type classification reports.
        let tab;
        try {
            tab = await chrome.tabs.get(tabId);
        }
        catch {
            // Losing a tab is not a user pause: finish the task and release ownership.
            return { ok: false, reason: 'failed', error: 'tab_lost' };
        }
        if (!tab.active)
            return { ok: false, reason: 'failed', error: 'not_foreground' };
        try {
            if (tab.windowId === undefined || !(await chrome.windows.get(tab.windowId)).focused) {
                return { ok: false, reason: 'failed', error: 'not_foreground' };
            }
        }
        catch {
            return { ok: false, reason: 'failed', error: 'not_foreground' };
        }
        if (tab.url && tab.url !== 'about:blank' && !isRunnerNavOrigin(tab.url)) {
            return { ok: false, reason: 'failed', error: 'wrong_origin' };
        }
        if (!tab.url || !sameSearchTarget(tab.url, targetUrl)) {
            pendingReason = 'target_mismatch';
            if (attempt < RUNNER_STABILIZE_MAX_ATTEMPTS - 1)
                await sleepMs(RUNNER_STABILIZE_INTERVAL_MS);
            continue;
        }
        const detected = await askTab(tabId, { type: 'jobagent:detect' }, attempt === 0);
        if (await checkpoint(taskId, runToken) !== 'continue')
            return { ok: false, reason: 'stopped' };
        if (!detected.ok && (detected.error === 'tab_lost' || detected.error === 'wrong_origin' || detected.error === 'not_foreground')) {
            return { ok: false, reason: 'failed', error: detected.error };
        }
        pendingReason = 'content_unavailable';
        if (detected.ok && detected.result) {
            if (detected.result.login_required)
                return { ok: false, reason: 'login_required' };
            if (detected.result.verification)
                return { ok: false, reason: 'verification' };
            if (detected.result.page_type === 'search')
                return { ok: true, result: detected.result };
            pendingReason = 'unsupported_page';
        }
        if (attempt < RUNNER_STABILIZE_MAX_ATTEMPTS - 1)
            await sleepMs(RUNNER_STABILIZE_INTERVAL_MS);
    }
    // Fixed codes only: never persist exception messages, page text or URL queries.
    return { ok: false, reason: 'failed', error: `stabilization_timeout:${pendingReason}` };
}
/**
 * Processes every currently-rendered, not-yet-handled candidate on the
 * page, one at a time, sequentially - never concurrently. Each candidate:
 * prepare(detail) -> click -> confirm (the exact M4b primitive), a bounded
 * wait for the right-hand pane, an identity-checked merge
 * (`captureAndMerge`, unchanged), then the existing loopback preview and -
 * only for M4f - an automatic import of a genuinely new result through the
 * same canonical `job_intake` path a human's own "导入" click already uses.
 * Stops the moment the candidate cap is reached, a policy violation is
 * detected, or a pause/cancel is requested - never partway through one
 * candidate's own steps.
 */
async function processNewCandidates(taskId, runToken, tabId) {
    for (;;) {
        const cp = await checkpoint(taskId, runToken);
        if (cp !== 'continue')
            return { status: 'stopped' };
        const fresh = await readCurrent(taskId, runToken);
        if (!fresh)
            return { status: 'stopped' };
        if (!validRunnerBudget(fresh))
            return { status: 'error', reason: 'invalid_candidate_budget' };
        // Complete a durably queued match before another browser step (including after resume).
        if (fresh.autoMatch && fresh.pendingMatch) {
            if (await checkpoint(taskId, runToken) !== 'continue')
                return { status: 'stopped' };
            try {
                await persistAndReport(taskId, runToken, { lastAction: 'matching_resume' });
                const outcome = await salaryBounded(fetchJson(`/api/tasks/${taskId}/auto-match/step`, {
                    method: 'POST', body: { job_id: fresh.pendingMatch.jobId, canonical_url: fresh.pendingMatch.canonicalUrl },
                }), 180000);
                if (outcome.state === 'running')
                    return { status: 'error', reason: 'match_result_uncertain_no_retry' };
                if (outcome.state !== 'done' && outcome.state !== 'failed')
                    return { status: 'error', reason: 'match_invalid_response' };
                await persistPatch(taskId, runToken, { pendingMatch: null });
                await persistAndReport(taskId, runToken, { lastAction: outcome.state === 'done' ? 'match_ready_for_review' : 'match_failed_no_retry' });
            }
            catch {
                if (await checkpoint(taskId, runToken) !== 'continue')
                    return { status: 'stopped' };
                return { status: 'error', reason: 'match_request_unconfirmed_no_retry' };
            }
            continue; // re-read pause/cancel and the immutable candidate budget
        }
        if (fresh.candidatesAttempted >= fresh.candidateCap)
            return { status: 'cap_reached' };
        const detected = await askTab(tabId, { type: 'jobagent:detect' });
        if (!detected.ok || !detected.result)
            return { status: 'error', reason: detected.error || 'detect_failed' };
        if (detected.result.login_required)
            return { status: 'login_required' };
        if (detected.result.verification)
            return { status: 'verification' };
        if (detected.result.page_type !== 'search')
            return { status: 'wrong_page' };
        if (!await rememberRenderedCandidates(taskId, runToken, detected.result))
            return { status: 'stopped' };
        const handled = new Set(fresh.handledUrls);
        let nextIndex = -1;
        let targetUrl = null;
        let skippedEarlyCareer = false;
        for (let i = 0; i < detected.result.candidates.length; i++) {
            const c = detected.result.candidates[i];
            const canonical = canonicalizeJobDetailUrl(c.source_url);
            const compactTitle = (c.title || '').replace(/\s+/g, '');
            const earlyCareer = /应届|校招|校园招聘|毕业生|管培生|实习|(?:20)?2[4-9]届/.test(compactTitle);
            // Only the experienced-track policy may safely reject from a title.
            // `only` must still open the detail because the JD can carry the
            // early-career evidence even when the card title does not.
            if ((fresh.earlyCareerPolicy ?? 'exclude') === 'exclude' && canonical && earlyCareer && !handled.has(canonical)) {
                handled.add(canonical);
                skippedEarlyCareer = true;
                continue;
            }
            if (c.title && canonical && !handled.has(canonical)) {
                nextIndex = i;
                targetUrl = canonical;
                break;
            }
        }
        if (skippedEarlyCareer) {
            const persisted = await persistPatch(taskId, runToken, {
                handledUrls: Array.from(handled),
                lastError: null,
                lastAction: 'skipped_early_career_track',
            });
            if (!persisted)
                return { status: 'stopped' };
        }
        if (nextIndex === -1 || !targetUrl)
            return { status: 'ok' }; // nothing new right now - caller may scroll
        const candidate = detected.result.candidates[nextIndex];
        const prepared = await navigatePrepareForTab(tabId, 'detail', targetUrl);
        if (!prepared.ok) {
            // CLAUDE.md M4f review item 3 - only a genuine candidate_cap denial is
            // a normal, successful cap completion; every other prepare failure
            // (not-owner, unreachable backend, session no longer running, ...) is
            // a real error and must not be silently folded into "completed".
            if (isCapDenied(prepared, 'candidate_cap'))
                return { status: 'cap_reached' };
            return { status: 'error', reason: prepared.error || 'prepare_denied' };
        }
        // Reserve before the browser side effect. Failed/mismatched/interrupted attempts are
        // not refunded: a worker restart must never allow an extra unapproved candidate.
        if (await checkpoint(taskId, runToken) !== 'continue')
            return { status: 'stopped' };
        handled.add(targetUrl);
        let reserved;
        try {
            reserved = await persistPatch(taskId, runToken, {
                candidatesAttempted: fresh.candidatesAttempted + 1,
                handledUrls: Array.from(handled),
            });
        }
        catch {
            return { status: 'error', reason: 'candidate_budget_write_failed' };
        }
        if (!reserved || reserved.pauseRequested || reserved.cancelRequested)
            return { status: 'stopped' };
        const opened = await askTab(tabId, {
            type: 'jobagent:open-candidate',
            index: nextIndex,
        });
        const clicked = !!opened.ok && !!opened.result?.ok;
        handled.add(targetUrl);
        const afterHandled = await persistPatch(taskId, runToken, { handledUrls: Array.from(handled) });
        if (!afterHandled)
            return { status: 'stopped' }; // superseded/cleared mid-step
        await persistAndReport(taskId, runToken, {
            currentCandidate: candidate.title,
            lastAction: 'opening_candidate',
        });
        const confirmed = await navigateConfirmForTab(tabId, 'detail', targetUrl, clicked ? 'success' : 'failed', clicked ? null : opened.result?.error || 'unknown');
        if (!clicked)
            return { status: 'error', reason: opened.error || 'click_failed' };
        // CLAUDE.md M4f review item 3 - a failed/unconfirmed confirm must stop
        // fail-closed and never reach capture/preview/import below: the click
        // really happened but the backend could not be told, so the recorded
        // candidate count (and cap enforcement) can no longer be trusted.
        if (!confirmed.ok)
            return { status: 'error', reason: confirmed.error || 'confirm_failed' };
        // Bounded wait for the right-hand detail pane - CLAUDE.md M4f review
        // item 5 ("bounded stabilization after ... each card selection").
        let captured = null;
        let mismatchSeen = false;
        for (let attempt = 0; attempt < RUNNER_CAPTURE_MAX_ATTEMPTS; attempt++) {
            const cpInner = await checkpoint(taskId, runToken);
            if (cpInner !== 'continue')
                return { status: 'stopped' };
            const cap = await askTab(tabId, {
                type: 'jobagent:capture-detail',
                canonicalUrl: targetUrl,
                cachedCard: candidate,
            });
            if (!cap.ok)
                return { status: 'error', reason: cap.error || 'capture_failed' }; // tab lost/wrong origin/backgrounded - fail closed
            // CLAUDE.md M4f delta - a bounded SPA results->detail transition can
            // leave the pane briefly showing the *previous* selected job. Retry
            // `identity_mismatch` exactly like `not_loaded`, within the same
            // ceiling - the identity check itself (title/company match) is never
            // weakened, only given the same bounded chance `not_loaded` already
            // gets to resolve.
            if (cap.result && cap.result.status === 'identity_mismatch') {
                mismatchSeen = true;
                if (attempt < RUNNER_CAPTURE_MAX_ATTEMPTS - 1)
                    await sleepMs(RUNNER_CAPTURE_INTERVAL_MS);
                continue;
            }
            if (cap.result && cap.result.status !== 'not_loaded') {
                captured = cap.result;
                break;
            }
            if (attempt < RUNNER_CAPTURE_MAX_ATTEMPTS - 1)
                await sleepMs(RUNNER_CAPTURE_INTERVAL_MS);
        }
        if (!captured) {
            if (mismatchSeen) {
                // Identity never resolved within the bounded ceiling - skip only
                // this one candidate (mark handled so it is never retried) rather
                // than failing the whole run; report concise evidence and continue.
                handled.add(targetUrl);
                await persistPatch(taskId, runToken, {
                    handledUrls: Array.from(handled),
                    lastError: 'identity_mismatch_persisted:' + targetUrl,
                    lastAction: 'skipped_identity_mismatch',
                });
                continue;
            }
            return { status: 'error', reason: 'capture_timeout' };
        }
        if (captured.status === 'login_required')
            return { status: 'login_required' };
        if (captured.status === 'verification')
            return { status: 'verification' };
        if (captured.status !== 'ok' || !captured.candidate) {
            return { status: 'error', reason: 'capture_failed' };
        }
        let merged = captured.candidate;
        const canonicalMerged = canonicalizeJobDetailUrl(merged.source_url);
        if (!canonicalMerged || canonicalMerged !== targetUrl)
            return { status: 'error', reason: 'invalid_identity' };
        if (!merged.salary_text) {
            try {
                await persistAndReport(taskId, runToken, { lastAction: 'reading_salary_local_ocr' });
                merged = await supplementSalary(tabId, merged, async () => await checkpoint(taskId, runToken) === 'continue');
                // Even a failed/unsupported OCR attempt must not hide a newly appeared challenge.
                if (await checkpoint(taskId, runToken) !== 'continue')
                    return { status: 'stopped' };
                const freshCapture = await askTab(tabId, {
                    type: 'jobagent:capture-detail', canonicalUrl: targetUrl, cachedCard: candidate,
                });
                if (freshCapture.result?.status === 'login_required')
                    return { status: 'login_required' };
                if (freshCapture.result?.status === 'verification')
                    return { status: 'verification' };
                if (!freshCapture.ok || freshCapture.result?.status !== 'ok')
                    return { status: 'error', reason: 'salary_identity_recheck_failed' };
            }
            catch (error) {
                const reason = error instanceof Error ? error.message : 'salary_failed';
                if (reason === 'salary_login_required')
                    return { status: 'login_required' };
                if (reason === 'salary_verification')
                    return { status: 'verification' };
                if (reason === 'salary_cancelled')
                    return { status: 'stopped' };
                return { status: 'error', reason };
            }
        }
        if (await checkpoint(taskId, runToken) !== 'continue')
            return { status: 'stopped' };
        // One more tab check immediately before the network write - item 2
        // covers "import" explicitly, not just the DOM steps above.
        const aliveForImport = await verifyRunnerTab(tabId);
        if (!aliveForImport.ok)
            return { status: 'error', reason: aliveForImport.reason };
        let didImport = false;
        let resolvedJobId = null;
        let intakeIncomplete = false;
        let intakeExcluded = false;
        try {
            const preview = (await fetchJson('/api/extension/jobs/preview', {
                method: 'POST',
                body: { page_type: 'detail', page_url: canonicalMerged, candidates: [merged], task_id: taskId },
            }));
            const previewRow = preview.rows?.[0];
            intakeIncomplete = previewRow?.status === 'incomplete' || (preview.incomplete_count ?? 0) > 0;
            intakeExcluded = previewRow?.status === 'excluded' || (preview.excluded_count ?? 0) > 0;
            // A global duplicate: preview already resolved which existing `Job`
            // this canonical URL belongs to - one `Job` may legitimately belong
            // to more than one `SearchTask`.
            resolvedJobId = previewRow?.existing_job_id ?? null;
            const needsSalaryEnrichment = previewRow?.status === 'duplicate'
                && previewRow.enrichable_fields?.includes('salary_text');
            if (!intakeIncomplete && !intakeExcluded && ((preview.new_count ?? 0) > 0 || needsSalaryEnrichment)) {
                // CLAUDE.md M4f review item 5 - "Re-check tab after preview and
                // immediately before import." Time has passed since the check
                // above (a real network round-trip); the tab may have been closed,
                // backgrounded, or navigated away in the interim.
                const aliveBeforeImport = await verifyRunnerTab(tabId);
                if (!aliveBeforeImport.ok)
                    return { status: 'error', reason: aliveBeforeImport.reason };
                const imported = (await fetchJson('/api/extension/jobs/import', {
                    method: 'POST',
                    body: { confirmed: true, candidate: merged, task_id: taskId },
                }));
                didImport = imported.duplicate !== true; // enrichment is not a new import
                resolvedJobId = imported.job_id ?? null;
            }
        }
        catch (err) {
            return { status: 'error', reason: 'intake_failed:' + String(err?.message || err) };
        }
        // A detail pane can have a valid stable job identity yet still lack the
        // minimum JD required by canonical intake. That is one failed candidate,
        // not a corrupt task: its attempt was already durably reserved above, so
        // record and continue within the same immutable candidate cap. A malformed
        // duplicate response with no job id still fails closed below.
        if (intakeIncomplete) {
            if (!await persistPatch(taskId, runToken, {
                lastError: 'candidate_incomplete',
                lastAction: 'skipped_incomplete',
            }))
                return { status: 'stopped' };
            reportState(taskId, { last_action: 'skipped_incomplete' });
            continue;
        }
        if (intakeExcluded) {
            if (!await persistPatch(taskId, runToken, {
                lastError: null,
                lastAction: 'skipped_early_career_track',
            }))
                return { status: 'stopped' };
            reportState(taskId, { last_action: 'skipped_early_career_track' });
            continue;
        }
        // CLAUDE.md M4f delta - associate the resolved job (new import or
        // pre-existing duplicate) with this running SearchTask through the
        // existing `POST /api/tasks/{id}/candidates` endpoint - idempotent, no
        // second persistence/dedup path. Missing identity or a failed
        // association must be visible and fail this candidate closed, never
        // silently dropped. Staged separately from the preview/import try above
        // so an association failure is always reported as
        // `candidate_association_failed`, on both the new-import and the
        // duplicate path - never folded into `intake_failed`, which is reserved
        // for the preview/import calls themselves.
        if (resolvedJobId === null)
            return { status: 'error', reason: 'missing_job_identity' };
        try {
            await fetchJson(`/api/tasks/${taskId}/candidates`, {
                method: 'POST',
                body: { job_id: resolvedJobId },
            });
        }
        catch (err) {
            return {
                status: 'error',
                reason: 'candidate_association_failed:' + String(err?.message || err),
            };
        }
        if (fresh.autoMatch)
            await persistPatch(taskId, runToken, {
                pendingMatch: { jobId: resolvedJobId, canonicalUrl: canonicalMerged },
            });
        const afterProgress = await persistAndReport(taskId, runToken, {
            candidatesProcessed: fresh.candidatesProcessed + 1,
            ...(didImport ? { importedJobs: fresh.importedJobs + 1 } : {}),
            lastAction: didImport ? 'imported' : 'duplicate_skipped',
        });
        if (!afterProgress)
            return { status: 'stopped' };
    }
}
async function runScrollRound(taskId, runToken, tabId) {
    const before = await askTab(tabId, { type: 'jobagent:detect' });
    if (!before.ok || !before.result)
        return { status: 'error', reason: before.error || 'detect_failed' };
    if (before.result.login_required)
        return { status: 'login_required' };
    if (before.result.verification)
        return { status: 'verification' };
    if (before.result.page_type !== 'search')
        return { status: 'wrong_page' };
    const beforeUrls = detectedCandidateUrls(before.result);
    const inventory = await rememberRenderedCandidates(taskId, runToken, before.result);
    if (!inventory)
        return { status: 'stopped' };
    const seenUrls = new Set(inventory.seenCandidateUrls ?? inventory.handledUrls);
    const prepared = await navigatePrepareForTab(tabId, 'scroll', null);
    if (!prepared.ok) {
        // CLAUDE.md M4f review item 3 - only a genuine scroll_cap denial is a
        // normal, successful cap completion; every other prepare failure is a
        // real error.
        if (isCapDenied(prepared, 'scroll_cap'))
            return { status: 'cap_reached' };
        return { status: 'error', reason: prepared.error || 'prepare_denied' };
    }
    // Reserve BEFORE the browser side effect. Pausing during post-scroll stabilization
    // must not refund the performed scroll after resume/worker recreation.
    if (await checkpoint(taskId, runToken) !== 'continue')
        return { status: 'stopped' };
    const budget = await readCurrent(taskId, runToken);
    if (!budget)
        return { status: 'stopped' };
    if (budget.scrollsUsed >= RUNNER_MAX_SCROLL_ROUNDS)
        return { status: 'cap_reached' };
    try {
        const reserved = await persistAndReport(taskId, runToken, { scrollsUsed: budget.scrollsUsed + 1 });
        if (!reserved || await checkpoint(taskId, runToken) !== 'continue')
            return { status: 'stopped' };
    }
    catch {
        return { status: 'error', reason: 'scroll_budget_write_failed' };
    }
    const scrolled = await askTab(tabId, {
        type: 'jobagent:scroll-step',
    });
    const succeeded = !!scrolled.ok && !!scrolled.result?.ok;
    const confirmed = await navigateConfirmForTab(tabId, 'scroll', null, succeeded ? 'success' : 'failed', succeeded ? null : scrolled.result?.error || 'unknown');
    if (!succeeded)
        return { status: 'error', reason: scrolled.error || 'scroll_failed' };
    // CLAUDE.md M4f review item 3 - an unconfirmed scroll must never proceed
    // to stabilization/inventory/round-recording below.
    if (!confirmed.ok)
        return { status: 'error', reason: confirmed.error || 'confirm_failed' };
    // Bounded post-scroll stabilization - CLAUDE.md M4f review item 5: "do not
    // treat the immediate post-scroll DOM as final." Polls until the card
    // canonical URL set gains a card (including same-size virtualized lists), or the
    // attempt ceiling is reached (whichever result is used either way - never
    // an unbounded wait for "settled").
    let stableResult = null;
    for (let attempt = 0; attempt < RUNNER_STABILIZE_MAX_ATTEMPTS; attempt++) {
        const cp = await checkpoint(taskId, runToken);
        if (cp !== 'continue')
            return { status: 'stopped' };
        const after = await askTab(tabId, { type: 'jobagent:detect' });
        if (!after.ok || !after.result)
            return { status: 'error', reason: after.error || 'detect_failed' };
        if (after.result.login_required)
            return { status: 'login_required' };
        if (after.result.verification)
            return { status: 'verification' };
        if (after.result.page_type !== 'search')
            return { status: 'wrong_page' };
        const appeared = Array.from(detectedCandidateUrls(after.result)).some(url => !beforeUrls.has(url));
        if (appeared || attempt === RUNNER_STABILIZE_MAX_ATTEMPTS - 1) {
            stableResult = after.result;
            break;
        }
        await sleepMs(RUNNER_STABILIZE_INTERVAL_MS);
    }
    if (!stableResult)
        return { status: 'error', reason: 'stabilize_timeout' };
    const afterUrls = detectedCandidateUrls(stableResult);
    let newCount = 0;
    for (const url of afterUrls)
        if (!seenUrls.has(url))
            newCount += 1;
    const duplicateCount = afterUrls.size - newCount;
    if (!await rememberRenderedCandidates(taskId, runToken, stableResult))
        return { status: 'stopped' };
    return { status: 'ok', observed: afterUrls.size, new: newCount, duplicate: duplicateCount };
}
/** Only a real action popup may defer focus validation until it closes. */
async function bindPopupTarget(value, sender) {
    const target = value;
    if (!target || !Number.isInteger(target.tabId) || !Number.isInteger(target.windowId)
        || sender.tab != null || sender.url !== chrome.runtime.getURL('popup.html')) {
        throw new Error('start-v4/invalid_popup_binding');
    }
    // Chrome does not populate MessageSender.documentId for an action popup.
    // Verify the own-extension sender URL plus exactly one real POPUP context.
    const contexts = await chrome.runtime.getContexts({ contextTypes: ['POPUP'] });
    if (contexts.length !== 1 || contexts[0].documentUrl !== sender.url)
        throw new Error('start-v4/popup_not_open');
    return { tabId: target.tabId, windowId: target.windowId };
}
async function foregroundStartTab(binding) {
    // Fixed, non-private reason codes distinguish live failures and loaded builds.
    const refuse = (reason) => ({ ok: false, error: 'start-v3/' + reason });
    let stage = 'window_query';
    try {
        // A worker's implicit currentWindow can be absent while the action popup is
        // open. Resolve the popup-bound normal window explicitly; only a validated
        // popup may defer focus to the post-close gate. Never activate or guess a tab.
        const window = await chrome.windows.getLastFocused({ windowTypes: ['normal'] });
        if (window.id === undefined)
            return refuse('window_id_missing');
        if (window.type !== 'normal')
            return refuse('window_not_normal');
        if (!binding && !window.focused)
            return refuse('window_not_focused');
        if (binding && binding.windowId !== window.id)
            return refuse('tab_window_mismatch');
        stage = 'tab_query';
        const tabs = await chrome.tabs.query({ active: true, windowId: window.id });
        if (!tabs.length)
            return refuse('active_tab_missing');
        if (tabs.length !== 1)
            return refuse('active_tab_ambiguous');
        if (!tabs[0].active)
            return refuse('tab_not_active');
        if (tabs[0].windowId !== window.id)
            return refuse('tab_window_mismatch');
        if (binding && tabs[0].id !== binding.tabId)
            return refuse('tab_not_active');
        stage = 'focus_recheck';
        if (!binding && !(await chrome.windows.get(window.id)).focused)
            return refuse('window_focus_changed');
        return { ok: true, tab: tabs[0] };
    }
    catch {
        return refuse(stage + '_failed');
    }
}
/** No BOSS commands while the popup owns focus. Never select a replacement tab.
 * The backend start/resume is acknowledged first so a network failure keeps the
 * popup open. This bounded, in-memory handoff is not restarted by a worker wake. */
async function waitForPopupHandoff(binding, initialUrl, stopped) {
    try {
        for (let attempt = 0; attempt < 20; attempt++) {
            if (await stopped())
                return 'stopped';
            await sleepMs(100);
            const state = await salaryBounded((async () => {
                const tab = await chrome.tabs.get(binding.tabId);
                const popups = await chrome.runtime.getContexts({ contextTypes: ['POPUP'] });
                const window = await chrome.windows.get(binding.windowId);
                return { tab, popups, window };
            })(), 500);
            if (await stopped())
                return 'stopped';
            if (state.tab.id !== binding.tabId || state.tab.windowId !== binding.windowId
                || !state.tab.active || state.tab.url !== initialUrl || !isRunnerNavOrigin(state.tab.url)) {
                return 'start-v4/handoff_tab_changed';
            }
            if (state.popups.length === 0 && state.window.focused)
                return null;
        }
        return 'start-v4/handoff_focus_timeout';
    }
    catch {
        return 'start-v4/handoff_query_failed';
    }
}
async function afterPopupHandoff(pointer, binding, initialUrl, proceed) {
    const { taskId, runToken } = pointer;
    const reason = await waitForPopupHandoff(binding, initialUrl, async () => (await checkpoint(taskId, runToken)) !== 'continue');
    const cp = await checkpoint(taskId, runToken);
    if (cp === 'superseded') {
        releaseRunnerLoop(runToken);
        return;
    }
    if (cp === 'cancel' || cp === 'pause') {
        await haltForStop(taskId, runToken);
        return;
    }
    if (reason) {
        await endRunner(taskId, runToken, 'failed', reason);
        return;
    }
    await proceed();
}
/**
 * The human's popup entry point. Claims single-flight synchronously before
 * awaiting anything; every later step re-checks durable state and its bounds.
 */
async function startRunner(taskId, candidateCap, binding, matchApproval, consoleSender, batchEntry = false, expectedTabId, launch = true) {
    if (salaryOcrBusy)
        return { ok: false, error: 'salary_ocr_busy' };
    if (!validCandidateCap(candidateCap))
        return { ok: false, error: '候选上限必须是 1–20 的整数，请重新确认。' };
    if (matchApproval && (matchApproval.confirmed !== true || matchApproval.cap !== candidateCap || candidateCap > 3)) {
        return { ok: false, error: '自动匹配需明确确认费用，候选/分析上限最多 3 个。' };
    }
    if (batchAdmission && !batchEntry)
        return { ok: false, error: 'batch_admission_active' };
    if (activeRunToken !== null)
        return { ok: false, error: 'already_running' };
    const runToken = nextRunToken();
    activeRunToken = runToken;
    const fail = (error) => {
        releaseRunnerLoop(runToken);
        return { ok: false, error };
    };
    if (await getRunnerPointer())
        return fail('already_running');
    if (await getGlobalPointer())
        return fail('session_already_active');
    const batch = await getBatchPointer();
    if (batch && ['running', 'paused'].includes(batch.state)) {
        if (!batchEntry || batch.taskIds[batch.currentIndex] !== taskId)
            return fail('batch_already_active');
    }
    else if (batchEntry)
        return fail('batch_not_active');
    // The console cannot supply a URL, tab id, code, or paid approval. Validate the
    // saved SearchTask before creating a tab, under the SAME single-flight guard.
    let consoleTab;
    if (consoleSender) {
        try {
            const task = await fetchRunnerTask(taskId);
            if (task.run_status !== 'pending')
                return fail('任务不是待开始状态；已结束的任务不会自动重跑。');
            if (!task.search_url || !isConsoleSearchUrl(task.search_url))
                return fail('wrong_search_url');
            if (task.max_candidates != null && (!validCandidateCap(task.max_candidates)
                || candidateCap > task.max_candidates))
                return fail('invalid_task_candidate_cap');
            const source = await consoleSourceTab(consoleSender, true);
            const reusable = await reusableBossTab(source.windowId);
            const previousReusableUrl = reusable?.url;
            consoleTab = reusable?.id !== undefined
                ? await chrome.tabs.update(reusable.id, { url: task.search_url, active: true })
                : await chrome.tabs.create({ url: task.search_url, active: true, windowId: source.windowId });
            if (consoleTab.id === undefined || consoleTab.windowId !== source.windowId)
                return fail('console_tab_create_failed');
            // tabs.create resolves before Chrome publishes the committed URL. Do not
            // treat a temporarily empty URL as a missing permission or start on a
            // guessed tab; wait only on the new id, with a short absolute bound.
            let ready = false;
            for (let attempt = 0; attempt < 30; attempt++) {
                const target = await chrome.tabs.get(consoleTab.id);
                if (!target.active || target.windowId !== source.windowId
                    || !(await chrome.windows.get(source.windowId)).focused)
                    return fail('console_tab_changed');
                if (target.url === task.search_url) {
                    ready = true;
                    break;
                }
                // tabs.update may resolve before the reused tab publishes its new
                // committed URL. Only its exact pre-update BOSS URL is tolerated
                // during this short wait; any third URL is still a hard mismatch.
                if (target.url && target.url !== 'about:blank' && target.url !== previousReusableUrl) {
                    return fail('console_navigation_changed');
                }
                await sleepMs(100);
            }
            if (!ready)
                return fail('console_navigation_timeout');
        }
        catch {
            return fail('控制台启动失败：请确认后端在线，并在正常 Chrome 前台打开控制台。');
        }
    }
    const resolved = await foregroundStartTab(binding);
    if (!resolved.ok)
        return fail(resolved.error);
    const tab = resolved.tab;
    if (consoleTab && tab.id !== consoleTab.id)
        return fail('console_tab_changed');
    if (expectedTabId !== undefined && tab.id !== expectedTabId)
        return fail('batch_tab_changed');
    if (tab.id === undefined)
        return fail('start-v3/tab_id_missing');
    if (!tab.url)
        return fail('start-v3/tab_url_unavailable');
    if (!isRunnerNavOrigin(tab.url))
        return fail('wrong_origin');
    if (tab.active !== true)
        return fail('not_foreground');
    const tabId = tab.id;
    let task;
    try {
        task = await fetchRunnerTask(taskId);
    }
    catch (err) {
        return fail(String(err?.message || err));
    }
    if (!task.search_url || !isRunnerNavOrigin(task.search_url)) {
        return fail('wrong_origin');
    }
    if (!['exclude', 'include', 'only'].includes(task.early_career_policy)) {
        return fail('invalid_early_career_policy');
    }
    if (task.max_candidates != null) {
        if (!Number.isInteger(task.max_candidates) || task.max_candidates < 1)
            return fail('invalid_task_candidate_cap');
        if (candidateCap > task.max_candidates)
            return fail('候选上限超过任务允许的数量，请降低后重试。');
    }
    try {
        await postTaskRun(taskId, 'start', matchApproval ? { match_approval: matchApproval } : undefined);
    }
    catch (err) {
        return fail(String(err?.message || err));
    }
    // No `SupervisedSession` yet - see `runRunnerLoop`, which creates it only
    // once the tab is confirmed stable on the SearchTask's own `search_url`
    // (CLAUDE.md M4f review item 6). `sessionId: null` in the meantime.
    const pointer = {
        taskId,
        tabId,
        sessionId: null,
        runToken,
        phase: 'navigating',
        pauseRequested: false,
        pauseConfirmed: false,
        cancelRequested: false,
        scrollsUsed: 0,
        candidateCap,
        candidatesAttempted: 0,
        candidatesProcessed: 0,
        handledUrls: [],
        seenCandidateUrls: [],
        observedCount: task.observed_count ?? 0,
        newCount: task.new_count ?? 0,
        duplicateCount: task.duplicate_count ?? 0,
        noNewRounds: task.no_new_rounds ?? 0,
        currentUrl: null,
        visibleJobs: 0,
        importedJobs: 0,
        currentCandidate: null,
        lastAction: binding ? 'waiting_popup_focus' : 'starting',
        pausedReason: null,
        city: task.city ?? null,
        keyword: task.keywords ?? null,
        earlyCareerPolicy: task.early_career_policy,
        updatedAt: new Date().toISOString(),
        lastError: null,
        pendingTerminal: null,
        autoMatch: !!matchApproval,
        pendingMatch: null,
    };
    await setRunnerPointer(pointer);
    if (batchEntry)
        await patchBatchForTask(taskId, { tabId, state: 'running', lastError: null });
    broadcastRunnerState(pointer);
    if (launch) {
        if (binding)
            void afterPopupHandoff(pointer, binding, tab.url, () => runRunnerLoop(taskId, runToken, tabId, task.search_url));
        else
            void runRunnerLoop(taskId, runToken, tabId, task.search_url, !!consoleTab);
    }
    return { ok: true };
}
async function runRunnerLoop(taskId, runToken, tabId, searchUrl, alreadyNavigated = false) {
    const cp0 = await checkpoint(taskId, runToken);
    if (cp0 === 'superseded') {
        releaseRunnerLoop(runToken); // review item 7 - every early exit must release the guard
        return;
    }
    if (cp0 === 'cancel' || cp0 === 'pause') {
        await haltForStop(taskId, runToken);
        return;
    }
    const aliveBefore = await verifyRunnerTab(tabId);
    if (!aliveBefore.ok) {
        await endRunner(taskId, runToken, 'failed', aliveBefore.reason);
        return;
    }
    try {
        if (!alreadyNavigated)
            await chrome.tabs.update(tabId, { url: searchUrl });
    }
    catch (err) {
        await endRunner(taskId, runToken, 'failed', 'navigate_failed:' + String(err?.message || err));
        return;
    }
    const patched = await persistAndReport(taskId, runToken, {
        phase: 'stabilizing',
        currentUrl: searchUrl,
        lastAction: 'navigating',
    });
    if (!patched) {
        releaseRunnerLoop(runToken); // superseded/cleared
        return;
    }
    // `searchUrl` (not just origin/page_type) - review item 5: never accept a
    // stale DOM from the tab's *previous* page (a different city/keyword)
    // just because it also happens to report `page_type: 'search'`.
    const stabilized = await waitForStableSearchPage(taskId, runToken, tabId, searchUrl);
    if (!stabilized.ok) {
        if (stabilized.reason === 'stopped')
            await haltForStop(taskId, runToken);
        else if (stabilized.reason === 'verification')
            await pauseForHumanGate(taskId, runToken, 'verification');
        else if (stabilized.reason === 'login_required')
            await pauseForHumanGate(taskId, runToken, 'login_required');
        else
            await endRunner(taskId, runToken, 'failed', stabilized.error);
        return;
    }
    await rememberRenderedCandidates(taskId, runToken, stabilized.result);
    await persistAndReport(taskId, runToken, { lastAction: 'stabilized' });
    // Only now - the tab is confirmed stable on the SearchTask's own
    // search_url - create the `SupervisedSession`. `create_session` counts
    // the session's starting page exactly once, automatically, at creation
    // (see `supervised_sessions.py`); creating it here rather than before
    // navigating means that automatically-counted page is genuinely this
    // page, never whatever unrelated zhipin.com page the tab was on before
    // the human clicked start (CLAUDE.md M4f review item 6). This is also why
    // this section never calls `navigatePrepareForTab(..., 'results', ...)`
    // itself - the M4b/c design already accounts for the starting page this
    // way, and a second accounting call would just be immediately denied
    // (`pages_visited` already at `page_cap`) or double-count it.
    let session;
    const approved = await readCurrent(taskId, runToken);
    if (!approved) {
        releaseRunnerLoop(runToken);
        return;
    }
    if (!validRunnerBudget(approved)) {
        await endRunner(taskId, runToken, 'failed', 'invalid_candidate_budget');
        return;
    }
    try {
        session = await fetchJson('/api/extension/sessions', {
            method: 'POST',
            body: {
                task_id: taskId,
                page_cap: 1,
                candidate_cap: approved.candidateCap,
                scroll_cap: RUNNER_MAX_SCROLL_ROUNDS,
                tab_origin: RUNNER_REQUIRED_ORIGIN,
            },
        });
    }
    catch (err) {
        await endRunner(taskId, runToken, 'failed', 'session_create_failed:' + String(err?.message || err));
        return;
    }
    await chrome.storage.session.set({ jobagent_session_pointer: { sessionId: session.id, tabId } });
    const withSession = await persistPatch(taskId, runToken, { phase: 'processing', sessionId: session.id });
    if (!withSession) {
        // superseded/cleared - the session above is simply left running; a
        // stale-tab check elsewhere will stop it.
        releaseRunnerLoop(runToken);
        return;
    }
    await runRoundsLoop(taskId, runToken, tabId);
}
/**
 * The shared bounded round loop - candidate processing then one scroll,
 * repeated - used both for a fresh run (after navigate+stabilize+session
 * creation) and for an explicit resume (continuing from wherever the
 * pause/verification left off, with no re-navigation). The stored
 * `scrollsUsed` is the resume point.
 */
async function runRoundsLoop(taskId, runToken, tabId) {
    const start = await readCurrent(taskId, runToken);
    if (!start) {
        releaseRunnerLoop(runToken); // review item 7 - every superseded/cleared early exit must release
        return;
    }
    const startRound = start.scrollsUsed;
    for (let round = startRound; round <= RUNNER_MAX_SCROLL_ROUNDS; round++) {
        const cp = await checkpoint(taskId, runToken);
        if (cp === 'superseded') {
            releaseRunnerLoop(runToken);
            return;
        }
        if (cp === 'cancel' || cp === 'pause') {
            await haltForStop(taskId, runToken);
            return;
        }
        const aliveTop = await verifyRunnerTab(tabId);
        if (!aliveTop.ok) {
            await endRunner(taskId, runToken, 'failed', aliveTop.reason);
            return;
        }
        await persistAndReport(taskId, runToken, { phase: 'processing', lastAction: 'processing_candidates' });
        const candidateOutcome = await processNewCandidates(taskId, runToken, tabId);
        if (candidateOutcome.status === 'login_required') {
            await pauseForHumanGate(taskId, runToken, 'login_required');
            return;
        }
        if (candidateOutcome.status === 'verification') {
            await pauseForHumanGate(taskId, runToken, 'verification');
            return;
        }
        if (candidateOutcome.status === 'wrong_page') {
            await endRunner(taskId, runToken, 'failed', 'wrong_page');
            return;
        }
        if (candidateOutcome.status === 'stopped') {
            await haltForStop(taskId, runToken);
            return;
        }
        if (candidateOutcome.status === 'error') {
            await endRunner(taskId, runToken, 'failed', candidateOutcome.reason);
            return;
        }
        if (candidateOutcome.status === 'cap_reached') {
            await endRunner(taskId, runToken, 'completed');
            return;
        }
        if (round >= RUNNER_MAX_SCROLL_ROUNDS)
            break; // no more scroll rounds left to spend
        const cp2 = await checkpoint(taskId, runToken);
        if (cp2 === 'superseded') {
            releaseRunnerLoop(runToken);
            return;
        }
        if (cp2 === 'cancel' || cp2 === 'pause') {
            await haltForStop(taskId, runToken);
            return;
        }
        const aliveScroll = await verifyRunnerTab(tabId);
        if (!aliveScroll.ok) {
            await endRunner(taskId, runToken, 'failed', aliveScroll.reason);
            return;
        }
        await persistAndReport(taskId, runToken, { phase: 'scrolling', lastAction: 'scrolling' });
        const scrollOutcome = await runScrollRound(taskId, runToken, tabId);
        if (scrollOutcome.status === 'login_required') {
            await pauseForHumanGate(taskId, runToken, 'login_required');
            return;
        }
        if (scrollOutcome.status === 'verification') {
            await pauseForHumanGate(taskId, runToken, 'verification');
            return;
        }
        if (scrollOutcome.status === 'wrong_page') {
            await endRunner(taskId, runToken, 'failed', 'wrong_page');
            return;
        }
        if (scrollOutcome.status === 'stopped') {
            await haltForStop(taskId, runToken);
            return;
        }
        if (scrollOutcome.status === 'cap_reached') {
            // The session's own scroll_cap denied it - a normal, successful end
            // of this run, never a failure or a pausable halt.
            await endRunner(taskId, runToken, 'completed');
            return;
        }
        if (scrollOutcome.status === 'error') {
            await endRunner(taskId, runToken, 'failed', scrollOutcome.reason);
            return;
        }
        const beforeScrollWrite = await readCurrent(taskId, runToken);
        if (!beforeScrollWrite) {
            releaseRunnerLoop(runToken);
            return;
        }
        const updated = await persistAndReport(taskId, runToken, {
            lastAction: 'scrolled',
        });
        if (!updated) {
            releaseRunnerLoop(runToken);
            return;
        }
        let roundRecorded;
        try {
            roundRecorded = (await postTaskRun(taskId, 'round', {
                observed: scrollOutcome.observed,
                new: scrollOutcome.new,
                duplicate: scrollOutcome.duplicate,
                no_new_round_threshold: RUNNER_DEFAULT_NO_NEW_ROUND_THRESHOLD,
            }));
        }
        catch (err) {
            await endRunner(taskId, runToken, 'failed', 'round_record_failed:' + String(err?.message || err));
            return;
        }
        // CLAUDE.md M4f review item 6 - persist the backend's own cumulative
        // task-level counters (never re-derived locally) so the overlay/popup
        // can render them without polling.
        await persistPatch(taskId, runToken, {
            observedCount: roundRecorded.observed_count ?? beforeScrollWrite.observedCount,
            newCount: roundRecorded.new_count ?? beforeScrollWrite.newCount,
            duplicateCount: roundRecorded.duplicate_count ?? beforeScrollWrite.duplicateCount,
            noNewRounds: roundRecorded.no_new_rounds ?? beforeScrollWrite.noNewRounds,
        });
        if (roundRecorded.run_status === 'completed') {
            // The backend's own consecutive-no-new-round threshold already ended
            // this run at the task level (`record_round`) - `skipTransition`
            // (review item 1) so `endRunner` never replays `/run/complete`
            // (`complete_run` requires `running` and would 422), while still
            // stopping the session and clearing the pointer only once that is
            // confirmed, exactly like every other terminal outcome.
            await endRunner(taskId, runToken, 'completed', undefined, { skipTransition: true });
            return;
        }
    }
    await endRunner(taskId, runToken, 'completed');
}
/** CLAUDE.md M4f review item 4 - "pause failure must be visible/retryable."
 * `pauseRequested` is set durably first regardless of the backend call's
 * outcome - the loop will still stop at its next checkpoint either way,
 * exactly as before - but a failed `pause` transition now leaves
 * `pauseConfirmed: false` plus a `lastError` instead of being silently
 * swallowed, and `jobagent:runner-pause` (this same message) is itself the
 * retry: calling it again while already `pauseRequested` simply retries the
 * backend transition without disturbing anything else. */
async function requestRunnerPause() {
    const fresh = await getRunnerPointer();
    if (!fresh)
        return { ok: false };
    const merged = {
        ...fresh,
        pauseRequested: true,
        pausedReason: 'user_pause',
        updatedAt: new Date().toISOString(),
    };
    await setRunnerPointer(merged);
    broadcastRunnerState(merged);
    try {
        await postTaskRun(merged.taskId, 'pause');
    }
    catch (err) {
        await persistPatch(merged.taskId, merged.runToken, {
            lastError: 'pause_transition_failed:' + String(err?.message || err),
        });
        return { ok: true };
    }
    await persistPatch(merged.taskId, merged.runToken, { lastError: null });
    const confirmed = await readCurrent(merged.taskId, merged.runToken);
    if (confirmed) {
        const withConfirm = { ...confirmed, pauseConfirmed: true };
        await setRunnerPointer(withConfirm);
        broadcastRunnerState(withConfirm);
    }
    return { ok: true };
}
/** CLAUDE.md M4f review item 4 - "cancelling an already-paused/
 * verification-halted runner must execute the terminal path even with no
 * active loop." Once paused/verification-halted, no loop is left polling
 * `checkpoint()`, so merely setting `cancelRequested` would never be
 * observed by anything - `activeRunToken` (the in-memory single-flight
 * guard) tells us whether a loop is still actually running for this exact
 * `runToken`; if not, `endRunner` is invoked directly, right here. */
async function requestRunnerCancel() {
    const fresh = await getRunnerPointer();
    if (!fresh)
        return { ok: false };
    const merged = { ...fresh, cancelRequested: true, updatedAt: new Date().toISOString() };
    await setRunnerPointer(merged);
    broadcastRunnerState(merged);
    if (activeRunToken !== fresh.runToken) {
        await endRunner(fresh.taskId, fresh.runToken, 'cancelled');
    }
    return { ok: true };
}
/** Explicit human resume only - never automatic. Mints a fresh `runToken`
 * (so any stale execution's next checkpoint sees a mismatch and stops
 * rather than continuing as a second concurrent loop - see the section
 * banner) and claims the in-memory single-flight guard synchronously before
 * this token is written anywhere, exactly like `startRunner`. Re-enters the
 * loop from wherever it left off; it does not re-navigate (the human may
 * have intervened) and re-validates the page shape at its very next bounded
 * check regardless. */
async function resumeRunner(binding, consoleTaskId) {
    if (salaryOcrBusy)
        return { ok: false, error: 'salary_ocr_busy' };
    if (activeRunToken !== null)
        return { ok: false, error: 'already_running' };
    const newToken = nextRunToken();
    activeRunToken = newToken; // claim before the first await, including concurrent resume clicks
    const fail = (error) => {
        releaseRunnerLoop(newToken);
        return { ok: false, error };
    };
    const pointer = await getRunnerPointer();
    if (!pointer)
        return fail('not_running');
    if (consoleTaskId !== undefined && (pointer.taskId !== consoleTaskId || pointer.autoMatch)) {
        return fail('task_mismatch_or_paid_run');
    }
    if (!validRunnerBudget(pointer)) {
        return fail('旧运行缺少有效的候选上限或计数，请取消旧运行后重新开始。');
    }
    if (!pointer.pauseRequested && !pointer.cancelRequested)
        return fail('not_paused');
    if (pointer.cancelRequested)
        return fail('cancelled');
    let resumeUrl = '';
    let pendingSearchUrl = null;
    if (binding) {
        if (binding.tabId !== pointer.tabId)
            return fail('start-v4/handoff_tab_changed');
        const resolved = await foregroundStartTab(binding);
        if (!resolved.ok)
            return fail(resolved.error);
        if (!resolved.tab.url || !isRunnerNavOrigin(resolved.tab.url))
            return fail('wrong_origin');
        resumeUrl = resolved.tab.url;
    }
    // A pause during popup handoff occurs before navigation/session creation.
    // Resume that unfinished startup, retaining the same spent-budget pointer.
    if (pointer.sessionId === null) {
        try {
            const task = await fetchRunnerTask(pointer.taskId);
            if (!task.search_url || !isRunnerNavOrigin(task.search_url))
                return fail('wrong_origin');
            pendingSearchUrl = task.search_url;
        }
        catch {
            return fail('resume_task_unavailable');
        }
    }
    // CLAUDE.md M4f review item 4 - "resume must not clear pause/change token
    // until backend resume succeeds." Confirm with the backend *before*
    // touching the pointer at all - a failed confirmation leaves
    // `pauseRequested`/`runToken` exactly as they were (only `lastError` is
    // recorded for visibility), never a half-applied resume with no loop
    // behind it.
    try {
        await postTaskRun(pointer.taskId, 'resume');
    }
    catch (err) {
        await persistPatch(pointer.taskId, pointer.runToken, {
            lastError: 'resume_transition_failed:' + String(err?.message || err),
        });
        return fail(String(err?.message || err));
    }
    const updated = {
        ...pointer,
        runToken: newToken,
        pauseRequested: false,
        pauseConfirmed: false,
        pausedReason: null,
        phase: pendingSearchUrl ? 'navigating' : 'processing',
        lastAction: binding ? 'waiting_popup_focus' : 'resumed',
        lastError: null,
        updatedAt: new Date().toISOString(),
    };
    await setRunnerPointer(updated);
    await patchBatchForTask(updated.taskId, { state: 'running', lastError: null });
    broadcastRunnerState(updated);
    const proceed = () => pendingSearchUrl
        ? runRunnerLoop(pointer.taskId, newToken, pointer.tabId, pendingSearchUrl)
        : runRoundsLoop(pointer.taskId, newToken, pointer.tabId);
    if (binding)
        void afterPopupHandoff(updated, binding, resumeUrl, proceed);
    else
        void proceed();
    return { ok: true };
}
/** CLAUDE.md M4f review item 3's recovery path - re-attempts the terminal
 * transition/session-stop `endRunner` could not confirm last time, using
 * exactly the outcome/error it recorded. A no-op (reports `ok:false`) if
 * nothing is actually pending. */
async function retryRunnerTerminal() {
    const pointer = await getRunnerPointer();
    if (!pointer || !pointer.pendingTerminal)
        return { ok: false, error: 'nothing_pending' };
    await endRunner(pointer.taskId, pointer.runToken, pointer.pendingTerminal.outcome, pointer.pendingTerminal.error);
    const after = await getRunnerPointer();
    if (after && after.pendingTerminal)
        return { ok: false, error: after.lastError || 'still_pending' };
    return { ok: true };
}
// Only our exact local UI, top frame, may use this transport. Never expose the
// trusted pointer (tab/session ids, run token, discovery history) to the page.
function isConsoleUrl(raw) {
    try {
        const url = new URL(raw || '');
        return [
            'http://127.0.0.1:5173', 'http://localhost:5173',
            'http://127.0.0.1:8000', 'http://localhost:8000',
        ].includes(url.origin)
            && !url.username && !url.password && url.pathname === '/'
            && ['#/console', '#/queue', '#/recruiter'].includes(url.hash) && !url.search;
    }
    catch {
        return false;
    }
}
function isConsoleSearchUrl(raw) {
    try {
        const url = new URL(raw);
        return isRunnerNavOrigin(raw) && !url.username && !url.password && !url.hash
            && url.pathname === '/web/geek/jobs' && /^\d+$/.test(url.searchParams.get('city') || '')
            && !!url.searchParams.get('query')
            && Array.from(url.searchParams.keys()).every(key => key === 'city' || key === 'query');
    }
    catch {
        return false;
    }
}
async function consoleSourceTab(sender, foreground) {
    if (sender.frameId !== 0 || !isConsoleUrl(sender.url) || sender.tab?.id === undefined)
        throw new Error('untrusted_console');
    const tab = await chrome.tabs.get(sender.tab.id);
    if (!isConsoleUrl(tab.url) || tab.windowId === undefined)
        throw new Error('console_changed');
    if (foreground) {
        const window = await chrome.windows.getLastFocused({ windowTypes: ['normal'] });
        if (!tab.active || !window.focused || window.type !== 'normal' || window.id !== tab.windowId)
            throw new Error('console_not_foreground');
    }
    return tab;
}
async function startBatch(taskIds, candidateCap, sender) {
    if (!validBatchTaskIds(taskIds))
        return { ok: false, error: '批次必须包含 1–16 个不重复的待处理任务。' };
    if (!validCandidateCap(candidateCap))
        return { ok: false, error: '候选上限必须是 1–20 的整数。' };
    if (batchAdmission)
        return { ok: false, error: '批次启动确认正在处理，请勿重复提交。' };
    batchAdmission = true;
    try {
        if (activeRunToken !== null || await getRunnerPointer() || await getGlobalPointer()) {
            return { ok: false, error: '已有任务运行；不会并发启动批次。' };
        }
        const existing = await getBatchPointer();
        if (existing && ['running', 'paused'].includes(existing.state))
            return { ok: false, error: '已有批次运行。' };
        // Validate the whole approved list before the first browser side effect.
        const validatedTasks = [];
        for (const taskId of taskIds) {
            let task;
            try {
                task = await fetchRunnerTask(taskId);
            }
            catch {
                return { ok: false, error: '批次任务读取失败；尚未启动。' };
            }
            if (task.run_status !== 'pending' || !task.search_url || !isConsoleSearchUrl(task.search_url)) {
                return { ok: false, error: `任务 #${taskId} 已变化或不是待处理搜索任务；尚未启动。` };
            }
            if (task.max_candidates != null && (!validCandidateCap(task.max_candidates)
                || candidateCap > task.max_candidates))
                return { ok: false, error: `任务 #${taskId} 的候选上限不足；尚未启动。` };
            validatedTasks.push(task);
        }
        const batch = { taskIds: [...taskIds], candidateCap, currentIndex: 0,
            state: 'running', tabId: null, lastError: null, updatedAt: new Date().toISOString() };
        await setBatchPointer(batch);
        const started = await startRunner(taskIds[0], candidateCap, undefined, undefined, sender, true, undefined, false);
        if (!started.ok) {
            await setBatchPointer({ ...batch, state: 'stopped',
                lastError: ('first_task_start_failed:' + (started.error || 'unknown')).slice(0, 120),
                updatedAt: new Date().toISOString() });
            return started;
        }
        const admitted = await getBatchPointer();
        const runner = await getRunnerPointer();
        if (!admitted || admitted.taskIds[admitted.currentIndex] !== taskIds[0] || admitted.tabId === null
            || !runner || runner.taskId !== taskIds[0] || runner.tabId !== admitted.tabId) {
            await setBatchPointer({ ...batch, state: 'stopped', lastError: 'first_task_pointer_missing',
                updatedAt: new Date().toISOString() });
            return { ok: false, error: '首个任务状态未确认；批次已停止。' };
        }
        void runRunnerLoop(runner.taskId, runner.runToken, runner.tabId, validatedTasks[0].search_url, true);
        return { ok: true };
    }
    finally {
        batchAdmission = false;
    }
}
/** Explicit recovery after a worker restart. It never changes backend state
 * when the current task is still running, and never replenishes any budget. */
async function resumeBatch(source) {
    const batch = await getBatchPointer();
    const pointer = await getRunnerPointer();
    if (!batch || !pointer || !['running', 'paused'].includes(batch.state)
        || batch.taskIds[batch.currentIndex] !== pointer.taskId || batch.tabId !== pointer.tabId) {
        return { ok: false, error: '没有可恢复的有界批次。' };
    }
    if (pointer.autoMatch)
        return { ok: false, error: '批次入口不恢复付费任务。' };
    if (activeRunToken !== null)
        return { ok: false, error: '批次仍在运行，无需恢复。' };
    let target;
    try {
        target = await chrome.tabs.get(pointer.tabId);
    }
    catch {
        return { ok: false, error: '原 BOSS 标签页已关闭，批次不能恢复。' };
    }
    if (target.windowId !== source.windowId || !target.url || !isRunnerNavOrigin(target.url)) {
        return { ok: false, error: '原 BOSS 标签页已移动或离开 BOSS，批次不能恢复。' };
    }
    await chrome.tabs.update(pointer.tabId, { active: true });
    const foreground = await foregroundStartTab();
    if (!foreground.ok || foreground.tab.id !== pointer.tabId)
        return { ok: false, error: 'BOSS 标签页未处于前台。' };
    const task = await fetchRunnerTask(pointer.taskId);
    if (pointer.pauseRequested) {
        if (!['paused', 'paused_verification', 'paused_login_required'].includes(task.run_status || '')) {
            return { ok: false, error: '后端尚未确认暂停，请刷新状态。' };
        }
        return resumeRunner(undefined, pointer.taskId);
    }
    if (task.run_status !== 'running')
        return { ok: false, error: '当前批次任务已不在运行状态，不能恢复。' };
    const newToken = nextRunToken();
    if (!claimRunnerLoop(newToken))
        return { ok: false, error: 'already_running' };
    let pendingSearchUrl = null;
    if (pointer.sessionId === null) {
        if (!task.search_url || !isConsoleSearchUrl(task.search_url)) {
            releaseRunnerLoop(newToken);
            return { ok: false, error: '任务搜索地址无效，不能恢复。' };
        }
        pendingSearchUrl = task.search_url;
    }
    const updated = { ...pointer, runToken: newToken, phase: pendingSearchUrl ? 'navigating' : 'processing',
        pauseRequested: false, pauseConfirmed: false, lastAction: 'batch_resumed_after_worker_restart',
        pausedReason: null, lastError: null, updatedAt: new Date().toISOString() };
    await setRunnerPointer(updated);
    await patchBatchForTask(updated.taskId, { state: 'running', lastError: null });
    broadcastRunnerState(updated);
    if (pendingSearchUrl)
        void runRunnerLoop(updated.taskId, newToken, updated.tabId, pendingSearchUrl);
    else
        void runRoundsLoop(updated.taskId, newToken, updated.tabId);
    return { ok: true };
}
async function runSalaryBackfillLoop(runId, tabId) {
    if (salaryBackfillBusy)
        return;
    salaryBackfillBusy = true;
    try {
        while (true) {
            const pointer = await getSalaryBackfillPointer();
            if (!pointer || pointer.runId !== runId || pointer.tabId !== tabId || !pointer.active)
                return;
            let run = await fetchJson(`/api/jobs/salary-backfill/runs/${runId}/claim`, { method: 'POST' });
            if (run.state !== 'running' || run.current_job_id === null) {
                await setSalaryBackfillPointer(run.state === 'completed' || run.state === 'cancelled' ? null
                    : { ...pointer, active: false, updatedAt: new Date().toISOString() });
                return;
            }
            const item = run.items.find(value => value.job_id === run.current_job_id);
            const canonical = item && canonicalizeJobDetailUrl(item.source_url);
            if (!item || !canonical)
                throw new Error('invalid_backfill_identity');
            const alive = await verifyRunnerTab(tabId);
            if (!alive.ok)
                throw new Error(alive.reason);
            await chrome.tabs.update(tabId, { url: canonical, active: true });
            let detected = null;
            for (let attempt = 0; attempt < RUNNER_CAPTURE_MAX_ATTEMPTS; attempt += 1) {
                const response = await askTab(tabId, { type: 'jobagent:detect' }, attempt === 0);
                if (response.ok && response.result) {
                    if (response.result.login_required) {
                        await fetchJson(`/api/jobs/salary-backfill/runs/${runId}/pause`, {
                            method: 'POST', body: { reason: 'login_required' },
                        });
                        await setSalaryBackfillPointer({ ...pointer, active: false, updatedAt: new Date().toISOString() });
                        return;
                    }
                    if (response.result.verification) {
                        await fetchJson(`/api/jobs/salary-backfill/runs/${runId}/pause`, {
                            method: 'POST', body: { reason: 'verification' },
                        });
                        await setSalaryBackfillPointer({ ...pointer, active: false, updatedAt: new Date().toISOString() });
                        return;
                    }
                    const candidate = response.result.candidates[0];
                    if (response.result.page_type === 'detail' && candidate
                        && canonicalizeJobDetailUrl(candidate.source_url) === canonical) {
                        detected = response.result;
                        break;
                    }
                }
                await sleepMs(RUNNER_CAPTURE_INTERVAL_MS);
            }
            let outcome = 'unavailable';
            let reason = 'detail_unavailable';
            if (detected?.candidates[0]) {
                let candidate = detected.candidates[0];
                try {
                    if (!candidate.salary_text)
                        candidate = await supplementSalary(tabId, candidate, async () => {
                            const current = await getSalaryBackfillPointer();
                            return !!current && current.runId === runId && current.tabId === tabId && current.active;
                        });
                    if (candidate.salary_text) {
                        const preview = await fetchJson('/api/extension/jobs/preview', {
                            method: 'POST', body: { page_type: 'detail', page_url: canonical, candidates: [candidate] },
                        });
                        const row = preview.rows?.[0];
                        if (row?.status !== 'duplicate' || row.existing_job_id !== item.job_id
                            || !row.enrichable_fields?.includes('salary_text'))
                            throw new Error('intake_identity_mismatch');
                        const beforeImport = await verifyRunnerTab(tabId);
                        if (!beforeImport.ok)
                            throw new Error(beforeImport.reason);
                        const imported = await fetchJson('/api/extension/jobs/import', {
                            method: 'POST', body: { confirmed: true, candidate },
                        });
                        if (imported.job_id !== item.job_id || imported.duplicate !== true)
                            throw new Error('intake_result_mismatch');
                        outcome = 'updated';
                        reason = 'salary_enriched';
                    }
                    else
                        reason = 'salary_unreadable';
                }
                catch (error) {
                    const code = error instanceof Error ? error.message : 'unknown';
                    if (code === 'salary_login_required' || code === 'salary_verification') {
                        await fetchJson(`/api/jobs/salary-backfill/runs/${runId}/pause`, {
                            method: 'POST', body: { reason: code === 'salary_login_required' ? 'login_required' : 'verification' },
                        });
                        await setSalaryBackfillPointer({ ...pointer, active: false, updatedAt: new Date().toISOString() });
                        return;
                    }
                    outcome = 'failed';
                    reason = code.slice(0, 128);
                }
            }
            run = await fetchJson(`/api/jobs/salary-backfill/runs/${runId}/item`, {
                method: 'POST', body: { job_id: item.job_id, outcome, reason },
            });
            await setSalaryBackfillPointer(run.state === 'completed' || run.state === 'cancelled' ? null
                : { runId, tabId, active: run.state === 'running', updatedAt: new Date().toISOString() });
            if (run.state !== 'running')
                return;
        }
    }
    catch {
        try {
            const run = await fetchJson(`/api/jobs/salary-backfill/runs/${runId}`);
            if (run.state === 'running')
                await fetchJson(`/api/jobs/salary-backfill/runs/${runId}/pause`, {
                    method: 'POST', body: { reason: 'worker_error' },
                });
        }
        catch { /* backend state remains observable; never guess success */ }
        const pointer = await getSalaryBackfillPointer();
        if (pointer?.runId === runId)
            await setSalaryBackfillPointer({ ...pointer, active: false,
                updatedAt: new Date().toISOString() });
    }
    finally {
        salaryBackfillBusy = false;
    }
}
async function startSalaryBackfill(runId, source) {
    if (salaryBackfillBusy || activeRunToken !== null || await getRunnerPointer() || await getGlobalPointer()) {
        return { ok: false, error: '已有浏览器任务运行，不会并发回填。' };
    }
    if (source.windowId === undefined)
        return { ok: false, error: '找不到当前 Chrome 窗口。' };
    const existing = await getSalaryBackfillPointer();
    if (existing && existing.runId !== runId)
        return { ok: false, error: '已有其他薪资回填计划。' };
    const tab = existing ? await chrome.tabs.get(existing.tabId) : await reusableBossTab(source.windowId);
    if (!tab?.id || !tab.url || !isRunnerNavOrigin(tab.url) || tab.windowId !== source.windowId) {
        return { ok: false, error: '请在同一 Chrome 窗口保持已登录的 BOSS 标签页。' };
    }
    await chrome.tabs.update(tab.id, { active: true });
    const focused = await foregroundStartTab();
    if (!focused.ok || focused.tab.id !== tab.id)
        return { ok: false, error: 'BOSS 标签页未处于前台。' };
    const current = await fetchJson(`/api/jobs/salary-backfill/runs/${runId}`);
    if (current.state !== 'running') {
        await fetchJson(`/api/jobs/salary-backfill/runs/${runId}/start`, { method: 'POST' });
    }
    const pointer = { runId, tabId: tab.id, active: true, updatedAt: new Date().toISOString() };
    await setSalaryBackfillPointer(pointer);
    void runSalaryBackfillLoop(runId, tab.id);
    return { ok: true };
}
let m6AttemptBusy = false;
function isM6CanonicalUrl(raw, externalId) {
    try {
        const url = new URL(raw);
        return url.origin === REQUIRED_ORIGIN && !url.username && !url.password
            && !url.search && !url.hash
            && url.pathname === `/job_detail/${externalId}.html`
            && /^[A-Za-z0-9_-]+$/.test(externalId);
    }
    catch {
        return false;
    }
}
/** One event-driven navigation wait. The timeout can only stop; it never triggers a click. */
async function openM6Detail(tabId, canonicalUrl) {
    let timeoutId;
    return new Promise((resolve) => {
        let settled = false;
        const finish = (ok) => {
            if (settled)
                return;
            settled = true;
            if (timeoutId !== undefined)
                clearTimeout(timeoutId);
            chrome.tabs.onUpdated.removeListener(updated);
            resolve(ok);
        };
        const updated = (changedId, change) => {
            if (changedId === tabId && change.status === 'complete')
                finish(true);
        };
        chrome.tabs.onUpdated.addListener(updated);
        timeoutId = setTimeout(() => finish(false), 15000);
        void chrome.tabs.update(tabId, { url: canonicalUrl, active: true }).catch(() => finish(false));
    });
}
async function settleM6Outcome(approval, identity, outcome, detail) {
    await fetchJson(`/api/application-approvals/${approval.id}/outcome`, {
        method: 'POST',
        body: { ...identity, outcome, detail },
    });
}
async function executeM6Application(approvalId, source) {
    if (m6AttemptBusy)
        return { ok: false, error: '已有单岗位投递确认正在处理；不会并发或重复执行。' };
    m6AttemptBusy = true;
    try {
        if (activeRunToken !== null || salaryBackfillBusy || await getRunnerPointer()
            || await getGlobalPointer())
            return { ok: false, error: '已有浏览器任务运行；请先结束后再逐岗位确认。' };
        const approval = await fetchJson(`/api/application-approvals/${approvalId}`);
        if (approval.id !== approvalId || approval.state !== 'pending'
            || approval.answers_source !== 'boss_dynamic_unverified'
            || approval.answers_text !== ''
            || !isM6CanonicalUrl(approval.canonical_url, approval.external_id)) {
            return { ok: false, error: '投递确认无效、已使用或不是未知动态招呼语模式；不会执行。' };
        }
        const existing = await reusableBossTab(source.windowId);
        const target = existing?.id
            ? await chrome.tabs.update(existing.id, { active: true })
            : await chrome.tabs.create({ url: approval.canonical_url, active: true, windowId: source.windowId });
        if (target.id === undefined)
            return { ok: false, error: '无法建立前台 BOSS 标签页；不会执行。' };
        const focused = await foregroundStartTab();
        if (!focused.ok || focused.tab.id !== target.id)
            return { ok: false, error: 'BOSS 标签页不是前台；不会执行。' };
        if (!target.url || new URL(target.url).origin !== REQUIRED_ORIGIN)
            return { ok: false, error: 'BOSS 标签页来源不正确；不会执行。' };
        if (target.url.split('?')[0].split('#')[0] !== approval.canonical_url) {
            if (!await openM6Detail(target.id, approval.canonical_url)) {
                return { ok: false, error: '岗位详情页未在时限内加载；不会执行或重试。' };
            }
        }
        const identity = {
            canonical_url: approval.canonical_url,
            external_id: approval.external_id,
            company: approval.company,
            title: approval.title,
        };
        const preflight = await askTab(target.id, {
            type: 'jobagent:m6-preflight', applicationIdentity: identity,
        }, true);
        if (!preflight.ok || !preflight.result || preflight.result.status !== 'ok'
            || !preflight.result.observed_url || !preflight.result.observed_external_id) {
            return { ok: false, error: `投递前检查未通过：${preflight.result?.status || preflight.error || 'unavailable'}。不会执行。` };
        }
        const observed = {
            observed_url: preflight.result.observed_url,
            observed_external_id: preflight.result.observed_external_id,
        };
        await fetchJson(`/api/application-approvals/${approval.id}/validate`, {
            method: 'POST', body: observed,
        });
        await fetchJson(`/api/application-approvals/${approval.id}/begin`, {
            method: 'POST', body: observed,
        });
        const alive = await verifyRunnerTab(target.id);
        if (!alive.ok) {
            await settleM6Outcome(approval, observed, 'failed', `foreground_lost:${alive.reason}`);
            return { ok: false, application: { approvalId, outcome: 'failed', detail: alive.reason }, error: '领取尝试后前台状态变化；未点击且不会重试。' };
        }
        const executed = await askTab(target.id, {
            type: 'jobagent:m6-execute', applicationIdentity: identity,
        });
        if (!executed.ok || !executed.result) {
            await settleM6Outcome(approval, observed, 'unknown', 'content_response_unknown');
            return { ok: false, application: { approvalId, outcome: 'unknown', detail: 'content_response_unknown' }, error: '点击结果无法确认；请到 BOSS 人工核对，绝不要直接重试。' };
        }
        if (executed.result.status !== 'clicked') {
            await settleM6Outcome(approval, observed, 'failed', executed.result.status);
            return { ok: false, application: { approvalId, outcome: 'failed', detail: executed.result.status }, error: '最终页面检查未通过，未点击且不会自动重试。' };
        }
        // No live success-state fixture exists yet. A click/greeting is therefore
        // always recorded as unknown, never guessed into Job.status=applied.
        await settleM6Outcome(approval, observed, 'unknown', 'clicked_site_result_unverified');
        return { ok: true, application: { approvalId, outcome: 'unknown', detail: 'clicked_site_result_unverified' } };
    }
    catch {
        return { ok: false, error: '单岗位投递执行中断或后端结果未确认；请人工核对，不会自动重试。' };
    }
    finally {
        m6AttemptBusy = false;
    }
}
async function consoleCommand(message, sender) {
    const data = message;
    const foregroundAction = ['start', 'resume', 'start-batch', 'resume-batch',
        'start-salary-backfill', 'resume-salary-backfill', 'execute-application'].includes(data.action || '');
    const source = await consoleSourceTab(sender, foregroundAction);
    if (data.action === 'status') {
        const pointer = await getRunnerPointer();
        const batch = await getBatchPointer();
        const salaryBackfill = await getSalaryBackfillPointer();
        return { ok: true, protocol: 1, extensionVersion: chrome.runtime.getManifest().version,
            capabilities: ['console-search-v1', 'console-batch-v1', 'salary-backfill-v1',
                'human-confirmed-apply-v1'], runner: pointer ? {
                taskId: pointer.taskId, phase: pointer.phase, paused: pointer.pauseRequested,
                paid: pointer.autoMatch === true, candidateCap: pointer.candidateCap,
            } : null, batch: batch ? {
                state: batch.state, taskIds: batch.taskIds, currentIndex: batch.currentIndex,
                currentTaskId: batch.taskIds[batch.currentIndex], candidateCap: batch.candidateCap,
                requiresResume: ['running', 'paused'].includes(batch.state) && !!pointer
                    && activeRunToken !== pointer.runToken,
                lastError: batch.lastError,
            } : null, salaryBackfill: salaryBackfill ? {
                runId: salaryBackfill.runId, active: salaryBackfill.active,
                updatedAt: salaryBackfill.updatedAt,
            } : null };
    }
    if (data.action === 'execute-application') {
        if (!Number.isSafeInteger(data.approvalId) || data.approvalId <= 0) {
            return { ok: false, error: 'bad_approval_id' };
        }
        return executeM6Application(data.approvalId, source);
    }
    if (['start-salary-backfill', 'resume-salary-backfill'].includes(data.action || '')) {
        if (!Number.isSafeInteger(data.runId) || data.runId <= 0)
            return { ok: false, error: 'bad_run_id' };
        return startSalaryBackfill(data.runId, source);
    }
    if (['pause-salary-backfill', 'cancel-salary-backfill'].includes(data.action || '')) {
        const stored = await getSalaryBackfillPointer();
        if (!stored || stored.runId !== data.runId)
            return { ok: false, error: '没有可操作的薪资回填计划。' };
        const action = data.action === 'pause-salary-backfill' ? 'pause' : 'cancel';
        await fetchJson(`/api/jobs/salary-backfill/runs/${stored.runId}/${action}`, {
            method: 'POST', body: action === 'pause' ? { reason: 'user_pause' } : {},
        });
        await setSalaryBackfillPointer(action === 'pause' ? { ...stored, active: false,
            updatedAt: new Date().toISOString() } : null);
        return { ok: true };
    }
    if (data.action === 'start-batch')
        return startBatch(data.taskIds, data.candidateCap, sender);
    if (data.action === 'pause-batch' || data.action === 'cancel-batch') {
        const batch = await getBatchPointer();
        const pointer = await getRunnerPointer();
        if (!batch || !pointer || !['running', 'paused'].includes(batch.state)
            || batch.taskIds[batch.currentIndex] !== pointer.taskId || batch.tabId !== pointer.tabId) {
            return { ok: false, error: '没有可操作的有界批次。' };
        }
        return data.action === 'pause-batch' ? requestRunnerPause() : requestRunnerCancel();
    }
    if (data.action === 'resume-batch')
        return resumeBatch(source);
    if (!Number.isSafeInteger(data.taskId) || data.taskId <= 0)
        return { ok: false, error: 'bad_task_id' };
    if (data.action === 'start')
        return startRunner(data.taskId, data.candidateCap, undefined, undefined, sender);
    const pointer = await getRunnerPointer();
    if (!pointer || pointer.taskId !== data.taskId)
        return { ok: false, error: '本扩展没有该任务的运行记录，请刷新状态；不会操作其他任务。' };
    if (data.action === 'pause')
        return requestRunnerPause();
    if (data.action === 'cancel')
        return requestRunnerCancel();
    if (data.action === 'resume') {
        if (pointer.autoMatch)
            return { ok: false, error: '含 AI 费用的旧任务请通过原扩展入口恢复；此入口仅免费采集。' };
        if (!pointer.pauseRequested || activeRunToken !== null)
            return { ok: false, error: '请等待任务确认暂停后再恢复。' };
        // Verification pauses use their own backend transition, not the user-pause
        // acknowledgement flag. Neither kind may activate a tab before confirmation.
        const task = await fetchRunnerTask(pointer.taskId);
        if (!['paused', 'paused_verification', 'paused_login_required'].includes(task.run_status || '')) {
            return { ok: false, error: '后端尚未确认暂停，请刷新状态。' };
        }
        const target = await chrome.tabs.get(pointer.tabId);
        if (target.windowId !== source.windowId || !target.url || !isRunnerNavOrigin(target.url))
            return { ok: false, error: '原 BOSS 标签页已关闭、移动或离开 BOSS，无法恢复。' };
        await chrome.tabs.update(pointer.tabId, { active: true });
        const foreground = await foregroundStartTab();
        if (!foreground.ok || foreground.tab.id !== pointer.tabId)
            return { ok: false, error: 'BOSS 标签页未处于前台。' };
        return resumeRunner(undefined, data.taskId);
    }
    return { ok: false, error: 'unsupported_console_command' };
}
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    const request = (message || {});
    if (request.type === 'jobagent:console-command') {
        void consoleCommand(message, sender).then(sendResponse, () => sendResponse({ ok: false,
            code: 'worker_rejected',
            error: '控制台连接失效或来源不受信任；请在正常 Chrome 刷新本地控制台。' }));
        return true;
    }
    if (isConsoleUrl(sender.url))
        return false; // no access to the legacy popup protocol
    // Popup only. Re-read the candidate from Chrome, never accept supplied image/identity.
    if (request.type === 'jobagent:detect-with-salary' && sender.tab?.id === undefined) {
        void (async () => {
            if (activeRunToken !== null || salaryOcrBusy)
                throw new Error('请先暂停自动任务，再进行单独检测。');
            const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
            const tabId = tabs[0]?.id;
            if (tabId === undefined)
                throw new Error('找不到当前标签页。');
            const response = await askTab(tabId, { type: 'jobagent:detect' });
            if (!response.ok || !response.result)
                throw new Error('页面未就绪，请刷新 BOSS 页面。');
            if (!response.result.verification && response.result.page_type === 'detail' && response.result.candidates.length === 1) {
                response.result.candidates[0] = await supplementSalary(tabId, response.result.candidates[0], async () => activeRunToken === null);
            }
            return response;
        })().then(sendResponse, () => sendResponse({ ok: false, error: '检测中断：页面/任务已变化，或扩展未就绪；请刷新后重试。' }));
        return true;
    }
    if (request.type === 'jobagent:session-snapshot') {
        void buildSnapshot(sender.tab?.id).then((result) => sendResponse(result));
        return true; // asynchronous response
    }
    if (request.type === 'jobagent:stop-session') {
        const payload = message;
        const allowed = [
            'user_stop',
            'stale_tab',
            'verification',
            'wrong_origin',
            'wrong_page',
            'no_candidates',
            'loop_detected',
            'prepare_denied',
            'click_failed',
            'confirm_failed',
            'identity_mismatch',
        ];
        const reason = allowed.indexOf(payload.reason || '') !== -1
            ? payload.reason
            : 'user_stop';
        void stopForTab(sender.tab?.id, reason).then((result) => sendResponse(result));
        return true; // asynchronous response
    }
    if (request.type === 'jobagent:navigate-prepare') {
        const payload = message;
        if (!isNavigateTarget(payload.target)) {
            sendResponse({ ok: false, error: 'bad_target' });
            return false;
        }
        void navigatePrepareForTab(sender.tab?.id, payload.target, payload.pageUrl ?? null).then((result) => sendResponse(result));
        return true; // asynchronous response
    }
    if (request.type === 'jobagent:navigate-confirm') {
        const payload = message;
        if (!isNavigateTarget(payload.target)) {
            sendResponse({ ok: false, error: 'bad_target' });
            return false;
        }
        if (payload.outcome !== 'success' && payload.outcome !== 'failed') {
            sendResponse({ ok: false, error: 'bad_outcome' });
            return false;
        }
        void navigateConfirmForTab(sender.tab?.id, payload.target, payload.pageUrl ?? null, payload.outcome, payload.error ?? null).then((result) => sendResponse(result));
        return true; // asynchronous response
    }
    if (request.type === 'jobagent:send-preview') {
        const payload = message;
        if (payload.pageType !== 'search' && payload.pageType !== 'detail') {
            sendResponse({ ok: false, error: 'bad_page_type' });
            return false;
        }
        if (!payload.candidate) {
            sendResponse({ ok: false, error: 'no_candidate' });
            return false;
        }
        void sendPreviewForTab(sender.tab?.id, payload.pageType, payload.pageUrl ?? null, payload.candidate).then((result) => sendResponse(result));
        return true; // asynchronous response
    }
    if (request.type === 'jobagent:runner-start') {
        const payload = message;
        if (typeof payload.taskId !== 'number') {
            sendResponse({ ok: false, error: 'bad_task_id' });
            return false;
        }
        void (async () => {
            const binding = payload.popupTarget === undefined ? undefined : await bindPopupTarget(payload.popupTarget, sender);
            return startRunner(payload.taskId, payload.candidateCap, binding, payload.matchApproval);
        })().then(sendResponse, () => sendResponse({ ok: false, error: 'start-v4/popup_binding_failed' }));
        return true;
    }
    if (request.type === 'jobagent:runner-status') {
        void getRunnerPointer().then((pointer) => sendResponse({ ok: true, runner: pointer }));
        return true;
    }
    if (request.type === 'jobagent:runner-pause') {
        void requestRunnerPause().then((result) => sendResponse(result));
        return true;
    }
    if (request.type === 'jobagent:runner-resume') {
        const payload = message;
        void (async () => {
            const binding = payload.popupTarget === undefined ? undefined : await bindPopupTarget(payload.popupTarget, sender);
            return resumeRunner(binding);
        })().then(sendResponse, () => sendResponse({ ok: false, error: 'start-v4/popup_binding_failed' }));
        return true;
    }
    if (request.type === 'jobagent:runner-cancel') {
        void requestRunnerCancel().then((result) => sendResponse(result));
        return true;
    }
    if (request.type === 'jobagent:runner-retry-terminal') {
        void retryRunnerTerminal().then((result) => sendResponse(result));
        return true;
    }
    return false;
});
