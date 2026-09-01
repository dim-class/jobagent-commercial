# Authorized task — P2C-A2 repeatable Windows installer lifecycle gate

## Authorization

The active user goal is to make JobAgent usable by anyone. After P2C-A passed
local lifecycle and clean-checkout build acceptance, Codex continued with the
next bounded, no-purchase productization step on 2026-09-01.

## Goal

Turn the currently manual installer lifecycle evidence into a repeatable
GitHub Windows release gate: install, run the frozen product without relying on
Python or Node from `PATH`, upgrade under the stable AppId, and silently
uninstall while preserving the default per-user data directory.

## Scope

- Add a Windows-only acceptance script that is hard-gated to an ephemeral
  GitHub Actions runner and refuses to run when JobAgent data, installation, or
  uninstall registration already exists.
- Build two installer versions from the same audited portable payload.
- Install the first version as the current user without administrator rights.
- Run `--doctor`, start the installed frozen EXE with browser opening disabled,
  verify `/health` and the compiled frontend, and stop through JobAgent's own
  process-identity guard.
- Upgrade to the second version under the same AppId and prove the database and
  a test sentinel survive.
- Silently uninstall, prove program/registration removal, default data
  preservation, and port release, then clean only runner-owned test state.
- Add offline contract tests that lock the destructive and CI boundaries.

## Boundaries

- Do not run this lifecycle script on the developer's Windows profile. It may
  target the default `%LOCALAPPDATA%\JobAgent` only after proving it is an
  ephemeral GitHub runner and that the target did not exist beforehand.
- Do not touch Chrome, BOSS, real resumes, real databases, credentials, AI, or
  any recruitment action.
- Do not weaken installer data-preservation defaults or process-identity checks.
- A GitHub runner still contains build tools. Sanitizing runtime `PATH` proves
  the frozen EXE does not resolve Python/Node there; it does not replace the
  later genuinely toolchain-free disposable Windows VM acceptance.
- Keep the artifact unsigned and explicitly non-commercial-ready. Do not buy or
  invent signing or Inno Setup licensing evidence.

## Acceptance

- Offline tests prove the script requires GitHub Actions plus `RUNNER_TEMP`,
  refuses pre-existing state, uses only the stable per-user install/data roots,
  invokes JobAgent's guarded stop, and always cleans runner-owned state.
- The installer workflow builds two versions, runs the lifecycle gate before
  upload, and uploads only the final unsigned candidate plus integrity metadata.
- A clean remote run proves doctor, health/database `ok`, frontend HTTP 200,
  stable-AppId upgrade, unchanged database hash, preserved sentinel/default
  data, silent uninstall cleanup, and released port.
- Full backend, frontend, and extension regressions remain green.

## Result (2026-09-01)

Implemented `scripts/test-windows-installer-lifecycle.ps1` with an explicit
GitHub Actions/`RUNNER_TEMP` gate, refusal of any pre-existing JobAgent install,
data, or uninstall registration, exact runner-owned cleanup targets, and no
browser or recruitment behavior. The installer workflow now builds `-a` and
`-b` candidates from one audited payload and runs the lifecycle before upload.

Remote run `33474909477` passed every step. It installed the first version,
ran doctor and the frozen server with Python/Node absent from runtime `PATH`,
verified health/database `ok` and frontend HTTP 200, stopped through the product
guard, upgraded under the stable AppId without changing the database or test
sentinel, then silently uninstalled while preserving default user data and
releasing the port. The downloaded final `0.1.0-3-b` EXE matched manifest
SHA-256 `d3e1787980c852e9297b92b7a522f6c28742b347fc137fdc270b9a8e40b1c235`.
It remains `NotSigned`, `development_candidate`, and
`commercial_distribution_ready=false`. Regular CI run `33474896134`, full
local backend regression, frontend 31/31, and extension 301/301 all passed.

This closes the repeatable clean-runner lifecycle gate. It does not replace a
genuinely toolchain-free disposable Windows VM, Authenticode signing,
applicable Inno Setup commercial licensing, or extension distribution.
