'use strict'

/**
 * M4c ("向下滚动" / "下一页") behavior - Node built-ins only (`node:test`,
 * `assert`, `vm`). No browser, no Playwright/CDP, no network, no
 * dependencies.
 *
 * Loads the real built `dist/overlay.js` into a `vm` sandbox with a minimal
 * DOM mock, a scriptable `chrome.runtime.sendMessage` (standing in for
 * background.ts's real responses), and a scriptable `BossContentScript`
 * that records every `jobagent:scroll-step` / `jobagent:next-page` call.
 * Same harness shape as `m4b-capture-behavior.test.cjs`.
 *
 * Proves: one human click causes at most one scroll/click primitive call
 * and one prepare/confirm round-trip; both actions refuse outright (no
 * fetch, no DOM action) while a candidate is pending capture or while any
 * other step is already in flight; caps (including the starting page
 * already counting toward `page_cap`) and their per-page scroll reset are
 * read from the session, never guessed client-side; verification/wrong-page/
 * prepare/perform/confirm failures each end the session without retry; a
 * repeated results-page URL is refused as a loop, including immediately
 * after a simulated content-script reload; and no timer, poll or
 * MutationObserver exists in the built code.
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
 * `scrollResults`/`pageResults` are queues: each call to the matching
 * primitive shifts the next scripted result off, defaulting to a generic
 * failure once exhausted. `href` seeds `document.location.href` (and can be
 * mutated mid-test via `env.setHref` to simulate a navigation).
 */
function loadOverlay({
  detectResult,
  openResult,
  captureResults,
  scrollResults,
  pageResults,
  backendResponses,
  href,
  referrer,
}) {
  const elementsById = {}
  const sentMessages = []
  const openCalls = []
  const captureCalls = []
  const scrollCalls = []
  const pageCalls = []
  const listeners = []
  const remainingCaptures = (captureResults || []).slice()
  const remainingScrolls = (scrollResults || []).slice()
  const remainingPages = (pageResults || []).slice()

  function registerIfHasId(el) {
    if (el && el.id) elementsById[el.id] = el
  }
  const documentElement = {
    appendChild(el) {
      registerIfHasId(el)
    },
  }
  const location = { protocol: 'https:', host: 'www.zhipin.com', href: href || PAGE_URL }
  const doc = {
    location,
    referrer: referrer || '',
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
        if (msg.type === 'jobagent:detect') return { ok: true, result: detectResult }
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
        if (msg.type === 'jobagent:next-page') {
          pageCalls.push(true)
          const result = remainingPages.length
            ? remainingPages.shift()
            : { ok: false, error: 'no_control' }
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

  return {
    sandbox,
    elementsById,
    sentMessages,
    openCalls,
    captureCalls,
    scrollCalls,
    pageCalls,
    setHref(next) {
      location.href = next
    },
  }
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
    // `extra` gets first refusal so a test can override one specific
    // target's prepare/confirm response - the generic fallbacks below
    // would otherwise always answer `navigate-prepare`/`navigate-confirm`
    // for *any* target before `extra` ever saw the message.
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
// starting page / caps - the bar must reflect them without any click
// --------------------------------------------------------------------------

test('the starting page already counts toward page_cap: page_cap=1 disables 下一页 on first render', { skip: SKIP }, async () => {
  const s = session({ page_cap: 1, pages_visited: 1 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    backendResponses: backend(s),
  })
  await settle()
  assert.equal(pageButtonOf(env).disabled, true)
  assert.equal(preparesOf(env, 'results').length, 0)
})

test('scroll_cap=0 disables 向下滚动 on first render, with page_cap still allowing 下一页', { skip: SKIP }, async () => {
  const s = session({ scroll_cap: 0, scrolls_used: 0, page_cap: 3, pages_visited: 1 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    backendResponses: backend(s),
  })
  await settle()
  assert.equal(scrollButtonOf(env).disabled, true)
  assert.equal(pageButtonOf(env).disabled, false)
})

// --------------------------------------------------------------------------
// a successful scroll: exactly one scroll primitive call, one prepare/
// confirm pair, and the resulting session drives the new button state
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

test('a confirmed pagination resets the per-page scroll button', { skip: SKIP }, async () => {
  const s = session({ page_cap: 3, pages_visited: 1, scroll_cap: 2, scrolls_used: 2 })
  const afterPage = { ...s, pages_visited: 2, scrolls_used: 0 }
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    pageResults: [{ ok: true }],
    backendResponses: backend(s, (msg) => {
      if (msg.type === 'jobagent:navigate-confirm' && msg.target === 'results') {
        return { ok: true, session: afterPage }
      }
    }),
  })
  await settle()
  assert.equal(scrollButtonOf(env).disabled, true, 'scroll_cap already exhausted on the starting page')

  pageButtonOf(env).click()
  await settle()

  assert.equal(env.pageCalls.length, 1)
  assert.equal(confirmsOf(env, 'results')[0].outcome, 'success')
  assert.equal(scrollButtonOf(env).disabled, false, 'the new page reset scrolls_used to 0')
  assert.equal(pageButtonOf(env).disabled, false, 'still below page_cap=3')
})

// --------------------------------------------------------------------------
// pending capture blocks both actions outright - zero endpoint, zero DOM
// --------------------------------------------------------------------------

test('scroll and next-page both refuse outright while a candidate is pending capture', { skip: SKIP }, async () => {
  const s = session({ candidate_cap: 5, candidates_extracted: 0 })
  const confirmedOpen = { ...s, candidates_extracted: 1 }
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    scrollResults: [{ ok: true }],
    pageResults: [{ ok: true }],
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
  pageButtonOf(env).click()
  await settle()

  assert.equal(env.scrollCalls.length, 0, 'scroll must never touch the DOM while pending')
  assert.equal(env.pageCalls.length, 0, 'pagination must never touch the DOM while pending')
  assert.equal(preparesOf(env, 'scroll').length, 0)
  assert.equal(preparesOf(env, 'results').length, 0)
  assert.equal(stopMessagesOf(env).length, 0, 'a refusal, not a policy violation')
})

// --------------------------------------------------------------------------
// rapid same-button and cross-button clicks: at most one step runs
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

test('clicking 下一页 while a scroll is in flight is dropped, not queued', { skip: SKIP }, async () => {
  const s = session({ page_cap: 3, scroll_cap: 5 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    scrollResults: [{ ok: true }],
    pageResults: [{ ok: true }],
    backendResponses: backend(s),
  })
  await settle()

  scrollButtonOf(env).click()
  pageButtonOf(env).click()
  await settle()

  assert.equal(env.scrollCalls.length, 1)
  assert.equal(env.pageCalls.length, 0, 'cross-button click during an in-flight step is dropped too')
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

test('an ambiguous next-page control confirms failed and stops without clicking twice', { skip: SKIP }, async () => {
  const s = session({ page_cap: 3 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    pageResults: [{ ok: false, error: 'control_ambiguous' }],
    backendResponses: backend(s),
  })
  await settle()

  pageButtonOf(env).click()
  await settle()

  assert.equal(env.pageCalls.length, 1)
  assert.equal(confirmsOf(env, 'results')[0].outcome, 'failed')
  const stops = stopMessagesOf(env)
  assert.equal(stops.length, 1)
  assert.equal(stops[0].reason, 'click_failed')
})

test('a missing next-page control (no_control) is treated the same as ambiguous - stop, no guess', { skip: SKIP }, async () => {
  const s = session({ page_cap: 3 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    pageResults: [{ ok: false, error: 'no_control' }],
    backendResponses: backend(s),
  })
  await settle()
  pageButtonOf(env).click()
  await settle()
  assert.equal(stopMessagesOf(env)[0].reason, 'click_failed')
})

test('a disabled next-page control is refused rather than clicked', { skip: SKIP }, async () => {
  const s = session({ page_cap: 3 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    pageResults: [{ ok: false, error: 'disabled' }],
    backendResponses: backend(s),
  })
  await settle()
  pageButtonOf(env).click()
  await settle()
  assert.equal(env.pageCalls.length, 1, 'the primitive is still called once - it is the one that refuses')
  assert.equal(stopMessagesOf(env)[0].reason, 'click_failed')
})

test('a wrong-origin next-page href is refused rather than clicked', { skip: SKIP }, async () => {
  const s = session({ page_cap: 3 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    pageResults: [{ ok: false, error: 'wrong_origin' }],
    backendResponses: backend(s),
  })
  await settle()
  pageButtonOf(env).click()
  await settle()
  assert.equal(stopMessagesOf(env)[0].reason, 'click_failed')
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

test('a non-search page stops pagination without ever preparing', { skip: SKIP }, async () => {
  const s = session({ page_cap: 3 })
  const env = loadOverlay({
    detectResult: { page_type: 'detail', verification: false, candidates: [] },
    openResult: { ok: true },
    backendResponses: backend(s),
  })
  await settle()
  pageButtonOf(env).click()
  await settle()
  assert.equal(preparesOf(env, 'results').length, 0)
  assert.equal(stopMessagesOf(env)[0].reason, 'wrong_page')
})

// --------------------------------------------------------------------------
// page loop detection, including immediately after a simulated reload
// --------------------------------------------------------------------------

test('paginating back to an already-handled results URL is refused as a loop', { skip: SKIP }, async () => {
  const s = session({ page_cap: 3, pages_visited: 1 })
  const afterFirst = { ...s, pages_visited: 2 }
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    pageResults: [{ ok: true }, { ok: true }],
    backendResponses: backend(s, (msg) => {
      if (msg.type === 'jobagent:navigate-confirm' && msg.target === 'results') {
        return { ok: true, session: afterFirst }
      }
    }),
  })
  await settle()

  pageButtonOf(env).click()
  await settle()
  assert.equal(env.pageCalls.length, 1)

  // The click succeeded but the tab never actually changed page (a stuck
  // client-side pager) - the second attempt starts from the exact same URL.
  pageButtonOf(env).click()
  await settle()

  assert.equal(env.pageCalls.length, 1, 'the loop was caught before a second click')
  const stops = stopMessagesOf(env)
  assert.equal(stops.length, 1)
  assert.equal(stops[0].reason, 'loop_detected')
})

test('page 2 -> page 3 is allowed on a fresh reconnect after a full-page reload', { skip: SKIP }, async () => {
  // Simulates the real lifecycle a full-page pagination navigation causes:
  // this `loadOverlay` call is a brand new script instance - `handledUrls`/
  // `handledPageUrls` from whatever instance clicked "下一页" on page 1 are
  // gone, exactly like every in-memory Set here. The fix under test: this
  // must NOT be treated as a loop just because the session's own
  // `pages_visited` is already 2 - only an actual repeated/bounced-back URL
  // may refuse.
  const thirdPageUrl = 'https://www.zhipin.com/web/geek/jobs?query=x&page=3'
  const s = session({ page_cap: 3, pages_visited: 2 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    pageResults: [{ ok: true }],
    backendResponses: backend(s),
    href: 'https://www.zhipin.com/web/geek/jobs?query=x&page=2',
    referrer: 'https://www.zhipin.com/web/geek/jobs?query=x', // came from page 1, not itself
  })
  await settle()

  pageButtonOf(env).click()
  await settle()

  assert.equal(env.pageCalls.length, 1, 'the first pagination attempt from this fresh page must proceed')
  assert.equal(stopMessagesOf(env).length, 0)
  void thirdPageUrl
})

test('a same-page bounce-back is caught via document.referrer, even on a fresh reload', { skip: SKIP }, async () => {
  // The reload-safe complement: the browser sets `document.referrer` to the
  // actual previous page regardless of any script state, so a page whose
  // own referrer is itself proves the last navigation went nowhere - caught
  // here even though this is a brand-new script instance with an empty
  // `handledPageUrls`.
  const bouncedUrl = 'https://www.zhipin.com/web/geek/jobs?query=x&page=2'
  const s = session({ page_cap: 3, pages_visited: 2 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    pageResults: [{ ok: true }],
    backendResponses: backend(s),
    href: bouncedUrl,
    referrer: bouncedUrl, // navigated here from here - a bounce-back
  })
  await settle()

  pageButtonOf(env).click()
  await settle()

  assert.equal(env.pageCalls.length, 0, 'the referrer-based guard caught it before any click attempt')
  const stops = stopMessagesOf(env)
  assert.equal(stops.length, 1)
  assert.equal(stops[0].reason, 'loop_detected')
})

// --------------------------------------------------------------------------
// restart / reconnect: button state always comes from the session snapshot
// --------------------------------------------------------------------------

test('reconnecting mid-session (script re-injected) derives button state from the snapshot alone', { skip: SKIP }, async () => {
  const s = session({ page_cap: 3, pages_visited: 2, scroll_cap: 2, scrolls_used: 2, candidate_cap: 1, candidates_extracted: 1 })
  const env = loadOverlay({
    detectResult: SEARCH_PAGE,
    openResult: { ok: true },
    backendResponses: backend(s),
  })
  await settle()

  assert.equal(nextButtonOf(env).disabled, true, 'candidate_cap already reached')
  assert.equal(scrollButtonOf(env).disabled, true, 'scroll_cap already reached')
  assert.equal(pageButtonOf(env).disabled, false, 'page_cap not yet reached')
  assert.equal(env.sentMessages.filter((m) => m.type !== 'jobagent:session-snapshot').length, 0)
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
    // Checked as a constructor call, not the bare identifier - this file's
    // own doc comments name "MutationObserver" as one of the things that
    // must never appear as *code*, which would otherwise false-positive.
    assert.ok(!code.includes('new MutationObserver('))
    assert.ok(!code.includes('requestAnimationFrame('))
  }
})
