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

// The exact set of neutral-base on-disk files the 8 default emitters target, per the
// OTTO customization surface table. Used to build an isolated temp copy of
// just the relevant tree for full-suite neutralize/write round-trip tests
// without ever touching the real repo. The last 6 are the home emitter's
// resolver files (main.ts is shared with main-identity, already listed).
const EMITTER_FILES = [
  'pyproject.toml',
  'hermes_cli/skin_engine.py',
  'apps/desktop/package.json',
  'apps/desktop/electron/main.ts',
  'apps/desktop/brand.config.json',
  'apps/desktop/src/components/chat/intro.tsx',
  'hermes_constants.py',
  'apps/bootstrap-installer/src-tauri/src/paths.rs',
  'apps/desktop/scripts/test-desktop.mjs',
  'scripts/install.ps1',
  'scripts/install.sh',
  'scripts/lib/node-bootstrap.sh'
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

test('DEFAULT_EMITTERS has eight provider-owned branding emitters', () => {
  assert.equal(DEFAULT_EMITTERS.length, 8)
  assert.equal(DEFAULT_EMITTERS.some(e => e.id === 'auth-noauth'), false)
  assert.equal(EMITTER_FILES.includes('hermes_cli/auth.py'), false)
})

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

test('write(otto) is idempotent in an isolated temp copy — a second write reports zero changes (hermetic: never touches the real repo, branch-independent)', () => {
  // NOTE: this runs write against a TEMP copy, never the live ROOT. Running
  // write(otto) against ROOT is only a no-op on the branded otto tree; on the
  // neutral base tree it would brand ~13 real files. Copy first, then assert
  // idempotency of write in the isolated copy so this passes on any branch.
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'write-idempotent-'))
  copyEmitterTree(ROOT, tmpRoot)
  const descriptor = loadDescriptor('otto', { root: ROOT })

  runEmitters(descriptor, { root: tmpRoot, mode: 'write', emitters: DEFAULT_EMITTERS }) // brand the copy
  const { results } = runEmitters(descriptor, { root: tmpRoot, mode: 'write', emitters: DEFAULT_EMITTERS }) // second write
  const changed = results.filter(r => r.changed)
  assert.deepEqual(changed, [], `expected the second write to be a no-op, got: ${JSON.stringify(changed)}`)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
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

test('DRY-RUN-CLEAN: --neutralize (no --write) against the REAL neutral tree leaves it byte-for-byte unmodified', () => {
  const slug = resolveActiveBrand({ root: ROOT })
  const descriptor = loadDescriptor(slug, { root: ROOT })
  const before = Object.fromEntries(EMITTER_FILES.map(rel => [rel, fs.readFileSync(path.join(ROOT, rel), 'utf8')]))

  const { results } = runEmitters(descriptor, { root: ROOT, mode: 'neutralize', emitters: DEFAULT_EMITTERS, write: false })

  assert.equal(results.length, 8)

  for (const rel of EMITTER_FILES) {
    const after = fs.readFileSync(path.join(ROOT, rel), 'utf8')
    assert.equal(after, before[rel], `${rel} must be byte-identical after a --neutralize dry run`)
  }
})

test('write(otto) then neutralize --write applies all 8 emitters and round-trips neutral base byte-for-byte', () => {
  const before = Object.fromEntries(EMITTER_FILES.map(rel => [rel, fs.readFileSync(path.join(ROOT, rel), 'utf8')]))

  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'neutralize-write-all-'))
  copyEmitterTree(ROOT, tmpRoot)

  const descriptor = loadDescriptor('otto', { root: ROOT })

  runEmitters(descriptor, { root: tmpRoot, mode: 'write', emitters: DEFAULT_EMITTERS })
  assert.equal(fs.existsSync(path.join(tmpRoot, 'plugins/model-providers/otto')), true)
  const neutralizeResult = runEmitters(descriptor, { root: tmpRoot, mode: 'neutralize', emitters: DEFAULT_EMITTERS, write: true })
  assert.equal(neutralizeResult.results.length, 8)
  assert.ok(neutralizeResult.results.some(r => r.changed), 'expected --neutralize --write to actually change something')
  assert.equal(fs.existsSync(path.join(tmpRoot, 'plugins/model-providers/otto')), false)

  for (const rel of EMITTER_FILES) {
    const after = fs.readFileSync(path.join(tmpRoot, rel), 'utf8')
    assert.equal(after, before[rel], `${rel} must round-trip byte-for-byte through write(otto) -> neutralize`)
  }

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})
