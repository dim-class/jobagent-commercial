const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
const onboarding = fs.readFileSync(path.join(root, 'src/pages/OnboardingPage.tsx'), 'utf8')
const consolePanel = fs.readFileSync(path.join(root, 'src/pages/ConsoleSearchPanel.tsx'), 'utf8')
const client = fs.readFileSync(path.join(root, 'src/api/client.ts'), 'utf8')

test('first-use setup requires resume, supported city and preferred role before save', () => {
  assert.match(onboarding, /api\.getActiveResume\(\)/)
  assert.match(onboarding, /api\.getSearchPlanOptions\(\)/)
  assert.match(onboarding, /if \(!resume\)/)
  assert.match(onboarding, /if \(!supportedCities\.length\)/)
  assert.match(onboarding, /if \(!preferredRoles\.length\)/)
  assert.match(onboarding, /api\.saveStrategy/)
})

test('console search reads backend capabilities and saved strategy instead of city or role literals', () => {
  assert.match(consolePanel, /api\.getSearchPlanOptions/)
  assert.match(consolePanel, /api\.getStrategy/)
  assert.match(consolePanel, /strategyResponse\.strategy\.target_cities/)
  assert.match(consolePanel, /strategyResponse\.strategy\.preferred_roles/)
  assert.doesNotMatch(consolePanel, /\['北京', '上海', '广州', '杭州'\]/)
  assert.doesNotMatch(consolePanel, /useState\('云平台'\)/)
})

test('search capability request is read-only', () => {
  assert.match(client, /getSearchPlanOptions:[\s\S]*request<SearchPlanOptions>\('\/api\/tasks\/search-plan\/options', \{ signal \}\)/)
})

test('personal quick search is aggregate, broader and does not expose execution order', () => {
  assert.match(consolePanel, /useState\(8\)/)
  assert.match(consolePanel, /本次综合搜索/)
  assert.match(consolePanel, /整体进度/)
  assert.match(consolePanel, /最多 8 个相关方向/)
  assert.doesNotMatch(consolePanel, /将按以下固定顺序/)
  assert.doesNotMatch(consolePanel, /<ol>/)
})
