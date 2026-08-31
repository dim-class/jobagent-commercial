# Automated browser testing: feasibility and boundaries

## Latest correction after live task #12

- Real screenshot: `start-v3/window_not_focused` with the native action popup open;
  read-only backend #12 remains pending with zero discoveries/imports. Earlier headless
  PASS did not prove this normal-Chrome focus behavior.
- Start/resume now bind the tab in the popup and validate its own extension sender URL
  against one native POPUP context. Chrome's actual popup sender did not supply documentId
  in the isolated test; do not assume it does. Context inspection uses the official
  [runtime.getContexts API](https://developer.chrome.com/docs/extensions/reference/api/runtime#method-getContexts).
- Backend start/resume acknowledgement precedes popup closure. Only then may a bounded
  gate release browser work: no open extension popup, same tab/window/URL, active tab and
  focused normal window. No force-focus; stop/cancel wins and failure ends without retry.
  A pause before initialization now resumes that initialization without resetting budget.
- Latest acceptance: build, 226/226 Node tests; 18/18 isolated native-MV3 tests, 82.92s,
  zero failures/errors/skips. Added real-popup-held-open timeout and cancellation tests;
  both prove no navigation, OCR or imports. Full suite retains OCR/canonical intake,
  scroll/dedup/identity/verification and pause/resume coverage. Evidence:
  `.tmp/mv3-handoff-v4-full.xml` (headless Chromium, no live site or production DB).
- No more implementation pending this check. One human Reload + BOSS refresh + start
  task #12 at cap=3, keeping BOSS foreground, is still needed for normal-Chrome live evidence.

## Initial findings (before the implementation below)

- Repository has Python Playwright 1.62.0 and its bundled Chromium executable installed.
  This was an installation check, not a new browser/E2E PASS.
- Current extension tests use Node VM Chrome API doubles; backend DOM tests inject built
  extraction scripts into fixture pages. They do not prove real action-popup focus handoff.
- In-app Browser is available with its Playwright page interface. It is useful for local
  frontend UI tests, but the available interface does not establish that the user's JobAgent
  extension or normal Chrome/BOSS session is attached.
- Native Chrome browser selection returned unavailable. Windows Computer Use discovery
  returned no matching Chrome window/app. No browser input, installation or driver repair
  was attempted; this does not prove Chrome is absent from the user's desktop.

## Implemented isolated harness

1. Use existing Playwright + isolated temporary Chromium profile to load the real unpacked
   extension and its built files, without altering the production manifest or normal profile.
2. Fulfill all recruiting-site requests with local fixtures; block external network. Use an
   isolated test backend/database and existing canonical intake, never production job writes.
3. Exercise actual content script, worker and UI messaging; test popup acknowledgement/close,
   screenshot permissions, candidate identity, cropped salary OCR, cancellation and budgets.
   Add positive and negative cases, assert stored salary/provenance and exact candidate counts.
4. Real toolbar-popup testing uses CDP `Extensions.triggerAction` on the TAB target and
   attaches the actual POPUP context. `chrome.action.openPopup()` alone does not grant
   activeTab; a negative test checks that distinction. A popup.html normal tab cannot prove
   native popup behavior. DOM activation inside the real popup is not desktop mouse/focus proof.
5. Emit compact evidence and a final review checklist. This can move routine development
   acceptance off the user, but must remain labeled fixture/E2E rather than live BOSS PASS.

## Accepted evidence — isolated fixture suite, not live BOSS

- Added `backend/tests/test_extension_e2e.py` (16 cases), fictional `mv3_runner.html`, and
  `scripts/test-extension-e2e.ps1`. The wrapper sets/restores `JOBAGENT_MV3_E2E=1`; ordinary
  pytest skips all 16 (verified), so it cannot silently start experimental extension runs.
- Python Playwright 1.62.0 / bundled Chromium 151.0.7922.34. Temporary profile, actual original
  manifest/build, actual worker/content scripts, TestClient/throwaway SQLite. Allowlisted API
  traffic is served in-process by a loopback proxy that never opens forwarding connections.
  Fixture documents are intercepted; other requests are denied. This is not production
  localhost connectivity proof. No user Chrome/profile/session or production DB is used.
- Before the fix, both modes showed implicit worker `currentWindow` returning no tab while
  the actual popup and windows API could identify the correct normal window. This also
  reproduced in the real start handler, not just debugger-side preconditions. Chrome documents
  that a worker's implicit current window is not guaranteed. Chromium's internal cause was
  not established. Tests now verify the user's starting context in the real popup and pass
  an explicit window ID when testing capture permission, matching production capture behavior.
- Product start now resolves `getLastFocused({windowTypes:['normal']})`, requires focused=true,
  queries exactly that window's single active tab and rechecks window focus. Errors, ambiguity,
  another window or lost focus fail closed. No force-focus, added permission or mock Chrome
  signal. Ten new unit regressions cover the resolution and rejection cases.
- After the fix: all 16 actual-MV3 cases passed together in 67.63 seconds. Then the public
  wrapper independently built and ran 215 Node tests plus 64 backend/DOM/MV3 tests, including
  the same 16 cases: 0 failures/errors/skips, pytest 84.90 seconds. This was two complete
  sequences, not retrying failures until green. No test retry plugin or runner retry added.
- Coverage includes actual injection/activeTab, acknowledgement-close, real crop/local OCR,
  canonical intake/provenance, two controlled scroll rounds/three unique jobs, wrong identity,
  verification, cancel/background-tab discard, pause/resume preserving spent budget, three
  no-new rounds stopping, and network backstop. All pages/jobs are fictional.
- Evidence: `.tmp/mv3-suite-foreground-fix.xml` and
  `.tmp/extension-e2e-a14c689e6fb340f292004ba59908337e/results.xml` (gitignored). The latter records
  Chromium 151.0.7922.34, headless, live_site=false. The wrapper fails on any skipped test.
  Headed mode was not reaccepted after the fix; no Windows desktop-input/focus PASS claimed.
- Synthetic salary uses private-use DOM characters plus CSS-painted digits. This exercises
  real pixel OCR, not decoding or proving compatibility with live BOSS fonts.

### Minimal product regressions found

1. A verification notice after the old first-400-character prefix could be missed and allow
   intake. Added rendered known verification-container checks in selectors/extract; hidden
   templates do not trigger this additional guard. New local DOM regressions: 2/2 PASS.
2. Opening the popup to pause/cancel could change focus and misclassify an acknowledged stop.
   Check durable stop before sticky focus-error classification; both paths discard the image
   and prohibit import. Added two Node regressions. No screenshot guard or permission removed.

Final offline acceptance: TypeScript build PASS; extension Node suite 215/215 PASS; focused
backend/DOM/MV3 64/64 PASS (includes late-verification 2/2 and MV3 16/16); public wrapper PASS.
Source and actual diff reviewed. Existing dirty changes preserved, no commit or push. No real
Chrome/BOSS action or paid AI call. No unrelated full backend suite rerun.

Old jobagent-ocr heartbeat remains deleted. Development tests can now run through
`scripts/test-extension-e2e.ps1` without user interaction. Stop adding features at this point.
Final real salary/focus acceptance needs one reloaded, user-started cap=3 BOSS run plus backend
evidence. No manual scrolling; no live PASS claimed from these fixtures.

## Existing logged-in Chrome

No currently verified path attaches this task to the user's normal Chrome. Playwright's
extension test context is separate; it does not inherit the BOSS login. Do not copy profiles,
cookies or tokens. Do not restart the failed Browser Control/Playwright Extension experiments.
Chrome 136+ ignores remote-debugging switches against its default data directory; arbitrary
connect-over-CDP is not an available attach mechanism for this setup.

User's desired "only final acceptance" is feasible for isolated development tests. Genuine
live BOSS testing additionally needs a working approved connection or the user-started
JobAgent runner and its backend evidence. Login/verification remains human-owned. No hidden
unbounded tasks, automatic applications or paid AI calls are introduced by this study.

## Primary documentation

- [Playwright: Chrome extensions](https://playwright.dev/docs/chrome-extensions)
- [Chrome: end-to-end extension testing and real popups](https://developer.chrome.com/docs/extensions/how-to/test/end-to-end-testing)
- [Chrome: remote-debugging default-profile restriction](https://developer.chrome.com/blog/remote-debugging-port)
- [Chrome DevTools Protocol: extension action trigger](https://chromedevtools.github.io/devtools-protocol/tot/Extensions/)
- [Chrome windows API: implicit current window in service workers](https://developer.chrome.com/docs/extensions/reference/api/windows#the-current-window)
