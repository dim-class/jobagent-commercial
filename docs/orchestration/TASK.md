# Authorized task — P2A commercial runtime foundation

## Goal

Move the commercial branch from a source-only developer launch toward a
single-process Windows product that a new local user can run without storing
personal data in the checkout. This is the first bounded P2 increment; it does
not claim that a signed installer already exists.

## Scope

- Add an explicit per-user runtime-data root suitable for a packaged build,
  while keeping current source-development defaults and overrides compatible.
- On first startup create runtime directories and a neutral strategy copy.
- Let FastAPI optionally serve the already-built React frontend so a packaged
  release needs one local process and no Node runtime at use time.
- Add a safe, non-secret `doctor` check and tests for clean-user initialization.
- Document exactly what remains before a distributable Windows artifact.

## Boundaries

- No BOSS/Chrome actions, task starts, AI calls, application, message, favorite,
  credential access, CAPTCHA behavior, or live user-data migration.
- Never stage or copy `.env`, SQLite, resumes, browser profiles, logs, or
  `data/` contents into a release or Git commit.
- Do not break the current developer launcher or existing user's local data.
- No installer claims until an artifact is actually built and clean-machine
  tested.

## Acceptance

- Tests prove an override runtime root creates isolated directories and a
  neutral strategy without touching repository data.
- Production frontend serving is opt-in and serves the built index/assets;
  development root/API behavior stays compatible when disabled.
- `doctor` reports actionable component status without secrets.
- Backend, frontend and extension suites remain green; docs/status state the
  remaining packaging/signing/upgrade work accurately.

## Authorization

The active user goal is to make JobAgent usable by anyone, and the user already
authorized keeping personal data local while publishing a commercial branch.

## Result (2026-08-31)

P2A is complete and verified offline. Runtime data can be isolated outside the
checkout; clean startup creates a neutral strategy; the backend can serve the
built frontend as one process; doctor reports only safe component state. An
isolated lifecycle (rechecked on ports 18127/18128) created a new database and strategy,
served HTML + healthy API, and stopped only its recorded PID. Full backend
pytest collected 1787 and exited 0 (30 existing environment skips); extension
build + 301/301 and frontend build + 31/31 passed. The MV3 console bridge now
accepts only the exact development/commercial loopback origins on ports 5173
and 8000; tests still reject arbitrary ports. No site/browser/AI action occurred.
P2B packaging, signing, upgrade rollback and clean-machine acceptance remain
explicitly incomplete.
