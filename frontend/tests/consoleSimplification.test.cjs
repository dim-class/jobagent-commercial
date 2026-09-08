'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const app = fs.readFileSync(path.join(__dirname, '../src/App.tsx'), 'utf8')
const consolePage = fs.readFileSync(path.join(__dirname, '../src/pages/ConsolePage.tsx'), 'utf8')

test('default navigation keeps the frequent workflow short and folds secondary tools', () => {
  // To the array's own closing bracket, not to SECONDARY_NAV: the comment
  // between them explains why 面试/Offer moved, and naming them there is
  // documentation, not a nav entry.
  const primaryStart = app.indexOf('const PRIMARY_NAV')
  const primary = app.slice(primaryStart, app.indexOf(']', primaryStart))
  assert.match(primary, /搜索岗位/)
  assert.match(primary, /投递队列/)
  assert.match(primary, /岗位库/)
  assert.match(primary, /简历/)
  // 面试 / Offer moved to SECONDARY_NAV when the sidebar was cut down: both
  // are empty until a recruiter replies, so they were daily clutter for a
  // funnel whose front half is where the work actually is. The assertion
  // followed the design rather than pinning the version before it.
  assert.doesNotMatch(primary, /面试|Offer|浏览器采集|策略分析|设置/)
  const secondary = app.slice(app.indexOf('const SECONDARY_NAV'))
  assert.match(secondary, /面试/)
  assert.match(secondary, /Offer/)
  assert.match(app, /<summary>更多工具<\/summary>/)
  assert.match(app, /<Navigate to="\/console" replace \/>/)
})

test('the console is the search flow, with the superseded surface deleted not folded', () => {
  // This used to assert that the legacy surface stayed behind 更多功能. Hiding
  // it was the half-measure: it survived a year of being unused because a fold
  // costs nothing to keep. 搜索 -> 全部分析 -> 投递队列 does the same work in
  // three clicks, and the numbers said so - of 890 rows in `job_search_tasks`
  // exactly one came from 新建任务, and `orchestration_events` held 2 rows in
  // total. The assertion follows the design, not the version before it.
  // Comments stripped first: the page's own docstring names what was removed
  // and why, and that record is worth more than a grep that trips over it.
  const code = consolePage.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*/g, '')
  for (const gone of [
    '新建任务', '任务列表', '关联已有岗位', '事件记录', '待处理事项',
    'CrossTaskMatchPanel', 'AutoMatchReviewPanel', '生成匹配计划',
  ]) {
    assert.doesNotMatch(code, new RegExp(gone), `${gone} is superseded`)
  }
  // No fold left to hide anything in.
  assert.doesNotMatch(code, /showAdvancedConsole|更多功能/)

  // The one panel that survives, and visible: the search panel can start a
  // salary backfill but only this one can resume a paused run, finish the
  // remainder in a single session, or replace a stale plan.
  assert.match(consolePage, /<SalaryBackfillPanel \/>/)
  assert.match(consolePage, /<ConsoleSearchPanel \/>/)
})
