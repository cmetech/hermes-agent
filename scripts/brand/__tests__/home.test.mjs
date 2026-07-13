import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDescriptor } from '../descriptor.mjs'
import { homeEmitter, homeNames, NEUTRAL_HOME, FILE_SPECS } from '../emitters/home.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..')

test('homeNames: win = slug, posix = homeDir', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  assert.deepEqual(homeNames(d), { win: 'otto', posix: '.otto' })
  const l = loadDescriptor('loop24', { root: ROOT })
  assert.deepEqual(homeNames(l), { win: 'loop24', posix: '.loop24' })
})

test('NEUTRAL_HOME is the upstream Hermes value', () => {
  assert.deepEqual(NEUTRAL_HOME, { win: 'hermes', posix: '.hermes' })
})

// Build an isolated fixture tree carrying each anchor with NEUTRAL values,
// then prove write → check → neutralize round-trips.
function makeFixture() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'home-emitter-'))
  const fixtures = {
    'hermes_constants.py': '    if x:\n        return base / "hermes"\n    return Path.home() / ".hermes"\n',
    'apps/bootstrap-installer/src-tauri/src/paths.rs':
      '        return local_app_data.join("hermes");\n    return home.join(".hermes");\n    PathBuf::from(".hermes")\n',
    'apps/desktop/electron/main.ts':
      "  if (win) {\n    return path.join(process.env.LOCALAPPDATA, 'hermes')\n  }\n  return path.join(app.getPath('home'), '.hermes')\n",
    'apps/desktop/scripts/test-desktop.mjs':
      "    return path.join(process.env.LOCALAPPDATA, 'hermes')\n  return path.join(os.homedir(), '.hermes')\n",
    'scripts/install.ps1':
      '  $HermesHome = "$env:LOCALAPPDATA\\hermes"\n  $InstallDir = "$env:LOCALAPPDATA\\hermes\\hermes-agent"\n',
    'scripts/install.sh': 'HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"\n',
    'scripts/lib/node-bootstrap.sh': 'HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"\n',
  }
  for (const [rel, body] of Object.entries(fixtures)) {
    const p = path.join(dir, rel)
    fs.mkdirSync(path.dirname(p), { recursive: true })
    fs.writeFileSync(p, body)
  }
  return dir
}

test('write(otto) stamps .otto/otto; check passes; neutralize restores', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const root = makeFixture()

  homeEmitter.write(d, { root })
  assert.equal(homeEmitter.check(d, { root }).ok, true)

  const ps1 = fs.readFileSync(path.join(root, 'scripts/install.ps1'), 'utf8')
  assert.match(ps1, /\$env:LOCALAPPDATA\\otto"/)
  assert.match(ps1, /\$env:LOCALAPPDATA\\otto\\hermes-agent"/)  // clone dir preserved
  const consts = fs.readFileSync(path.join(root, 'hermes_constants.py'), 'utf8')
  assert.match(consts, /return base \/ "otto"/)
  assert.match(consts, /return Path\.home\(\) \/ "\.otto"/)

  // write is idempotent
  const before = FILE_SPECS.map(s => fs.readFileSync(path.join(root, s.file), 'utf8'))
  homeEmitter.write(d, { root })
  const after = FILE_SPECS.map(s => fs.readFileSync(path.join(root, s.file), 'utf8'))
  assert.deepEqual(after, before)

  // neutralize restores the exact NEUTRAL fixture bytes (round-trip)
  homeEmitter.neutralize(d, { root })
  const consts2 = fs.readFileSync(path.join(root, 'hermes_constants.py'), 'utf8')
  assert.match(consts2, /return base \/ "hermes"/)
  assert.match(consts2, /return Path\.home\(\) \/ "\.hermes"/)
  const ps1n = fs.readFileSync(path.join(root, 'scripts/install.ps1'), 'utf8')
  assert.match(ps1n, /\$env:LOCALAPPDATA\\hermes"/)
  assert.match(ps1n, /\$env:LOCALAPPDATA\\hermes\\hermes-agent"/)
})

test('check fails when a literal is wrong', () => {
  const d = loadDescriptor('otto', { root: ROOT })
  const root = makeFixture()  // holds NEUTRAL (.hermes), not .otto
  assert.equal(homeEmitter.check(d, { root }).ok, false)
})
