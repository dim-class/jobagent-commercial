# Authorized task — P3A personal comprehensive resume search

## Authorization

The user explicitly requested on 2026-09-01 that the personal console stop
presenting a short ordered list of narrow searches and instead prepare one
broader search based on the active resume and intended career directions.

## Goal

Turn the existing quick SearchPlan entry point into one bounded comprehensive
search portfolio. The user selects cities and a desired per-direction job
count; JobAgent derives several directions from the active resume-linked career
strategy, executes the existing tasks safely in sequence, and presents one
aggregate batch rather than an implementation-order list.

## Scope

- Reuse the active resume and its existing local career strategy; do not add an
  AI call for keyword generation.
- Cover at most eight distinct preferred-role directions and at most sixteen
  city x direction tasks per explicit confirmation.
- Default the personal quick search to eight candidate attempts per direction,
  while preserving the existing editable 1–20 bound.
- Keep internal execution sequential in one visible normal-Chrome BOSS tab.
- Replace the ordered confirmation list and primary per-task presentation with
  an aggregate summary of cities, directions, task progress and collected jobs.
- Keep custom tasks, task history, connection diagnostics and low-level batch
  controls under advanced settings.
- Update backend, frontend and extension validators together; bump the unpacked
  extension version because its accepted batch protocol changes.

## Boundaries

- No real BOSS operation, browser control, user-data mutation, paid AI call,
  application, favorite, recruiter message, CAPTCHA handling, stealth,
  credential/session access, background timer, automatic retry or task start.
- Canonical intake, job persistence/deduplication, login/verification pauses,
  per-task candidate cap, five-scroll cap and stop-on-failure behavior remain
  unchanged.
- A comprehensive search still requires one explicit in-page confirmation and
  never resumes automatically after a browser/worker restart.
- The broader batch limit supersedes only the M4g five-task ceiling; all other
  M4/M4g safety rules remain in force.

## Acceptance

- One city prepares up to eight unique configured directions; two cities can
  prepare sixteen tasks; four cities prepare four directions each; no batch
  exceeds sixteen tasks.
- The same 1–16 validation is enforced by backend options, frontend bridge and
  MV3 worker, including duplicate/invalid-id rejection.
- The main confirmation shows aggregate cities/directions and totals without an
  ordered task list or task ids.
- The primary card shows aggregate progress/counters; detailed task selection
  and keyword performance are available only in advanced settings.
- Existing fixture-only backend, frontend and extension tests pass after the
  changes. No live task or AI call is part of acceptance.
