// scripts/brand/__tests__/descriptor.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { loadDescriptor } from '../descriptor.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

test('otto descriptor loads with expected identity', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  assert.equal(d.slug, 'otto')
  assert.equal(d.displayName, 'OTTO')
  assert.equal(d.scheme, 'otto')
  assert.equal(d.gateway, 'otto')
  assert.ok(Array.isArray(d.curation.skills.exclude))
})

test('defaults derive displayName and wordmark from slug', () => {
  const d = loadDescriptor('loop24', { root: ROOT })
  assert.equal(d.displayName, 'LOOP24')
  assert.equal(typeof d.wordmark, 'string')
  assert.equal(d.curation.tools.disabledByDefault instanceof Array, true)
})

test('rejects an invalid slug', () => {
  assert.throws(() => loadDescriptor('Bad Slug', { root: ROOT }), /slug/i)
})

test('descriptor surfaces the cli banner art', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  assert.match(d.cli.bannerLogo, /█/) // otto ASCII art present
  assert.equal(typeof d.cli.bannerHero, 'string')
})

test('withDefaults: curation.channels.allow defaults to [] and passes through', () => {
  // otto.json carries the 11-id messaging-channel allowlist.
  const otto = loadDescriptor('otto', { root: ROOT })
  assert.equal(otto.curation.channels.allow.length, 11)
  assert.ok(otto.curation.channels.allow.includes('telegram'))
  assert.ok(otto.curation.channels.allow.includes('msgraph_webhook'))

  // A brand descriptor that omits `curation.channels` entirely (loop24 has no
  // curation block at all) must still normalize to allow: [].
  const loop24 = loadDescriptor('loop24', { root: ROOT })
  assert.deepEqual(loop24.curation.channels, { allow: [] })
})
