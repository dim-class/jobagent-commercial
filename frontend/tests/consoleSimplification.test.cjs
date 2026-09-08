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

test('maintenance and legacy task management stay behind one explicit more-tools control', () => {
  const toggle = consolePage.indexOf("showAdvancedConsole ? '收起更多功能' : '更多功能'")
  const conditional = consolePage.indexOf('{showAdvancedConsole ? <>', toggle)
  const salary = consolePage.indexOf('<SalaryBackfillPanel />', conditional)
  const crossTask = consolePage.indexOf('<CrossTaskMatchPanel />', conditional)
  assert.ok(toggle >= 0 && conditional > toggle)
  assert.ok(salary > conditional && crossTask > conditional)
  assert.equal(consolePage.indexOf('<SalaryBackfillPanel />'), salary)
})
