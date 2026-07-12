// scripts/brand/__tests__/package-json.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { renderPackageJson, packageJsonEmitter, setPath } from '../emitters/package-json.mjs'

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
