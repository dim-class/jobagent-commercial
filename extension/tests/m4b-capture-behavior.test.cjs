'use strict'

/**
 * M4b phase-two ("捕获详情") behavior - Node built-ins only (`node:test`,
 * `assert`, `vm`). No browser, no Playwright/CDP, no network, no
 * dependencies.
 *
 * Loads the real built `dist/overlay.js` into a `vm` sandbox with a minimal
 * DOM mock, a scriptable `chrome.runtime.sendMessage` (standing in for
 * background.ts's real responses - background.ts's own `jobagent:send-
 * preview` plumbing is a separate, focused test below), and a scriptable
 * `BossContentScript` that records every `jobagent:capture-detail` call.
 * Same harness shape as `m4b-overlay-hardstop.test.cjs` and
 * `m4b-doubleclick-guard.test.cjs`.
 *
 * Proves the two-phase state machine end to end: phase one opens a card and
 * caches it; phase two reads the pane, and only a `status: 'ok'` capture
 * ever reaches `jobagent:send-preview` - `not_loaded` and a failed preview
 * POST are soft, human-retryable states (capture button re-enabled, nothing
 * auto-retried), while `identity_mismatch` and `verification` end the
 * session outright, exactly like every other M4b policy violation.
 */

const { test } = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const path = require('node:path')

const OVERLAY_PATH = path.join(__dirname, '..', 'dist', 'overlay.js')
const BUILT = fs.existsSync(OVERLAY_PATH)
const SKIP = BUILT ? false : "extension not built - run 'npm run build' in extension/"

const SESSION = {
  id: 42,
  status: 'running',
  page_cap: 2,
  candidate_cap: 5,
  scroll_cap: 0,
  tab_origin: 'https://www.zhipin.com',
  pages_visited: 0,
  candidates_extracted: 0,
  scrolls_used: 0,
  approved_criteria: { task_name: '云计算运维-北京' },
}

const CARD = {
  title: '云计算工程师',
  company: '纳新电子',
  salary_text: '10-15K',
  city: '北京',
  experience_text: '3-5年',
  education_text: '本科',
  source_url: 'https://www.zhipin.com/job_detail/abc.html',
  external_id: 'abc',
  matched_selectors: { title: '.job-name' },
}

const SEARCH_PAGE = { page_type: 'search', verification: false, candidates: [CARD] }

const CARD_2 = {
  ...CARD,
  title: '云计算运维工程师',
  source_url: 'https://www.zhipin.com/job_detail/def.html',
  external_id: 'def',
}

const TWO_CARD_SEARCH_PAGE = {
  page_type: 'search',
  verification: false,
  candidates: [CARD, CARD_2],
}

const MERGED_CANDIDATE = {
  ...CARD,
  description: '负责云平台日常运维。',
  missing_fields: [],
  warnings: [],
}

function makeElement(tag) {
  return {
    tagName: tag,
    id: '',
    style: {},
    children: [],
    _listeners: {},
    disabled: false,
    textContent: '',
    setAttribute() {},
    appendChild(child) {
      this.children.push(child)
    },
    addEventListener(evt, fn) {
      this._listeners[evt] = fn
    },
    remove() {
      this._removed = true
    },
    click() {
      if (this._listeners.click) this._listeners.click()
    },
  }
}

/**
 * `captureResults` is a queue: each `jobagent:capture-detail` call shifts
 * the next scripted result off it (so a test can script "not loaded, then
 * ok" or similar retry sequences). `backendResponses(msg)` scripts every
 * `chrome.runtime.sendMessage` reply, standing in for background.ts.
 */
function loadOverlay({ detectResult, openResult, captureResults, backendResponses }) {
  const elementsById = {}
  const sentMessages = []
  const openCalls = []
  const captureCalls = []
  const listeners = []
  const remainingCaptures = (captureResults || []).slice()

  // A real `document.getElementById` finds any element attached anywhere in
  // the document, not just direct children of `documentElement` - needed
  // here because both "下一位候选人" and "捕获详情" are looked up by id while
  // nested inside the bar, not appended to `documentElement` directly. See
  // `m4b-doubleclick-guard.test.cjs` for the same pattern.
  function registerIfHasId(el) {
    if (el && el.id) elementsById[el.id] = el
  }
  const documentElement = {
    appendChild(el) {
      registerIfHasId(el)
    },
  }
  const doc = {
    location: { protocol: 'https:', host: 'www.zhipin.com' },
    documentElement,
    getElementById(id) {
      const el = elementsById[id]
      return el && !el._removed ? el : null
    },
    createElement(tag) {
      const el = makeElement(tag)
      const baseAppendChild = el.appendChild.bind(el)
      el.appendChild = (child) => {
        baseAppendChild(child)
        registerIfHasId(child)
      }
      return el
    },
  }

  const sandbox = {
    document: doc,
    window: {},
    chrome: {
      runtime: {
        onMessage: { addListener: (fn) => listeners.push(fn) },
        sendMessage: (msg, cb) => {
          sentMessages.push(msg)
          const response = backendResponses(msg)
          if (cb) cb(response)
        },
      },
    },
    BossContentScript: {
      DETECT: 'jobagent:detect',
      OPEN_CANDIDATE: 'jobagent:open-candidate',
      CAPTURE_DETAIL: 'jobagent:capture-detail',
      handle(msg) {
        if (msg.type === 'jobagent:detect') return { ok: true, result: detectResult }
        if (msg.type === 'jobagent:open-candidate') {
          openCalls.push(msg.index)
          return { ok: true, result: openResult }
        }
        if (msg.type === 'jobagent:capture-detail') {
          captureCalls.push({ canonicalUrl: msg.canonicalUrl, cachedCard: msg.cachedCard })
          const result = remainingCaptures.length
            ? remainingCaptures.shift()
            : { status: 'not_loaded', candidate: null }
          return { ok: true, result }
        }
        return { ok: false }
      },
    },
    console,
  }
  vm.createContext(sandbox)
  const code = fs.readFileSync(OVERLAY_PATH, 'utf8')
  new vm.Script(code, { filename: 'overlay.js' }).runInContext(sandbox)

  return { sandbox, elementsById, sentMessages, openCalls, captureCalls }
}

async function settle() {
  await new Promise((resolve) => setImmediate(resolve))
  await new Promise((resolve) => setImmediate(resolve))
}

function nextButtonOf(env) {
  const bar = env.elementsById['jobagent-session-bar']
  return bar && bar.children.find((c) => c.textContent === '下一位候选人')
}

function captureButtonOf(env) {
  const bar = env.elementsById['jobagent-session-bar']
  return bar && bar.children.find((c) => c.textContent === '捕获详情')
}

function stopMessagesOf(env) {
  return env.sentMessages.filter((m) => m.type === 'jobagent:stop-session')
}

function previewMessagesOf(env) {
  return env.sentMessages.filter((m) => m.type === 'jobagent:send-preview')
}

const openThenCaptureBackend = (extra) => (msg) => {
  if (msg.type === 'jobagent:session-snapshot') return { kind: 'session', session: SESSION }
  if (msg.type === 'jobagent:navigate-prepare') return { ok: true, session: SESSION }
  if (msg.type === 'jobagent:navigate-confirm') {
    return { ok: true, session: { ...SESSION, candidates_extracted: 1 } }
  }
  if (msg.type === 'jobagent:stop-session') return { ok: true }
  if (extra) {
    const result = extra(msg)
    if (result !== undefined) return result
  }
  return { ok: false }
}

async function openFirstCandidate(env) {
  nextButtonOf(env).click()
  await settle()
}

/**
 * A backend responder that actually tracks `candidates_extracted` across
 * calls (unlike `openThenCaptureBackend`'s fixed `1`), so tests can assert
 * Next's unlock/lock behavior at a specific cap. `sendPreviewOk` may be a
 * function of the call index for a fail-then-succeed sequence.
 */
function makeCapAwareBackend({ candidateCap = 5, sendPreviewOk = () => true }) {
  let extracted = 0
  let previewAttempt = 0
  return (msg) => {
    if (msg.type === 'jobagent:session-snapshot') {
      return { kind: 'session', session: { ...SESSION, candidate_cap: candidateCap, candidates_extracted: extracted } }
    }
    if (msg.type === 'jobagent:navigate-prepare') {
      return { ok: true, session: { ...SESSION, candidate_cap: candidateCap, candidates_extracted: extracted } }
    }
    if (msg.type === 'jobagent:navigate-confirm') {
      extracted += 1
      return { ok: true, session: { ...SESSION, candidate_cap: candidateCap, candidates_extracted: extracted } }
    }
    if (msg.type === 'jobagent:stop-session') return { ok: true }
    if (msg.type === 'jobagent:send-preview') {
      previewAttempt += 1
      if (sendPreviewOk(previewAttempt)) {
        return { ok: true, preview: { new_count: 1, duplicate_count: 0, incomplete_count: 0 } }
      }
      return { ok: false, error: 'network_error' }
    }
    return { ok: false }
  }
}

// --------------------------------------------------------------------------
// the live-found bug: repeated Next clicks must never open a second
// candidate while one is still pending capture
// --------------------------------------------------------------------------

test(
  'a repeated Next click while one candidate is pending opens nothing and touches no backend endpoint',
  { skip: SKIP },
  async () => {
    const env = loadOverlay({
      detectResult: TWO_CARD_SEARCH_PAGE,
      openResult: { ok: true },
      captureResults: [],
      backendResponses: makeCapAwareBackend({ candidateCap: 5 }),
    })
    await settle()
    await openFirstCandidate(env)

    assert.equal(env.openCalls.length, 1, 'the first candidate opened normally')
    assert.equal(nextButtonOf(env).disabled, true, 'Next is disabled while capture is pending')

    // The live failure: the human clicks Next again before capturing.
    const prepareCallsBefore = env.sentMessages.filter((m) => m.type === 'jobagent:navigate-prepare').length
    nextButtonOf(env).click()
    await settle()

    assert.equal(env.openCalls.length, 1, 'no second click happened')
    assert.equal(
      env.sentMessages.filter((m) => m.type === 'jobagent:navigate-prepare').length,
      prepareCallsBefore,
      'no second prepare/confirm round-trip happened either',
    )
    assert.equal(stopMessagesOf(env).length, 0, 'this is a refusal, not a policy violation')
    assert.equal(nextButtonOf(env).disabled, true)
  },
)

// --------------------------------------------------------------------------
// capture success unlocks exactly one further candidate, below cap
// --------------------------------------------------------------------------

test(
  'a successful capture unlocks Next below cap, and the next candidate can then open',
  { skip: SKIP },
  async () => {
    const env = loadOverlay({
      detectResult: TWO_CARD_SEARCH_PAGE,
      openResult: { ok: true },
      captureResults: [{ status: 'ok', candidate: MERGED_CANDIDATE }],
      backendResponses: makeCapAwareBackend({ candidateCap: 5 }),
    })
    await settle()
    await openFirstCandidate(env)
    assert.equal(nextButtonOf(env).disabled, true)

    captureButtonOf(env).click()
    await settle()

    assert.equal(previewMessagesOf(env).length, 1)
    assert.equal(nextButtonOf(env).disabled, false, 'below cap, Next unlocks once the send succeeds')

    // And it actually works: the second, distinct candidate can now open.
    nextButtonOf(env).click()
    await settle()

    assert.equal(env.openCalls.length, 2)
    assert.equal(nextButtonOf(env).disabled, true, 'disabled again for the newly pending candidate')
  },
)

// --------------------------------------------------------------------------
// soft capture/preview failures never unlock Next
// --------------------------------------------------------------------------

test('a not_loaded capture leaves Next disabled', { skip: SKIP }, async () => {
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    captureResults: [{ status: 'not_loaded', candidate: null }],
    backendResponses: makeCapAwareBackend({ candidateCap: 5 }),
  })
  await settle()
  await openFirstCandidate(env)

  captureButtonOf(env).click()
  await settle()

  assert.equal(previewMessagesOf(env).length, 0)
  assert.equal(nextButtonOf(env).disabled, true, 'a soft "not yet" must not unlock Next')
  assert.equal(captureButtonOf(env).disabled, false, 'only capture itself may be retried')
})

test('a failed preview send leaves Next disabled', { skip: SKIP }, async () => {
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    captureResults: [{ status: 'ok', candidate: MERGED_CANDIDATE }],
    backendResponses: makeCapAwareBackend({ candidateCap: 5, sendPreviewOk: () => false }),
  })
  await settle()
  await openFirstCandidate(env)

  captureButtonOf(env).click()
  await settle()

  assert.equal(previewMessagesOf(env).length, 1, 'the attempt was made and failed')
  assert.equal(nextButtonOf(env).disabled, true, 'a failed send must not unlock Next')
  assert.equal(captureButtonOf(env).disabled, false, 'only capture itself may be retried')
})

// --------------------------------------------------------------------------
// cap completion never unlocks Next, even after a successful capture
// --------------------------------------------------------------------------

test(
  'reaching the approved cap on the opening click keeps Next disabled after a successful capture',
  { skip: SKIP },
  async () => {
    // candidate_cap 1: the one navigate/confirm this test makes already
    // reaches it (matches the real 5/5 case reported live).
    const env = loadOverlay({
      detectResult: SEARCH_PAGE,
      openResult: { ok: true },
      captureResults: [{ status: 'ok', candidate: MERGED_CANDIDATE }],
      backendResponses: makeCapAwareBackend({ candidateCap: 1 }),
    })
    await settle()
    await openFirstCandidate(env)

    captureButtonOf(env).click()
    await settle()

    assert.equal(previewMessagesOf(env).length, 1, 'the capture itself still succeeds')
    assert.equal(nextButtonOf(env).disabled, true, 'at cap, Next stays disabled even though nothing is pending')
  },
)

// --------------------------------------------------------------------------
// the clean two-phase path: open -> capture ok -> preview sent
// --------------------------------------------------------------------------

test(
  'a successful capture sends the merged candidate to the loopback preview path',
  { skip: SKIP },
  async () => {
    const env = loadOverlay({
      detectResult: SEARCH_PAGE,
      openResult: { ok: true },
      captureResults: [{ status: 'ok', candidate: MERGED_CANDIDATE }],
      backendResponses: openThenCaptureBackend((msg) => {
        if (msg.type === 'jobagent:send-preview') {
          return { ok: true, preview: { new_count: 1, duplicate_count: 0, incomplete_count: 0 } }
        }
      }),
    })
    await settle()
    await openFirstCandidate(env)

    assert.equal(captureButtonOf(env).disabled, false, 'capture becomes available after open')

    captureButtonOf(env).click()
    await settle()

    assert.equal(env.captureCalls.length, 1)
    assert.equal(env.captureCalls[0].canonicalUrl, CARD.source_url)
    assert.equal(env.captureCalls[0].cachedCard.title, CARD.title)

    const previews = previewMessagesOf(env)
    assert.equal(previews.length, 1)
    assert.equal(previews[0].pageType, 'detail')
    assert.equal(previews[0].candidate.description, MERGED_CANDIDATE.description)
    assert.equal(previews[0].candidate.source_url, CARD.source_url)

    assert.equal(stopMessagesOf(env).length, 0, 'a clean capture never stops the session')
    assert.equal(captureButtonOf(env).disabled, true, 'nothing left pending after a sent capture')
  },
)

// --------------------------------------------------------------------------
// not_loaded: soft, human-retryable - never a stop, never a send
// --------------------------------------------------------------------------

test(
  'a not_loaded capture never sends and lets the human retry manually',
  { skip: SKIP },
  async () => {
    const env = loadOverlay({
      detectResult: SEARCH_PAGE,
      openResult: { ok: true },
      captureResults: [
        { status: 'not_loaded', candidate: null },
        { status: 'ok', candidate: MERGED_CANDIDATE },
      ],
      backendResponses: openThenCaptureBackend((msg) => {
        if (msg.type === 'jobagent:send-preview') {
          return { ok: true, preview: { new_count: 1, duplicate_count: 0, incomplete_count: 0 } }
        }
      }),
    })
    await settle()
    await openFirstCandidate(env)

    captureButtonOf(env).click()
    await settle()

    assert.equal(env.captureCalls.length, 1)
    assert.equal(previewMessagesOf(env).length, 0, 'not_loaded must never send a preview')
    assert.equal(stopMessagesOf(env).length, 0, 'not_loaded is not a policy violation')
    assert.equal(captureButtonOf(env).disabled, false, 'the human may just click again')

    // The human waits for the pane to load and clicks "捕获详情" again.
    captureButtonOf(env).click()
    await settle()

    assert.equal(env.captureCalls.length, 2)
    assert.equal(previewMessagesOf(env).length, 1, 'the retry succeeds and sends once')
  },
)

// --------------------------------------------------------------------------
// identity mismatch / verification during capture: hard stop, never a send
// --------------------------------------------------------------------------

test(
  'an identity mismatch during capture stops the session and sends nothing',
  { skip: SKIP },
  async () => {
    const env = loadOverlay({
      detectResult: SEARCH_PAGE,
      openResult: { ok: true },
      captureResults: [{ status: 'identity_mismatch', candidate: null }],
      backendResponses: openThenCaptureBackend(),
    })
    await settle()
    await openFirstCandidate(env)

    captureButtonOf(env).click()
    await settle()

    assert.equal(previewMessagesOf(env).length, 0)
    const stops = stopMessagesOf(env)
    assert.equal(stops.length, 1)
    assert.equal(stops[0].reason, 'identity_mismatch')
    assert.equal(env.elementsById['jobagent-session-bar']._removed, true)
  },
)

test(
  'a verification interstitial during capture stops the session and sends nothing',
  { skip: SKIP },
  async () => {
    const env = loadOverlay({
      detectResult: SEARCH_PAGE,
      openResult: { ok: true },
      captureResults: [{ status: 'verification', candidate: null }],
      backendResponses: openThenCaptureBackend(),
    })
    await settle()
    await openFirstCandidate(env)

    captureButtonOf(env).click()
    await settle()

    assert.equal(previewMessagesOf(env).length, 0)
    const stops = stopMessagesOf(env)
    assert.equal(stops.length, 1)
    assert.equal(stops[0].reason, 'verification')
  },
)

// --------------------------------------------------------------------------
// preview POST failure: soft, human-retryable - never a stop
// --------------------------------------------------------------------------

test(
  'a failed preview send never stops the session and lets the human retry manually',
  { skip: SKIP },
  async () => {
    let attempt = 0
    const env = loadOverlay({
      detectResult: SEARCH_PAGE,
      openResult: { ok: true },
      captureResults: [
        { status: 'ok', candidate: MERGED_CANDIDATE },
        { status: 'ok', candidate: MERGED_CANDIDATE },
      ],
      backendResponses: openThenCaptureBackend((msg) => {
        if (msg.type === 'jobagent:send-preview') {
          attempt += 1
          if (attempt === 1) return { ok: false, error: 'network_error' }
          return { ok: true, preview: { new_count: 1, duplicate_count: 0, incomplete_count: 0 } }
        }
      }),
    })
    await settle()
    await openFirstCandidate(env)

    captureButtonOf(env).click()
    await settle()

    assert.equal(previewMessagesOf(env).length, 1, 'the first attempt was made and failed')
    assert.equal(stopMessagesOf(env).length, 0, 'a backend failure is not a policy violation')
    assert.equal(captureButtonOf(env).disabled, false, 'the human may retry manually')

    // Nothing here retries automatically - the human clicks again.
    captureButtonOf(env).click()
    await settle()

    assert.equal(previewMessagesOf(env).length, 2, 'the manual retry sends again')
    assert.equal(captureButtonOf(env).disabled, true, 'nothing left pending once it succeeds')
  },
)

// --------------------------------------------------------------------------
// no candidate opened yet: capture refuses without touching the backend
// --------------------------------------------------------------------------

test(
  'capturing with nothing pending does nothing and calls no backend endpoint',
  { skip: SKIP },
  async () => {
    const env = loadOverlay({
      detectResult: SEARCH_PAGE,
      openResult: { ok: true },
      captureResults: [],
      backendResponses: (msg) => {
        if (msg.type === 'jobagent:session-snapshot') return { kind: 'session', session: SESSION }
        return { ok: false }
      },
    })
    await settle()

    // The capture button starts disabled before any card has been opened,
    // but drive the handler directly to prove it refuses even if invoked.
    const bar = env.elementsById['jobagent-session-bar']
    assert.equal(captureButtonOf(env).disabled, true)

    captureButtonOf(env).click()
    await settle()

    assert.equal(env.captureCalls.length, 0)
    assert.equal(previewMessagesOf(env).length, 0)
    assert.equal(stopMessagesOf(env).length, 0)
    assert.ok(bar, 'the bar is unaffected')
  },
)
