'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const page = fs.readFileSync(path.join(__dirname, '../src/pages/JobsPage.tsx'), 'utf8')
const client = fs.readFileSync(path.join(__dirname, '../src/api/client.ts'), 'utf8')

test('historical early-career cleanup is explicit, previewed and reuses skip workflow', () => {
  assert.match(client, /early_career_cleanup\?: boolean/)
  assert.match(page, /筛选并全选应届\/校招\/实习岗位/)
  assert.match(page, /early_career_cleanup: true/)
  assert.match(page, /setCleanupConfirm\(true\)/)
  assert.match(page, /api\.skipJob\(job\.id, '当前不是应届生；历史岗位库清理'\)/)
  assert.match(page, /岗位与历史记录仍保留在“已跳过”状态/)
  assert.doesNotMatch(page, /runEarlyCareerCleanup[\s\S]{0,1600}deleteJob/)
})

test('cleanup does not call AI and failures are retained without automatic retry', () => {
  const start = page.indexOf('async function runEarlyCareerCleanup')
  const end = page.indexOf('\n  return (', start)
  assert.ok(start >= 0 && end > start, 'cleanup function must remain independently inspectable')
  const cleanup = page.slice(start, end)
  assert.doesNotMatch(cleanup, /analyzeJob|analyzeBatch|reanalyzeSmart|setInterval|setTimeout/)
  assert.match(cleanup, /failed\.add\(job\.id\)/)
  assert.match(cleanup, /失败 \$\{failed\.size\} 个（未自动重试）/)
})
