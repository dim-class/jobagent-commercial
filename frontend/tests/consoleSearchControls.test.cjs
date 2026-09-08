'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const panel = fs.readFileSync(
  path.join(__dirname, '../src/pages/ConsoleSearchPanel.tsx'), 'utf8')

test('the search panel drives one aggregate run, with no second set of controls', () => {
  // 开始搜索 prepares the batch and the 本次综合搜索 block pauses, resumes and
  // cancels it. A parallel low-level surface for the same runner - 开始批量搜索
  // beside 暂停批次/恢复批次/取消批次, and 开始单任务 beside its own
  // 暂停/恢复/取消 - drove exactly the same worker through exactly the same
  // states, and existed only because the aggregate flow was built on top of it
  // and nothing was taken away afterwards.
  for (const gone of [
    '开始批量搜索', '暂停批次', '恢复批次', '取消批次',
    '开始单任务', '暂停单任务', '恢复单任务', '取消单任务',
    '准备自定义搜索', '选择已有搜索任务', '单任务候选上限',
    // The label of the deleted input, not the words themselves: 「总搜索单元数
    // 不变」 is prose in the segmentation help and is not a control.
    '搜索单元数（1–',
  ]) {
    assert.doesNotMatch(panel, new RegExp(gone), `${gone} is a duplicate control`)
  }
  // A custom keyword is not lost with 准备自定义搜索: 策略 page ->
  // preferred_roles -> search_direction_ranking -> this run. That route
  // persists the keyword and accumulates the outcome statistics the ranking
  // reads, which a one-off form never did.
  assert.match(panel, /开始搜索/)
  assert.match(panel, /batchCommand\('pause-batch'\)/)
})

test('nothing that decides or advances a run is hidden behind 高级设置', () => {
  const advanced = panel.indexOf('{showAdvanced ? <div')
  assert.ok(advanced > 0, 'the advanced block still exists')

  // How many searches the button is about to start is the number to read
  // before pressing it. It spent its life inside a collapsed block, which is
  // the same mistake the experience bands were moved out of.
  const unitCount = panel.indexOf('本次将创建')
  assert.ok(unitCount > 0 && unitCount < advanced, '搜索单元数 must be visible by default')

  // Status is read on open and on regaining focus, so while a run is on
  // screen nothing moves on its own; this button is what advances it.
  const refresh = panel.indexOf('刷新状态')
  assert.ok(refresh > 0 && refresh < advanced, '刷新状态 must be visible by default')

  // One layer of hiding is a choice; two is how a control stops existing.
  const rest = panel.slice(advanced)
  assert.doesNotMatch(rest, /<summary/, 'no folds nested inside the collapsed block')
})
