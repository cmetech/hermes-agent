import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { skinEmitter, hasBrandSkin, hasActiveSkin, renderSkin, extractSkinBlock } from '../emitters/skin.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

// Loaded directly (not via loadDescriptor) — its slug starts with `_` and
// deliberately fails descriptor.mjs's SLUG_RE, which is what keeps it out of
// real-brand enumeration. It already carries the full descriptor shape
// (including a `$`-bearing displayName), so a plain JSON.parse is a valid
// stand-in for a loaded descriptor in these tests.
const FIXTURE_QUOTE = JSON.parse(fs.readFileSync(path.join(ROOT, 'brands/_fixture-quote.json'), 'utf8'))

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

test('write splices a new brand block, is idempotent, and leaves existing blocks byte-identical', () => {
  // Build a temp repo root with a real copy of skin_engine.py so write()'s
  // path.join(root, 'hermes_cli/skin_engine.py') resolves. Load the loop24
  // descriptor from the real ROOT (its brands/loop24.json) but target the temp.
  const realSrc = fs.readFileSync(path.join(ROOT, 'hermes_cli/skin_engine.py'), 'utf8')
  const ottoBefore = extractSkinBlock(realSrc, 'otto')
  const defaultBefore = extractSkinBlock(realSrc, 'default')
  assert.ok(ottoBefore, 'precondition: otto block extractable')
  assert.ok(defaultBefore, 'precondition: default block extractable')

  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'skinwrite-'))
  fs.mkdirSync(path.join(tmpRoot, 'hermes_cli'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'hermes_cli/skin_engine.py')
  fs.writeFileSync(tmpFile, realSrc)

  const d = loadDescriptor('loop24', { root: ROOT })

  // First write: splices a new loop24 block in.
  const r1 = skinEmitter.write(d, { root: tmpRoot })
  assert.equal(r1.changed, true)
  const after1 = fs.readFileSync(tmpFile, 'utf8')
  const loop1 = extractSkinBlock(after1, 'loop24')
  assert.ok(loop1, 'loop24 block spliced in')
  assert.equal(loop1, renderSkin(d))
  // Pre-existing blocks untouched.
  assert.equal(extractSkinBlock(after1, 'otto'), ottoBefore)
  assert.equal(extractSkinBlock(after1, 'default'), defaultBefore)

  // Second write: idempotent — no change, no duplicate block.
  const r2 = skinEmitter.write(d, { root: tmpRoot })
  assert.equal(r2.changed, false)
  const after2 = fs.readFileSync(tmpFile, 'utf8')
  assert.equal(after2, after1)
  // Exactly one loop24 block header exists (no duplication).
  const occurrences = after2.split('\n    "loop24": {').length - 1
  assert.equal(occurrences, 1)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('renderSkin substitutes a $-bearing displayName verbatim into every label (split/join, not regex-replace $-patterns)', () => {
  const rendered = renderSkin(FIXTURE_QUOTE)
  assert.ok(FIXTURE_QUOTE.displayName.includes('$'), 'precondition: fixture displayName is $-bearing')
  assert.match(rendered, /"agent_name": "FIX\$TURE"/)
  assert.match(rendered, /Welcome to FIX\$TURE! Type your message/)
  assert.match(rendered, / ⚕ FIX\$TURE /)
  // Guard against String.prototype.replace-style $-pattern corruption (e.g. a
  // literal `$&`/`$'` displayName would be mishandled by a naive .replace()
  // call): every label occurrence must carry the `$` immediately, not have
  // it swallowed or the surrounding text mangled.
  // 4 occurrences: "description", "agent_name", "welcome", "response_label".
  assert.equal((rendered.match(/FIX\$TURE/g) || []).length, 4)
  assert.ok(!rendered.includes('FIXTURE'), 'the literal $ must never be dropped from the displayName')
})

test('renderSkin($-bearing fixture) round-trips through write/extract on a temp skin_engine.py', () => {
  const realSrc = fs.readFileSync(path.join(ROOT, 'hermes_cli/skin_engine.py'), 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'skinwrite-quote-'))
  fs.mkdirSync(path.join(tmpRoot, 'hermes_cli'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'hermes_cli/skin_engine.py')
  fs.writeFileSync(tmpFile, realSrc)

  const r1 = skinEmitter.write(FIXTURE_QUOTE, { root: tmpRoot })
  assert.equal(r1.changed, true)
  const after = fs.readFileSync(tmpFile, 'utf8')
  const spliced = extractSkinBlock(after, FIXTURE_QUOTE.slug)
  assert.ok(spliced, `${FIXTURE_QUOTE.slug} block spliced in`)
  assert.equal(spliced, renderSkin(FIXTURE_QUOTE))

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})
