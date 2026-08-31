# Result — M5b bounded cross-task matching, OFFLINE PASS (2026-08-29)

- Added one loopback-only plan/run coordinator and one local-console unified review panel. It accepts
  only an exact 1–20 set of completed SearchPlan tasks and deduplicates their existing associations
  by `job_id` before looking at cache state.
- Planning is read-only and fingerprints the exact task criteria, associations, active-resume
  content, fast model, cache keys/results and configured call ceiling. A stale candidate, task,
  resume, model, strategy/cache state or an inaccurate confirmation fails before a model call.
- One confirmed batch permits exactly the displayed number of uncached calls, never more than 3
  (or `MAX_ANALYSES_PER_RUN` when lower). Cache hits are free; failures consume the batch slot, are
  safely classified and are not retried. All persistence still flows through the existing
  `job_matcher` -> `JobAnalysis` cache path with `mark_reviewed=False` and `no_retries=True`.
- Unified review is score sorted and shows every source task once per unique job. Missing or
  conflicting city, salary, experience or education is surfaced as `待确认`; no score records a
  human decision or changes `Job.status`.
- PASS: focused backend M5a+M5b 22/22; full backend 1630 collected and completed with 29 existing
  browser-dependent skips; frontend TypeScript, 14/14 Node tests and production build. All model
  paths were mocked. No Chrome/extension code, canonical intake, application, favorite, message,
  database schema, live task, real AI call, commit or push was involved.
- Runtime activation PASS: only the backend was restarted (PID 23100 -> 31464). Health/database are
  `ok`; a read-only plan over 12 completed SearchPlan tasks returned 25 unique jobs, 4 cached and 21
  pending, with the correct whole-batch cap 3 and 64-character fingerprint. No run endpoint, model,
  search task, Chrome action, application, favorite or message was invoked. The frontend dev server
  remained running and the new panel is available after refreshing the local console.
- Local UI PASS: the built-in browser loaded the panel, selected all 12 completed tasks and generated
  the read-only plan. The page showed 25 unique jobs, 4 cached, 21 pending, 25 detail links, 4
  `待确认` and 21 `待分析`. The cached rows were score sorted with conflict reasons. The paid
  confirmation remained untouched, so this verification cost zero and made no Job/review changes.

- Authorized runtime confirmation (2026-08-30): that local-console batch then used the explicit
  allowance of at most 3 fast-model calls; the UI reported 3 completed and 0 failed. No browser,
  application, favorite, message or Job.status operation was performed. The allowance is consumed;
  no additional paid call was attempted. Afterward the backend was no longer listening on 8000,
  preventing a fresh read-only plan query.

## Preserved M4g result

- Corrected 0.1.7 live PASS: task #14/session #34 ran first with cap=1, extracted=1 and one scroll;
  after it stopped, task #15/session #35 started with cap=1, extracted=1 and one scroll. Both tasks
  completed without error or pause, proving exact two-task serial advancement and the approved
  per-task budget. Both produced zero canonical associations/imports; no import is claimed.

- First 0.1.6 live evidence is partial only: #7 then #13 proved one-tab serial advancement, but the
  sessions carried cap 3 rather than the acceptance cap 1. #13 extracted three candidates and added
  two canonical associations (#75/#76). 0.1.7 fixes the UI cause by giving batches their own cap,
  default 1; no completed task was reset or retried and the real imports remain intact.
- Pre-live safety review caught that 0.1.5 could only select the first five pending tasks. No live
  batch ran. 0.1.6 adds the missing explicit 1–5 task-count control, defaulting to two, and binds
  stale confirmation validation to that exact count.

- Extension 0.1.7 contains the finite batch pointer in trusted MV3 session storage and a local-console
  confirmation listing the exact ordered pending SearchPlan tasks. It validates all 1–5 tasks before
  opening one foreground BOSS tab and reuses the existing M4f runner/canonical intake sequentially.
- Normal completion alone advances. Failure/cancel stops the batch; verification or pause preserves
  the batch for explicit resume. Service-worker/browser restart performs no browser work until that
  resume, and existing candidate/scroll reservations remain consumed.
- PASS: extension build + 276/276 Node; focused console/runner tests 128/128; frontend production
  build + 14/14 Node. No backend schema/persistence path, paid AI call, live browser task, application,
  favorite, message, commit or push was introduced.
- Live status: PASS for the M4g batch state machine and budget. Canonical intake remains covered by
  the earlier unchanged M4f live path; this particular two-candidate run added no jobs.

## Preserved 0.1.4 live evidence

- Extension 0.1.4 reports fixed startup failure codes and clears the runner/backend state when its
  newly opened tab disappears. It does not broaden permissions, wait longer, auto-retry, or close a
  BOSS tab.
- The free start/resume confirmation is now an accessible in-page modal. Cancel/Escape and stale
  task snapshots are inert; confirmation sends one command and keeps all existing gates/budgets.
- Live 0.1.2 reached the exact BOSS search URL but failed safely with
  `stabilization_timeout:content_unavailable`, zero scrolls/candidates/imports. The automatic runner
  lacked the popup's existing recovery for a missing post-Reload content script. 0.1.3 reuses that
  exact-host recovery: one packaged-script injection and one retry, with foreground/origin checks
  before and after. It adds no permission and does not extend the ten stabilization polls.
- Live 0.1.3 passed navigation/content recovery and produced real bounded progress: 15 observed,
  one scroll and one canonical import (#74), then stopped because an intake-incomplete second job
  was mislabeled `missing_job_identity`. 0.1.4 consumes that failed candidate slot and continues;
  truly malformed duplicate identity responses still stop fail-closed.
- PASS: extension build + 269/269 Node; frontend build + 12/12 Node; 9/9 focused actual React/MV3
  fixture scenarios. `.tmp/startup-acceptance-20260829.xml` recorded eight passes plus one test-only
  enum typo; `.tmp/startup-stale-20260829.xml` confirms both corrected stale-dialog cases pass.
- Live 0.1.4: task #6 (Beijing / Platform Engineer, cap 3) completed automatically after four
  controlled scrolls: observed=8, new=2, duplicate=6, no_new_rounds=3, imported=0, with no error or
  pause. The two unique results did not produce canonical candidates, so this is not represented as
  an import. Together with task #5's real canonical association to job #74, the unchanged runner path
  has live evidence for automatic navigation, repeated scroll/discovery/dedup, detail processing,
  canonical intake and bounded completion. No CAPTCHA, paid AI, application, favorite or message.
- Review: scoped `git diff --check` PASS before live execution; the accepted bundle was unchanged
  during live testing. No task reset/retry, new driver, commit or push. Chrome 0.1.4 is active; no
  further Reload is required now. The earlier BOSS-tab disappearance remains unproven; repository
  code has no BOSS-tab close call.
