import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { skinEmitter, hasBrandSkin, hasActiveSkin, renderSkin, extractSkinBlock, setActiveSkin, hasInitSkinDefault, setInitSkinDefault } from '../emitters/skin.mjs'

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

test('setActiveSkin sets the module default and is idempotent', () => {
  const src = '_active_skin_name: str = "default"\n'
  const once = setActiveSkin(src, 'otto')
  assert.equal(once, '_active_skin_name: str = "otto"\n')
  assert.equal(setActiveSkin(once, 'otto'), once)
})

test('write on the neutralized (no-otto, active=default) source restores the otto block immediately before "mono" AND sets _active_skin_name, byte-identical to the real tree', () => {
  // The real tree (ROOT) is the source of truth for what "restored" looks
  // like: extract its otto block as the expected rendered block, then build
  // a synthetic "upstream-neutral" source by removing that block and
  // reverting the active-skin default — mirroring exactly what the cutover
  // neutralization step does — and prove write() reconstructs the original
  // bytes exactly.
  const realSrc = fs.readFileSync(path.join(ROOT, 'hermes_cli/skin_engine.py'), 'utf8')
  const ottoBlock = extractSkinBlock(realSrc, 'otto')
  assert.ok(ottoBlock, 'precondition: otto block extractable from the real tree')
  assert.ok(hasActiveSkin(realSrc, 'otto'), 'precondition: real tree active skin is otto')

  const blockIdx = realSrc.indexOf(ottoBlock)
  const withoutBlock = realSrc.slice(0, blockIdx) + realSrc.slice(blockIdx + ottoBlock.length)
  const neutralSrc = withoutBlock.replace(
    '_active_skin_name: str = "otto"',
    '_active_skin_name: str = "default"'
  )
  assert.notEqual(neutralSrc, realSrc)
  assert.equal(hasBrandSkin(neutralSrc, 'otto'), false)
  assert.equal(hasActiveSkin(neutralSrc, 'otto'), false)
  // Otto block must sit immediately before "mono" in the real tree (the
  // structural fact the new insertion anchor relies on).
  assert.ok(realSrc.indexOf(ottoBlock) + ottoBlock.length === realSrc.indexOf('    "mono": {'))

  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'skinwrite-neutral-'))
  fs.mkdirSync(path.join(tmpRoot, 'hermes_cli'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'hermes_cli/skin_engine.py')
  fs.writeFileSync(tmpFile, neutralSrc)

  const d = loadDescriptor('otto', { root: ROOT })
  const r1 = skinEmitter.write(d, { root: tmpRoot })
  assert.equal(r1.changed, true)
  const restored = fs.readFileSync(tmpFile, 'utf8')
  assert.equal(restored, realSrc, 'write() must reconstruct the real tree byte-for-byte')

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('write is a no-op (changed:false) on the already-otto tree — combined idempotency on block AND active name', () => {
  const realSrc = fs.readFileSync(path.join(ROOT, 'hermes_cli/skin_engine.py'), 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'skinwrite-idem-'))
  fs.mkdirSync(path.join(tmpRoot, 'hermes_cli'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'hermes_cli/skin_engine.py')
  fs.writeFileSync(tmpFile, realSrc)

  const d = loadDescriptor('otto', { root: ROOT })
  const r1 = skinEmitter.write(d, { root: tmpRoot })
  assert.equal(r1.changed, false)
  assert.equal(fs.readFileSync(tmpFile, 'utf8'), realSrc)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('check passes on the otto form, fails on the neutral (no-otto, active=default) form', () => {
  const realSrc = fs.readFileSync(path.join(ROOT, 'hermes_cli/skin_engine.py'), 'utf8')
  const ottoBlock = extractSkinBlock(realSrc, 'otto')
  const blockIdx = realSrc.indexOf(ottoBlock)
  const withoutBlock = realSrc.slice(0, blockIdx) + realSrc.slice(blockIdx + ottoBlock.length)
  const neutralSrc = withoutBlock.replace(
    '_active_skin_name: str = "otto"',
    '_active_skin_name: str = "default"'
  )

  const d = loadDescriptor('otto', { root: ROOT })
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'skincheck-'))
  fs.mkdirSync(path.join(tmpRoot, 'hermes_cli'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'hermes_cli/skin_engine.py')

  fs.writeFileSync(tmpFile, realSrc)
  assert.equal(skinEmitter.check(d, { root: tmpRoot }).ok, true)

  fs.writeFileSync(tmpFile, neutralSrc)
  assert.equal(skinEmitter.check(d, { root: tmpRoot }).ok, false)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

// A minimal but structurally faithful mirror of the real skin_engine.py
// regions this task must own: the module docstring examples (~line 89-96,
// carrying `set_active_skin("ares")` / `set_active_skin("mytheme")`) and
// `init_skin_from_config` (~line 857-869, carrying the `display.get(...)`
// fallback, the `skin_name.strip()` variable call, and the `else:` fallback).
function buildInitSkinFixture(fallback) {
  return `def foo():
    """Example:

    from hermes_cli.skin_engine import get_active_skin, list_skins, set_active_skin

    set_active_skin("ares")               # Switch to built-in ares skin
    set_active_skin("mytheme")            # Switch to user skin from ~/.hermes/skins/
    """


def init_skin_from_config(config: dict) -> None:
    """Initialize the active skin from CLI config at startup.

    Call this once during CLI init with the loaded config dict.
    """
    display = config.get("display") or {}
    if not isinstance(display, dict):
        display = {}
    skin_name = display.get("skin", "${fallback}")
    if isinstance(skin_name, str) and skin_name.strip():
        set_active_skin(skin_name.strip())
    else:
        set_active_skin("${fallback}")
`
}

test('setInitSkinDefault on a NEUTRAL fixture (both literals "default") sets both to the slug', () => {
  const src = buildInitSkinFixture('default')
  const next = setInitSkinDefault(src, 'otto')
  assert.match(next, /display\.get\("skin", "otto"\)/)
  assert.match(next, /else:\n {8}set_active_skin\("otto"\)/)
  assert.doesNotMatch(next, /display\.get\("skin", "default"\)/)
  assert.doesNotMatch(next, /else:\n {8}set_active_skin\("default"\)/)
})

test('setInitSkinDefault is idempotent on the slug form', () => {
  const src = buildInitSkinFixture('otto')
  const once = setInitSkinDefault(src, 'otto')
  assert.equal(once, src)
  assert.equal(setInitSkinDefault(once, 'otto'), once)
})

test('setInitSkinDefault collision guard: docstring set_active_skin("ares") and the skin_name.strip() variable call are untouched', () => {
  const src = buildInitSkinFixture('default')
  const next = setInitSkinDefault(src, 'otto')
  assert.match(next, /set_active_skin\("ares"\)/)
  assert.match(next, /set_active_skin\("mytheme"\)/)
  assert.match(next, /set_active_skin\(skin_name\.strip\(\)\)/)
  // Byte-identical lines, not just "contains" — extract and compare exactly.
  const aresLine = src.split('\n').find((l) => l.includes('set_active_skin("ares")'))
  const stripLine = src.split('\n').find((l) => l.includes('skin_name.strip()'))
  const nextAresLine = next.split('\n').find((l) => l.includes('set_active_skin("ares")'))
  const nextStripLine = next.split('\n').find((l) => l.includes('skin_name.strip()'))
  assert.equal(nextAresLine, aresLine)
  assert.equal(nextStripLine, stripLine)
})

test('hasInitSkinDefault true on the otto form, false when a literal is "default"', () => {
  const ottoSrc = buildInitSkinFixture('otto')
  const neutralSrc = buildInitSkinFixture('default')
  assert.equal(hasInitSkinDefault(ottoSrc, 'otto'), true)
  assert.equal(hasInitSkinDefault(neutralSrc, 'otto'), false)
})

test('hasInitSkinDefault false when only one of the two literals matches the slug', () => {
  const mixedGet = buildInitSkinFixture('otto').replace(
    'display.get("skin", "otto")',
    'display.get("skin", "default")'
  )
  assert.equal(hasInitSkinDefault(mixedGet, 'otto'), false)

  const mixedElse = buildInitSkinFixture('otto').replace(
    'else:\n        set_active_skin("otto")',
    'else:\n        set_active_skin("default")'
  )
  assert.equal(hasInitSkinDefault(mixedElse, 'otto'), false)
})

test('check(otto) fails when init_skin_from_config fallback is still "default"', () => {
  const realSrc = fs.readFileSync(path.join(ROOT, 'hermes_cli/skin_engine.py'), 'utf8')
  const neutralized = realSrc.replace(
    'skin_name = display.get("skin", "otto")',
    'skin_name = display.get("skin", "default")'
  )
  assert.notEqual(neutralized, realSrc, 'precondition: replacement applied')

  const d = loadDescriptor('otto', { root: ROOT })
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'skincheck-initdefault-'))
  fs.mkdirSync(path.join(tmpRoot, 'hermes_cli'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'hermes_cli/skin_engine.py')

  fs.writeFileSync(tmpFile, neutralized)
  assert.equal(skinEmitter.check(d, { root: tmpRoot }).ok, false)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('write also applies setInitSkinDefault, factored into the changed decision, and is a no-op on the already-otto tree', () => {
  const realSrc = fs.readFileSync(path.join(ROOT, 'hermes_cli/skin_engine.py'), 'utf8')
  const neutralized = realSrc.replace(
    'skin_name = display.get("skin", "otto")',
    'skin_name = display.get("skin", "default")'
  ).replace(
    'else:\n        set_active_skin("otto")',
    'else:\n        set_active_skin("default")'
  )
  assert.notEqual(neutralized, realSrc)

  const d = loadDescriptor('otto', { root: ROOT })
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'skinwrite-initdefault-'))
  fs.mkdirSync(path.join(tmpRoot, 'hermes_cli'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'hermes_cli/skin_engine.py')

  fs.writeFileSync(tmpFile, neutralized)
  const r1 = skinEmitter.write(d, { root: tmpRoot })
  assert.equal(r1.changed, true)
  assert.equal(fs.readFileSync(tmpFile, 'utf8'), realSrc, 'write() must restore the init-skin fallbacks byte-for-byte')

  // Second write on the already-restored (otto) tree: no-op.
  const r2 = skinEmitter.write(d, { root: tmpRoot })
  assert.equal(r2.changed, false)
  assert.equal(fs.readFileSync(tmpFile, 'utf8'), realSrc)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
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
