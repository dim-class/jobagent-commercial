'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const page = fs.readFileSync(
  path.join(__dirname, '../src/pages/ApplicationQueuePage.tsx'), 'utf8')

test('the queue remembers 我的经验年数 instead of resetting it every visit', () => {
  // A fact about the person, not a per-visit filter. Resetting to 不限 on every
  // visit put the 3+ year postings collected before the search-side experience
  // filter existed back into view each time.
  assert.match(page, /const EXPERIENCE_YEARS_KEY = 'jobagent\.queue\.maxRequiredYears'/)
  assert.match(page, /useState<QueueFilters>\(\(\) => \{[\s\S]*?loadRememberedYears\(\)/)
  assert.match(page, /if \(key === 'max_required_years'\) rememberYears\(/)
})

test('reading or writing the remembered value can never break the page', () => {
  // A private window or blocked site data throws on access rather than
  // returning empty, and the queue must still render.
  const load = page.slice(
    page.indexOf('function loadRememberedYears'), page.indexOf('function rememberYears'))
  assert.match(load, /try \{[\s\S]*localStorage\.getItem[\s\S]*\} catch/)
  const save = page.slice(
    page.indexOf('function rememberYears'), page.indexOf('function rememberYears') + 400)
  assert.match(save, /try \{[\s\S]*localStorage[\s\S]*\} catch/)
})

test('重置筛选 forgets the remembered experience too', () => {
  const label = page.indexOf('重置筛选')
  const handler = page.slice(page.lastIndexOf('onClick={() => {', label), label)
  assert.match(handler, /rememberYears\(undefined\)/)
})
