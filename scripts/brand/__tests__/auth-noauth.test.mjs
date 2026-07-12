import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { authNoauthEmitter, hasSlugInNoauth, addSlugToNoauth, removeSlugFromNoauth } from '../emitters/auth-noauth.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

test('current auth.py already lists otto in the no-auth tuple', () => {
  const src = fs.readFileSync(path.join(ROOT, 'hermes_cli/auth.py'), 'utf8')
  assert.equal(hasSlugInNoauth(src, 'otto'), true)
})

test('check(otto) passes against the current tree', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  assert.equal(authNoauthEmitter.check(d, { root: ROOT }).ok, true)
})

test('addSlugToNoauth is idempotent and inserts loop24', () => {
  const line = 'if not api_key and provider_id in ("lmstudio", "otto"):'
  const once = addSlugToNoauth(line, 'loop24')
  assert.match(once, /"lmstudio", "otto", "loop24"/)
  assert.equal(addSlugToNoauth(once, 'loop24'), once)  // idempotent
})

test('addSlugToNoauth converts the upstream scalar form into the otto tuple, byte-for-byte', () => {
  const line = 'if not api_key and provider_id == "lmstudio":'
  const converted = addSlugToNoauth(line, 'otto')
  assert.equal(converted, 'if not api_key and provider_id in ("lmstudio", "otto"):')
})

test('addSlugToNoauth is idempotent once already in tuple form (converted-from-scalar path)', () => {
  const line = 'if not api_key and provider_id == "lmstudio":'
  const once = addSlugToNoauth(line, 'otto')
  const twice = addSlugToNoauth(once, 'otto')
  assert.equal(twice, once)
})

test('addSlugToNoauth converts only the no-auth scalar line, leaving an unrelated collision line untouched', () => {
  const src = [
    'if not api_key and provider_id == "lmstudio":',
    '    api_key = "not-needed"',
    '',
    'def _normalize_lmstudio_runtime_base_url(base_url):',
    '    if provider_id == "lmstudio":',
    '        base_url = _normalize(base_url)',
    '    return base_url',
  ].join('\n')

  const converted = addSlugToNoauth(src, 'otto')

  assert.match(converted, /if not api_key and provider_id in \("lmstudio", "otto"\):/)
  // The unrelated base-url normalization check must be left exactly as-is.
  assert.match(converted, /    if provider_id == "lmstudio":\n        base_url = _normalize\(base_url\)/)
  // Only one line should have been rewritten.
  assert.equal((converted.match(/provider_id ==/g) || []).length, 1)
  assert.equal((converted.match(/provider_id in \(/g) || []).length, 1)
})

test('check reports ok:false on the upstream scalar (neutral) form, ok:true on the otto tuple form', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'authwrite-'))
  fs.mkdirSync(path.join(tmpRoot, 'hermes_cli'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'hermes_cli/auth.py')

  fs.writeFileSync(tmpFile, 'if not api_key and provider_id == "lmstudio":\n')
  assert.equal(authNoauthEmitter.check(d, { root: tmpRoot }).ok, false)

  fs.writeFileSync(tmpFile, 'if not api_key and provider_id in ("lmstudio", "otto"):\n')
  assert.equal(authNoauthEmitter.check(d, { root: tmpRoot }).ok, true)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('removeSlugFromNoauth collapses the otto tuple back to the upstream scalar form, byte-for-byte', () => {
  const line = 'if not api_key and provider_id in ("lmstudio", "otto"):'
  const reverted = removeSlugFromNoauth(line, 'otto')
  assert.equal(reverted, 'if not api_key and provider_id == "lmstudio":')
})

test('removeSlugFromNoauth is a no-op when the slug is absent (already scalar)', () => {
  const line = 'if not api_key and provider_id == "lmstudio":'
  assert.equal(removeSlugFromNoauth(line, 'otto'), line)
})

test('removeSlugFromNoauth is a no-op when a different slug is present', () => {
  const line = 'if not api_key and provider_id in ("lmstudio", "otto"):'
  assert.equal(removeSlugFromNoauth(line, 'loop24'), line)
})

test('removeSlugFromNoauth only strips the given slug, leaving other tuple entries', () => {
  const line = 'if not api_key and provider_id in ("lmstudio", "otto", "loop24"):'
  const reverted = removeSlugFromNoauth(line, 'loop24')
  assert.equal(reverted, 'if not api_key and provider_id in ("lmstudio", "otto"):')
})

test('removeSlugFromNoauth leaves the unrelated base-url normalization collision line untouched', () => {
  const src = [
    'if not api_key and provider_id in ("lmstudio", "otto"):',
    '    api_key = "not-needed"',
    '',
    'def _normalize_lmstudio_runtime_base_url(base_url):',
    '    if provider_id == "lmstudio":',
    '        base_url = _normalize(base_url)',
    '    return base_url'
  ].join('\n')
  const reverted = removeSlugFromNoauth(src, 'otto')
  assert.match(reverted, /if not api_key and provider_id == "lmstudio":/)
  assert.match(reverted, /    if provider_id == "lmstudio":\n        base_url = _normalize\(base_url\)/)
})

test('neutralize(otto) reverts the real auth.py no-auth branch to the upstream scalar form in a temp root', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const realSrc = fs.readFileSync(path.join(ROOT, 'hermes_cli/auth.py'), 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'authneutral-'))
  fs.mkdirSync(path.join(tmpRoot, 'hermes_cli'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'hermes_cli/auth.py')
  fs.writeFileSync(tmpFile, realSrc)

  const r = authNoauthEmitter.neutralize(d, { root: tmpRoot })
  assert.equal(r.changed, true)
  const after = fs.readFileSync(tmpFile, 'utf8')
  assert.match(after, /not api_key and provider_id == "lmstudio":/)
  assert.doesNotMatch(after, /not api_key and provider_id in \(/)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('neutralize dryRun:true reports changed but does not write the file', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const realSrc = fs.readFileSync(path.join(ROOT, 'hermes_cli/auth.py'), 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'authneutral-dry-'))
  fs.mkdirSync(path.join(tmpRoot, 'hermes_cli'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'hermes_cli/auth.py')
  fs.writeFileSync(tmpFile, realSrc)

  const r = authNoauthEmitter.neutralize(d, { root: tmpRoot, dryRun: true })
  assert.equal(r.changed, true)
  assert.equal(fs.readFileSync(tmpFile, 'utf8'), realSrc, 'dry run must not mutate the file')

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('ROUND-TRIP: neutralize then write(otto) reproduces the current on-disk auth.py byte-for-byte', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const realSrc = fs.readFileSync(path.join(ROOT, 'hermes_cli/auth.py'), 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'auth-roundtrip-'))
  fs.mkdirSync(path.join(tmpRoot, 'hermes_cli'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'hermes_cli/auth.py')
  fs.writeFileSync(tmpFile, realSrc)

  authNoauthEmitter.neutralize(d, { root: tmpRoot })
  authNoauthEmitter.write(d, { root: tmpRoot })
  assert.equal(fs.readFileSync(tmpFile, 'utf8'), realSrc)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})
