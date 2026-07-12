// scripts/brand/__tests__/generate.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { runEmitters, parseArgs, DEFAULT_EMITTERS } from '../generate.mjs'
import { resolveActiveBrand } from '../active.mjs'
import { loadDescriptor } from '../descriptor.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

// The exact set of on-disk files/dirs the 8 default emitters target, per the
// OTTO customization surface table. Used to build an isolated temp copy of
// just the relevant tree for full-suite neutralize/write round-trip tests
// without ever touching the real repo.
const EMITTER_FILES = [
  'plugins/model-providers/otto/__init__.py',
  'plugins/model-providers/otto/plugin.yaml',
  'hermes_cli/auth.py',
  'pyproject.toml',
  'hermes_cli/skin_engine.py',
  'apps/desktop/package.json',
  'apps/desktop/electron/main.ts',
  'apps/desktop/brand.config.json',
  'apps/desktop/src/components/chat/intro.tsx'
]

function copyEmitterTree(srcRoot, dstRoot) {
  for (const rel of EMITTER_FILES) {
    const src = path.join(srcRoot, rel)
    const dst = path.join(dstRoot, rel)
    fs.mkdirSync(path.dirname(dst), { recursive: true })
    fs.copyFileSync(src, dst)
  }
}

const fakeDescriptor = { slug: 'x' }
const passing = { id: 'a', check: () => ({ ok: true }), write: () => ({ changed: false }) }
const failing = { id: 'b', check: () => ({ ok: false, detail: 'nope' }), write: () => ({ changed: true }) }

test('check mode aggregates emitter results', () => {
  const r = runEmitters(fakeDescriptor, { root: '/x', mode: 'check', emitters: [passing, failing] })
  assert.equal(r.mode, 'check')
  assert.equal(r.results.length, 2)
  assert.equal(r.results.find(x => x.id === 'b').ok, false)
})

test('write mode calls write on each emitter', () => {
  const r = runEmitters(fakeDescriptor, { root: '/x', mode: 'write', emitters: [passing, failing] })
  assert.equal(r.results.find(x => x.id === 'b').changed, true)
})

test('parseArgs with no args at all resolves the active brand and defaults to check', () => {
  const { slug, mode } = parseArgs([], { root: ROOT })
  assert.equal(slug, resolveActiveBrand({ root: ROOT }))
  assert.equal(mode, 'check')
})

test('parseArgs with only --write (no slug) resolves the active brand', () => {
  const { slug, mode } = parseArgs(['--write'], { root: ROOT })
  assert.equal(slug, resolveActiveBrand({ root: ROOT }))
  assert.equal(mode, 'write')
})

test('parseArgs with only --check (no slug) resolves the active brand', () => {
  const { slug, mode } = parseArgs(['--check'], { root: ROOT })
  assert.equal(slug, resolveActiveBrand({ root: ROOT }))
  assert.equal(mode, 'check')
})

test('parseArgs with an explicit slug uses it regardless of the active marker', () => {
  const { slug, mode } = parseArgs(['loop24', '--write'], { root: ROOT })
  assert.equal(slug, 'loop24')
  assert.equal(mode, 'write')
})

test('parseArgs is order-independent: flag before the slug positional', () => {
  const { slug, mode } = parseArgs(['--write', 'loop24'], { root: ROOT })
  assert.equal(slug, 'loop24')
  assert.equal(mode, 'write')
})

test('parseArgs honors OTTO_BRAND env override when no slug positional is given', () => {
  const prev = process.env.OTTO_BRAND
  process.env.OTTO_BRAND = 'loop24'
  try {
    const { slug } = parseArgs(['--check'], { root: ROOT })
    assert.equal(slug, 'loop24')
  } finally {
    if (prev === undefined) delete process.env.OTTO_BRAND
    else process.env.OTTO_BRAND = prev
  }
})

test('resolved active brand on the otto tree is otto and reports zero changes for --write (hermetic: direct emitter call, no CLI spawn)', () => {
  const slug = resolveActiveBrand({ root: ROOT })
  assert.equal(slug, 'otto')
  const descriptor = loadDescriptor(slug, { root: ROOT })
  const { results } = runEmitters(descriptor, { root: ROOT, mode: 'write', emitters: DEFAULT_EMITTERS })
  const changed = results.filter(r => r.changed)
  assert.deepEqual(changed, [], `expected no emitter to report changes on the otto tree, got: ${JSON.stringify(changed)}`)
})

test('parseArgs: --neutralize (no --write) resolves mode "neutralize" and write:false', () => {
  const { slug, mode, write } = parseArgs(['--neutralize'], { root: ROOT })
  assert.equal(slug, resolveActiveBrand({ root: ROOT }))
  assert.equal(mode, 'neutralize')
  assert.equal(write, false)
})

test('parseArgs: --neutralize --write resolves mode "neutralize" and write:true', () => {
  const { mode, write } = parseArgs(['--neutralize', '--write'], { root: ROOT })
  assert.equal(mode, 'neutralize')
  assert.equal(write, true)
})

test('runEmitters mode:neutralize without write:true is a dry run — reports changed but does not call fs mutation (via a fake emitter spy)', () => {
  let neutralizeCalledWith = null
  const spy = {
    id: 'spy',
    neutralize: (d, opts) => {
      neutralizeCalledWith = opts
      return { changed: true, detail: 'would remove x' }
    }
  }
  const { results } = runEmitters(fakeDescriptor, { root: '/x', mode: 'neutralize', emitters: [spy] })
  assert.equal(results[0].changed, true)
  assert.equal(neutralizeCalledWith.dryRun, true, 'dry run must be signaled to the emitter via dryRun:true')
})

test('runEmitters mode:neutralize with write:true passes dryRun:false through to each emitter', () => {
  let neutralizeCalledWith = null
  const spy = {
    id: 'spy',
    neutralize: (d, opts) => {
      neutralizeCalledWith = opts
      return { changed: true, detail: 'removed x' }
    }
  }
  runEmitters(fakeDescriptor, { root: '/x', mode: 'neutralize', emitters: [spy], write: true })
  assert.equal(neutralizeCalledWith.dryRun, false)
})

test('DRY-RUN-CLEAN: --neutralize (no --write) against the REAL tree leaves it byte-for-byte unmodified', () => {
  const slug = resolveActiveBrand({ root: ROOT })
  const descriptor = loadDescriptor(slug, { root: ROOT })
  const before = Object.fromEntries(EMITTER_FILES.map(rel => [rel, fs.readFileSync(path.join(ROOT, rel), 'utf8')]))

  const { results } = runEmitters(descriptor, { root: ROOT, mode: 'neutralize', emitters: DEFAULT_EMITTERS, write: false })

  // Every emitter that has something to neutralize on the real (branded)
  // tree should report changed:true (it's a plan, not a no-op) — but must
  // not have touched the filesystem.
  assert.ok(results.some(r => r.changed), 'expected the dry-run plan to report at least one intended change')

  for (const rel of EMITTER_FILES) {
    const after = fs.readFileSync(path.join(ROOT, rel), 'utf8')
    assert.equal(after, before[rel], `${rel} must be byte-identical after a --neutralize dry run`)
  }
  // The provider dir itself must still exist (dry run never deletes).
  assert.ok(fs.existsSync(path.join(ROOT, 'plugins/model-providers/otto')), 'provider dir must survive a dry run')
})

test('CLI --neutralize --write in an isolated temp copy of the tree applies all 8 emitters, and a subsequent write(otto) round-trips every file back byte-for-byte', () => {
  const before = Object.fromEntries(EMITTER_FILES.map(rel => [rel, fs.readFileSync(path.join(ROOT, rel), 'utf8')]))

  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'neutralize-write-all-'))
  copyEmitterTree(ROOT, tmpRoot)

  const descriptor = loadDescriptor('otto', { root: ROOT })

  const neutralizeResult = runEmitters(descriptor, { root: tmpRoot, mode: 'neutralize', emitters: DEFAULT_EMITTERS, write: true })
  assert.ok(neutralizeResult.results.some(r => r.changed), 'expected --neutralize --write to actually change something')
  // provider dir removed
  assert.equal(fs.existsSync(path.join(tmpRoot, 'plugins/model-providers/otto')), false)

  // Regenerate with the otto descriptor and confirm every emitter-covered
  // file is restored byte-for-byte to the original branded content.
  runEmitters(descriptor, { root: tmpRoot, mode: 'write', emitters: DEFAULT_EMITTERS })

  for (const rel of EMITTER_FILES) {
    const after = fs.readFileSync(path.join(tmpRoot, rel), 'utf8')
    assert.equal(after, before[rel], `${rel} must round-trip byte-for-byte through neutralize -> write(otto)`)
  }

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})
