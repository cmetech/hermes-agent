// scripts/brand/__tests__/brand-config.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { renderBrandConfig, renderNeutralBrandConfig, brandConfigEmitter } from '../emitters/brand-config.mjs'

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

test('renderBrandConfig is idempotent under double-application for a quote-bearing displayName', () => {
  // Minimal single-line-rules fixture, independent of the real on-disk file.
  const fixture = [
    '{',
    '  "name": "Hermes",',
    '  "rules": [',
    '    ["\\\\bHermes\\\\b", "Hermes"],',
    '    ["\\\\bHERMES\\\\b", "HERMES"]',
    '  ]',
    '}',
    ''
  ].join('\n')

  const descriptor = { displayName: 'He said "hi"' }

  const once = renderBrandConfig(descriptor, fixture)
  const twice = renderBrandConfig(descriptor, once)

  assert.equal(twice, once, 'a second application (fed the first application\'s own output) must be a no-op')

  const parsedOnce = JSON.parse(once)
  const parsedTwice = JSON.parse(twice)
  assert.equal(parsedOnce.name, 'He said "hi"')
  assert.deepEqual(parsedOnce.rules[0], ['\\bHermes\\b', 'He said "hi"'])
  assert.deepEqual(parsedOnce.rules[1], ['\\bHERMES\\b', 'He said "hi"'])
  assert.equal(parsedTwice.name, 'He said "hi"')
  assert.deepEqual(parsedTwice.rules[0], ['\\bHermes\\b', 'He said "hi"'])
  assert.deepEqual(parsedTwice.rules[1], ['\\bHERMES\\b', 'He said "hi"'])
})

test('renderNeutralBrandConfig sets name to Hermes and each rule to its case-identity form', () => {
  const onDisk = fs.readFileSync(FILE, 'utf8')
  const out = renderNeutralBrandConfig(onDisk)
  const j = JSON.parse(out)
  assert.equal(j.name, 'Hermes')
  assert.deepEqual(j.rules[0], ['\\bHermes\\b', 'Hermes'])
  assert.deepEqual(j.rules[1], ['\\bHERMES\\b', 'HERMES'])
})

test('renderNeutralBrandConfig preserves protect and $note keys byte-for-byte', () => {
  const onDisk = fs.readFileSync(FILE, 'utf8')
  const out = renderNeutralBrandConfig(onDisk)
  const before = JSON.parse(onDisk)
  const after = JSON.parse(out)
  assert.deepEqual(after.protect, before.protect)
  assert.equal(after.$comment, before.$comment)
  assert.equal(after.$protectNote, before.$protectNote)
  assert.equal(after.$rulesNote, before.$rulesNote)
})

test('neutralize(otto) reverts the real brand.config.json to identity rules in a temp root', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const realSrc = fs.readFileSync(FILE, 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'brandconfigneutral-'))
  fs.mkdirSync(path.join(tmpRoot, 'apps/desktop'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'apps/desktop/brand.config.json')
  fs.writeFileSync(tmpFile, realSrc)

  const r = brandConfigEmitter.neutralize(d, { root: tmpRoot })
  assert.equal(r.changed, true)
  const after = JSON.parse(fs.readFileSync(tmpFile, 'utf8'))
  assert.equal(after.name, 'Hermes')
  assert.deepEqual(after.rules[0], ['\\bHermes\\b', 'Hermes'])
  assert.deepEqual(after.rules[1], ['\\bHERMES\\b', 'HERMES'])

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('neutralize dryRun:true reports changed but does not write the file', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const realSrc = fs.readFileSync(FILE, 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'brandconfigneutral-dry-'))
  fs.mkdirSync(path.join(tmpRoot, 'apps/desktop'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'apps/desktop/brand.config.json')
  fs.writeFileSync(tmpFile, realSrc)

  const r = brandConfigEmitter.neutralize(d, { root: tmpRoot, dryRun: true })
  assert.equal(r.changed, true)
  assert.equal(fs.readFileSync(tmpFile, 'utf8'), realSrc, 'dry run must not mutate the file')

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('ROUND-TRIP: neutralize then write(otto) reproduces the current on-disk brand.config.json byte-for-byte', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const realSrc = fs.readFileSync(FILE, 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'brandconfig-roundtrip-'))
  fs.mkdirSync(path.join(tmpRoot, 'apps/desktop'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'apps/desktop/brand.config.json')
  fs.writeFileSync(tmpFile, realSrc)

  brandConfigEmitter.neutralize(d, { root: tmpRoot })
  brandConfigEmitter.write(d, { root: tmpRoot })
  assert.equal(fs.readFileSync(tmpFile, 'utf8'), realSrc)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
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
