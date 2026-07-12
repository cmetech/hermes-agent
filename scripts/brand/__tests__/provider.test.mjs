// scripts/brand/__tests__/provider.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { providerEmitter, renderProvider } from '../emitters/provider.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

test('renderProvider(otto) reproduces the current provider files byte-for-byte', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const rendered = renderProvider(d)
  const initOnDisk = fs.readFileSync(path.join(ROOT, 'plugins/model-providers/otto/__init__.py'), 'utf8')
  const yamlOnDisk = fs.readFileSync(path.join(ROOT, 'plugins/model-providers/otto/plugin.yaml'), 'utf8')
  assert.equal(rendered['__init__.py'], initOnDisk)
  assert.equal(rendered['plugin.yaml'], yamlOnDisk)
})

test('providerEmitter.check(otto) passes against the current tree', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  assert.equal(providerEmitter.check(d, { root: ROOT }).ok, true)
})

test('renderProvider(loop24) swaps identity but keeps the OTTO gateway', () => {
  const d = loadDescriptor('loop24', { root: ROOT })
  const out = renderProvider(d)['__init__.py']
  assert.match(out, /name="loop24"/)
  assert.match(out, /display_name="LOOP24 Gateway"/)
  assert.match(out, /OTTO_API_KEY/)              // gateway creds unchanged
  assert.match(out, /http:\/\/127\.0\.0\.1:18080\/v1/)
})

test('neutralize removes plugins/model-providers/otto/ entirely', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'providerneutral-'))
  const dir = path.join(tmpRoot, 'plugins/model-providers/otto')
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, '__init__.py'), 'x')
  fs.writeFileSync(path.join(dir, 'plugin.yaml'), 'y')

  const r = providerEmitter.neutralize(d, { root: tmpRoot })
  assert.equal(r.changed, true)
  assert.equal(fs.existsSync(dir), false)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('neutralize is a guarded no-op when the directory is already absent', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'providerneutral-absent-'))
  const r = providerEmitter.neutralize(d, { root: tmpRoot })
  assert.equal(r.changed, false)
  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('neutralize with dryRun:true reports changed but does not delete the directory', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'providerneutral-dry-'))
  const dir = path.join(tmpRoot, 'plugins/model-providers/otto')
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, '__init__.py'), 'x')

  const r = providerEmitter.neutralize(d, { root: tmpRoot, dryRun: true })
  assert.equal(r.changed, true)
  assert.equal(fs.existsSync(dir), true, 'dry run must not delete anything')

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('ROUND-TRIP: neutralize then write(otto) reproduces the current on-disk provider files byte-for-byte', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const initOnDisk = fs.readFileSync(path.join(ROOT, 'plugins/model-providers/otto/__init__.py'), 'utf8')
  const yamlOnDisk = fs.readFileSync(path.join(ROOT, 'plugins/model-providers/otto/plugin.yaml'), 'utf8')

  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'provider-roundtrip-'))
  const dir = path.join(tmpRoot, 'plugins/model-providers/otto')
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, '__init__.py'), initOnDisk)
  fs.writeFileSync(path.join(dir, 'plugin.yaml'), yamlOnDisk)

  providerEmitter.neutralize(d, { root: tmpRoot })
  assert.equal(fs.existsSync(dir), false, 'precondition: neutralize removed the dir')

  providerEmitter.write(d, { root: tmpRoot })
  assert.equal(fs.readFileSync(path.join(dir, '__init__.py'), 'utf8'), initOnDisk)
  assert.equal(fs.readFileSync(path.join(dir, 'plugin.yaml'), 'utf8'), yamlOnDisk)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})
