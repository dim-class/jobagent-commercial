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
