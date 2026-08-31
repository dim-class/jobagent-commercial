# Handoff — 2026-09-01

Written for whoever picks this up next. Read `CLAUDE.md` first; this file only
records the things that are true right now and are easy to get wrong.

## Where the code lives

| | |
| --- | --- |
| Local repo | `F:\jobagent\bossagent1.0` |
| Personal branch | `main` — **never pushed**, holds the personal-use history |
| Commercial branch | `commercial` — pushed to `origin` |
| Remote | `https://github.com/dim-class/jobagent-commercial` (**private**) |
| Archived old repo | `dim-class/Qirui-JobAgent` — unrelated history, 1 commit, read-only |

Verified clean at `959faa8`, in sync with `origin/commercial`, with regular CI
green (run `33417074087`). Always re-verify the current hash rather than
trusting a historical handoff value.

**The user runs this on Windows only.** Weigh Windows behaviour first; a
Linux-only concern is not a reason to change product behaviour here.

## Personal vs commercial — do not break this

The personal data is **only** on disk, never in git:

- `data/career_strategy.yaml` — the user's real strategy (13 cloud roles)
- `data/jobagent.db` (+ `-wal`/`-shm`) — 130 jobs, 130 analyses
- `data/uploads/` — résumé PDFs
- `data/browser_profiles/` — BOSS login session
- `backend/.env` — the OpenAI key
- `data/acceptance/` — pytest residue

All are gitignored. `config/career_strategy.yaml` is the **neutral template**
(`preferred_roles: []`). Before any commit, confirm nothing under `data/`
except the 0-byte `data/.gitkeep` is staged.

## Things that will bite you

**The backend runs without `--reload`.** Backend edits do not take effect until
it is restarted. Several "the fix didn't work" moments this session were just
that. `Start-Process` from a tool call does not survive; start it as a durable
background process.

**`extension/dist` is committed and Chrome loads it.** A stale `dist` makes the
suite pass against code that is not the source. `extension/tests/dist-freshness
.test.cjs` now enforces this — if it fails, run `npm run build` in `extension/`
and commit the result. If typescript is missing from `extension/node_modules`,
that test fails on purpose: `npm install` there before trusting any extension
result.

**Time-rotted fixtures.** `test_analytics_api` built data around a frozen
`NOW = 2026-08-21` while calling routes that use the real clock; it began
failing on 2026-08-31 with no code change. Those fixtures now anchor to real
time. If you add a test that mixes a frozen date with an HTTP route, it will rot
the same way. CI runs daily partly to catch this.

## CI

`.github/workflows/ci.yml` — backend pytest, extension build+test, frontend
build+test, on push/PR and daily. All three green as of run `33400062616`.
Note pytest does not create the parent of `--basetemp`, hence the `mkdir -p`.

## P2B — the Windows portable candidate

Built by `.github/workflows/windows-portable.yml` (manual dispatch or a `v*`
tag). It produces an **unsigned** ZIP plus `SHA256SUMS.txt`. Signing,
install/uninstall UX and a clean toolchain-free Windows acceptance are P2C and
have not been started.

Remote acceptance passed on 2026-09-01: every workflow step green, the
downloaded ZIP matched its declared SHA256, `--doctor` passed, `/health`
returned `status=ok` + `database=ok`, the root page returned 200, and `--stop`
removed the process, the PID record and the port binding. The bundle carries
the neutral strategy template only.

### Two traps this milestone actually sprang

**A failed start could look like a successful one.** The launcher used to write
its PID record, take the upgrade backup and start the browser poller *before*
uvicorn tried to bind. On a busy port it exited 3, but the poller had already
found the *other* process's healthy `/health`, written `runtime-version.json`,
set `ready` (which suppresses the upgrade rollback) and opened a browser onto
that other process. A failed upgrade would silently keep its backup
unrestored. Fixed in `4f9aac4`: `portable._port_available()` runs first, and a
busy port exits 4 with a message naming the port and saying the page on that
address belongs to another program.

**`get_settings()` is `lru_cache`d.** Setting `APP_PORT` in `portable.main()`
does not reach an already-constructed `Settings`, so the doctor's port check
read a stale value. It passed locally only because this machine had a dev
backend on 8000; CI, where 8000 is free, caught it. Fixed in `959faa8` by
passing `--port` into the doctor subcommand explicitly. **If you add a check
that depends on runtime configuration, take it from the caller, not from
`get_settings()`.**

The second trap is the more general lesson: a local green can be an accident of
what happens to be listening. When a test involves ports or the clock, make it
assert both directions in one run.

### Do not "fix" this — it is not a bug

The portable exe writes its Chinese console output as **GBK** (`端` = `B6CB`),
which is correct for a Chinese Windows console (codepage 936). It looks like
mojibake in a tool that decodes captured output as UTF-8. This was checked at
the byte level: UTF-8 decoding fails, GBK decoding yields the intact message.
Changing it to UTF-8 would break the real console it is meant for.

### Known, deliberately unfixed

`SHA256SUMS.txt` is now written with LF so `sha256sum -c` (Git for Windows) can
consume it; verified against the real artifact. Nothing else from P2B is
outstanding.

## P2C-A — the Windows installer candidate

`scripts/build-windows-installer.ps1` re-audits the P2B bundle and compiles it
with exactly Inno Setup 7.1.0. The installer is per-user/non-admin, has a stable
AppId, Start Menu entry, optional desktop shortcut and uninstall registration.
Its manifest must continue to say `NotSigned`, `development_candidate` and
`commercial_distribution_ready=false` until the later release gates pass.

Local isolated lifecycle passed with two versions: install, frozen EXE doctor,
health/database ok, HTML 200, same-AppId upgrade, unchanged test database hash,
silent uninstall, preserved data, removed install/registry state and released
port. The first sandboxed attempt failed only because the sandbox denied HKCU
uninstall-key creation and Inno rolled every file back; the approved out-of-
sandbox run passed. Do not mistake that environment denial for an installer bug.

The first installer CI run (`33451647195`) exposed a different environment
trap: `windows-2025` retained Inno 6 on PATH even after winget installed 7.1.0.
The resolver must search the fixed Inno 7 directories before PATH; a contract
test now locks this order. Do not simplify it back to `Get-Command` first.

The fixed clean-checkout run `33451995662` passed every step, including artifact
upload. Its downloaded `0.1.0-2` EXE matched the manifest SHA-256
`398ab6bcfec4cddab3ba106ba878c63808d7b5284157a1395454ff659e48be3b` and
reported Inno 7.1.0, `NotSigned`, `development_candidate`, and
`commercial_distribution_ready=false`. This closes the P2C-A remote-build gate;
it does not close signing, licensing, or clean-VM acceptance.

The interactive delete-data branch points at the real default
`%LOCALAPPDATA%\JobAgent`. It is structurally tested, defaults to No, and was
deliberately not clicked on the developer profile. Test that destructive branch
only on a clean disposable Windows user/VM. Also, the local compiler prints
`Non-commercial use only`; confirm and satisfy applicable Inno Setup commercial
licensing plus Authenticode signing before production distribution.

## M6 — the one genuinely dangerous area

M6 is human-confirmed **single** application execution. Policy is in
`CLAUDE.md`; the user authorized it in stages and ruled on 2026-08-30 that on
BOSS, 立即沟通 plus its inseparable first greeting is one application action.

What exists: the approval gate (`ApplicationApproval`, migration `0018`/`0019`),
atomic one-attempt claim, `application_result_unknown`, the extension click
primitive, and a two-step confirmation UI.

What is verified: the selector `.btn-startchat` was checked read-only against
the user's real logged-in pages in both states (`data-isfriend="false"` /
`"true"`), and the already-chatted case is proven to refuse via real-DOM tests.

**What is NOT verified: that clicking actually submits.** Nothing has ever been
clicked on the live site. `HUMAN_CONFIRMED_APPLY_ENABLED` and `AUTO_APPLY` are
both `False`. Do not turn the flag on for the user; that is their call, on a job
they actually intend to apply to.

Hard limits that no authorization has ever lifted: no unattended, bulk, timed or
AI-triggered application; no message after the first greeting; no auto-retry; no
CAPTCHA bypass, stealth, or credential/cookie/token access.

## Current state of the user's data

130 jobs, 130 analysed, 0 missing salary, 2 applied, 0 replies. 21 recommended.
Score distribution is poor: 0 jobs above 89, 72 of 130 below 60.

`GET /api/analytics/search-keywords` says why, from the scores already paid for:

| keyword | n | avg | recommend |
| --- | --- | --- | --- |
| 云计算工程师 | 28 | 61.0 | 8/28 (29%) |
| 运维开发 | 24 | 55.4 | 4/24 (17%) |
| 基础设施 | 8 | 31.5 | **0/8** |

## Suggested next steps (none started, none authorized)

1. **P2C-B**: Authenticode signing, applicable Inno Setup commercial licensing,
   and acceptance on a clean Windows box with no Python/Node toolchain.
2. Windows launch polish the user has not asked for yet: what the console
   window does on a double-click, and whether `README-FIRST.txt` is readable
   enough for a first run.
3. The user was advised to drop 「基础设施」 from `preferred_roles` — it has
   enough evidence to call wasteful. Their edit to make, not yours.
4. Add `--reload` to the backend launcher, or make the launcher obvious about
   needing a restart.
5. Multi-user / auth / deployment for "giving it to other people" is a separate
   design conversation the user has explicitly deferred.

**Note:** the dev backend that had been running on port 8000 was stopped on
2026-09-01, to reproduce the CI environment while verifying the port fix. It
was not restarted. Ask before starting it.

Ask the user before starting any of these. The project's convention is one
bounded, explicitly authorized milestone at a time in `TASK.md`.
