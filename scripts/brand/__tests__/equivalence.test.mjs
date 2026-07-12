// scripts/brand/__tests__/equivalence.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { runEmitters, DEFAULT_EMITTERS } from '../generate.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

test('EQUIVALENCE: generate(otto) --check passes for every default emitter', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const { results } = runEmitters(d, { root: ROOT, mode: 'check', emitters: DEFAULT_EMITTERS })
  const failed = results.filter(r => !r.ok)
  assert.deepEqual(failed, [], `failing emitters: ${JSON.stringify(failed)}`)
})
