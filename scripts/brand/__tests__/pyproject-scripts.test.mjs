import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { pyprojectScriptsEmitter, hasBrandScripts, addBrandScripts, removeBrandScripts } from '../emitters/pyproject-scripts.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

test('current pyproject already has otto scripts', () => {
  const src = fs.readFileSync(path.join(ROOT, 'pyproject.toml'), 'utf8')
  assert.equal(hasBrandScripts(src, 'otto'), true)
})

test('check(otto) passes', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  assert.equal(pyprojectScriptsEmitter.check(d, { root: ROOT }).ok, true)
})

test('addBrandScripts inserts three entries after hermes-acp and is idempotent', () => {
  const src = '[project.scripts]\nhermes = "hermes_cli.main:main"\nhermes-agent = "run_agent:main"\nhermes-acp = "acp_adapter.entry:main"\n'
  const once = addBrandScripts(src, 'loop24', 'LOOP24')
  assert.match(once, /loop24 = "hermes_cli.main:main"/)
  assert.match(once, /loop24-agent = "run_agent:main"/)
  assert.match(once, /loop24-acp = "acp_adapter.entry:main"/)
  assert.equal(addBrandScripts(once, 'loop24', 'LOOP24'), once)
})

test('addBrandScripts on the upstream-neutral pyproject yields the exact otto comment+scripts block, byte-for-byte', () => {
  const src = '[project.scripts]\nhermes = "hermes_cli.main:main"\nhermes-agent = "run_agent:main"\nhermes-acp = "acp_adapter.entry:main"\n\n[tool.setuptools]\n'
  const next = addBrandScripts(src, 'otto', 'OTTO')
  const expected =
    '[project.scripts]\nhermes = "hermes_cli.main:main"\nhermes-agent = "run_agent:main"\nhermes-acp = "acp_adapter.entry:main"\n' +
    '# OTTO branding — same entry points under the OTTO command name. The `hermes*`\n' +
    '# commands are kept intact (the desktop backend spawns `hermes serve` and\n' +
    '# upstream merges expect them); `otto*` are additive aliases.\n' +
    'otto = "hermes_cli.main:main"\n' +
    'otto-agent = "run_agent:main"\n' +
    'otto-acp = "acp_adapter.entry:main"\n' +
    '\n[tool.setuptools]\n'
  assert.equal(next, expected)
})

test('removeBrandScripts reverses addBrandScripts, byte-for-byte, back to the upstream-neutral pyproject', () => {
  const neutral = '[project.scripts]\nhermes = "hermes_cli.main:main"\nhermes-agent = "run_agent:main"\nhermes-acp = "acp_adapter.entry:main"\n\n[tool.setuptools]\n'
  const branded = addBrandScripts(neutral, 'otto', 'OTTO')
  assert.notEqual(branded, neutral, 'precondition: addBrandScripts changed the source')
  assert.equal(removeBrandScripts(branded, 'otto', 'OTTO'), neutral)
})

test('removeBrandScripts is a no-op when the brand scripts are absent', () => {
  const neutral = '[project.scripts]\nhermes = "hermes_cli.main:main"\nhermes-agent = "run_agent:main"\nhermes-acp = "acp_adapter.entry:main"\n'
  assert.equal(removeBrandScripts(neutral, 'otto', 'OTTO'), neutral)
})

test('neutralize(otto) reverts the real pyproject.toml to the upstream-neutral form in a temp root', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const realSrc = fs.readFileSync(path.join(ROOT, 'pyproject.toml'), 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pyprojectneutral-'))
  const tmpFile = path.join(tmpRoot, 'pyproject.toml')
  fs.writeFileSync(tmpFile, realSrc)

  const r = pyprojectScriptsEmitter.neutralize(d, { root: tmpRoot })
  assert.equal(r.changed, true)
  const after = fs.readFileSync(tmpFile, 'utf8')
  assert.equal(hasBrandScripts(after, 'otto'), false)
  assert.doesNotMatch(after, /OTTO branding/)
  assert.match(after, /hermes-acp = "acp_adapter\.entry:main"\n\n\[tool\.setuptools\]/)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('neutralize dryRun:true reports changed but does not write the file', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const realSrc = fs.readFileSync(path.join(ROOT, 'pyproject.toml'), 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pyprojectneutral-dry-'))
  const tmpFile = path.join(tmpRoot, 'pyproject.toml')
  fs.writeFileSync(tmpFile, realSrc)

  const r = pyprojectScriptsEmitter.neutralize(d, { root: tmpRoot, dryRun: true })
  assert.equal(r.changed, true)
  assert.equal(fs.readFileSync(tmpFile, 'utf8'), realSrc, 'dry run must not mutate the file')

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})

test('ROUND-TRIP: neutralize then write(otto) reproduces the current on-disk pyproject.toml byte-for-byte', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const realSrc = fs.readFileSync(path.join(ROOT, 'pyproject.toml'), 'utf8')
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pyproject-roundtrip-'))
  const tmpFile = path.join(tmpRoot, 'pyproject.toml')
  fs.writeFileSync(tmpFile, realSrc)

  pyprojectScriptsEmitter.neutralize(d, { root: tmpRoot })
  pyprojectScriptsEmitter.write(d, { root: tmpRoot })
  assert.equal(fs.readFileSync(tmpFile, 'utf8'), realSrc)

  fs.rmSync(tmpRoot, { recursive: true, force: true })
})
