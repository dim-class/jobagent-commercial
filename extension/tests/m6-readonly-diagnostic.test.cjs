'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const selectors = fs.readFileSync(path.join(__dirname, '../src/boss/selectors.ts'), 'utf8')
const extract = fs.readFileSync(path.join(__dirname, '../src/boss/extract.ts'), 'utf8')
const content = fs.readFileSync(path.join(__dirname, '../src/content.ts'), 'utf8')
const background = fs.readFileSync(path.join(__dirname, '../src/background.ts'), 'utf8')
const fixture = fs.readFileSync(path.join(__dirname, 'fixtures/boss_job_detail_live_shape.html'), 'utf8')

test('the M6 probe stays read-only and execution has exactly one named click primitive', () => {
  assert.match(selectors, /APPLICATION_CONTROL:\s*\['\.btn-startchat'\]/)
  assert.match(extract, /querySelectorAll\(selector\)/)
  assert.match(extract, /visible_usable_count:\s*visibleUsableCount/)
  assert.match(extract, /unique_visible_usable_control:\s*visibleUsableCount === 1/)
  assert.match(extract, /confirmed_message_text:\s*null/)
  assert.match(extract, /禁止据此执行/)
  assert.equal((extract.match(/control\.node\.click\(\)/g) || []).length, 1)
  // The selector's own doc comment must keep saying it is clicked. It once
  // claimed "Nothing in the current extension clicks this selector" while
  // execute already did - a false guarantee on the riskiest selector here.
  assert.match(selectors, /\*\*This selector IS clicked\*\*/)
  assert.doesNotMatch(selectors, /Nothing in the current extension clicks/)
  assert.match(selectors, /verified read-only against the user's own logged-in/)
  assert.match(selectors, /Still not verified: that clicking actually submits/)
  assert.match(content, /const M6_PREFLIGHT = 'jobagent:m6-preflight'/)
  assert.match(content, /const M6_EXECUTE = 'jobagent:m6-execute'/)
})

test('fixture carries structural evidence but the probe never exposes sensitive attribute values', () => {
  assert.match(fixture, /class="btn btn-startchat"/)
  assert.equal((fixture.match(/class="btn btn-startchat"/g) || []).length, 2)
  assert.match(fixture, /class="btn btn-startchat" style="display:none"/)
  assert.match(fixture, /redirect-url=/)
  assert.match(fixture, /data-url=/)
  assert.match(extract, /redirect_url_present:\s*node\.hasAttribute\('redirect-url'\)/)
  assert.match(extract, /data_url_present:\s*node\.hasAttribute\('data-url'\)/)
  assert.doesNotMatch(extract, /getAttribute\(['"](?:redirect-url|data-url)['"]\)/)
})

test('M6 claims one attempt before the click and never guesses applied', () => {
  const begin = background.indexOf('/begin`')
  const clickMessage = background.indexOf("type: 'jobagent:m6-execute'")
  assert.ok(begin > 0 && clickMessage > begin)
  assert.match(background, /answers_source !== 'boss_dynamic_unverified'/)
  assert.match(background, /answers_text !== ''/)
  assert.match(background, /outcome, detail/)
  assert.match(background, /'unknown', 'clicked_site_result_unverified'/)
  assert.doesNotMatch(background, /application-approvals\/\$\{approval\.id\}\/outcome[\s\S]{0,300}outcome:\s*'applied'/)
  assert.doesNotMatch(background, /executeM6Application[\s\S]{0,8000}(?:setInterval|MutationObserver)/)
})
