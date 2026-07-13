import { test } from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { brandJsonPayload, BRAND_JSON_SCHEMA_VERSION } from '../brand-json.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

// MUST match CANONICAL_KEYS in tests/hermes_cli/test_brand_runtime.py
const CANONICAL_KEYS = [
  'schemaVersion', 'slug', 'displayName', 'appId', 'scheme',
  'schemes', 'homeDir', 'releasesRepo', 'updateCommand', 'gateway',
]

test('brandJsonPayload otto: canonical keys + values', () => {
  const p = brandJsonPayload(loadDescriptor('otto', { root: ROOT }))
  assert.deepEqual(Object.keys(p), CANONICAL_KEYS)
  assert.equal(p.schemaVersion, BRAND_JSON_SCHEMA_VERSION)
  assert.equal(p.slug, 'otto')
  assert.equal(p.displayName, 'OTTO')
  assert.equal(p.appId, 'io.cmetech.otto')
  assert.deepEqual(p.schemes, ['otto', 'hermes'])
  assert.equal(p.homeDir, '.otto')
  assert.equal(p.releasesRepo, 'cmetech/otto')
})

test('brandJsonPayload loop24: scheme + releasesRepo', () => {
  const p = brandJsonPayload(loadDescriptor('loop24', { root: ROOT }))
  assert.equal(p.slug, 'loop24')
  assert.deepEqual(p.schemes, ['loop24', 'hermes'])
  assert.equal(p.releasesRepo, 'cmetech/loop24')
})
