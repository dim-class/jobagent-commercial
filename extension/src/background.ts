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

const STORAGE_KEY = 'jobagent_session_pointer'
const BACKEND_BASE = 'http://127.0.0.1:8000'
const REQUIRED_ORIGIN = 'https://www.zhipin.com'

interface StoredPointer {
  sessionId: number
  tabId: number
}

interface SessionOut {
  id: number
  status: 'running' | 'stopped'
  page_cap: number
  candidate_cap: number
  scroll_cap: number
  tab_origin: string
  pages_visited: number
  candidates_extracted: number
  scrolls_used: number
  approved_criteria: { task_name: string }
}

type Snapshot =
  | { kind: 'session'; session: SessionOut }
  | { kind: 'other_tab' }
  | { kind: 'none' }
  | { kind: 'unreachable' }

interface RuntimeRequest {
  type?: string
}

async function getGlobalPointer(): Promise<StoredPointer | null> {
  const stored = await chrome.storage.session.get(STORAGE_KEY)
  return (stored[STORAGE_KEY] as StoredPointer | undefined) || null
}

async function clearGlobalPointer(): Promise<void> {
  await chrome.storage.session.remove(STORAGE_KEY)
}

async function fetchJson<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const response = await fetch(BACKEND_BASE + path, {
    method: init?.method || 'GET',
    headers: init?.body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
  })
  const text = await response.text()
  const payload: unknown = text ? JSON.parse(text) : null
  if (!response.ok) {
    const message = (payload as { message?: string } | null)?.message
    throw new Error(message || `HTTP ${response.status}`)
  }
  return payload as T
}

function fetchActive(): Promise<SessionOut | null> {
  return fetchJson<SessionOut | null>('/api/extension/sessions/active')
}

//: `user_stop` (the Stop button) and `stale_tab` (M4a's fail-closed reload/
//: restart path) are unchanged from M4a. The rest are M4b policy hard
//: stops, each ending the whole session, never just refusing one step -
//: see CLAUDE.md's "Chrome extension - M4 supervised navigation policy"
//: section 6.
type StopReason =
  | 'user_stop'
  | 'stale_tab'
  | 'verification'
  | 'wrong_origin'
  | 'wrong_page'
  | 'no_candidates'
  | 'loop_detected'
  | 'prepare_denied'
  | 'click_failed'
  | 'confirm_failed'
  | 'identity_mismatch'

async function postStop(sessionId: number, reason: StopReason): Promise<void> {
  await fetchJson(`/api/extension/sessions/${sessionId}/stop`, {
    method: 'POST',
    body: { reason },
  })
}

/** Best effort - a failed stop attempt here must never surface as a thrown
 * error to the caller; the pointer is simply left untouched either way. */
async function tryStopIfRunning(active: SessionOut | null, reason: StopReason) {
  if (!active || active.status !== 'running') return
  try {
    await postStop(active.id, reason)
  } catch {
    // Nothing more this worker can do right now.
  }
}

async function buildSnapshot(tabId: number | undefined): Promise<Snapshot> {
  if (tabId === undefined) return { kind: 'none' }

  const pointer = await getGlobalPointer()

  if (pointer && pointer.tabId !== tabId) {
    // A session is approved for a different tab - silent, no backend call
    // at all, and the response never carries that tab's pointer or session id.
    return { kind: 'other_tab' }
  }

  if (!pointer) {
    // No pointer anywhere. Only a *confirmed* still-running backend session
    // is a genuine zombie (browser restart, or storage.session was
    // otherwise cleared) - an unreachable backend proves nothing and must
    // not be treated as confirmation of anything.
    let active: SessionOut | null
    try {
      active = await fetchActive()
    } catch {
      return { kind: 'none' }
    }
    await tryStopIfRunning(active, 'stale_tab')
    return { kind: 'none' }
  }

  // The pointer belongs to this tab.
  let active: SessionOut | null
  try {
    active = await fetchActive()
  } catch {
    // Unreachable - preserve the pointer and the backend session untouched;
    // the caller shows nothing this pass and gets a fair chance next time.
    return { kind: 'unreachable' }
  }

  const matches =
    !!active &&
    active.status === 'running' &&
    active.id === pointer.sessionId &&
    active.tab_origin === REQUIRED_ORIGIN

  if (matches) {
    return { kind: 'session', session: active as SessionOut }
  }

  // The backend was reachable and confirmed a disagreement - only now is it
  // safe to fail closed for real.
  await tryStopIfRunning(active, 'stale_tab')
  await clearGlobalPointer()
  releasePrepareLock(pointer.tabId)
  return { kind: 'none' }
}

async function stopForTab(
  tabId: number | undefined,
  reason: StopReason = 'user_stop',
): Promise<{ ok: boolean }> {
  if (tabId === undefined) return { ok: false }
  const pointer = await getGlobalPointer()
  if (!pointer || pointer.tabId !== tabId) return { ok: false } // sender does not own the pointer

  try {
    await postStop(pointer.sessionId, reason)
  } catch {
    return { ok: false } // never clear the pointer on an unconfirmed stop
  }
  await clearGlobalPointer()
  releasePrepareLock(tabId)
  return { ok: true }
}

//: 'scroll' is M4c (CLAUDE.md "Chrome extension - M4 supervised navigation
//: policy", explicitly authorized): one bounded scroll step on the current
//: results page, bounded by scroll_cap and reset on every confirmed
//: 'results' navigation - see supervised_sessions.py.
type NavigateTarget = 'results' | 'detail' | 'scroll'

function isNavigateTarget(value: unknown): value is NavigateTarget {
  return value === 'results' || value === 'detail' || value === 'scroll'
}
type NavigateOutcome = 'success' | 'failed'

interface NavigateResult {
  ok: boolean
  session?: SessionOut
  error?: string
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
const preparePending = new Set<number>()

function releasePrepareLock(tabId: number | undefined): void {
  if (tabId !== undefined) preparePending.delete(tabId)
}

/**
 * M4b (CLAUDE.md "Chrome extension - M4 supervised navigation policy",
 * explicitly authorized). Authorizes exactly one upcoming click for the
 * *owning* tab only - the same per-tab pointer-ownership check every other
 * handler here uses. Writes nothing on the backend; a non-owning tab (or a
 * tab with no session) is rejected with no fetch at all.
 */
async function navigatePrepareForTab(
  tabId: number | undefined,
  target: NavigateTarget,
  pageUrl: string | null,
): Promise<NavigateResult> {
  if (tabId === undefined) return { ok: false, error: 'no_tab' }
  const pointer = await getGlobalPointer()
  if (!pointer || pointer.tabId !== tabId) return { ok: false, error: 'not_owner' }

  if (preparePending.has(tabId)) {
    // A prepare for this tab is already outstanding (or already succeeded
    // and is still waiting on its matching confirm) - a duplicate call from
    // a rapid double-click gets no second fetch, ever.
    return { ok: false, error: 'prepare_in_flight' }
  }
  preparePending.add(tabId)

  try {
    const session = await fetchJson<SessionOut>(
      `/api/extension/sessions/${pointer.sessionId}/navigate/prepare`,
      { method: 'POST', body: { target, page_url: pageUrl } },
    )
    return { ok: true, session }
  } catch (err) {
    // A denied/failed prepare never gets a matching confirm - the overlay
    // hard-stops and calls stop-session instead, so the lock is released
    // there (once that stop is actually confirmed), not here. Leaving it
    // held is intentional: it is exactly the "release only on confirm or
    // confirmed stop" rule, and it means a second prepare for this tab
    // stays blocked until the session is genuinely known to be over.
    return { ok: false, error: String((err as Error)?.message || err) }
  }
}

/**
 * Accounts for what the click the extension already attempted actually
 * did. Same per-tab ownership check as prepare/stop - a non-owning tab can
 * never confirm (or fabricate) another tab's navigation.
 */
async function navigateConfirmForTab(
  tabId: number | undefined,
  target: NavigateTarget,
  pageUrl: string | null,
  outcome: NavigateOutcome,
  error: string | null,
): Promise<NavigateResult> {
  if (tabId === undefined) return { ok: false, error: 'no_tab' }
  const pointer = await getGlobalPointer()
  if (!pointer || pointer.tabId !== tabId) return { ok: false, error: 'not_owner' }

  // The prepare/confirm window is closing either way - release the lock
  // before the fetch so an unrelated later prepare for this tab is never
  // blocked by this call's own outcome.
  releasePrepareLock(tabId)

  try {
    const session = await fetchJson<SessionOut>(
      `/api/extension/sessions/${pointer.sessionId}/navigate/confirm`,
      { method: 'POST', body: { target, page_url: pageUrl, outcome, error } },
    )
    return { ok: true, session }
  } catch (err) {
    return { ok: false, error: String((err as Error)?.message || err) }
  }
}

interface PreviewResult {
  ok: boolean
  preview?: unknown
  error?: string
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
async function sendPreviewForTab(
  tabId: number | undefined,
  pageType: 'search' | 'detail',
  pageUrl: string | null,
  candidate: unknown,
): Promise<PreviewResult> {
  if (tabId === undefined) return { ok: false, error: 'no_tab' }
  const pointer = await getGlobalPointer()
  if (!pointer || pointer.tabId !== tabId) return { ok: false, error: 'not_owner' }

  try {
    const preview = await fetchJson('/api/extension/jobs/preview', {
      method: 'POST',
      body: { page_type: pageType, page_url: pageUrl, candidates: [candidate] },
    })
    return { ok: true, preview }
  } catch (err) {
    return { ok: false, error: String((err as Error)?.message || err) }
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const request = (message || {}) as RuntimeRequest

  if (request.type === 'jobagent:session-snapshot') {
    void buildSnapshot(sender.tab?.id).then((result) => sendResponse(result))
    return true // asynchronous response
  }

  if (request.type === 'jobagent:stop-session') {
    const payload = message as { reason?: string }
    const allowed: StopReason[] = [
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
    ]
    const reason = (allowed as string[]).indexOf(payload.reason || '') !== -1
      ? (payload.reason as StopReason)
      : 'user_stop'
    void stopForTab(sender.tab?.id, reason).then((result) => sendResponse(result))
    return true // asynchronous response
  }

  if (request.type === 'jobagent:navigate-prepare') {
    const payload = message as { target?: NavigateTarget; pageUrl?: string | null }
    if (!isNavigateTarget(payload.target)) {
      sendResponse({ ok: false, error: 'bad_target' })
      return false
    }
    void navigatePrepareForTab(sender.tab?.id, payload.target, payload.pageUrl ?? null).then(
      (result) => sendResponse(result),
    )
    return true // asynchronous response
  }

  if (request.type === 'jobagent:navigate-confirm') {
    const payload = message as {
      target?: NavigateTarget
      pageUrl?: string | null
      outcome?: NavigateOutcome
      error?: string | null
    }
    if (!isNavigateTarget(payload.target)) {
      sendResponse({ ok: false, error: 'bad_target' })
      return false
    }
    if (payload.outcome !== 'success' && payload.outcome !== 'failed') {
      sendResponse({ ok: false, error: 'bad_outcome' })
      return false
    }
    void navigateConfirmForTab(
      sender.tab?.id,
      payload.target,
      payload.pageUrl ?? null,
      payload.outcome,
      payload.error ?? null,
    ).then((result) => sendResponse(result))
    return true // asynchronous response
  }

  if (request.type === 'jobagent:send-preview') {
    const payload = message as { pageType?: string; pageUrl?: string | null; candidate?: unknown }
    if (payload.pageType !== 'search' && payload.pageType !== 'detail') {
      sendResponse({ ok: false, error: 'bad_page_type' })
      return false
    }
    if (!payload.candidate) {
      sendResponse({ ok: false, error: 'no_candidate' })
      return false
    }
    void sendPreviewForTab(
      sender.tab?.id,
      payload.pageType,
      payload.pageUrl ?? null,
      payload.candidate,
    ).then((result) => sendResponse(result))
    return true // asynchronous response
  }

  return false
})
