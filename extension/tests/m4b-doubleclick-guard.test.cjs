'use strict'

/**
 * M4b rapid-double-click defense-in-depth - Node built-ins only
 * (`node:test`, `assert`, `vm`). No browser, no Playwright/CDP, no network,
 * no dependencies.
 *
 * A double-click on "下一位候选人" can fire two click events in the same
 * tick, before either async chain has a chance to run. Two independent
 * layers must both hold:
 *
 *  - `overlay.ts`: a synchronous in-flight guard set/checked before any
 *    `await`, plus disabling the button for the duration of the step, so a
 *    second click does no second fetch and no second click on the page;
 *  - `background.ts`: an in-memory, per-tab single-flight guard around the
 *    one real `navigate/prepare` fetch, released only on the matching
 *    `navigate-confirm` or a confirmed stop - never a timer, never
 *    `chrome.storage` - so even a duplicate message that somehow reaches the
 *    worker (bypassing the overlay's own guard) still gets no second fetch.
 *
 * Loads the real built `dist/overlay.js` and `dist/background.js`, exactly
 * like `m4b-overlay-hardstop.test.cjs` and `m4b-behavior.test.cjs`.
 */

const { test } = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const path = require('node:path')

const OVERLAY_PATH = path.join(__dirname, '..', 'dist', 'overlay.js')
const BACKGROUND_PATH = path.join(__dirname, '..', 'dist', 'background.js')
const BUILT = fs.existsSync(OVERLAY_PATH) && fs.existsSync(BACKGROUND_PATH)
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

// ----------------------------------------------------------------------
// overlay.js harness (same shape as m4b-overlay-hardstop.test.cjs)
// ----------------------------------------------------------------------

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

function loadOverlay({ detectResult, openResult, backendResponses }) {
  const elementsById = {}
  const sentMessages = []
  const openCalls = []
  const listeners = []

  // A real `document.getElementById` finds any element attached anywhere in
  // the document, not just direct children of `documentElement`. The plain
  // `appendChild` in `makeElement` only tracks parent/child locally, so
  // register-by-id on every `appendChild` (not just `documentElement`'s) to
  // approximate that - needed here because "下一位候选人" is looked up by id
  // while nested inside the bar, not appended to `documentElement` directly.
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
      handle(msg) {
        if (msg.type === 'jobagent:detect') return { ok: true, result: detectResult }
        if (msg.type === 'jobagent:open-candidate') {
          openCalls.push(msg.index)
          return { ok: true, result: openResult }
        }
        return { ok: false }
      },
    },
    console,
  }
  vm.createContext(sandbox)
  const code = fs.readFileSync(OVERLAY_PATH, 'utf8')
  new vm.Script(code, { filename: 'overlay.js' }).runInContext(sandbox)

  return { sandbox, elementsById, sentMessages, openCalls, listeners }
}

async function settle() {
  await new Promise((resolve) => setImmediate(resolve))
  await new Promise((resolve) => setImmediate(resolve))
  await new Promise((resolve) => setImmediate(resolve))
}

function nextButtonOf(env) {
  const bar = env.elementsById['jobagent-session-bar']
  return bar && bar.children.find((c) => c.textContent === '下一位候选人')
}

function stopMessagesOf(env) {
  return env.sentMessages.filter((m) => m.type === 'jobagent:stop-session')
}

// ----------------------------------------------------------------------
// background.js harness (same shape as m4b-behavior.test.cjs)
// ----------------------------------------------------------------------

function jsonResponse(body) {
  return Promise.resolve({ ok: true, text: () => Promise.resolve(JSON.stringify(body)) })
}

function deferredResponse() {
  let resolve
  const promise = new Promise((res) => {
    resolve = res
  })
  return { promise, resolveWith: (body) => resolve({ ok: true, text: () => Promise.resolve(JSON.stringify(body)) }) }
}

function loadBackground({ tabId, storedPointer, fetchImpl }) {
  const code = fs.readFileSync(BACKGROUND_PATH, 'utf8')
  const listeners = []
  const storageData = {}
  if (storedPointer) storageData.jobagent_session_pointer = storedPointer
  const fetchCalls = []

  const sandbox = {
    chrome: {
      runtime: { onMessage: { addListener: (fn) => listeners.push(fn) } },
      storage: {
        session: {
          get: (key) => Promise.resolve({ [key]: storageData[key] }),
          set: (obj) => {
            Object.assign(storageData, obj)
            return Promise.resolve()
          },
          remove: (key) => {
            delete storageData[key]
            return Promise.resolve()
          },
        },
      },
    },
    fetch(url, init) {
      fetchCalls.push({ url: String(url), init: init || {} })
      return fetchImpl(String(url), init || {})
    },
    console,
  }
  vm.createContext(sandbox)
  new vm.Script(code, { filename: 'background.js' }).runInContext(sandbox)

  function send(message) {
    return new Promise((resolve) => {
      const sender = { tab: { id: tabId } }
      let responded = false
      const sendResponse = (resp) => {
        if (!responded) {
          responded = true
          resolve(resp)
        }
      }
      for (const fn of listeners) fn(message, sender, sendResponse)
    })
  }

  return { send, storageData, fetchCalls }
}

// ------------------------------------------------------------------------
// overlay: rapid double-click on 下一位候选人
// ------------------------------------------------------------------------

test('a rapid double-click runs the step exactly once (prepare/open/confirm)', { skip: SKIP }, async () => {
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

  const btn = nextButtonOf(env)
  // Two clicks back-to-back, synchronously, with no await in between - the
  // worst case for a double-click racing the async chain the first click
  // started.
  btn.click()
  btn.click()
  await settle()

  const prepares = env.sentMessages.filter((m) => m.type === 'jobagent:navigate-prepare')
  const confirms = env.sentMessages.filter((m) => m.type === 'jobagent:navigate-confirm')
  assert.equal(prepares.length, 1, 'exactly one prepare despite two clicks')
  assert.equal(env.openCalls.length, 1, 'exactly one click on the page despite two clicks')
  assert.equal(confirms.length, 1, 'exactly one confirm despite two clicks')
  assert.equal(stopMessagesOf(env).length, 0, 'a clean double-click never stops the session')
})

test('the next-candidate button is disabled synchronously for the duration of the step', { skip: SKIP }, async () => {
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

  const btn = nextButtonOf(env)
  assert.equal(btn.disabled, false)
  btn.click()
  // Checked immediately, before any microtask flush: the guard/disable must
  // both be synchronous, not something that only takes effect after an
  // await.
  assert.equal(btn.disabled, true, 'disabled synchronously by the first click, before any await')

  await settle()
})

test('a hard-stop whose stop POST is not confirmed keeps the Stop control usable', { skip: SKIP }, async () => {
  const env = loadOverlay({
    detectResult: { page_type: 'detail', verification: false, candidates: [] },
    openResult: { ok: true },
    backendResponses: (msg) => {
      if (msg.type === 'jobagent:session-snapshot') return { kind: 'session', session: SESSION }
      // The backend/worker never confirms this stop (e.g. unreachable).
      if (msg.type === 'jobagent:stop-session') return { ok: false }
      return { ok: false }
    },
  })
  await settle()

  nextButtonOf(env).click()
  await settle()

  const bar = env.elementsById['jobagent-session-bar']
  assert.equal(bar._removed, undefined, 'the bar (and its Stop button) must not be removed on an unconfirmed stop')
  const stopBtn = bar.children.find((c) => c.textContent === '停止会话')
  assert.ok(stopBtn, 'Stop control is still present for an explicit human retry')
})

// ------------------------------------------------------------------------
// background: duplicate prepare for the same tab
// ------------------------------------------------------------------------

test('a duplicate prepare for the same tab while one is in flight does no second fetch', { skip: SKIP }, async () => {
  const deferred = deferredResponse()
  const env = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: () => deferred.promise,
  })

  const first = env.send({ type: 'jobagent:navigate-prepare', target: 'detail', pageUrl: null })
  const second = await env.send({ type: 'jobagent:navigate-prepare', target: 'detail', pageUrl: null })

  assert.equal(second.ok, false)
  assert.equal(second.error, 'prepare_in_flight')
  assert.equal(env.fetchCalls.length, 1, 'the duplicate did not reach fetch')

  deferred.resolveWith({ id: 42, candidates_extracted: 0 })
  const firstResult = await first
  assert.equal(firstResult.ok, true)
  assert.equal(env.fetchCalls.length, 1, 'still only the one fetch from the original call')
})

test('the lock survives a successful prepare and is released only by confirm', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: (url) => jsonResponse({ id: 42, candidates_extracted: 0 }),
  })

  const prepareFetches = () => env.fetchCalls.filter((c) => /\/navigate\/prepare$/.test(c.url)).length

  const prepared = await env.send({ type: 'jobagent:navigate-prepare', target: 'detail', pageUrl: null })
  assert.equal(prepared.ok, true)
  assert.equal(prepareFetches(), 1)

  // A second prepare before confirm - still denied, still no second fetch.
  const stillLocked = await env.send({ type: 'jobagent:navigate-prepare', target: 'detail', pageUrl: null })
  assert.equal(stillLocked.ok, false)
  assert.equal(stillLocked.error, 'prepare_in_flight')
  assert.equal(prepareFetches(), 1)

  const confirmed = await env.send({
    type: 'jobagent:navigate-confirm',
    target: 'detail',
    pageUrl: 'https://www.zhipin.com/job_detail/abc.html',
    outcome: 'success',
  })
  assert.equal(confirmed.ok, true)

  // Released: a fresh prepare for the next candidate reaches fetch again.
  const nextPrepare = await env.send({ type: 'jobagent:navigate-prepare', target: 'detail', pageUrl: null })
  assert.equal(nextPrepare.ok, true)
  assert.equal(prepareFetches(), 2)
})

test('two different tabs never share the same prepare lock', { skip: SKIP }, async () => {
  const deferred = deferredResponse()
  const envA = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: () => deferred.promise,
  })
  const envB = loadBackground({
    tabId: 9,
    storedPointer: { sessionId: 43, tabId: 9 },
    fetchImpl: (url) => jsonResponse({ id: 43, candidates_extracted: 0 }),
  })

  const pendingA = envA.send({ type: 'jobagent:navigate-prepare', target: 'detail', pageUrl: null })
  const resultB = await envB.send({ type: 'jobagent:navigate-prepare', target: 'detail', pageUrl: null })

  assert.equal(resultB.ok, true, 'a different tab is unaffected by tab 7 holding the lock')
  assert.equal(envB.fetchCalls.length, 1)

  deferred.resolveWith({ id: 42, candidates_extracted: 0 })
  await pendingA
})
