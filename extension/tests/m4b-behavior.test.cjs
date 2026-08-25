'use strict'

/**
 * M4b executable behavior tests - Node built-ins only (`node:test`, `assert`,
 * `vm`). No browser, no Playwright/CDP, no network, no dependencies.
 *
 * Loads the real built `dist/background.js` and drives its
 * `jobagent:navigate-prepare` / `jobagent:navigate-confirm` handlers exactly
 * as Chrome would deliver a message, with a mocked `chrome.storage.session`
 * and `fetch`. Proves the prepare-before-click, confirm-the-real-outcome
 * state machine and per-tab ownership at the message-plumbing level; the
 * cap/state logic itself is covered by backend/tests/test_supervised_sessions.py.
 */

const { test } = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const path = require('node:path')

const BACKGROUND_PATH = path.join(__dirname, '..', 'dist', 'background.js')
const BUILT = fs.existsSync(BACKGROUND_PATH)
const SKIP = BUILT ? false : "extension not built - run 'npm run build' in extension/"

function jsonResponse(body) {
  return Promise.resolve({ ok: true, text: () => Promise.resolve(JSON.stringify(body)) })
}

function errorResponse(status, message) {
  return Promise.resolve({
    ok: false,
    status,
    text: () => Promise.resolve(JSON.stringify({ message })),
  })
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

test('prepare succeeds for the owning tab and writes nothing', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: (url) => jsonResponse({ id: 42, candidates_extracted: 0 }),
  })
  const result = await env.send({
    type: 'jobagent:navigate-prepare',
    target: 'detail',
    pageUrl: 'https://www.zhipin.com/job_detail/abc.html',
  })
  assert.equal(result.ok, true)
  assert.equal(env.fetchCalls.length, 1)
  assert.match(env.fetchCalls[0].url, /\/navigate\/prepare$/)
})

test('prepare is denied when the backend refuses (cap reached)', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: () => errorResponse(422, '已达到本次会话的候选人上限'),
  })
  const result = await env.send({ type: 'jobagent:navigate-prepare', target: 'detail', pageUrl: null })
  assert.equal(result.ok, false)
})

test('prepare from a non-owning tab is rejected with zero fetch calls', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 99,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: () => Promise.reject(new Error('must not be called')),
  })
  const result = await env.send({ type: 'jobagent:navigate-prepare', target: 'detail', pageUrl: null })
  assert.equal(result.ok, false)
  assert.equal(env.fetchCalls.length, 0)
})

test('confirm success relays outcome and returns the updated session', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: (url) => jsonResponse({ id: 42, candidates_extracted: 1 }),
  })
  const result = await env.send({
    type: 'jobagent:navigate-confirm',
    target: 'detail',
    pageUrl: 'https://www.zhipin.com/job_detail/abc.html',
    outcome: 'success',
  })
  assert.equal(result.ok, true)
  assert.equal(result.session.candidates_extracted, 1)
  assert.match(env.fetchCalls[0].url, /\/navigate\/confirm$/)
  const body = JSON.parse(env.fetchCalls[0].init.body)
  assert.equal(body.outcome, 'success')
})

test('confirm failed relays the failure outcome and error', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: (url) => jsonResponse({ id: 42, candidates_extracted: 0 }),
  })
  const result = await env.send({
    type: 'jobagent:navigate-confirm',
    target: 'detail',
    pageUrl: null,
    outcome: 'failed',
    error: 'selector_ambiguous',
  })
  assert.equal(result.ok, true)
  assert.equal(result.session.candidates_extracted, 0)
  const body = JSON.parse(env.fetchCalls[0].init.body)
  assert.equal(body.outcome, 'failed')
  assert.equal(body.error, 'selector_ambiguous')
})

test('confirm from a non-owning tab is rejected with zero fetch calls', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 99,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: () => Promise.reject(new Error('must not be called')),
  })
  const result = await env.send({
    type: 'jobagent:navigate-confirm',
    target: 'detail',
    pageUrl: null,
    outcome: 'success',
  })
  assert.equal(result.ok, false)
  assert.equal(env.fetchCalls.length, 0)
})

test('an unknown outcome is rejected before any fetch', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: () => Promise.reject(new Error('must not be called')),
  })
  const result = await env.send({
    type: 'jobagent:navigate-confirm',
    target: 'detail',
    pageUrl: null,
    outcome: 'maybe',
  })
  assert.equal(result.ok, false)
  assert.equal(env.fetchCalls.length, 0)
})

// --------------------------------------------------------------------------
// M4b phase two: jobagent:send-preview - the one call captureSelectedDetail
// makes, straight to the existing, unchanged /api/extension/jobs/preview.
// --------------------------------------------------------------------------

test('send-preview posts the candidate to the existing preview path', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: () => jsonResponse({ new_count: 1, duplicate_count: 0, incomplete_count: 0 }),
  })
  const candidate = { title: '云计算工程师', source_url: 'https://www.zhipin.com/job_detail/abc.html' }
  const result = await env.send({
    type: 'jobagent:send-preview',
    pageType: 'detail',
    pageUrl: candidate.source_url,
    candidate,
  })
  assert.equal(result.ok, true)
  assert.equal(result.preview.new_count, 1)
  assert.equal(env.fetchCalls.length, 1)
  assert.match(env.fetchCalls[0].url, /\/api\/extension\/jobs\/preview$/)
  const body = JSON.parse(env.fetchCalls[0].init.body)
  assert.equal(body.page_type, 'detail')
  assert.deepEqual(body.candidates, [candidate])
})

test('send-preview from a non-owning tab is rejected with zero fetch calls', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 99,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: () => Promise.reject(new Error('must not be called')),
  })
  const result = await env.send({
    type: 'jobagent:send-preview',
    pageType: 'detail',
    pageUrl: null,
    candidate: { title: 'x' },
  })
  assert.equal(result.ok, false)
  assert.equal(env.fetchCalls.length, 0)
})

test('send-preview surfaces a backend failure without throwing', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: () => errorResponse(500, '内部错误'),
  })
  const result = await env.send({
    type: 'jobagent:send-preview',
    pageType: 'detail',
    pageUrl: null,
    candidate: { title: 'x' },
  })
  assert.equal(result.ok, false)
  assert.ok(result.error)
})

test('send-preview accepts "search" too, and rejects an unknown page type before any fetch', { skip: SKIP }, async () => {
  const env = loadBackground({
    tabId: 7,
    storedPointer: { sessionId: 42, tabId: 7 },
    fetchImpl: () => jsonResponse({ new_count: 0, duplicate_count: 0, incomplete_count: 1 }),
  })
  const result = await env.send({
    type: 'jobagent:send-preview',
    pageType: 'search',
    pageUrl: null,
    candidate: { title: 'x' },
  })
  assert.equal(result.ok, true, 'search is a valid page_type too')
  assert.equal(env.fetchCalls.length, 1)

  const badResult = await env.send({
    type: 'jobagent:send-preview',
    pageType: 'nonsense',
    pageUrl: null,
    candidate: { title: 'x' },
  })
  assert.equal(badResult.ok, false)
  assert.equal(env.fetchCalls.length, 1, 'the rejected request never reaches fetch')
})
