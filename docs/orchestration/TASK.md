# Authorized task — P1 candidate-stage filtering and product-neutral onboarding

## Goal

Finish the current P1 implementation so a fresh JobAgent user can choose a
candidate-stage policy and the existing SearchPlan/extension pipeline applies
it deterministically. The immediate user need is to filter the job library and
future searches so explicit graduate/campus/intern tracks can be excluded.

## Existing work already on disk

- Productization roadmap and onboarding UI already exist.
- `career_strategy` is user-local under `data/`; tracked config is neutral.
- `early_career_policy` has been added to strategy/task/intake code, migration
  `0021_candidate_stage_policy.py`, and initial tests. Review and complete it;
  do not duplicate code.
- The extension runner carries the task policy and sends `task_id` to preview /
  import; review TypeScript/build correctness and add focused regressions.

## Required behavior

Policy values:

- `exclude`: social/experienced recruiting; reject explicit 应届、校招、校园招聘、
  毕业生、管培生、实习 or cohort markers using the existing classifier.
- `include`: keep both stages.
- `only`: keep only postings explicitly identified as early-career; detail JD
  evidence must be allowed to decide, so do not title-prefilter normal cards.

Snapshot the policy onto every new SearchPlan task. Preview and single import use
that snapshot when `task_id` is supplied; manual intake without a task uses the
current local strategy. Unknown task IDs/policies fail closed. Historical
cleanup endpoints remain explicit tools and must not depend on this preference.

Do not create another Job store, dedup path, scheduler, browser driver, AI call,
or live BOSS behavior. Preserve Normal Chrome + MV3 + localhost architecture.
Do not touch Chrome/BOSS, credentials, CAPTCHA, messages, applications,
favorites, or recruiter inboxes.

## Implementation checklist

1. Review current P1 diffs and complete missing wiring.
2. Ensure migration tests reflect `0021` without rebuilding the task table.
3. Add focused tests for `exclude/include/only`, task snapshot precedence,
   invalid task/policy fail-closed behavior, and runner prefiltering.
4. Keep the concise Chinese UI control on onboarding and StrategyPage; explain
   that existing tasks are not mutated.
5. Build extension and frontend; run focused tests, then full backend pytest
   with a workspace-local basetemp. Fix failures and rerun affected tests.
6. Update `docs/orchestration/RESULT.md` and a short current-state note in
   `docs/orchestration/STATUS.md`. Do not commit or push.

## Acceptance

- Generic installs default to `include`; this user's local strategy explicitly
  remains `exclude`.
- SearchPlan rows expose the policy and extension requests include task ID.
- A graduate title is excluded/accepted/retained according to each policy, with
  no AI/network dependency; `only` can accept a JD-only early-career signal.
- Existing backend, extension, and frontend tests pass with exact counts.
- No live browser action or paid model call occurs in this delegation.

## Authorization

User explicitly requested product-neutral onboarding and candidate-stage
filtering, then requested this handoff to the persistent Claude worker.
