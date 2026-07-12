import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { pyprojectScriptsEmitter, hasBrandScripts, addBrandScripts } from '../emitters/pyproject-scripts.mjs'

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
