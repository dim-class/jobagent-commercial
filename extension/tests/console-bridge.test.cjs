const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const code = fs.readFileSync(path.join(__dirname, '../dist/console-bridge.js'), 'utf8')
function bridge(mode) {
  const calls = [], replies = []
  let listener
  const window = { addEventListener: (_, fn) => { listener = fn }, postMessage: msg => replies.push(msg) }
  window.top = window
  const location = { origin: 'http://127.0.0.1:5173', pathname: '/', hash: '#/console' }
  const navigator = { userActivation: { isActive: false } }
  vm.runInNewContext(code, { window, location, navigator, chrome: { runtime: {
    lastError: mode === 'lastError' ? { message: 'Private runtime detail must not escape' } : undefined,
    sendMessage: (msg, reply) => { calls.push(msg); if (mode === 'throw') throw new Error('Private runtime detail');
      if (mode !== 'hang') reply({ ok: true, protocol: 1 }) },
  } } })
  const send = (action, extras = {}, eventOverrides = {}) => listener({ source: window,
    origin: location.origin, data: { channel: 'jobagent-console-request', id: 'test', action, taskId: 5, ...extras }, ...eventOverrides })
  return { calls, replies, send, navigator, location }
}
test('bridge status is read only, requires no activation, never forwards arbitrary fields', () => {
  const env = bridge()
  env.send('status', { url: 'https://evil.test', matchApproval: {}, type: 'jobagent:runner-start' })
  assert.equal(env.calls[0].type, 'jobagent:console-command')
  assert.equal(env.calls[0].action, 'status')
  assert.equal(env.calls[0].url, undefined)
  assert.equal(env.calls[0].matchApproval, undefined)
  assert.equal(env.replies[0].channel, 'jobagent-console-receipt')
  assert.equal(env.replies[1].channel, 'jobagent-console-response')
})
test('bridge refuses background/timer single, batch or M6 mutations without activation', () => {
  const env = bridge()
  for (const action of ['start', 'resume', 'pause', 'cancel', 'start-batch', 'pause-batch', 'resume-batch', 'cancel-batch', 'execute-application']) env.send(action)
  assert.equal(env.calls.length, 0)
  assert.equal(env.replies.length, 9)
  assert.ok(env.replies.every(r => !r.result.ok))
})
test('bridge requires same window, exact origin and console route', () => {
  const env = bridge()
  env.send('status', {}, { source: {} })
  env.send('status', {}, { origin: 'http://evil.test' })
  env.send('apply')
  env.location.hash = '#/jobs'
  env.send('status')
  assert.equal(env.calls.length, 0)
})

test('bridge accepts the exact queue route and forwards only one approval id on activation', () => {
  const env = bridge()
  env.location.hash = '#/queue'
  env.navigator.userActivation.isActive = true
  env.send('execute-application', { approvalId: 17, answersText: 'must not cross bridge', jobIds: [1, 2] })
  assert.equal(env.calls.length, 1)
  assert.equal(env.calls[0].action, 'execute-application')
  assert.equal(env.calls[0].approvalId, 17)
  assert.equal(env.calls[0].answersText, undefined)
  assert.equal(env.calls[0].jobIds, undefined)
})
test('bridge forwards one allowlisted action on human activation', () => {
  const env = bridge()
  env.navigator.userActivation.isActive = true
  env.send('start', { candidateCap: 3 })
  assert.equal(env.calls.length, 1)
  assert.equal(env.calls[0].candidateCap, 3)
  assert.equal(env.replies.length, 1) // mutations never receive diagnostic receipts
})

test('bridge forwards only bounded batch ids/cap fields on human activation', () => {
  const env = bridge()
  env.navigator.userActivation.isActive = true
  env.send('start-batch', { taskIds: [5, 6], candidateCap: 1, url: 'https://evil.test', tabId: 99 })
  assert.deepEqual(env.calls[0].taskIds, [5, 6])
  assert.equal(env.calls[0].candidateCap, 1)
  assert.equal(env.calls[0].url, undefined)
  assert.equal(env.calls[0].tabId, undefined)
})

test('bridge distinguishes missing worker response and invalid runtime without exposing errors', () => {
  for (const [mode, code] of [['lastError', 'worker_unavailable'], ['throw', 'extension_context_unavailable'], ['hang', undefined]]) {
    const env = bridge(mode)
    env.send('status')
    assert.equal(env.replies[0].channel, 'jobagent-console-receipt')
    assert.equal(env.replies[1]?.result.code, code)
    assert.ok(!JSON.stringify(env.replies).includes('Private runtime detail'))
    assert.equal(env.calls.length, 1)
  }
})
test('manifest bridge stays exact loopback and BOSS; no all-sites permission or external protocol', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '../manifest.json'), 'utf8'))
  assert.deepEqual(manifest.content_scripts[1].matches, ['http://127.0.0.1:5173/*', 'http://localhost:5173/*'])
  assert.deepEqual(manifest.host_permissions, ['http://127.0.0.1:8000/*', 'http://localhost:8000/*',
    'https://www.zhipin.com/*', 'http://127.0.0.1:5173/*', 'http://localhost:5173/*'])
  assert.equal(manifest.externally_connectable, undefined)
})
