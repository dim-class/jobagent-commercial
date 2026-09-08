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
  // Two greeting modes since 2026-09-03, each with its own strict shape:
  // `boss_dynamic_unverified` must carry no body (a body there would be a
  // guess about what BOSS sends, which a live run already disproved), and
  // `boss_typed_greeting` must carry one (an empty body would type nothing).
  assert.match(background, /dynamic && approval\.answers_text === ''/)
  assert.match(background, /typed && approval\.answers_text\.trim\(\)\.length > 0/)
  assert.match(background, /outcome, detail/)
  // The outcome is still never guessed into `applied`, whichever mode ran.
  assert.match(background, /'clicked_site_result_unverified'/)
  assert.match(background, /'clicked_and_greeted_site_result_unverified'/)
  assert.match(background, /settleM6Outcome\(approval, observed, 'unknown', detail\)/)
  // The greeting is typed after the click and reported separately - it can
  // never re-run or undo the application click.
  const click = background.indexOf("type: 'jobagent:m6-execute'")
  const greeting = background.indexOf("type: 'jobagent:m6-greeting'")
  assert.ok(greeting > click, 'the greeting follows the click, never precedes it')
  assert.doesNotMatch(background, /application-approvals\/\$\{approval\.id\}\/outcome[\s\S]{0,300}outcome:\s*'applied'/)
  assert.doesNotMatch(background, /executeM6Application[\s\S]{0,8000}(?:setInterval|MutationObserver)/)
})

test('the M6 greeting may repair a tab that has no content script', () => {
  // BOSS answers 立即沟通 by loading /web/geek/chat as a fresh document, and
  // the declarative content script attaches at document_idle on a page whose
  // conversation pane alone takes ~2.2s (measured live, 2026-09-08). Every
  // attempt was reaching a tab with no receiver, which is what
  // `greeting_unavailable:chat` records - not a wrong job, not a missing
  // composer, simply nobody to answer.
  //
  // The recovery already existed for the search runner's startup; this pins
  // that M6 asks for it too, since the default is off.
  const call = background.indexOf("'jobagent:m6-greeting'")
  assert.ok(call > 0, 'the greeting call site is still there')
  const tail = background.slice(call, call + 400)
  assert.match(tail, /\}\s*,\s*true\s*\)/,
    'askTab must be called with allowPackagedInjection = true')
})
