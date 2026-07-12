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

test('renderBrandConfig(otto) is a byte-for-byte no-op on the current on-disk file', () => {
  const onDisk = fs.readFileSync(FILE, 'utf8')
  const out = renderBrandConfig(loadDescriptor('otto', { root: ROOT }), onDisk)
  assert.equal(out, onDisk, 'write on an already-otto tree must not reflow formatting')
})

test('brandConfigEmitter.write(otto) reports unchanged and does not touch the file', () => {
  const before = fs.readFileSync(FILE, 'utf8')
  const result = brandConfigEmitter.write(loadDescriptor('otto', { root: ROOT }), { root: ROOT })
  assert.equal(result.changed, false)
  assert.equal(fs.readFileSync(FILE, 'utf8'), before)
})

test('renderBrandConfig preserves single-line rules formatting (does not reflow to multi-line)', () => {
  const onDisk = fs.readFileSync(FILE, 'utf8')
  const out = renderBrandConfig(loadDescriptor('loop24', { root: ROOT }), onDisk)
  assert.ok(out.includes('["\\\\bHermes\\\\b", "LOOP24"],'), 'rules pair should stay on a single line')
  assert.ok(out.includes('["\\\\bHERMES\\\\b", "LOOP24"]'), 'rules pair should stay on a single line')
})

test('renderBrandConfig restores OTTO values in place on a neutralized-but-single-line fixture, byte-identical apart from the replaced values', () => {
  const onDisk = fs.readFileSync(FILE, 'utf8')
  // Simulate a hypothetically-neutralized (upstream/Hermes) source: same
  // formatting, but name + rule replacements reverted to "Hermes"/"HERMES".
  const neutralized = onDisk
    .replace('"name": "OTTO"', '"name": "Hermes"')
    .replace('["\\\\bHermes\\\\b", "OTTO"]', '["\\\\bHermes\\\\b", "Hermes"]')
    .replace('["\\\\bHERMES\\\\b", "OTTO"]', '["\\\\bHERMES\\\\b", "HERMES"]')
  assert.notEqual(neutralized, onDisk, 'fixture setup sanity check')

  const restored = renderBrandConfig(loadDescriptor('otto', { root: ROOT }), neutralized)
  assert.equal(restored, onDisk, 'regenerating from a neutralized-but-single-line source should exactly reproduce the OTTO file')
})

test('renderBrandConfig preserves protect and $note keys byte-for-byte', () => {
  const onDisk = fs.readFileSync(FILE, 'utf8')
  const out = renderBrandConfig(loadDescriptor('loop24', { root: ROOT }), onDisk)
  const beforeObj = JSON.parse(onDisk)
  const afterObj = JSON.parse(out)
  assert.deepEqual(afterObj.protect, beforeObj.protect)
  assert.equal(afterObj.$comment, beforeObj.$comment)
  assert.equal(afterObj.$protectNote, beforeObj.$protectNote)
  assert.equal(afterObj.$rulesNote, beforeObj.$rulesNote)
})
