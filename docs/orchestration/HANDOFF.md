# Handoff — 2026-08-31

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

Working tree is clean and `commercial` is in sync with `origin/commercial` at
`b1cd433`.

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

1. The user was advised to drop 「基础设施」 from `preferred_roles` — it has
   enough evidence to call wasteful. Their edit to make, not yours.
2. Add `--reload` to the backend launcher, or make the launcher obvious about
   needing a restart.
3. Multi-user / auth / deployment for "giving it to other people" is a separate
   design conversation the user has explicitly deferred.

Ask the user before starting any of these. The project's convention is one
bounded, explicitly authorized milestone at a time in `TASK.md`.
