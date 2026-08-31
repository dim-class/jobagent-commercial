# Project operating authorization — 2026-08-29

The user authorized both Codex development autonomy and JobAgent search execution,
then asked to continue. This records task scope, not an OS/browser permission grant.

- Codex works directly in this repository: inspect, make scoped changes, build, test,
  diagnose and manage JobAgent's local services when needed. Preserve existing edits.
  Request tool approval when required; do not repeatedly ask conversational approval
  for ordinary steps already within the authorized milestone.
- Keep normal Chrome -> JobAgent MV3 -> loopback FastAPI -> canonical job_intake.
  The extension owns search/navigation, bounded scrolling, card/detail extraction,
  identity checks, deduplication, intake, progress and pause/cancel.
- Daily search starts from the local console, not a sequence of popup clicks. One
  explicit confirmation may authorize either one task or an exact finite M4g batch of
  1–5 already-pending tasks. This is not a scheduler: keep one foreground tab/task,
  <=20 candidates and <=5 scroll rounds per task, and never auto-resume after restart.
  The first M4g live acceptance requires a separate visible confirmation and is capped at
  two tasks with one candidate attempt each.
- Login/verification remains human-owned. Do not bypass CAPTCHA, site blocks or tool
  policy denials. Never read browser credentials, cookies or tokens. No paid AI,
  automatic applications, favorites or messages are authorized by this statement.
- No commit/push, destructive cleanup, global administrator access, disabled sandbox,
  all-sites extension permissions, new browser drivers or security weakening is implied.
  Codex's effective sandbox and approval policy remain controlled by the host app.

## Current handoff

User screenshots confirmed enabled 0.1.1 at F:\jobagent\bossagent1.0\extension, and normal
Chrome console reported a compatible ready handshake. Task #11 (Beijing / infrastructure), cap 3,
then failed after ~4.60s with the older generic stabilization_timeout before scroll/intake.
It remains failed with zero candidates and was not reset or retried.

Package 0.1.4 is live-accepted. Package 0.1.7 contains the corrected authorized M4g batch runner and
is live-accepted for a two-task cap-1 batch. 0.1.4 replaced the native console confirmation with an
in-page stale-safe modal and distinguishes startup tab/origin/foreground/content failures. The
earlier BOSS tab disappearance is not attributed; repository code has no BOSS-tab close call.
Live 0.1.2 reached task #4's exact BOSS search URL but failed safely before scroll/intake because
the new page had no responsive content script. 0.1.3 reuses the popup's existing exact-host,
foreground-rechecked, single packaged-script injection recovery. One human Chrome extension Reload
enabled #5 to observe 15 jobs, scroll once and canonically import job #74. An intake-incomplete
second candidate then exposed a false terminal `missing_job_identity`; 0.1.4 consumes that candidate
slot and continues while retaining terminal handling for malformed duplicate identity responses.
After the human Reload, #6 automatically completed four scroll rounds with 8 observations, 2 unique
jobs, 6 duplicates, 3 no-new rounds and no error. It imported zero candidates because none met the
canonical intake contract; #5 remains the live canonical-import evidence (job #74). No further Reload
is currently required. The tool previously blocked chrome://extensions; no alternate control path is
allowed to achieve that denied operation.
