import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { skinEmitter, hasBrandSkin, hasActiveSkin, renderSkin, extractSkinBlock } from '../emitters/skin.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

test('current skin_engine has the otto skin and active default', () => {
  const src = fs.readFileSync(path.join(ROOT, 'hermes_cli/skin_engine.py'), 'utf8')
  assert.equal(hasBrandSkin(src, 'otto'), true)
  assert.equal(hasActiveSkin(src, 'otto'), true)
})

test('renderSkin(otto) matches the current on-disk otto block byte-for-byte', () => {
  const src = fs.readFileSync(path.join(ROOT, 'hermes_cli/skin_engine.py'), 'utf8')
  const extracted = extractSkinBlock(src, 'otto')
  const d = loadDescriptor('otto', { root: ROOT })
  assert.equal(renderSkin(d), extracted)
})

test('check(otto) passes', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  assert.equal(skinEmitter.check(d, { root: ROOT }).ok, true)
})

test('renderSkin(loop24) contains brand-derived labels and placeholder art', () => {
  const d = loadDescriptor('loop24', { root: ROOT })
  const rendered = renderSkin(d)
  assert.match(rendered, /"name": "loop24"/)
  assert.match(rendered, /"agent_name": "LOOP24"/)
  assert.match(rendered, /Welcome to LOOP24!/)
  assert.ok(rendered.includes(d.cli.bannerLogo))
  assert.ok(rendered.includes(d.cli.bannerHero))
})

test('renderSkin(otto) only emits the otto block, not other skins', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const rendered = renderSkin(d)
  assert.ok(!rendered.includes('"mono"'))
  assert.ok(!rendered.includes('"default"'))
})
