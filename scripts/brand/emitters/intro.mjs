// scripts/brand/emitters/intro.mjs
//
// Chat intro splash emitter for apps/desktop/src/components/chat/intro.tsx.
//
// The wordmark and tagline are real source edits (not build-time branding —
// see the workspace CLAUDE.md "Desktop UI branding is BUILD-TIME" note and
// the intro.tsx row in the OTTO customization surface table). This emitter
// sets/checks the two `const` declarations directly:
//   const WORDMARK = 'OTTO COWORKER'                (single-quoted)
//   const TAGLINE = "OTTO orchestrates ... there."   (double-quoted, may
//                                                      contain an em-dash
//                                                      and an apostrophe)

import fs from 'node:fs'
import path from 'node:path'

const INTRO_FILE = 'apps/desktop/src/components/chat/intro.tsx'

// Neutral (upstream `main`) wordmark, and a neutral Hermes-worded tagline
// for the inverse of write(). main has no WORDMARK/TAGLINE consts at all
// (see the OTTO customization surface table); this is the defined neutral
// target for THIS emitter's two anchors, keeping the `const TAGLINE = "…"`
// declaration in place (per the neutralization task brief) rather than
// removing it.
export const NEUTRAL_WORDMARK = 'HERMES AGENT'
export const NEUTRAL_TAGLINE =
  "Hermes orchestrates your thoughts and tasks into effective outcomes — tell me what you need and I'll take it from there."

// Matches: const WORDMARK = '...'  (single-quoted). setWordmark() escapes `'`
// and `\` before substituting so the emitted line is always syntactically
// valid TS. NOTE: this regex's `[^']*` does NOT tolerate an escaped `\'`
// inside the captured value, so hasWordmark()/setWordmark() are not
// guaranteed to round-trip a wordmark that itself contains a `'` — only to
// emit valid syntax for it. No real brand's wordmark contains a quote today.
const WORDMARK_RE = /(const WORDMARK = ')([^']*)(')/

// Matches: const TAGLINE = "..."  (double-quoted). The value may contain an
// em-dash and apostrophes but not an unescaped double quote, so capturing
// up to the next `"` is safe and matches the exact current line.
const TAGLINE_RE = /(const TAGLINE = ")([^"]*)(")/

export function hasWordmark(source, wordmark) {
  const m = source.match(WORDMARK_RE)
  if (!m) return false
  return m[2] === wordmark
}

function escapeForSingleQuotedString(value) {
  return value.replace(/\\/g, '\\\\').replace(/'/g, "\\'")
}

export function setWordmark(source, wordmark) {
  if (!WORDMARK_RE.test(source)) return source
  const escaped = escapeForSingleQuotedString(wordmark)
  return source.replace(WORDMARK_RE, (_all, a, _val, c) => `${a}${escaped}${c}`)
}

export function hasTagline(source, tagline) {
  const m = source.match(TAGLINE_RE)
  if (!m) return false
  return m[2] === tagline
}

function escapeForDoubleQuotedString(value) {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
}

export function setTagline(source, tagline) {
  if (!TAGLINE_RE.test(source)) return source
  const escaped = escapeForDoubleQuotedString(tagline)
  return source.replace(TAGLINE_RE, (_all, a, _val, c) => `${a}${escaped}${c}`)
}

export const introEmitter = {
  id: 'intro',
  check(d, { root }) {
    const src = fs.readFileSync(path.join(root, INTRO_FILE), 'utf8')
    if (!hasWordmark(src, d.wordmark)) {
      return { ok: false, detail: `WORDMARK is not '${d.wordmark}'` }
    }
    if (!hasTagline(src, d.tagline)) {
      return { ok: false, detail: `TAGLINE is not "${d.tagline}"` }
    }
    return { ok: true }
  },
  write(d, { root }) {
    const p = path.join(root, INTRO_FILE)
    const src = fs.readFileSync(p, 'utf8')
    let next = setWordmark(src, d.wordmark)
    next = setTagline(next, d.tagline)
    if (next === src) return { changed: false }
    fs.writeFileSync(p, next)
    return { changed: true, detail: p }
  },
  neutralize(_d, { root, dryRun = false } = {}) {
    const p = path.join(root, INTRO_FILE)
    const src = fs.readFileSync(p, 'utf8')
    let next = setWordmark(src, NEUTRAL_WORDMARK)
    next = setTagline(next, NEUTRAL_TAGLINE)
    if (next === src) return { changed: false }
    if (!dryRun) fs.writeFileSync(p, next)
    return { changed: true, detail: p }
  }
}
