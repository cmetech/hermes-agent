import { test } from 'node:test'
import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import {
  mainIdentityEmitter,
  hasAppName,
  setAppName,
  hasAppId,
  setAppId,
  hasScheme,
  setScheme
} from '../emitters/main-identity.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

test('check(otto) passes against the current main.ts tree', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  assert.equal(mainIdentityEmitter.check(d, { root: ROOT }).ok, true)
})

test('setAppName replaces the quoted value and is idempotent', () => {
  const line = "const APP_NAME = process.env.HERMES_DESKTOP_APP_NAME || 'OTTO'"
  const once = setAppName(line, 'LOOP24')
  assert.match(once, /\|\| 'LOOP24'/)
  assert.equal(hasAppName(once, 'LOOP24'), true)
  assert.equal(setAppName(once, 'LOOP24'), once) // idempotent
})

test('setScheme sets OTTO_PROTOCOL value only, leaves HERMES_PROTOCOL untouched', () => {
  const src = "const OTTO_PROTOCOL = 'otto'\nconst HERMES_PROTOCOL = 'hermes'"
  const once = setScheme(src, 'loop24')
  assert.match(once, /OTTO_PROTOCOL = 'loop24'/)
  assert.match(once, /HERMES_PROTOCOL = 'hermes'/)
  assert.equal(hasScheme(once, 'loop24'), true)
  assert.equal(setScheme(once, 'loop24'), once) // idempotent
})

test('setAppId replaces app id and is idempotent', () => {
  const line = "app.setAppUserModelId('io.cmetech.otto')"
  const once = setAppId(line, 'io.cmetech.loop24')
  assert.match(once, /io\.cmetech\.loop24/)
  assert.equal(hasAppId(once, 'io.cmetech.loop24'), true)
  assert.equal(setAppId(once, 'io.cmetech.loop24'), once) // idempotent
})
