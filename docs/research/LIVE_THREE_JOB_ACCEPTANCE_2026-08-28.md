# Three-job live acceptance — 2026-08-28

Evidence: read-only localhost APIs and local backend request-log route counts after the user
reported starting the extension. No remote browser controller, model call or production-data edit.

## Verified runtime boundary

- SearchTask #38: Hangzhou / 云计算; state=completed, last_error=null.
- Supervised session #23: candidate_cap=3, candidates_extracted=3, scroll_cap=5,
  scrolls_used=1, status=stopped.
- Session events: started -> scroll -> detail -> detail -> detail -> stopped.
- Local request log: one task start, four navigation confirmations (one scroll + three detail),
  three canonical extension imports, three candidate associations, one task complete, one session stop.
- Session stop reason is the generic `user_stop`; completion plus matching counters supports
  the bounded-stop result, but that label alone does not identify the cap as the cause.
- Task has exactly three associated jobs (#67, #68, #69), all still `new`, no analysis.
- All three URLs are unique query-free `/job_detail/<id>.html`; external IDs agree with paths.

## Persisted extraction

| Job | Title | Company | City | Experience | Education | JD characters | Salary |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 67 | 云计算交付工程师 | 四川准达 | 杭州 | 经验不限 | 本科 | 793 | missing |
| 68 | 华为计算产品线--软件开发工程师 | 华为技术有限公司 | 杭州 | 在校/ | 本科 | 464 | missing |
| 69 | 云计算运维工程师 | 杭州嘉信云信息科技 | 杭州 | 3-5年 | 本科 | 364 | missing |

- JD #68/#69 contains interspersed `来自` text; #68 experience is truncated/suspicious.
- Capture code requires pane title/company agreement before intake; this run reached intake
  without an identity error. No independent current-DOM comparison was collected.
- observed=15, new=15, duplicate=0. This run did NOT exercise a nonzero duplicate branch or
  multiple scroll rounds. `visible_jobs=0` is inconsistent with discovery and needs follow-up.

## Verdict / cost gate

Three-candidate limit and collection-to-canonical-intake boundary: PASS for this live sample.
Full extraction quality and end-to-end AI matching/review: NOT PASS yet.
Read-only match-plan: three uncached candidates, pending=3; no match-run submitted.
Separate user approval is required before paid AI matching. Salary must remain unknown, not
inferred from other jobs; do not silently rewrite historical descriptions or review statuses.
