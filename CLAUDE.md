# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

## Product

Local-first AI job search assistant (中文 UI). The user is a cloud /
infrastructure / DevOps engineer job-hunting on Chinese recruiting platforms.

**What exists today (v0.1 - v1.0):**

```
resume PDF/DOCX  -> local parse (no AI) -> stored profile

manual JD paste   ─┐
quick capture     ─┤─> normalize + hash -> dedup -> stored job
  (paste / image) ─┤   (one shared path: services/job_intake.py)
browser capture   ─┘

job + resume     -> JobMatchAgent -> typed JobMatchResult (0-100 + reasons + 招呼语)
recommendation   -> 投递队列       -> HUMAN decides -> ApplicationEvent trail
recruiter msg    -> 分析 + 回复草稿 -> HUMAN edits & sends -> RecruiterMessage

ApplicationEvent -> deterministic analytics -> observations -> proposals
                                                    -> HUMAN confirms -> strategy

resume variants  -> HUMAN picks one at apply time -> attribution on the
                    application cycle -> per-variant conversion

application cycle -> InterviewProcess -> InterviewRound(s)
                     HUMAN records每轮结果 -> stage funnel / drop-off

application cycle -> Offer -> OfferRevision(s)
                     company offer  vs  candidate counter
                     HUMAN accepts/declines -> frozen snapshot

HUMAN weights + HUMAN 1-5 ratings -> deterministic offer score
                     transparent breakdown + coverage + deal-breakers
                     HUMAN decides -> DecisionSnapshot (frozen)
```

Explicitly **not** implemented: unattended or bulk application, automatic
recruiter messaging, search-result crawling, mass scraping. Executing a single
application that the human has explicitly confirmed for one specific job is
defined by "M6 — human-confirmed single application execution" below. Its
entry point remains feature-gated and each job still needs its own final human
confirmation; the feature flag never authorizes a job.

## Architecture

```
data/career_strategy.yaml     per-user definition of "a good job" (gitignored DATA)
data/jobagent.db              SQLite (gitignored)

backend/app/
  main.py                     FastAPI app, exception handlers, CORS, lifespan
  cli.py                      python -m app.cli  init-db | seed | info
  core/                       config, paths, logging, errors, career_strategy loader
  db/                         base, session, migrations (Alembic since v0.4)
alembic/                      migration scripts; alembic.ini at backend/
  models/                     Resume, Job, JobAnalysis, ApplicationEvent, enums
  schemas/                    Pydantic request/response models
  services/
    hashing.py                every stable hash + the analysis cache key
    job_normalizer.py         deterministic JD normalization
    resume_parser.py          PyMuPDF / python-docx extraction + profile
    scoring.py                deterministic pre-analysis + post-analysis guardrails
    job_matcher.py            orchestration: cache -> agent -> guardrails -> persist
    job_intake.py             THE single path a posting takes into the DB (v0.2)
    extension_intake.py       Chrome-extension candidates -> job_intake (POC)
    browser_session.py        singleton headed-browser lifecycle (v0.2)
    job_import_parser.py      deterministic parse of pasted page text (v0.3)
    quick_capture.py          parse -> confirm orchestration + image validation
    urls.py                   canonical_url / detect_source (never fetches)
    application_workflow.py   THE only place Job.status changes (v0.4)
    application_queue.py      derives the daily queue from Job + analysis
    application_metrics.py    deterministic funnel counts / rates (no LLM)
    statistics.py             Wilson intervals, confidence bands, percentiles (v0.6)
    application_cycles.py     rebuilds application cycles from the event trail (v0.6)
    application_analytics.py  THE analytics engine - no AI, no network (v0.6)
    strategy_recommendations.py proposals + the one confirmed write path (v0.6)
    resume_variants.py        variant lifecycle: rename/clone/archive (v0.7)
    resume_comparison.py      job x resume planning + the cost gate (v0.7)
    resume_analytics.py       per-variant conversion + AI-fit matrix (v0.7)
    interview_pipeline.py     THE only place interview state changes (v0.8)
    interview_analytics.py    stage funnel / drop-off / latency, no AI (v0.8)
    offer_calculator.py       pure compensation arithmetic, no DB (v0.9)
    offer_management.py       THE only place offer state changes (v0.9)
    offer_analytics.py        offer funnel / medians / uplift, no AI (v0.9)
    offer_decision.py         pure weighted scoring, no DB, no AI (v1.0)
    decision_support.py       THE only place decision state changes (v1.0)
    recruiter_parser.py       speaker split + keyword signals (v0.5)
    recruiter_conversations.py conversation persistence + human decisions
    recruiter_message_analyzer.py context, cache, grounding guard
    timezones.py              local-day helpers (Asia/Tokyo reporting)
    task_matching.py          task-scoped candidate matching plan + the cost gate (M5a)
    seed.py                   fictional demo jobs
  agents/
    prompts.py                SYSTEM_PROMPT + PROMPT_VERSION (part of the cache key)
    job_match_agent.py        matching agent (OpenAI Agents SDK, typed output)
    import_prompts.py         extraction prompts + IMPORT_PROMPT_VERSION (v0.3)
    job_import_agent.py       extraction agent: text + vision (v0.3)
    recruiter_prompts.py      RECRUITER_PROMPT_VERSION + bounded context
    recruiter_agent.py        RecruiterConversationAgent (v0.5)
  job_sources/
    base.py                   JobSource ABC + BrowserJobSource (v0.2)
    manual.py                 pasted JD
    boss/                     selectors.py | extractor.py | source.py  (v0.2)
    liepin/ zhaopin/ job51/   empty placeholders, on purpose

extension/                    Chrome MV3 POC - reads the BOSS page the human
                              already has open (see below). src/boss/selectors.ts
                              holds every selector; dist/ is the built output
                              Chrome loads unpacked.

frontend/src/                 React + TS + Vite; api/client.ts, pages/, components/ui.tsx
scripts/dev.ps1               Windows dev tasks
scripts/smoke_openai.py       one real API call, manual only
```

### The scoring pipeline (important)

The score is **not** a raw LLM vibe. `services/scoring.py` computes a
deterministic feature block first — city match, preferred-role match, skill
overlap (with aliases), excluded-role hits, extracted experience requirement,
parsed salary, and a transparent `heuristic_score`. The agent receives those
facts and *interprets* them. After the model returns, `apply_guardrails()`
clamps every score to 0-100 and enforces the one hard strategy rule (excluded
role + no meaningful skill overlap → capped and forced to `skip`).

Most of the pipeline is therefore testable without spending a token.

### The greeting is a chat message, not a summary (prompt v2, 2026-09-06)

`greeting_message` is the first thing a recruiter reads, and until v2 the
prompt prescribed the shape that made it useless: *"一句话说明自己是谁 + 2-4 个
相关经历 + 一句表达希望进一步沟通"*. Every output obeyed it exactly, so all of
them read「您好，我目前从事云基础设施与中间件工程，具备 A、B、C 经验，期待进一步
沟通。」- 90 to 130 characters of flattened résumé, identical opener, and not one
word about the posting.

What the prompt asks for instead: at most 90 characters, something the JD
actually says, **one concrete thing from the work history** rather than two
skill names, and **a question the recruiter can answer in one line**. The question is the part
that matters:「期待进一步沟通」gives them nothing to reply to, while「这个岗位更偏
平台建设还是值班运维？」gets answered on the way to the next message. It must ask
about something the JD says or conspicuously omits - never an invented detail.

The opener rule is written as "start from the job, not from yourself" rather
than "vary it", because a single-shot call cannot know what the last greeting
looked like. Feeding it recent greetings would work and is deliberately not
done: they are not in `analysis_cache_key`, so the same job would quietly
produce different results while the key claimed the inputs were identical.
Starting from the JD varies the opener for free, because the JDs vary.

Grounding is unchanged and is the one rule that never bends: nothing may
appear that is not in the résumé. A better register must never buy itself a
fabricated project.

It took six versions and about twenty real fast-model calls to get there,
and the useful part is *why* the middle ones failed:

- **v2/v3 replaced one template with another.** Prescribing "三句话，按这个
  顺序" produced four greetings with identical rhythm. A rigid structure is
  what made v1 stiff; a different rigid structure is still stiff.
- **v4 gave four deliberately different example shapes, and the model picked
  one and stayed there.** Examples anchor much harder than they diversify, so
  variety has to come from a rule about the *input* (start from this JD),
  never from a menu of shapes.
- **v5 found the real defect.** Every greeting read 「我做过 AWS 迁移测试和
  WAS/IHS 排障」 - two nouns lifted from the `skills` list with a verb bolted
  on, which any candidate in the field could have written. The résumé had
  「2 套 ST 应用服务器环境的搭建与配置核对」 and 「4 人团队」 sitting in
  `work_experience`, and the agent receives the full text, so the material was
  never the problem. v5 requires the concrete sentence to come from the work
  history - an action, a project, a quantity - and forbids dressing up a skill
  name.
- **v6 is bookkeeping**: pin 您好 (v4 dropped the rule and outputs drifted to
  你好), ban 参与过 and 正是我想发展的方向, and list five question shapes,
  because all four greetings had converged on 「更偏 A 还是 B？」.

Grounding never moved through any of it: a warmer register may not buy itself
one fabricated fact, and a number that is not in the résumé is a fabricated
fact.

`PROMPT_VERSION` is `v6`, so **only newly analyzed jobs get the new
greeting**. 投递队列's 刷新招呼语 refreshes the listed ones, and it needed no new
endpoint or service: `analyze-batch/plan` then `analyze-batch` with
**`force=false`**, because `analysis_cache_key` already contains the prompt
version. A job analyzed under the current prompt is a cache hit and costs
nothing; one written by an older prompt is a miss and gets rewritten. So the
plan's existing `cached` / `pending` split *is* the count of out-of-date
greetings, and the confirmation states it before anything is spent. Measured
when it shipped: 21 listed, 4 already current, 17 calls.

Re-analysis rewrites the score and verdict too, not only the greeting, and the
dialog says so - a refreshed job can move a point or two. Everything already cached keeps the old one until it is
re-analyzed, which costs a call - the per-job 重新分析 on the job page is the
cheap way to refresh one.

### Browser capture (v0.2)

```
visible browser -> BossJobSource -> RawJobPosting -> job_intake.save_posting()
                                                     -> the v0.1 normalizer,
                                                        hash, dedup, Job model
```

`services/job_intake.py` is the only place a Job row is created. Manual paste
and browser capture both call it, so a captured job is indistinguishable
downstream - **JobMatchAgent must never learn where a JD came from.**

Rules for anything under `job_sources/`:

- the browser is **always headed**; there is no headless switch for it;
- the **human** logs in, searches, navigates and clears any verification;
- **never** store or ask for recruitment-site passwords;
- **no** CAPTCHA solving, anti-bot evasion, stealth flags or fingerprint
  spoofing - if a site blocks automation, report that and stop;
- no search-result crawling, no pagination, no bulk collection;
- never click 立即沟通 / apply / send. Collection only. This applies to
  `job_sources/` without exception: the M6 human-confirmed application gate
  belongs to the Chrome extension alone and never to this Playwright browser.
- **all selectors live in `job_sources/boss/selectors.py`** - never inline a
  site selector anywhere else;
- automated tests must **never** touch live zhipin.com. Use the HTML fixtures
  in `backend/tests/fixtures/`.

Profile: `data/browser_profiles/boss` (gitignored) holds cookies and the login
session so the user logs in once. It is a dedicated profile - never the user's
day-to-day Chrome/Edge profile. Passwords are never persisted separately.

`BROWSER_CHANNEL` defaults to `auto`: Playwright's Chromium first, then system
Edge, then Chrome. The fallback exists because Playwright's bundled `chrome.exe`
will not start without the MSVC runtime (side-by-side error) on some Windows
machines; every candidate still uses our own profile directory.

Never log cookies, storage state, page HTML, or full URLs - BOSS puts session
tokens in the query string, so use `redact_url()` / `canonical_url()`.

### Quick Capture (v0.3) - the preferred intake method

```
user copies / screenshots in THEIR OWN browser
        -> QuickCaptureParser (deterministic)
        -> optional single AI extraction pass
        -> JobImportCandidate
        -> USER REVIEWS AND EDITS          <- mandatory, never skipped
        -> job_intake.save_posting()       <- the same path as everything else
```

Recruitment sites are **human-controlled**. The backend processes only content
the user explicitly hands over. It never fetches a URL, never opens a site,
never injects script, never uses an extension, and never reads site cookies or
localStorage. A supplied URL is metadata: `detect_source()` reads its hostname
and `canonical_url()` strips the query before storage. That is all.

Clipboard reading lives in the **frontend**, fires only on an explicit click,
and is never polled - turning it into background monitoring is forbidden.
`http://127.0.0.1` is a secure context, so the Clipboard API is available; when
the browser denies it, the UI falls back to `Ctrl+V`.

Screenshots are **ephemeral**: validated by magic bytes, hashed, sent for
extraction, then dropped. Nothing is written under `data/`, and image bytes are
never logged.

`job_import_parser.py` is site-agnostic on purpose - it keys off Chinese
recruitment *conventions* (salary/experience/education token shapes, section
headings), not any site's DOM. Do not add site selectors to it; that is what
`job_sources/boss/selectors.py` is for.

Extraction and matching are **two separate AI calls**. Quick Capture never
triggers `JobMatchAgent`; the user clicks AI分析 afterwards, as always. AI
extraction runs only when the deterministic result is weak
(`needs_ai_extraction`), and its result is cached on
`sha256(content, IMPORT_PROMPT_VERSION, model)`. A missing key or an AI outage
must never break deterministic text import.

### Application workflow (v0.4) - the rule that matters

```
AI verdict   = a recommendation      (JobAnalysis.verdict)
Job.status   = what the HUMAN did    (applied / skipped / replied / ...)
```

**Never let model output write a human status.** A `skip` verdict means the
queue does not propose the job; it must not mark it skipped. Nothing in the
codebase may set `Job.status` from an analysis result.

Every status change goes through `services/application_workflow.py`. Routes
call it; they never touch `Job.status` themselves. Each transition appends an
`ApplicationEvent`, and that trail is **append-only**: undoing a mistake
appends `status_reset` rather than deleting history, so a job can legitimately
read `applied -> status_reset -> applied`.

`mark_applied` requires an explicit `confirmed=true`, and there is deliberately
no bulk version: "applied" describes a real action the user took on a
recruitment platform. JobAgent still submits nothing and messages no one.

#### Batch back-recording of 已投递 (user authorized 2026-09-02)

The user's authorization, verbatim: *allow batch back-recording of the 已投递
status for jobs **already in the library**, behind one explicit confirmation
displaying the exact count; batch execution of applications remains forbidden,
an AI verdict may still never write a human status, and a link or entry that
matches nothing may never create a job.*

This supersedes only the "no bulk version" clause above, and only for
*recording*. The distinction is the whole point: **executing** an application is
an action JobAgent takes on a recruitment site, and stays per-job and forbidden
in bulk (M6). **Recording** describes applications the human already made
themselves, and refusing to record them in bulk does not prevent anything - it
just leaves the funnel, the resume attribution and the 已投递 view wrong. A day
of manual applying was producing exactly that.

`services/applied_backfill.py` is the only implementation. Its rules:

- **the site is never touched.** The text arrives because a human selected it
  in their own browser and copied it, exactly like Quick Capture (v0.3). No tab
  is opened, no conversation is clicked, no request is made. This is
  deliberately *more* restrictive than the suspended M7 scan and re-enables no
  part of it;
- **matching runs backwards.** It does not parse BOSS's layout - that would be
  guessing at an undocumented format, and a layout change would become silently
  wrong records. It asks which *already stored* jobs appear in the pasted text,
  so an entry with no stored job simply does not appear;
- **a company-only hit is never pre-selected.** Several roles at one company is
  normal, and the wrong pick records an application that did not happen;
- **the count is part of the confirmation.** `expected_count` must equal the
  number of jobs actually being recorded, so a selection that moved between
  reading the dialog and pressing the button cancels rather than recording a
  different set;
- **every job still goes through `application_workflow.mark_applied`** with its
  own `confirmed=true` and its own event. There is no second status-writing
  path, a forbidden transition (a skipped job) is reported rather than forced,
  and one bad row never loses the rest;
- nothing here submits, greets, favourites or messages, and no AI call is
  involved at any point.

The queue itself is **derived** (Job + latest JobAnalysis + latest event), not
stored - so a re-analysis or a status change is reflected immediately and there
is no second table to drift. Eligibility keys off the verdict, not a score
threshold. `Job.review_after` (稍后处理) is a scheduling hint that leaves status
untouched; nothing polls it, the queue just compares it to now.

Daily metrics convert UTC timestamps to `REPORT_TIMEZONE` (default
`Asia/Tokyo`) before comparing dates - never compare naive UTC dates.

### Database migrations (since v0.4)

Alembic owns the schema. `init_db()` creates a fresh database and stamps head,
adopts an un-stamped v0.3 database at the baseline revision, or upgrades one
already under Alembic. **Deleting the database is never the answer** - add a
migration.

Two migration hazards this repo has actually hit:

- Alembic's SQLite **batch mode rebuilds a table by dropping it**, which
  fires every `ON DELETE CASCADE` pointing at it. 0005 wiped `job_analyses`
  that way until it suspended foreign keys around the rebuild. Prefer plain
  `CREATE TABLE` / `ADD COLUMN`;
- a migration must repeat the model's **server defaults**. `TimestampMixin`
  leaves `created_at` to the database, so a migration that omits
  `server_default` produces a schema that rejects every insert - on
  upgraded databases only, which is exactly what real users have;
- **mutually referencing tables have no valid delete order.** `offers`
  and `offer_revisions` point at each other, so `RESTRICT` on the
  snapshot columns made a job carrying an accepted offer impossible to
  delete. They are `SET NULL`, and `offers.applied_event_id` is
  `CASCADE` - the event and the offer are both children of the job, so
  `RESTRICT` protected nothing there and only blocked deletion.

One more thing Alembic does by default: `fileConfig` disables every
logger that already exists. Since `init_db()` runs migrations during
startup, that silently switched off the whole `app.*` logger tree for
the rest of the process. `alembic/env.py` passes
`disable_existing_loggers=False`; a test guards it.

```powershell
backend\.venv\Scripts\python.exe -m app.cli migrate
```

### Recruiter conversations (v0.5)

```
recruiter message (pasted / screenshot)
        -> deterministic signals (keywords, language, speaker split)
        -> RecruiterConversationAgent -> typed analysis + reply DRAFT
        -> USER EDITS THE DRAFT
        -> USER SENDS IT THEMSELVES, elsewhere
        -> 标记已回复 -> RecruiterMessage(direction=user) + candidate_reply event
```

**A draft is not a sent message.** JobAgent has no inbox access and no send
capability. Copying changes nothing; only an explicit `confirmed=true`
mark-sent records that the human sent something - and it stores the user's
**edited final text**, never the model's draft.

**Recruiter analysis never mutates `Job.status`.** Detecting "this looks like
an HR reply" or "they proposed an interview time" only surfaces a button. The
status still moves solely through `services/application_workflow.py`, exactly
as in v0.4.

Two tables, two purposes - never merge them:

| table | holds |
| --- | --- |
| `RecruiterMessage` | what was actually said |
| `ApplicationEvent` | what happened in the workflow |

An event may reference `conversation_id` / `message_id` in its
`metadata_json`; message bodies are never copied into the event trail.

Grounding: the agent may treat only the active resume, the career strategy and
the linked job as fact. Anything else goes in `missing_information`, and the
draft uses a `【请填写】` placeholder. `enforce_grounding()` in
`recruiter_message_analyzer.py` is the mechanical backstop - a quoted salary
survives only if its figures match what the user configured.

Context is bounded (`MAX_CONTEXT_MESSAGES` in `agents/recruiter_prompts.py`):
a running summary plus the last few turns, so a long thread never grows the
prompt without limit.

Cache key covers message content, job context, resume, strategy, model, prompt
version and reply language - so re-linking a job or switching language
re-analyses, while an unrelated application event does not.

Privacy: message bodies, screenshots, phone numbers and emails are never
logged. Screenshots reuse the v0.3 validator and are dropped after extraction.

### Career strategy analytics (v0.6) - the rule that matters

```
DATA OBSERVES   ->   SYSTEM SUGGESTS   ->   HUMAN DECIDES
```

**Zero OpenAI calls.** Every number is a count or a ratio over rows the
human created, so the page is free, reproducible and fully testable. There
is no code path from analytics to an agent; a test asserts it.

Three denominators, defined once in `services/application_analytics.py` and
used everywhere:

- `applications` - jobs with an *effective cycle* (see `application_cycles.py`)
  whose `applied` event falls in the window. A cycle undone by `status_reset`
  is not an application - the user withdrew it, they did not fail at it;
- `mature_applications` - already replied to, **or** at least
  `RESPONSE_MATURITY_DAYS` old. Something applied to this morning has not
  failed, it has not had time. This is the denominator for reply rates;
- `interview_rate` - over applications mature by `INTERVIEW_MATURITY_DAYS`.

Honesty rules that are load-bearing, not decoration:

- a rate with a zero denominator is `null`, never `0%`;
- every rate travels with its numerator, denominator and Wilson 95% interval.
  The frontend has no code path that renders a bare percentage;
- tables rank by **confidence tier first, then the Wilson lower bound**. The
  lower bound alone is not enough: a perfect `2/2` scores 0.34 and would beat
  a solid `5/12` at 0.19, so a cohort labelled 样本不足 would end up presented
  as the best direction while the same row says the sample is too small;
- a small cohort that *looks* impressive is still called out by name with
  「样本不足，暂不做结论」. Sorting it to the bottom must not silence it - that
  is the row a reader is most likely to over-read;
- comparisons use the overall cohort **in the same window and under the same
  filters**, never every job ever collected;
- observations are template sentences, never model-written, so they can be
  recomputed and checked. They describe correlation and never claim cause.

A proposal must clear three bars before it is even shown as actionable:
at least `ANALYTICS_RECOMMEND_SAMPLE` mature applications, a Wilson *lower*
bound past the overall rate, and a non-`insufficient` confidence band.
Anything thinner becomes a `collect_more_data` note instead. In practice
this means an honest run often produces **no** recommendations, which is the
correct output, not a bug.

**Never auto-edit `career_strategy.yaml`.** Reading analytics, listing
proposals and previewing a diff all write nothing. Applying goes through
`strategy_recommendations.apply_proposal`, requires `confirmed=true`, reuses
`core.career_strategy.save_strategy`, and appends a `CareerStrategyChange`
audit row. A dismissal is remembered by a bucketed semantic signature, so
one extra application cannot resurrect it while genuinely new evidence can.

Strategy proposals reorder priorities. They never touch `Job.status`, never
reanalyse anything, and never submit anything.

### Resume variants (v0.7) - the rule that matters

```
OUTCOME ATTRIBUTION USES THE RESUME USED AT APPLICATION TIME.
Never whichever resume happens to be active today.
```

A `Resume` row **is** a variant - there is no separate table. It gains
`variant_name`, `variant_group`, `parent_resume_id`, `notes` and `archived_at`.

Two "current resume" concepts exist and must never be conflated:

| concept | where it lives | means |
| --- | --- | --- |
| 当前AI分析简历 | `Resume.is_active` | what a newly captured job is matched against |
| 本次实际投递简历 | the application cycle | what the human actually submitted, once |

Attribution is a typed `application_events.resume_id` FK, written when the user
confirms 已投递. A typed column rather than a key inside `metadata_json`: it
cannot go dangling, coverage is one SQL count, and `ondelete=RESTRICT` stops a
referenced resume from being deleted. The variant *name* is snapshotted into
`metadata_json` for readability; the id stays authoritative, so renaming a
variant never rewrites history.

`ResumeUsage` is `used` / `no_resume` / `unknown`. **`unknown` is an answer, not
a gap.** Every pre-v0.7 application is `unknown` forever unless a human says
otherwise - a migration that guessed from `is_active` would invent history. A
correction appends `application_resume_attributed` (or
`application_resume_changed`) naming its target `applied_event_id`; the original
`applied` row is never edited, and the correction is itself auditable.

Each cycle carries its own resume, so `applied(A) -> reset -> applied(B) ->
interview` credits **B**, and A's superseded cycle stays empty.

Analytics reuses v0.6 wholesale - same maturity windows, same Wilson intervals,
same confidence tiers, same "tier first, then lower bound" ranking. There is no
second definition of confidence. Unattributed applications count in overall
totals but are **never** folded into a named variant, and no variant is ranked
as a winner when attribution coverage is below 60% or fewer than two variants
clear the sample threshold.

Two comparisons that must stay visibly separate:

- **real outcome** (`by_resume`) - what recruiters did after applications that
  used each variant;
- **AI fit** (`fit_comparison`) - how the model scored each variant on the same
  JDs. A variant can read better to a model and still get fewer replies.

Resume A/B data is **observational**, never a randomised experiment: different
variants get used on different jobs, cities, salary bands and time periods. Say
「DevOps版在已记录样本中的成熟回复率更高」, never 「DevOps版带来了更高的回复率」.

Cost: outcome analytics is **zero OpenAI calls**, like v0.6. Only 比较简历 and
按其他简历分析 may spend, only on combinations with no cached analysis, and
比较简历 requires an explicit confirmation carrying the exact pending count.
The score matrix reads cached rows only - an empty cell stays empty.

**Never auto-edit resume content.** v0.7 tracks and compares variants; it does
not write them. 复制为新版本 clones metadata and content as-is. Analytics may
report that Kubernetes keeps appearing as a missing skill; it must never insert
that claim into a resume. Nothing auto-sets the active resume either.

Archiving replaces deletion: an archived variant stays in every historical
number and simply stops being offered for new applications. There is no delete
endpoint.

### Interview pipeline (v0.8) - the rule that matters

```
An InterviewProcess belongs to ONE application cycle, named by applied_event_id.
```

`ApplicationEvent` stays an append-only milestone trail. It is not the database
for a multi-round process whose schedules, interviewers and feedback all get
edited - those live in `InterviewProcess` / `InterviewRound`.

Attribution follows `applied_event_id`, resolved once at creation and never
re-derived. `applied(A) -> reset -> applied(B) -> interview` credits **B**, and
which resume gets the credit comes from B's own `applied` event (v0.7). Never
from `Job.status`, never from the active resume. A unique constraint enforces
one process per cycle - a second would double-count the same candidacy in every
funnel.

`Job.status` remains the coarse human status. Recording a round moves it to
`interview`; nothing here ever forces `offer` or `rejected`, which stay explicit
human actions through `application_workflow`. Offer and rejection reuse those
existing paths and additionally close the process - one definition each.

Events are emitted per **milestone**, not per field edit: adding a round,
recording a result, cancelling, withdrawing, correcting. Editing an interviewer
name emits nothing.

Honesty rules:

- **a withdrawal is not a rejection.** `candidate_withdrawn` and employer
  rejection are counted separately everywhere; merging them would turn the
  user's own decisions into a story about failing;
- **a pending outcome is not a failure.** Round pass rates exclude pending
  rounds from the denominator;
- **stages come from recorded rounds**, never from `Job.status`. A job sitting
  at `interview` says a human clicked something; only rounds say what happened;
- **a rejection never erases prior passed rounds.** Four cleared rounds stay
  four cleared rounds;
- **an outcome is never silently overwritten.** Re-recording one requires
  `correction=true` and appends `interview_round_corrected`; the original result
  event stays in the trail.

**Never infer historical rounds.** Pre-v0.8 `interview` events are surfaced as
legacy milestones with unknown round detail and are excluded from every stage
statistic. Mapping a generic old event onto "HR" or "technical" would fabricate
history. The user may add rounds manually.

**AI scheduling detection never creates an interview.** The v0.5 recruiter agent
can spot "are you free Wednesday?"; that produces a *suggestion*, and only an
explicit human action creates a round. Reading suggestions calls no model.

Interview analytics are deterministic - **zero OpenAI calls** - and reuse
`statistics.py` wholesale: same maturity windows, Wilson intervals, confidence
tiers and "tier first, then lower bound" ranking. There is no second definition
of confidence.

Privacy: `meeting_url` routinely embeds an access token. It is never logged and
never enters an analytics payload, along with interviewer names and feedback
bodies; only the interview UI and its own endpoints return it. Analytics
aggregates structured, user-chosen values only - feedback tags and reasons -
and never interprets free text.

### Offers and negotiation (v0.9) - the rules that matter

```
An Offer belongs to ONE application cycle, named by applied_event_id.
A candidate counter is NOT a company offer.
Accepted compensation is frozen to accepted_revision_id.
```

`ApplicationEvent` stays the milestone trail. Compensation changes during
negotiation, so it lives in `Offer` / `OfferRevision`. Events carry
`offer_id` / `offer_revision_id` references and **never** a salary figure.

Negotiation history is append-only. A revision is never edited to reflect a
later round; correcting one appends a new revision pointing at it via
`corrects_revision_id`.

**`latest_company_revision` filters on `source`.** If the sequence is
`company 330K -> candidate counter 350K`, the company's offer is still 330K.
Reading "the latest revision" would report the user's own ask back to them as
an offer - the single most misleading thing this feature could do. Accepting or
declining against a candidate revision is refused outright.

**Accepting freezes `accepted_revision_id`**, and every later read of "what did
I accept" goes through that id. A revision added afterwards, or a correction,
must not move it. Analytics for accepted offers reads the same snapshot.

Compensation methodology, defined once in `offer_calculator.py`:

- **guaranteed vs target** - base + *guaranteed* bonus + recurring extras is
  what you can count on; the target bonus is a separate, labelled figure.
  Collapsing them is how offer comparisons mislead;
- **first year vs steady state** - a signing bonus is paid once, so it appears
  in first-year figures and is excluded from steady state;
- **equity is not cash** - a grant becomes a per-year number only when both a
  value and a vesting period exist. Otherwise it is excluded and says
  「未计入可比较总包」. An option grant is never guaranteed money;
- **missing is not zero** - every function returns `None` when its inputs are
  absent. A present `0` is real; an absent field is unknown.

**Never compare compensation across currencies** without a rate the user typed.
v0.9 fetched none at all; v1.0 will convert, but only using
`DecisionProfile.fx_rates_json`. With no rate, 400K CNY and 8M JPY stay two rows
in their own units. The v0.9 comparison page names no winner at all and folds no
non-cash factor into a score.

Offer actions require explicit confirmation: recording, accepting, declining
and expiring all take `confirmed=true`. A passed deadline is *displayed* as
passed; nothing is marked expired automatically. Declining is the candidate's
decision and never moves `Job.status` to `rejected`.

The v0.5 recruiter agent may detect offer discussion; that produces a
suggestion only. No offer is ever created from message analysis.

Privacy: salary, bonus, equity values, offer notes and pasted offer text are
**never logged**. Log lines carry `offer_id`, `job_id` and status only.
Analytics returns compensation because the page needs it; unrelated endpoints
do not.

### Offer decision support (v1.0) - the rules that matter

```
THE USER WEIGHS.  THE USER RATES.  THE SYSTEM ONLY DOES ARITHMETIC.
```

**Zero OpenAI calls**, like v0.6. A test asserts it. Every number on the page
can be recomputed by hand from the weights and ratings a human entered, which is
the only reason a score is trustworthy enough to show at all.

`services/offer_decision.py` is pure - no DB, no network. `decision_support.py`
is the only place `DecisionProfile` / `OfferAssessment` / `DecisionSnapshot`
change, and the only bridge between stored offers and the engine.

The rules that make the score honest rather than impressive:

- **the app has no opinion.** `DEFAULT_WEIGHTS` is deliberately `{}`. All-zero
  or empty weights yield `{}` and therefore no score - never an invented even
  split. Weights are stored raw and normalized only at calculation time, so
  5/3/2 and 50/30/20 are the same thing and the user's own numbers survive;
- **unknown is not zero.** A dimension with no rating and no recorded fact is
  *excluded* from the weighted average - it does not score 0 and quietly sink an
  offer. What it does instead is lower `coverage`;
- **below the coverage floor, nothing wins.** Under
  `OFFER_DECISION_MIN_COVERAGE` (default 0.7) the comparison reports every score
  and names no winner. So does a tie, and so does having no weights;
- **never a bare total.** Every response carries each dimension's score, weight
  and `weight × score` contribution, plus `coverage`. The frontend has no code
  path that renders a total on its own;
- **urgency is not quality.** `days_to_deadline` / `deadline_state` travel
  *beside* the score and never enter it. A deadline tomorrow makes an offer
  urgent, not better;
- **AI never rates a company.** The 1-5 ratings are human-only. `None` removes a
  rating, because "I no longer have a view" is not "I rate it 1";
- **deal-breakers report, they do not reject.** `passed` / `failed` / `unknown`,
  where `unknown` means the fact was never recorded - penalising that would
  punish incomplete data rather than a bad offer. Nothing is auto-declined and
  nothing is dropped from the comparison.

Compensation reuses v0.9 wholesale: `accepted_revision(...) or
latest_company_revision(...)`, so **a candidate counter never scores**, and an
accepted offer reads its frozen `accepted_revision_id`. Guaranteed cash carries
4x the weight of target cash; unvalued equity contributes nothing.

**FX is user-entered only.** `_rate_for()` returns `None` when the user supplied
no rate, which marks the offer non-comparable on compensation rather than
converting it at a guess. v1.0 fetches nothing and looks nothing up.

**A `DecisionSnapshot` is frozen.** It stores the offers, the exact revision each
figure came from, the raw and normalized weights, the ratings, the FX rates, the
deal-breakers and the results as one document. There is no update path and no
`updated_at` - editing any input tomorrow must leave it untouched, because a
past decision should stay explicable in terms of what was known when it was
made.

Accepting still goes through `offer_management`. v1.0 only *shows* the other
undecided offers first; it never blocks the acceptance and **never declines a
competing offer automatically** - saying no to three companies is the user's
call and often has an order to it. A below-minimum offer produces a warning and
nothing else.

Privacy: weights, 1-5 ratings, FX rates, negotiation targets, decision notes and
deal-breaker values are **never logged** - they describe what the user privately
values and what they will settle for. Log lines carry ids and counts only
(`weighted_dimensions`, `rated_dimensions`, `offers`).

### Chrome extension POC - the rules that matter

```
THE HUMAN OPENS THE PAGE.  THE HUMAN CLICKS.  THE EXTENSION ONLY READS.
```

A Manifest V3 extension that runs in the user's **normal, already-logged-in**
Chrome. It is a feasibility test, not a product surface - `extension/README.md`
says plainly that live BOSS compatibility is unproven until the user checks it.

```
human opens a BOSS page  ->  clicks 检测当前页面
        -> content script reads the DOM once, replies with structured fields
        -> POST /api/extension/jobs/preview   (writes NOTHING)
        -> human clicks 导入 on one job
        -> POST /api/extension/jobs/import    -> job_intake.save_posting()
```

Same rules as `job_sources/` (they are the same product promise, a different
transport):

- **no automation.** No scrolling, no pagination, no clicking a job, no
  searching, no applying, no messaging. There is no timer, no
  `MutationObserver` and no background poll - a detection happens because a
  human clicked, or it does not happen;
- **no credentials of any kind.** Cookies, `localStorage`, `sessionStorage`,
  form values and auth headers are never read. A test greps the built bundle
  for those APIs, so removing the rule means deleting the test;
- **no stealth.** No CAPTCHA handling, no fingerprint changes, no anti-bot
  work. A verification interstitial is *reported* to the human and nothing else;
- **no Playwright, no CDP.** The extension is the whole mechanism;
- **all selectors live in `extension/src/boss/selectors.ts`** - the same rule
  `job_sources/boss/selectors.py` follows, for the same reason. Multiple short
  fallbacks per field; never a generated `div > div:nth-child(3)` path;
- **structured fields only.** `document.body` is never sent. The description
  comes from the JD container, with page furniture stripped on a *clone* so the
  page the user is looking at is never modified.

URLs are cleaned twice: the extension returns `scheme + host + path`, and
`canonical_url()` strips the query again server-side. BOSS puts `lid` /
`securityId` in the query, so neither the database nor the logs ever see one.

`services/extension_intake.py` has two operations and the difference is the
point: `inspect()` normalizes and hashes exactly as a save would and then
**writes nothing**; `import_one()` writes, once, through
`job_intake.save_posting`. **There is no parallel persistence path** - an
extension job is indistinguishable downstream from a pasted JD.

Two ownership rules the 2026-09-05 runs proved were missing:

- **the toolbar popup never stops a session it did not start.** A run started
  from the console has no pointer in `session.ts`, and the popup was treating
  that as a zombie and stopping it - 0.6s after the session was created, while
  the console tells the user to click that very icon to grant `activeTab` for
  salary OCR. It now reports the other entry point's run and touches nothing,
  the same way the worker's `resolveSessionForTab` already refuses another
  owner's tab;
- **a run that ends releases its prepare lock.** `navigatePrepareForTab`
  deliberately keeps the lock on a *denied* prepare, because the overlay's
  hard-stop releases it once its stop is confirmed. The runner's own
  termination never did, so one denied prepare made every later run on that
  tab fail in 70ms with `prepare_in_flight` until the worker restarted.
  `stopRunnerSession` now releases it where it already clears the global
  pointer - that *is* the confirmed stop.

**The approved per-task number counts NEW jobs, not opened details (user asked
2026-09-05).** A posting the library already holds costs nothing: it is skipped
before the click when the URL or the card signature says so, and when only the
description reveals the duplicate it costs an *open* but not one of the
requested jobs. Before this, a direction could report "8 of 8" having collected
nothing at all.

Two numbers, both enforced and both shown:

- the human's number (1-20) is the **target of new jobs**. It lives on the
  task (`max_candidates`) and stops the task as soon as it is met;
- **opened details stay hard-capped at 20** - the immutable ceiling from M4
  section 1a, which nothing may raise. It is what the `SupervisedSession`'s
  `candidate_cap` carries, because that is the count the backend denies on,
  and it is what ends a task whose target can never be met.

Neither hides behind the other: the console states both before a run, and the
overlay shows 新岗位 x/target beside 已打开 y/20. A pause/resume never gives back
a spent *open*; it does continue collecting toward a target that was not met,
because progress toward it is preserved too.

**Salary segments now come from BOSS's own menu (user asked 2026-09-05).**
This module still holds no table of filter codes and still cannot say what
`406` means - what changed is where a code comes from. The extension reads the
results-page 薪资待遇 menu on a tab the human already has open
(`readSalaryFilterOptions`, read-only: it opens no menu, clicks nothing,
changes nothing), and the console shows every band **with its code** and asks
the human to check one against BOSS's own URL before using it. A band whose
code cannot be found is reported as such, never paired with a neighbour's.

`salary_codes` on the quick-prepare request becomes one segment each, through
the same `filter_sets` path a pasted URL already used - there is no second
segmentation mechanism, and the same `_VALUE` pattern validates both, because
the value ends up in a URL the extension will navigate to.

The fixtures for this reader are **authored, not captured**: no save of the
BOSS filter bar existed, and no automated test may visit zhipin.com to make
one. The tests pin the reader's logic; whether it matches the live bar stays a
manual claim the user makes in their own Chrome - which is exactly why the
console displays the codes rather than hiding them.

**A card the strategy excludes is skipped before the click (2026-09-05).** The
same discipline the early-career title filter already followed, for the same
reason: an opened detail is the scarce resource. `excluded_title_keywords` on
the task carries `career_strategy.yaml`'s own `excluded_keywords`, read fresh
on every task read so an edited strategy applies to a task created last week,
and never written back.

Matched on the **card's title only** - the card carries no description, and a
guess about the JD is exactly what this must not make. Measured on the library
it was added against: 21 stored jobs had one of these words in the title, and
the model judged 20 of them `skip` and none `apply`, so those opens bought
nothing. All three pre-open skips (already stored, early-career, excluded
title) now report their `last_action`, because a run that skipped everything
used to look from the console like a run that did nothing.

`POST /api/extension/jobs/known` is what keeps a run from spending its budget
on work already done. A candidate slot is an *opened detail pane*, so the only
place a duplicate can be skipped for free is before the click. Two answers, and
the second is the one that matters in practice:

- `(source, external_id)` - the posting itself is stored;
- **the card signature** - company, title, salary, experience *and* city all
  agree with a stored BOSS job. BOSS re-lists the same posting under a new
  `job_detail` id, so the id check says "new", the run opens it, and the
  content hash then reports a duplicate and imports nothing. Over one real
  15-task run that was 66 of 78 opened details.

The signature is deliberately the *whole* card and nothing more. Company and
title alone collided for 18 rows in a 581-job library; all five fields collided
for 2. A card is never matched on a partial agreement, because the description
- which decides the real duplicate - is not on the card, and a wrongly skipped
posting never enters the library at all. When one side has **no** salary the other four decide it. BOSS hides most card
salaries behind a private-use font, so the card reads empty while the stored
job carries a figure the OCR or the salary backfill supplied later - 409 of the
632 jobs in the library this was written against were created that way, and
requiring the salary to agree sent the run to reopen them on every run. The
cost is measured too: on that library the full key collided for 2 rows and the
salary-less one for 7.

The endpoint still writes nothing, and
an extension build that sends no `cards` behaves exactly as it did before.

Both endpoints refuse a non-loopback peer. The server already binds to
127.0.0.1; the guard makes that an assertion rather than a deployment
assumption. `CORS_ORIGIN_REGEX` matches the *shape* of a Chrome extension
origin because an unpacked extension's id is machine-specific.

Extraction is tested by injecting the **built** `dist/boss/*.js` into a headless
Chromium page holding a local fixture and calling the real `BossExtract.detect()`.
There is deliberately no Python re-implementation to drift out of sync, and no
automated test may ever touch zhipin.com.

### Chrome extension — M4 supervised navigation policy

#### Local console launch amendment (user authorized 2026-08-28)

The normal Chrome localhost console may explicitly start/pause/resume/cancel ONE existing
SearchTask through a narrow MV3 content-script bridge. Only top-frame
http://127.0.0.1:5173/#/console or http://localhost:5173/#/console is trusted; the worker
rechecks sender and current tab URL. Page load/status refresh never starts or resumes work.
Only explicit human activation sends mutations. The console supplies task id/candidate cap,
never arbitrary URLs, script, browser identifiers or paid approvals.
The existing worker loads the backend-owned search URL, creates one visible tab in the same
foreground normal Chrome window on start, and reselects only its owned BOSS tab on resume.
No second browser, driver, job store, intake path or task queue. No automatic task chaining.
Exact BOSS and localhost:5173 host access is allowed for this handoff; no all-sites permission.
No background browsing or window activation: existing foreground/identity/verification and
20-candidate/5-scroll caps still apply. This entry is search-only; it cannot start paid matching
or resume a paid run. A completed task is never silently reset by plan generation or start.
Console clicks do not grant activeTab to the created BOSS tab. Screenshot OCR may therefore
remain unavailable; do not broaden permissions to all websites to hide this limitation.
This supersedes only conflicting old popup-only/no-tab-create clauses. Offline fixture
acceptance does not authorize a real task start or certify live Chrome compatibility.

#### Local salary OCR amendment (user authorized 2026-08-28)

On missing salary, explicit detail Detect or an approved foreground runner candidate may
use `tabs.captureVisibleTab` with the existing `activeTab` grant. The full bitmap remains
transiently in the extension worker; only an exact-identity, visible, unobstructed salary
crop (maximum 800x160 PNG pixels / 256 KiB) reaches the loopback OCR endpoint. No image
is saved, logged or sent externally. Windows-local OCR is not a browser controller or paid AI.
Two scale readings must agree on a strict salary parse; reject lost units/month suffixes.
Record OCR provenance for human checking in the existing intake creation note.
Recheck identity, foreground tab/window, region and cancellation before/after capture/OCR.
Verification or changed context discards the result. Hidden/ambiguous regions stay unknown:
no extra scrolling, retries, navigation or budget expansion for OCR. Bounded request
deadlines are permitted, not passive polling. Existing intake and forbidden-action rules remain.

#### M4e/M4f authorized amendment (2026-08-25)

The user has explicitly authorized M4e deterministic SearchPlan and M4f bounded automatic search
runner. This amendment supersedes only the conflicting M4a-M4d clauses below; every privacy,
origin, extraction, intake and forbidden-action rule remains in force.

- Reuse `JobSearchTask`, `TaskCandidate`, `SupervisedSession`, the MV3 extension, loopback FastAPI
  and `extension_intake.py -> job_intake.save_posting()`. No second job/task persistence or dedup
  pipeline.
- SearchPlan deterministically expands configurable city x keyword inputs and creates no duplicate
  pair. Ordinary combinations never call an AI model.
- A human starts or resumes the selected SearchTask. After that explicit action, one foreground
  Chrome tab may sequentially navigate to its allowlisted BOSS search URL, collect rendered cards,
  open/capture new candidates through the existing right-side detail panel, and continue bounded
  scrolling without another click per step. Only one SearchTask and one browser operation may run
  at a time; it never advances to another task without a separately visible runner decision/state.
- BOSS results are one continuous list. Pagination and page controls are forbidden. Identity is the
  query-stripped `https://www.zhipin.com/job_detail/<id>.html` URL/external id.
- Bounded DOM-stabilization waits are allowed inside the running foreground task. They must have
  configuration-owned timeouts and termination counters; no unbounded polling, background/hidden
  execution, 24/7 scheduler, random anti-detection timing or concurrent navigation.
- Existing immutable ceilings remain: one session/task, one foreground tab, at most 20 processed
  candidates and at most 5 scroll rounds per SearchTask. Config and human approval may lower, never
  raise, these limits. Consecutive no-new rounds is configurable and bounded (default 3).
- Task states are `PENDING`, `RUNNING`, `PAUSED`, `PAUSED_VERIFICATION`, `COMPLETED`, `FAILED`, and
  `CANCELLED`, with persisted timestamps, observed/new/duplicate/no-new counters and last error.
- Human pause/stop, caps, no-new threshold and normal completion stop all browser actions. Login,
  CAPTCHA, verification, security/risk-control or rate-limit signals transition to
  `PAUSED_VERIFICATION` and stop all browser actions; only an explicit human resume may continue.
  There is no bypass or automatic retry. Wrong origin, ambiguous identity/selector and
  unrecoverable navigation/extraction errors fail closed.
- Automatic apply, favorite/follow, recruiter messaging, OpenAI scoring orchestration, `/wapi/`,
  cookies/storage/auth tokens, `securityId`, Playwright/CDP, hidden tabs, stealth, fingerprint
  spoofing and CAPTCHA solving remain unconditionally forbidden. "Automatic apply" means exactly
  that: no search runner, batch, timer or model result may reach an application. The separate M6
  gate is not automatic - it requires one explicit human confirmation per job - and it is never
  reachable from a search run.
- Automated tests remain fixture-only. Code acceptance requires one minimal real logged-in Chrome
  verification before M4e/M4f may be marked live-compatible.

#### M4g bounded SearchPlan batch amendment (user authorized 2026-08-29)

The user explicitly authorized one finite SearchPlan batch from the local console. This supersedes
only the M4 clauses that prohibit advancing to another task after the already-approved task completes.
All privacy, foreground, verification, extraction, intake and forbidden-action rules remain in force.

- P3A personal comprehensive search (user authorized 2026-09-01) supersedes only the original
  five-task ceiling below: one explicit confirmation may bind at most sixteen already-created
  `pending` tasks, covering at most eight configured resume/career directions. The primary UI
  presents this as one aggregate portfolio and need not expose internal execution order or task ids.
  All tasks are still validated up front and executed strictly one at a time.
- One explicit in-page confirmation names an ordered list of at most sixteen already-created `pending`
  SearchPlan task ids and one per-task candidate cap. The worker re-reads and validates every task;
  the page never supplies a URL, tab id, script or paid approval.
- The extension runs one task and one browser operation at a time in one owned, visible foreground
  BOSS tab. Only normal completion may advance to the next approved pending task. Failure,
  cancellation, verification, login/risk control, foreground loss, identity ambiguity or an invalid
  next task stops or pauses the whole batch without starting another task.
- Existing per-task ceilings remain unchanged: at most 20 candidate attempts and five scroll rounds.
  Batch size is an additional hard ceiling of five; callers may lower but never raise any ceiling.
- The batch pointer may use trusted `chrome.storage.session` and contains only task ids, order,
  counters/status and the owned tab id. It is not a second backend task/job queue or persistence path.
  A service-worker/browser/extension restart never auto-resumes browser work; an explicit visible
  console confirmation is required and all remaining task/candidate/scroll budgets are preserved.
- No clock/timer start, scheduler, 24/7/background/hidden run, terminal-task reset, automatic retry,
  paid AI/matching, apply/favorite/follow/message, credential/token/storage extraction, `/wapi/`,
  stealth, CAPTCHA bypass, fingerprint spoofing, Playwright or CDP is authorized.
- Fixture-only code acceptance precedes a separately confirmed live run of at most two tasks with
  candidate cap one. A live run is not started merely by this implementation authorization.

**Sixteen directions per run (user authorized 2026-09-05).** `MAX_SEARCH_DIRECTIONS`
rises from 8 to 16, superseding only P3A's "covering at most eight configured
resume/career directions" clause. Nothing else moves: the batch is still at most
sixteen already-created `pending` tasks, each still opens at most 20 details over
at most 5 scroll rounds, and every stop condition is unchanged.

Why: a single-city run was filling half its own batch. 8 directions against a
16-unit ceiling, while `search_direction_ranking` had 18 directions to offer -
so the reachable set was 8 x 30 = 240 postings when the already-authorized
ceiling allowed 480. Measured the same day: a BOSS search returns exactly 30
cards and has no next-page control, so the *only* way to reach more postings is
more distinct queries. Directions are the one axis that needs no filter codes.

The search panel also now reads BOSS's salary bands **without being asked** -
one silent, read-only DOM probe per page load, only while nothing is running and
only when none are stored. If they are in the DOM it offers segmentation in one
click; if BOSS keeps them behind the menu the explicit button is still there.
Nothing about `boss_search_filters.py` changes: the codes still come from BOSS's
own page and are still shown with their labels.

#### Sixty opened details per task (user authorized 2026-09-05)

`RUNNER_MAX_CANDIDATES` / `MAX_CANDIDATE_CAP` rise from 20 to 60, superseding
only M4 section 1a's "at most 20 candidate jobs opened/extracted". Everything
else stands: one session, one tab, at most 3 results pages, 30 scroll rounds
per page, and every hard stop.

Measured on the run that prompted it: three directions stopped at exactly 20
opens with `no_new_rounds` 0 - still turning up new cards - having spent 3 of
their 30 scroll rounds. The budget ran out, not the list.

- **the human's own number is a different limit.** It counts NEW jobs
  collected, not details opened, and has its own constant
  (`RUNNER_MAX_TARGET`) so that raising what a run may open is never an
  accident of raising what the console may ask for;
- **the target may now be asked for up to the same 60**, and defaults to it
  (user asked 2026-09-05). It is not a policy ceiling - opens are, and they are
  capped at 60 whatever the target says; a target above that could never be
  met, and one below it only makes a direction stop earlier. Two numbers that
  differed only because one had not been raised yet is exactly the confusion
  the console showed: 「值は 20 以下にする必要があります」 on a field sitting
  beside the words 「最多打开 60 个详情」. `RUNNER_MAX_TARGET` stays a separate
  constant so raising one is never an accident of raising the other. The
  console remembers a typed value per browser.
- a card already in the library, an early-career title, or a title the strategy
  excludes is still skipped *before* the click, so a bigger budget is spent on
  postings the library does not have rather than on more of the same;
- my objection, recorded because the user asked for it anyway: this triples the
  detail clicks a single run makes on BOSS, and BOSS restricted this account
  once already (M7, 2026-08-31). Sixteen directions at the new ceiling is a run
  of hours rather than minutes. Every stop condition still applies, and pause
  and cancel still work, but the exposure is real and it is the reason to watch
  the first run rather than start it and walk away.

#### Scrolling reaches the list's end; 30 rounds per page (user authorized 2026-09-05)

`MAX_SCROLL_CAP` / `RUNNER_MAX_SCROLL_ROUNDS` rise from 5 to 30, superseding
only M4 section 1a's "at most 5 scroll steps per results page". Nothing else
moves: one session, one foreground-or-authorized-background tab, at most 3
results pages, at most 20 candidates opened per task, and every hard stop.

The reason the old 5 looked sufficient was a bug in what a round did.
`scrollResultsContainer` moved **one viewport height**. BOSS loads its next
batch when the list's end comes into view, so the first round reached the end
of 15 cards and pulled in 15 more - and every round after that landed in the
middle of a list whose end had moved further away. Every task on 2026-09-05,
every city, every keyword, reported exactly 30 observed cards and then nothing.
That read like a BOSS page size, and a whole round of work (bounded pagination)
was built on the misreading. A round now scrolls to the end of the list, which
is what a human dragging the scrollbar does, and rounds buy depth again.

- **the element that scrolls is found, not assumed.** `.job-list-box` is where
  the cards live but not necessarily the element with the scrollbar, and
  `scrollBy` on a container that does not scroll is a silent no-op - no error,
  no movement, no lazy load. On 2026-09-05 that pinned eleven of sixteen
  directions to BOSS's first 15 cards while two reached 60; the two that worked
  were the ones that opened many details, because clicking a card low in the
  list makes the browser scroll it into view and loaded the next batch by
  accident. `scrollableFor()` walks up a bounded six ancestors for the first
  one that is scrollable by style and has somewhere left to scroll, and falls
  back to the document. Confirmed by the user against the live page before it
  was written: manual scrolling on a keyword that had been stuck at 15 does
  keep loading.
- still exactly one `scrollBy` call site, still one scroll per approved round,
  still no `scrollTo`/`scrollIntoView` anywhere - a contract test pins the
  count, and only the distance changed;
- **depth costs scrolling, not opens.** The 20-candidate ceiling is untouched,
  and a card already in the library is skipped before the click, so a deeper
  run spends its budget on new postings rather than on more of the same;
- the consecutive-no-new threshold (default 3) still ends a direction that has
  genuinely run dry, so 30 is a ceiling and rarely a target.
- **a cap lives in two files, and they must be changed together.** The request
  schema (`schemas/supervised_session.py`) repeats every ceiling that
  `services/supervised_sessions.py` enforces, because the service imports the
  schema and the dependency cannot run the other way. Raising the scroll
  ceiling in the service alone made Pydantic reject the request before the
  service was ever reached, and the run failed with the generic
  「请求参数不合法」 - which names no field, so it reads to the user as "the
  button does nothing". `test_schema_bounds_match_the_service_ceilings` now
  pins the pair.

#### A backgrounded tab cannot deepen the list (measured 2026-09-06)

The 2026-09-04 background-search authorization traded away the foreground
check. What nobody knew then is that Chrome takes the search's depth with it.

Measured in a real Chrome tab on 2026-09-06, with a page that counts frames,
scroll events and `IntersectionObserver` callbacks: with the tab backgrounded,
`scrollTop` advanced 700 -> 1600 and all three counters stayed exactly where
they were. The list did not grow. Chrome runs the script of a tab it is not
painting; it does not run the rendering steps, and scroll events and observer
callbacks are delivered from those. BOSS loads its next batch from exactly
that machinery.

So a backgrounded search reads the first server-rendered batch - about 15
cards - and nothing more, however far it scrolls and however long it runs.
The 上海+杭州 run that prompted this measured it precisely: 15 of 16
directions reported **exactly 15 observed cards**, two barren scroll rounds
each, and every one of them ended `completed`. The sixteenth reported 30 -
the one moment the window came back into view. The same batch shape run in
the foreground the day before averaged 41 cards per direction.

`document.visibilityState` does not catch this: in the frozen tab it still
read `visible`. The page cannot tell, and neither can a check that asks it.

- **`ScrollResult.rendered` is the signal.** One `requestAnimationFrame` per
  scroll step - a single-shot callback, never a loop and never a poll - says
  whether a frame landed since the previous scroll. A frame counter rather
  than a timestamp, because `Date.now()` has millisecond resolution and a
  frame can land inside the same millisecond that armed it;
- **a frozen tab pauses the run, it never completes it.** `pauseForFrozenTab`
  posts a plain `pause` with reason `background_not_rendering`, keeps the
  pointer and every budget, and stops the batch. A completed task is a claim
  that the list ran out, and that claim was false sixteen times in a row;
- **the console says what to do**: leave the BOSS window visible - unfocused
  and mostly covered is fine, minimised or completely covered is not - then
  press 恢复. Chrome keeps painting a visible-but-unfocused window;
- the checkbox no longer promises what it cannot deliver. 「后台运行」 now
  states the constraint instead of implying a covered window is fine;
- nothing else moves. Every ceiling, every hard stop, the foreground-only
  application and salary capture, and the opt-in flag itself are unchanged.

#### Bounded results pagination (user authorized 2026-09-05)

The user authorized the search runner to use the results pages M4 section 1a's
immutable ceiling already permitted - **at most 3** - superseding only the
M4e/M4f clause "Pagination and page controls are forbidden". Nothing else moves,
and no ceiling is raised.

What made it necessary, measured rather than assumed: on 2026-09-05 every one
of 15 tasks reported **exactly 30 observed cards** and ended with two barren
scroll rounds. A BOSS search renders 15, one scroll loads a second 15, and
nothing loads after that. 30 is one BOSS page - so with `page_cap: 1` every
search could only ever reach the first 30 results, and a library that already
held them found nothing however long it ran. It also means the earlier reading
of "14 of 15 tasks hit the scroll ceiling" was wrong: they had already stopped
finding cards, and raising the scroll ceiling would have bought nothing.

- `runNextPageRound` is the only page advance, and it is the same
  detect -> prepare -> act -> confirm -> bounded-stabilize shape as
  `runScrollRound`. It clicks exactly one next-page control through
  `activateNextPage`, which refuses an ambiguous or disabled one;
- **the scroll budget is per page** - 5 rounds each - which is what M4 1a
  always said and what the backend's `record_navigation` already implemented
  by resetting `scrolls_used` on a confirmed `results` navigation;
- a page is left as soon as it has produced nothing twice, while the
  task-level no-new threshold (3) still has room - once *that* fires the task
  is completed, and a completed task is never reset;
- **no next control, a disabled one, or nothing new rendered after the click,
  all end the task normally.** Only an ambiguous control or a failed confirm
  is an error;
- the 20-candidate ceiling, the foreground/verification/login/risk-control
  stops, the one-tab rule and the batch rules are all untouched. Pages are
  visited in the same tab, one at a time, and never in the background beyond
  what the 2026-09-04 background-search authorization already allows.

The overlay used to state 「结果为连续滚动列表（无翻页控件）」 unconditionally -
a hardcoded sentence, never a detection. It now shows 第 N/3 页, so what the run
is actually doing is visible.

#### Background search amendment (user authorized 2026-09-04)

The user authorized a search run to keep going while its BOSS tab sits behind
other windows, so searching no longer holds the screen hostage. **Search only.**
Applying (M6) and the salary capture are untouched and stay strictly foreground.

My objection, recorded because the user overrode it knowingly: the foreground
rule was never really about the tab, it is what keeps a human in front of the
page while it acts. Backgrounded, a login prompt, a verification interstitial or
a risk-control page still stops the run - but nobody watches it happen, and BOSS
has restricted this account once already (M7, 2026-08-31). The user accepted
that and asked for it anyway. The run still reports its state to the console,
which is where they will find out.

What changed, and nothing else:

- `verifySearchTab()` in `extension/src/background.ts` is the search runner's
  only tab check and the single place the foreground half is dropped. The
  salary backfill and M6 call `verifyRunnerTab()` directly and keep it; a test
  reads the built worker and asserts neither of those bodies mentions the
  helper;
- **the tab must still exist and still be exactly `https://www.zhipin.com`.**
  Those are the checks that keep the runner off the wrong page, and they do not
  weaken when nobody is watching. Only "is it in front" was traded away;
- it is **opt-in, and remembered once chosen** (the reset-every-run behaviour
  was changed 2026-09-05: a run is hours long now, and a choice that silently
  reset itself killed one ten seconds in with `not_foreground` while the
  option sat collapsed out of sight). It is off until the human ticks it, the
  console states 「前台搜索（切走会立即停止）」 in bold while it is off, and the
  choice rides on the run's own `RunnerPointer.background`, so a resume after a
  worker restart continues as whatever it was started as and any older pointer
  with no flag resumes strictly foreground;
- **the run still starts in the foreground.** The tab is created or activated by
  the human's own click, exactly as before; what is relaxed is only what happens
  after they switch away. The search runner never re-activates the tab mid-run -
  the `active: true` updates left in the worker belong to start, to an explicit
  resume, to the salary backfill and to M6;
- **a backgrounded search skips the salary OCR entirely.** The local capture is
  strictly foreground (authorized 2026-08-28) and that has not moved one inch:
  what changed is that the runner no longer *attempts* it when there is no
  foreground to attempt it in. The salary stays unknown, the reason is recorded
  in the intake note like every other OCR miss, and the salary backfill fills
  it in later. Before this, one candidate with an unreadable salary ended the
  whole run;
- **an unrendered detail pane skips one candidate, it does not end the run.**
  A backgrounded tab renders lazily-populated content late, and a tab an opaque
  window fully covers is not painted at all - so the pane failing to come up
  stopped being rare the moment background search existed. The wait is longer
  when backgrounded (`RUNNER_CAPTURE_BACKGROUND_ATTEMPTS`) and still hard
  bounded, still one wait and never a retry; a candidate whose pane never
  arrives is marked handled and reported as `skipped_capture_timeout`, so a run
  where nothing rendered says so rather than reporting a quiet zero;
- **the batch advance travels with the run.** An approved M4g batch started in
  the background advances to its next task without the foreground check as
  well; requiring it there ended a sixteen-task batch after task one, which is
  what the authorization was for. The advance still needs the *same* tab, still
  exactly BOSS, and still happens only after a normal completion - every other
  outcome stops the batch. Every human-initiated start (console, popup, resume)
  keeps the strict check;
- every stop condition is unchanged: login, verification, risk control, wrong
  origin, ambiguous selector, a lost or navigated tab, the 20-candidate and
  5-scroll ceilings, and the human's pause/cancel. A background run pauses or
  fails exactly where a foreground one would, and never retries;
- this is not a background *browser*: no hidden tab, no minimised window, no
  timer, no scheduler, no unattended trigger, and no chaining beyond the M4g
  batch that was already authorized. One task, one tab, one operation at a time.

**Status: the user has explicitly authorized M4a, M4b and M4c.** M4a —
bounded-session scaffolding only, with no navigation — is implemented (a
popup approval UI, a background service worker that is the sole owner of
the session pointer, and an approved-tab overlay bar) and is **complete and
live-verified** in a real, logged-in Chrome profile. M4b — bounded
navigation to search/results/detail pages and result-card selection, one
human click at a time via a prepare-then-confirm handshake, plus the
two-phase open/capture/loopback-preview flow for one candidate at a time —
is also **complete and live-verified** in a real, logged-in Chrome profile.
M4c — bounded scrolling and pagination within a results page, one human
click per step, under the same limits in sections 1-8 below — is
implemented (fixture-tested; backend session accounting, selectors,
extraction primitives and the overlay's scroll/next-page controls and
gating) but is **pending verification in a real, logged-in Chrome profile**;
see `docs/orchestration/STATUS.md` for exactly what remains unverified. See
"Implementation gate" below and `docs/orchestration/ROADMAP.md` M4 for the
staged spec.

**1. Bounded, human-started session.** Every session is started by the human,
who explicitly approves, before anything navigates: the exact task/search
criteria, a page cap and a candidate/result cap, and which foreground Chrome
tab the session is scoped to. No session starts itself, no session resumes
after being stopped, and a session never operates outside the one tab the
human approved.

**1a. Immutable POC ceilings.** Independent of whatever the human approves,
these hard limits apply and code must enforce them, not just document them:
one concurrent session at a time; one visible foreground tab per session;
at most 3 search-results pages visited; at most 20 candidate jobs opened/
extracted; at most 5 scroll steps per results page. A human's approved
criteria may set any of these **lower** for a given session; nothing —
not user input, not a future setting, not a config file — may set any of
them **higher** than this list without a new `CLAUDE.md` amendment and a
fresh explicit authorization, exactly like the rest of this policy.

**2. What navigation would be allowed, inside that session, those approved
caps, and the immutable ceilings in 1a — whichever is lowest — only.** BOSS
search pages, search-results pages, and job-detail pages; selecting/opening
a result card from a results page; scrolling and pagination *bounded by the
approved and immutable caps together* — never open-ended, never "until
nothing new appears." Nothing here permits navigating outside the approved
tab, opening new windows, or visiting a page the human's criteria don't call
for.

**3. What it would extract.** Structured job fields only (title, company,
salary, location, experience/education text, the JD body) — never
`document.body`, never cookies/`localStorage`/`sessionStorage`/form
values/auth headers, exactly as the current extension already refuses to
read them (see the rules above; this is not weakened here). Extracted
fields would preview and import through the existing, unchanged
`extension_intake.py` -> `job_intake.save_posting()` path — no second
persistence path, same as today.

**4. Absolute mechanical limits — unconditional, not softened by "supervised."**
No auto-start and no auto-restart of a stopped/finished session. No timer,
poll loop, `MutationObserver`, or any other unattended trigger — every step
happens because the human is present and the session is running, not because
time passed. No hidden or background browsing: the tab being driven must be
the visible foreground tab, always. No mass scraping or crawling beyond the
approved caps. No credential access of any kind. No Playwright, no
Chrome DevTools Protocol, no native messaging host, and no other automation
mechanism — the extension's own content script, invoked as today, is the
only mechanism a future M4 build may use.

**5. Absolute action limits — same as the non-negotiable rule above, restated
for emphasis.** Never apply, never click 立即沟通, never send or follow up on
a message, never follow/collect a candidate or company, never change account
state, and never report or log that any of those happened. This is not part
of the gated exception; **no version of M4 changes it** — an M4 session
navigates and reads, and may never submit anything. The one narrow exception
anywhere in this file is M6, which lives outside M4 entirely: it needs its own
per-job human confirmation, and no M4 supervised session, batch or runner may
ever reach it.

**6. Hard stops — any one of these ends the session immediately, with no
retry and no workaround:** a CAPTCHA, identity/phone verification, a login
prompt, or a security/risk-control warning; a navigation target whose scheme
and host are not exactly `https://www.zhipin.com` — the same host already
declared in `extension/manifest.json`'s `content_scripts.matches`, and
nothing broader (no `*.zhipin.com`, no other scheme, no other subdomain) —
checked before every navigation and **failing closed** (stop, don't guess)
on any mismatch; a selector that cannot uniquely resolve an expected element
(ambiguous match); a detected navigation loop (the same URL shape reached
again within the session); any approved cap, or any immutable ceiling from
1a, being reached; the human clicking the session's stop control; or the
site itself blocking/rate-limiting the tab. None of these may be worked
around, retried automatically, or bypassed with stealth, fingerprint
changes, or altered timing — "report and stop" is the only response, exactly
as the existing browser-capture and extension rules already require.

**7. What a session must show and record.** A visible, continuously updated
progress indicator (page N of cap, candidates found so far) and an always-
available stop control the human can hit at any time. Every extracted item
carries its provenance (which page/URL it came from) and its
`canonical_url()` — never a URL still carrying a query-string session token.
All communication with the backend stays loopback-only, exactly like the
current extension (`127.0.0.1`, non-loopback peers refused). Every
navigation/extraction step appends to the same kind of audit trail M3
introduced (`OrchestrationEvent`-shaped: what happened, when, which task) —
never the raw page HTML, never a token, never a cookie value.

**8. Testing bar before any of this ships.** Automated tests are fixture-only,
exactly like the rest of `job_sources/` and the existing extension
extraction tests — no automated test may ever touch zhipin.com. Real-site
navigation compatibility is never claimed as verified by an automated test;
it stays a manual claim the user makes after checking it themselves in their
own logged-in Chrome, exactly as `extension/README.md` already says for
detection today.

**Implementation gate.** This section satisfies the "explicit CLAUDE.md
policy change" half of the M4 gate in `docs/orchestration/ROADMAP.md`. The
other half — "separate, explicit authorization from the user" — has now
happened three times: for M4a (the bounded-session scaffolding in section 1
— approval, caps, the approved tab, start/stop, the overlay bar), for M4b
(bounded navigation under section 2's caps — search/results/detail pages and
result-card selection, plus the two-phase capture/preview flow), and for M4c
(bounded scrolling and pagination under the same section 2 caps and the
immutable ceilings in 1a). No further milestone under this policy exists yet
in `docs/orchestration/ROADMAP.md`; any future one still requires its own
separate, explicit user authorization, exactly like these three.

### M5a — task-scoped candidate matching + human review (explicitly authorized, implemented)

#### Bounded search-to-match amendment (user authorized 2026-08-28)

Codex may implement and offline-test an opt-in search -> intake -> fast-model matching ->
ranked human review flow. Runtime requires a fresh explicit cost confirmation naming the
resume, model and maximum candidate/call budget (at most 3; failures consume a slot).
This supersedes only conflicting prohibitions on automatic scoring orchestration elsewhere in this file.
Reuse TaskCandidate, job_matcher and JobAnalysis; persist approval/claims on the existing
SearchTask. No automatic retry after uncertain paid results; pause/cancel stops subsequent
calls, cannot undo an already dispatched call. Resume never replenishes approval/budget.
Information/location/currency conflicts are marked 待确认, not direct recommendations.
No automatic human status, application, favorite or message. Implementation authorization
does not authorize paid live calls: this milestone's acceptance is offline with mock models.

`services/task_matching.py` is the only new service for this milestone. It scores a
`JobSearchTask`'s existing `TaskCandidate` associations against the **active** analysis resume and
the **fast** model — it never calls the smart model automatically, never creates a job or
association, and never touches `Job.status`. It reuses `job_matcher.compute_cache_key`/
`find_cached`/`analyze_job` verbatim (the same cache table, the same guardrails) — there is no
parallel persistence or scoring pipeline.

Two operations, the same "plan then confirm" shape `resume_comparison.py` already established:

- `plan_task_match` — pure read. Reports, for every candidate, whether it is already cached, plus
  the active resume, the model, and the exact pending/cap counts. Calling it spends nothing.
- `run_task_match` — the one place that spends money, and only with `confirmed=true`. Rejects
  outright (writes nothing) if there is unconfirmed pending work. Bounded by
  `settings.max_analyses_per_run` (read fresh from config every call — never hard-coded); a task
  with more pending candidates than the cap needs another confirmed click. Each candidate is
  analyzed independently inside its own `try/except` — one bad job never aborts the rest of the run,
  and every outcome (cached / freshly scored / errored) is reported back.

Routes: `GET /api/tasks/{id}/match-plan`, `POST /api/tasks/{id}/match-run`.

Console UI rules that are load-bearing:

- the plan/confirm action is never triggered on page load or when a task is otherwise viewed —
  only an explicit "生成匹配计划" click reads it, and only an explicit "确认分析" click spends
  anything;
- associating a new candidate into the task invalidates the currently displayed plan (it is cleared,
  not silently left stale) — a stale plan's pending/cap numbers must never be confirmed against a
  candidate set that has since changed;
- once a plan exists, every candidate's shown score/verdict/cached status comes from *that plan's*
  active-resume + fast-model result, never an unrelated "latest analysis" that might be a different
  resume variant or the smart model; before a plan exists, an existing analysis is still shown, but
  labeled honestly as not being this plan's own result;
- candidates sort score-descending with unanalyzed candidates last, and the task's `min_score` — if
  set — is an optional visible filter, never an invisible one;
- reviewed/dismissed counts derive each candidate's **latest** `reviewed`/`dismissed` event (events
  are stored oldest-first) — a task-level event (`job_id` absent) never counts toward any candidate,
  and clicking "标记已复核"/"标记已忽略" more than once never inflates the count past one per
  candidate.

Existing M1 (`TaskCandidate`) and M3 (`OrchestrationEvent`/`reviewed`/`dismissed`) surfaces are
reused unmodified. This section covers M5a; the bounded amendment above separately authorizes
the opt-in search-to-match implementation/offline tests. Apply,
favorite, message, any browser/site action, and automatic smart-model use remain forbidden
regardless, the same as every earlier milestone.

### M5b — bounded cross-task matching + unified human review (explicitly authorized 2026-08-29)

Codex may implement and offline-test one backend + localhost-console coordinator over an exact,
finite selection of completed SearchPlan tasks. It resolves only their existing `TaskCandidate`
associations, deduplicates by canonical `job_id`, and reuses the active analysis resume, fast model,
`job_matcher`, `JobAnalysis` and existing cache. It must not create another Job, score, candidate,
deduplication or persistence pipeline.

Planning is read-only and must bind the exact task/candidate/resume/model snapshot. Before any
uncached work, the UI must separately confirm the exact cached/pending counts and a whole-batch cap
of at most three new fast-model calls. Failed or uncertain calls consume a slot and are never
automatically retried; cached results cost zero. A stale snapshot must fail before a model call.

The output is one score-sorted human-review surface. Missing or contradictory location, salary,
education or experience stays `待确认`; an AI score never records a review decision or mutates
`Job.status`. M5b never controls Chrome, starts a search, changes canonical intake, uses the smart
model automatically, applies, favorites, messages, schedules work, reads credentials or bypasses
verification. Offline acceptance must mock the model and stop before any paid live call.

### M6 — human-confirmed single application execution (policy authorized 2026-08-30)

This section first defined the policy boundary. The user separately authorized
its implementation on 2026-08-30 and authorized the unknown-dynamic-greeting
amendment on 2026-08-31 after the first live attempt disproved the manually
entered expected-text assumption. Neither implementation authorization nor the feature flag is an
approval for any job; every real action still requires the two per-job human
confirmations below.

M6 defines the one narrow circumstance in which the foreground Chrome extension
may submit an application on a recruitment site: **after the user has explicitly
confirmed that one specific job and explicitly accepted that BOSS will choose an
unknown dynamic first greeting which JobAgent cannot preview, verify or
control.** JobAgent must never ask the human to guess that text, present a guess
as site-verified, or imply that the text shown by an AI will be sent.

The authorized action has a name, fixed by the user's ruling of 2026-08-30:
**a human-confirmed single application action with an inseparable initial
greeting.** On BOSS, 立即沟通 and the first 招呼语 it sends are one indivisible
action, so M6 covers both halves of it - and nothing beyond them.

**This amendment supersedes only conflicting earlier clauses, and only for the
per-job, human-confirmed, single application execution defined here.** Every
other prohibition in this file stays in force, unchanged.

#### 1. The gate - one job, one human confirmation

- **AI recommends; it never decides.** No `JobAnalysis.verdict`, `overall_score`,
  queue position, ranking or any other model output may initiate, schedule or
  pre-approve an application. There is no code path from an analysis result to an
  application execution, and a test asserts it.
- **Every job is confirmed on its own.** One confirmation authorizes exactly one
  application to exactly one job. There is no batch confirmation, no "approve
  all", no "apply to the top N", no select-all-then-apply, and no bulk-apply
  endpoint.
- Each confirmation is a record that **binds, at minimum**:
  - `job_id`;
  - `company`;
  - `title` (the role);
  - `canonical_url` **and** `external_id`;
  - `resume_id` **and** `resume_hash` (the variant's content at confirmation time);
  - the fixed `boss_dynamic_unverified` greeting mode. For schema compatibility
    its text field is deliberately empty and its hash binds that empty marker;
    neither is a claim about the text BOSS will send.
- **BOSS unknown-dynamic-greeting mode (authorized 2026-08-31):** because the
  foreground BOSS page does not expose the greeting before the application
  action, the final confirmation must state prominently that JobAgent cannot
  preview, independently verify or control the first greeting and that BOSS may
  choose different text dynamically. The human must explicitly accept that
  uncertainty for this one job.
- The mode must never ask for, auto-generate, infer, copy from an AI analysis,
  select, remember or silently prefill an expected greeting. Any non-empty
  greeting value makes the approval invalid. One acceptance applies to one
  approval only and is not reusable authority for another job.

#### Confirmed typed greeting (user authorized 2026-09-03)

The user's authorization, verbatim: *M6 may, after clicking 立即沟通, fill in
and send once the (editable) greeting shown in the confirmation dialog, if the
chat input box is empty. The text must be displayed in full and be editable
before confirming; it must not send when the box is non-empty; every subsequent
message, automatic retry, batch and background execution remain forbidden.*

**What changed the facts.** The unknown-dynamic mode above exists because a
2026-08-31 live run disproved the assumption that BOSS sends the text a human
typed into JobAgent. A 2026-09-03 live run then disproved the other half of the
premise: the click succeeded, the conversation opened, and BOSS sent **no**
greeting at all - while an earlier manual application to a different company
did send one. So the greeting is not inseparable from 立即沟通; it is sometimes
sent and sometimes not, and this is not predictable from here.

That makes typing a greeting a **separate authorized action**, not the other
half of the application click, and it is why it needed its own authorization.

Both modes now exist and neither may drift into the other:

| `answers_source` | body | meaning |
| --- | --- | --- |
| `boss_dynamic_unverified` | must be empty | BOSS decides; JobAgent cannot preview, verify or control it |
| `boss_typed_greeting` | must be non-empty | the exact characters JobAgent will type |

The typed mode is not the manually-attested mode that was removed. That one
asked the human to *predict* what BOSS would send, and the prediction was
unverifiable. This text is what JobAgent itself types - shown in full, editable
before confirming, and bound by hash, so editing it makes the approval stale
rather than sending something the human did not read.

Rules specific to it:

- **the box must already be empty.** BOSS greets on its own sometimes; typing
  into a box that is not empty would append a second message to whatever is
  there. A non-empty box skips the greeting and records why;
- **the composer must resolve unambiguously.** Exactly one visible, enabled,
  empty textarea sharing a container with exactly one send control. Two
  candidates, none, or no send control all skip without typing. Selectors are
  structural and text-based (`extension/src/boss/selectors.ts`) because the
  panel's classes were never observed, and a guessed class here would type into
  the wrong box - a message to a real person, not a blank field;
- **it happens after the click and is reported separately.** A greeting failure
  never undoes, re-runs or retries the application click; the conversation
  exists either way and the outcome stays `unknown` for a human to check. The
  attempt detail records how far it got
  (`clicked_and_greeted_site_result_unverified`, or
  `clicked_greeting_skipped:<reason>`);
- **exactly one send.** No follow-up, no second attempt, no polling for the
  composer beyond one bounded wait;
- **the greeting goes to one of exactly two places, and both are checked
  against the approval's own `external_id`** (chat page authorized
  2026-09-06). BOSS normally answers 立即沟通 with an in-page panel, but on
  four consecutive applications it navigated the whole tab to
  `/web/geek/chat` instead. The composer resolves there perfectly well - it
  just belongs to whichever conversation BOSS happened to select, and typing
  into it is a message to a real person who may not be the one this approval
  names.

  So `sendConfirmedGreeting` takes the approval's `external_id`, and:

  - on `/job_detail/<id>.html` the URL's own id must match (`wrong_job`);
  - on `/web/geek/chat` the **open conversation's own pane** must carry both
    the approved company and the approved title
    (`chat_wrong_job` / `chat_job_ambiguous` / `chat_job_unknown`);
  - anywhere else, nothing is typed at all (`left_job_page`).

  The first version of that second rule read `/job_detail/<id>.html` links,
  because an id is what an approval binds and text needs normalizing to
  compare. The live page has **none**: read read-only on 2026-09-06 (one
  navigation, DOM reads, no conversation selected, nothing clicked) it
  reported zero such anchors, and its forty conversation-list items are plain
  `div`s. So the header's text is the only identity the page exposes, which
  is what the user authorized checking in the first place.

  **`CHAT_CONVERSATION` scoping is the whole safety argument.** The left list
  holds every recruiter this account has ever spoken to, so a document-wide
  text match would happily confirm a job applied to yesterday while BOSS had
  a different conversation open - the exact wrong-person send this prevents.
  One pane, one conversation. Both company *and* title must be found: several
  roles at one company is normal, and so is the same title at two companies.
  A leaf's text may *lead* with what is wanted, because BOSS renders
  「公司 | 招聘者职位」 and 「职位 25-40K 北京」 as single nodes - but only when
  what follows is a separator, a space or a digit, so 「云运维工程师(高级)」
  never matches 「云运维工程师」.

  `boss_chat_conversation.html`'s **class names come from that live read; its
  content is authored**, because no conversation was opened to capture one and
  no automated test may visit zhipin.com. The header's internal markup is
  still a reconstruction from a screenshot, so what the tests pin is the
  reader's logic and its scoping - whether it matches the live header stays a
  manual claim the user makes in their own Chrome.

  Two statuses mean "not ready yet" and are the only ones the bounded retry
  waits out: `no_composer`, and a content script that did not answer at all
  because BOSS is mid-navigation. A wrong or ambiguous job is refused, never
  waited on.

  The queue used to report only the click, so all four read
  「已执行一次立即沟通」 and the user found out an hour later by looking at
  BOSS. `explainM6Greeting` names the outcome in the same feedback line: a
  greeting that did not go is a thirty-second fix in a conversation that
  already exists, but only if someone is told.

  Still exactly one message. This reads no conversation list, opens and
  activates no chat tab, selects no conversation and reads no message
  history - the suspended M7 scan stays suspended, and nothing here
  re-enables any part of it.

Everything else about M6 is unchanged: one job per confirmation, the acceptance
checkbox, one attempt per confirmation, no batch, no background, no automatic
retry, and `Job.status` still never guessed into `applied`.
- The user must be shown all of the above **before** confirming. A confirmation
  the user could not read in full is not a confirmation.
- **A confirmation screen is shown immediately before the attempt**, displaying
  the exact job, its canonical URL, the selected resume and the
  unknown/uncontrolled greeting warning. Confirming once in a queue days earlier
  is not enough: the last thing before the browser acts is a human looking at
  the exact job and resume and accepting the unknown dynamic first greeting.

  **Single-confirmation amendment (user authorized 2026-09-02).** This was
  originally two screens - one to bind the snapshot, a second to re-read it and
  execute. The second guarded against a *time gap*, which does not exist when
  the two are seconds apart, and the re-reading it asked of a human is done far
  more strictly by `validate` on the backend, which refuses a snapshot that no
  longer matches the job, the resume or the loaded page's identity. One screen
  may therefore both bind and execute, provided it displays everything above
  before the human confirms.

  Nothing else about the gate moves: the confirmation is still per job, still
  requires the explicit acceptance checkbox, still authorizes exactly one
  attempt, and is still consumed by that attempt whatever its outcome. There is
  no confirmation that covers two jobs, and none that can be given in advance.
- A confirmation authorizes **one attempt** and is consumed by that attempt,
  whatever its outcome.

#### 2. What an approved execution may do

Only after such a confirmation, and only then: the extension may, in the
**visible foreground tab**, open that one job's canonical detail page, fill in
site-exposed confirmed answers where applicable, and submit that one
application. Under the BOSS unknown-dynamic-greeting mode, it must not write,
fill or claim to control BOSS's hidden greeting configuration. Nothing else.

#### 3. Unconditionally still forbidden under M6

- no unattended, scheduled, timed, queued or background application - **no timer,
  cron, poll loop, `MutationObserver` or any other unattended trigger** may reach
  an application; M4 section 4 applies here in full;
- no hidden tab, background tab, minimised window or non-foreground execution;
- no bulk, mass or repeated application, and no automatic retry after any
  failure, timeout or uncertain result - a retry is a new human confirmation;
- no automatic recruiter messaging: no follow-up message, no second message, no
  answer to a recruiter's reply, no auto-reply, no scheduled or unattended
  message of any kind. **The one exception, and it is not "messaging":** on BOSS
  the application *is* 立即沟通, and clicking it inseparably sends the first
  greeting (招呼语). Sending that one greeting, as the indivisible other half of
  a single human-confirmed application action, is inside M6 - it is a
  *human-confirmed single application action with an inseparable initial
  greeting*, not automatic recruiter messaging. Everything after that first
  greeting is a message, is not part of an application, and stays forbidden;
- no CAPTCHA solving or bypass, no anti-bot evasion, no stealth, no fingerprint
  spoofing, no altered timing to look human;
- no credential, cookie, `localStorage`, `sessionStorage`, form-value or
  auth-header reading, and no `securityId` / `/wapi/` use;
- no Playwright, no CDP, no native messaging host - the extension's own content
  script remains the only mechanism;
- no account-state change beyond the one confirmed application: no favourite, no
  follow, no collect, no profile edit, no settings change.

#### 4. Approval goes stale, and a stale approval fails closed

A confirmation is invalidated - and the execution must refuse rather than
proceed - when any of these has changed since it was given:

- the page identity: the loaded page's canonical URL / external job id does not
  match the confirmed one **exactly**;
- the job row (`company`, `title`, or the canonical URL / external id);
- the selected resume variant, or that variant's content;
- the application answers, or the fixed unknown-dynamic-greeting mode/source;
- the site presents a different form than the one described to the user.

For the BOSS unknown-dynamic-greeting mode, JobAgent can re-check only the fixed
mode/source and empty compatibility marker; it **cannot** claim to know what
BOSS's hidden dynamic greeting will be. That limitation must remain visible on
the final confirmation and in the recorded attempt result. It never permits
reading settings, form values, cookies, tokens or storage to try to discover the
hidden text.

Identity is re-checked immediately before submitting, not only at confirmation
time. Any mismatch stops and hands control back to the user.

#### 5. Hard stops - stop and return control, never work around

A CAPTCHA, a login prompt, identity/phone verification, a security or
risk-control interstitial, a rate-limit response, loss of foreground, a closed or
navigated-away tab, an ambiguous selector, or a wrong origin (anything whose
scheme+host is not exactly `https://www.zhipin.com`) **ends the attempt
immediately**. No bypass, no retry, no workaround, no stealth. The user is told
what happened and decides what to do next.

#### 6. Recording the outcome - never assume success

- `Job.status = applied` may be written **only** after the recruitment site has
  been observed, on the page, to have accepted the submission, and only by
  calling the existing `application_workflow.mark_applied`. A click is not an
  application, and a sent greeting is not by itself proof of one.
- When the result cannot be verified - navigation interrupted, ambiguous
  response, timeout, unclear confirmation UI - the outcome is recorded as
  **`application_result_unknown`**. It is never upgraded to `applied` by
  guessing, and it is never silently discarded; the user resolves it.
- The greeting is not known or site-verified merely because the click occurred.
  The attempt record must preserve that its mode/source was
  `boss_dynamic_unverified`; only an independent, visible application-success
  state may still justify `applied` under the preceding rule.
- A verified submission produces **exactly one** `applied` event. A repeated or
  retried attempt must not produce a second one.
- Unchanged: `Job.status` still moves only through
  `services/application_workflow.py`, and the event trail stays append-only.

#### 7. What M6 does *not* touch

M6 is one application submission, and on BOSS that action indivisibly carries
its first greeting (see section 3). It ends there.

Everything past that first greeting remains forbidden exactly as before:
automatic recruiter messaging, follow-ups, answering a recruiter's reply,
auto-reply, inbox access and message polling. So do unattended application, bulk
application, mass application, timed application, AI-triggered application,
automatic retry, favourite/follow/collect and any other account-state change,
CAPTCHA/risk-control bypass, stealth, fingerprint spoofing, background browsing
and credential/token/storage access. M6 supersedes none of those.

The boundary in one line: **M6 may send the application and, when the human
confirmed its exact text, the one greeting that opens the conversation; it may
never send a second message.**

(Until 2026-09-03 this read "the greeting that *is* the application". A live run
showed the click and the greeting are separable - BOSS sent none - so the
greeting became its own authorized action rather than half of another one. The
limit did not move: one message, and nothing after it.)

#### 8. The architecture principle is unchanged

```
AI verdict   = a recommendation      (JobAnalysis.verdict)
Job.status   = what the HUMAN did    (applied / skipped / replied / ...)
```

M6 does not weaken this by one inch. A verdict, a score or a recommendation may
never write `Job.status` and may never trigger a browser action. What M6 adds is
that a *human decision, expressed per job*, may be **executed** by the foreground
extension instead of retyped by hand.

#### 9. Feature flag

There is no global auto-apply mode. If the implementation needs an entry-point
flag it is `HUMAN_CONFIRMED_APPLY_ENABLED`, and it **only exposes the feature**.

The code default is `False` and stays that way. The packaged build sets it to
`true` as a *default env value* in `backend/launcher.py` (user asked
2026-09-06), which a recipient overrides in `data/.env`. That changes what is
visible in one distribution and nothing else: every application still requires
its own confirmation naming that exact job, there is still no batch, no
schedule and no automatic retry, and the flag is still never evidence that any
job was approved.
It is never, under any circumstance, evidence that a particular job has been
approved: the per-job confirmation record is the sole authority, and a flag that
is on with no confirmation authorizes nothing.

#### 10. Implementation gate

This policy is implemented only behind the feature gate. Every material change
to its scope still requires explicit user authorization.

Before any M6 implementation may be accepted, automated tests (fixture-only; no
automated test may ever touch live zhipin.com) must at minimum prove:

- with no explicit confirmation, execution never happens;
- a confirmation binds exactly one job and cannot be reused for another;
- a stale confirmation is refused before any browser action;
- changing the resume variant or the application answers/mode invalidates the
  confirmation;
- the BOSS confirmation asks for or contains greeting text, is silently
  prefilled, is reused across jobs, lacks the unknown/uncontrolled warning, or
  can proceed without the per-job acceptance checkbox;
- a legacy manually-attested expected-text approval is refused;
- a job-identity mismatch at submit time stops the attempt;
- a CAPTCHA / login / verification / risk-control state stops the attempt;
- an uncertain result is recorded as `application_result_unknown`, never as
  `applied`;
- one verified submission produces exactly one `applied` event;
- no bulk-apply endpoint and no batch-confirmation path exists;
- no code path leads from an AI score/verdict to an application execution.

Live-site compatibility is never asserted by an automated test; it stays a manual
claim the user makes after checking it themselves, exactly as
`extension/README.md` already says for detection.

### M7 — BOSS conversation scan (suspended after live risk-control, 2026-08-31)

The user has withdrawn this runtime feature after the live attempt triggered or
coincided with a BOSS account restriction. The local HR沟通 UI and the extension
console bridge must expose no command that opens, activates, traverses or scans
the BOSS chat page. The implementation below is retained only as historical
design context and inert fixture-tested code; it is not a current capability.
Re-enabling any part requires a separate policy decision and implementation
milestone. Manual paste remains available and no HR reply synchronization is in
scope.

M7 authorizes one explicit confirmation in the local HR沟通 page to open or
activate exactly one foreground `/web/geek/chat` tab and scan its rendered
BOSS conversation list. If no chat tab exists in the confirmed console window,
the extension may create one new active tab at the fixed, query-free
`https://www.zhipin.com/web/geek/chat` URL. It never reuses or overwrites an
unrelated BOSS/search/detail tab. If exactly one chat tab already exists it is
activated; multiple chat tabs fail closed.
The extension may sequentially select a visible conversation-list item and read
only its rendered header and text-message rows. One run is bounded to at most
50 distinct conversations, five conversation-list scroll steps and 100 text
messages per conversation. It imports only previously unseen messages into the
existing `RecruiterConversation` / `RecruiterMessage` persistence.

The human no longer selects a Job before scanning. The backend associates a
conversation only when its header exposes a canonical
`/job_detail/<external_id>.html` identity that resolves to exactly one existing
applied BOSS Job, or when normalized company + title resolve uniquely. An
unmatched or ambiguous conversation is skipped and counted; it is never
guessed, attached to a newly-created Job, or persisted as a second job pipeline.

The extension may create or activate the single chat tab, perform bounded
readiness checks within that same human-triggered operation, click only a unique
visible `li[role=listitem]` conversation control, and perform only the bounded
scrolls above. Selecting a conversation may cause BOSS itself to mark that
conversation read; the final confirmation UI must disclose this inseparable
side effect. Apart from opening the fixed chat URL above, it may not navigate a
tab, expand message history, touch an input/send control, or make any other
account-state change. Login,
verification, wrong origin/path, multiple candidate chat tabs, foreground
loss, missing message identity, or ambiguous DOM all fail closed. A scan is
sequential and has no automatic retry.

M7 remains an explicit foreground snapshot, never background inbox access: no
timer, unattended polling, `MutationObserver`, scheduled scan, hidden-tab read or
unattended trigger. It performs no AI analysis and never generates, fills,
sends, replies to or follows up on a message. It never reads cookies, tokens,
storage, form values, `securityId`, `data-url`, `redirect-url` or `/wapi/`.
Message bodies must not appear in structured logs or scan summaries. Automated
acceptance is fixture-only and must prove exact chat path/origin, foreground
enforcement, unique-list-item selection, bounded traversal, text-message-only
extraction, exact/unique job association, `data-mid` incremental deduplication,
zero-AI import and absence of any input/send primitive.

### Historical salary backfill (explicitly authorized 2026-08-30)

Codex may implement a one-time, human-confirmed maintenance plan over the current finite set of
existing BOSS Jobs whose salary is missing and whose canonical URL is exactly
`https://www.zhipin.com/job_detail/<external_id>.html`. The extension may visit those visible
detail pages in normal logged-in Chrome, verify exact job identity, use the existing bounded local
salary OCR fallback when needed, and enrich only the same existing Job through
`extension_intake -> job_intake`. There is no second Job/dedup persistence path.

The whole plan is persistent and observable and limited to at most 100 jobs. It automatically
pauses after at most three processed detail pages, and each next batch requires a fresh explicit
resume click.

**Session-cap amendment (user authorized 2026-08-30, extended same day).** One explicit,
per-run human confirmation naming the exact remaining count may raise that run's session cap
from the default 3 to the whole remaining plan (never above the 100-job plan ceiling). The
confirmation is available both on a paused run and on a created-but-not-yet-started run, so an
18-job plan can be authorized as one continuous session instead of six three-job batches. The
cap is stored on the run (`salary_backfill_runs.session_cap`) and applies to that run only - the
default stays 3 for every other run, and nothing raises a cap without a fresh human click. Every
stop condition below is unchanged: a larger cap buys continuity, never permission.

**Automatic salary backfill after a search (user authorized 2026-09-05).** The
user asked for this to stop being a chore they must remember: *「我还是想你想办
法直接就解决了不要每次都回填」*. This supersedes only the clause above that
made starting a real backfill run "a separate human action in the console", and
only for a run that follows the user's own just-completed search.

Why it is a second pass at all, and why that cannot be fixed in the search: BOSS
renders the salary in a private-use font both in the results list and in the
detail pane beside it, and only the standalone `/job_detail/<id>.html` page
carries it as text. 409 of 632 jobs in the library were created with no readable
salary for exactly this reason. The search cannot read what the page does not
show it.

- the chain fires **once** per completed batch, from the console the user is
  looking at, and only when the batch actually completed - never after a
  failure, a cancellation, a pause, or a verification stop;
- it is a **checkbox, on by default, remembered per browser**. Turning it off
  restores the old one-click prompt;
- nothing else moves: the backfill's own plan ceiling (100 jobs), its
  per-run session cap, and every stop condition - login, verification,
  foreground loss, worker error, the user's pause - are exactly as they were.
  It still visits only stored jobs' own canonical detail URLs, still enriches
  through `extension_intake -> job_intake`, and still never applies, favourites
  or messages;
- it is not a scheduler. No timer starts it, nothing starts it on page load,
  and a worker or browser restart resumes nothing on its own.

**The salary backfill may also run in the background (user authorized 2026-09-06).**
This supersedes only the clause above that kept the backfill on the strict
foreground check, and only for a run the user chose to background.

Measured before asking, which is why the trade is small: of the salaries this
feature has recovered, **775 came from reading the standalone detail page's
text and 4 from the screenshot OCR**, which had failed to run 685 times for
want of an `activeTab` grant. The foreground requirement was protecting a
fallback that almost never fires, at the cost of holding the screen for the
whole run.

- `verifyBackfillTab()` is the backfill's own check and the only place its
  foreground half is dropped. It has its own flag (`backgroundBackfillRun`),
  separate from the search's, so one can never silently enable the other, and
  M6 still calls `verifyRunnerTab` directly. A test pins all three;
- **the capture itself is untouched.** `salaryForeground()` still refuses a tab
  that is not in front (authorized 2026-08-28); a backgrounded run simply does
  not attempt it, records `本地薪资 OCR 未采用：background_backfill` like every
  other miss, and leaves the salary for a later run;
- the tab must still exist and still be exactly BOSS, the run still starts from
  a human click on a foreground tab, and it no longer pulls that tab forward
  between jobs;
- every stop condition is unchanged: login, verification, a lost or navigated
  tab, worker error, the user's pause, the 100-job plan ceiling and the per-run
  session cap.

Login, verification, foreground loss or worker error pauses closed; it never logs in, reads
credentials, bypasses verification, retries invisibly or runs after a worker restart. It never
searches, scrolls, applies, favorites or messages. Offline implementation acceptance does not
authorize starting the real maintenance run; that remains a separate human action in the console.

### Résumé -> search directions (the second agent)

```
résumé + strategy  -> ResumeDirectionAgent -> per-direction fit 0-100
                                              + suggested Chinese keywords
        combined with
past outcomes per keyword (zero AI, v0.6 statistics)
        -> ranked directions -> the comprehensive search
```

The second agent in the codebase, and only because there is a genuine second
job: `JobMatchAgent` answers "does this JD fit this résumé", which cannot
answer "what should I be searching for at all".

It exists because the deterministic fallback it replaces was measuring the
wrong thing. Character overlap gave `云计算工程师`, `云运维工程师` and
`云平台工程师` an identical 33% on a real résumé, and every point came from the
shared suffix 工程师. It read like a measurement and was really just "this is an
engineering role". `_GENERIC_ROLE_TOKENS` now strips those, but a stripped
character count is still a weak stand-in - hence the agent.

Rules that are load-bearing:

- **ranking never calls a model.** `search_direction_ranking.rank()` takes the
  analysis as an argument. Paying for one is the caller's confirmed decision,
  so preparing or re-running a search stays free;
- **plan then confirm**, the same shape as `resume_comparison` and
  `task_matching`. `direction_analysis.plan()` is a pure read the console loads
  on open; `run()` requires `confirmed=true`. **One call covers the whole
  résumé** - never one per direction;
- **a cached answer needs no API key.** The key gate lives in the agent, so
  reading an answer already paid for works without one;
- `direction_cache_key = sha256(resume_hash, strategy_hash, candidates, model,
  DIRECTION_PROMPT_VERSION)`. A new résumé, an edited strategy, an added
  direction or a changed prompt all re-analyse; nothing else does;
- **an AI judgement and a character count are different claims and must never
  be ranked against each other.** When an analysis exists but skipped a
  direction, that direction is `unjudged` with fit 0 - a perfect character
  overlap (`云运维工程师` appearing verbatim in the résumé) would otherwise
  outrank a direction the model actually assessed at 95/100. The frontend
  renders the three sources differently for the same reason;
- **evidence still outranks fit.** A model reading a résumé is a better guess
  than counting bigrams, but it is still a guess about what BOSS will return; a
  direction that already surfaced 28 jobs has told us the answer;
**A keyword BOSS returns nothing for is demoted (2026-09-05).** The ranking
already weighed outcome evidence, but only over jobs it had *collected* - a
keyword whose searches render no card at all had no cohort, so it kept being
picked. Five of sixteen units in one run went to `Infrastructure Engineer`,
`Cloud Engineer`, `Cloud Operations Engineer`, `Cloud Infrastructure Engineer`
and `DevOps Engineer`, each observing zero cards.

`_barren_keywords()` reads the user's own completed runs and marks a keyword
whose runs have summed to zero observed cards, after at least two of them - one
run can end early for its own reasons. `DirectionRanking.top()` then **drops**
it rather than merely ranking it last: with sixteen units to spend, four dead
keywords are a quarter of the run, and demotion alone was not enough because
the candidate list is short enough that a demoted keyword still gets picked.
Fourteen searches that return jobs beat sixteen where four return none. If every
direction were barren the order stands, so the planner always gets a plan.
Deterministic, no model call, and a different claim from "these jobs are a poor
match": that is a judgement the ranking makes, this is a fact the search itself
established, twice.

- **suggested keywords are used, never written.** The model may propose Chinese
  keywords the strategy lacks (a résumé saying 基础设施工程师 while the strategy
  only lists the English `Infrastructure Engineer`). They join *this* search and
  are labelled `AI 补充`; `career_strategy.yaml` is never auto-edited;
- the agent scores directions only. It never picks a job, never writes
  `Job.status`, and never triggers a search - a human still starts that.

### Caching (mandatory)

`analysis_cache_key = sha256(resume_hash, strategy_hash, jd_hash, model, prompt_version)`

A repeat click never costs money. Changing the resume, the career strategy, the
JD, the model, or `PROMPT_VERSION` all invalidate the cache automatically.
`force=true` bypasses it and **replaces** the cached row.

`job_hash` is `Job.content_hash` - company + title + normalized description.
It deliberately excludes salary/city/experience so dedup stays stable, but
`services/scoring.py` *does* read `salary_text`. A job scored while its salary
was unknown would therefore keep serving that score after a later backfill
filled it in, because the key never moved. So the one place a salary is filled
in after the fact - `job_intake.save_posting`'s `enrich_missing_salary` branch -
drops that job's cached analyses and says so in the appended event. It is
scoped to the one job whose input actually changed: a job whose salary was
already known is never re-billed.

Bump `PROMPT_VERSION` in `agents/prompts.py` whenever you change the prompt.

### Models

Read from config, never hardcoded:
`OPENAI_MODEL_FAST` (bulk analysis) and `OPENAI_MODEL_SMART` (explicit
"高质量重分析" only). Never call both automatically for one job.

## Commands

```powershell
# from the repo root
.\scripts\dev.ps1 setup      # venv + backend deps + npm install + .env
.\scripts\dev.ps1 seed       # create the DB and insert demo jobs
.\scripts\dev.ps1 start      # backend :8000 and frontend :5173 in two windows
.\scripts\dev.ps1 test       # pytest + frontend production build
```

Raw equivalents:

```powershell
backend\.venv\Scripts\python.exe -m pytest                    # run from backend\
backend\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
npm run build                                                 # run from frontend\
```

Backend binds to **127.0.0.1**, never 0.0.0.0. CORS allows only the local Vite
origin.

## Database

`data/jobagent.db` (SQLite, WAL). Path comes from `DATABASE_URL` and relative
paths resolve against the repo root, not the CWD. Since v0.4 the schema is
owned by Alembic — if you change a model, add a migration. Never tell the user
to delete their database.

Unique indexes that matter: `jobs.content_hash`, `(jobs.source, external_id)`,
`job_analyses.cache_key`.

## Security rules (non-negotiable)

- `OPENAI_API_KEY` lives **only** in the backend process, read from `.env`.
  Never put it in React, never commit it, never log it, never return it from an
  API, never store it in SQLite. `services/ai_settings.py` lets the user *set*
  one from the app (so a new user need not edit a file and restart): it writes
  the same `.env`, clears `get_settings`'s cache so it takes effect at once,
  and returns only whether a key is configured plus its last four characters.
  Reading one back, storing it anywhere else, or logging it stays forbidden -
  a test asserts the saved key reaches neither the response nor the log.
  `OPENAI_BASE_URL` is how this project supports providers other than OpenAI:
  one OpenAI-compatible protocol, not several SDKs, and
  `agents/openai_client.py` is the single place a client is built. `core/logging.py` has a redaction filter as a
  backstop — it scrubs credential *values*, not mentions of the variable name.
- **There is no global `AUTO_APPLY` mode.** Application execution is permitted
  only through the M6 per-job explicit human confirmation gate. AI recommends,
  a human approves *one named job at a time*, and only then may the foreground
  extension execute that single application. Any entry-point flag
  (`HUMAN_CONFIRMED_APPLY_ENABLED`) exposes the feature and nothing more - it is
  never evidence that a job was approved. The `job_sources/` capture browser
  **reads** pages and must never gain the ability to apply, M6 included.
- No CAPTCHA solving, no anti-bot evasion, no stealth fingerprinting, no
  bypassing rate limits or login protection. If a future job-source adapter
  cannot be written without one of those, do not write it.
- Never store recruitment-site passwords.
- Never add stealth or anti-bot behaviour. If a site blocks automation, say so
  and point the user at Quick Capture - do not work around it.
- Never turn clipboard reading into background monitoring: explicit click only.
- Never derive a human status from an AI verdict, and never bulk-apply.
  Batch *recording* of applications the human already made is a different
  act and is allowed under the 2026-09-02 amendment above; batch
  *execution* stays forbidden, and no paste may ever create a job.
- Never let analytics write the career strategy on its own, and never
  present a small sample as a finding. Correlation is not causation, and a
  cohort of three is not evidence.
- Never infer which resume a past application used. `Resume.is_active` is
  the analysis resume, not the applied one; an unrecorded application stays
  `unknown` until a human says otherwise.
- Never auto-edit resume content, never auto-set the active resume, and
  never delete a resume that an application references - archive it.
- Never fill the job x resume score matrix automatically. Spending money
  requires an explicit confirmation showing the exact number of calls.
- Never let direction ranking call a model on its own, and never rank an AI
  fit against a character-overlap number - they are different claims.
- Never write an AI-suggested keyword into `career_strategy.yaml`.
- Never infer interview rounds from a pre-v0.8 event, and never let
  recruiter-message analysis create an interview on its own.
- Never count a candidate withdrawal as an employer rejection, and never
  treat a pending round result as a failure.
- Never log a meeting URL or put one in analytics - it may carry an
  access token.
- Never log compensation figures, offer notes or pasted offer text.
- Never read a candidate counter as the company's offer, and never
  recompute accepted compensation from the latest revision.
- Never rank or convert compensation across currencies without a rate the
  user typed. Nothing fetches one, ever.
- Never invent a weight. No weights means no score, not an even split.
- Never treat an unrated dimension as 0, never name a winner below the
  coverage floor, and never show a total without its breakdown.
- Never let a deadline change a score, and never let AI rate a company.
- Never auto-decline a competing offer, and never auto-reject an offer
  that fails a deal-breaker.
- Never log weights, ratings, FX rates, negotiation targets, decision
  notes or deal-breaker values.
- Never rewrite a DecisionSnapshot. Later edits must not reach it.
- Never claim JobAgent sent a message. Drafts are copied by the user and
  sent elsewhere; only an explicit confirmation records candidate_reply.
- Never background an application. Search (2026-09-04) and the salary
  backfill (2026-09-06) may run backgrounded; M6 and the screenshot
  capture itself keep the strict foreground check,
  and a backgrounded search still stops on login, verification or risk
  control rather than pushing through unwatched.
- Never report a list as finished when the browser stopped painting the
  tab. A frozen tab scrolls and loads nothing, so that is a pause, not a
  completion.
- Never add automatic/background inbox access, message polling or auto-reply.
  M7 is the only exception: one explicit human click may perform the bounded,
  foreground-only BOSS conversation scan defined above; it is not a mailbox
  connection and cannot run unattended.
  There is no
  `AUTO_REPLY`, and no global auto-apply mode - see the M6 gate above for the
  only permitted, per-job, human-confirmed application execution.
- **Except for the narrowly scoped M6 human-confirmed single-application
  workflow, the extension must not apply or change recruitment-site account
  state.** Outside that gate it may never apply, click 立即沟通, send or follow
  up on a message, or follow/collect a candidate or company - for any reason,
  under any mode. **Inside** M6 the boundary is: the one confirmed application,
  including the first greeting that 立即沟通 indivisibly sends, is authorized;
  any message *after* that greeting, and every other account-state change
  (favourite, follow, collect, profile, settings), stay forbidden. It is not
  part of what M4 defines an exception for.
- As of M1-M3 (current, shipped behavior), the Chrome extension also never
  scrolls, paginates, or navigates to a job on its own — it reads only the
  page a human already opened, when a human clicks 检测当前页面. A precisely
  bounded exception to *that* part (navigation only, never the apply/message
  rule above) is documented under "Chrome extension — M4 supervised
  navigation policy" below; application execution is a separate matter defined
  only by M6. That section is a **policy definition only**: it
  does not by itself authorize writing the code. Implementing it requires a
  second, separate, explicit user authorization that references that section
  — see "Implementation gate" at the end of it.
- Never read cookies, localStorage, sessionStorage, form values or auth
  headers from a page, and never send a whole document.body.
- Never skip a candidate on a partial card match. Company, title,
  salary, experience and city must all agree with a stored job; the
  description is not on the card, and a wrongly skipped posting never
  enters the library.
- Never add a second persistence path for extension imports - it goes
  through job_intake like every other source.
- Never accept extension requests from a non-loopback peer.
- Do not log a whole resume or a whole JD. Log hashes, lengths, and ids.

## Coding standards

- Python: 3.11+, `from __future__ import annotations`, full type hints, ~100
  char lines. Services stay framework-free; routes stay thin.
- Domain errors subclass `core/errors.AppError` and map to clean HTTP codes via
  the handler in `main.py`. Error messages shown to the user are in Chinese and
  say what to do next.
- Log with `log_event(logger, "noun.verb", key=value)` — greppable, no prose.
- TypeScript: `strict`, no `any`. `src/types/index.ts` mirrors the Pydantic
  schemas; update both sides together.
- UI text is Simplified Chinese. Technical nouns (Kubernetes, Terraform) stay
  in English.

## Working agreements

- **Run `python -m pytest` after any backend change.** Tests must never make a
  real OpenAI call — patch `app.services.job_matcher.run_job_match` — and must
  never reach a live recruitment site.
- Browser tests use a headless Chromium against local fixture HTML only. That
  headless usage is a test-harness detail; the capture browser stays headed.
- **Run `npm run build` after any frontend change** (it type-checks first).
- **Run `npm run build` in `extension/` after any extension change** - the
  extraction tests inject `extension/dist`, and a stale build shows up as a
  skipped test rather than a silent pass. `.\scripts\dev.ps1 test` does it
  first, before pytest.
- `scripts/smoke_openai.py` is the only thing that talks to OpenAI for real. It
  is manual, it makes exactly one call, and without a key it prints
  `SKIPPED - OPENAI_API_KEY not configured` and exits 0.
- Avoid unnecessary rewrites. Prefer extending `scoring.py` /
  `career_strategy.yaml` over adding new abstractions.
- Do not add a second agent until there is a genuine second job to do.
- Demo data is fictional and marked `[演示数据]`. Never present it as real
  vacancies, and never let a mock result reach production code paths.
- When you change a command, update `README.md` in the same commit.
