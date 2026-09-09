'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const dir = path.join(__dirname, '../src/pages')
const pages = fs.readdirSync(dir)
  .filter((name) => name.endsWith('.tsx'))
  .map((name) => ({ name, src: fs.readFileSync(path.join(dir, name), 'utf8') }))

/** Lines that render text, with comments and code identifiers filtered out.
 *
 *  A crude filter on purpose: it keeps any line holding a CJK character, which
 *  is what user-visible copy looks like in this app, and drops the comment
 *  lines that legitimately discuss internals. */
function visibleLines(src) {
  return src.split('\n')
    .map((line, i) => [i + 1, line])
    .filter(([, line]) => /[\u4e00-\u9fff]/.test(line))
    .filter(([, line]) => !/^\s*(\/\/|\*|\/\*)/.test(line))
}

test('no internal milestone or symbol names in text the user reads', () => {
  // These are CLAUDE.md's names for the work, not the product's names for
  // itself. 「M6 双重人工确认入口」 was on the queue's own subtitle, and it was
  // also stale - the 2026-09-02 amendment folded two confirmation screens into
  // one, so it named a flow that no longer existed. 「本批次上限
  // （MAX_ANALYSES_PER_RUN）」 and 「复用现有 mark_applied 唯一路径」 put a
  // constant and a function name in front of someone deciding whether to spend
  // money.
  const banned = [
    /\bM[4-7][a-z]?\b/,          // milestone names
    /MAX_[A-Z_]{4,}/,            // config constants
    /\bmark_applied\b/,
    /\bjob_intake\b/,
    /canonical intake/i,
    /测试数据/,                   // "…or test data" in a real tooltip
  ]
  const found = []
  for (const { name, src } of pages) {
    for (const [lineNo, line] of visibleLines(src)) {
      for (const pattern of banned) {
        if (pattern.test(line)) found.push(`${name}:${lineNo} ${line.trim().slice(0, 70)}`)
      }
    }
  }
  assert.deepEqual(found, [], `internal names in user-facing copy:\n${found.join('\n')}`)
})

test('the settings page describes the confirmation flow that exists', () => {
  const settings = pages.find((p) => p.name === 'SettingsPage.tsx').src
  // It promised 「双重人工确认」 - two screens - long after the second one was
  // removed. A security page that overstates its own guarantees is worse than
  // one that understates them: the reader cannot tell which claims still hold.
  assert.doesNotMatch(settings, /双重人工确认/)
  assert.match(settings, /不存在全局自动投递模式/)
  assert.match(settings, /每个岗位都要单独确认一次/)
})
