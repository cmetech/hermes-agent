// scripts/brand/__tests__/package-json.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { renderPackageJson, renderNeutralPackageJson, packageJsonEmitter, setPath } from '../emitters/package-json.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')
const PKG = path.join(ROOT, 'apps/desktop/package.json')

test('check(otto) passes against the real on-disk package.json', () => {
  const result = packageJsonEmitter.check(loadDescriptor('otto', { root: ROOT }), { root: ROOT })
  assert.equal(result.ok, true, result.detail)
})

test('renderPackageJson(loop24) swaps identity fields, keeps hermes scheme second', () => {
  const onDisk = fs.readFileSync(PKG, 'utf8')
  const out = renderPackageJson(loadDescriptor('loop24', { root: ROOT }), onDisk)
  const j = JSON.parse(out)
  assert.equal(j.productName, 'LOOP24')
  assert.equal(j.name, 'loop24')
  assert.equal(j.build.appId, 'io.cmetech.loop24')
  assert.deepEqual(j.build.protocols[0].schemes, ['loop24', 'hermes'])
  assert.equal(j.build.nsis.title !== undefined ? j.build.nsis.title : j.build.dmg.title, 'Install LOOP24')
})

test('renderPackageJson(loop24) sets nested mac/win/linux identity fields', () => {
  const onDisk = fs.readFileSync(PKG, 'utf8')
  const out = renderPackageJson(loadDescriptor('loop24', { root: ROOT }), onDisk)
  const j = JSON.parse(out)
  assert.equal(j.build.mac.extendInfo.CFBundleDisplayName, 'LOOP24')
  assert.equal(j.build.mac.extendInfo.CFBundleExecutable, 'LOOP24')
  assert.equal(j.build.mac.extendInfo.CFBundleName, 'LOOP24')
  assert.equal(j.build.win.legalTrademarks, 'LOOP24')
  assert.equal(j.build.linux.synopsis, 'Native desktop shell for LOOP24.')
  assert.equal(j.build.artifactName, 'LOOP24-${version}-${os}-${arch}.${ext}')
})

test('renderPackageJson uses a per-brand shortcutName so co-installed brands do not collide', () => {
  const onDisk = fs.readFileSync(PKG, 'utf8')
  const loop = JSON.parse(renderPackageJson(loadDescriptor('loop24', { root: ROOT }), onDisk))
  const otto = JSON.parse(renderPackageJson(loadDescriptor('otto', { root: ROOT }), onDisk))
  assert.equal(loop.build.nsis.shortcutName, 'LOOP24')
  assert.equal(otto.build.nsis.shortcutName, 'OTTO')
  // uninstall display name stays branded (Add/Remove Programs shows the brand)
  assert.equal(loop.build.nsis.uninstallDisplayName, 'LOOP24')
  assert.equal(otto.build.nsis.uninstallDisplayName, 'OTTO')
})

test('renderPackageJson(otto) round-trips with no fields lost (deep-equal, not byte-equal)', () => {
  const onDisk = fs.readFileSync(PKG, 'utf8')
  const out = renderPackageJson(loadDescriptor('otto', { root: ROOT }), onDisk)
  assert.deepEqual(JSON.parse(out), JSON.parse(onDisk))
})

test('setPath throws (does not silently no-op) on a missing intermediate key, naming the path', () => {
  assert.throws(() => setPath({ build: {} }, 'build.mac.extendInfo.CFBundleName', 'X'), /build\.mac\.extendInfo\.CFBundleName/)
})

test('setPath throws (does not silently no-op) on a missing last key, naming the path', () => {
  assert.throws(() => setPath({ build: { mac: {} } }, 'build.mac.doesNotExist', 'X'), /build\.mac\.doesNotExist/)
})

test('setPath still succeeds and mutates in place when every key on the path exists', () => {
  const obj = { build: { mac: { extendInfo: { CFBundleName: 'old' } } } }
  const result = setPath(obj, 'build.mac.extendInfo.CFBundleName', 'new')
  assert.equal(result, true)
  assert.equal(obj.build.mac.extendInfo.CFBundleName, 'new')
})

test('renderNeutralPackageJson sets every brand field path to the upstream Hermes value', () => {
  const onDisk = fs.readFileSync(PKG, 'utf8')
  const out = renderNeutralPackageJson(onDisk)
  const j = JSON.parse(out)
  assert.equal(j.name, 'hermes')
  assert.equal(j.productName, 'Hermes')
  assert.equal(j.description, 'Native desktop shell for Hermes Agent.')
  assert.equal(j.build.appId, 'com.nousresearch.hermes')
  assert.equal(j.build.productName, 'Hermes')
  assert.equal(j.build.executableName, 'Hermes')
  assert.equal(j.build.artifactName, 'Hermes-${version}-${os}-${arch}.${ext}')
  assert.deepEqual(j.build.protocols[0], { name: 'Hermes Protocol', schemes: ['hermes'] })
  assert.equal(j.build.mac.extendInfo.CFBundleDisplayName, 'Hermes')
  assert.equal(j.build.mac.extendInfo.CFBundleExecutable, 'Hermes')
  assert.equal(j.build.mac.extendInfo.CFBundleName, 'Hermes')
  assert.equal(j.build.mac.extendInfo.NSAudioCaptureUsageDescription, 'Hermes uses audio capture for voice conversations.')
  assert.equal(
    j.build.mac.extendInfo.NSMicrophoneUsageDescription,
    'Hermes uses the microphone for voice input and voice conversations.'
  )
  assert.equal(j.build.win.legalTrademarks, 'Hermes')
  assert.equal(j.build.linux.synopsis, 'Native desktop shell for Hermes Agent.')
  assert.equal(j.build.dmg.title, 'Install Hermes')
  assert.equal(j.build.nsis.shortcutName, 'Hermes')
  assert.equal(j.build.nsis.uninstallDisplayName, 'Hermes')
})

test('renderNeutralPackageJson preserves every other field (deep-equal apart from the brand paths)', () => {
  const onDisk = fs.readFileSync(PKG, 'utf8')
  const out = renderNeutralPackageJson(onDisk)
  const before = JSON.parse(onDisk)
  const after = JSON.parse(out)
  assert.deepEqual(Object.keys(after).sort(), Object.keys(before).sort())
  assert.deepEqual(after.dependencies, before.dependencies)
  assert.deepEqual(after.scripts, before.scripts)
})

test('neutralize(otto) reverts the real package.json to neutral Hermes values in a temp root', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const realSrc = fs.readFileSync(PKG, 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pkgneutral-'))
  fs.mkdirSync(path.join(tmpRoot, 'apps/desktop'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'apps/desktop/package.json')
  fs.writeFileSync(tmpFile, realSrc)

  const r = packageJsonEmitter.neutralize(d, { root: tmpRoot })
  assert.equal(r.changed, true)
  const after = JSON.parse(fs.readFileSync(tmpFile, 'utf8'))
  assert.equal(after.productName, 'Hermes')
  assert.equal(after.build.appId, 'com.nousresearch.hermes')

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('neutralize dryRun:true reports changed but does not write the file', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const realSrc = fs.readFileSync(PKG, 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pkgneutral-dry-'))
  fs.mkdirSync(path.join(tmpRoot, 'apps/desktop'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'apps/desktop/package.json')
  fs.writeFileSync(tmpFile, realSrc)

  const r = packageJsonEmitter.neutralize(d, { root: tmpRoot, dryRun: true })
  assert.equal(r.changed, true)
  assert.equal(fs.readFileSync(tmpFile, 'utf8'), realSrc, 'dry run must not mutate the file')

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('ROUND-TRIP: neutralize then write(otto) reproduces the current on-disk package.json byte-for-byte', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const realSrc = fs.readFileSync(PKG, 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pkg-roundtrip-'))
  fs.mkdirSync(path.join(tmpRoot, 'apps/desktop'), { recursive: true })
  const tmpFile = path.join(tmpRoot, 'apps/desktop/package.json')
  fs.writeFileSync(tmpFile, realSrc)

  packageJsonEmitter.neutralize(d, { root: tmpRoot })
  packageJsonEmitter.write(d, { root: tmpRoot })
  assert.equal(fs.readFileSync(tmpFile, 'utf8'), realSrc)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})
