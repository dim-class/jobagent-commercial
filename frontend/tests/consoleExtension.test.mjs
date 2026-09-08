import assert from 'node:assert/strict'
import test from 'node:test'
import { assessConsoleConnection, consoleExtension, DEFAULT_BATCH_CANDIDATE_CAP,
  selectBoundedPendingTasks } from '../src/pages/consoleExtension.ts'

function setup(t) {
  const originalWindow = globalThis.window, originalLocation = globalThis.location
  let timeout
  const listeners = new Set(), sent = []
  const window = {
    setTimeout: fn => { timeout = fn; return 1 },
    addEventListener: (_, fn) => listeners.add(fn),
    removeEventListener: (_, fn) => listeners.delete(fn),
    postMessage: (data, origin) => sent.push({ data, origin }),
  }
  globalThis.window = window
  globalThis.location = { origin: 'http://127.0.0.1:5173' }
  t.after(() => { globalThis.window = originalWindow; globalThis.location = originalLocation })
  return { sent, listeners, expire: () => timeout(), reply: overrides => {
    const first = sent[0].data
    const event = { source: window, origin: location.origin,
      data: { channel: 'jobagent-console-response', id: first.id, result: { ok: true } }, ...overrides }
    for (const listener of [...listeners]) listener(event)
  } }
}
test('console request carries task/cap only and cleans correlated response listener', async t => {
  const env = setup(t)
  assert.equal(env.sent.length, 0)
  const promise = consoleExtension('start', 5, 3)
  assert.equal(env.sent.length, 1)
  assert.equal(env.sent[0].data.action, 'start')
  assert.equal(env.sent[0].data.taskId, 5)
  assert.equal(env.sent[0].data.candidateCap, 3)
  env.reply()
  assert.equal((await promise).ok, true)
  assert.equal(env.listeners.size, 0)
})
test('batch request carries only the approved task ids and cap', async t => {
  const env = setup(t)
  const promise = consoleExtension('start-batch', undefined, 1, [5, 6])
  assert.deepEqual(env.sent[0].data.taskIds, [5, 6])
  assert.equal(env.sent[0].data.taskId, undefined)
  assert.equal(env.sent[0].data.candidateCap, 1)
  env.reply()
  assert.equal((await promise).ok, true)
})
test('bounded batch selection honors the explicitly chosen 1-16 task count', () => {
  assert.equal(DEFAULT_BATCH_CANDIDATE_CAP, 1)
  const tasks = [
    { id: 7, state: 'pending' }, { id: 8, state: 'completed' },
    { id: 13, state: 'pending' }, { id: 14, state: 'pending' },
  ]
  assert.deepEqual(selectBoundedPendingTasks(tasks, 2).map(task => task.id), [7, 13])
  assert.deepEqual(selectBoundedPendingTasks(tasks, 1).map(task => task.id), [7])
  const broad = Array.from({ length: 18 }, (_, index) => ({ id: index + 1, state: 'pending' }))
  assert.equal(selectBoundedPendingTasks(broad, 16).length, 16)
  for (const count of [0, 17, 1.5, NaN]) assert.throws(() => selectBoundedPendingTasks(tasks, count), /1–16/)
})
test('unrelated windows, origins and request ids cannot acknowledge a command', async t => {
  const env = setup(t)
  const promise = consoleExtension('pause', 5)
  env.reply({ source: {} }); env.reply({ origin: 'https://evil.test' })
  env.reply({ data: { channel: 'jobagent-console-response', id: 'wrong', result: { ok: true } } })
  assert.equal(env.listeners.size, 1)
  env.reply()
  await promise
})
test('start timeout is unknown, never retries or leaves a response listener', async t => {
  const env = setup(t)
  const promise = consoleExtension('start', 5, 3)
  env.expire()
  await assert.rejects(promise, /尚未确认/)
  assert.equal(env.sent.length, 1)
  assert.equal(env.listeners.size, 0)
})
test('missing bridge gives actionable Chrome instructions without browser actions', async t => {
  const env = setup(t)
  const promise = consoleExtension('status')
  env.expire()
  await assert.rejects(promise, err => err.code === 'bridge_no_response' && /原因未确定/.test(err.message))
  assert.equal(env.sent.length, 1)
  assert.equal(env.sent[0].data.action, 'status')
})

test('bridge receipt is not worker success and does not retry or reset deadline', async t => {
  const env = setup(t)
  const promise = consoleExtension('status')
  const receipt = { channel: 'jobagent-console-receipt', id: env.sent[0].data.id, protocol: 1 }
  env.reply({ data: receipt }); env.reply({ data: receipt })
  assert.equal(env.listeners.size, 1)
  env.expire()
  await assert.rejects(promise, err => err.code === 'worker_timeout')
  assert.equal(env.listeners.size, 0)
  assert.equal(env.sent.length, 1)
})

test('wrong-origin receipt cannot change the diagnosis; mutation receipt is not acknowledgement', async t => {
  const env = setup(t)
  const status = consoleExtension('status')
  env.reply({ origin: 'https://evil.test', data: { channel: 'jobagent-console-receipt', id: env.sent[0].data.id, protocol: 1 } })
  env.expire()
  await assert.rejects(status, err => err.code === 'bridge_no_response')
  const start = consoleExtension('start', 5, 3)
  env.reply({ data: { channel: 'jobagent-console-receipt', id: env.sent[1].data.id, protocol: 1 } })
  env.expire()
  await assert.rejects(start, err => err.code === 'command_unknown')
  assert.equal(env.sent.length, 2)
})

test('compatible version and complete runner state are needed before enabling start', () => {
  const ready = { ok: true, protocol: 1, extensionVersion: '0.1.1', capabilities: ['console-search-v1'], runner: null }
  assert.equal(assessConsoleConnection(ready).ready, true)
  assert.match(assessConsoleConnection(ready).detail, /0.1.1/)
  const old = assessConsoleConnection({ ok: true, protocol: 1, runner: null })
  assert.equal(old.ready, false)
  assert.match(old.detail, /旧版扩展已响应/)
  for (const patch of [{ protocol: 2 }, { capabilities: [] }, { extensionVersion: '<bad>' }, { extensionVersion: 123 },
    { runner: undefined }, { runner: {} }, { runner: { taskId: 5, candidateCap: 61, phase: 'running', paid: false, paused: false } }]) {
    assert.equal(assessConsoleConnection({ ...ready, ...patch }).ready, false)
  }
  assert.equal(assessConsoleConnection({ ...ready, runner: { taskId: 5, candidateCap: 3, phase: 'paused', paid: false, paused: true } }).ready, true)
  assert.equal(assessConsoleConnection({ ok: false, error: 'worker unavailable' }).ready, false)
  const batchReady = { ...ready, capabilities: ['console-search-v1', 'console-batch-v1'], batch: {
    state: 'running', taskIds: [5, 6], currentIndex: 0, currentTaskId: 5, candidateCap: 1,
    requiresResume: false, lastError: null,
  } }
  assert.equal(assessConsoleConnection(batchReady).ready, true)
  for (const batch of [{ ...batchReady.batch, taskIds: [5, 5] },
    { ...batchReady.batch, currentIndex: 2 }, { ...batchReady.batch, currentTaskId: 6 },
    { ...batchReady.batch, candidateCap: 61 }]) {
    assert.equal(assessConsoleConnection({ ...batchReady, batch }).ready, false)
  }
})
