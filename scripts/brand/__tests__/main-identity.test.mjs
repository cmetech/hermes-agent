import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
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

test('check() fails against the real main.ts when the descriptor identity is wrong', () => {
  const badDescriptor = {
    displayName: 'NOT-A-REAL-BRAND',
    appId: 'io.cmetech.not-a-real-brand',
    scheme: 'not-a-real-brand'
  }
  const result = mainIdentityEmitter.check(badDescriptor, { root: ROOT })
  assert.equal(result.ok, false)
  assert.match(result.detail, /NOT-A-REAL-BRAND/)
})

test('setAppId replaces app id and is idempotent', () => {
  const line = "app.setAppUserModelId('io.cmetech.otto')"
  const once = setAppId(line, 'io.cmetech.loop24')
  assert.match(once, /io\.cmetech\.loop24/)
  assert.equal(hasAppId(once, 'io.cmetech.loop24'), true)
  assert.equal(setAppId(once, 'io.cmetech.loop24'), once) // idempotent
})

test('neutralize(otto) reverts the real main.ts anchors to upstream Hermes values in a temp root', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const realSrc = fs.readFileSync(path.join(ROOT, 'apps/desktop/electron/main.ts'), 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mainidneutral-'))
  fs.mkdirSync(path.join(tmpRoot, 'apps/desktop/electron'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'apps/desktop/electron/main.ts')
  fs.writeFileSync(tmpFile, realSrc)

  const r = mainIdentityEmitter.neutralize(d, { root: tmpRoot })
  assert.equal(r.changed, true)
  const after = fs.readFileSync(tmpFile, 'utf8')
  assert.equal(hasAppName(after, 'Hermes'), true)
  assert.equal(hasAppId(after, 'com.nousresearch.hermes'), true)
  assert.equal(hasScheme(after, 'hermes'), true)
  // HERMES_PROTOCOL / DEEP_LINK_PROTOCOLS must survive untouched.
  assert.match(after, /const HERMES_PROTOCOL = 'hermes'/)
  assert.match(after, /const DEEP_LINK_PROTOCOLS = \[OTTO_PROTOCOL, HERMES_PROTOCOL\]/)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('neutralize dryRun:true reports changed but does not write the file', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const realSrc = fs.readFileSync(path.join(ROOT, 'apps/desktop/electron/main.ts'), 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mainidneutral-dry-'))
  fs.mkdirSync(path.join(tmpRoot, 'apps/desktop/electron'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'apps/desktop/electron/main.ts')
  fs.writeFileSync(tmpFile, realSrc)

  const r = mainIdentityEmitter.neutralize(d, { root: tmpRoot, dryRun: true })
  assert.equal(r.changed, true)
  assert.equal(fs.readFileSync(tmpFile, 'utf8'), realSrc, 'dry run must not mutate the file')

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('ROUND-TRIP: neutralize then write(otto) reproduces the current on-disk main.ts byte-for-byte', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const realSrc = fs.readFileSync(path.join(ROOT, 'apps/desktop/electron/main.ts'), 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'mainid-roundtrip-'))
  fs.mkdirSync(path.join(tmpRoot, 'apps/desktop/electron'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'apps/desktop/electron/main.ts')
  fs.writeFileSync(tmpFile, realSrc)

  mainIdentityEmitter.neutralize(d, { root: tmpRoot })
  mainIdentityEmitter.write(d, { root: tmpRoot })
  assert.equal(fs.readFileSync(tmpFile, 'utf8'), realSrc)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})
