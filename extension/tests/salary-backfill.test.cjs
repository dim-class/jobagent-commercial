const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const root = path.resolve(__dirname, '..')
const source = fs.readFileSync(path.join(root, 'src', 'background.ts'), 'utf8')
const bridge = fs.readFileSync(path.join(root, 'src', 'console-bridge.ts'), 'utf8')
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'))

test('salary backfill is explicit, bounded and uses canonical intake', () => {
  assert.match(source, /salary-backfill-v1/)
  assert.match(source, /salary-backfill\/runs\/\$\{runId\}\/claim/)
  assert.match(source, /\/api\/extension\/jobs\/preview/)
  assert.match(source, /\/api\/extension\/jobs\/import/)
  assert.match(source, /row\.existing_job_id !== item\.job_id/)
  assert.match(source, /imported\.duplicate !== true/)
  assert.match(source, /reason: 'login_required'/)
  assert.match(source, /reason: 'verification'/)
})

test('console bridge requires a real user activation for every write', () => {
  assert.match(bridge, /start-salary-backfill/)
  assert.match(bridge, /navigator\.userActivation\.isActive/)
  assert.equal(manifest.version, '0.1.20')
})
