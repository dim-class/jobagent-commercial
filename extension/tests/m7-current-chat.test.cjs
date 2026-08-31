'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const read = name => fs.readFileSync(path.join(__dirname, '../src', name), 'utf8')
const selectors = read('boss/selectors.ts')
const extract = read('boss/extract.ts')
const background = read('background.ts')
const bridge = read('console-bridge.ts')
const overlay = read('overlay.ts')

test('M7 runtime entry is suspended after live risk-control', () => {
  assert.doesNotMatch(bridge, /scan-current-boss-chat/)
  const command = background.slice(background.indexOf('async function consoleCommand'),
    background.indexOf('chrome.runtime.onMessage.addListener'))
  assert.doesNotMatch(command, /scan-current-boss-chat|boss-current-chat-scan-v1|scanCurrentBossChat/)
})

test('retained fixture-only M7 parser still has no send or credential primitive', () => {
  const m7 = extract.slice(extract.indexOf('function scanCurrentBossConversation'), extract.indexOf('// ------------------------------------------------------------------- api'))
  assert.match(selectors, /CHAT_MESSAGE_TEXT/)
  assert.doesNotMatch(m7, /MutationObserver|setInterval|querySelector\([^)]*(?:textarea|input|send)/)
  assert.doesNotMatch(m7, /data-url|redirect-url|securityId|localStorage|sessionStorage/)
  assert.doesNotMatch(background, /scanCurrentBossChat[\s\S]{0,5000}\/analyze/)
})
