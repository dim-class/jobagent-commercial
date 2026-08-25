# Delta: enforce M4b phase ordering after live failure

Continue the current implementation; do not redesign it.

Real logged-in Chrome result:
- Card discovery/open and capture-to-loopback-preview worked.
- The human could click 下一位候选人 repeatedly before capturing. The overlay reached 5/5 and
  overwrote the pending candidate; only the latest capture was previewed.

Fix and test:
1. Once one candidate opens successfully, disable 下一位候选人 until that exact pending candidate
   is captured and its preview succeeds. `not_loaded` or preview failure keeps Next disabled while
   allowing only another explicit 捕获详情 attempt.
2. After capture/preview succeeds, re-enable Next only when the approved candidate cap has not been
   reached. At cap (live case 5/5), keep it disabled.
3. Session start/re-render/reconnect must derive button state safely: no pending capture means Next
   follows cap state; pending capture never gets silently discarded or overwritten.
4. Add behavior tests proving repeated Next clicks cannot prepare/open/confirm a second candidate,
   capture success unlocks one next candidate below cap, soft capture failures do not unlock it,
   and cap completion never unlocks it.
5. Rebuild and run all extension behavior tests plus focused extraction tests; fix failures.

No scrolling, pagination, auto-retry/import/apply/message behavior, or live site access. Keep
RESULT concise with exact counts and the remaining one-candidate live recheck.
