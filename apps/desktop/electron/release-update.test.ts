/**
 * Tests for electron/release-update.ts — pure helpers for the OTTO release-based
 * update path (packaged installs check GitHub releases, not git commits).
 *
 * Run with: npx tsx --test electron/release-update.test.ts
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  assetPattern,
  buildReleaseUpdateStatus,
  compareSemver,
  installerFileName,
  installerLaunch,
  isReleaseInstall,
  parseLatestRelease
} from './release-update'

test('isReleaseInstall: true only when packaged AND productVersion present', () => {
  assert.equal(isReleaseInstall({ productVersion: '0.1.2' }, true), true)
  assert.equal(isReleaseInstall({ productVersion: null }, true), false) // source rebuild
  assert.equal(isReleaseInstall({ productVersion: '0.1.2' }, false), false) // dev / not packaged
  assert.equal(isReleaseInstall(null, true), false)
})

test('compareSemver orders numeric dotted versions', () => {
  assert.equal(compareSemver('0.1.2', '0.1.3'), -1)
  assert.equal(compareSemver('0.1.10', '0.1.2'), 1) // numeric, not lexical
  assert.equal(compareSemver('0.1.2', '0.1.2'), 0)
  assert.equal(compareSemver('v0.2.0', '0.1.9'), 1) // tolerates leading v
})

test('parseLatestRelease picks newest release + matching asset', () => {
  const releases = [
    {
      tag_name: 'v0.1.3',
      html_url: 'https://github.com/cmetech/otto/releases/tag/v0.1.3',
      assets: [{ name: 'OTTO-0.1.3-win-x64.exe', browser_download_url: 'https://x/OTTO-0.1.3-win-x64.exe' }]
    },
    { tag_name: 'v0.1.2', html_url: 'u2', assets: [] }
  ]
  const r = parseLatestRelease(releases, 'win32', 'x64')
  assert.equal(r?.version, '0.1.3')
  assert.equal(r?.assetUrl, 'https://x/OTTO-0.1.3-win-x64.exe')
})

test('parseLatestRelease returns null asset when no matching platform asset', () => {
  const releases = [{ tag_name: 'v0.1.3', html_url: 'u', assets: [{ name: 'OTTO-0.1.3-mac-arm64.dmg' }] }]
  const r = parseLatestRelease(releases, 'win32', 'x64')
  assert.equal(r?.version, '0.1.3')
  assert.equal(r?.assetUrl, null)
})

test('parseLatestRelease returns null for empty input', () => {
  assert.equal(parseLatestRelease([], 'win32', 'x64'), null)
})

test('buildReleaseUpdateStatus flags updateAvailable when latest > current', () => {
  const latest = { version: '0.1.3', releaseUrl: 'u', assetUrl: 'a' }
  const s = buildReleaseUpdateStatus('0.1.2', latest, 'otto', 'cmetech/otto')
  assert.equal(s.mode, 'release')
  assert.equal(s.updateAvailable, true)
  assert.equal(s.latestVersion, '0.1.3')
  assert.equal(s.behind, 1)

  const same = buildReleaseUpdateStatus('0.1.3', latest, 'otto', 'cmetech/otto')
  assert.equal(same.updateAvailable, false)
  assert.equal(same.behind, 0)
})

test('buildReleaseUpdateStatus fallback releaseUrl uses the given releasesRepo', () => {
  const latest = { version: '0.1.3', tag_name: 'v0.1.3', assetUrl: null, html_url: null }
  const s = buildReleaseUpdateStatus('0.1.2', latest, 'loop24', 'cmetech/loop24')
  assert.match(s.releaseUrl, /github\.com\/cmetech\/loop24\/releases\/tag\/v0\.1\.3/)
})

test('parseLatestRelease -> buildReleaseUpdateStatus: absent html_url falls through to the brand-derived releaseUrl (previously-dead fallback is now reachable)', () => {
  const releases = [
    {
      tag_name: 'v0.1.3',
      html_url: undefined,
      assets: [{ name: 'OTTO-0.1.3-mac-arm64.dmg', browser_download_url: 'https://x/OTTO-0.1.3-mac-arm64.dmg' }]
    }
  ]
  const latest = parseLatestRelease(releases, 'darwin', 'arm64')
  assert.equal(latest?.releaseUrl, null) // no more hardcoded cmetech/otto fallback inside parseLatestRelease

  const s = buildReleaseUpdateStatus('0.1.2', latest, 'loop24', 'cmetech/loop24')
  assert.equal(s.releaseUrl, 'https://github.com/cmetech/loop24/releases/tag/v0.1.3')
})

test('assetPattern is brand-agnostic (matches OTTO and LOOP24, not blockmaps)', () => {
  const win = assetPattern('win32', 'x64')
  assert.ok(win.test('OTTO-0.1.12-win-x64.exe'))
  assert.ok(win.test('LOOP24-0.1.3-win-x64.exe'))
  assert.ok(!win.test('OTTO-0.1.12-win-x64.exe.blockmap'))
  assert.ok(!win.test('OTTO-0.1.12-mac-arm64.dmg'))
  const mac = assetPattern('darwin', 'arm64')
  assert.ok(mac.test('LOOP24-0.1.3-mac-arm64.dmg'))
  assert.ok(!mac.test('LOOP24-0.1.3-mac-arm64.dmg.blockmap'))
})

test('parseLatestRelease resolves the LOOP24 win asset (regression: was OTTO-hardcoded)', () => {
  const releases = [
    {
      tag_name: 'v0.1.3',
      html_url: 'https://github.com/cmetech/loop24/releases/tag/v0.1.3',
      assets: [
        { name: 'LOOP24-0.1.3-win-x64.exe', browser_download_url: 'https://x/LOOP24-0.1.3-win-x64.exe' },
        { name: 'LOOP24-0.1.3-win-x64.exe.blockmap', browser_download_url: 'https://x/bm' }
      ]
    }
  ]
  const latest = parseLatestRelease(releases, 'win32', 'x64')
  assert.equal(latest.assetUrl, 'https://x/LOOP24-0.1.3-win-x64.exe')
})

test('installerFileName derives the basename from the asset URL', () => {
  assert.equal(installerFileName('https://x/y/LOOP24-0.1.3-win-x64.exe'), 'LOOP24-0.1.3-win-x64.exe')
})

test('installerLaunch: win runs the exe directly; mac opens the dmg', () => {
  assert.deepEqual(installerLaunch('C:\\t\\OTTO-0.1.12-win-x64.exe', 'win32'), {
    cmd: 'C:\\t\\OTTO-0.1.12-win-x64.exe',
    args: []
  })
  assert.deepEqual(installerLaunch('/t/OTTO.dmg', 'darwin'), { cmd: 'open', args: ['/t/OTTO.dmg'] })
})
