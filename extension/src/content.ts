/**
 * The content script.
 *
 * It sits idle on a BOSS page and does exactly nothing until the popup or
 * `overlay.ts` asks it a question. There is no timer, no MutationObserver,
 * no scroll handler and no automatic capture: a detection happens only
 * because a human clicked 检测当前页面, and the one click/scroll this script
 * can perform (`OPEN_CANDIDATE` and M4c's `NEXT_PAGE`/`SCROLL_STEP` -
 * explicitly authorized) only happens after `overlay.ts` has already gotten
 * a per-step authorization from the backend.
 *
 * Beyond those named paths it only reads the DOM and replies. M6 adds one
 * separate mutation: after a per-job approval has been atomically claimed
 * and exact page identity is rechecked, `jobagent:m6-execute` may click the
 * single visible "立即沟通" control once. It never retries or follows up.
 */

// eslint-disable-next-line no-var
var BossContentScript = (function () {
  const PING = 'jobagent:ping'
  const DETECT = 'jobagent:detect'
  const DIAGNOSE = 'jobagent:diagnose'
  //: M4b (CLAUDE.md "Chrome extension - M4 supervised navigation policy").
  //: Called directly by `overlay.ts` - both files share this content
  //: script's isolated world, so this is a plain function call, not a
  //: `chrome.runtime` message.
  const OPEN_CANDIDATE = 'jobagent:open-candidate'
  //: M4b phase two - read the selected-card detail pane and merge it with
  //: the card `OPEN_CANDIDATE` was told to click. Same isolated-world direct
  //: call as `OPEN_CANDIDATE`, not a `chrome.runtime` message.
  const CAPTURE_DETAIL = 'jobagent:capture-detail'
  //: M4c (CLAUDE.md "Chrome extension - M4 supervised navigation policy",
  //: explicitly authorized). Same isolated-world direct-call pattern as
  //: `OPEN_CANDIDATE`/`CAPTURE_DETAIL` - `overlay.ts` calls these only after
  //: its own backend-authorized prepare step succeeds.
  const SCROLL_STEP = 'jobagent:scroll-step'
  const NEXT_PAGE = 'jobagent:next-page'
  const M6_PREFLIGHT = 'jobagent:m6-preflight'
  const M6_EXECUTE = 'jobagent:m6-execute'
  const M6_GREETING = 'jobagent:m6-greeting'
  const M6_GREETING_DIAG = 'jobagent:m6-greeting-diagnostic'

  interface CachedCardLike {
    title: string | null
    company: string | null
    salary_text: string | null
    city: string | null
    experience_text: string | null
    education_text: string | null
    source_url: string | null
    external_id: string | null
    matched_selectors?: Record<string, string>
  }

  interface Request {
    type?: string
    index?: number
    canonicalUrl?: string
    cachedCard?: CachedCardLike
    expectedTitle?: string
    applicationIdentity?: {
      canonical_url: string
      external_id: string
      company: string
      title: string
    }
    /** The human-confirmed greeting to type. Only the worker sends this, and
     *  only for an approval whose bound text it read back from the backend. */
    greeting?: string
    /** The approval's own job id. The greeting is refused unless the page is
     *  still that job's detail page - see `sendConfirmedGreeting`. */
    expectedExternalId?: string
  }

  function handle(message: unknown): unknown {
    const request = (message || {}) as Request

    if (request.type === PING) {
      return { ok: true, ready: true }
    }

    if (request.type === 'jobagent:salary-frame' && typeof request.canonicalUrl === 'string' && typeof request.expectedTitle === 'string') {
      return { ok: true, result: BossExtract.salaryFrame(document, document.location.href, request.canonicalUrl, request.expectedTitle) }
    }

    if (request.type === DETECT) {
      // `document.location.href` rather than anything cached: the user may
      // have navigated since the script was injected.
      return { ok: true, result: BossExtract.detect(document, document.location.href) }
    }

    if (request.type === 'jobagent:read-salary-filter') {
      // Read-only: it opens no menu and clicks nothing. If BOSS keeps the
      // options out of the DOM until the menu is open, the human opens it.
      return { ok: true, result: BossExtract.readSalaryFilterOptions(document) }
    }

    if (request.type === DIAGNOSE) {
      // Developer-mode only, explicit-click structural diagnostic. See
      // `boss/extract.ts` - it never sends anything anywhere by itself.
      return { ok: true, result: BossExtract.diagnoseDetail(document, document.location.href) }
    }

    if (request.type === M6_GREETING_DIAG) {
      // Read-only page shape, so a failed greeting can be diagnosed from the
      // recorded attempt instead of another live one.
      return { ok: true, result: { shape: BossExtract.greetingDiagnostic(document) } }
    }

    if (request.type === M6_GREETING && typeof request.greeting === 'string') {
      // Types the human-confirmed greeting into an empty composer and sends it
      // once. Refuses on anything ambiguous, and never touches a box that
      // already holds text - BOSS sometimes greets on its own.
      return {
        ok: true,
        result: BossExtract.sendConfirmedGreeting(
          document,
          request.greeting,
          document.location.href,
          request.expectedExternalId || '',
        ),
      }
    }

    if (request.type === M6_PREFLIGHT && request.applicationIdentity) {
      return {
        ok: true,
        result: BossExtract.preflightConfirmedApplication(
          document,
          document.location.href,
          request.applicationIdentity,
        ),
      }
    }

    if (request.type === M6_EXECUTE && request.applicationIdentity) {
      return {
        ok: true,
        result: BossExtract.executeConfirmedApplication(
          document,
          document.location.href,
          request.applicationIdentity,
        ),
      }
    }

    if (request.type === OPEN_CANDIDATE && typeof request.index === 'number') {
      // The one navigation primitive: click an already-rendered card's own
      // link, by index. Never scrolls, never constructs a URL, never
      // guesses on an ambiguous match. See `boss/extract.ts`.
      return { ok: true, result: BossExtract.openCandidateLink(document, request.index) }
    }

    if (
      request.type === CAPTURE_DETAIL
      && typeof request.canonicalUrl === 'string'
      && request.cachedCard
    ) {
      // Read the (hopefully now-loaded) detail pane and merge it with the
      // exact card `OPEN_CANDIDATE` clicked. See `boss/extract.ts`.
      return {
        ok: true,
        result: BossExtract.captureAndMerge(
          document,
          document.location.href,
          request.canonicalUrl,
          request.cachedCard,
        ),
      }
    }

    if (request.type === SCROLL_STEP) {
      // One bounded scroll step on the results container (or the page
      // itself as fallback). See `boss/extract.ts`.
      return { ok: true, result: BossExtract.scrollResultsContainer(document) }
    }

    if (request.type === NEXT_PAGE) {
      // Activate exactly one same-origin, non-disabled "next page" control.
      // See `boss/extract.ts`.
      return { ok: true, result: BossExtract.activateNextPage(document) }
    }

    return { ok: false, error: 'unknown_request' }
  }

  // Guard against a double registration if the script is ever injected twice
  // into the same isolated world - two listeners would both answer and the
  // second response would be dropped with a console error.
  const flag = '__jobagentBossDetectorInstalled'
  const globals = window as unknown as Record<string, boolean>
  if (!globals[flag]) {
    globals[flag] = true
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      try {
        sendResponse(handle(message))
      } catch (err) {
        sendResponse({ ok: false, error: String((err as Error)?.message || err) })
      }
      return false // responded synchronously
    })
  }

  return { handle, PING, DETECT, DIAGNOSE, OPEN_CANDIDATE, CAPTURE_DETAIL, SCROLL_STEP,
    NEXT_PAGE, M6_PREFLIGHT, M6_EXECUTE, M6_GREETING, M6_GREETING_DIAG }
})()
