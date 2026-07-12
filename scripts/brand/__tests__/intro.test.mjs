import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import {
  introEmitter,
  hasWordmark,
  setWordmark,
  hasTagline,
  setTagline
} from '../emitters/intro.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

// Loaded directly (not via loadDescriptor) — its slug starts with `_` and
// deliberately fails descriptor.mjs's SLUG_RE, which is what keeps it out of
// real-brand enumeration. It already carries the full descriptor shape, so a
// plain JSON.parse is a valid stand-in for a loaded descriptor in these tests.
const FIXTURE_QUOTE = JSON.parse(fs.readFileSync(path.join(ROOT, 'brands/_fixture-quote.json'), 'utf8'))

test('check(otto) passes against the current intro.tsx tree', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  assert.equal(introEmitter.check(d, { root: ROOT }).ok, true)
})

test('setWordmark replaces the single-quoted value and is idempotent', () => {
  const line = "const WORDMARK = 'OTTO COWORKER'"
  const once = setWordmark(line, 'LOOP24 AGENT')
  assert.match(once, /'LOOP24 AGENT'/)
  assert.equal(hasWordmark(once, 'LOOP24 AGENT'), true)
  assert.equal(setWordmark(once, 'LOOP24 AGENT'), once) // idempotent
})

test('setTagline replaces the double-quoted value and is idempotent', () => {
  const src = 'const TAGLINE = "OTTO orchestrates your thoughts and tasks into effective outcomes — tell me what you need and I\'ll take it from there."'
  const once = setTagline(src, 'Loop24 does things.')
  assert.match(once, /"Loop24 does things\."/)
  assert.equal(hasTagline(once, 'Loop24 does things.'), true)
  assert.equal(setTagline(once, 'Loop24 does things.'), once) // idempotent
})

test('setTagline handles a tagline with an em-dash', () => {
  const src = 'const TAGLINE = "Old tagline."'
  const withEmDash = 'Loop24 handles your work — start to finish.'
  const once = setTagline(src, withEmDash)
  assert.match(once, /"Loop24 handles your work — start to finish\."/)
  assert.equal(hasTagline(once, withEmDash), true)
  assert.equal(setTagline(once, withEmDash), once) // idempotent
})

test('setWordmark escapes a single quote for the single-quoted JS literal (quote-fixture wordmark)', () => {
  const src = "const WORDMARK = 'OTTO COWORKER'"
  const next = setWordmark(src, FIXTURE_QUOTE.wordmark)
  // The line must remain a syntactically valid single-quoted JS string
  // literal: greedily capture between the first and last `'` on the line.
  const m = next.match(/^const WORDMARK = '([\s\S]*)'$/)
  assert.ok(m, `expected a single-quoted const declaration, got: ${next}`)
  // Prove it's actually valid JS that evaluates back to the original value —
  // not merely "matches a permissive regex".
  const evaluated = Function(`'use strict'; return '${m[1]}';`)()
  assert.equal(evaluated, FIXTURE_QUOTE.wordmark)
})

test('setWordmark escapes a literal backslash for the single-quoted JS literal', () => {
  const src = "const WORDMARK = 'OTTO COWORKER'"
  const tricky = String.raw`C:\brands\weird'name`
  const next = setWordmark(src, tricky)
  const m = next.match(/^const WORDMARK = '([\s\S]*)'$/)
  assert.ok(m, `expected a single-quoted const declaration, got: ${next}`)
  const evaluated = Function(`'use strict'; return '${m[1]}';`)()
  assert.equal(evaluated, tricky)
})

test('setTagline escapes an embedded double quote for the double-quoted JS literal (quote-fixture tagline)', () => {
  const src = 'const TAGLINE = "Old tagline."'
  const next = setTagline(src, FIXTURE_QUOTE.tagline)
  const m = next.match(/^const TAGLINE = "([\s\S]*)"$/)
  assert.ok(m, `expected a double-quoted const declaration, got: ${next}`)
  const evaluated = Function(`'use strict'; return "${m[1]}";`)()
  assert.equal(evaluated, FIXTURE_QUOTE.tagline)
})
