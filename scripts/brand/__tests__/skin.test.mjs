import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { skinEmitter, hasBrandSkin, hasActiveSkin } from '../emitters/skin.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

test('current skin_engine has the otto skin and active default', () => {
  const src = fs.readFileSync(path.join(ROOT, 'hermes_cli/skin_engine.py'), 'utf8')
  assert.equal(hasBrandSkin(src, 'otto'), true)
  assert.equal(hasActiveSkin(src, 'otto'), true)
})

test('check(otto) passes', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  assert.equal(skinEmitter.check(d, { root: ROOT }).ok, true)
})

test('write throws in Plan 1 (deferred to Plan 2)', () => {
  const d = loadDescriptor('loop24', { root: ROOT })
  assert.throws(() => skinEmitter.write(d, { root: ROOT }), /Plan 2/)
})
