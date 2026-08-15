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

test('renderProvider(otto) declares provider-owned no-auth gateway capabilities', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const rendered = renderProvider(d)
  const source = rendered['__init__.py']
  assert.match(source, /supports_unauthenticated=True,/)
  assert.match(source, /model_capabilities_path="model-capabilities",/)
  assert.match(source, /otto_tool_contract_version="v1",/)
  assert.doesNotMatch(source, /auth\.py substitutes/)
  assert.doesNotMatch(source, /hardcoded tuple/)
})

test('providerEmitter.write(otto) creates files that pass check', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'provider-check-'))
  providerEmitter.write(d, { root: tmpRoot })
  assert.equal(providerEmitter.check(d, { root: tmpRoot }).ok, true)
  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('renderProvider(loop24) swaps identity but keeps the OTTO gateway', () => {
  const d = loadDescriptor('loop24', { root: ROOT })
  const out = renderProvider(d)['__init__.py']
  assert.match(out, /name="loop24"/)
  assert.match(out, /display_name="LOOP24 Gateway"/)
  assert.match(out, /OTTO_API_KEY/)              // gateway creds unchanged
  assert.match(out, /http:\/\/127\.0\.0\.1:18080\/v1/)
  assert.match(out, /otto_tool_contract_version="v1",/)
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

test('ROUND-TRIP: write then neutralize restores the neutral provider-absent state', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'provider-roundtrip-'))
  const dir = path.join(tmpRoot, 'plugins/model-providers/otto')

  providerEmitter.write(d, { root: tmpRoot })
  assert.equal(providerEmitter.check(d, { root: tmpRoot }).ok, true)
  providerEmitter.neutralize(d, { root: tmpRoot })
  assert.equal(fs.existsSync(dir), false)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})
