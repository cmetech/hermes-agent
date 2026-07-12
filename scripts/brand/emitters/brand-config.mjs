// scripts/brand/emitters/brand-config.mjs
//
// Desktop rebrand-config emitter for apps/desktop/brand.config.json.
//
// NOTE on approach: this is FORMAT-PRESERVING, not a JSON.parse ->
// JSON.stringify reserialization (that reflows formatting - e.g. it expands
// the single-line `["\\bHermes\\b", "OTTO"]` rule pairs onto multiple lines
// - which dirties the tree on every otherwise-no-op build). Instead:
//   - `check` is PATH-BASED: parse the on-disk file and assert `name` and
//     `rules` equal the descriptor's expected values.
//   - `renderBrandConfig` does TARGETED in-place string replacement of just
//     (a) the top-level `"name"` value and (b) the replacement (2nd)
//     element of each `rules` pair (the pattern side, `\\bHermes\\b` /
//     `\\bHERMES\\b`, is upstream-invariant and untouched). Every other
//     byte - `protect`, all `$comment`/`$note` keys, whitespace, line
//     breaks - is left exactly as found. On a tree that already has the
//     descriptor's values, `write` is a byte-for-byte no-op.
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

// JSON-escape a string for insertion between existing quotes in raw JSON
// text (i.e. the escaped contents without the surrounding quote chars).
function jsonInner(s) {
  return JSON.stringify(s).slice(1, -1)
}

export function renderBrandConfig(descriptor, currentJsonText) {
  const value = jsonInner(descriptor.displayName)
  let next = currentJsonText

  // (a) top-level "name" value. Anchored to start-of-line so this can never
  // match "name" appearing inside a $comment/$note string value.
  next = next.replace(/^(\s*"name"\s*:\s*")(?:\\.|[^"\\])*(")/m, (_m, pre, post) => `${pre}${value}${post}`)

  // (b) the replacement (2nd) element of each rules pair. The pattern side
  // (raw text `\\bHermes\\b` / `\\bHERMES\\b`, i.e. two literal backslashes
  // either side of the case) is matched verbatim and left untouched.
  next = next.replace(/(\\\\bHermes\\\\b"\s*,\s*")(?:\\.|[^"\\])*(")/, (_m, pre, post) => `${pre}${value}${post}`)
  next = next.replace(/(\\\\bHERMES\\\\b"\s*,\s*")(?:\\.|[^"\\])*(")/, (_m, pre, post) => `${pre}${value}${post}`)

  return next
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
