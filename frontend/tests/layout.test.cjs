'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const css = fs.readFileSync(path.join(__dirname, '../src/styles/app.css'), 'utf8')

test('a stacking margin never applies inside a container that owns its gap', () => {
  // `.card + .card { margin-top: 14px }` is for cards following one another in
  // normal flow. A grid spaces its own children with `gap`, and a stretched
  // grid item's height is the row minus its own margins - so the margin made
  // the row's FIRST card 14px taller than every sibling. On 投递队列 that put
  // 待处理 a visible head above the other four counters.
  assert.match(css, /\.grid > \.card \+ \.card \{ margin-top: 0; \}/)

  // Direct children only. Two cards genuinely stacked inside one grid cell
  // still want the margin between them.
  assert.doesNotMatch(css, /\.grid \.card \+ \.card/)
})

test('the filter row aligns by stretch, so one hint cannot lift its neighbours', () => {
  const filters = css.slice(css.indexOf('.filters {'), css.indexOf('.choice-group'))
  // `align-items: end` hangs every cell off the tallest one's bottom edge, so
  // 我的经验年数 - the only filter carrying a hint under its control - pushed
  // its own label and select 67px above the rest of the row.
  assert.doesNotMatch(filters, /align-items:\s*(end|flex-end)/)
  // A column, so a hint sits under its control instead of flowing inline
  // after the select and wrapping around it.
  assert.match(filters, /\.filters \.field \{[^}]*flex-direction: column/s)
})

test('the boxed fold style and the one page that wants it stay paired', () => {
  // The box moved off the base `details` selector, so the page it was written
  // for has to opt in by name. If either half is renamed without the other,
  // the offers list silently loses the borders that separate one offer from
  // the next - a visual regression no type-check would catch.
  const decision = fs.readFileSync(
    path.join(__dirname, '../src/pages/DecisionPage.tsx'), 'utf8')
  assert.match(css, /\.details-panel \{/)
  assert.match(decision, /className="details-panel"/)

  // And the base selector must not carry the box again.
  const base = css.slice(css.indexOf('details {'), css.indexOf('details > summary'))
  assert.doesNotMatch(base, /border|background/)
})
