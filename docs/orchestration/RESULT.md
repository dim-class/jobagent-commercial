# Claude Worker Result

State: implementation — M4b phase-ordering fix after live acceptance failure

## Root cause (confirmed live)

`renderBar()`'s "下一位候选人" (Next) disabled state was computed from cap math alone
(`candidates_extracted >= candidate_cap`), ignoring whether a candidate was already open and
awaiting "捕获详情". Once below cap, Next stayed clickable after every open, so repeated clicks
silently overwrote `pendingCapture` - only the last-opened candidate was ever captured/previewed.

## Fix

`extension/src/overlay.ts`:
- New `nextAllowed(session)` (never allowed while `pendingCapture` is set; otherwise cap-gated) is
  now the single source of truth for Next's disabled state, used by `renderBar()` and by a new
  `refreshNextButton()` called whenever a capture attempt finishes without a full re-render.
- `openNextCandidateStep()` now refuses outright (no page read, no prepare, no click) if
  `pendingCapture` is already set - defense in depth beyond the disabled attribute, since that's
  exactly what the live failure exploited.
- Capture success (`sent.ok`) now clears `pendingCapture` and calls `refreshNextButton()`, so Next
  re-enables only when below cap; `not_loaded` and a failed preview send leave Next disabled and
  only re-enable "捕获详情" for a manual retry.
- `lastSession` tracks the freshest known session (updated on every render and every
  session-snapshot check) so `nextAllowed` can be re-evaluated without a full bar re-render; cleared
  alongside `pendingCapture` on every stop path.

## Tests added

- `extension/tests/m4b-capture-behavior.test.cjs` (+5): repeated Next clicks while one candidate is
  pending touch no backend endpoint at all; a successful capture unlocks Next below cap and the
  next (distinct) candidate can then open; a `not_loaded` capture leaves Next disabled; a failed
  preview send leaves Next disabled; reaching cap on the opening click keeps Next disabled even
  after a successful capture.
- `extension/tests/m4b-overlay-hardstop.test.cjs`: the pre-existing "duplicate/looping candidate"
  test now captures the opened candidate before its second Next click (added `CAPTURE_DETAIL`
  mocking to that file's harness) - otherwise the new pending-capture refusal fires first, which is
  correct but not what that test targets.

## Test / build counts

- `extension`: `npm run build` (tsc) - clean, no errors.
- `extension`: `node --test tests/*.test.cjs` - **43/43 passed** (38 previous + 5 new).
- `backend/tests/test_extension_extraction.py` - **42/42 passed**, unchanged from the prior
  invocation (this delta touched only `overlay.ts` and its tests, not `extract.ts`).
- Full backend suite was not re-run this invocation (budget); no backend product file changed, so
  no regression risk beyond the already-passing focused extraction suite.

## Remaining live verification

Everything above runs against a `vm`-sandboxed load of the real built `dist/overlay.js` and local
fixtures; no live BOSS request was made. The one remaining check: on a real logged-in `/web/geek/jobs`
session, open one candidate, click "下一位候选人" again *before* capturing, and confirm it is now
refused (no second open, Next stays disabled) instead of overwriting the pending candidate as
before; then capture and confirm Next unlocks (or stays disabled at cap, matching the 5/5 case).
