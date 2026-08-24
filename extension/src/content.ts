/**
 * The content script.
 *
 * It sits idle on a BOSS page and does exactly nothing until the popup asks it
 * a question. There is no timer, no MutationObserver, no scroll handler and no
 * automatic capture: a detection happens only because a human clicked
 * 检测当前页面.
 *
 * It reads the DOM and replies. It never navigates, clicks, submits, scrolls,
 * or writes to the page.
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

  interface Request {
    type?: string
    index?: number
  }

  function handle(message: unknown): unknown {
    const request = (message || {}) as Request

    if (request.type === PING) {
      return { ok: true, ready: true }
    }

    if (request.type === DETECT) {
      // `document.location.href` rather than anything cached: the user may
      // have navigated since the script was injected.
      return { ok: true, result: BossExtract.detect(document, document.location.href) }
    }

    if (request.type === DIAGNOSE) {
      // Developer-mode only, explicit-click structural diagnostic. See
      // `boss/extract.ts` - it never sends anything anywhere by itself.
      return { ok: true, result: BossExtract.diagnoseDetail(document, document.location.href) }
    }

    if (request.type === OPEN_CANDIDATE && typeof request.index === 'number') {
      // The one navigation primitive: click an already-rendered card's own
      // link, by index. Never scrolls, never constructs a URL, never
      // guesses on an ambiguous match. See `boss/extract.ts`.
      return { ok: true, result: BossExtract.openCandidateLink(document, request.index) }
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

  return { handle, PING, DETECT, DIAGNOSE, OPEN_CANDIDATE }
})()
