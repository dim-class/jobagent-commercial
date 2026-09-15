'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const src = (rel) => fs.readFileSync(path.join(__dirname, '../src', rel), 'utf8')
const app = src('App.tsx')
const banner = src('components/AiKeyBanner.tsx')
const settings = src('pages/SettingsPage.tsx')

/** Lines a user reads: any line with CJK text that is not a comment. */
function visible(text) {
  return text.split('\n')
    .filter((line) => /[一-鿿]/.test(line))
    .filter((line) => !/^\s*(\/\/|\*|\/\*|\{\/\*)/.test(line))
}

test('with no key configured, the banner itself takes the key', () => {
  // 「请在项目根目录的 .env 中填写后重启后端」 sat on every page: an instruction
  // a finished product should never need, and it named a different file from
  // the one the settings page wrote.
  assert.match(app, /<AiKeyBanner \/>/)
  assert.match(banner, /type="password"/)
  assert.match(banner, /api\.saveAiSettings\(/)
  assert.match(banner, /announceAiSettingsSaved\(\)/)
})

test('a save made anywhere clears the banner without a reload', () => {
  assert.match(app, /addEventListener\(AI_SETTINGS_SAVED/)
  assert.match(settings, /announceAiSettingsSaved\(\)/)
  // 未配置 and the disabled 批量分析 button read `settings`, which a save alone
  // never refreshed.
  const save = settings.slice(
    settings.indexOf('async function saveAi'), settings.indexOf('if (loading && !settings)'))
  assert.match(save, /await load\(\)/)
})

test('no screen tells the user to edit .env and restart', () => {
  const pagesDir = path.join(__dirname, '../src/pages')
  const pages = fs.readdirSync(pagesDir)
    .filter((name) => name.endsWith('.tsx'))
    .map((name) => fs.readFileSync(path.join(pagesDir, name), 'utf8'))
  const offenders = [app, banner, ...pages]
    .flatMap(visible)
    .filter((line) => /\.env/.test(line) && /重启/.test(line))
  assert.deepEqual(offenders, [])
})
