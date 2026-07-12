// scripts/brand/emitters/auth-noauth.mjs
import fs from 'node:fs'
import path from 'node:path'

const AUTH_FILE = 'hermes_cli/auth.py'
// Matches an already-converted tuple form: provider_id in ("lmstudio", "otto", ...):
const TUPLE_RE = /(provider_id in \()("lmstudio"[^)]*)(\):)/
// Matches the upstream scalar form of the no-auth branch specifically (anchored on the
// `not api_key and ` prefix so this can NEVER match the unrelated base-url normalization
// check a few lines later in auth.py, which is also `provider_id == "lmstudio":` but with
// no `not api_key and` prefix).
const SCALAR_RE = /not api_key and provider_id == "lmstudio":/

export function hasSlugInNoauth(source, slug) {
  const m = source.match(TUPLE_RE)
  if (!m) return false
  return m[2].includes(`"${slug}"`)
}

export function addSlugToNoauth(source, slug) {
  if (hasSlugInNoauth(source, slug)) return source
  if (TUPLE_RE.test(source)) {
    return source.replace(TUPLE_RE, (_all, a, tuple, c) => `${a}${tuple}, "${slug}"${c}`)
  }
  if (SCALAR_RE.test(source)) {
    return source.replace(SCALAR_RE, `not api_key and provider_id in ("lmstudio", "${slug}"):`)
  }
  return source
}

// Inverse of addSlugToNoauth(): removes `slug` from the tuple. When the only
// entry left is "lmstudio" (the upstream-neutral case), collapses the whole
// tuple form back to the upstream scalar `provider_id == "lmstudio":` — the
// exact byte-for-byte inverse of the scalar->tuple branch of addSlugToNoauth.
// If other entries remain (a future multi-brand tree), the tuple is rebuilt
// with just `slug` removed. No-op if `slug` isn't present or the branch is
// already scalar.
export function removeSlugFromNoauth(source, slug) {
  const m = source.match(TUPLE_RE)
  if (!m) return source
  const items = m[2].split(',').map(s => s.trim()).filter(Boolean)
  const idx = items.indexOf(`"${slug}"`)
  if (idx === -1) return source
  items.splice(idx, 1)
  if (items.length === 1 && items[0] === '"lmstudio"') {
    return source.replace(TUPLE_RE, 'provider_id == "lmstudio":')
  }
  const rebuiltTuple = items.join(', ')
  return source.replace(TUPLE_RE, (_all, a, _tuple, c) => `${a}${rebuiltTuple}${c}`)
}

export const authNoauthEmitter = {
  id: 'auth-noauth',
  check(d, { root }) {
    const src = fs.readFileSync(path.join(root, AUTH_FILE), 'utf8')
    return hasSlugInNoauth(src, d.slug)
      ? { ok: true }
      : { ok: false, detail: `${d.slug} not in auth.py no-auth tuple` }
  },
  write(d, { root }) {
    const p = path.join(root, AUTH_FILE)
    const src = fs.readFileSync(p, 'utf8')
    const next = addSlugToNoauth(src, d.slug)
    if (next === src) return { changed: false }
    fs.writeFileSync(p, next)
    return { changed: true, detail: p }
  },
  neutralize(d, { root, dryRun = false } = {}) {
    const p = path.join(root, AUTH_FILE)
    const src = fs.readFileSync(p, 'utf8')
    const next = removeSlugFromNoauth(src, d.slug)
    if (next === src) return { changed: false }
    if (!dryRun) fs.writeFileSync(p, next)
    return { changed: true, detail: p }
  }
}
