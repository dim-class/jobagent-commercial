const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const root = path.resolve(__dirname, '..')

test('suspended M7 chat scanner is absent from extension runtime', () => {
  const runtime = [
    'src/background.ts',
    'src/content.ts',
    'src/overlay.ts',
    'src/boss/extract.ts',
    'src/boss/selectors.ts',
  ].map(file => fs.readFileSync(path.join(root, file), 'utf8')).join('\n')

  for (const marker of [
    'jobagent:m7-scan-current-chat',
    'scanCurrentBossChat',
    'scanCurrentBossConversation',
    'selectNextBossConversation',
    'scrollBossConversationList',
    'resetBossConversationTraversal',
    '/api/recruiter-conversations/boss-current-scan',
    'CHAT_MESSAGE_TEXT',
  ]) assert.doesNotMatch(runtime, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
})

test('historical M7 fixture remains test-only and cannot enter the package allow-list', () => {
  assert.equal(fs.existsSync(path.join(root, 'tests/fixtures/boss_chat_current_conversation.html')), true)
  const builder = fs.readFileSync(path.resolve(root, '..', 'scripts/build-extension-store.py'), 'utf8')
  assert.doesNotMatch(builder, /tests\/fixtures|boss_chat_current_conversation/)
})
