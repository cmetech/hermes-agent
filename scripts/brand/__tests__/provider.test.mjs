// scripts/brand/__tests__/provider.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
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
