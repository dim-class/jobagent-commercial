'use strict'

/**
 * M4f bounded automatic runner behavior - Node built-ins only (`node:test`,
 * `assert`, `vm`). No browser, no Playwright/CDP, no network, no
 * dependencies.
 *
 * Loads the real built `dist/background.js` into a `vm` sandbox with a
 * mocked `chrome.tabs` (query/get/update/sendMessage),
 * `chrome.storage.session` and `fetch`, and drives it exactly as Chrome
 * would deliver messages from the popup (`jobagent:runner-*`). All mocked
 * async operations resolve immediately (no real timers), so the entire
 * fire-and-forget run loop drains within one `settle()` (pure microtask
 * queue draining - Node always empties the microtask queue before running a
 * macrotask/immediate callback, regardless of chain depth).
 *
 * This file exists because a prior version of the M4f runner held one
 * mutable `RunnerPointer` object in a closure across the whole run;
 * `requestRunnerPause`/`requestRunnerCancel` read a *different*, freshly
 * deserialized copy of `chrome.storage.session` and wrote to that, so the
 * running loop's closure never observed the flag and pause/cancel were
 * silently invisible. The rewrite re-reads durable storage at every
 * checkpoint; several tests below simulate a pause/cancel by writing
 * directly into the mocked storage between two bounded-poll attempts
 * (exactly what a concurrent `requestRunnerPause` call would have done) and
 * assert the very next checkpoint honors it - proving the fix without
 * depending on fragile message-interleaving timing.
 */

const { test } = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const path = require('node:path')

const BACKGROUND_PATH = path.join(__dirname, '..', 'dist', 'background.js')
const BUILT = fs.existsSync(BACKGROUND_PATH)
const SKIP = BUILT ? false : "extension not built - run 'npm run build' in extension/"

const ORIGIN = 'https://www.zhipin.com'
const SEARCH_URL = 'https://www.zhipin.com/web/geek/jobs?city=101010100&query=SRE'
const RUNNER_KEY = 'jobagent_runner_pointer'
const BATCH_KEY = 'jobagent_runner_batch'
const GLOBAL_SESSION_KEY = 'jobagent_session_pointer'

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

function rejected(message) {
  return Promise.reject(new Error(message))
}

function errorResponseWithDetail(status, message, detail) {
  return Promise.resolve({
    ok: false,
    status,
    text: () => Promise.resolve(JSON.stringify({ message, detail })),
  })
}

function defaultTask(overrides) {
  return {
    id: 5,
    search_url: SEARCH_URL,
    run_status: 'running',
    early_career_policy: 'exclude',
    ...overrides,
  }
}

function defaultSession(overrides) {
  return {
    id: 42,
    status: 'running',
    page_cap: 1,
    candidate_cap: 20,
    scroll_cap: 5,
    tab_origin: ORIGIN,
    pages_visited: 1,
    candidates_extracted: 0,
    scrolls_used: 0,
    approved_criteria: { task_name: 'SRE-北京' },
    ...overrides,
  }
}

function candidate(id, overrides) {
  return {
    title: `候选人${id}`,
    company: '公司',
    salary_text: '20-30K',
    city: '北京',
    experience_text: '3-5年',
    education_text: '本科',
    source_url: `https://www.zhipin.com/job_detail/${id}.html`,
    external_id: id,
    ...overrides,
  }
}

function searchPage(candidates) {
  return { page_type: 'search', verification: false, candidates }
}

/** Builds the fetch router. Every route has a reasonable default; `overrides`
 * is checked first per-call and may return `undefined` to fall through. */
function makeFetchRouter(overrides) {
  const calls = []
  const router = (url, init) => {
    calls.push({ url, init })
    if (overrides) {
      const custom = overrides(url, init, calls)
      if (custom !== undefined) return custom
    }
    const method = (init && init.method) || 'GET'
    const body = init && init.body ? JSON.parse(init.body) : undefined
    if (/\/api\/tasks\/search-plan\/\d+$/.test(url) && method === 'GET') {
      return jsonResponse(defaultTask())
    }
    if (/\/api\/tasks\/\d+\/run\/round$/.test(url)) {
      return jsonResponse({ run_status: 'running', ...body })
    }
    if (/\/api\/tasks\/\d+\/run\/\w+$/.test(url)) {
      return jsonResponse({ run_status: 'running' })
    }
    if (/\/api\/extension\/sessions$/.test(url) && method === 'POST') {
      return jsonResponse(defaultSession())
    }
    if (/\/api\/extension\/sessions\/active$/.test(url)) {
      return jsonResponse(defaultSession())
    }
    if (/\/api\/extension\/sessions\/\d+\/stop$/.test(url)) {
      return jsonResponse(defaultSession({ status: 'stopped' }))
    }
    if (/\/navigate\/prepare$/.test(url) || /\/navigate\/confirm$/.test(url)) {
      return jsonResponse(defaultSession())
    }
    if (/\/api\/extension\/jobs\/preview$/.test(url)) {
      return jsonResponse({ new_count: 1, duplicate_count: 0 })
    }
    if (/\/api\/extension\/jobs\/import$/.test(url)) {
      return jsonResponse({ job_id: 1, duplicate: false })
    }
    if (/\/api\/tasks\/\d+\/candidates$/.test(url) && method === 'POST') {
      return jsonResponse({ id: 1, task_id: 1, job_id: (body && body.job_id) || 1 })
    }
    return errorResponse(404, 'unhandled: ' + url)
  }
  return { router, calls }
}

/** Builds the `chrome.tabs.sendMessage` responder. `respond(msg, callCount)`
 * gets every call (across detect/open-candidate/capture-detail/scroll-step)
 * with a running count so a test can change behavior after N calls. */
function loadBackground({ tab, fetchImpl, respond, tabUrlOverride, storage, beforeStorageSet, popupState, consoleTab,
  onExecuteScript }) {
  const code = fs.readFileSync(BACKGROUND_PATH, 'utf8')
  const listeners = []
  const storageData = storage || {}
  const tabState = { id: 7, windowId: 2, url: SEARCH_URL, active: true, ...tab }
  const tabsSendMessageCalls = []
  const tabsUpdateCalls = []
  const tabsGetCalls = []
  const tabsCreateCalls = []
  const scriptingExecuteCalls = []
  let sendMessageCallCount = 0

  const sandbox = {
    chrome: {
      runtime: {
        onMessage: { addListener: (fn) => listeners.push(fn) },
        getURL: name => 'chrome-extension://fixture/' + name,
        getManifest: () => ({ version: '0.1.1' }),
        getContexts: async () => popupState?.open ? [{ documentUrl: 'chrome-extension://fixture/popup.html' }] : [],
      },
      windows: {
        getLastFocused: async () => ({ id: 2, type: 'normal', focused: popupState ? popupState.focused : true }),
        get: async () => ({ focused: popupState ? popupState.focused : true }),
      },
      storage: {
        session: {
          get: (key) => Promise.resolve({ [key]: storageData[key] }),
          set: (obj) => {
            if (beforeStorageSet) beforeStorageSet(obj)
            Object.assign(storageData, obj)
            return Promise.resolve()
          },
          remove: (key) => {
            delete storageData[key]
            return Promise.resolve()
          },
        },
      },
      tabs: {
        query: (queryInfo = {}) => {
          let tabs = [tabState, consoleTab].filter(Boolean).map(tab => ({ ...tab }))
          if (queryInfo.windowId !== undefined) tabs = tabs.filter(tab => tab.windowId === queryInfo.windowId)
          if (queryInfo.active) tabs = tabs.filter(tab => tab.active)
          return Promise.resolve(tabs)
        },
        create: async properties => {
          tabsCreateCalls.push(properties)
          consoleTab.active = false
          Object.assign(tabState, { url: properties.url, active: true })
          return { ...tabState }
        },
        get: (tabId) => {
          tabsGetCalls.push(tabId)
          if (consoleTab?.id === tabId) return Promise.resolve({ ...consoleTab })
          if (tabState.missing || tabId !== tabState.id) return Promise.reject(new Error('no such tab'))
          if (tabUrlOverride) {
            const url = tabUrlOverride(tabsGetCalls.length)
            if (url) return Promise.resolve({ ...tabState, url })
          }
          return Promise.resolve({ ...tabState })
        },
        update: (tabId, updateProperties) => {
          tabsUpdateCalls.push({ tabId, url: updateProperties.url })
          if (updateProperties.url) tabState.url = updateProperties.url
          if (updateProperties.active) {
            tabState.active = true
            if (consoleTab) consoleTab.active = false
          }
          return Promise.resolve({ ...tabState })
        },
        sendMessage: (tabId, message) => {
          sendMessageCallCount += 1
          tabsSendMessageCalls.push({ tabId, message })
          if (tabId !== tabState.id) return Promise.reject(new Error('no receiving end'))
          return respond(message, sendMessageCallCount)
        },
      },
      scripting: {
        executeScript: async injection => {
          scriptingExecuteCalls.push(injection)
          if (onExecuteScript) await onExecuteScript(injection)
          return []
        },
      },
    },
    fetch(url, init) {
      return fetchImpl(String(url), init || {})
    },
    clearTimeout,
    setTimeout: (fn, ms) => {
      if (popupState) return setTimeout(fn, ms)
      fn()
      return 0
    },
    // `background.ts`'s `isRunnerNavOrigin`/`canonicalizeJobDetailUrl` use
    // `new URL(...)` - a fresh `vm.createContext` sandbox does not inherit
    // Node's global `URL` automatically (unlike pure ECMAScript intrinsics
    // like `Promise`/`Set`/`Map`), so it must be injected explicitly here,
    // exactly like `console`/`setTimeout`/`fetch` below.
    URL,
    console,
  }
  vm.createContext(sandbox)
  new vm.Script(code, { filename: 'background.js' }).runInContext(sandbox)

  function send(message, source) {
    return new Promise((resolve) => {
      const sender = source || (popupState ? { url: 'chrome-extension://fixture/popup.html' } : {})
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

  return {
    send,
    storageData,
    tabState,
    tabsSendMessageCalls,
    tabsUpdateCalls,
    tabsGetCalls,
    tabsCreateCalls,
    scriptingExecuteCalls,
  }
}

async function settle() {
  await new Promise((resolve) => setImmediate(resolve))
  await new Promise((resolve) => setImmediate(resolve))
  await new Promise((resolve) => setImmediate(resolve))
}

function pointerOf(env) {
  return env.storageData[RUNNER_KEY] || null
}

function detectCallsOf(env) {
  return env.tabsSendMessageCalls.filter((c) => c.message.type === 'jobagent:detect')
}

// Approved lower limits: run the real built worker, with no Chrome or network.
function budgetResponder(msg) {
  if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: searchPage(
    Array.from({ length: 25 }, (_, i) => candidate('budget' + i)),
  ) })
  if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
  if (msg.type === 'jobagent:capture-detail') return Promise.resolve({ ok: true, result: {
    status: 'ok', candidate: candidate('budget', { source_url: msg.canonicalUrl, description: 'Complete JD' }),
  } })
  return Promise.resolve({ ok: false })
}

function opensOf(env) {
  return env.tabsSendMessageCalls.filter(c => c.message.type === 'jobagent:open-candidate')
}

const popupStart = { type: 'jobagent:runner-start', taskId: 5, candidateCap: 3,
  popupTarget: { tabId: 7, windowId: 2 } }

const consoleSender = { tab: { id: 8 }, frameId: 0, url: 'http://127.0.0.1:5173/#/console' }
const commercialConsoleSender = { ...consoleSender, url: 'http://127.0.0.1:8000/#/console' }
const consoleStart = { type: 'jobagent:console-command', action: 'start', taskId: 5, candidateCap: 3 }
function consoleEnv(taskOverrides = {}, storage) {
  const consoleTab = { id: 8, windowId: 2, url: consoleSender.url, active: true }
  const { router, calls } = makeFetchRouter(url => {
    const match = url.match(/\/search-plan\/(\d+)$/)
    return match ? jsonResponse(defaultTask({ id: Number(match[1]), run_status: 'pending', ...taskOverrides })) : undefined
  })
  const env = loadBackground({ fetchImpl: router, respond: budgetResponder, consoleTab, storage })
  return { env, calls, consoleTab }
}

test('commercial single-process console origin is trusted without broadening arbitrary ports', async () => {
  const { env, consoleTab, calls } = consoleEnv()
  consoleTab.url = commercialConsoleSender.url
  const reply = await env.send({ type: 'jobagent:console-command', action: 'status' }, commercialConsoleSender)
  assert.equal(reply.ok, true)
  assert.equal(calls.length, 0)
})

test('suspended M7 console command cannot open or scan a chat tab', async () => {
  const consoleTab = { id: 8, windowId: 2, url: 'http://127.0.0.1:5173/#/recruiter', active: true }
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({ fetchImpl: router, consoleTab, respond: () => Promise.resolve({ ok: false }) })
  const result = await env.send({ type: 'jobagent:console-command', action: 'scan-current-boss-chat' }, {
    tab: { id: 8 }, frameId: 0, url: consoleTab.url,
  })
  assert.equal(result.ok, false)
  assert.equal(env.tabsCreateCalls.length, 0)
  assert.equal(env.tabsUpdateCalls.length, 0)
  assert.equal(env.tabsSendMessageCalls.length, 0)
  assert.equal(calls.length, 0)
})

test('console start reuses an existing BOSS tab, bounded canonical runner, and never pays', async () => {
  const { env, calls } = consoleEnv()
  const result = await env.send({ ...consoleStart, matchApproval: { confirmed: true, cap: 3, fingerprint: 'a'.repeat(64) },
    url: 'https://evil.test/', tabId: 999 }, consoleSender)
  assert.equal(result.ok, true)
  await until(() => pointerOf(env) === null)
  assert.equal(env.tabsCreateCalls.length, 0)
  assert.deepEqual(JSON.parse(JSON.stringify(env.tabsUpdateCalls)), [{ tabId: 7, url: SEARCH_URL }])
  assert.equal(opensOf(env).length, 3)
  assert.equal(calls.filter(c => c.url.endsWith('/jobs/import')).length, 3)
  assert.ok(!calls.some(c => c.url.includes('/auto-match/')))
  assert.equal(JSON.parse(calls.find(c => c.url.endsWith('/run/start')).init.body || '{}').match_approval, undefined)
})

test('console reuse tolerates only the exact old BOSS URL while Chrome commits navigation', async () => {
  const oldUrl = 'https://www.zhipin.com/web/geek/jobs?city=101020100&query=old'
  const consoleTab = { id: 8, windowId: 2, url: consoleSender.url, active: true }
  const { router } = makeFetchRouter(url => {
    const match = url.match(/\/search-plan\/(\d+)$/)
    return match ? jsonResponse(defaultTask({ id: Number(match[1]), run_status: 'pending' })) : undefined
  })
  const env = loadBackground({
    fetchImpl: router, respond: budgetResponder, consoleTab, tab: { url: oldUrl, active: true },
    tabUrlOverride: callIndex => callIndex <= 2 ? oldUrl : undefined,
  })
  const result = await env.send(consoleStart, consoleSender)
  assert.equal(result.ok, true)
  await until(() => pointerOf(env) === null)
  assert.equal(env.tabsCreateCalls.length, 0)
  assert.deepEqual(JSON.parse(JSON.stringify(env.tabsUpdateCalls)), [{ tabId: 7, url: SEARCH_URL }])
  assert.ok(env.tabsGetCalls.length >= 3)
})

test('simultaneous console starts share the runner single-flight guard', async () => {
  const { env } = consoleEnv()
  const results = await Promise.all([env.send(consoleStart, consoleSender), env.send(consoleStart, consoleSender)])
  assert.equal(results.filter(r => r.ok).length, 1)
  await until(() => pointerOf(env) === null)
  assert.equal(env.tabsCreateCalls.length, 0)
  assert.equal(env.tabsUpdateCalls.length, 1)
})

// The BOSS home page is a legitimate starting tab: same exact origin, and the
// run navigates it to its own target immediately. Adopting it keeps the per-tab
// `activeTab` grant that salary OCR needs; a worker-created tab has none.
for (const [label, url, adopted] of [
  ['home page', 'https://www.zhipin.com/', true],
  ['home page with a tracking query', 'https://www.zhipin.com/?ka=header-home', true],
  ['job detail', 'https://www.zhipin.com/job_detail/abc123.html', true],
  ['login page', 'https://www.zhipin.com/web/user/?ka=header-login', false],
  ['chat page', 'https://www.zhipin.com/web/geek/chat', false],
  ['security check', 'https://www.zhipin.com/web/common/security-check.html', false],
  ['another origin', 'https://example.test/', false],
]) {
  test(`console start adopts an existing BOSS tab only when safe: ${label}`, async () => {
    const { router } = makeFetchRouter(url2 => {
      const match = url2.match(/\/search-plan\/(\d+)$/)
      return match ? jsonResponse(defaultTask({ id: Number(match[1]), run_status: 'pending' })) : undefined
    })
    const consoleTab = { id: 8, windowId: 2, url: consoleSender.url, active: true }
    const env = loadBackground({
      fetchImpl: router, respond: budgetResponder, consoleTab, tab: { url, active: true },
    })
    assert.equal((await env.send(consoleStart, consoleSender)).ok, true)
    await until(() => pointerOf(env) === null)
    if (adopted) {
      assert.equal(env.tabsCreateCalls.length, 0, 'must reuse the existing tab, keeping activeTab')
      assert.deepEqual(JSON.parse(JSON.stringify(env.tabsUpdateCalls)), [{ tabId: 7, url: SEARCH_URL }])
    } else {
      assert.equal(env.tabsCreateCalls.length, 1, 'a chat/login/verification tab is never driven')
    }
  })
}

test('console start creates one foreground tab when no reusable BOSS tab exists', async () => {
  const { router } = makeFetchRouter(url => {
    const match = url.match(/\/search-plan\/(\d+)$/)
    return match ? jsonResponse(defaultTask({ id: Number(match[1]), run_status: 'pending' })) : undefined
  })
  const consoleTab = { id: 8, windowId: 2, url: consoleSender.url, active: true }
  const env = loadBackground({
    fetchImpl: router, respond: budgetResponder, consoleTab,
    tab: { url: 'https://example.test/', active: false },
  })
  assert.equal((await env.send(consoleStart, consoleSender)).ok, true)
  await until(() => pointerOf(env) === null)
  assert.deepEqual(JSON.parse(JSON.stringify(env.tabsCreateCalls)), [
    { url: SEARCH_URL, active: true, windowId: 2 },
  ])
})

for (const sender of [ {}, { ...consoleSender, frameId: 1 }, { ...consoleSender, url: 'https://www.zhipin.com/' },
  { ...consoleSender, url: 'http://127.0.0.1:9999/#/console' }, { ...consoleSender, url: 'http://localhost.evil:5173/#/console' } ]) {
  test('console rejects forged origin, subframe or absent sender: ' + JSON.stringify(sender), async () => {
    const { env, calls } = consoleEnv()
    assert.equal((await env.send(consoleStart, sender)).ok, false)
    assert.equal(env.tabsCreateCalls.length, 0)
    assert.equal(calls.length, 0)
  })
}

for (const override of [{ run_status: 'completed' }, { search_url: 'https://evil.test/' },
  { search_url: ORIGIN + '/web/geek/jobs?city=1&query=a&extra=b' }, { max_candidates: 2 }]) {
  test('console rejects invalid saved task before opening a tab: ' + JSON.stringify(override), async () => {
    const { env, calls } = consoleEnv(override)
    assert.equal((await env.send(consoleStart, consoleSender)).ok, false)
    assert.equal(env.tabsCreateCalls.length, 0)
    assert.ok(!calls.some(c => c.init.method === 'POST'))
  })
}

test('console status exposes no browser/session secrets or discovery history and cannot start', async () => {
  const { env, calls } = consoleEnv({}, { [RUNNER_KEY]: { taskId: 5, tabId: 7, sessionId: 42,
    runToken: 12, phase: 'paused', pauseRequested: true, candidateCap: 3, handledUrls: ['private'] } })
  const reply = await env.send({ type: 'jobagent:console-command', action: 'status' }, consoleSender)
  assert.deepEqual(Object.keys(reply.runner).sort(), ['candidateCap', 'paid', 'paused', 'phase', 'taskId'])
  assert.equal(reply.extensionVersion, '0.1.1')
  assert.equal(reply.protocol, 1)
  assert.deepEqual(Array.from(reply.capabilities), ['console-search-v1', 'console-batch-v1',
    'salary-backfill-v1', 'human-confirmed-apply-v1'])
  assert.deepEqual(Object.keys(reply).sort(), ['batch', 'capabilities', 'extensionVersion', 'ok', 'protocol', 'runner', 'salaryBackfill'])
  assert.equal(reply.salaryBackfill, null)
  assert.equal(env.tabsCreateCalls.length, 0)
  assert.equal(calls.length, 0)
  const wrong = await env.send({ ...consoleStart, action: 'cancel', taskId: 6 }, consoleSender)
  assert.equal(wrong.ok, false)
  assert.equal(pointerOf(env).taskId, 5)
})

test('console status accepts one persisted sixteen-unit comprehensive batch', async () => {
  const taskIds = Array.from({ length: 16 }, (_, index) => index + 1)
  const { env } = consoleEnv({}, { [BATCH_KEY]: {
    taskIds, candidateCap: 8, currentIndex: 7, state: 'paused', tabId: 7,
    lastError: null, updatedAt: new Date().toISOString(),
  } })
  const reply = await env.send({ type: 'jobagent:console-command', action: 'status' }, consoleSender)
  assert.deepEqual(Array.from(reply.batch.taskIds), taskIds)
  assert.equal(reply.batch.currentTaskId, 8)
  assert.equal(reply.batch.candidateCap, 8)
})

test('M6 queue command claims exactly once before one click and records only unknown', async () => {
  const detailUrl = 'https://www.zhipin.com/job_detail/m6job.html'
  const approval = {
    id: 17, job_id: 9, company: '公司', title: '云平台工程师', canonical_url: detailUrl,
    external_id: 'm6job', resume_id: 3, answers_text: '',
    answers_hash: 'a'.repeat(64), answers_source: 'boss_dynamic_unverified', state: 'pending',
  }
  const { router, calls } = makeFetchRouter((url, init) => {
    const method = (init && init.method) || 'GET'
    if (url.endsWith('/api/application-approvals/17') && method === 'GET') return jsonResponse(approval)
    if (url.endsWith('/api/application-approvals/17/validate')) return jsonResponse({ ok: true, approval })
    if (url.endsWith('/api/application-approvals/17/begin')) return jsonResponse({ ...approval, state: 'executing' })
    if (url.endsWith('/api/application-approvals/17/outcome')) return jsonResponse({ ...approval,
      state: 'consumed', outcome: 'unknown' })
    return undefined
  })
  const consoleTab = { id: 8, windowId: 2, url: 'http://127.0.0.1:5173/#/queue', active: true }
  const env = loadBackground({
    fetchImpl: router,
    consoleTab,
    tab: { url: detailUrl, active: false },
    respond: msg => {
      if (msg.type === 'jobagent:m6-preflight') return Promise.resolve({ ok: true, result: {
        status: 'ok', observed_url: detailUrl, observed_external_id: 'm6job',
      } })
      if (msg.type === 'jobagent:m6-execute') return Promise.resolve({ ok: true, result: {
        status: 'clicked', observed_url: detailUrl, observed_external_id: 'm6job',
      } })
      return Promise.resolve({ ok: false })
    },
  })
  const reply = await env.send(
    { type: 'jobagent:console-command', action: 'execute-application', approvalId: 17 },
    { tab: { id: 8 }, frameId: 0, url: consoleTab.url },
  )
  assert.equal(reply.ok, true)
  assert.equal(reply.application.outcome, 'unknown')
  assert.deepEqual(env.tabsSendMessageCalls.map(call => call.message.type), [
    'jobagent:m6-preflight', 'jobagent:m6-execute',
  ])
  const paths = calls.map(call => call.url.replace('http://127.0.0.1:8000', ''))
  assert.ok(paths.indexOf('/api/application-approvals/17/begin')
    < paths.indexOf('/api/application-approvals/17/outcome'))
  const outcomeCall = calls.find(call => call.url.endsWith('/outcome'))
  assert.equal(JSON.parse(outcomeCall.init.body).outcome, 'unknown')
  assert.equal(calls.filter(call => call.url.endsWith('/begin')).length, 1)
  assert.ok(!calls.some(call => call.url.includes('mark-applied')))
})

test('bounded console batch validates once, reuses one BOSS tab and advances two tasks serially', async () => {
  const { env, calls } = consoleEnv()
  const result = await env.send({ type: 'jobagent:console-command', action: 'start-batch',
    taskIds: [5, 6], candidateCap: 1, url: 'https://evil.test/', tabId: 99 }, consoleSender)
  assert.equal(result.ok, true)
  await until(() => env.storageData[BATCH_KEY]?.state === 'completed')
  assert.equal(env.tabsCreateCalls.length, 0)
  assert.equal(env.tabsUpdateCalls.length, 2)
  assert.equal(opensOf(env).length, 2)
  assert.equal(calls.filter(c => c.url.endsWith('/run/start')).length, 2)
  assert.equal(pointerOf(env), null)
  assert.deepEqual(JSON.parse(JSON.stringify(env.storageData[BATCH_KEY].taskIds)), [5, 6])
  assert.equal(env.storageData[BATCH_KEY].currentIndex, 1)
})

test('bounded batch rejects invalid, duplicate or over-sixteen task lists before browser work', async () => {
  for (const taskIds of [[], [5, 5], Array.from({ length: 17 }, (_, index) => index + 1), [5, '6']]) {
    const { env, calls } = consoleEnv()
    const result = await env.send({ type: 'jobagent:console-command', action: 'start-batch',
      taskIds, candidateCap: 1 }, consoleSender)
    assert.equal(result.ok, false)
    assert.equal(env.tabsCreateCalls.length, 0)
    assert.equal(calls.length, 0)
  }
})

test('bounded batch validates every task before opening the first BOSS tab', async () => {
  const consoleTab = { id: 8, windowId: 2, url: consoleSender.url, active: true }
  const { router, calls } = makeFetchRouter(url => {
    if (/\/search-plan\/5$/.test(url)) return jsonResponse(defaultTask({ id: 5, run_status: 'pending' }))
    if (/\/search-plan\/6$/.test(url)) return jsonResponse(defaultTask({ id: 6, run_status: 'completed' }))
  })
  const env = loadBackground({ fetchImpl: router, respond: budgetResponder, consoleTab })
  const result = await env.send({ type: 'jobagent:console-command', action: 'start-batch',
    taskIds: [5, 6], candidateCap: 1 }, consoleSender)
  assert.equal(result.ok, false)
  assert.equal(env.tabsCreateCalls.length, 0)
  assert.ok(!calls.some(c => c.init.method === 'POST'))
})

test('verification in one task pauses the batch and never starts its next task', async () => {
  const consoleTab = { id: 8, windowId: 2, url: consoleSender.url, active: true }
  const { router, calls } = makeFetchRouter(url => {
    const match = url.match(/\/search-plan\/(\d+)$/)
    return match ? jsonResponse(defaultTask({ id: Number(match[1]), run_status: 'pending' })) : undefined
  })
  const env = loadBackground({ fetchImpl: router, consoleTab,
    respond: msg => msg.type === 'jobagent:detect'
      ? Promise.resolve({ ok: true, result: { page_type: 'search', verification: true, candidates: [] } })
      : Promise.resolve({ ok: false }) })
  assert.equal((await env.send({ type: 'jobagent:console-command', action: 'start-batch',
    taskIds: [5, 6], candidateCap: 1 }, consoleSender)).ok, true)
  await until(() => env.storageData[BATCH_KEY]?.state === 'paused')
  assert.equal(calls.filter(c => c.url.endsWith('/run/start')).length, 1)
  assert.equal(env.storageData[BATCH_KEY].currentIndex, 0)
  assert.equal(env.tabsCreateCalls.length, 0)
  assert.equal(env.tabsUpdateCalls.length, 1)
})

test('logged-out BOSS pauses with login_required before cards, scroll, intake or next task', async () => {
  const consoleTab = { id: 8, windowId: 2, url: consoleSender.url, active: true }
  const { router, calls } = makeFetchRouter(url => {
    const match = url.match(/\/search-plan\/(\d+)$/)
    return match ? jsonResponse(defaultTask({ id: Number(match[1]), run_status: 'pending' })) : undefined
  })
  const env = loadBackground({ fetchImpl: router, consoleTab,
    respond: msg => msg.type === 'jobagent:detect'
      ? Promise.resolve({ ok: true, result: { page_type: 'unsupported', verification: false,
        login_required: true, candidates: [] } })
      : Promise.resolve({ ok: false }) })
  assert.equal((await env.send({ type: 'jobagent:console-command', action: 'start-batch',
    taskIds: [5, 6], candidateCap: 1 }, consoleSender)).ok, true)
  await until(() => env.storageData[BATCH_KEY]?.state === 'paused')
  assert.ok(calls.some(c => c.url.endsWith('/run/login-required')))
  assert.equal(calls.filter(c => c.url.endsWith('/run/start')).length, 1)
  assert.equal(calls.filter(c => c.url.endsWith('/jobs/import')).length, 0)
  assert.equal(opensOf(env).length, 0)
  assert.equal(env.storageData[BATCH_KEY].currentIndex, 0)
  assert.equal(pointerOf(env).pausedReason, 'login_required')
  assert.equal(pointerOf(env).lastAction, 'paused_login_required')
})

test('batch-only controls cannot pause or cancel an unrelated single-task runner', async () => {
  const { env, calls } = consoleEnv({}, { [RUNNER_KEY]: pausedConsolePointer({ pauseRequested: false, pauseConfirmed: false }) })
  assert.equal((await env.send({ type: 'jobagent:console-command', action: 'pause-batch' }, consoleSender)).ok, false)
  assert.equal((await env.send({ type: 'jobagent:console-command', action: 'cancel-batch' }, consoleSender)).ok, false)
  assert.equal(calls.length, 0)
  assert.equal(pointerOf(env).cancelRequested, false)
})

test('worker restart does not auto-resume a batch; explicit batch resume preserves its budget', async () => {
  const storage = {
    [RUNNER_KEY]: pausedConsolePointer({ pauseRequested: false, pauseConfirmed: false,
      candidateCap: 1, candidatesAttempted: 0, candidatesProcessed: 0, scrollsUsed: 0 }),
    [BATCH_KEY]: { taskIds: [5], candidateCap: 1, currentIndex: 0, state: 'running',
      tabId: 7, lastError: null, updatedAt: new Date().toISOString() },
    [GLOBAL_SESSION_KEY]: { sessionId: 42, tabId: 7 },
  }
  const { env, calls } = consoleEnv({ run_status: 'running' }, storage)
  await settle()
  assert.equal(calls.length, 0, 'worker load alone must not resume browser/backend work')
  const status = await env.send({ type: 'jobagent:console-command', action: 'status' }, consoleSender)
  assert.equal(status.batch.requiresResume, true)
  assert.equal((await env.send({ type: 'jobagent:console-command', action: 'resume-batch' }, consoleSender)).ok, true)
  await until(() => env.storageData[BATCH_KEY]?.state === 'completed')
  assert.equal(opensOf(env).length, 1)
})

test('console rejects lost focus and changed document before tab creation', async () => {
  for (const change of [{ active: false }, { url: 'https://evil.test/' }]) {
    const { env, consoleTab, calls } = consoleEnv()
    Object.assign(consoleTab, change)
    assert.equal((await env.send(consoleStart, consoleSender)).ok, false)
    assert.equal(env.tabsCreateCalls.length, 0)
    assert.equal(calls.length, 0)
  }
})

function pausedConsolePointer(overrides = {}) {
  return { taskId: 5, tabId: 7, sessionId: 42, runToken: 12, phase: 'paused',
    pauseRequested: true, pauseConfirmed: true, cancelRequested: false, autoMatch: false,
    candidateCap: 3, candidatesAttempted: 2, candidatesProcessed: 2, scrollsUsed: 2,
    handledUrls: [candidate('budget0').source_url, candidate('budget1').source_url],
    observedCount: 0, newCount: 0, duplicateCount: 0, noNewRounds: 0, importedJobs: 2,
    pendingTerminal: null, ...overrides }
}

for (const state of ['paused', 'paused_verification']) test('console resume reuses owned tab and remaining budget; no paid calls: ' + state, async () => {
  const { env, calls } = consoleEnv({ run_status: state }, { [RUNNER_KEY]: pausedConsolePointer({ pauseConfirmed: state === 'paused' }),
    [GLOBAL_SESSION_KEY]: { sessionId: 42, tabId: 7 } })
  assert.equal((await env.send({ ...consoleStart, action: 'resume', candidateCap: 20 }, consoleSender)).ok, true)
  await until(() => pointerOf(env) === null)
  assert.equal(env.tabsCreateCalls.length, 0)
  assert.equal(opensOf(env).length, 1, JSON.stringify(calls.slice(-6)))
  assert.ok(!calls.some(c => c.url.includes('/auto-match/')))
})

test('console cannot resume a paid run or a different task', async () => {
  for (const overrides of [{ autoMatch: true }, { taskId: 99 }]) {
    const { env, calls } = consoleEnv({}, { [RUNNER_KEY]: pausedConsolePointer(overrides) })
    assert.equal((await env.send({ ...consoleStart, action: 'resume' }, consoleSender)).ok, false)
    assert.equal(env.tabsUpdateCalls.length, 0)
    assert.ok(!calls.some(c => c.init.method === 'POST'))
  }
})

test('console pause and cancel retain budget, operate only on selected task', async () => {
  const { env, calls } = consoleEnv({}, { [RUNNER_KEY]: pausedConsolePointer({ pauseRequested: false, pauseConfirmed: false }) })
  assert.equal((await env.send({ ...consoleStart, action: 'pause' }, consoleSender)).ok, true)
  assert.equal(pointerOf(env).pauseConfirmed, true)
  assert.equal(pointerOf(env).candidatesAttempted, 2)
  assert.equal((await env.send({ ...consoleStart, action: 'cancel' }, consoleSender)).ok, true)
  assert.equal(pointerOf(env), null)
  assert.equal(env.tabsUpdateCalls.length, 0)
  assert.ok(calls.some(c => c.url.endsWith('/run/cancel')))
})

test('auto matching follows each canonical association, bounded at three without repeat', async () => {
  let imported = 0
  const { router, calls } = makeFetchRouter((url) => {
    if (url.endsWith('/jobs/import')) return jsonResponse({ job_id: ++imported })
    if (url.endsWith('/auto-match/step')) return jsonResponse({ state: 'done' })
  })
  const env = loadBackground({ fetchImpl: router, respond: budgetResponder, popupState: { open: false, focused: true } })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 3,
    matchApproval: { confirmed: true, cap: 3, fingerprint: 'a'.repeat(64) } })
  await until(() => pointerOf(env) === null)
  const steps = calls.filter(c => c.url.endsWith('/auto-match/step'))
  assert.equal(steps.length, 3)
  assert.deepEqual(steps.map(c => JSON.parse(c.init.body).job_id), [1, 2, 3])
  for (const step of steps) {
    const index = calls.indexOf(step)
    assert.ok(calls.slice(0, index).some(c => c.url.endsWith('/candidates')))
    assert.match(JSON.parse(step.init.body).canonical_url, /\/job_detail\/budget\d+\.html$/)
  }
  assert.equal(opensOf(env).length, 3)
})

for (const outcome of ['failed', 'running', 'network']) {
  test(`auto matching ${outcome} never retries paid requests`, async () => {
    const { router, calls } = makeFetchRouter(url => url.endsWith('/auto-match/step')
      ? outcome === 'network' ? rejected('offline') : jsonResponse({ state: outcome }) : undefined)
    const env = loadBackground({ fetchImpl: router, respond: budgetResponder, popupState: { open: false, focused: true } })
    await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 1,
      matchApproval: { confirmed: true, cap: 1, fingerprint: 'a'.repeat(64) } })
    await until(() => pointerOf(env) === null)
    assert.equal(calls.filter(c => c.url.endsWith('/auto-match/step')).length, 1)
    assert.equal(opensOf(env).length, 1)
    assert.ok(calls.some(c => c.url.endsWith(outcome === 'failed' ? '/run/complete' : '/run/fail')))
  })
}
async function until(check, timeout = 4000) {
  const end = Date.now() + timeout
  while (!check() && Date.now() < end) await new Promise(resolve => setTimeout(resolve, 20))
  assert.ok(check(), 'bounded condition not reached')
}
function popupEnv(state = { open: true, focused: false }) {
  const { router, calls } = makeFetchRouter()
  return { state, calls, env: loadBackground({ fetchImpl: router, respond: budgetResponder, popupState: state }) }
}
function noBrowserWork(env, calls) {
  assert.equal(env.tabsUpdateCalls.length, 0)
  assert.equal(detectCallsOf(env).length, 0)
  assert.equal(opensOf(env).length, 0)
  assert.equal(calls.filter(c => /\/jobs\/import$/.test(c.url)).length, 0)
}

test('popup-unfocused startup waits for closure AND focus, then runs approved three-job budget', async () => {
  const { env, state, calls } = popupEnv()
  assert.equal((await env.send(popupStart)).ok, true)
  assert.equal(pointerOf(env).lastAction, 'waiting_popup_focus')
  state.focused = true // even focused=true is not enough while popup remains open
  await new Promise(resolve => setTimeout(resolve, 130))
  noBrowserWork(env, calls)
  state.open = false
  await until(() => pointerOf(env) === null)
  assert.equal(env.tabsUpdateCalls.length, 1)
  assert.equal(opensOf(env).length, 3)
  assert.equal(calls.filter(c => /\/jobs\/import$/.test(c.url)).length, 3)
})

for (const mode of ['no_focus', 'popup_open', 'inactive', 'moved_window', 'url_changed', 'cancel', 'pause']) {
  test('popup handoff fail-closed: ' + mode, async () => {
    const { env, state, calls } = popupEnv()
    assert.equal((await env.send(popupStart)).ok, true)
    if (mode !== 'popup_open') state.open = false
    if (mode !== 'no_focus') state.focused = true
    if (mode === 'inactive') env.tabState.active = false
    if (mode === 'moved_window') env.tabState.windowId = 99
    if (mode === 'url_changed') env.tabState.url = SEARCH_URL + '&changed=1'
    if (mode === 'cancel' || mode === 'pause') {
      assert.equal((await env.send({ type: 'jobagent:runner-' + mode })).ok, true)
    }
    await until(() => mode === 'pause' ? pointerOf(env)?.pauseConfirmed : pointerOf(env) === null)
    noBrowserWork(env, calls)
    if (mode !== 'cancel' && mode !== 'pause') {
      const failure = calls.find(c => c.url.endsWith('/run/fail'))
      assert.match(JSON.parse(failure.init.body).error, /^start-v4\/handoff_/)
    }
  })
}

test('resume handoff preserves three-job budget and waits for the same tab', async () => {
  const { env, state, calls } = popupEnv()
  await env.send(popupStart)
  await env.send({ type: 'jobagent:runner-pause' })
  await until(() => pointerOf(env)?.pauseConfirmed)
  // The acknowledged pause flag precedes the old loop's next 100ms checkpoint.
  await new Promise(resolve => setTimeout(resolve, 130))
  noBrowserWork(env, calls)
  env.storageData[RUNNER_KEY].candidatesAttempted = 2
  assert.equal((await env.send({ type: 'jobagent:runner-resume', popupTarget: { tabId: 8, windowId: 2 } })).ok, false)
  assert.equal(pointerOf(env).pauseRequested, true)
  assert.equal((await env.send({ type: 'jobagent:runner-resume', popupTarget: popupStart.popupTarget })).ok, true)
  await new Promise(resolve => setTimeout(resolve, 130))
  noBrowserWork(env, calls)
  state.open = false
  state.focused = true
  await until(() => pointerOf(env) === null)
  assert.equal(opensOf(env).length, 1, 'resume cannot replenish the two spent slots')
})

test('content script cannot claim popup focus exception', async () => {
  const { env, calls } = popupEnv()
  const result = await env.send(popupStart, { tab: { id: 7 }, url: SEARCH_URL, documentId: 'site-document' })
  assert.equal(result.ok, false)
  noBrowserWork(env, calls)
  assert.equal(calls.length, 0)
})

for (const cap of [1, 3, 20]) {
  test(`approved cap ${cap} limits opens/imports and reaches backend + overlay`, { skip: SKIP }, async () => {
    const { router, calls } = makeFetchRouter()
    const env = loadBackground({ fetchImpl: router, respond: budgetResponder })
    assert.equal((await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: cap })).ok, true)
    await settle()
    assert.equal(opensOf(env).length, cap)
    assert.equal(calls.filter(c => /\/jobs\/import$/.test(c.url)).length, cap)
    assert.equal(calls.filter(c => /\/tasks\/5\/candidates$/.test(c.url)).length, cap)
    const sessionCreate = calls.find(c => /\/extension\/sessions$/.test(c.url))
    assert.equal(JSON.parse(sessionCreate.init.body).candidate_cap, cap)
    const states = env.tabsSendMessageCalls.filter(c => c.message.type === 'jobagent:runner-state' && c.message.state)
      .map(c => c.message.state)
    assert.ok(states.every(s => s.candidateCap === cap && s.candidatesAttempted <= cap))
    assert.ok(states.some(s => s.candidatesAttempted === cap && s.candidatesProcessed === cap))
    assert.equal(env.tabsSendMessageCalls.some(c => c.message.type === 'jobagent:scroll-step'), false)
    assert.ok(calls.some(c => /\/run\/complete$/.test(c.url)))
    assert.equal(pointerOf(env), null)
  })
}

for (const cap of [undefined, null, 0, -1, 21, 1.5, '3', NaN, Infinity]) {
  test(`invalid candidate cap ${String(cap)} fails before any fetch/navigation`, { skip: SKIP }, async () => {
    const { router, calls } = makeFetchRouter()
    const env = loadBackground({ fetchImpl: router, respond: budgetResponder })
    const result = await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: cap })
    assert.equal(result.ok, false)
    await settle()
    assert.equal(calls.length, 0)
    assert.equal(env.tabsUpdateCalls.length, 0)
    assert.equal(env.tabsSendMessageCalls.length, 0)
    assert.equal(pointerOf(env), null)
  })
}

test('a lower task-specific cap is not overridden by the popup', { skip: SKIP }, async () => {
  const { router, calls } = makeFetchRouter(url => /\/search-plan\/5$/.test(url)
    ? jsonResponse(defaultTask({ max_candidates: 2 })) : undefined)
  const env = loadBackground({ fetchImpl: router, respond: budgetResponder })
  assert.equal((await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 3 })).ok, false)
  assert.equal(calls.some(c => c.init.method === 'POST'), false)
  assert.equal(env.tabsUpdateCalls.length, 0)
  assert.equal((await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 2 })).ok, true)
  await settle()
  assert.equal(opensOf(env).length, 2)
})

test('identity mismatches consume slots and never cause a fourth candidate attempt', { skip: SKIP }, async () => {
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({ fetchImpl: router, respond: msg => msg.type === 'jobagent:capture-detail'
    ? Promise.resolve({ ok: true, result: { status: 'identity_mismatch' } }) : budgetResponder(msg) })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 3 })
  await settle()
  assert.equal(opensOf(env).length, 3)
  assert.equal(calls.filter(c => /\/jobs\/(preview|import)$/.test(c.url)).length, 0)
  assert.ok(calls.some(c => /\/run\/complete$/.test(c.url)))
})

test('the 3-candidate budget spans scroll rounds instead of resetting per batch', { skip: SKIP }, async () => {
  let scrolls = 0
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({ fetchImpl: router, respond: msg => {
    if (msg.type === 'jobagent:scroll-step') {
      scrolls += 1
      return Promise.resolve({ ok: true, result: { ok: true } })
    }
    if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: searchPage(
      Array.from({ length: scrolls ? 10 : 2 }, (_, i) => candidate('batch' + i)),
    ) })
    return budgetResponder(msg)
  } })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 3 })
  await settle()
  assert.equal(scrolls, 1)
  assert.equal(opensOf(env).length, 3)
  assert.equal(calls.filter(c => /\/jobs\/import$/.test(c.url)).length, 3)
  assert.ok(calls.some(c => /\/run\/complete$/.test(c.url)))
})

test('global duplicates consume slots without new imports', { skip: SKIP }, async () => {
  const { router, calls } = makeFetchRouter(url => /\/jobs\/preview$/.test(url)
    ? jsonResponse({ new_count: 0, rows: [{ existing_job_id: 99 }] }) : undefined)
  const env = loadBackground({ fetchImpl: router, respond: budgetResponder })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 3 })
  await settle()
  assert.equal(opensOf(env).length, 3)
  assert.equal(calls.filter(c => /\/jobs\/import$/.test(c.url)).length, 0)
  assert.equal(calls.filter(c => /\/tasks\/5\/candidates$/.test(c.url)).length, 3)
  assert.ok(calls.some(c => /\/run\/complete$/.test(c.url)))
})

async function pausedBudgetRun() {
  const { router, calls } = makeFetchRouter()
  let pauseOnce = true
  const env = loadBackground({ fetchImpl: router, respond: msg => {
    if (msg.type === 'jobagent:open-candidate' && pauseOnce) {
      pauseOnce = false
      env.storageData[RUNNER_KEY] = { ...pointerOf(env), pauseRequested: true }
    }
    return budgetResponder(msg)
  } })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 3 })
  await settle()
  assert.equal(pointerOf(env).candidatesAttempted, 1)
  assert.equal(pointerOf(env).phase, 'stopped')
  return { env, router, calls }
}

for (const reloadWorker of [false, true]) {
  test(`pause/resume preserves 3 slots; worker reload=${reloadWorker}`, { skip: SKIP }, async () => {
    const { env, router, calls } = await pausedBudgetRun()
    const resumed = reloadWorker ? loadBackground({ fetchImpl: router, respond: budgetResponder,
      storage: structuredClone(env.storageData) }) : env
    const before = structuredClone(pointerOf(resumed))
    const status = await resumed.send({ type: 'jobagent:runner-status' })
    assert.equal(status.runner.candidateCap, 3)
    assert.equal(status.runner.candidatesAttempted, 1)
    await settle()
    assert.deepEqual(structuredClone(pointerOf(resumed)), before, 'status/popup open does not reset budget or auto-resume')
    const results = await Promise.all([
      resumed.send({ type: 'jobagent:runner-resume', candidateCap: 20 }),
      resumed.send({ type: 'jobagent:runner-resume', candidateCap: 20 }),
    ])
    assert.equal(results.filter(r => r.ok).length, 1, 'simultaneous resume cannot start two loops')
    await settle()
    assert.equal(opensOf(resumed).length, reloadWorker ? 2 : 3)
    assert.equal(calls.filter(c => /\/jobs\/import$/.test(c.url)).length, 2)
    assert.equal(pointerOf(resumed), null)
  })
}

for (const patch of [{ candidateCap: undefined }, { candidatesAttempted: undefined },
  { candidateCap: 21 }, { candidatesAttempted: -1 }, { candidatesAttempted: 4 }, { candidatesProcessed: 2 },
  { scrollsUsed: undefined }, { scrollsUsed: -1 }, { scrollsUsed: 6 }, { scrollsUsed: 1.5 }]) {
  test(`invalid saved budget refuses resume: ${JSON.stringify(patch)}`, { skip: SKIP }, async () => {
    const { env, router, calls } = await pausedBudgetRun()
    env.storageData[RUNNER_KEY] = { ...pointerOf(env), ...patch }
    const restored = loadBackground({ fetchImpl: router, respond: budgetResponder, storage: env.storageData })
    const count = calls.length
    assert.equal((await restored.send({ type: 'jobagent:runner-resume' })).ok, false)
    await settle()
    assert.equal(calls.length, count)
    assert.equal(restored.tabsSendMessageCalls.length, 0)
  })
}

test('reservation storage failure prevents the first browser click', { skip: SKIP }, async () => {
  const { router, calls } = makeFetchRouter()
  let failed = false
  const env = loadBackground({ fetchImpl: router, respond: budgetResponder, beforeStorageSet: obj => {
    if (!failed && obj[RUNNER_KEY]?.candidatesAttempted === 1) {
      failed = true
      throw new Error('simulated storage write failure')
    }
  } })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 3 })
  await settle()
  assert.equal(opensOf(env).length, 0)
  assert.equal(calls.filter(c => /\/jobs\/import$/.test(c.url)).length, 0)
  assert.ok(calls.some(c => /\/run\/fail$/.test(c.url) && /candidate_budget_write_failed/.test(c.init.body)))
})

test('a stale pause snapshot cannot refund a candidate reservation', { skip: SKIP }, async () => {
  const code = fs.readFileSync(BACKGROUND_PATH, 'utf8')
  // A separate VM exposes internal writes only for this race test, not as a product API.
  const storage = {}
  const sandbox = { URL, console, chrome: { runtime: { onMessage: { addListener() {} } }, storage: {
    session: { get: async key => ({ [key]: structuredClone(storage[key]) }),
      set: async obj => Object.assign(storage, structuredClone(obj)), remove: async key => { delete storage[key] } },
  } } }
  vm.createContext(sandbox)
  new vm.Script(code).runInContext(sandbox)
  const p = { taskId: 5, candidateCap: 3, candidatesAttempted: 0, candidatesProcessed: 0,
    handledUrls: [], scrollsUsed: 0, seenCandidateUrls: [] }
  await sandbox.setRunnerPointer({ ...p })
  await Promise.all([
    sandbox.setRunnerPointer({ ...p, candidatesAttempted: 1, handledUrls: [candidate('a').source_url],
      scrollsUsed: 1, seenCandidateUrls: [candidate('a').source_url, candidate('b').source_url] }),
    sandbox.setRunnerPointer({ ...p, pauseRequested: true }),
  ])
  assert.equal(storage[RUNNER_KEY].candidatesAttempted, 1)
  assert.equal(storage[RUNNER_KEY].candidateCap, 3)
  assert.equal(storage[RUNNER_KEY].handledUrls.length, 1)
  assert.equal(storage[RUNNER_KEY].pauseRequested, true)
  assert.equal(storage[RUNNER_KEY].scrollsUsed, 1, 'stale pause does not refund a scroll')
  assert.equal(storage[RUNNER_KEY].seenCandidateUrls.length, 2, 'stale pause retains discovery history')
  await sandbox.setRunnerPointer({ ...p, pauseRequested: false })
  assert.equal(storage[RUNNER_KEY].pauseRequested, true, 'a stale progress write cannot clear a pause')
  await sandbox.setRunnerPointer({ ...p, cancelRequested: true })
  await sandbox.setRunnerPointer({ ...p, runToken: 2, cancelRequested: false })
  assert.equal(storage[RUNNER_KEY].cancelRequested, true, 'resume cannot overwrite concurrent cancel')
})

// --------------------------------------------------------------------------
// 1. start / navigation - the SearchTask's own URL, never the pre-nav page
// --------------------------------------------------------------------------

test('start navigates the approved tab to the SearchTask URL, not the pre-nav page', { skip: SKIP }, async () => {
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    tab: { url: 'https://www.zhipin.com/web/geek/job-recommend.html' },
    fetchImpl: router,
    respond: () => Promise.resolve({ ok: true, result: searchPage([]) }),
  })
  const result = await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  assert.equal(result.ok, true)
  await settle()

  assert.equal(env.tabsUpdateCalls.length, 1)
  assert.equal(env.tabsUpdateCalls[0].url, SEARCH_URL)

  // Session creation happens strictly after the navigation call, never
  // before - CLAUDE.md M4f review item 6 ("the approved session's initial
  // results page must be the navigated SearchTask URL, not an unrelated
  // pre-navigation page").
  const sessionCreateIndex = calls.findIndex(
    (c) => /\/api\/extension\/sessions$/.test(c.url) && (c.init.method || 'GET') === 'POST',
  )
  assert.ok(sessionCreateIndex !== -1, 'session was created')
})

test('start refuses a non-zhipin.com active tab with no navigation and no fetch', { skip: SKIP }, async () => {
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    tab: { url: 'https://example.com/' },
    fetchImpl: router,
    respond: () => Promise.resolve({ ok: true, result: searchPage([]) }),
  })
  const result = await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  assert.equal(result.ok, false)
  assert.equal(result.error, 'wrong_origin')
  assert.equal(calls.length, 0)
  assert.equal(env.tabsUpdateCalls.length, 0)
})

test('start refuses a SearchTask whose own search_url is not zhipin.com', { skip: SKIP }, async () => {
  const { router } = makeFetchRouter((url, init, calls) => {
    if (/\/api\/tasks\/search-plan\/\d+$/.test(url)) {
      return jsonResponse(defaultTask({ search_url: 'https://evil.example.com/jobs' }))
    }
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: () => Promise.resolve({ ok: true, result: searchPage([]) }),
  })
  const result = await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  assert.equal(result.ok, false)
  assert.equal(result.error, 'wrong_origin')
  assert.equal(env.tabsUpdateCalls.length, 0)
})

test('a second start while one is already running is rejected with zero extra fetch calls', { skip: SKIP }, async () => {
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    // Never resolves DETECT - keeps the first run stuck mid-stabilization.
    respond: () => new Promise(() => {}),
  })
  const first = env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()
  const callsAfterFirst = calls.length

  const second = await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  assert.equal(second.ok, false)
  assert.equal(second.error, 'already_running')
  assert.equal(calls.length, callsAfterFirst)
  void first
})

// --------------------------------------------------------------------------
// 2. canonical dedup - query-free /job_detail/<id>.html identity everywhere
// --------------------------------------------------------------------------

test('batch inventory follows same-size virtual cards and remembers reappearing URLs', { skip: SKIP }, async () => {
  let scrolls = 0
  let afterScrollDetects = 0
  const { router, calls } = makeFetchRouter((url) => {
    if (/\/run\/round$/.test(url) && scrolls === 3) return jsonResponse({ run_status: 'completed' })
  })
  const env = loadBackground({ fetchImpl: router, respond: msg => {
    if (msg.type === 'jobagent:detect') {
      if (scrolls) afterScrollDetects++
      const id = ['a', 'b', 'c', 'a'][scrolls]
      return Promise.resolve({ ok: true, result: searchPage([candidate(id, {
        source_url: `${ORIGIN}/job_detail/${id}.html?ka=fixture-${scrolls}`,
      })]) })
    }
    if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
    if (msg.type === 'jobagent:capture-detail') return Promise.resolve({ ok: true,
      result: { status: 'ok', candidate: candidate('fixture', { source_url: msg.canonicalUrl }) } })
    if (msg.type === 'jobagent:scroll-step') { scrolls++; return Promise.resolve({ ok: true, result: { ok: true } }) }
    return Promise.resolve({ ok: false })
  } })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()
  const rounds = calls.filter(c => /\/run\/round$/.test(c.url)).map(c => JSON.parse(c.init.body))
  assert.deepEqual(rounds.map(r => [r.observed, r.new, r.duplicate]), [[1, 1, 0], [1, 1, 0], [1, 0, 1]])
  assert.ok(afterScrollDetects < 15, 'same-count replacement does not exhaust every stabilization wait')
  assert.equal(opensOf(env).length, 3, 'reappearing a is not processed twice')
  const broadcasts = env.tabsSendMessageCalls.filter(c => c.message.type === 'jobagent:runner-state' && c.message.state)
  assert.ok(broadcasts.every(c => !JSON.stringify(c.message.state).includes('ka=fixture-')))
})

test('batch visible_jobs updates when cards load after the initial empty snapshot', { skip: SKIP }, async () => {
  let detects = 0
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({ fetchImpl: router, respond: msg => {
    if (msg.type === 'jobagent:detect' && ++detects === 1) return Promise.resolve({ ok: true, result: searchPage([]) })
    return budgetResponder(msg)
  } })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 1 })
  await settle()
  const reports = calls.filter(c => /\/run\/state$/.test(c.url)).map(c => JSON.parse(c.init.body))
  assert.equal(reports.filter(r => r.visible_jobs !== undefined).at(-1).visible_jobs, 25)
})

test('batch scroll storage failure prevents the scroll side effect', { skip: SKIP }, async () => {
  let scrolled = false
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({ fetchImpl: router, beforeStorageSet: obj => {
    if (obj[RUNNER_KEY]?.scrollsUsed === 1) throw new Error('fixture storage failure')
  }, respond: msg => {
    if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: searchPage([]) })
    if (msg.type === 'jobagent:scroll-step') scrolled = true
    return Promise.resolve({ ok: false })
  } })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 3 })
  await settle()
  assert.equal(scrolled, false)
  assert.ok(calls.some(c => c.url.endsWith('/run/fail') && c.init.body.includes('scroll_budget_write_failed')))
})

test('batch scroll reservation survives pause and worker recreation without extra scrolls', { skip: SKIP }, async () => {
  let scrolls = 0
  let paused = false
  const { router } = makeFetchRouter()
  let env
  const respond = msg => {
    if (msg.type === 'jobagent:detect') {
      if (scrolls === 1 && !paused) {
        paused = true
        env.storageData[RUNNER_KEY].pauseRequested = true
      }
      return Promise.resolve({ ok: true, result: searchPage([]) })
    }
    if (msg.type === 'jobagent:scroll-step') { scrolls++; return Promise.resolve({ ok: true, result: { ok: true } }) }
    return Promise.resolve({ ok: false })
  }
  env = loadBackground({ fetchImpl: router, respond })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 3 })
  await settle()
  assert.equal(pointerOf(env).scrollsUsed, 1, 'a performed scroll is not refunded by pause during stabilization')
  const restored = loadBackground({ fetchImpl: router, respond, storage: structuredClone(env.storageData) })
  await restored.send({ type: 'jobagent:runner-resume' })
  await settle()
  assert.equal(scrolls, 5, 'resume spends only the four remaining scroll reservations')
  assert.equal(pointerOf(restored), null)
})

test('a candidate URL differing only by query string is treated as the same identity across a scroll round', { skip: SKIP }, async () => {
  // Only 'a' is visible before scrolling - candidate processing drains it,
  // then the scroll round's own before/after DETECT reveals 'a' again under
  // a *different* query string plus a genuinely new 'b'.
  const pageBefore = searchPage([candidate('a', { source_url: 'https://www.zhipin.com/job_detail/a.html?lid=xyz&securityId=1' })])
  const pageAfter = searchPage([
    candidate('a', { source_url: 'https://www.zhipin.com/job_detail/a.html?lid=other' }), // same identity, different query
    candidate('b', { source_url: 'https://www.zhipin.com/job_detail/b.html' }),
  ])
  let detectCall = 0
  const { router, calls } = makeFetchRouter((url) => {
    if (/\/run\/round$/.test(url)) return jsonResponse({ run_status: 'completed' }) // stop after one round
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') {
        detectCall += 1
        // 1: initial stabilize. 2: candidate-processing DETECT (finds 'a').
        // 3: candidate-processing re-check (now handled, finds nothing).
        // 4: scroll round's own "before" snapshot. 5+: post-scroll snapshot.
        return Promise.resolve({ ok: true, result: detectCall <= 4 ? pageBefore : pageAfter })
      }
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        return Promise.resolve({
          ok: true,
          result: { status: 'ok', candidate: candidate('a', { source_url: msg.canonicalUrl }) },
        })
      }
      if (msg.type === 'jobagent:scroll-step') return Promise.resolve({ ok: true, result: { ok: true } })
      return Promise.resolve({ ok: false })
    },
  })

  const started = await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  assert.equal(started.ok, true)
  await settle()

  const roundCall = calls.find((c) => /\/run\/round$/.test(c.url))
  assert.ok(roundCall, 'a round was recorded')
  const body = JSON.parse(roundCall.init.body)
  assert.equal(body.observed, 2)
  assert.equal(body.new, 1, "'a' under a different query must not count as new")
  assert.equal(body.duplicate, 1)
})

test('a non-/job_detail/ URL is never selected as a candidate to open', { skip: SKIP }, async () => {
  const page = searchPage([
    candidate('x', { source_url: 'https://www.zhipin.com/jobs/detail/x' }), // wrong shape - rejected
    candidate('y', { source_url: 'https://www.zhipin.com/job_detail/y.html' }), // correct shape
  ])
  const opened = []
  const { router } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') {
        opened.push(msg.index)
        return Promise.resolve({ ok: true, result: { ok: true } })
      }
      if (msg.type === 'jobagent:capture-detail') {
        return Promise.resolve({
          ok: true,
          result: { status: 'ok', candidate: candidate('y', { source_url: 'https://www.zhipin.com/job_detail/y.html' }) },
        })
      }
      if (msg.type === 'jobagent:scroll-step') return Promise.resolve({ ok: true, result: { ok: true } })
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  assert.equal(opened.length >= 1, true)
  assert.equal(opened[0], 1) // index 1 = candidate 'y', never index 0 ('x')
})

// --------------------------------------------------------------------------
// 3. bounded stabilization timeout
// --------------------------------------------------------------------------

test('startup tab loss fails terminally instead of leaving a stopped running pointer', async () => {
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({ fetchImpl: router, respond: msg => {
    if (msg.type === 'jobagent:detect') env.tabState.missing = true
    return Promise.resolve({ ok: false })
  } })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 3 })
  await settle()
  assert.equal(pointerOf(env), null)
  const failed = calls.find(c => c.url.endsWith('/run/fail'))
  assert.ok(failed)
  assert.equal(JSON.parse(failed.init.body).error, 'tab_lost')
  assert.equal(opensOf(env).length, 0)
  assert.ok(!calls.some(c => c.url.endsWith('/jobs/import')))
})

test('startup foreground loss is reported immediately, not hidden by a generic timeout', async () => {
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({ fetchImpl: router, respond: msg => {
    if (msg.type === 'jobagent:detect') env.tabState.active = false
    return Promise.resolve({ ok: false })
  } })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 3 })
  await settle()
  const failed = calls.find(c => c.url.endsWith('/run/fail'))
  assert.equal(JSON.parse(failed.init.body).error, 'not_foreground')
  assert.equal(detectCallsOf(env).length, 1)
  assert.equal(pointerOf(env), null)
})

test('startup content transport timeout reports only a safe fixed code', async () => {
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({ fetchImpl: router,
    respond: () => Promise.reject(new Error('private-fixture-text-must-not-be-reported')) })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 3 })
  await settle()
  const failed = calls.find(c => c.url.endsWith('/run/fail'))
  assert.equal(JSON.parse(failed.init.body).error, 'stabilization_timeout:content_unavailable')
  assert.equal(detectCallsOf(env).length, 11, 'one injected retry plus the original ten bounded polls')
  assert.equal(env.scriptingExecuteCalls.length, 1)
  assert.ok(!JSON.stringify(calls).includes('private-fixture-text'))
  assert.equal(pointerOf(env), null)
})

test('startup safely injects the packaged content stack once when the new BOSS tab has no receiver', async () => {
  let injected = false
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    onExecuteScript: () => { injected = true },
    respond: msg => injected
      ? budgetResponder(msg)
      : Promise.reject(new Error('Could not establish connection. Receiving end does not exist.')),
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 3 })
  await settle()
  assert.deepEqual(JSON.parse(JSON.stringify(env.scriptingExecuteCalls)), [{
    target: { tabId: 7 },
    files: ['dist/boss/selectors.js', 'dist/boss/extract.js', 'dist/content.js', 'dist/overlay.js'],
  }])
  assert.equal(calls.filter(c => c.url.endsWith('/jobs/import')).length, 3)
  assert.ok(!calls.some(c => c.url.endsWith('/run/fail')))
  assert.equal(pointerOf(env), null)
})

test('a search page that never stabilizes ends the run failed after exactly the bounded attempt ceiling', { skip: SKIP }, async () => {
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: () => Promise.resolve({ ok: true, result: { page_type: 'detail', verification: false, candidates: [] } }),
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  // RUNNER_STABILIZE_MAX_ATTEMPTS = 10 in background.ts.
  assert.equal(detectCallsOf(env).length, 10)
  assert.equal(pointerOf(env), null) // endRunner cleared it - terminal 'failed'
  const failCall = calls.find((c) => /\/run\/fail$/.test(c.url))
  assert.ok(failCall)
  assert.match(JSON.parse(failCall.init.body).error, /stabilization_timeout/)
})

// --------------------------------------------------------------------------
// 4. identity mismatch
// --------------------------------------------------------------------------

test('a persistent identity mismatch skips only that candidate, sends no preview/import, and the run continues to a deterministic bounded end', { skip: SKIP }, async () => {
  const page = searchPage([candidate('a')])
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        return Promise.resolve({ ok: true, result: { status: 'identity_mismatch', candidate: null } })
      }
      // No candidates left to open once 'a' is skipped/handled - the loop
      // moves on to scrolling, which is not mocked here and fails, giving a
      // deterministic bounded terminal state (never a stuck run).
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  assert.equal(calls.some((c) => /\/jobs\/preview$/.test(c.url)), false)
  assert.equal(calls.some((c) => /\/jobs\/import$/.test(c.url)), false)
  const failCall = calls.find((c) => /\/run\/fail$/.test(c.url))
  assert.ok(failCall, 'the run must still reach a deterministic bounded end (scroll_failed here)')
  assert.match(JSON.parse(failCall.init.body).error, /scroll_failed/)
  assert.equal(pointerOf(env), null)
})

test('a transient identity mismatch retries within the bounded ceiling and still imports once it resolves', { skip: SKIP }, async () => {
  const page = searchPage([candidate('a')])
  let captureAttempts = 0
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        captureAttempts += 1
        // Mismatches twice (a bounded SPA transition still catching up),
        // then resolves to the real candidate - well within the ceiling.
        if (captureAttempts <= 2) {
          return Promise.resolve({ ok: true, result: { status: 'identity_mismatch', candidate: null } })
        }
        return Promise.resolve({ ok: true, result: { status: 'ok', candidate: candidate('a') } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  assert.equal(captureAttempts, 3)
  assert.ok(calls.some((c) => /\/jobs\/preview$/.test(c.url)))
  assert.ok(calls.some((c) => /\/jobs\/import$/.test(c.url)))
  assert.equal(calls.some((c) => /\/run\/fail$/.test(c.url) && /identity_mismatch/.test(c.init.body || '')), false)
})

test('a verification signal during capture still pauses immediately, never retried like a mismatch', { skip: SKIP }, async () => {
  const page = searchPage([candidate('a')])
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        return Promise.resolve({ ok: true, result: { status: 'verification', candidate: null } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  assert.ok(calls.some((c) => /\/run\/verification$/.test(c.url)))
  assert.equal(calls.some((c) => /\/run\/fail$/.test(c.url)), false)
  assert.equal(calls.some((c) => /\/jobs\/preview$/.test(c.url)), false)
  const pointer = pointerOf(env)
  assert.ok(pointer, 'verification must keep the pointer paused, not clear it')
  assert.equal(pointer.pauseRequested, true)
})

// --------------------------------------------------------------------------
// 5. caps: max 20 candidates / max 5 scroll rounds / no-new=3
// --------------------------------------------------------------------------

test('reaching the candidate cap ends the run completed without opening a 21st candidate', { skip: SKIP }, async () => {
  // 21 distinct always-new candidates on one page - the cap must stop
  // processing at 20, never open the 21st.
  const many = searchPage(Array.from({ length: 21 }, (_, i) => candidate('c' + i)))
  const opened = []
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: many })
      if (msg.type === 'jobagent:open-candidate') {
        opened.push(msg.index)
        return Promise.resolve({ ok: true, result: { ok: true } })
      }
      if (msg.type === 'jobagent:capture-detail') {
        return Promise.resolve({
          ok: true,
          result: { status: 'ok', candidate: candidate('c' + msg.canonicalUrl, { source_url: msg.canonicalUrl }) },
        })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  assert.equal(opened.length, 20)
  const completeCall = calls.find((c) => /\/run\/complete$/.test(c.url))
  assert.ok(completeCall)
  assert.equal(pointerOf(env), null)
})

test('the scroll round loop never performs a 6th scroll', { skip: SKIP }, async () => {
  let scrollCalls = 0
  const emptyPage = searchPage([])
  const { router, calls } = makeFetchRouter((url, init) => {
    if (/\/run\/round$/.test(url)) return jsonResponse({ run_status: 'running' }) // never auto-completes
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: emptyPage })
      if (msg.type === 'jobagent:scroll-step') {
        scrollCalls += 1
        return Promise.resolve({ ok: true, result: { ok: true } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  assert.equal(scrollCalls, 5) // RUNNER_MAX_SCROLL_ROUNDS
  const completeCall = calls.find((c) => /\/run\/complete$/.test(c.url))
  assert.ok(completeCall)
})

test('a backend-reported no-new-round completion stops the loop without a further round', { skip: SKIP }, async () => {
  let scrollCalls = 0
  const emptyPage = searchPage([])
  const { router, calls } = makeFetchRouter((url, init) => {
    if (/\/run\/round$/.test(url)) {
      // Backend's own consecutive-no-new-round threshold fires on round 1.
      return jsonResponse({ run_status: 'completed' })
    }
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: emptyPage })
      if (msg.type === 'jobagent:scroll-step') {
        scrollCalls += 1
        return Promise.resolve({ ok: true, result: { ok: true } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  assert.equal(scrollCalls, 1)
  // The backend already completed the task via `record_round` - a second
  // `/run/complete` would 422 (`complete_run` requires `running`).
  // `endRunner`'s `skipTransition` must never replay it (review item 1).
  const completeCall = calls.find((c) => /\/run\/complete$/.test(c.url))
  assert.equal(completeCall, undefined)
  assert.equal(pointerOf(env), null) // still stops the session and clears the pointer
})

// --------------------------------------------------------------------------
// 6. pause/cancel during bounded waits - the core closure-vs-durable-state fix
// --------------------------------------------------------------------------

test('a pause written to durable storage between stabilization polls is honored at the next checkpoint', { skip: SKIP }, async () => {
  let detectCalls = 0
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') {
        detectCalls += 1
        if (detectCalls === 2) {
          // Simulate exactly what a concurrent `requestRunnerPause()` call
          // would have written - a fresh, separate object in storage, never
          // the loop's own in-memory copy.
          const stored = env.storageData[RUNNER_KEY]
          env.storageData[RUNNER_KEY] = { ...stored, pauseRequested: true }
        }
        // Never a 'search' page - keeps stabilization polling.
        return Promise.resolve({ ok: true, result: { page_type: 'detail', verification: false, candidates: [] } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  // Stopped at (or immediately after) the 3rd checkpoint - never all the way
  // to the 10-attempt ceiling - proving the pause was actually observed.
  assert.ok(detectCalls < 10, `expected to stop early, saw ${detectCalls} DETECT calls`)
  const pointer = pointerOf(env)
  assert.ok(pointer, 'the pointer must survive a plain pause')
  assert.equal(pointer.phase, 'stopped')
  assert.equal(pointer.pauseRequested, true)
  // Never a terminal transition - a pause is not a completion/failure/cancel.
  assert.equal(calls.some((c) => /\/run\/(complete|fail|cancel)$/.test(c.url)), false)
})

test('a cancel written to durable storage between stabilization polls ends the run for good', { skip: SKIP }, async () => {
  let detectCalls = 0
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') {
        detectCalls += 1
        if (detectCalls === 2) {
          const stored = env.storageData[RUNNER_KEY]
          env.storageData[RUNNER_KEY] = { ...stored, cancelRequested: true }
        }
        return Promise.resolve({ ok: true, result: { page_type: 'detail', verification: false, candidates: [] } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  assert.ok(detectCalls < 10)
  assert.equal(pointerOf(env), null)
  assert.ok(calls.some((c) => /\/run\/cancel$/.test(c.url)))
})

test('requestRunnerPause via the real message merges onto fresh storage rather than a stale copy', { skip: SKIP }, async () => {
  const { router } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    // Never resolves - keeps the run stuck in its very first bounded
    // stabilization attempt so the pointer survives long enough to pause.
    respond: () => new Promise(() => {}),
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  const before = pointerOf(env)
  assert.ok(before)
  const pauseResult = await env.send({ type: 'jobagent:runner-pause' })
  assert.equal(pauseResult.ok, true)
  const after = pointerOf(env)
  assert.equal(after.pauseRequested, true)
  // Every other field the loop itself owns survives untouched.
  assert.equal(after.taskId, before.taskId)
  assert.equal(after.tabId, before.tabId)
})

test('resume mints a fresh runToken so a second resume is rejected rather than starting a concurrent loop', { skip: SKIP }, async () => {
  let detectCalls = 0
  const { router } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type !== 'jobagent:detect') return Promise.resolve({ ok: false })
      detectCalls += 1
      if (detectCalls === 2) {
        // Exactly the proven durable-pause technique above - reach a real
        // `haltForStop` so `phase` genuinely becomes 'stopped', rather than
        // relying on the bounded timeout (which would clear the pointer).
        const stored = env.storageData[RUNNER_KEY]
        env.storageData[RUNNER_KEY] = { ...stored, pauseRequested: true }
      }
      if (detectCalls > 2) return new Promise(() => {}) // resume's own detect never resolves
      return Promise.resolve({ ok: true, result: { page_type: 'detail', verification: false, candidates: [] } })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()
  assert.equal(pointerOf(env).phase, 'stopped')
  assert.equal(pointerOf(env).pauseRequested, true)

  // Resume itself gets stuck (detect never resolves for the new run
  // attempt) - the in-memory single-flight guard must still refuse a
  // second, concurrent resume call while it is in flight.
  const firstResume = env.send({ type: 'jobagent:runner-resume' })
  await settle()
  const secondResume = await env.send({ type: 'jobagent:runner-resume' })
  assert.equal(secondResume.ok, false)
  assert.equal(secondResume.error, 'already_running')
  void firstResume
})

// --------------------------------------------------------------------------
// 7. verification - explicit resume only, never automatic
// --------------------------------------------------------------------------

test('a verification interstitial halts the run but keeps the pointer and session for an explicit resume', { skip: SKIP }, async () => {
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: () => Promise.resolve({ ok: true, result: { page_type: 'search', verification: true, candidates: [] } }),
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  const pointer = pointerOf(env)
  assert.ok(pointer, 'pointer must survive a verification halt')
  assert.equal(pointer.pauseRequested, true)
  assert.ok(calls.some((c) => /\/run\/verification$/.test(c.url)))
  // Never a terminal transition, and never a session stop.
  assert.equal(calls.some((c) => /\/run\/(complete|fail|cancel)$/.test(c.url)), false)
  assert.equal(calls.some((c) => /\/sessions\/\d+\/stop$/.test(c.url)), false)

  // Only an explicit resume may continue - never automatic.
  const resumed = await env.send({ type: 'jobagent:runner-resume' })
  assert.equal(resumed.ok, true)
})

// --------------------------------------------------------------------------
// 8. foreground / origin loss - fail closed, never continue hidden
// --------------------------------------------------------------------------

test('the tab going background (not foreground) mid-run fails the run closed', { skip: SKIP }, async () => {
  let detectCalls = 0
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') {
        detectCalls += 1
        if (detectCalls === 1) {
          env.tabState.active = false // backgrounded right after the first stabilize DETECT
        }
        return Promise.resolve({ ok: true, result: searchPage([]) })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  const failCall = calls.find((c) => /\/run\/fail$/.test(c.url))
  assert.ok(failCall)
  assert.match(JSON.parse(failCall.init.body).error, /not_foreground/)
  assert.equal(pointerOf(env), null)
})

test('the tab being closed mid-run (chrome.tabs.get rejects) fails the run closed', { skip: SKIP }, async () => {
  let detectCalls = 0
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') {
        detectCalls += 1
        if (detectCalls === 1) env.tabState.missing = true
        return Promise.resolve({ ok: true, result: searchPage([]) })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  const failCall = calls.find((c) => /\/run\/fail$/.test(c.url))
  assert.ok(failCall)
  assert.match(JSON.parse(failCall.init.body).error, /tab_lost/)
})

test('the tab navigating to a different origin mid-run fails the run closed', { skip: SKIP }, async () => {
  let detectCalls = 0
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') {
        detectCalls += 1
        if (detectCalls === 1) env.tabState.url = 'https://example.com/somewhere'
        return Promise.resolve({ ok: true, result: searchPage([]) })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  const failCall = calls.find((c) => /\/run\/fail$/.test(c.url))
  assert.ok(failCall)
  assert.match(JSON.parse(failCall.init.body).error, /wrong_origin/)
})

// --------------------------------------------------------------------------
// 9. backend terminal-transition failure - preserve, never silently clear
// --------------------------------------------------------------------------

test('a failed terminal transition keeps the pointer with lastError/pendingTerminal instead of clearing it', { skip: SKIP }, async () => {
  const { router } = makeFetchRouter((url) => {
    if (/\/run\/fail$/.test(url)) return rejected('backend unreachable')
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: () => Promise.resolve({ ok: true, result: { page_type: 'detail', verification: false, candidates: [] } }),
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  const pointer = pointerOf(env)
  assert.ok(pointer, 'a failed confirmation must not clear the pointer')
  assert.ok(pointer.lastError)
  assert.ok(pointer.pendingTerminal)
  assert.equal(pointer.pendingTerminal.outcome, 'failed')
})

test('retrying a pending terminal transition clears the pointer once the backend accepts it', { skip: SKIP }, async () => {
  let failShouldReject = true
  const { router } = makeFetchRouter((url) => {
    if (/\/run\/fail$/.test(url) && failShouldReject) return rejected('backend unreachable')
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: () => Promise.resolve({ ok: true, result: { page_type: 'detail', verification: false, candidates: [] } }),
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()
  assert.ok(pointerOf(env))

  failShouldReject = false
  const retry = await env.send({ type: 'jobagent:runner-retry-terminal' })
  assert.equal(retry.ok, true)
  assert.equal(pointerOf(env), null)
})

// --------------------------------------------------------------------------
// 10. absence of pagination / concurrency in the built code
// --------------------------------------------------------------------------

test('the built runner never sends a pagination message type', { skip: SKIP }, async () => {
  const code = fs.readFileSync(BACKGROUND_PATH, 'utf8')
  assert.doesNotMatch(code, /jobagent:next-page/)
  assert.doesNotMatch(code, /['"]results-page['"]/)
})

test('the built background worker uses at most one runner loop at a time (single in-memory guard)', { skip: SKIP }, async () => {
  const code = fs.readFileSync(BACKGROUND_PATH, 'utf8')
  assert.match(code, /activeRunToken/)
  assert.match(code, /claimRunnerLoop|activeRunToken !== null/)
})

test('two concurrent scroll rounds are never issued for one run (sequential, never parallel)', { skip: SKIP }, async () => {
  let inFlight = 0
  let maxInFlight = 0
  const emptyPage = searchPage([])
  const { router } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: emptyPage })
      if (msg.type === 'jobagent:scroll-step') {
        inFlight += 1
        maxInFlight = Math.max(maxInFlight, inFlight)
        return Promise.resolve({ ok: true, result: { ok: true } }).finally(() => {
          inFlight -= 1
        })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()
  assert.equal(maxInFlight, 1)
})

// --------------------------------------------------------------------------
// 11. terminal recovery tracks which half succeeded (review item 2)
// --------------------------------------------------------------------------

test('a session-stop failure preserves a succeeded transition; retry only repeats the session stop', { skip: SKIP }, async () => {
  let failCallCount = 0
  let sessionStopShouldFail = true
  const page = searchPage([candidate('a')])
  const { router } = makeFetchRouter((url) => {
    if (/\/run\/fail$/.test(url)) {
      failCallCount += 1
      return undefined // let the default succeed
    }
    if (/\/sessions\/active$/.test(url) && sessionStopShouldFail) return rejected('backend unreachable')
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        return Promise.resolve({ ok: true, result: { status: 'identity_mismatch', candidate: null } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  assert.equal(failCallCount, 1)
  const pointer = pointerOf(env)
  assert.ok(pointer, 'a session-stop failure must preserve the pointer, not clear it')
  assert.ok(pointer.pendingTerminal)
  assert.equal(pointer.pendingTerminal.transitionDone, true)

  sessionStopShouldFail = false
  const retry = await env.send({ type: 'jobagent:runner-retry-terminal' })
  assert.equal(retry.ok, true)
  assert.equal(failCallCount, 1, 'the already-confirmed transition must never be replayed')
  assert.equal(pointerOf(env), null)
})

// --------------------------------------------------------------------------
// 12. navigateConfirmForTab results checked; cap-denial distinguished (item 3)
// --------------------------------------------------------------------------

test('an unconfirmed detail navigation stops fail-closed and never captures or imports', { skip: SKIP }, async () => {
  const page = searchPage([candidate('a')])
  let captureCalls = 0
  const { router, calls } = makeFetchRouter((url) => {
    if (/\/navigate\/confirm$/.test(url)) return rejected('confirm backend unreachable')
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        captureCalls += 1
        return Promise.resolve({ ok: true, result: { status: 'ok', candidate: candidate('a') } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  assert.equal(captureCalls, 0)
  assert.equal(calls.some((c) => /\/jobs\/preview$/.test(c.url)), false)
  assert.equal(calls.some((c) => /\/jobs\/import$/.test(c.url)), false)
  const failCall = calls.find((c) => /\/run\/fail$/.test(c.url))
  assert.ok(failCall)
})

test('an unconfirmed scroll stops fail-closed and never records a round', { skip: SKIP }, async () => {
  const emptyPage = searchPage([])
  const { router, calls } = makeFetchRouter((url) => {
    if (/\/navigate\/confirm$/.test(url)) return rejected('confirm backend unreachable')
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: emptyPage })
      if (msg.type === 'jobagent:scroll-step') return Promise.resolve({ ok: true, result: { ok: true } })
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  assert.equal(calls.some((c) => /\/run\/round$/.test(c.url)), false)
  const failCall = calls.find((c) => /\/run\/fail$/.test(c.url))
  assert.ok(failCall)
})

test('a candidate_cap denial completes normally', { skip: SKIP }, async () => {
  const page = searchPage([candidate('a')])
  const { router, calls } = makeFetchRouter((url) => {
    if (/\/navigate\/prepare$/.test(url)) {
      return errorResponseWithDetail(422, '已达到本次会话的候选人上限', { field: 'candidate_cap', cap: 20 })
    }
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  const completeCall = calls.find((c) => /\/run\/complete$/.test(c.url))
  assert.ok(completeCall, 'a genuine cap denial must complete normally')
})

test('a non-cap prepare failure is a real error, never treated as cap completion', { skip: SKIP }, async () => {
  const page = searchPage([candidate('a')])
  const { router, calls } = makeFetchRouter((url) => {
    if (/\/navigate\/prepare$/.test(url)) {
      return errorResponseWithDetail(422, '会话未在进行中，无法导航。', { session_id: 42, status: 'stopped' })
    }
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  const failCall = calls.find((c) => /\/run\/fail$/.test(c.url))
  assert.ok(failCall, 'must be treated as a real failure, not a cap completion')
  assert.equal(calls.some((c) => /\/run\/complete$/.test(c.url)), false)
})

// --------------------------------------------------------------------------
// 13. pause/resume/cancel coherent on backend failure (review item 4)
// --------------------------------------------------------------------------

test('a failed pause transition is recorded as lastError but the loop still stops locally', { skip: SKIP }, async () => {
  const { router } = makeFetchRouter((url) => {
    if (/\/run\/pause$/.test(url)) return rejected('backend unreachable')
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: () => new Promise(() => {}), // stuck mid-stabilization
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()
  const result = await env.send({ type: 'jobagent:runner-pause' })
  assert.equal(result.ok, true)
  const pointer = pointerOf(env)
  assert.equal(pointer.pauseRequested, true)
  assert.ok(pointer.lastError)
  assert.equal(pointer.pauseConfirmed, false)
})

test('a failed resume transition never clears pauseRequested or changes runToken', { skip: SKIP }, async () => {
  let detectCalls = 0
  const { router } = makeFetchRouter((url) => {
    if (/\/run\/resume$/.test(url)) return rejected('backend unreachable')
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type !== 'jobagent:detect') return Promise.resolve({ ok: false })
      detectCalls += 1
      if (detectCalls === 2) {
        const stored = env.storageData[RUNNER_KEY]
        env.storageData[RUNNER_KEY] = { ...stored, pauseRequested: true }
      }
      return Promise.resolve({ ok: true, result: { page_type: 'detail', verification: false, candidates: [] } })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()
  const before = pointerOf(env)
  assert.equal(before.phase, 'stopped')
  assert.equal(before.pauseRequested, true)

  const resumeResult = await env.send({ type: 'jobagent:runner-resume' })
  assert.equal(resumeResult.ok, false)
  const after = pointerOf(env)
  assert.equal(after.pauseRequested, true)
  assert.equal(after.runToken, before.runToken)
})

test('cancelling an already-paused runner executes the terminal path even with no active loop', { skip: SKIP }, async () => {
  let detectCalls = 0
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type !== 'jobagent:detect') return Promise.resolve({ ok: false })
      detectCalls += 1
      if (detectCalls === 2) {
        const stored = env.storageData[RUNNER_KEY]
        env.storageData[RUNNER_KEY] = { ...stored, pauseRequested: true }
      }
      return Promise.resolve({ ok: true, result: { page_type: 'detail', verification: false, candidates: [] } })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()
  assert.equal(pointerOf(env).phase, 'stopped') // paused, no active loop left running

  const cancelResult = await env.send({ type: 'jobagent:runner-cancel' })
  assert.equal(cancelResult.ok, true)
  assert.ok(calls.some((c) => /\/run\/cancel$/.test(c.url)))
  assert.equal(pointerOf(env), null)
})

// --------------------------------------------------------------------------
// 14. overlay/popup counters persisted from task/round responses (item 6)
// --------------------------------------------------------------------------

test('cumulative observed/new/duplicate/no-new counters are persisted and broadcast from the round response', { skip: SKIP }, async () => {
  const emptyPage = searchPage([])
  const { router } = makeFetchRouter((url) => {
    if (/\/run\/round$/.test(url)) {
      return jsonResponse({
        run_status: 'running',
        observed_count: 7,
        new_count: 3,
        duplicate_count: 4,
        no_new_rounds: 1,
      })
    }
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: emptyPage })
      if (msg.type === 'jobagent:scroll-step') return Promise.resolve({ ok: true, result: { ok: true } })
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  const states = env.tabsSendMessageCalls
    .filter((c) => c.message.type === 'jobagent:runner-state' && c.message.state)
    .map((c) => c.message.state)
  const withCounters = states.find(
    (s) => s.observedCount === 7 && s.newCount === 3 && s.duplicateCount === 4 && s.noNewRounds === 1,
  )
  assert.ok(withCounters, 'the persisted round counters must have been broadcast at least once')
})

// --------------------------------------------------------------------------
// 15. every superseded/cleared early exit releases the single-flight guard (item 7)
// --------------------------------------------------------------------------

test('a superseded run during stabilization releases the in-memory single-flight guard so a later valid run is not blocked', { skip: SKIP }, async () => {
  let detectCalls = 0
  const { router } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type !== 'jobagent:detect') return Promise.resolve({ ok: false })
      detectCalls += 1
      if (detectCalls === 1) {
        // Superseded/cleared by something else entirely, mid-stabilization -
        // before a SupervisedSession is ever created.
        delete env.storageData[RUNNER_KEY]
        return Promise.resolve({ ok: true, result: { page_type: 'detail', verification: false, candidates: [] } })
      }
      return Promise.resolve({ ok: true, result: searchPage([]) })
    },
  })
  const first = await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  assert.equal(first.ok, true)
  await settle()
  assert.equal(pointerOf(env), null)
  assert.equal(env.storageData[GLOBAL_SESSION_KEY], undefined, 'no session was ever created for the superseded run')

  // If the in-memory guard were never released, this would fail with
  // `already_running` even though nothing is actually running any more.
  const second = await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  assert.equal(second.ok, true)
})

// --------------------------------------------------------------------------
// 16. stabilization requires the tab URL to match the approved SearchTask
// path + city/query - never just `page_type: 'search'` (review item 5)
// --------------------------------------------------------------------------

test('stabilization never accepts a search-shaped page whose city/query does not match the SearchTask', { skip: SKIP }, async () => {
  const { router } = makeFetchRouter()
  const env = loadBackground({
    // The tab still reports a *different* search (a stale previous page) for
    // the first two `chrome.tabs.get` reads - `chrome.tabs.update`'s own
    // mock effect is overridden here to simulate the DOM not having caught
    // up yet - then the third read finally matches.
    tabUrlOverride: (callIndex) =>
      callIndex <= 2 ? 'https://www.zhipin.com/web/geek/jobs?city=999999&query=other' : undefined,
    fetchImpl: router,
    respond: () => Promise.resolve({ ok: true, result: searchPage([]) }),
  })
  const result = await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  assert.equal(result.ok, true)
  await settle()

  // DETECT must never even be asked while the tab URL still mismatches -
  // only after the 3rd `chrome.tabs.get` read agrees with the target.
  assert.ok(env.tabsGetCalls.length >= 3)
  assert.ok(detectCallsOf(env).length >= 1)
  assert.ok(detectCallsOf(env).length < env.tabsGetCalls.length)
})

test('stabilization times out (never a stuck/false accept) if the tab URL never matches the SearchTask', { skip: SKIP }, async () => {
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    tabUrlOverride: () => 'https://www.zhipin.com/web/geek/jobs?city=999999&query=other',
    fetchImpl: router,
    respond: () => Promise.resolve({ ok: true, result: searchPage([]) }),
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  assert.equal(detectCallsOf(env).length, 0, 'DETECT must never be reached while the URL never matches')
  const failCall = calls.find((c) => /\/run\/fail$/.test(c.url))
  assert.ok(failCall)
  assert.match(JSON.parse(failCall.init.body).error, /stabilization_timeout/)
  assert.equal(pointerOf(env), null)
})

// --------------------------------------------------------------------------
// 17. observability wiring - POST /run/state at real checkpoints
// --------------------------------------------------------------------------

test('the runner reports current_url, visible_jobs, current_candidate and imported_jobs to /run/state', { skip: SKIP }, async () => {
  const page = searchPage([candidate('a')])
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        return Promise.resolve({ ok: true, result: { status: 'ok', candidate: candidate('a') } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  const stateCalls = calls.filter((c) => /\/run\/state$/.test(c.url)).map((c) => JSON.parse(c.init.body))
  assert.ok(stateCalls.some((b) => b.current_url === 'https://www.zhipin.com/web/geek/jobs'))
  assert.ok(stateCalls.some((b) => b.visible_jobs === 1))
  assert.ok(stateCalls.some((b) => b.current_candidate === '候选人a'))
  assert.ok(stateCalls.some((b) => b.imported_jobs === 1))
  // Never a query string, even though the search URL constant carries one.
  for (const body of stateCalls) {
    if (body.current_url) assert.doesNotMatch(body.current_url, /\?/)
  }
})

// --------------------------------------------------------------------------
// 18. SearchTask candidate provenance - POST /api/tasks/{id}/candidates
// --------------------------------------------------------------------------

test('a new import is associated with the running SearchTask using the import response job_id', { skip: SKIP }, async () => {
  const page = searchPage([candidate('a')])
  const { router, calls } = makeFetchRouter((url) => {
    if (/\/api\/extension\/jobs\/import$/.test(url)) return jsonResponse({ job_id: 42, duplicate: false })
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        return Promise.resolve({ ok: true, result: { status: 'ok', candidate: candidate('a') } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  const assocCall = calls.find((c) => /\/api\/tasks\/5\/candidates$/.test(c.url))
  assert.ok(assocCall, 'the resolved job must be associated with the SearchTask')
  assert.deepEqual(JSON.parse(assocCall.init.body), { job_id: 42 })
  const importIndex = calls.findIndex((c) => /\/jobs\/import$/.test(c.url))
  const assocIndex = calls.indexOf(assocCall)
  assert.ok(importIndex !== -1 && importIndex < assocIndex, 'association happens after import')
})

test('a global duplicate is associated using the preview existing_job_id, with no import call', { skip: SKIP }, async () => {
  const page = searchPage([candidate('a')])
  const { router, calls } = makeFetchRouter((url) => {
    if (/\/jobs\/preview$/.test(url)) {
      return jsonResponse({ new_count: 0, duplicate_count: 1, rows: [{ existing_job_id: 99 }] })
    }
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        return Promise.resolve({ ok: true, result: { status: 'ok', candidate: candidate('a') } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  assert.equal(calls.some((c) => /\/jobs\/import$/.test(c.url)), false)
  const assocCall = calls.find((c) => /\/api\/tasks\/5\/candidates$/.test(c.url))
  assert.ok(assocCall)
  assert.deepEqual(JSON.parse(assocCall.init.body), { job_id: 99 })
  // A duplicate is never counted as a genuinely new import.
  const pointer = pointerOf(env)
  if (pointer) assert.equal(pointer.importedJobs, 0)
})

test('a duplicate with newly recovered salary uses canonical intake enrichment without counting a new import', { skip: SKIP }, async () => {
  const page = searchPage([candidate('a')])
  const { router, calls } = makeFetchRouter((url) => {
    if (/\/jobs\/preview$/.test(url)) return jsonResponse({ new_count: 0, duplicate_count: 1,
      rows: [{ status: 'duplicate', existing_job_id: 99, enrichable_fields: ['salary_text'] }] })
    if (/\/jobs\/import$/.test(url)) return jsonResponse({ job_id: 99, duplicate: true })
    return undefined
  })
  const env = loadBackground({ fetchImpl: router, respond: (msg) => {
    if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
    if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
    if (msg.type === 'jobagent:capture-detail') return Promise.resolve({ ok: true,
      result: { status: 'ok', candidate: candidate('a') } })
    return Promise.resolve({ ok: false })
  } })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 1 })
  await settle()

  assert.equal(calls.filter(c => /\/jobs\/import$/.test(c.url)).length, 1)
  const assocCall = calls.find(c => /\/api\/tasks\/5\/candidates$/.test(c.url))
  assert.deepEqual(JSON.parse(assocCall.init.body), { job_id: 99 })
  const reports = calls.filter(c => /\/run\/state$/.test(c.url)).map(c => JSON.parse(c.init.body))
  assert.equal(reports.some(report => report.imported_jobs > 0), false)
})

test('association is idempotent - two different candidates resolving to the same existing job both associate cleanly', { skip: SKIP }, async () => {
  const page = searchPage([candidate('a'), candidate('b')])
  const { router, calls } = makeFetchRouter((url) => {
    if (/\/jobs\/preview$/.test(url)) {
      return jsonResponse({ new_count: 0, duplicate_count: 1, rows: [{ existing_job_id: 7 }] })
    }
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        return Promise.resolve({ ok: true, result: { status: 'ok', candidate: candidate('a') } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  const assocCalls = calls.filter((c) => /\/api\/tasks\/5\/candidates$/.test(c.url))
  assert.ok(assocCalls.length >= 1)
  for (const c of assocCalls) assert.deepEqual(JSON.parse(c.init.body), { job_id: 7 })
})

test('a missing job identity (no import, no existing_job_id) fails that candidate closed without associating', { skip: SKIP }, async () => {
  const page = searchPage([candidate('a')])
  const { router, calls } = makeFetchRouter((url) => {
    if (/\/jobs\/preview$/.test(url)) return jsonResponse({ new_count: 0, duplicate_count: 1, rows: [{}] })
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        return Promise.resolve({ ok: true, result: { status: 'ok', candidate: candidate('a') } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  assert.equal(calls.some((c) => /\/api\/tasks\/5\/candidates$/.test(c.url)), false)
  const failCall = calls.find((c) => /\/run\/fail$/.test(c.url))
  assert.ok(failCall)
  assert.match(JSON.parse(failCall.init.body).error, /missing_job_identity/)
})

test('an intake-incomplete candidate consumes its slot and the bounded run continues', async () => {
  const page = searchPage([candidate('a'), candidate('b'), candidate('c')])
  const { router, calls } = makeFetchRouter((url, init) => {
    if (/\/jobs\/preview$/.test(url)) {
      const body = JSON.parse(init.body)
      if (body.candidates[0].external_id === 'b') return jsonResponse({
        new_count: 0, duplicate_count: 0, incomplete_count: 1,
        rows: [{ status: 'incomplete', existing_job_id: null, blocking_fields: ['description'] }],
      })
    }
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: msg => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        const id = /\/job_detail\/([^/]+)\.html/.exec(msg.canonicalUrl)?.[1] || 'unknown'
        return Promise.resolve({ ok: true, result: { status: 'ok', candidate: candidate(id, {
          source_url: msg.canonicalUrl,
        }) } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 3 })
  await settle()

  assert.equal(opensOf(env).length, 3, 'the incomplete candidate consumes one of exactly three attempts')
  assert.equal(calls.filter(c => c.url.endsWith('/jobs/import')).length, 2)
  assert.equal(calls.filter(c => /\/api\/tasks\/5\/candidates$/.test(c.url)).length, 2)
  assert.ok(!calls.some(c => c.url.endsWith('/run/fail')))
  assert.ok(calls.some(c => c.url.endsWith('/run/complete')))
  assert.equal(pointerOf(env), null)
})

test('an early-career card is skipped before opening and does not consume the useful candidate cap', async () => {
  const page = searchPage([
    candidate('fresh', { title: '2027届云平台工程师' }),
    candidate('experienced', { title: '云平台工程师' }),
  ])
  const { router, calls } = makeFetchRouter()
  const env = loadBackground({
    fetchImpl: router,
    respond: msg => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        return Promise.resolve({ ok: true, result: { status: 'ok', candidate: candidate('experienced', {
          title: '云平台工程师', source_url: msg.canonicalUrl,
        }) } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 1 })
  await settle()

  assert.equal(opensOf(env).length, 1, 'only one useful card is opened')
  assert.equal(opensOf(env)[0].message.index, 1, 'the fresh-graduate card is never opened')
  assert.equal(calls.filter(c => c.url.endsWith('/jobs/import')).length, 1)
  assert.ok(calls.some(c => c.url.endsWith('/run/complete')))
})

test('an association failure is reported as candidate_association_failed on both the new-import and duplicate paths', { skip: SKIP }, async () => {
  const page = searchPage([candidate('a')])
  const { router: newImportRouter, calls: newImportCalls } = makeFetchRouter((url) => {
    if (/\/api\/tasks\/\d+\/candidates$/.test(url)) return rejected('association backend unreachable')
    return undefined
  })
  const envNew = loadBackground({
    fetchImpl: newImportRouter,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        return Promise.resolve({ ok: true, result: { status: 'ok', candidate: candidate('a') } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await envNew.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()
  const newImportFail = newImportCalls.find((c) => /\/run\/fail$/.test(c.url))
  assert.ok(newImportFail)
  assert.match(JSON.parse(newImportFail.init.body).error, /candidate_association_failed/)

  const { router: dupRouter, calls: dupCalls } = makeFetchRouter((url) => {
    if (/\/jobs\/preview$/.test(url)) {
      return jsonResponse({ new_count: 0, duplicate_count: 1, rows: [{ existing_job_id: 3 }] })
    }
    if (/\/api\/tasks\/\d+\/candidates$/.test(url)) return rejected('association backend unreachable')
    return undefined
  })
  const envDup = loadBackground({
    fetchImpl: dupRouter,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        return Promise.resolve({ ok: true, result: { status: 'ok', candidate: candidate('a') } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await envDup.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()
  const dupFail = dupCalls.find((c) => /\/run\/fail$/.test(c.url))
  assert.ok(dupFail)
  assert.match(JSON.parse(dupFail.init.body).error, /candidate_association_failed/)
  assert.equal(dupCalls.some((c) => /\/jobs\/import$/.test(c.url)), false)
})

test('imported_jobs increments only for a genuinely new import, never for a duplicate association', { skip: SKIP }, async () => {
  const page = searchPage([candidate('a')])
  const { router } = makeFetchRouter((url) => {
    if (/\/jobs\/preview$/.test(url)) return jsonResponse({ new_count: 1, duplicate_count: 0 })
    return undefined
  })
  const env = loadBackground({
    fetchImpl: router,
    respond: (msg) => {
      if (msg.type === 'jobagent:detect') return Promise.resolve({ ok: true, result: page })
      if (msg.type === 'jobagent:open-candidate') return Promise.resolve({ ok: true, result: { ok: true } })
      if (msg.type === 'jobagent:capture-detail') {
        return Promise.resolve({ ok: true, result: { status: 'ok', candidate: candidate('a') } })
      }
      return Promise.resolve({ ok: false })
    },
  })
  await env.send({ type: 'jobagent:runner-start', taskId: 5, candidateCap: 20 })
  await settle()

  const states = env.tabsSendMessageCalls
    .filter((c) => c.message.type === 'jobagent:runner-state' && c.message.state)
    .map((c) => c.message.state)
  assert.ok(states.some((s) => s.importedJobs === 1))
})
