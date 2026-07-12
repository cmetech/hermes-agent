// scripts/brand/emitters/brand-config.mjs
//
// Desktop rebrand-config emitter for apps/desktop/brand.config.json.
//
// NOTE on approach: this deliberately does NOT require byte-for-byte JSON
// reserialization (JSON.parse -> JSON.stringify is not guaranteed to
// reproduce source formatting exactly). Instead:
//   - `check` is PATH-BASED: parse the on-disk file and assert `name` and
//     `rules` equal the descriptor's expected values.
//   - `renderBrandConfig` parses the input text, sets exactly `name` and
//     `rules` from the descriptor, and PRESERVES `protect` plus every
//     `$comment`/`$note`/other key unchanged, then reserializes with
//     `JSON.stringify(obj, null, 2) + '\n'`. It is used by `write` to
//     produce a new brand's brand.config.json; it does not need to be
//     byte-identical to the source text, only valid JSON with the right
//     values and all other keys intact.
//
// `protect` is the list of FUNCTIONAL title-case "Hermes" strings that must
// never be rewritten (X-Hermes-Session-Token auth header, Hermes-Desktop
// User-Agent) - rewriting them breaks desktop<->backend auth. This emitter
// never touches `protect`.

import fs from 'node:fs'
import path from 'node:path'

const FILE = 'apps/desktop/brand.config.json'

function expectedRules(displayName) {
  return [
    ['\\bHermes\\b', displayName],
    ['\\bHERMES\\b', displayName]
  ]
}

export function renderBrandConfig(descriptor, currentJsonText) {
  const obj = JSON.parse(currentJsonText)
  obj.name = descriptor.displayName
  obj.rules = expectedRules(descriptor.displayName)
  return JSON.stringify(obj, null, 2) + '\n'
}

export const brandConfigEmitter = {
  id: 'brand-config',
  check(d, { root }) {
    const onDisk = fs.readFileSync(path.join(root, FILE), 'utf8')
    const obj = JSON.parse(onDisk)
    if (obj.name !== d.displayName) {
      return { ok: false, detail: `name: expected ${JSON.stringify(d.displayName)}, got ${JSON.stringify(obj.name)}` }
    }
    const expected = expectedRules(d.displayName)
    if (JSON.stringify(obj.rules) !== JSON.stringify(expected)) {
      return { ok: false, detail: `rules: expected ${JSON.stringify(expected)}, got ${JSON.stringify(obj.rules)}` }
    }
    return { ok: true }
  },
  write(d, { root }) {
    const p = path.join(root, FILE)
    const src = fs.readFileSync(p, 'utf8')
    const next = renderBrandConfig(d, src)
    if (next === src) return { changed: false }
    fs.writeFileSync(p, next)
    return { changed: true, detail: p }
  }
}
