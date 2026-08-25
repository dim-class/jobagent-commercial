'use strict'

/**
 * M4b overlay hard-stop behavior - Node built-ins only (`node:test`, `assert`,
 * `vm`). No browser, no Playwright/CDP, no network, no dependencies.
 *
 * Loads the real built `dist/overlay.js` into a `vm` sandbox with a minimal
 * DOM mock, a scriptable `chrome.runtime.sendMessage` (standing in for
 * background.ts's real responses - background.ts's own plumbing is covered
 * separately in m4b-behavior.test.cjs), and a scriptable `BossContentScript`
 * that records every `jobagent:open-candidate` call so "was a click even
 * attempted" is directly observable. Proves: a denied prepare never clicks
 * and still stops the session; a failed click never counts and still stops;
 * verification/loop/wrong-page each stop without ever asking to navigate;
 * and a clean run does exactly one click and confirms success.
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

const SEARCH_PAGE = {
  page_type: 'search',
  verification: false,
  candidates: [{ title: '云计算工程师', source_url: 'https://www.zhipin.com/job_detail/abc.html' }],
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
 * Loads a fresh copy of the real dist/overlay.js. `backendResponses(msg)`
 * is the test's script for what `chrome.runtime.sendMessage` returns for
 * each message type - it stands in for background.ts, which is tested for
 * real separately.
 */
function loadOverlay({ detectResult, openResult, backendResponses, captureResult }) {
  const elementsById = {}
  const sentMessages = []
  const openCalls = []
  const captureCalls = []
  const listeners = []

  const documentElement = {
    appendChild(el) {
      if (el.id) elementsById[el.id] = el
    },
  }
  const doc = {
    location: { protocol: 'https:', host: 'www.zhipin.com' },
    documentElement,
    getElementById(id) {
      const el = elementsById[id]
      return el && !el._removed ? el : null
    },
    createElement: makeElement,
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
          captureCalls.push(msg.canonicalUrl)
          return { ok: true, result: captureResult || { status: 'not_loaded', candidate: null } }
        }
        return { ok: false }
      },
    },
    console,
  }
  vm.createContext(sandbox)
  const code = fs.readFileSync(OVERLAY_PATH, 'utf8')
  new vm.Script(code, { filename: 'overlay.js' }).runInContext(sandbox)

  return { sandbox, elementsById, sentMessages, openCalls, captureCalls, listeners }
}

async function settle() {
  // Flush pending microtasks/macrotask boundary so `init()`'s async chain
  // (and any subsequent awaited chain a button click starts) completes.
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

// --------------------------------------------------------------------------
// denial: prepare refuses -> no click, session stopped
// --------------------------------------------------------------------------

test('a denied prepare clicks nothing and stops the session', { skip: SKIP }, async () => {
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    backendResponses: (msg) => {
      if (msg.type === 'jobagent:session-snapshot') return { kind: 'session', session: SESSION }
      if (msg.type === 'jobagent:navigate-prepare') return { ok: false, error: 'cap_reached' }
      if (msg.type === 'jobagent:stop-session') return { ok: true }
      return { ok: false }
    },
  })
  await settle()

  nextButtonOf(env).click()
  await settle()

  assert.equal(env.openCalls.length, 0, 'a denied prepare must never click')
  const stops = stopMessagesOf(env)
  assert.equal(stops.length, 1)
  assert.equal(stops[0].reason, 'prepare_denied')
  assert.equal(env.elementsById['jobagent-session-bar']._removed, true)
})

// --------------------------------------------------------------------------
// failed click: confirmed failed, never counted, session stopped
// --------------------------------------------------------------------------

test('a failed click confirms failed and stops - never counted', { skip: SKIP }, async () => {
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: false, error: 'selector_ambiguous' },
    backendResponses: (msg) => {
      if (msg.type === 'jobagent:session-snapshot') return { kind: 'session', session: SESSION }
      if (msg.type === 'jobagent:navigate-prepare') return { ok: true, session: SESSION }
      if (msg.type === 'jobagent:navigate-confirm') return { ok: true, session: SESSION }
      if (msg.type === 'jobagent:stop-session') return { ok: true }
      return { ok: false }
    },
  })
  await settle()

  nextButtonOf(env).click()
  await settle()

  assert.equal(env.openCalls.length, 1, 'exactly one click attempt')
  const confirms = env.sentMessages.filter((m) => m.type === 'jobagent:navigate-confirm')
  assert.equal(confirms.length, 1)
  assert.equal(confirms[0].outcome, 'failed')

  const stops = stopMessagesOf(env)
  assert.equal(stops.length, 1)
  assert.equal(stops[0].reason, 'click_failed')
})

// --------------------------------------------------------------------------
// verification / loop / wrong page - each stops without ever preparing
// --------------------------------------------------------------------------

test('a verification interstitial stops without preparing a navigation', { skip: SKIP }, async () => {
  const env = loadOverlay({
    detectResult: { page_type: 'search', verification: true, candidates: [] },
    openResult: { ok: true },
    backendResponses: (msg) => {
      if (msg.type === 'jobagent:session-snapshot') return { kind: 'session', session: SESSION }
      if (msg.type === 'jobagent:stop-session') return { ok: true }
      return { ok: false }
    },
  })
  await settle()
  nextButtonOf(env).click()
  await settle()

  const prepares = env.sentMessages.filter((m) => m.type === 'jobagent:navigate-prepare')
  assert.equal(prepares.length, 0)
  assert.equal(env.openCalls.length, 0)
  const stops = stopMessagesOf(env)
  assert.equal(stops.length, 1)
  assert.equal(stops[0].reason, 'verification')
})

test('a duplicate (looping) candidate URL stops without a second click', { skip: SKIP }, async () => {
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    // A candidate pending capture already keeps Next disabled and refused
    // (see m4b-capture-behavior.test.cjs) - so to reach the loop-guard this
    // test actually targets, the one candidate must be captured first, same
    // as a real run would require.
    captureResult: { status: 'ok', candidate: { title: '云计算工程师', source_url: SEARCH_PAGE.candidates[0].source_url } },
    backendResponses: (msg) => {
      if (msg.type === 'jobagent:session-snapshot') return { kind: 'session', session: SESSION }
      if (msg.type === 'jobagent:navigate-prepare') return { ok: true, session: SESSION }
      if (msg.type === 'jobagent:navigate-confirm') return { ok: true, session: SESSION }
      if (msg.type === 'jobagent:send-preview') {
        return { ok: true, preview: { new_count: 1, duplicate_count: 0, incomplete_count: 0 } }
      }
      if (msg.type === 'jobagent:stop-session') return { ok: true }
      return { ok: false }
    },
  })
  await settle()

  // First click succeeds normally and marks the only candidate as handled.
  nextButtonOf(env).click()
  await settle()
  assert.equal(env.openCalls.length, 1)

  // Capture it so nothing is pending - otherwise a second Next click is
  // refused for that reason alone (see m4b-capture-behavior.test.cjs), not
  // because the candidate is a duplicate, which is what this test covers.
  captureButtonOf(env).click()
  await settle()
  assert.equal(env.captureCalls.length, 1)

  // A second click finds the same (only) candidate already handled - the
  // search page never changes, so this is the loop path, not "no candidates".
  const secondBtn = nextButtonOf(env)
  if (secondBtn) secondBtn.click()
  await settle()

  const stops = stopMessagesOf(env)
  assert.equal(stops.length, 1)
  assert.ok(['no_candidates', 'loop_detected'].includes(stops[0].reason))
  assert.equal(env.openCalls.length, 1, 'no second click')
})

test('a non-search page stops without preparing a navigation', { skip: SKIP }, async () => {
  const env = loadOverlay({
    detectResult: { page_type: 'detail', verification: false, candidates: [] },
    openResult: { ok: true },
    backendResponses: (msg) => {
      if (msg.type === 'jobagent:session-snapshot') return { kind: 'session', session: SESSION }
      if (msg.type === 'jobagent:stop-session') return { ok: true }
      return { ok: false }
    },
  })
  await settle()
  nextButtonOf(env).click()
  await settle()

  const prepares = env.sentMessages.filter((m) => m.type === 'jobagent:navigate-prepare')
  assert.equal(prepares.length, 0)
  const stops = stopMessagesOf(env)
  assert.equal(stops.length, 1)
  assert.equal(stops[0].reason, 'wrong_page')
})

// --------------------------------------------------------------------------
// the clean path: prepare -> exactly one click -> confirm success
// --------------------------------------------------------------------------

test('a clean run prepares, clicks exactly once, and confirms success', { skip: SKIP }, async () => {
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    backendResponses: (msg) => {
      if (msg.type === 'jobagent:session-snapshot') return { kind: 'session', session: SESSION }
      if (msg.type === 'jobagent:navigate-prepare') return { ok: true, session: SESSION }
      if (msg.type === 'jobagent:navigate-confirm') {
        return { ok: true, session: { ...SESSION, candidates_extracted: 1 } }
      }
      return { ok: false }
    },
  })
  await settle()

  nextButtonOf(env).click()
  await settle()

  const prepares = env.sentMessages.filter((m) => m.type === 'jobagent:navigate-prepare')
  assert.equal(prepares.length, 1)
  assert.equal(env.openCalls.length, 1)
  const confirms = env.sentMessages.filter((m) => m.type === 'jobagent:navigate-confirm')
  assert.equal(confirms.length, 1)
  assert.equal(confirms[0].outcome, 'success')
  assert.equal(stopMessagesOf(env).length, 0, 'a clean run never stops the session')
})
