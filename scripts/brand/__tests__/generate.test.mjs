// scripts/brand/__tests__/generate.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { runEmitters, parseArgs, DEFAULT_EMITTERS } from '../generate.mjs'
import { resolveActiveBrand } from '../active.mjs'
import { loadDescriptor } from '../descriptor.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

const fakeDescriptor = { slug: 'x' }
const passing = { id: 'a', check: () => ({ ok: true }), write: () => ({ changed: false }) }
const failing = { id: 'b', check: () => ({ ok: false, detail: 'nope' }), write: () => ({ changed: true }) }

test('check mode aggregates emitter results', () => {
  const r = runEmitters(fakeDescriptor, { root: '/x', mode: 'check', emitters: [passing, failing] })
  assert.equal(r.mode, 'check')
  assert.equal(r.results.length, 2)
  assert.equal(r.results.find(x => x.id === 'b').ok, false)
})

test('write mode calls write on each emitter', () => {
  const r = runEmitters(fakeDescriptor, { root: '/x', mode: 'write', emitters: [passing, failing] })
  assert.equal(r.results.find(x => x.id === 'b').changed, true)
})

test('parseArgs with no args at all resolves the active brand and defaults to check', () => {
  const { slug, mode } = parseArgs([], { root: ROOT })
  assert.equal(slug, resolveActiveBrand({ root: ROOT }))
  assert.equal(mode, 'check')
})

test('parseArgs with only --write (no slug) resolves the active brand', () => {
  const { slug, mode } = parseArgs(['--write'], { root: ROOT })
  assert.equal(slug, resolveActiveBrand({ root: ROOT }))
  assert.equal(mode, 'write')
})

test('parseArgs with only --check (no slug) resolves the active brand', () => {
  const { slug, mode } = parseArgs(['--check'], { root: ROOT })
  assert.equal(slug, resolveActiveBrand({ root: ROOT }))
  assert.equal(mode, 'check')
})

test('parseArgs with an explicit slug uses it regardless of the active marker', () => {
  const { slug, mode } = parseArgs(['loop24', '--write'], { root: ROOT })
  assert.equal(slug, 'loop24')
  assert.equal(mode, 'write')
})

test('parseArgs is order-independent: flag before the slug positional', () => {
  const { slug, mode } = parseArgs(['--write', 'loop24'], { root: ROOT })
  assert.equal(slug, 'loop24')
  assert.equal(mode, 'write')
})

test('parseArgs honors OTTO_BRAND env override when no slug positional is given', () => {
  const prev = process.env.OTTO_BRAND
  process.env.OTTO_BRAND = 'loop24'
  try {
    const { slug } = parseArgs(['--check'], { root: ROOT })
    assert.equal(slug, 'loop24')
  } finally {
    if (prev === undefined) delete process.env.OTTO_BRAND
    else process.env.OTTO_BRAND = prev
  }
})

test('resolved active brand on the otto tree is otto and reports zero changes for --write (hermetic: direct emitter call, no CLI spawn)', () => {
  const slug = resolveActiveBrand({ root: ROOT })
  assert.equal(slug, 'otto')
  const descriptor = loadDescriptor(slug, { root: ROOT })
  const { results } = runEmitters(descriptor, { root: ROOT, mode: 'write', emitters: DEFAULT_EMITTERS })
  const changed = results.filter(r => r.changed)
  assert.deepEqual(changed, [], `expected no emitter to report changes on the otto tree, got: ${JSON.stringify(changed)}`)
})
