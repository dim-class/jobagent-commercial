# Claude Worker Task

State: implementation — live BOSS detail extraction

## Objective

Make detail extraction reliably return company, city, experience, education, and description on
current BOSS dedicated detail pages while preserving working title, salary, and canonical URL.

## Live evidence

- Dedicated URLs use `https://www.zhipin.com/job_detail/<id>.html`.
- Current live verification reports title, salary, and URL, but the five objective fields missing.
- BOSS also renders a selected-job detail pane inside `/web/geek/jobs`; do not regress it.
- Salary may use a PUA font; never emit unreadable PUA text as a valid salary.

## Scope and acceptance

- Inspect existing selectors/extraction/tests first; make the smallest robust change.
- Centralize selectors in `extension/src/boss/selectors.ts`; adjust extraction only if necessary.
- Add/update fixture coverage for both dedicated detail and selected-job detail shapes.
- Preserve canonical URL/external-id behavior and meaningful `missing_fields`.
- Run extension build and all relevant extension tests; fix failures in this invocation.
- Run focused backend extension extraction/API tests if extension output contracts are affected.
- Do not access live BOSS, secrets, browser state, `.env`, databases, or browser profiles.
- Do not add navigation, scrolling, pagination, applying, messaging, CDP, Playwright, stealth, or
  CAPTCHA behavior.
- Write a concise `docs/orchestration/RESULT.md`: files changed, behavior, exact test/build counts,
  and remaining logged-in Chrome verification.
