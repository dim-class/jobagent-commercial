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
 * tab. No timer, no alarm, no navigation, no `tabs.update`/`tabs.create`,
 * no `webNavigation`. See CLAUDE.md's "Chrome extension - M4 supervised
 * navigation policy".
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
        throw new Error(message || `HTTP ${response.status}`);
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
        return { ok: false, error: String(err?.message || err) };
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
        return { ok: false, error: String(err?.message || err) };
    }
}
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    const request = (message || {});
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
        ];
        const reason = allowed.indexOf(payload.reason || '') !== -1
            ? payload.reason
            : 'user_stop';
        void stopForTab(sender.tab?.id, reason).then((result) => sendResponse(result));
        return true; // asynchronous response
    }
    if (request.type === 'jobagent:navigate-prepare') {
        const payload = message;
        if (payload.target !== 'results' && payload.target !== 'detail') {
            sendResponse({ ok: false, error: 'bad_target' });
            return false;
        }
        void navigatePrepareForTab(sender.tab?.id, payload.target, payload.pageUrl ?? null).then((result) => sendResponse(result));
        return true; // asynchronous response
    }
    if (request.type === 'jobagent:navigate-confirm') {
        const payload = message;
        if (payload.target !== 'results' && payload.target !== 'detail') {
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
    return false;
});
