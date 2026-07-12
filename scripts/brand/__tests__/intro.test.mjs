import { test } from 'node:test'
import assert from 'node:assert/strict'
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
