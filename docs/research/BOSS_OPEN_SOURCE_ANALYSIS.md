# BOSS open-source reference analysis

Date: 2026-08-25

## Scope and method

This is a read-only design study. It does not merge code or change the product architecture:

```text
Normal Chrome -> JobAgent MV3 extension -> 127.0.0.1 FastAPI -> canonical job_intake
```

The four repositories were cloned under the gitignored
`references/boss-projects/` directory and inspected at these commits:

| Repository | Commit | Evidence available |
| --- | --- | --- |
| [`h1077/BossJob-Helper`](https://github.com/h1077/BossJob-Helper) | `e1fc6b37b52a336ad04ca1a6b9a7b0394ed7160a` | README only; the source files named by the README are absent |
| [`Autumn-Rains-yan/BossAutoExtension`](https://github.com/Autumn-Rains-yan/BossAutoExtension) | `dc4878bfa94caa3d043ca6afd30eebaf77ef7d39` | MV3 extension source |
| [`DINQ-labs/smart-job`](https://github.com/DINQ-labs/smart-job) | `0d794035f977bbbf70a6e2e390047cf9380d4eeb` | extension, gateways and task engine source |
| [`czc6666/czc-good-job`](https://github.com/czc6666/czc-good-job) | `e07bf4cb663d9531864ca22eb6195b77e064c3b5` | userscript and FastAPI source |

No selector below is treated as live proof merely because it appears in another project. Our
logged-in Chrome output remains the compatibility authority. No anti-detection, fingerprint,
CAPTCHA bypass, credential/cookie extraction, private security-token flow, stealth, automatic
application or messaging mechanism is recommended.

## Executive conclusion

The best proven model for the BOSS page the user showed is **one continuously growing result list,
where selecting a left-hand card populates a right-hand detail panel**. It is not pagination.

Our current implementation already has the correct safe primitive: obtain the canonical detail URL
from a rendered card, click exactly that card after an explicit human action, retain the exact card
as pending identity, then merge it with the loaded detail panel and send the result to local
FastAPI. The minimum next improvement is to make collection state aware of **unique canonical job
URLs observed before and after each explicit bounded scroll**. A zero-new-URL result is an
end-of-current-batch signal; it must not trigger automatic scrolling, retries, keyword switching or
private API calls.

## A. Live BOSS DOM extraction

### Useful selectors and extraction mechanisms

| Repository / file | Mechanism | Difference from JobAgent | Decision |
| --- | --- | --- | --- |
| BossAutoExtension `extension/content.js` | Result roots `.job-card-wrap`, `.job-card-box`, `.job-card-wrapper`; title `.job-title .job-name` / `.job-name`; company `.company-name`; salary `.job-title .job-salary` / `.job-salary`; tags `.tag-list li`. | JobAgent has narrower card roots plus an already-strong fallback that discovers and deduplicates `a[href*="/job_detail/"]` and climbs only within one-card ancestry. | **Adapt** the missing short card-root candidates as fallbacks, but preserve URL-anchor discovery as the authoritative fallback and keep all selectors centralized in `selectors.ts`. |
| BossAutoExtension `extension/content.js` | Excludes detail-panel anchors while enumerating result cards: `.job-detail-box, [class*="job-detail-box"]`. | JobAgent already excludes anchors inside `.job-detail-box`. | **Adopted already**; retain and test. |
| BossAutoExtension `extension/content.js` | Detail root `.job-detail-box`; title `.job-name`; salary `.job-salary`; company `.company-info .name, .company-name`; city `.job-area, .company-location`; tags `.tag-list li`. | JobAgent standalone detail selectors are live-verified and more specific. Its search-panel merge can use card values when the panel omits them. | **Adapt selectively** as late, detail-root-scoped fallbacks only. Never use unscoped generic selectors. |
| BossAutoExtension `extension/content.js` | Description: find a heading matching `职位描述|岗位职责|工作内容`, read its adjacent `.desc`; then broad `.desc`, `[class*='desc']`, `.job-sec-text`. | JobAgent's live `.job-detail-box p.desc` and `.job-detail-section .job-sec-text` are already verified. | **Adapt** only the heading-adjacent fallback inside a confirmed detail root. **Reject** global `[class*='desc']` and body-wide fallback because they can capture recommendations or unrelated text. |
| czc-good-job `web_script.js` | Search `.rec-job-list`, `.job-card-box`, `.job-card-box .job-name`; detail `.name`, `h1`, `.salary`, `.job-sec-text`. | Fewer fields and much looser detail selectors than JobAgent. | **Reject** generic `.name`/`h1`; the list and job-name selectors are useful only as corroborating fallbacks. |
| BossJob-Helper `README.md` | Claims `extension/src/selectors.js` and content scripts. | The pinned checkout contains only `README.md`; selectors cannot be inspected or verified. | **Reject as code evidence**. Treat only as an architectural claim. |
| smart-job `extensions/job-seeker/lib/ext-core/bosszp/api.js` and `commands/jobs.js` | Reads job objects from BOSS private `/wapi/` requests and chains `securityId`/token state. | JobAgent reads visible DOM only and never reads cookies, tokens, request state or private APIs. | **Reject completely**. This is neither a DOM-selector reference nor compatible with our privacy/policy boundary. |

### Field-by-field recommendation

| Field | Best safe evidence | Recommendation for JobAgent |
| --- | --- | --- |
| title | Our live `.job-banner .name h1` and `.job-detail-box .job-name`; reference `.job-title .job-name` for cards | Keep current order; add only scoped card-root coverage if a live miss proves necessary. |
| company | Our live `.job-boss-info .boss-info-attr`; card `.company-name`; reference panel `.company-info .name` | Prefer exact card company during search-panel merge, then scoped panel fallback. |
| salary | Our live `.job-banner .salary`; card `.job-salary`; references confirm the same class family | Keep current special-font rejection. Card value should continue to win over a corrupted panel value. |
| city | Our live `.text-city`; card `.job-area` / `.company-location`; reference panel same classes | Preserve card-first merge and tag parsing; add panel fallback only under `.job-detail-box`. |
| experience | Our live `.text-experiece`; card/panel `.tag-list li` | Parse semantic token patterns, not positional `li` indexes. |
| education | Our live `.text-degree`; card/panel `.tag-list li` | Parse the known education vocabulary, not positional indexes. |
| description | Our live `.job-detail-box p.desc` / `.job-sec-text`; reference heading-adjacent `.desc` | Add a bounded heading-adjacent fallback inside the detail root only if live evidence requires it. |
| job ID | All useful DOM projects ultimately rely on `/job_detail/<id>.html` or a job-id attribute | Continue deriving `external_id` from the query-stripped card/detail URL. Never use `securityId`. |
| search cards | BossAutoExtension card roots plus czc-good-job `.rec-job-list`; canonical job-link anchor is common | Treat canonical URLs as identity; DOM root classes are accelerators, not identity. |

## B. Search execution

| Repository / file | Mechanism | Difference from JobAgent | Decision |
| --- | --- | --- | --- |
| BossAutoExtension `extension/popup.js`, `extension/content.js` | Builds `https://www.zhipin.com/web/geek/jobs?city=<id>&query=<encoded keyword>` with `URL`; accepts city IDs or extracts `city` from a supplied URL. | JobAgent currently starts from a human-opened page and does not execute a search. | **Adapt later, with separate authorization**: construct one validated same-origin URL from an approved keyword and approved city ID, then navigate only on one explicit user click. No loose DOM search-button guessing. |
| smart-job `packages/agent-gateway/tasks/steps/_geo.py` | Maps common Chinese city names to nine-digit BOSS city codes; unknown names silently fall back to Beijing. | JobAgent task data stores human-readable city text. | **Adapt the mapping as local data**, but reject silent Beijing fallback. Unknown/ambiguous cities must stop for user correction. Do not fetch the private city endpoint named in the source comment. |
| BossAutoExtension `extension/content.js` | Iterates keyword × city, keeps query/city indexes, uses URL and localStorage resume markers. | JobAgent has one explicit supervised task/session and fails closed on stale tabs. | **Adapt only explicit indexes/state fields**. **Reject** page localStorage, timer-driven continuation and unattended keyword switching. |
| BossAutoExtension `extension/content.js` | Continuous scrolling; deduplicates by `data-jobid` or detail `href`; after repeated no-new rounds switches target. | JobAgent already limits explicit scroll actions and stores handled URLs, but does not yet expose observed/new-card counts per scroll. | **Adapt** canonical-URL sets and before/after unique counts. A human click performs one scroll; no timer or auto-switch. |
| czc-good-job `web_script.js` | Enumerates `.rec-job-list` links, remembers processed hrefs, scrolls last item into view, and declares exhaustion when element count stops growing. | It uses recursive timers and automatic loops; JobAgent forbids them. | **Adapt** URL-delta/end-of-batch logic; **reject** recursion, automatic scroll and automatic detail-tab traversal. |
| smart-job `commands/jobs.js`, `tasks/steps/boss.py` | Uses private API `page` parameters and dedupes `encryptJobId`. | The live page is an infinite list and JobAgent is DOM-only. | **Reject pagination and API search**. The only transferable idea is stable item identity, implemented as canonical detail URL/external ID. |

### Safe search/result algorithm for our architecture

1. A human selects an existing JobAgent task and explicitly approves one keyword/city pair.
2. A future, separately authorized action builds a query with `URL`/`URLSearchParams`, validates
   exact origin and path, then navigates the foreground tab once.
3. On the loaded search page, read only rendered job cards and create an ordered set keyed by
   canonical detail URL (with `external_id` as a secondary check).
4. `下一位候选人` selects the next unhandled item from that ordered set. The exact card snapshot is
   retained until its right-hand detail panel is manually captured.
5. `向下滚动` is a separate human click. Snapshot unique URLs before and after the one bounded
   scroll. Report `observed`, `new`, and `duplicate` counts.
6. If new URLs appear, append them without reordering already-seen items. If none appear, report
   `no_new_candidates`; do not automatically scroll again or switch keywords.
7. Stop on the candidate cap, scroll cap, verification/rate-limit signal, wrong origin, identity
   mismatch, ambiguous DOM target or explicit human stop.

There is no next-page state and no page-number end condition for this BOSS UI.

## C. Browser task state machine

| Repository / file | Mechanism | Difference from JobAgent | Decision |
| --- | --- | --- | --- |
| BossAutoExtension `extension/content.js` | `phase`, query/city indexes, click cap, scroll count, no-new count, localStorage resume markers and timers. | JobAgent uses backend-enforced caps, extension session storage and explicit actions. | **Adapt** small typed phases and counters; **reject** timers, site storage and silent restart. |
| czc-good-job `web_script.js` | Search -> detail tab -> score -> greet; pause boolean, BroadcastChannel heartbeat, retries/timeouts. | New tabs, background loops, scoring-driven messaging and auto retry violate our policy. | **Reject workflow actions**. Adapt only explicit pause/stop state and item-level result recording. |
| smart-job `tasks/engine.py` | Durable step runner, per-item progress, pause/resume/cancel, heartbeats/leases, risk strategies (`auto_retry`, `user_action`, `skip_item`, `abort`). | Far heavier multi-service orchestration than a single local Chrome session. | **Adapt** explicit step/status vocabulary, idempotent item IDs and user-action pause. **Reject** auto-retry for BOSS verification/rate limits and defer Redis leases/heartbeats. |
| smart-job `tasks/templates/jobseeker_find_best_jobs.py` | `search -> fetch_detail(iter) -> score -> summarize`; one detail failure need not lose successful items. | JobAgent captures to canonical intake and analyzes later; it must not let an agent control the browser. | **Adapt** item-level success/failure accounting. Keep extraction/import separate from matching and keep every browser step human-triggered. |

Recommended minimal states (proposal only):

```text
approved
  -> search_ready
  -> collecting_visible_cards
  -> candidate_opened
  -> awaiting_detail_capture
  -> preview_ready
  -> awaiting_human_step
  -> completed | stopped | verification_stop | error_stop
```

Persist only local task/session/item metadata. A browser restart or stale tab remains a fail-closed
stop, not automatic resume. Verification and rate-limit signals always stop the session; after the
human resolves them, the human starts or explicitly resumes a new approved step.

## D. Extension to localhost architecture

| Repository / file | Mechanism | Difference from JobAgent | Decision |
| --- | --- | --- | --- |
| BossJob-Helper `README.md` | Claims MV3 extension + local Flask desktop service and a command queue/ack flow. | Source is absent, so transport and security cannot be audited. JobAgent already has MV3 -> loopback FastAPI. | **Architectural corroboration only**; adopt no code or protocol claim without source. |
| czc-good-job `main.py`, `config.py`, `web_script.js` | Userscript calls FastAPI endpoints for tags/config/scoring/logging. `main.py` binds `0.0.0.0`; raw JDs/actions may be logged. | JobAgent binds `127.0.0.1`, uses typed schemas and canonical intake, and avoids page dumps. | **Keep ours**. Reject non-loopback binding and raw-JD/action logs. A small read-only task-config endpoint is the only reusable concept, and ours already has task endpoints. |
| BossAutoExtension `background.js`, `popup.js` | `chrome.runtime` message broker, but extension directly calls an external AI API and stores settings/API key. | JobAgent routes data only to local FastAPI; AI and secrets stay backend-side. | **Reject external API and secrets in extension**. Adapt only typed/enumerated message envelopes. |
| smart-job `docs/ARCHITECTURE.md`, `packages/agent-gateway/protocol.py` | Extension -> WebSocket gateway -> agent gateway; typed `tool_call`, `tool_result`, `error`; SSE/heartbeat/task monitoring. | JobAgent is a single-user local POC using loopback REST and prepare/confirm. | **Keep REST now**. Adapt compact command/result fields (`session_id`, `step_id`, item identity, idempotency key, status). Add heartbeat only if a real stale-worker problem appears; it must never trigger browser action. |

## E. Agent orchestration

BossJob-Helper describes an extension as browser executor and desktop app as AI/data coordinator,
which agrees with our separation, but its source is not present. Its advertised automatic apply,
message and scheduling behavior is out of scope and rejected.

smart-job contains the strongest orchestration reference: a command registry, typed task steps,
per-item progress, pause/resume/cancel, risk signals, leases and event output. We should adapt the
**shape**, not its browser authority. In JobAgent:

- the backend may authorize and record one bounded step;
- the extension may execute only the exact foreground-tab DOM action approved for that step;
- an AI/agent may rank or analyze jobs only after canonical intake;
- an AI/agent may never choose, repeat or escalate browser actions;
- verification, rate-limit, ambiguous selectors and identity mismatch are hard stops;
- no apply, favorite, message or recruiter-chat command belongs in this state machine.

## Explicitly rejected mechanisms

- BOSS private `/wapi/` calls, `securityId` chains, request interception or reuse of cookies/tokens.
- Hidden/worker tabs, middle-click/new-tab traversal and BroadcastChannel cross-tab automation.
- Fingerprint spoofing, stealth, randomized delays intended as anti-detection, CAPTCHA solving.
- Timer/poll/MutationObserver-driven search, scroll, keyword switching, retry, apply or messaging.
- Automatic retry after verification, rate-limit or ambiguous page state.
- Whole-body extraction, broad `[class*=desc]` scraping, raw page HTML/JD logging.
- Direct external AI calls or API keys in the extension.
- Redis/Postgres/WebSocket/SSE infrastructure before a measured local requirement exists.

## Minimum changes proposed for JobAgent

No change below is implemented by this study.

1. **Selector resilience (small):** add the short result-card root fallbacks corroborated by
   BossAutoExtension; if a live description miss occurs, add a detail-root-scoped
   heading-adjacent `.desc` fallback. Preserve the anchor-based card discovery and all existing
   live-verified selectors.
2. **Continuous-list inventory (next milestone):** maintain an ordered, session-local set of
   canonical job URLs observed on the current search task. After every explicit bounded scroll,
   return observed/new/duplicate counts and a `no_new_candidates` outcome. Do not auto-scroll.
3. **Candidate traversal:** choose only the next unhandled URL from that inventory, retain the exact
   card snapshot, click its rendered link once, then require a separate human `捕获详情` action.
4. **Task state:** add the minimal typed phases above plus per-item outcomes. Keep backend
   prepare/confirm, candidate <= 20, scroll <= 5 and stale-tab fail-closed behavior.
5. **Search URL execution (later, separately authorized):** local allowlisted city-name-to-ID data,
   strict unknown-city error, `URLSearchParams`, exact origin/path validation and one human-approved
   foreground navigation. No automatic keyword loop yet.
6. **Transport:** keep loopback REST and canonical `job_intake`; enrich existing payloads rather
   than introduce a second gateway or database.

## Suggested acceptance evidence for the next milestone

- Fixture tests show stable URL ordering, duplicate suppression, and before/after scroll counts.
- Repeated rendered anchors for one job never create multiple candidates.
- One explicit scroll causes at most one DOM scroll and no timer/poll follows it.
- Zero new URLs reports `no_new_candidates` without stopping for a fictitious missing next-page
  control.
- The next candidate is never opened while another candidate awaits detail capture.
- Verification/rate-limit, wrong origin, ambiguous container and identity mismatch stop safely.
- Extension build and focused tests pass.
- Final compatibility is claimed only after real logged-in Chrome output confirms the behavior.
