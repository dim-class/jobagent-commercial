'use strict'

/**
 * M4c ("向下滚动") behavior - Node built-ins only (`node:test`, `assert`,
 * `vm`). No browser, no Playwright/CDP, no network, no dependencies.
 *
 * Real logged-in Chrome verification established that BOSS's
 * `/web/geek/jobs` results are one continuous/infinite scroll list with no
 * pagination control - a "下一页" action was implemented and tested here,
 * but always failed with `no_control` live and ended an otherwise-valid
 * session. It has been removed from `overlay.ts`; this file now only covers
 * "向下滚动", the sole remaining M4c action.
 *
 * Loads the real built `dist/overlay.js` into a `vm` sandbox with a minimal
 * DOM mock and a scriptable `chrome.runtime.sendMessage`/`BossContentScript`
 * that records every `jobagent:scroll-step` call. Same harness shape as
 * `m4b-capture-behavior.test.cjs`.
 *
 * Proves: one human click causes at most one scroll primitive call and one
 * prepare/confirm round-trip; the action refuses outright (no fetch, no DOM
 * action) while a candidate is pending capture or while any other step is
 * already in flight; the per-page scroll cap is read from the session,
 * never guessed client-side; verification/wrong-page/prepare/perform/confirm
 * failures each end the session without retry; no "下一页" control exists
 * anywhere in the rendered bar; and no timer, poll or MutationObserver
 * exists in the built code.
 */

const { test } = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const path = require('node:path')

const OVERLAY_PATH = path.join(__dirname, '..', 'dist', 'overlay.js')
const CONTENT_PATH = path.join(__dirname, '..', 'dist', 'content.js')
const BUILT = fs.existsSync(OVERLAY_PATH) && fs.existsSync(CONTENT_PATH)
const SKIP = BUILT ? false : "extension not built - run 'npm run build' in extension/"

const PAGE_URL = 'https://www.zhipin.com/web/geek/jobs?query=x&city=101010100'

function session(overrides) {
  return {
    id: 42,
    status: 'running',
    page_cap: 3,
    candidate_cap: 20,
    scroll_cap: 5,
    tab_origin: 'https://www.zhipin.com',
    pages_visited: 1,
    candidates_extracted: 0,
    scrolls_used: 0,
    approved_criteria: { task_name: '云计算运维-北京' },
    ...overrides,
  }
}

const SEARCH_PAGE = {
  page_type: 'search',
  verification: false,
  candidates: [{ title: '云计算工程师', source_url: 'https://www.zhipin.com/job_detail/abc.html' }],
}

/** Builds a search-page DETECT result from a list of canonical URLs, each
 * turned into a minimal candidate (title/source_url only - the inventory
 * logic under test only ever reads `source_url`). */
function pageOf(urls) {
  return {
    page_type: 'search',
    verification: false,
    candidates: urls.map((source_url, i) => ({ title: `候选人${i + 1}`, source_url })),
  }
}

const URL_A = 'https://www.zhipin.com/job_detail/a.html'
const URL_B = 'https://www.zhipin.com/job_detail/b.html'
const URL_C = 'https://www.zhipin.com/job_detail/c.html'

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

function loadOverlay({
  detectResult,
  detectResults,
  openResult,
  captureResults,
  scrollResults,
  backendResponses,
}) {
  const elementsById = {}
  const sentMessages = []
  const openCalls = []
  const captureCalls = []
  const scrollCalls = []
  const listeners = []
  const remainingCaptures = (captureResults || []).slice()
  const remainingScrolls = (scrollResults || []).slice()
  // `detectResults` is a queue consumed one item per DETECT call (the scroll
  // step calls DETECT twice - once before scrolling, once after) - once
  // exhausted, the last item repeats, so a test that only cares about the
  // first call (or passes the single-value `detectResult`) still works.
  const remainingDetects = (detectResults || (detectResult ? [detectResult] : [])).slice()
  let lastDetect = detectResult

  function registerIfHasId(el) {
    if (el && el.id) elementsById[el.id] = el
  }
  const documentElement = {
    appendChild(el) {
      registerIfHasId(el)
    },
  }
  const doc = {
    location: { protocol: 'https:', host: 'www.zhipin.com', href: PAGE_URL },
    referrer: '',
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
      SCROLL_STEP: 'jobagent:scroll-step',
      NEXT_PAGE: 'jobagent:next-page',
      handle(msg) {
        if (msg.type === 'jobagent:detect') {
          if (remainingDetects.length) lastDetect = remainingDetects.shift()
          // `null` in the queue means "this DETECT call fails" (`ok: false`).
          if (lastDetect === undefined || lastDetect === null) return { ok: false }
          return { ok: true, result: lastDetect }
        }
        if (msg.type === 'jobagent:open-candidate') {
          openCalls.push(msg.index)
          return { ok: true, result: openResult }
        }
        if (msg.type === 'jobagent:capture-detail') {
          captureCalls.push(msg.canonicalUrl)
          const result = remainingCaptures.length
            ? remainingCaptures.shift()
            : { status: 'not_loaded', candidate: null }
          return { ok: true, result }
        }
        if (msg.type === 'jobagent:scroll-step') {
          scrollCalls.push(true)
          const result = remainingScrolls.length
            ? remainingScrolls.shift()
            : { ok: false, error: 'not_scrollable' }
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

  return { sandbox, elementsById, sentMessages, openCalls, captureCalls, scrollCalls }
}

async function settle() {
  await new Promise((resolve) => setImmediate(resolve))
  await new Promise((resolve) => setImmediate(resolve))
}

function barOf(env) {
  return env.elementsById['jobagent-session-bar']
}

function scrollButtonOf(env) {
  const bar = barOf(env)
  return bar && bar.children.find((c) => c.textContent === '向下滚动')
}

function pageButtonOf(env) {
  const bar = barOf(env)
  return bar && bar.children.find((c) => c.textContent === '下一页')
}

function nextButtonOf(env) {
  const bar = barOf(env)
  return bar && bar.children.find((c) => c.textContent === '下一位候选人')
}

function captureButtonOf(env) {
  const bar = barOf(env)
  return bar && bar.children.find((c) => c.textContent === '捕获详情')
}

function stopMessagesOf(env) {
  return env.sentMessages.filter((m) => m.type === 'jobagent:stop-session')
}

function preparesOf(env, target) {
  return env.sentMessages.filter((m) => m.type === 'jobagent:navigate-prepare' && m.target === target)
}

function confirmsOf(env, target) {
  return env.sentMessages.filter((m) => m.type === 'jobagent:navigate-confirm' && m.target === target)
}

function backend(sessionOut, extra) {
  return (msg) => {
    if (extra) {
      const result = extra(msg)
      if (result !== undefined) return result
    }
    if (msg.type === 'jobagent:session-snapshot') return { kind: 'session', session: sessionOut }
    if (msg.type === 'jobagent:navigate-prepare') return { ok: true, session: sessionOut }
    if (msg.type === 'jobagent:navigate-confirm') return { ok: true, session: sessionOut }
    if (msg.type === 'jobagent:stop-session') return { ok: true }
    return { ok: false }
  }
}

// --------------------------------------------------------------------------
// no pagination control anywhere - the concrete live acceptance failure
// --------------------------------------------------------------------------

test('there is no 下一页 control in the rendered bar - BOSS results are a continuous list', { skip: SKIP }, async () => {
  const s = session({ page_cap: 3, pages_visited: 1 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    backendResponses: backend(s),
  })
  await settle()
  assert.equal(pageButtonOf(env), undefined)
  assert.equal(env.sentMessages.some((m) => m.target === 'results'), false)
})

// --------------------------------------------------------------------------
// caps - the bar must reflect them without any click
// --------------------------------------------------------------------------

test('scroll_cap=0 disables 向下滚动 on first render', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 0, scrolls_used: 0 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    backendResponses: backend(s),
  })
  await settle()
  assert.equal(scrollButtonOf(env).disabled, true)
})

// --------------------------------------------------------------------------
// a successful scroll: exactly one scroll primitive call, one prepare/
// confirm pair
// --------------------------------------------------------------------------

test('a successful scroll makes exactly one scroll call and one prepare/confirm pair', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 2, scrolls_used: 0 })
  const confirmed = { ...s, scrolls_used: 1 }
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    scrollResults: [{ ok: true }],
    backendResponses: backend(s, (msg) => {
      if (msg.type === 'jobagent:navigate-confirm' && msg.target === 'scroll') {
        return { ok: true, session: confirmed }
      }
    }),
  })
  await settle()

  scrollButtonOf(env).click()
  await settle()

  assert.equal(env.scrollCalls.length, 1)
  assert.equal(preparesOf(env, 'scroll').length, 1)
  assert.equal(confirmsOf(env, 'scroll').length, 1)
  assert.equal(confirmsOf(env, 'scroll')[0].outcome, 'success')
  assert.equal(stopMessagesOf(env).length, 0)
  assert.equal(scrollButtonOf(env).disabled, false, 'still below the scroll_cap=2 after one use')
})

// --------------------------------------------------------------------------
// M4d: session-local candidate inventory around each scroll step
// --------------------------------------------------------------------------

test('the first scroll counts pre-existing cards as duplicates, only appended URLs as new', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 5 })
  const env = loadOverlay({
    // Before this scroll: A, B already rendered. After: A, B, C - only C is
    // genuinely new; A and B must not be miscounted just because
    // `observedUrls` started empty.
    detectResults: [pageOf([URL_A, URL_B]), pageOf([URL_A, URL_B, URL_C])],
    openResult: { ok: true },
    scrollResults: [{ ok: true }],
    backendResponses: backend(s),
  })
  await settle()

  scrollButtonOf(env).click()
  await settle()

  const status = env.elementsById['jobagent-session-status-line']
  assert.match(status.textContent, /可见 3 个/)
  assert.match(status.textContent, /新增 1 个/)
  assert.match(status.textContent, /重复 2 个/)
  assert.equal(stopMessagesOf(env).length, 0)
})

test('repeated/duplicate anchors for the same URL are counted once in the inventory', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 5 })
  // The same candidate URL appears twice in one DETECT result (e.g. two
  // anchors on one card) - it must still count as exactly one unique URL.
  const beforePage = pageOf([URL_A])
  const afterPage = { ...pageOf([URL_A, URL_B]), candidates: [...pageOf([URL_A, URL_B]).candidates, { title: '重复锚点', source_url: URL_B }] }
  const env = loadOverlay({
    detectResults: [beforePage, afterPage],
    openResult: { ok: true },
    scrollResults: [{ ok: true }],
    backendResponses: backend(s),
  })
  await settle()

  scrollButtonOf(env).click()
  await settle()

  const status = env.elementsById['jobagent-session-status-line']
  assert.match(status.textContent, /可见 2 个/, 'the duplicate anchor for B must not inflate observed_count')
  assert.match(status.textContent, /新增 1 个/)
})

test('a later zero-growth scroll reports no_new_candidates and takes no automatic action', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 5 })
  const env = loadOverlay({
    // Same set before and after this scroll - nothing new appeared.
    detectResults: [pageOf([URL_A, URL_B]), pageOf([URL_A, URL_B])],
    openResult: { ok: true },
    scrollResults: [{ ok: true }],
    backendResponses: backend(s),
  })
  await settle()

  scrollButtonOf(env).click()
  await settle()

  const status = env.elementsById['jobagent-session-status-line']
  assert.match(status.textContent, /未发现新候选人/)
  assert.equal(env.scrollCalls.length, 1, 'exactly the one human-triggered scroll, nothing automatic after it')
  assert.equal(stopMessagesOf(env).length, 0, 'zero growth is not a policy violation')
})

test('a failed post-scroll DETECT never reports a fabricated zero/new count', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 5 })
  const env = loadOverlay({
    // The pre-scroll DETECT succeeds; the post-scroll one fails outright.
    detectResults: [pageOf([URL_A]), null],
    openResult: { ok: true },
    scrollResults: [{ ok: true }],
    backendResponses: backend(s),
  })
  await settle()

  scrollButtonOf(env).click()
  await settle()

  const status = env.elementsById['jobagent-session-status-line']
  assert.doesNotMatch(status.textContent, /可见 0 个/, 'must never fabricate a zero-observed result')
  assert.doesNotMatch(status.textContent, /新增 0 个/)
  assert.match(status.textContent, /失败/)
  assert.equal(stopMessagesOf(env).length, 0, 'an unreadable page is inconclusive, not a policy violation')
  assert.equal(scrollButtonOf(env).disabled, false, 'the human may click 向下滚动 again')
})

test('a verification interstitial revealed only after scrolling still hard-stops the session', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 5 })
  const env = loadOverlay({
    detectResults: [pageOf([URL_A]), { page_type: 'search', verification: true, candidates: [] }],
    openResult: { ok: true },
    scrollResults: [{ ok: true }],
    backendResponses: backend(s),
  })
  await settle()
  scrollButtonOf(env).click()
  await settle()
  assert.equal(stopMessagesOf(env)[0].reason, 'verification')
})

test('a page shape that is no longer search after scrolling still hard-stops the session', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 5 })
  const env = loadOverlay({
    detectResults: [pageOf([URL_A]), { page_type: 'detail', verification: false, candidates: [] }],
    openResult: { ok: true },
    scrollResults: [{ ok: true }],
    backendResponses: backend(s),
  })
  await settle()
  scrollButtonOf(env).click()
  await settle()
  assert.equal(stopMessagesOf(env)[0].reason, 'wrong_page')
})

// --------------------------------------------------------------------------
// pending capture blocks scrolling outright - zero endpoint, zero DOM
// --------------------------------------------------------------------------

test('scroll refuses outright while a candidate is pending capture', { skip: SKIP }, async () => {
  const s = session({ candidate_cap: 5, candidates_extracted: 0 })
  const confirmedOpen = { ...s, candidates_extracted: 1 }
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    scrollResults: [{ ok: true }],
    backendResponses: backend(s, (msg) => {
      if (msg.type === 'jobagent:navigate-confirm' && msg.target === 'detail') {
        return { ok: true, session: confirmedOpen }
      }
    }),
  })
  await settle()

  nextButtonOf(env).click()
  await settle()
  assert.equal(env.openCalls.length, 1)
  assert.ok(captureButtonOf(env).disabled === false, 'a candidate is now pending')

  scrollButtonOf(env).click()
  await settle()

  assert.equal(env.scrollCalls.length, 0, 'scroll must never touch the DOM while pending')
  assert.equal(preparesOf(env, 'scroll').length, 0)
  assert.equal(stopMessagesOf(env).length, 0, 'a refusal, not a policy violation')
})

// --------------------------------------------------------------------------
// rapid clicks: at most one step runs
// --------------------------------------------------------------------------

test('a rapid double-click on 向下滚动 performs exactly one scroll', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 5 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    scrollResults: [{ ok: true }, { ok: true }],
    backendResponses: backend(s),
  })
  await settle()

  scrollButtonOf(env).click()
  scrollButtonOf(env).click()
  await settle()

  assert.equal(env.scrollCalls.length, 1, 'the second click landed while the first was in flight')
})

// --------------------------------------------------------------------------
// prepare / perform / confirm failures each hard-stop, never retry
// --------------------------------------------------------------------------

test('a denied scroll prepare scrolls nothing and stops the session', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 0 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    scrollResults: [{ ok: true }],
    backendResponses: backend(s, (msg) => {
      if (msg.type === 'jobagent:navigate-prepare' && msg.target === 'scroll') {
        return { ok: false, error: 'cap_reached' }
      }
    }),
  })
  await settle()

  scrollButtonOf(env).click()
  await settle()

  assert.equal(env.scrollCalls.length, 0)
  const stops = stopMessagesOf(env)
  assert.equal(stops.length, 1)
  assert.equal(stops[0].reason, 'prepare_denied')
})

test('a failed scroll (no unique container) confirms failed and stops', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 5 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    scrollResults: [{ ok: false, error: 'container_ambiguous' }],
    backendResponses: backend(s),
  })
  await settle()

  scrollButtonOf(env).click()
  await settle()

  assert.equal(env.scrollCalls.length, 1)
  const confirms = confirmsOf(env, 'scroll')
  assert.equal(confirms.length, 1)
  assert.equal(confirms[0].outcome, 'failed')
  assert.equal(confirms[0].error, 'container_ambiguous')
  const stops = stopMessagesOf(env)
  assert.equal(stops.length, 1)
  assert.equal(stops[0].reason, 'scroll_failed')
})

test('scroll confirm failing to reach the backend stops rather than risk an unaccounted step', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 5 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    scrollResults: [{ ok: true }],
    backendResponses: backend(s, (msg) => {
      if (msg.type === 'jobagent:navigate-confirm' && msg.target === 'scroll') return { ok: false, error: 'network' }
    }),
  })
  await settle()
  scrollButtonOf(env).click()
  await settle()
  assert.equal(stopMessagesOf(env)[0].reason, 'confirm_failed')
})

// --------------------------------------------------------------------------
// verification / wrong page shape
// --------------------------------------------------------------------------

test('a verification interstitial stops scrolling without ever preparing', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 5 })
  const env = loadOverlay({
    detectResult: { page_type: 'search', verification: true, candidates: [] },
    openResult: { ok: true },
    backendResponses: backend(s),
  })
  await settle()
  scrollButtonOf(env).click()
  await settle()
  assert.equal(preparesOf(env, 'scroll').length, 0)
  assert.equal(stopMessagesOf(env)[0].reason, 'verification')
})

test('a non-search page stops scrolling without ever preparing', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 5 })
  const env = loadOverlay({
    detectResult: { page_type: 'detail', verification: false, candidates: [] },
    openResult: { ok: true },
    backendResponses: backend(s),
  })
  await settle()
  scrollButtonOf(env).click()
  await settle()
  assert.equal(preparesOf(env, 'scroll').length, 0)
  assert.equal(stopMessagesOf(env)[0].reason, 'wrong_page')
})

// --------------------------------------------------------------------------
// restart / reconnect: button state always comes from the session snapshot
// --------------------------------------------------------------------------

test('reconnecting mid-session (script re-injected) derives button state from the snapshot alone', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 2, scrolls_used: 2, candidate_cap: 1, candidates_extracted: 1 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    backendResponses: backend(s),
  })
  await settle()

  assert.equal(nextButtonOf(env).disabled, true, 'candidate_cap already reached')
  assert.equal(scrollButtonOf(env).disabled, true, 'scroll_cap already reached')
  assert.equal(pageButtonOf(env), undefined, 'no pagination control exists to reconnect to')
  // M4e/M4f added one more one-time (never polled) read on load, the
  // runner-status check - see overlay.ts's `init()`.
  assert.equal(
    env.sentMessages.filter(
      (m) => m.type !== 'jobagent:session-snapshot' && m.type !== 'jobagent:runner-status',
    ).length,
    0,
  )
})

// --------------------------------------------------------------------------
// no automatic behavior anywhere in the built code
// --------------------------------------------------------------------------

test('the built overlay/content scripts contain no timer, poll or MutationObserver', { skip: SKIP }, async () => {
  const overlayCode = fs.readFileSync(OVERLAY_PATH, 'utf8')
  const contentCode = fs.readFileSync(CONTENT_PATH, 'utf8')
  for (const code of [overlayCode, contentCode]) {
    assert.ok(!code.includes('setInterval('))
    assert.ok(!code.includes('setTimeout('))
    assert.ok(!code.includes('new MutationObserver('))
    assert.ok(!code.includes('requestAnimationFrame('))
  }
})
