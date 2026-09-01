# Authorized task — P2C-A3 Chrome Web Store submission candidate

## Authorization

The active user goal is to make JobAgent usable by anyone. After the Windows
installer lifecycle passed, Codex continued with the next bounded,
no-purchase productization step on 2026-09-01.

## Goal

Produce a reproducible, reviewable Chrome Web Store candidate for the existing
JobAgent MV3 extension so ordinary users will not ultimately depend on Chrome
developer mode. This milestone prepares the package and disclosure evidence;
it does not upload or publish anything.

## Scope

- Keep the unpacked development manifest for local work, but generate a store
  manifest that omits the Vite-only localhost port 5173.
- Add neutral JobAgent-owned extension icons without using BOSS branding.
- Remove the suspended M7 BOSS-chat scanner from extension runtime code. The
  store package must match the current promise that JobAgent does not scan
  recruiter conversations.
- Build a deterministic ZIP whose manifest is at the archive root and whose
  contents are limited to compiled runtime assets, popup assets, and icons.
- Fail closed on unexpected permissions, hosts, files, remote-hosted code, or
  other disallowed browser primitives; emit SHA-256 and machine-readable
  package metadata.
- Document the extension single purpose, permission justifications, local data
  handling, draft listing copy, and the remaining manual store-dashboard work.
- Add an artifact-only GitHub Actions workflow. It may build and test the
  candidate but must not authenticate to or publish through Chrome Web Store.

## Boundaries

- Do not operate Chrome, BOSS, user sessions, resumes, databases, AI, or any
  recruitment action.
- Do not add telemetry, a remote developer service, credentials, remote-hosted
  code, broad host access, CAPTCHA handling, stealth, or fingerprint behavior.
- Do not re-enable M7 chat scanning. Backend historical schema/API code is not
  a browser capability and is outside this removal unless a normal regression
  proves a required compatibility adjustment.
- Do not enable M6, automatic application, bulk application, or messaging.
- Do not create a Chrome Web Store account, accept agreements, pay a fee,
  upload assets, publish, or claim store approval.
- Privacy policy and listing text are submission drafts until the owner reviews
  them and publishes the privacy policy at a stable public URL.

## Acceptance

- Two package builds from the same checkout are byte-identical.
- The ZIP contains a root manifest, 16/32/48/128 PNG icons, popup assets, and
  only the expected compiled JavaScript; no source, tests, maps, Node modules,
  or developer-only files are present.
- The store manifest requests only `activeTab`, `scripting`, and `storage`, plus
  BOSS and loopback port 8000 hosts; no port 5173 permission or match remains.
- Runtime/package audits reject remote script loading, `eval`, `new Function`,
  `chrome.debugger`, `<all_urls>`, and the suspended M7 command/parser surface.
- Privacy and submission docs accurately disclose website-content processing,
  cropped salary screenshots, loopback transport, local companion dependence,
  and all remaining human dashboard steps.
- Extension, frontend, and backend regressions remain green; a clean remote
  workflow uploads only the unsigned store-submission candidate and integrity
  metadata.
