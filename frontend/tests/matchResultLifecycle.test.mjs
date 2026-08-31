// Framework-free regression test (Node's built-in test runner + assert, no
// new dependency) for the exact bug Codex reproduced: a post-run, read-only
// plan refresh (`plan-refresh`) must never erase the result of the run that
// just completed. Run with: node --test tests/matchResultLifecycle.test.mjs
import assert from 'node:assert/strict'
import test from 'node:test'

import { nextMatchResult } from '../src/pages/matchResultLifecycle.ts'

test('a plan refresh preserves the current match result', () => {
  const result = { task_id: 1, results: [], analyzed: 3, failed: 1 }
  assert.equal(nextMatchResult(result, { type: 'plan-refresh' }), result)
})

test('a plan refresh on an empty result stays empty', () => {
  assert.equal(nextMatchResult(null, { type: 'plan-refresh' }), null)
})

test('confirm-success sets the result regardless of what came before', () => {
  const result = { task_id: 1, results: [], analyzed: 2, failed: 0 }
  assert.equal(nextMatchResult(null, { type: 'confirm-success', result }), result)
  assert.equal(
    nextMatchResult({ stale: true }, { type: 'confirm-success', result }),
    result,
  )
})

test('a task change clears a previous result', () => {
  const result = { task_id: 1, results: [], analyzed: 2, failed: 0 }
  assert.equal(nextMatchResult(result, { type: 'task-changed' }), null)
})

test('the confirm -> plan-refresh sequence never loses the just-set result', () => {
  // Reproduces `handleConfirmMatch` calling `handlePlanMatch` right after a
  // run: confirm-success sets the result, then the refresh must not wipe it.
  let current = null
  const result = { task_id: 7, results: [{ job_id: 1, error: 'boom' }], analyzed: 0, failed: 1 }
  current = nextMatchResult(current, { type: 'confirm-success', result })
  current = nextMatchResult(current, { type: 'plan-refresh' })
  assert.equal(current, result)
})
