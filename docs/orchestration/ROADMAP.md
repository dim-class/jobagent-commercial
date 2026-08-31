# JobAgent Orchestration Roadmap

Planning document only. No product code changes ship with this file.

## Purpose

求职任务控制台 is the foundation of the target end-to-end flow:

```
search criteria  ->  candidate intake  ->  human review  ->  (later, gated) supervised execution
```

It is not merely a cross-domain "what needs attention" dashboard. An
attention overview across capture/queue/recruiter/interview/offer is a
useful **later subview** of the console, not M1's identity — the existing
per-domain lifecycle and analytics pages already do that job well and stay
the primary UI for those domains; the console's own scope is the search →
intake → review pipeline described below.

## Existing capabilities (verified against the repo)

- **Capture:** manual paste, Quick Capture (`services/quick_capture.py`),
  headed browser capture (`job_sources/boss/*`), Chrome extension POC
  (`extension/`, read-only, explicit click, no click/scroll/paginate).
- **Analysis:** `JobMatchAgent` + `scoring.py` deterministic guardrails,
  cached by `analysis_cache_key`.
- **Application:** `application_queue.py` (derived, not stored),
  `application_workflow.py` (the only place `Job.status` changes, always
  human-confirmed), append-only `ApplicationEvent` trail.
- **Recruiter, interviews, offers, decision, analytics:** as already
  documented in `CLAUDE.md` (v0.5–v1.0) — all downstream of a `Job` that
  already exists in the database. **These stay strengths to build on, not
  things M1 replaces or re-implements.**
- **Frontend:** one page per domain, no page that starts from "what should
  I search for" and carries that intent through capture and review.

## What is missing today

- No persisted notion of a **search task**: keywords, city, experience,
  education, salary band, exclusions, resume variant, a candidate cap, a
  minimum score, or an explicit mode. Every existing intake path (manual
  paste, Quick Capture, browser capture, extension) is fired ad hoc, per
  posting, with no standing configuration behind it.
- No review surface that shows "candidates gathered under task X" as a
  batch, distinct from the global `JobsPage` / queue.
- No documented, gated path from "reviewed candidates" to any kind of
  execution beyond what a human already does by hand today (open BOSS,
  read, decide). Anything resembling the extension or browser *navigating
  on the task's behalf* is out of scope until explicitly authorized — see
  "Later stages" below.

## Safety gates (apply to every milestone, without exception)

1. **Read-only until a human explicitly confirms.** Carried forward from
   the whole codebase; no milestone weakens it.
2. **No new capability may click, scroll, paginate, search, apply, or
   message** on a recruitment site. A task's "search criteria" are stored
   configuration for the human to apply themselves (or to filter/prioritize
   candidates already captured through existing, human-driven intake) —
   never a trigger for the system to open or drive a browser.
3. **`Job.status` changes only through `application_workflow.py`.**
4. **Read before write; migrate deliberately.** If a milestone genuinely
   needs persistence, it gets a real model and an Alembic migration — see
   M1 below for why zero-schema is not honest here.
5. **Fixture-tested before "done."** No milestone touches zhipin.com, adds
   Playwright/CDP automation, or is marked complete without a passing
   fixture-based test for its new behavior.
6. **Any future supervised-navigation capability requires an explicit
   CLAUDE.md policy change and separate authorization before design work
   starts**, not just a milestone entry in this roadmap. See "Later
   stages."
7. **STATUS.md reflects reality before the next milestone starts.**

## Staged milestones

- **M1 — 求职任务控制台: task configuration + candidate review.** A
  persisted search task (criteria) and a review view over the candidates
  captured under it. Fully specified below.
- **M2 — 属地任务视图整合 (attention subview).** Add the cross-domain "待处理"
  overview (analysis pending / queue / recruiter / interview / offer) as a
  **subview inside the console**, read-only, built the same way previously
  drafted (composed from existing services, no new writes). Demoted from
  M1's identity to an M2 addition per this revision.
- **M3 — 编排会话记录 (orchestration session log).** Persist **explicit human
  actions only** — a human clicking "标记已打开 / 已复核 / 已忽略" on a task or
  one of its candidates — as an additive, append-only audit log. **Not**
  passive tracking: no page-view timers, no "viewed for N seconds", no event
  fired automatically on navigation or page load. Reuses the v0.6 analytics
  honesty rules (no invented rates, no derived conclusions — raw counts and
  a chronological history only). No effect on `Job.status`. Fully specified
  below.
- **M4 — supervised, then bounded-automatic, extension navigation. Implemented
  and live-verified.** Every stage (M4a session scaffolding, M4b bounded
  card/detail navigation, M4c continuous-list bounded scrolling, M4d
  canonical incremental discovery/dedup, M4e deterministic SearchPlan
  generation, M4f the extension-owned bounded automatic runner plus
  `TaskCandidate` provenance) went through its own separate, explicit user
  authorization naming the relevant `CLAUDE.md` policy section before any
  code was written, exactly as this roadmap originally required. See
  "Milestone 4" below for the completed scope and live evidence. **No
  further extension milestone (M5+) is authorized.** The unconditional
  boundaries — no apply, no message, no follow/collect, no stealth, no
  Playwright/CDP — remain in force and are not part of any gated exception.

Each milestone requires the previous one merged and tested first.

## Milestone 1 — 求职任务控制台 (full spec)

### User flow

1. Human opens `/console` and creates a **task**: keywords, city,
   experience, education, salary range, exclusion keywords/companies, the
   resume variant to match against, a max-candidates cap, a minimum score
   threshold, and an explicit **mode** (e.g. `manual_review_only` — the only
   mode M1 implements; any other mode is out of scope until M4's gate
   clears).
2. The human still captures postings themselves through the existing,
   unchanged paths (Quick Capture, browser capture, the extension) — M1
   adds **no new capture mechanism**. What M1 adds is a way to tag which
   task a captured job belongs to and see them grouped.
3. The console shows candidates gathered under a task, each with its
   existing `JobAnalysis` score/verdict once analyzed (analysis itself is
   unchanged — same manual "AI分析" click as today).
4. The human reviews and acts through the existing pages (job detail,
   queue, apply); the console links out, it does not duplicate those
   actions.

### Scope

- **New persistence is required and is being stated honestly, not avoided:**
  a `JobSearchTask` model holding the criteria listed above, plus a
  many-to-many **`TaskCandidate`** association table (`task_id`, `job_id`,
  unique on the pair) linking tasks to jobs. **Not a `task_id` column on
  `Job`:** the repo globally deduplicates `Job` by `content_hash` — the
  same posting can legitimately be discovered under two different tasks
  (different keywords/cities matching the same job), and a nullable
  `Job.task_id` would force a single owning task per job, silently losing
  provenance for every other task that also found it. The association
  table preserves per-task provenance without duplicating or re-owning the
  `Job` row. This needs a new Alembic migration.
  A zero-schema, purely-derived design was considered and rejected: task
  criteria are configuration a human sets once and expects to persist
  across sessions and future capture runs, which a derived-only view
  cannot provide.
- One new backend surface: create/list tasks, and list candidates by task
  (composing existing `Job` / `JobAnalysis` queries, not new scoring logic).
- One new frontend page/section for task creation and per-task candidate
  review.

### Non-goals (explicit)

- No automated search, crawling, or capture triggered by creating a task.
  A task is configuration and a grouping key; it does not act on its own.
- No click, scroll, paginate, search, apply, or message automation of any
  kind — not in M1, and not implied for later milestones without the M4
  gate above being cleared first.
- No new AI call beyond the existing, unchanged, human-triggered "AI分析."
- No execution mode besides `manual_review_only` in M1.
- No replacement of `JobsPage`, `ApplicationQueuePage`, or any lifecycle
  page — they remain the systems of record for their domains.

### Data / API / UI boundaries

- **Data:** new `JobSearchTask` table (criteria as listed) + a many-to-many
  `TaskCandidate` association table with `UNIQUE(task_id, job_id)`. Global
  `Job` deduplication remains unchanged. Alembic migration required, following the existing
  hazards documented in `CLAUDE.md` (plain `ADD COLUMN`/`CREATE TABLE`,
  repeat server defaults, avoid SQLite batch-mode table rebuilds where a
  cascade exists).
- **API:** `POST /api/tasks`, `GET /api/tasks`, `GET /api/tasks/{id}`,
  `POST /api/tasks/{id}/candidates`, and `GET /api/tasks/{id}/candidates`
  (composes existing `Job`/`JobAnalysis` reads, filtered by task
  association). No endpoint performs a capture or a status change.
- **UI:** a task-creation form (the fields listed above) and a per-task
  candidate list reusing existing job-row components; links out to existing
  detail/queue pages for any action.

### Migration implications

Real and non-trivial: a task table plus a join table with two foreign keys
and `UNIQUE(task_id, job_id)`, using the usual v0.4+ Alembic discipline.
This is called out explicitly rather than
claimed as a zero-schema change, because task configuration must survive
across sessions and future capture runs to be useful at all.

### Acceptance tests

Backend (pytest, fixtures/seeded data only, no network, no OpenAI call):

- Creating a task persists exactly the submitted criteria; omitted optional
  fields (e.g. exclusions) are stored as absent, never defaulted to an
  invented value.
- The same globally deduplicated job can appear under two tasks without a
  duplicate `Job` row.
- Associating the same job with the same task twice is idempotent and leaves
  one `TaskCandidate` row.
- `GET /api/tasks/{id}/candidates` reflects each job's real, current
  `JobAnalysis` (or its absence) — never a fabricated score.
- No endpoint under `/api/tasks` triggers `run_job_match` or any OpenAI
  call.
- No endpoint under `/api/tasks` changes `Job.status`.
- Alembic upgrade/downgrade round-trips cleanly on a fresh DB and on the
  existing seeded fixture DB.

Frontend:

- `npm run build` passes (type-checks the new page/form).

## Milestone 3 — 编排会话记录 (full spec)

### Contract

An `OrchestrationEvent` row is written **only** in direct response to a human
clicking an explicit console control — "标记已打开" (opened), "标记已复核"
(reviewed), or "标记已忽略" (dismissed) — on a task or on one of its
candidates. There is no other producer of this table:

- No event fires on component mount, route navigation, or a task/candidate
  being merely rendered on screen.
- No event fires on a timer, an interval, or any polling loop — M3 adds
  none of those, matching M2.
- No "time spent" / "scroll depth" / "last seen" telemetry of any kind —
  only the three explicit action types above exist.
- Selecting a task to view its candidates (M1's existing `selectTask`) does
  **not** itself write an event; only pressing a dedicated M3 control does.

This is a deliberately narrow definition: M3 is a human-authored audit trail
of console review decisions, not a usage-analytics or session-recording
feature.

### Scope

- **New, additive persistence:** an `OrchestrationEvent` table — `task_id`
  (required FK to `job_search_tasks`, `CASCADE`), `job_id` (optional FK to
  `jobs`, `CASCADE` — `NULL` means a task-level event such as "opened the
  task itself" rather than one of its candidates), `event_type` (`opened` /
  `reviewed` / `dismissed`), an optional human `note`, and a server-assigned
  `created_at`. Rows are **never** updated or deleted — corrections are new
  rows, exactly like `ApplicationEvent`. A new Alembic migration adds this
  table only; nothing existing is altered.
- When `job_id` is present, the service validates that `(task_id, job_id)`
  is an existing `TaskCandidate` association before writing — an event
  about a candidate that was never associated with that task is rejected,
  not silently recorded.
- One new backend surface: append and list events for a task
  (`/api/tasks/{id}/events`), composing `task_console` for task/candidate
  lookups — no new lifecycle or scoring logic.
- One new console UI addition: explicit per-task and per-candidate controls,
  plus a compact, honest history (raw event list, no computed rates or
  conclusions) — reusing the v0.6 analytics honesty rules.

### Non-goals (explicit)

- No passive tracking of any kind (see Contract above).
- No polling or timer to refresh the history automatically — it reloads
  when a human takes an action or re-opens the task, exactly like M1/M2.
- No effect on `Job.status`, `ApplicationEvent`, or any other existing
  lifecycle record — this is an independent, additive log.
- No AI call, capture, browser/site action, search, apply, or messaging.
- No editing or deleting a recorded event — corrections are new rows.

### Data / API / UI boundaries

- **Data:** new `orchestration_events` table as described above. Alembic
  migration required, following the same additive-only discipline as 0009
  (plain `create_table`, no rebuild of an existing table).
- **API:** `POST /api/tasks/{id}/events` (body: `event_type`, optional
  `job_id`, optional `note`) and `GET /api/tasks/{id}/events` (chronological,
  oldest first, mirroring `GET /api/jobs/{id}/application-events`). Both 404
  on a missing task; the POST also rejects a `job_id` that is not a
  candidate of that task.
- **UI:** explicit buttons inside `ConsolePage`'s existing task/candidate
  views (no new page) and a compact history list scoped to the selected
  task, with loading/error/empty states and no invented summary text.

### Acceptance tests

Backend (pytest, fixtures/seeded data only, no network, no OpenAI call):

- Appending an event with a valid `(task_id, job_id)` association persists
  exactly the submitted `event_type`/`note`, with a server-assigned
  `created_at`.
- A task-level event (`job_id` omitted) persists with `job_id = NULL`.
- Appending an event for a `job_id` that is not a candidate of that task is
  rejected (422), and writes nothing.
- Appending an event for a missing task 404s.
- `GET /api/tasks/{id}/events` returns events oldest-first and never
  includes another task's events.
- Rows are append-only: nothing in this milestone updates or deletes an
  `OrchestrationEvent` row.
- No endpoint under `/api/tasks/.../events` triggers `run_job_match`, any
  OpenAI call, or a `Job.status` change.
- Alembic upgrade/downgrade round-trips cleanly on a fresh DB and on the
  existing seeded fixture DB.

Frontend:

- `npm run build` passes (type-checks the new controls/history).

## Milestone 4 — supervised, then bounded-automatic, navigation (full spec)

**Implementation status: complete and live-verified.** Every stage below
(M4a-M4f) received its own separate, explicit user authorization naming the
`CLAUDE.md` "Chrome extension — M4 supervised navigation policy" section (and
its M4e/M4f amendment) before any code for that stage was written, exactly as
this spec originally required.

Completed scope:

- **M4a** — bounded-session scaffolding: human-approved criteria/caps/tab,
  start/stop, progress indicator. No navigation yet.
- **M4b** — bounded navigation to search/results/detail pages and result-card
  selection, one human click at a time (prepare-then-confirm), plus the
  two-phase open/capture/loopback-preview flow for one candidate.
- **M4c** — bounded continuous-list scrolling within a results page, under the
  same caps. Real logged-in Chrome verification found BOSS's `/web/geek/jobs`
  is one continuous scroll list with no pagination control at all, so
  pagination was removed rather than shipped permanently broken — M4 has
  never included page-number navigation on BOSS.
- **M4d** — canonical, query-free incremental card discovery/dedup across
  scroll rounds (`/job_detail/<id>.html` identity only).
- **M4e** — deterministic SearchPlan generation (city × keyword, no OpenAI
  call) and its own loopback API/state machine.
- **M4f** — the extension-owned bounded automatic runner: one human-started
  SearchTask navigates, discovers cards, scrolls (still capped at 5 rounds/20
  candidates/one foreground tab), opens and identity-checks each detail pane,
  imports through the unchanged `extension_intake.py` -> `job_intake` path,
  and associates each resulting job with the running `JobSearchTask` via the
  existing `TaskCandidate` endpoint (new imports by `job_id`, global
  duplicates by the preview's `existing_job_id` — one `Job` may belong to
  multiple tasks). Verification/CAPTCHA/login/risk signals pause immediately
  with no bypass; a bounded SPA detail-transition delay is tolerated by
  retrying the identity check within the existing capture-wait ceiling before
  skipping just that one candidate.
- **M4g** — one explicitly confirmed finite batch of 1–5 already-created pending
  SearchPlan tasks. It validates the full ordered list before browser work, reuses
  one foreground tab and the unchanged M4f runner serially, and never schedules or
  auto-resumes. Corrected implementation and cap-1 live acceptance are complete in 0.1.7.

Live evidence (task #10, logged-in Chrome): 3 controlled scroll rounds, 60
observed cards, 30 new / 30 duplicate, 14 new imports, 16 unique
`TaskCandidate` associations (the 2 extra beyond the 14 imports are global
duplicates linked without re-import) — every associated job carries a
canonical query-free BOSS detail URL, an external ID, and a non-empty JD; no
error and no unresolved pause during the run.

**No post-M4 extension milestone (M5+) is authorized.** This spec, and the
unconditional boundaries below, remain the frozen scope until a future
milestone gets its own separate, explicit authorization — apply, 立即沟通,
messaging, follow/collect, and any stealth/fingerprinting/Playwright/CDP
mechanism stay forbidden regardless.

### Policy basis

Every rule in this milestone is defined in `CLAUDE.md` under "Chrome
extension — M4 supervised navigation policy." That section is the source of
truth; this spec breaks it into stages and acceptance tests. If the two ever
disagree, `CLAUDE.md` wins and this file needs fixing.

### Contract (restated from CLAUDE.md, for staging purposes only)

- A human starts a bounded session and approves its criteria, page/candidate
  caps, and foreground tab before anything navigates.
- Inside that session, the extension may navigate BOSS search/results/detail
  pages, select/open a result card, and scroll/paginate — strictly bounded by
  the approved caps.
- It extracts structured job fields only, through the existing
  `extension_intake.py` -> `job_intake.save_posting()` path. No new
  persistence path.
- No auto-start, no resume-after-stop, no timer/poll loop, no hidden/
  background browsing, no mass scraping, no credential access, no
  Playwright/CDP/native-messaging-host mechanism.
- Immutable POC ceilings, enforced in code, that a human's approval may
  lower but never raise: one concurrent session, one visible foreground
  tab, max 3 results pages, max 20 candidate jobs, max 5 scroll steps per
  results page.
- Every navigation target's scheme+host must equal exactly
  `https://www.zhipin.com` (the host already in
  `extension/manifest.json`'s `content_scripts.matches`) — fail closed,
  stop the session, on any other scheme or host.
- Never apply, click 立即沟通, message, follow/collect, or change account
  state — unconditional, not part of the gated exception.
- Any CAPTCHA/verification/login/security warning, unexpected origin,
  selector ambiguity, navigation loop, cap reached, user stop, or site
  blocking ends the session immediately; no bypass or stealth, ever.

### Staged implementation — all stages complete and live-verified

- **M4a — session scaffolding, no navigation.** Done: the session approval
  UI, start/running/stopped state machine, stop control and progress
  indicator.
- **M4b — bounded navigation.** Done: navigation to search/results/detail
  pages and result-card selection, gated by the approved caps and every hard
  stop in the contract above, extraction through the unchanged
  `extension_intake.py` path.
- **M4c — bounded continuous-list scrolling.** Done. Real logged-in Chrome
  verification found BOSS's results page has no pagination control at all
  (one continuous scroll list), so pagination was removed rather than left
  permanently broken; scrolling stayed bounded by the same caps.
- **M4d — canonical incremental discovery/dedup.** Done: query-free
  `/job_detail/<id>.html` identity across scroll rounds.
- **M4e — deterministic SearchPlan generation.** Done: city × keyword
  expansion with its own loopback API, no OpenAI call.
- **M4f — extension-owned bounded automatic runner + TaskCandidate
  provenance.** Done: one human-started SearchTask runs unattended within
  the same caps, imports through the unchanged `job_intake` path, and
  associates every resulting job with the running task via the existing
  `TaskCandidate` endpoint. Live-verified on task #10 (see above).
- **M4g — bounded SearchPlan batch runner.** Offline complete: one confirmation,
  at most five pending tasks, one tab/task at a time, advance only on normal
  completion, and stop/pause the whole batch on every existing hard-stop signal.
  It reuses M4f state and intake. The corrected 0.1.7 live batch ran #14 then #15 with separate
  supervised sessions, cap=1 and one extracted candidate each; both completed without error.

Each stage was implemented, fixture-tested, and manually verified by the user in
their own logged-in Chrome before the next stage started, matching the
existing `extension/README.md` posture that live compatibility is a user
claim, not an automated one. No further stage (M5+) is authorized.

### Non-goals (explicit, unconditional — no stage of M4 changes these)

- No apply, 立即沟通, message send/follow-up, follow/collect, or account-
  state change, ever.
- No Playwright, CDP, or native-messaging-host automation — the extension's
  own content script is the only mechanism.
- No timer, poll loop, or any trigger other than the human starting/
  continuing an approved, bounded session.
- No credential, cookie, storage, form, or auth-header access.
- No claiming an action occurred that did not.

### Data / API / UI boundaries

- **Data:** the session's approved criteria/caps reuse M1's `JobSearchTask`
  shape where they overlap (keywords, city, caps) rather than inventing a
  parallel config model; a session's navigation/extraction steps append to
  an `OrchestrationEvent`-shaped audit trail (M3) — the exact schema is
  designed at M4a time, not here, but it carries no raw HTML and no token.
- **API:** new endpoints are additive only, loopback-only like the existing
  extension endpoints, and never a second persistence path — imports still
  go through `job_intake.save_posting()`.
- **UI:** the session approval/progress/stop UI lives in the extension
  popup/content-script surface, not a new backend page; the console (M1-M3)
  may link to session history the same way it links to other domains today.

### Acceptance tests (per stage, before that stage is "done")

- Fixture-only: every navigation/extraction/hard-stop path is exercised
  against local HTML fixtures in a headless Chromium page, the same pattern
  `job_sources/boss` and the current extension extraction tests already use.
  No automated test touches zhipin.com, at any stage.
- Every hard stop in the contract is a separate test: CAPTCHA/verification
  interstitial, a navigation target whose scheme+host is not exactly
  `https://www.zhipin.com` (fail closed), ambiguous selector, navigation
  loop, an approved cap or an immutable ceiling reached, user stop,
  site-blocking response — each must end the session with no retry and no
  further navigation.
- A test asserts each immutable ceiling (1 session, 1 tab, 3 pages, 20
  candidates, 5 scroll steps/page) is enforced even when a human-approved
  value would be higher, and that a lower human-approved value is honored.
- A test asserts the built bundle contains no cookie/`localStorage`/
  `sessionStorage`/form/auth-header read, the same grep-the-bundle test the
  current extension already has.
- A test asserts no apply/message/follow/collect call site exists anywhere
  in the session code path.
- A test asserts every navigation step is bounded by the approved cap and
  the session cannot exceed it even if the site would allow more.
- Live BOSS compatibility is never asserted by an automated test; it is
  recorded as a manual user claim, exactly like `extension/README.md`'s
  existing detection claim.

## Milestone 5a — task-scoped candidate matching + human review

**Implementation status: complete.** Explicitly authorized as its own, separate delegation - distinct
from the M4 Chrome-extension gate above ("No further extension milestone (M5+) is authorized" refers
only to *browser automation* stages; M5a never touches the extension, a browser, or `Job.status`).

### Scope

- `backend/app/services/task_matching.py`: scores a task's existing `TaskCandidate` associations
  against the active analysis resume and the fast model, reusing `job_matcher`'s cache/guardrail
  pipeline verbatim - no parallel persistence or scoring path. `plan_task_match` is a pure read;
  `run_task_match` is the only spending path, gated by an explicit `confirmed=true` and bounded by
  the configured `settings.max_analyses_per_run` (never hard-coded). Each candidate analyzes
  independently - one failure never aborts the run.
- New routes: `GET /api/tasks/{id}/match-plan`, `POST /api/tasks/{id}/match-run`.
- Console UI (`frontend/src/pages/ConsolePage.tsx`): an explicit "生成匹配计划" read, then an
  explicit "确认分析" (confirmed) run - never triggered on page load or task completion. Associating
  a new candidate invalidates the currently shown plan so a confirm click can never use a stale call
  count. Once a plan exists, every candidate's score/verdict/cache status comes from that plan's own
  active-resume + fast-model result, not an unrelated "latest analysis"; candidates sort
  score-descending with unanalyzed candidates last; the task's `min_score` is an optional visible
  filter. Reviewed/dismissed counts derive each candidate's *latest* `reviewed`/`dismissed` event
  (M3's `OrchestrationEvent`, oldest-first) - a task-level event never counts toward a candidate, and
  repeated clicks never inflate the count.

### Non-goals (explicit, unconditional)

- No apply, 立即沟通, message send, follow/collect, or account-state change.
- No browser/site action of any kind - this milestone is backend + console UI only.
- No automatic smart-model use - only the fast model, exactly like bulk analysis.
- No `Job.status` mutation and no new persistence/scoring pipeline - every score still lands in the
  existing `JobAnalysis` table through the existing `job_matcher.analyze_job`.
- **No post-M5a milestone is authorized.** A future M5b/M6/etc. needs its own separate, explicit
  authorization, exactly like every milestone before it.
