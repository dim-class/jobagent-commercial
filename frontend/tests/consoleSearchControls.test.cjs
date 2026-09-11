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

test('the one paid button in the panel asks before it spends', () => {
  // 重新分析 sits among free controls - 刷新状态, 按方向看简历支撑度, the whole
  // search form - and is the only one that bills. The forced variant is the
  // one that most needs the dialog: it re-bills for a result already paid for
  // and replaces it, and nothing on the button said so.
  assert.doesNotMatch(panel, /onClick=\{\(\) => void analyzeDirections\(/,
    'the click opens the confirmation, it does not call the model')
  assert.match(panel, /setDirectionConfirm\(\{/)
  // The exact call count travels into the dialog, as it does for 全部分析.
  assert.match(panel, /确认分析（\{directionConfirm\.calls\} 次调用）/)
  assert.match(panel, /这次会重新计费并替换它/)
})

test('nothing that decides or advances a run is hidden behind 高级设置', () => {
  const advanced = panel.indexOf('{showAdvanced ? <div')
  assert.ok(advanced > 0, 'the advanced block still exists')

  // How many searches the button is about to start is the number to read
  // before pressing it. It spent its life inside a collapsed block, which is
  // the same mistake the experience bands were moved out of.
  // Anchored on the computation, not its wording - 「搜索单元」 was the
  // backend's word for a task and has since become 「次搜索」.
  const unitCount = panel.indexOf('Math.min(searchOptions.max_batch_tasks,')
  assert.ok(unitCount > 0 && unitCount < advanced, 'the search count must be visible by default')

  // Status is read on open and on regaining focus, so while a run is on
  // screen nothing moves on its own; this button is what advances it.
  const refresh = panel.indexOf('刷新状态')
  assert.ok(refresh > 0 && refresh < advanced, '刷新状态 must be visible by default')

  // One layer of hiding is a choice; two is how a control stops existing.
  const rest = panel.slice(advanced)
  assert.doesNotMatch(rest, /<summary/, 'no folds nested inside the collapsed block')
})

test('a refresh downloads only the tasks the panel shows', () => {
  // The console refreshes on open, on focus and on every 刷新状态, and each time
  // pulled the whole task table: 889 rows, 1023 KiB, 263 ms on 2026-09-11, to
  // look up about sixteen. The extension popup still lists every task, so only
  // the console's call narrows - the bare endpoint keeps its old answer.
  assert.match(panel, /api\.listSearchPlan\(AbortSignal\.timeout\(5000\), shown\(\)\)/)
  assert.doesNotMatch(panel, /api\.listSearchPlan\(AbortSignal\.timeout\(5000\)\)/)
  // Merged rather than replaced, so a search prepared mid-request survives.
  assert.match(panel, /setTasks\(current => \{[\s\S]*?byId\.set\(row\.id, row\)/)
  const client = fs.readFileSync(path.join(__dirname, '../src/api/client.ts'), 'utf8')
  assert.match(client, /ids === undefined \? '\/api\/tasks\/search-plan'/)
})
