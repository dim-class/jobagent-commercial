# Claude Worker Result

State: implementation — live BOSS detail extraction

## Files changed

- `extension/src/boss/extract.ts` — bug fix in `findUniqueSelectedCard()`.
- `extension/dist/boss/extract.js` — rebuilt output of the above (checked-in dist).
- `backend/tests/test_extension_extraction.py` — fixed one stale test selector.
- `extension/tests/fixtures/README.md` — documented the three fixtures that already existed on
  disk but were missing from the table.

No changes to `extension/src/boss/selectors.ts`: the selector lists (`DETAIL_CITY`,
`DETAIL_EXPERIENCE`, `DETAIL_EDUCATION`, `COMPANY`, `DESCRIPTION`, `DIAGNOSTIC_COMPANY_ROOT`, etc.)
and their matching fixtures (`boss_job_detail_live_shape.html`,
`boss_search_split_pane_live_shape.html`) were already present from an earlier milestone and
already encode the live-observed markup named in this task's "Live evidence" (`.job-banner
.info-primary .text-city/.text-experiece/.text-degree`, `.job-boss-info .boss-info-attr`, PUA
salary handling). Inspection confirmed they match the task's evidence; the smallest robust change
was fixing a real correlation bug, not touching selectors.

## The bug and the fix

`findUniqueSelectedCard()` (used on the split-pane search+detail shape at `/web/geek/jobs`, and by
extension on any dedicated detail page render that reuses card-shaped markup) climbs up to 7
ancestor levels from a matched result-card title node to find/register a "card root" when the
title isn't already inside a known `CARD` selector match. That climb was unconditional: it could
add a wrapper (e.g. the whole `<ul class="job-list-box">` results list) that contains *multiple*
cards. Extracting fields from that wrapper picks the first descendant match for each field
independently (title from card 1, salary from card 2 — whichever appears first in DOM order for
each field's selector), producing a Frankenstein candidate. Because that wrapper reused card 1's
own `source_url`, it silently overwrote card 1's correct extraction in the `byUrl` map — no
ambiguity was ever detected, so the wrong (unrelated card's) salary was returned for the correct
job. This is exactly the class of bug that would make live salary/city/experience/education
correlation unreliable on the split-pane shape.

Fix: while climbing, stop (and do not add) the moment an ancestor contains more than one
`CARD_TITLE` match — such an ancestor spans multiple cards, not one, and every level above it only
contains more. This was caught by an *existing* fixture/test
(`boss_search_split_pane_live_shape.html` /
`test_live_split_pane_reads_only_the_human_selected_detail`), which was failing before this fix
(salary `18-25K` instead of the correct `8-12K`) and passes after it.

The second fix (`test_extension_extraction.py`) corrected a diagnostic test that queried
`.job-detail-box .job-name` — a selector that matches nothing in the `boss_job_detail.html`
fixture (its title lives at `.job-banner .name h1`, per the fixture and the existing
`matched_selectors` assertion in the same file) — causing a `null.parentElement` crash unrelated to
extraction correctness.

## Test / build counts

- `extension`: `npm run build` (tsc) — clean, no errors, both before and after the fix.
- `extension`: `node --test tests/*.test.cjs` — **28/28 passed** (m4a/m4b session-navigation
  behavior; unaffected by this change). Note: `npm test` (which runs `node --test tests/` in
  directory mode) fails with `Cannot find module '...\tests'` on this machine's Node v24.19.0 —
  a pre-existing Node/npm path-resolution quirk on this box, not a code or test regression;
  running the same files via an explicit glob works and is what was used to verify.
- `backend/tests/test_extension_extraction.py` (Playwright/Chromium against local fixtures,
  injecting the real built `extension/dist`) — installed the missing Playwright Chromium browser
  (`python -m playwright install chromium`, local binary download only, no BOSS/live-site access)
  then ran: **36/36 passed** (2 were failing before the fix: the split-pane salary-correlation bug
  above, and the stale diagnostic selector).
- Full backend suite (`python -m pytest`, via `--junit-xml` for an authoritative count since this
  pytest config prints no trailing summary line): **1393 passed, 0 failed, 0 errors, 0 skipped.**

## Remaining live verification

Everything above runs against local, hand-written fixtures only, per the repository's fixture-only
testing rule — no automated test may touch zhipin.com. A human still needs to open a real,
logged-in BOSS dedicated detail page (`/job_detail/<id>.html`) and the split-pane view under
`/web/geek/jobs`, and confirm company, city, experience, education and description now populate
alongside the already-working title/salary/URL, exactly as `extension/README.md`'s manual checklist
describes. This worker made no live BOSS request of any kind.
