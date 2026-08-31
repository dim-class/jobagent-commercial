# Authorized task — P2C-A Windows installer candidate

## Authorization

The user resumed the active “任何人可用” productization goal on 2026-09-01.
This bounded milestone follows the completed P2B portable candidate.

## Goal

Produce and prove an **unsigned, per-user Windows x64 installer candidate**
that installs, upgrades and uninstalls JobAgent without requiring administrator
rights, Python, Node or a source checkout at normal runtime.

## Scope

- Compile the already-audited P2B bundle into one Inno Setup installer EXE.
- Install under the current user's local Programs directory with one stable
  AppId, Start Menu shortcuts, an optional desktop shortcut and Add/Remove
  Programs uninstall support.
- Stop only the recorded JobAgent process before upgrade/uninstall.
- Preserve `%LOCALAPPDATA%\JobAgent` by default on uninstall. An interactive
  uninstall may delete it only after a separate explicit warning and
  confirmation; silent uninstall always preserves it.
- Produce SHA-256 metadata and an auditable clean-checkout Windows workflow.
- Add offline structural tests for the destructive and privilege boundaries.

## Boundaries

- This is still an **unsigned candidate**, not a generally available release.
- Do not purchase or invent a signing certificate, publish a GitHub Release,
  or claim SmartScreen trust.
- No BOSS/Chrome action, task start, application, message, AI call, credential
  access or live user-data migration.
- Do not read, copy, bundle or delete the developer's real `data/`, `.env`,
  resumes, browser profiles or logs.
- A genuinely clean Windows machine/VM without the development toolchain is a
  separate acceptance gate; CI is clean-checkout build evidence, not that VM.

## Acceptance

- Installer build starts from the audited P2B bundle and fails closed if any
  required payload or installer compiler is missing.
- Setup is per-user/non-admin and creates the expected program and shortcuts.
- Reinstall/upgrade uses the same AppId and keeps runtime data intact.
- Interactive uninstall offers a clear keep/delete choice, defaults to keep;
  silent uninstall keeps data without prompting.
- Installer and checksum are built on a clean GitHub Windows runner.
- Local isolated acceptance proves install, launch/health, upgrade and
  uninstall-keep without touching real user data. The destructive interactive
  delete branch is structurally tested here and must be clicked only in the
  separate clean-VM acceptance, never in the developer's real Windows profile.
- Full repository tests remain green.

## Result (local, 2026-09-01)

Implemented an Inno Setup 7.1.0 per-user installer with a stable AppId,
Start Menu entry, optional desktop shortcut, guarded stop, and a default-keep
uninstaller. The build re-audits the P2B payload and emits an installer SHA-256
plus a manifest that explicitly records `NotSigned`,
`development_candidate`, and `commercial_distribution_ready=false`.

An isolated two-version lifecycle passed: p2ca1 installed without admin rights,
the installed frozen EXE returned health/database `ok` and HTML 200, p2ca2
upgraded under the same AppId, the isolated database hash stayed unchanged,
and silent uninstall removed the program/registration while preserving the
database and releasing the port. The interactive delete-data branch defaults
to No and is structurally tested; it was deliberately not clicked in the real
user profile. Full backend regression, frontend 31/31 and extension 301/301
pass. Clean-checkout installer CI run `33451995662` also passed: the downloaded
`0.1.0-2` artifact used Inno Setup 7.1.0, its actual SHA-256 matched the
manifest (`398ab6bcfec4cddab3ba106ba878c63808d7b5284157a1395454ff659e48be3b`),
and the manifest remained fail-closed at `NotSigned`, `development_candidate`,
and `commercial_distribution_ready=false`. Authenticode signing, confirmation
of applicable Inno Setup commercial licensing, and clean-VM interactive delete
acceptance remain release gates until separately proven.
