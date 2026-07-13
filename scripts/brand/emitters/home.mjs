// scripts/brand/emitters/home.mjs
import fs from 'node:fs'
import path from 'node:path'

// Windows dir name = slug (no dot); POSIX dir name = homeDir (with dot).
export function homeNames(descriptor) {
  return { win: descriptor.slug, posix: descriptor.homeDir }
}
export const NEUTRAL_HOME = { win: 'hermes', posix: '.hermes' }

// Each sub: a regex with 3 capture groups (prefix)(value)(suffix); `value`
// (group 2) is the home-dir name to replace. `kind` selects win/posix.
// `global: true` marks a sub that legitimately matches more than once.
export const FILE_SPECS = [
  { file: 'hermes_constants.py', subs: [
    { re: /(return base \/ ")([^"]*)(")/, kind: 'win' },
    { re: /(return Path\.home\(\) \/ ")([^"]*)(")/, kind: 'posix' },
  ]},
  { file: 'apps/bootstrap-installer/src-tauri/src/paths.rs', subs: [
    { re: /(local_app_data\.join\(")([^"]*)("\))/, kind: 'win' },
    { re: /(home\.join\(")([^"]*)("\))/, kind: 'posix' },
    { re: /(PathBuf::from\(")(\.[^"]*)("\))/, kind: 'posix' },
  ]},
  { file: 'apps/desktop/electron/main.ts', subs: [
    { re: /(return path\.join\(process\.env\.LOCALAPPDATA, ')([^']*)('\))/, kind: 'win' },
    { re: /(return path\.join\(app\.getPath\('home'\), ')([^']*)('\))/, kind: 'posix' },
  ]},
  { file: 'apps/desktop/scripts/test-desktop.mjs', subs: [
    { re: /(return path\.join\(process\.env\.LOCALAPPDATA, ')([^']*)('\))/, kind: 'win' },
    { re: /(return path\.join\(os\.homedir\(\), ')([^']*)('\))/, kind: 'posix' },
  ]},
  { file: 'scripts/install.ps1', subs: [
    // Two sites ($HermesHome + $InstallDir); the [^"\\]+ stops before
    // "\hermes-agent" so the clone-dir segment is preserved.
    { re: /(\$env:LOCALAPPDATA\\)([^"\\]+)()/g, kind: 'win', global: true },
  ]},
  { file: 'scripts/install.sh', subs: [
    { re: /(\$\{HERMES_HOME:-\$HOME\/)([^}]*)(\})/, kind: 'posix' },
  ]},
  { file: 'scripts/lib/node-bootstrap.sh', subs: [
    { re: /(\$\{HERMES_HOME:-\$HOME\/)([^}]*)(\})/, kind: 'posix' },
  ]},
]

function applyNames(src, subs, names) {
  let next = src
  for (const s of subs) {
    const target = names[s.kind]
    next = next.replace(s.re, (_m, a, _v, c) => `${a}${target}${c ?? ''}`)
  }
  return next
}

function checkNames(src, subs, names) {
  for (const s of subs) {
    const g = s.re.global ? s.re : new RegExp(s.re.source, s.re.flags + 'g')
    const matches = [...src.matchAll(g)]
    if (matches.length === 0) return { ok: false, detail: `anchor not found: ${s.re}` }
    for (const m of matches) {
      if (m[2] !== names[s.kind]) {
        return { ok: false, detail: `value ${JSON.stringify(m[2])} != ${JSON.stringify(names[s.kind])} for ${s.re}` }
      }
    }
  }
  return { ok: true }
}

export const homeEmitter = {
  id: 'home',
  check(d, { root }) {
    const names = homeNames(d)
    for (const spec of FILE_SPECS) {
      const src = fs.readFileSync(path.join(root, spec.file), 'utf8')
      const r = checkNames(src, spec.subs, names)
      if (!r.ok) return { ok: false, detail: `${spec.file}: ${r.detail}` }
    }
    return { ok: true }
  },
  write(d, { root }) {
    const names = homeNames(d)
    let changed = false
    for (const spec of FILE_SPECS) {
      const p = path.join(root, spec.file)
      const src = fs.readFileSync(p, 'utf8')
      const next = applyNames(src, spec.subs, names)
      if (next !== src) { fs.writeFileSync(p, next); changed = true }
    }
    return { changed, detail: changed ? 'home dir literals stamped' : undefined }
  },
  neutralize(_d, { root, dryRun = false } = {}) {
    let changed = false
    for (const spec of FILE_SPECS) {
      const p = path.join(root, spec.file)
      const src = fs.readFileSync(p, 'utf8')
      const next = applyNames(src, spec.subs, NEUTRAL_HOME)
      if (next !== src) { changed = true; if (!dryRun) fs.writeFileSync(p, next) }
    }
    return { changed, detail: changed ? 'home dir literals neutralized' : undefined }
  },
}
