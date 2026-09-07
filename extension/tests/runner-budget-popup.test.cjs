'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const code = fs.readFileSync(path.join(__dirname, '../dist/runner.js'), 'utf8')
const html = fs.readFileSync(path.join(__dirname, '../popup.html'), 'utf8')
const settle = async () => { await new Promise(resolve => setImmediate(resolve)) }

async function popup({ runner = null, status = 'pending', maxCandidates = null, actionReply = { ok: true }, deferAction = false, consent = true } = {}) {
  const elements = new Map()
  const messages = []
  const state = { closes: 0, reply: null, confirmations: [], reads: [] }
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: id === 'runner-candidate-cap' ? /id="runner-candidate-cap"[^>]*value="(\d+)"/.exec(html)[1] : '',
      textContent: '', disabled: false, listeners: {},
      get valueAsNumber() { return this.value.trim() ? Number(this.value) : NaN },
      addEventListener(event, callback) { this.listeners[event] = callback },
      appendChild(option) { if (!this.value) this.value = option.value },
    })
    return elements.get(id)
  }
  const task = { id: 5, task_id: 5, name: 'SRE', run_status: status, max_candidates: maxCandidates }
  const context = {
    window: { close() { state.closes++ }, confirm(text) { state.confirmations.push(text); return consent } },
    document: { getElementById: element },
    Option: function (label, value) { return { label, value } },
    JobAgentConfig: { BACKEND_BASE: 'http://127.0.0.1:8000', SEARCH_PLAN_PATH: '/api/tasks/search-plan' },
    fetch: async url => {
      state.reads.push(url)
      return { ok: true, text: async () => JSON.stringify(url.includes('/auto-match/quote')
        ? { cap: 3, resume_name: '演示简历', model: 'test-model', fingerprint: 'a'.repeat(64) }
        : url.endsWith('/5') ? task : { items: [task] }) }
    },
    chrome: { tabs: { query: async () => [{ id: 7, windowId: 4 }] }, runtime: { sendMessage(message, callback) {
      messages.push(message)
      if (message.type === 'jobagent:runner-status') callback({ ok: true, runner })
      else if (deferAction) state.reply = () => callback(actionReply)
      else callback(actionReply)
    } } },
  }
  vm.runInNewContext(code, context)
  await settle()
  return { element, messages, state, click: async id => { element(id).listeners.click(); await settle() } }
}

test('popup defaults to 3 and start sends the explicit approved cap', async () => {
  const ui = await popup()
  assert.equal(ui.element('runner-candidate-cap').value, '3')
  assert.equal(ui.element('runner-candidate-cap').disabled, false)
  await ui.click('runner-start')
  const start = ui.messages.find(m => m.type === 'jobagent:runner-start')
  assert.equal(start.candidateCap, 3)
  assert.equal(start.matchApproval, undefined)
  assert.equal(ui.state.confirmations.length, 0)
  assert.deepEqual(JSON.parse(JSON.stringify(start.popupTarget)), { tabId: 7, windowId: 4 })
  assert.equal(ui.state.closes, 1, 'release popup focus after successful runner start')
})

for (const consent of [false, true]) {
  test(`optional auto-match requires explicit runtime consent ${consent}`, async () => {
    const ui = await popup({ consent })
    ui.element('runner-auto-match').checked = true
    await ui.click('runner-start')
    assert.equal(ui.state.confirmations.length, 1)
    assert.match(ui.state.confirmations[0], /演示简历/)
    assert.match(ui.state.confirmations[0], /test-model/)
    assert.match(ui.state.confirmations[0], /最多 3 次付费/)
    const start = ui.messages.find(m => m.type === 'jobagent:runner-start')
    assert.equal(!!start, consent)
    if (consent) assert.equal(start.matchApproval.fingerprint, 'a'.repeat(64))
    assert.equal(ui.state.closes, consent ? 1 : 0)
  })
}

test('optional auto-match rejects four candidates before quote or start', async () => {
  const ui = await popup()
  ui.element('runner-auto-match').checked = true
  ui.element('runner-candidate-cap').value = '4'
  await ui.click('runner-start')
  assert.equal(ui.state.reads.some(p => p.includes('/auto-match/')), false)
  assert.equal(ui.messages.some(m => m.type === 'jobagent:runner-start'), false)
})

// '61' rather than '21': the ceiling is RUNNER_MAX_CANDIDATES (60). It was
// 20 when this list was written, and a stale copy of that number in the
// console's own handshake validator is what silently disabled every button on
// 2026-09-07 - see tests/test_console_bridge_bounds.py.
for (const value of ['', '0', '61', '1.5', 'abc']) {
  test(`popup rejects invalid cap ${JSON.stringify(value)} without starting`, async () => {
    const ui = await popup()
    ui.element('runner-candidate-cap').value = value
    await ui.click('runner-start')
    assert.equal(ui.messages.some(m => m.type === 'jobagent:runner-start'), false)
    assert.match(ui.element('runner-status').textContent, /整数候选上限/)
    assert.equal(ui.state.closes, 0)
  })
}

test('popup reopening displays the stored cap and disables editing during pause', async () => {
  const ui = await popup({ status: 'paused', runner: {
    taskId: 5, candidateCap: 3, candidatesAttempted: 1, candidatesProcessed: 0, pauseRequested: true,
  } })
  assert.equal(ui.element('runner-candidate-cap').value, '3')
  assert.equal(ui.element('runner-candidate-cap').disabled, true)
  assert.match(ui.element('runner-counters').textContent, /已用候选名额 1\/3/)
  await ui.click('runner-resume')
  const message = ui.messages.find(m => m.type === 'jobagent:runner-resume')
  assert.ok(message)
  assert.equal('candidateCap' in message, false)
  assert.equal(ui.state.closes, 1, 'release popup focus after successful runner resume')
})

for (const [button, status] of [['runner-start', 'pending'], ['runner-resume', 'paused']]) {
  test(`${button} closes only after background accepts the request`, async () => {
    const ui = await popup({ status, deferAction: true })
    await ui.click(button)
    assert.equal(ui.state.closes, 0)
    assert.ok(ui.state.reply)
    ui.state.reply()
    await settle()
    assert.equal(ui.state.closes, 1)
  })
  test(`${button} failure stays open and shows error`, async () => {
    const ui = await popup({ status, actionReply: { ok: false, error: 'not_foreground' } })
    await ui.click(button)
    assert.equal(ui.state.closes, 0)
    assert.match(ui.element('runner-status').textContent, /not_foreground/)
  })
}

for (const button of ['runner-pause', 'runner-cancel', 'runner-refresh']) {
  test(`${button} does not close the control popup`, async () => {
    const ui = await popup({ status: 'running' })
    await ui.click(button)
    assert.equal(ui.state.closes, 0)
  })
}

test('popup does not start above a lower task-specific cap', async () => {
  const ui = await popup({ maxCandidates: 2 })
  await ui.click('runner-start')
  assert.equal(ui.messages.some(m => m.type === 'jobagent:runner-start'), false)
  ui.element('runner-candidate-cap').value = '2'
  await ui.click('runner-start')
  assert.equal(ui.messages.find(m => m.type === 'jobagent:runner-start').candidateCap, 2)
})

test('startup diagnostic identifies the failed guard and keeps popup open', async () => {
  const ui = await popup({ actionReply: { ok: false, error: 'start-v3/window_not_focused' } })
  await ui.click('runner-start')
  assert.equal(ui.state.closes, 0)
  assert.match(ui.element('runner-status').textContent, /窗口未聚焦/)
  assert.match(ui.element('runner-status').textContent, /start-v3\/window_not_focused/)
})
