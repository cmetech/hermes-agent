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
  }
}
