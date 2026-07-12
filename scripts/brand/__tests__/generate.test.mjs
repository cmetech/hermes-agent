// scripts/brand/__tests__/generate.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { runEmitters } from '../generate.mjs'

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
