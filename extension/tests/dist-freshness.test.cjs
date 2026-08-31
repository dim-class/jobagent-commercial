'use strict'

/**
 * `extension/dist` is committed on purpose: Chrome loads the unpacked
 * extension straight from `extension/`, and the extraction tests inject the
 * built files. That makes a stale `dist` genuinely dangerous:
 *
 *   - the tests inject the OLD bundle, so they pass against code that is not
 *     the source anyone is reading or reviewing;
 *   - Chrome loads the OLD bundle, so a fix that "passed" never actually runs.
 *
 * This really happened: `extension/node_modules` lost its typescript, so
 * `npm run build` could not run at all and 294 tests were green against a
 * bundle nobody could regenerate. Nothing caught it.
 *
 * So: compile the current source to a scratch directory and require the result
 * to match `dist` byte for byte.
 */

const { test } = require('node:test')
const assert = require('node:assert/strict')
const { execFileSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const root = path.resolve(__dirname, '..')
const dist = path.join(root, 'dist')

/** Every emitted file, relative to a build root, sorted for stable compares. */
function listFiles(dir) {
  const out = []
  const walk = (current, prefix) => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true }).sort((a, b) =>
      a.name.localeCompare(b.name))) {
      const rel = prefix ? `${prefix}/${entry.name}` : entry.name
      if (entry.isDirectory()) walk(path.join(current, entry.name), rel)
      else out.push(rel)
    }
  }
  walk(dir, '')
  return out
}

test('the committed dist is exactly what the current source compiles to', () => {
  const tsc = path.join(root, 'node_modules', 'typescript', 'bin', 'tsc')
  // A missing compiler is precisely the failure this guard exists for: it is
  // what let a stale bundle sit behind a green suite. Say so, do not skip.
  assert.ok(
    fs.existsSync(tsc),
    'typescript is not installed in extension/node_modules, so dist cannot be '
      + 'verified against src. Run `npm install` in extension/ before trusting '
      + 'any extension test result.',
  )

  const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'jobagent-dist-'))
  try {
    execFileSync(process.execPath, [tsc, '-p', 'tsconfig.json', '--outDir', scratch], {
      cwd: root,
      stdio: 'pipe',
    })

    const fresh = listFiles(scratch)
    const committed = listFiles(dist)
    assert.deepEqual(
      committed,
      fresh,
      'dist has a different set of files than a fresh build - run `npm run build`',
    )

    const drifted = fresh.filter((rel) =>
      !fs.readFileSync(path.join(scratch, rel)).equals(fs.readFileSync(path.join(dist, rel))))
    assert.deepEqual(
      drifted,
      [],
      `dist is stale for: ${drifted.join(', ')}. Run \`npm run build\` and commit the `
        + 'result - the tests inject dist, and Chrome loads it.',
    )
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true })
  }
})
