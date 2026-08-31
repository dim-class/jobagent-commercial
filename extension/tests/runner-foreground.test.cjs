'use strict'
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const code = fs.readFileSync(path.join(__dirname, '../dist/background.js'), 'utf8')

function harness(options = {}) {
  const calls = []
  const window = { id: 4, type: 'normal', focused: true, ...options.window }
  const tab = { id: 7, windowId: 4, active: true, url: 'https://www.zhipin.com/web/geek/jobs' }
  const chrome = {
    runtime: { onMessage: { addListener() {} } },
    tabs: { query: async query => {
      calls.push(query)
      // Reproduce the real failure: implicit worker window has no tabs.
      if (query.currentWindow) return []
      return options.tabs ?? [tab]
    } },
    windows: {
      getLastFocused: async query => {
        assert.deepEqual(JSON.parse(JSON.stringify(query)), { windowTypes: ['normal'] })
        if (options.error) throw new Error('window unavailable')
        return window
      },
      get: async id => {
        assert.equal(id, 4)
        return { focused: options.focusAfter !== false }
      },
    },
  }
  const ctx = vm.createContext({ chrome, URL, console, setTimeout, clearTimeout })
  vm.runInContext(code, ctx)
  return { calls, run: () => ctx.foregroundStartTab() }
}

test('runner resolves explicit focused window despite missing implicit worker context', async () => {
  const h = harness()
  const result = await h.run()
  assert.equal(result.ok, true)
  assert.equal(result.tab.id, 7)
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls)), [{ active: true, windowId: 4 }])
})

for (const [name, options, reason] of [
  ['window unfocused', { window: { focused: false } }, 'window_not_focused'],
  ['not normal window', { window: { type: 'popup' } }, 'window_not_normal'],
  ['missing window id', { window: { id: undefined } }, 'window_id_missing'],
  ['window closed', { error: true }, 'window_query_failed'],
  ['no active tab', { tabs: [] }, 'active_tab_missing'],
  ['ambiguous active tabs', { tabs: [{ active: true, windowId: 4 }, { active: true, windowId: 4 }] }, 'active_tab_ambiguous'],
  ['tab in another window', { tabs: [{ active: true, windowId: 9 }] }, 'tab_window_mismatch'],
  ['inactive tab', { tabs: [{ active: false, windowId: 4 }] }, 'tab_not_active'],
  ['focus lost during query', { focusAfter: false }, 'window_focus_changed'],
]) test('runner refuses ' + name, async () => {
  const result = await harness(options).run()
  assert.equal(result.ok, false)
  assert.equal(result.error, 'start-v3/' + reason)
  assert.equal(result.tab, undefined)
})
