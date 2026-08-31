// Framework-free regression test (Node's built-in test runner, no new
// dependency) for the job library's selection rule. The bug this guards:
// 「选中未分析的 N 个」 used to pick out of the current page, which reported 0
// while 18 unanalysed jobs existed - they were exactly the rows the 100-item
// page dropped. The button now filters first and takes the whole result.
// Run with: node --test tests/jobSelection.test.mjs
import assert from 'node:assert/strict'
import test from 'node:test'

import { nextJobSelection } from '../src/pages/jobSelection.ts'

const ids = (...values) => new Set(values)

test('a pending select-all takes the whole reloaded result', () => {
  const next = nextJobSelection(ids(), ids(1, 2, 3), true)
  assert.deepEqual([...next].sort(), [1, 2, 3])
})

test('select-all replaces an unrelated earlier selection', () => {
  const next = nextJobSelection(ids(9, 10), ids(1, 2), true)
  assert.deepEqual([...next].sort(), [1, 2])
})

test('select-all on an empty result selects nothing', () => {
  assert.equal(nextJobSelection(ids(1, 2), ids(), true).size, 0)
})

test('without select-all the selection is pruned to what the list returned', () => {
  const next = nextJobSelection(ids(1, 2, 3), ids(2, 3, 4), false)
  assert.deepEqual([...next].sort(), [2, 3])
})

test('an untouched selection keeps its identity so React does not re-render', () => {
  const current = ids(1, 2)
  assert.equal(nextJobSelection(current, ids(1, 2, 3), false), current)
})

test('a selection with nothing left visible becomes empty', () => {
  assert.equal(nextJobSelection(ids(1, 2), ids(7, 8), false).size, 0)
})
