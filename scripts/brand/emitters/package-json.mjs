// scripts/brand/emitters/package-json.mjs
//
// Desktop identity emitter for apps/desktop/package.json.
//
// NOTE on approach: this deliberately does NOT do byte-for-byte JSON
// reserialization (JSON.parse -> JSON.stringify is not guaranteed to
// reproduce source formatting exactly - array wrapping, key spacing, etc).
// Instead:
//   - `check` is PATH-BASED: parse the on-disk file and assert each
//     brand-driven field path equals the descriptor's expected value.
//   - `renderPackageJson` parses the input text, SETS exactly the brand
//     field paths from the descriptor, and reserializes with
//     `JSON.stringify(obj, null, 2) + '\n'`. It is used by `write` to
//     produce a new brand's package.json; it does not need to be
//     byte-identical to the source text, only valid JSON with the right
//     values.
//
// NOTE on real file shape vs. the naive "flat under build" description:
// a few fields live one level deeper than a first guess would suggest:
//   - `legalTrademarks` lives under `build.win`, not `build` directly.
//   - the mac Info.plist overrides live under `build.mac.extendInfo`,
//     not `build.mac` directly.
//   - `synopsis` lives under `build.linux`, not `build` directly.
//   - the "Install <Name>" title lives under `build.dmg.title` (dmg,
//     not nsis - the nsis block has no `title` key at all).
// This emitter targets the real paths as they exist in
// apps/desktop/package.json today.

import fs from 'node:fs'
import path from 'node:path'

const FILE = 'apps/desktop/package.json'

function fieldPaths(d) {
  const displayName = d.displayName
  const description = `Native desktop shell for ${displayName}.`
  return [
    ['name', d.slug],
    ['productName', displayName],
    ['description', description],
    ['build.appId', d.appId],
    ['build.productName', displayName],
    ['build.executableName', displayName],
    ['build.artifactName', `${displayName}-\${version}-\${os}-\${arch}.\${ext}`],
    ['build.protocols.0.name', `${displayName} Protocol`],
    ['build.protocols.0.schemes', [d.scheme, 'hermes']],
    ['build.mac.extendInfo.CFBundleDisplayName', displayName],
    ['build.mac.extendInfo.CFBundleExecutable', displayName],
    ['build.mac.extendInfo.CFBundleName', displayName],
    [
      'build.mac.extendInfo.NSAudioCaptureUsageDescription',
      `${displayName} uses audio capture for voice conversations.`
    ],
    [
      'build.mac.extendInfo.NSMicrophoneUsageDescription',
      `${displayName} uses the microphone for voice input and voice conversations.`
    ],
    ['build.win.legalTrademarks', displayName],
    ['build.linux.synopsis', description],
    ['build.dmg.title', `Install ${displayName}`],
    ['build.nsis.shortcutName', displayName],
    ['build.nsis.uninstallDisplayName', displayName]
  ]
}

function getPath(obj, dotted) {
  return dotted.split('.').reduce((cur, key) => (cur == null ? undefined : cur[key]), obj)
}

// Throws (rather than silently no-oping) when an intermediate or final key is
// missing from `obj`, naming the full dotted path in the message. A silent
// skip here would mean a brand-driven field quietly never gets written if
// apps/desktop/package.json's shape ever drifts from what fieldPaths()
// expects — exactly the kind of failure that must be loud once `write` is
// authoritative against the real tree (Plan 3).
export function setPath(obj, dotted, value) {
  const keys = dotted.split('.')
  let cur = obj
  for (let i = 0; i < keys.length - 1; i++) {
    const key = keys[i]
    if (cur[key] == null) {
      throw new Error(`setPath: missing intermediate key "${key}" while setting "${dotted}"`)
    }
    cur = cur[key]
  }
  const last = keys[keys.length - 1]
  if (cur[last] === undefined) {
    throw new Error(`setPath: missing key "${last}" while setting "${dotted}"`)
  }
  cur[last] = value
  return true
}

export function renderPackageJson(descriptor, currentJsonText) {
  const obj = JSON.parse(currentJsonText)
  for (const [dotted, value] of fieldPaths(descriptor)) {
    setPath(obj, dotted, value)
  }
  return JSON.stringify(obj, null, 2) + '\n'
}

export const packageJsonEmitter = {
  id: 'package-json',
  check(d, { root }) {
    const onDisk = fs.readFileSync(path.join(root, FILE), 'utf8')
    const obj = JSON.parse(onDisk)
    for (const [dotted, expected] of fieldPaths(d)) {
      const actual = getPath(obj, dotted)
      if (JSON.stringify(actual) !== JSON.stringify(expected)) {
        return { ok: false, detail: `${dotted}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}` }
      }
    }
    return { ok: true }
  },
  write(d, { root }) {
    const p = path.join(root, FILE)
    const src = fs.readFileSync(p, 'utf8')
    const next = renderPackageJson(d, src)
    if (next === src) return { changed: false }
    fs.writeFileSync(p, next)
    return { changed: true, detail: p }
  }
}
