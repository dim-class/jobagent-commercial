/**
 * Pure sequencing rule for `matchResult` (M5a match-plan/match-run UI).
 *
 * Extracted so the exact regression Codex reproduced - `handleConfirmMatch`
 * sets `matchResult`, then its own post-run `handlePlanMatch` call used to
 * immediately clear it, silently wiping the just-completed run's feedback -
 * has a dependency-free, framework-free unit test (see
 * `tests/matchResultLifecycle.test.mjs`). `ConsolePage.tsx` is the only
 * caller in the app; this file never touches React state itself.
 */

export type MatchLifecycleEvent =
  | { type: 'plan-refresh' }
  | { type: 'confirm-success'; result: unknown }
  | { type: 'task-changed' }

export function nextMatchResult(current: unknown, event: MatchLifecycleEvent): unknown {
  switch (event.type) {
    case 'plan-refresh':
      // A read-only plan refresh - including the one `handleConfirmMatch`
      // triggers right after a run - must never erase the result of the run
      // that just completed.
      return current
    case 'confirm-success':
      return event.result
    case 'task-changed':
      // Selecting a different task, or associating a new candidate into the
      // current one, is exactly when a previous run's result goes stale.
      return null
  }
}
