import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { buildDescriptor, applyReleaseSwaps, stageReleases, RESERVED_SLUGS, parseCloneArgs } from '../create-clone.mjs'

test('buildDescriptor: defaults use house style (COWORKER wordmark, cmetech appId/releases)', () => {
  const d = buildDescriptor('acme')
  assert.equal(d.slug, 'acme')
  assert.equal(d.displayName, 'ACME')
  assert.equal(d.wordmark, 'ACME COWORKER')
  assert.equal(d.tagline, 'ACME orchestrates your thoughts and tasks into effective outcomes.')
  assert.equal(d.appId, 'io.cmetech.acme')
  assert.equal(d.scheme, 'acme')
  assert.equal(d.homeDir, '.acme')
  assert.equal(d.releasesRepo, 'cmetech/acme')
  assert.equal(d.updateCommand, 'acme update')
  assert.equal(d.theme, 'otto')
  assert.equal(d.gateway, 'otto')
  assert.deepEqual(d.curation, { skills: { exclude: [], disabledByDefault: [] }, tools: { excludeToolsets: [], disabledByDefault: [] } })
  assert.deepEqual(d.capabilitySets, [])
  assert.deepEqual(d.personaSets, [])
  assert.deepEqual(d.cli, { bannerLogo: '', bannerHero: '' })
})

test('buildDescriptor: wordmark/tagline args override defaults', () => {
  const d = buildDescriptor('acme', { wordmark: 'ACME BOT', tagline: 'Ship it.' })
  assert.equal(d.wordmark, 'ACME BOT')
  assert.equal(d.tagline, 'Ship it.')
})

test('buildDescriptor: refuses an invalid slug', () => {
  assert.throws(() => buildDescriptor('Acme'), /invalid slug/)
  assert.throws(() => buildDescriptor('1acme'), /invalid slug/)
  assert.throws(() => buildDescriptor('a b'), /invalid slug/)
})

test('buildDescriptor: refuses a reserved slug', () => {
  for (const s of RESERVED_SLUGS) assert.throws(() => buildDescriptor(s), /reserved slug/)
})

test('applyReleaseSwaps: swaps functional otto tokens, preserves shared refs', () => {
  const ctx = { slug: 'loop24', displayName: 'LOOP24' }
  assert.equal(applyReleaseSwaps('REPO="cmetech/otto"', ctx), 'REPO="cmetech/loop24"')
  assert.equal(applyReleaseSwaps('OTTO-${version}-mac-arm64.dmg', ctx), 'LOOP24-${version}-mac-arm64.dmg')
  assert.equal(applyReleaseSwaps('OTTO.AppImage', ctx), 'LOOP24.AppImage')
  assert.equal(applyReleaseSwaps('OTTO.app', ctx), 'LOOP24.app')
  assert.equal(applyReleaseSwaps('[otto] downloading', ctx), '[loop24] downloading')
  assert.equal(applyReleaseSwaps("'User-Agent' = 'otto-installer'", ctx), "'User-Agent' = 'loop24-installer'")
  assert.equal(applyReleaseSwaps('name: Build & publish OTTO desktop release', ctx), 'name: Build & publish LOOP24 desktop release')
  assert.equal(applyReleaseSwaps('OTTO Desktop v1.0', ctx), 'LOOP24 Desktop v1.0')
  assert.equal(applyReleaseSwaps('default: "otto"', ctx), 'default: "loop24"')
  assert.equal(applyReleaseSwaps('cmetech/hermes-agent@otto', ctx), 'cmetech/hermes-agent@loop24')
  assert.equal(applyReleaseSwaps('-f ref=otto -f version', ctx), '-f ref=loop24 -f version')
  // shared refs and lowercase doc/workspace paths must NOT change:
  assert.equal(applyReleaseSwaps('repository: cmetech/hermes-agent', ctx), 'repository: cmetech/hermes-agent')
  assert.equal(applyReleaseSwaps('docs/otto-desktop-release-install.md', ctx), 'docs/otto-desktop-release-install.md')
  assert.equal(applyReleaseSwaps('the otto_hermes workspace', ctx), 'the otto_hermes workspace')
})

test('applyReleaseSwaps: preserves OTTO_ env-var identifiers, swaps display OTTO', () => {
  const ctx = { slug: 'loop24', displayName: 'LOOP24' }
  assert.equal(applyReleaseSwaps('OTTO_PRODUCT_VERSION: ${{ github.event.inputs.version }}', ctx), 'OTTO_PRODUCT_VERSION: ${{ github.event.inputs.version }}')
  assert.equal(applyReleaseSwaps('env:\n  OTTO_VERSION: 1', ctx), 'env:\n  OTTO_VERSION: 1')
  assert.equal(applyReleaseSwaps('Launch OTTO from /Applications', ctx), 'Launch LOOP24 from /Applications')
  assert.equal(applyReleaseSwaps('files: dist/OTTO-*', ctx), 'files: dist/LOOP24-*')
})

test('parseCloneArgs: --releases-dir value does not leak into wordmark/tagline', () => {
  assert.deepEqual(parseCloneArgs(['loop24', '--releases-dir', '../loop24-releases']),
    { slug: 'loop24', wordmark: undefined, tagline: undefined, releasesDir: '../loop24-releases', force: false })
})
test('parseCloneArgs: positional wordmark/tagline + --force', () => {
  assert.deepEqual(parseCloneArgs(['loop24', 'LOOP24 COWORKER', 'Tag.', '--force']),
    { slug: 'loop24', wordmark: 'LOOP24 COWORKER', tagline: 'Tag.', releasesDir: undefined, force: true })
})
test('parseCloneArgs: positional wordmark alongside --releases-dir (no tagline)', () => {
  assert.deepEqual(parseCloneArgs(['loop24', 'WM', '--releases-dir', '../x']),
    { slug: 'loop24', wordmark: 'WM', tagline: undefined, releasesDir: '../x', force: false })
})

test('stageReleases: copies+swaps into a target dir, preserving workflow subpath', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'cc-'))
  const src = path.join(tmp, 'otto-releases')
  fs.mkdirSync(path.join(src, '.github', 'workflows'), { recursive: true })
  fs.writeFileSync(path.join(src, 'install.sh'), 'REPO="cmetech/otto"\n')
  fs.writeFileSync(path.join(src, 'README.md'), '# OTTO Desktop\n')
  fs.writeFileSync(path.join(src, '.github', 'workflows', 'release.yml'), 'files: dist/OTTO-*\n')
  const dst = path.join(tmp, 'loop24-releases')
  const written = stageReleases({ repoRoot: tmp, releasesDir: dst, srcReleasesDir: src, slug: 'loop24', displayName: 'LOOP24' })
  assert.ok(written.length >= 3)
  assert.equal(fs.readFileSync(path.join(dst, 'install.sh'), 'utf8'), 'REPO="cmetech/loop24"\n')
  assert.equal(fs.readFileSync(path.join(dst, 'README.md'), 'utf8'), '# LOOP24 Desktop\n')
  assert.equal(fs.readFileSync(path.join(dst, '.github', 'workflows', 'release.yml'), 'utf8'), 'files: dist/LOOP24-*\n')
})
