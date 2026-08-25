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

Explicitly **not** implemented: automatic application, automatic recruiter
messaging, search-result crawling, mass scraping.

## Architecture

```
config/career_strategy.yaml   the definition of "a good job" - DATA, never code
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
- never click 立即沟通 / apply / send. Collection only.
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

Both endpoints refuse a non-loopback peer. The server already binds to
127.0.0.1; the guard makes that an assertion rather than a deployment
assumption. `CORS_ORIGIN_REGEX` matches the *shape* of a Chrome extension
origin because an unpacked extension's id is machine-specific.

Extraction is tested by injecting the **built** `dist/boss/*.js` into a headless
Chromium page holding a local fixture and calling the real `BossExtract.detect()`.
There is deliberately no Python re-implementation to drift out of sync, and no
automated test may ever touch zhipin.com.

### Chrome extension — M4 supervised navigation policy

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
of the gated exception; no version of M4 changes it.

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

### Caching (mandatory)

`analysis_cache_key = sha256(resume_hash, strategy_hash, jd_hash, model, prompt_version)`

A repeat click never costs money. Changing the resume, the career strategy, the
JD, the model, or `PROMPT_VERSION` all invalidate the cache automatically.
`force=true` bypasses it and **replaces** the cached row.

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
  API, never store it in SQLite. `core/logging.py` has a redaction filter as a
  backstop — it scrubs credential *values*, not mentions of the variable name.
- `AUTO_APPLY` is false and there is no code path that flips it. AI recommends,
  a human approves, and only later (v0.4+) does a browser execute. v0.2 added a
  browser that **reads** pages; it must never gain the ability to apply.
- No CAPTCHA solving, no anti-bot evasion, no stealth fingerprinting, no
  bypassing rate limits or login protection. If a future job-source adapter
  cannot be written without one of those, do not write it.
- Never store recruitment-site passwords.
- Never add stealth or anti-bot behaviour. If a site blocks automation, say so
  and point the user at Quick Capture - do not work around it.
- Never turn clipboard reading into background monitoring: explicit click only.
- Never derive a human status from an AI verdict, and never bulk-apply.
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
- Never add inbox access, message polling or auto-reply. AUTO_APPLY stays
  false and there is no AUTO_REPLY.
- Never let the Chrome extension apply, click 立即沟通, send or follow up on a
  message, follow/collect a candidate or company, or change any account
  state on a recruitment site — for any reason, under any mode. **No gate,
  no future milestone, and no user instruction ever authorizes this**; it is
  not part of what M4 defines an exception for.
- As of M1-M3 (current, shipped behavior), the Chrome extension also never
  scrolls, paginates, or navigates to a job on its own — it reads only the
  page a human already opened, when a human clicks 检测当前页面. A precisely
  bounded exception to *that* part (navigation only, never the apply/message
  rule above) is documented under "Chrome extension — M4 supervised
  navigation policy" below. That section is a **policy definition only**: it
  does not by itself authorize writing the code. Implementing it requires a
  second, separate, explicit user authorization that references that section
  — see "Implementation gate" at the end of it.
- Never read cookies, localStorage, sessionStorage, form values or auth
  headers from a page, and never send a whole document.body.
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
