'use strict'

/**
 * M4f observability delta - regression test proving the popup (`runner.ts`)
 * and overlay (`overlay.ts`) built bundles actually *render* every required
 * field, not just declare it in a type. Static inspection of the built
 * output (not execution) is enough here: the required fields are backend
 * response property names / pointer field names that only ever need to
 * appear as a property access somewhere in the rendering code for the
 * value to reach the DOM - if a field is dropped from `renderCounters`/
 * `renderRunnerBar`, the corresponding property access disappears from the
 * built bundle too, and this test catches that.
 */

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const RUNNER_JS = path.join(__dirname, '..', 'dist', 'runner.js')
const OVERLAY_JS = path.join(__dirname, '..', 'dist', 'overlay.js')
const BUILT = fs.existsSync(RUNNER_JS) && fs.existsSync(OVERLAY_JS)
const SKIP = BUILT ? false : "extension not built - run 'npm run build' in extension/"

const runnerCode = BUILT ? fs.readFileSync(RUNNER_JS, 'utf8') : ''
const overlayCode = BUILT ? fs.readFileSync(OVERLAY_JS, 'utf8') : ''

// TASK.md: every field the popup must visibly render from the task response.
const REQUIRED_POPUP_FIELDS = [
  'task_id',
  'state',
  'city',
  'keyword',
  'current_url',
  'scroll_round',
  'visible_jobs',
  'observed_jobs',
  'new_jobs',
  'duplicate_jobs',
  'imported_jobs',
  'no_new_rounds',
  'current_candidate',
  'last_action',
  'last_error',
  'paused_reason',
  'updated_at',
]

for (const field of REQUIRED_POPUP_FIELDS) {
  test(`popup renderCounters references task.${field}`, { skip: SKIP }, () => {
    assert.match(
      runnerCode,
      new RegExp('task\\.' + field.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')),
      `dist/runner.js must reference task.${field} for it to ever reach the DOM`,
    )
  })
}

// The overlay's live runner-pointer message uses camelCase field names
// (RunnerStateMessage), not the backend's snake_case task response.
const REQUIRED_OVERLAY_FIELDS = [
  'taskId',
  'phase',
  'city',
  'keyword',
  'currentUrl',
  'scrollsUsed',
  'candidateCap',
  'candidatesAttempted',
  'visibleJobs',
  'observedCount',
  'newCount',
  'duplicateCount',
  'importedJobs',
  'noNewRounds',
  'currentCandidate',
  'lastAction',
  'lastError',
  'pausedReason',
  'updatedAt',
]

for (const field of REQUIRED_OVERLAY_FIELDS) {
  test(`overlay renderRunnerBar references state.${field}`, { skip: SKIP }, () => {
    assert.match(
      overlayCode,
      new RegExp('state\\.' + field.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')),
      `dist/overlay.js must reference state.${field} for it to ever reach the DOM`,
    )
  })
}
