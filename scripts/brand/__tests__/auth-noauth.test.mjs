import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
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
