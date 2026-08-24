'use strict'

/**
 * M4a executable behavior tests - Node built-ins only (`node:test`, `assert`,
 * `vm`). No browser, no Playwright/CDP, no network, no dependencies.
 *
 * The actual *built* `dist/background.js` is loaded with `vm` and run for
 * real inside a sandbox that mocks `chrome.runtime`/`chrome.storage.session`
 * and `fetch`. This exercises the real message handlers
 * (`jobagent:session-snapshot`, `jobagent:stop-session`), not a
 * re-implementation of them, so a regression in the shipped code fails
 * these tests directly.
 *
 * `overlay.ts` is deliberately not loaded here: none of the scenarios below
 * depend on its DOM rendering, and building a DOM mock for it is not needed
 * to prove the fix (background.js owns every backend call and every
 * storage.session read/write; overlay.js only relays two narrow messages
 * to it). Overlay's "no fetch / no backend URL" property is asserted
 * separately, against both source and dist, by
 * backend/tests/test_extension_m4a_contract.py.
 */

const { test } = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const path = require('node:path')

const BACKGROUND_PATH = path.join(__dirname, '..', 'dist', 'background.js')
const BUILT = fs.existsSync(BACKGROUND_PATH)
const SKIP = BUILT ? false : "extension not built - run 'npm run build' in extension/"

const SESSION = {
  id: 42,
  status: 'running',
  page_cap: 2,
  candidate_cap: 5,
  scroll_cap: 1,
  tab_origin: 'https://www.zhipin.com',
  pages_visited: 0,
  candidates_extracted: 0,
  scrolls_used: 0,
  approved_criteria: { task_name: '云计算运维-北京' },
}

function jsonResponse(body) {
  return Promise.resolve({ ok: true, text: () => Promise.resolve(JSON.stringify(body)) })
}

/** Loads the real dist/background.js into a fresh `vm` sandbox with a mock
 * `chrome`/`fetch`, and returns a `send()` helper that drives its real
 * `chrome.runtime.onMessage` listener exactly as Chrome would. */
function loadBackground({ tabId, storedPointer, fetchImpl }) {
  const code = fs.readFileSync(BACKGROUND_PATH, 'utf8')
  const listeners = []
  const storageData = {}
  if (storedPointer) storageData.jobagent_session_pointer = storedPointer
  const fetchCalls = []

  const sandbox = {
    chrome: {
      runtime: {
        onMessage: {
          addListener(fn) {
            listeners.push(fn)
          },
        },
      },
      storage: {
        session: {
          get(key) {
            return Promise.resolve({ [key]: storageData[key] })
          },
          set(obj) {
            Object.assign(storageData, obj)
            return Promise.resolve()
          },
          remove(key) {
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

function stopCalls(fetchCalls, reason) {
  return fetchCalls.filter((c) => {
    if (!c.url.includes('/stop')) return false
    try {
      return JSON.parse(c.init.body || '{}').reason === reason
    } catch {
      return false
    }
  })
}

// --------------------------------------------------------------------------

test('same-tab snapshot returns the session', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: (url) =>
      url.includes('/active') ? jsonResponse(SESSION) : Promise.reject(new Error('unexpected')),
  })
  const result = await env.send({ type: 'jobagent:session-snapshot' })
  assert.equal(result.kind, 'session')
  assert.equal(result.session.id, 42)
  assert.equal(stopCalls(env.fetchCalls, 'stale_tab').length, 0)
})

test('repeated snapshot preserves the pointer', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: (url) =>
      url.includes('/active') ? jsonResponse(SESSION) : Promise.reject(new Error('unexpected')),
  })
  const first = await env.send({ type: 'jobagent:session-snapshot' })
  const second = await env.send({ type: 'jobagent:session-snapshot' })
  assert.equal(first.kind, 'session')
  assert.equal(second.kind, 'session')
  assert.deepEqual(env.storageData.jobagent_session_pointer, { sessionId: 42, tabId: 7 })
})

test('a different tab returns other_tab with zero fetch calls', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 99, // the stored pointer below belongs to tab 7, not this one
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: () => Promise.reject(new Error('must not be called')),
  })
  const result = await env.send({ type: 'jobagent:session-snapshot' })
  assert.equal(result.kind, 'other_tab')
  assert.equal(env.fetchCalls.length, 0, 'a different tab must never call the backend')
  assert.deepEqual(
    env.storageData.jobagent_session_pointer,
    { sessionId: 42, tabId: 7 },
    "another tab's pointer must survive untouched",
  )
})

test('missing pointer with a running active session posts stale_tab', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: null,
    fetchImpl: (url) => {
      if (url.includes('/active')) return jsonResponse(SESSION)
      if (url.includes('/stop')) return jsonResponse({ ...SESSION, status: 'stopped' })
      return Promise.reject(new Error('unexpected'))
    },
  })
  const result = await env.send({ type: 'jobagent:session-snapshot' })
  assert.equal(result.kind, 'none')
  const stale = stopCalls(env.fetchCalls, 'stale_tab')
  assert.equal(stale.length, 1)
  assert.match(stale[0].url, /\/api\/extension\/sessions\/42\/stop/)
})

test('missing pointer with an unreachable backend stops nothing', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: null,
    fetchImpl: () => Promise.reject(new Error('network down')),
  })
  const result = await env.send({ type: 'jobagent:session-snapshot' })
  assert.equal(result.kind, 'none')
  assert.equal(env.fetchCalls.filter((c) => c.url.includes('/stop')).length, 0)
})

test('an unreachable backend preserves an owned pointer and posts no stop', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: () => Promise.reject(new Error('network down')),
  })
  const result = await env.send({ type: 'jobagent:session-snapshot' })
  assert.equal(result.kind, 'unreachable')
  assert.equal(env.fetchCalls.filter((c) => c.url.includes('/stop')).length, 0)
  assert.deepEqual(env.storageData.jobagent_session_pointer, { sessionId: 42, tabId: 7 })
})

test('stop message posts user_stop and clears the pointer only after success', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: (url) =>
      url.includes('/stop') ? jsonResponse({ ...SESSION, status: 'stopped' }) : Promise.reject(new Error('unexpected')),
  })
  const result = await env.send({ type: 'jobagent:stop-session' })
  assert.equal(result.ok, true)
  const userStop = stopCalls(env.fetchCalls, 'user_stop')
  assert.equal(userStop.length, 1)
  assert.equal(env.storageData.jobagent_session_pointer, undefined)
})

test('a failed stop preserves the pointer', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: () => Promise.reject(new Error('network down')),
  })
  const result = await env.send({ type: 'jobagent:stop-session' })
  assert.equal(result.ok, false)
  assert.deepEqual(env.storageData.jobagent_session_pointer, { sessionId: 42, tabId: 7 })
})

test('a tab that does not own the pointer cannot stop it', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 99,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: () => Promise.reject(new Error('must not be called')),
  })
  const result = await env.send({ type: 'jobagent:stop-session' })
  assert.equal(result.ok, false)
  assert.equal(env.fetchCalls.length, 0)
  assert.deepEqual(env.storageData.jobagent_session_pointer, { sessionId: 42, tabId: 7 })
})
