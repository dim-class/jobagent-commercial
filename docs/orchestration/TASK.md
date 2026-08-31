# Authorized task — P2B unsigned Windows portable candidate

## Goal

Produce and prove a self-contained Windows x64 release candidate that no
longer needs a source checkout, Python or Node at normal runtime, while keeping
all user data outside the bundle.

## Scope

- Add a pinned, repeatable PyInstaller onedir build containing the backend
  runtime, compiled frontend, schema migrations, neutral strategy and the
  exact unpacked MV3 extension release.
- Add a release audit and per-file SHA-256 manifest that fail closed on missing
  runtime parts or bundled databases, environment files, logs, browser
  profiles, uploads and editable-install source paths.
- Add safe start/stop process identity, first-run initialization, pre-upgrade
  SQLite/strategy backup and automatic rollback if the new runtime never
  becomes healthy.
- Add an on-demand/tagged Windows GitHub Actions artifact workflow.

## Boundaries

- This milestone produces an **unsigned candidate**, not a signed installer or
  generally available release.
- No BOSS/Chrome action, task start, application, message, AI call, credential
  access or live user-data migration.
- No `.env`, database, resume, log, browser profile, upload or local source path
  may enter Git or the release artifact.
- Do not publish a GitHub Release or claim clean-machine acceptance until a
  separate machine without the development toolchain proves it.

## Acceptance

- The tracked build script creates a ZIP, SHA256SUMS and internal file manifest.
- The audit passes the real bundle and tests prove forbidden residue fails.
- An extracted EXE passes doctor, serves HTML + healthy API from isolated data,
  creates its neutral strategy/database, and stops only its recorded process.
- A successful upgrade creates a database/strategy backup; a deliberately
  failed startup restores both and does not advance the installed version.
- Full backend tests pass and the remote Windows workflow builds the same
  candidate from a clean checkout.

## Result (local, 2026-09-01)

The unsigned candidate is implemented and locally proven. PyInstaller 6.22.2
built a 3222-file onedir bundle and the release audit found zero forbidden or
missing items. The ZIP checksum matched after extraction; no `direct_url.json`
or personal/runtime residue was present. The extracted EXE passed doctor,
served HTML 200 with health/database `ok`, created isolated data, and its stop
command matched and removed the recorded PID. Upgrade success created both
SQLite and strategy backups. A forced port-conflict startup exited 3 and
restored a custom database proof row and strategy, retained the old version,
and removed its PID record. Full backend pytest reached 100% with exit 0.

Remote clean-checkout Windows artifact build, code signing, installer/uninstall
UX, and a genuinely toolchain-free Windows VM acceptance remain separate gates.

## P2B remote acceptance (2026-09-01)

Pushed `b5be113`, ran **Windows portable candidate** on GitHub Actions and
verified the artifact end to end. Still an **unsigned candidate**, not the
final commercial installer - signing, install/uninstall UX and a clean
toolchain-free Windows acceptance remain P2C.

- First remote run failed only at `upload-artifact`: the build and the audit
  succeeded and the verify step listed a 100 MB ZIP, but `.artifacts` is
  dot-prefixed and v4 skips hidden paths. Fixed in `a1e5612` with
  `include-hidden-files: true`; the rerun passed every step.
- Downloaded ZIP matches the declared SHA256 exactly. `SHA256SUMS.txt` is CRLF,
  so `sha256sum -c` cannot consume it directly - noted for P2C.
- Bundle carries the neutral strategy template only; no database, `.env`,
  résumé or browser profile.
- `--doctor` PASS. Running on an isolated port and data dir: `/health` returns
  `status=ok` and `database=ok`, the root page returns HTTP 200, and `--stop`
  removes the process, the PID record and the port binding.
- A first attempt produced a false positive - the portable failed to bind port
  8000 because a leftover dev backend held it, and the healthy `/health` came
  from that dev server. Re-verified on `--port 8123`.
- User data untouched: `data/` fingerprints identical before and after.
