import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { authNoauthEmitter, hasSlugInNoauth, addSlugToNoauth } from '../emitters/auth-noauth.mjs'

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
