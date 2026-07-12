// scripts/brand/__tests__/brand-config.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { renderBrandConfig, brandConfigEmitter } from '../emitters/brand-config.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')
const FILE = path.join(ROOT, 'apps/desktop/brand.config.json')

test('check(otto) passes against the real on-disk brand.config.json', () => {
  const result = brandConfigEmitter.check(loadDescriptor('otto', { root: ROOT }), { root: ROOT })
  assert.equal(result.ok, true, result.detail)
})

test('renderBrandConfig(loop24) sets name+rules, preserves protect unchanged', () => {
  const onDisk = fs.readFileSync(FILE, 'utf8')
  const out = renderBrandConfig(loadDescriptor('loop24', { root: ROOT }), onDisk)
  const j = JSON.parse(out)
  assert.equal(j.name, 'LOOP24')
  assert.deepEqual(j.rules[0], ['\\bHermes\\b', 'LOOP24'])
  assert.deepEqual(j.rules[1], ['\\bHERMES\\b', 'LOOP24'])
  assert.deepEqual(j.protect, ['X-Hermes-[A-Za-z0-9-]+', 'Hermes-Desktop'])
})

test('renderBrandConfig(otto) round-trips with no keys lost (deep-equal, not byte-equal)', () => {
  const onDisk = fs.readFileSync(FILE, 'utf8')
  const out = renderBrandConfig(loadDescriptor('otto', { root: ROOT }), onDisk)
  assert.deepEqual(JSON.parse(out), JSON.parse(onDisk))
})

test('renderBrandConfig JSON literally contains \\\\bHermes\\\\b (JSON-escaped regex)', () => {
  const onDisk = fs.readFileSync(FILE, 'utf8')
  const out = renderBrandConfig(loadDescriptor('loop24', { root: ROOT }), onDisk)
  const parsed = JSON.parse(out)
  assert.equal(parsed.rules[0][0], '\\bHermes\\b')
  assert.ok(out.includes('\\\\bHermes\\\\b'), 'serialized JSON text should contain the double-escaped regex source')
})
