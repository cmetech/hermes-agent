// scripts/brand/emitters/main-identity.mjs
import fs from 'node:fs'
import path from 'node:path'

const MAIN_TS_FILE = 'apps/desktop/electron/main.ts'

// Matches: const APP_NAME = process.env.HERMES_DESKTOP_APP_NAME || 'OTTO'
const APP_NAME_RE = /(const APP_NAME = process\.env\.HERMES_DESKTOP_APP_NAME \|\| ')([^']*)(')/
// Matches: app.setAppUserModelId('io.cmetech.otto')
const APP_ID_RE = /(app\.setAppUserModelId\(')([^']*)('\))/
// Matches: const OTTO_PROTOCOL = 'otto'  (const NAME is never rewritten — only the value)
const SCHEME_RE = /(const OTTO_PROTOCOL = ')([^']*)(')/
// Presence-only checks — these must never be mutated by this emitter.
const HERMES_PROTOCOL_RE = /const HERMES_PROTOCOL = 'hermes'/
const DEEP_LINK_PROTOCOLS_RE = /const DEEP_LINK_PROTOCOLS = \[OTTO_PROTOCOL, HERMES_PROTOCOL\]/

export function hasAppName(source, displayName) {
  const m = source.match(APP_NAME_RE)
  if (!m) return false
  return m[2] === displayName
}

export function setAppName(source, displayName) {
  if (!APP_NAME_RE.test(source)) return source
  return source.replace(APP_NAME_RE, (_all, a, _val, c) => `${a}${displayName}${c}`)
}

export function hasAppId(source, appId) {
  const m = source.match(APP_ID_RE)
  if (!m) return false
  return m[2] === appId
}

export function setAppId(source, appId) {
  if (!APP_ID_RE.test(source)) return source
  return source.replace(APP_ID_RE, (_all, a, _val, c) => `${a}${appId}${c}`)
}

export function hasScheme(source, scheme) {
  const m = source.match(SCHEME_RE)
  if (!m) return false
  return m[2] === scheme
}

export function setScheme(source, scheme) {
  if (!SCHEME_RE.test(source)) return source
  return source.replace(SCHEME_RE, (_all, a, _val, c) => `${a}${scheme}${c}`)
}

export const mainIdentityEmitter = {
  id: 'main-identity',
  check(d, { root }) {
    const src = fs.readFileSync(path.join(root, MAIN_TS_FILE), 'utf8')
    if (!hasAppName(src, d.displayName)) {
      return { ok: false, detail: `APP_NAME fallback is not '${d.displayName}'` }
    }
    if (!hasAppId(src, d.appId)) {
      return { ok: false, detail: `setAppUserModelId is not '${d.appId}'` }
    }
    if (!hasScheme(src, d.scheme)) {
      return { ok: false, detail: `OTTO_PROTOCOL value is not '${d.scheme}'` }
    }
    if (!HERMES_PROTOCOL_RE.test(src)) {
      return { ok: false, detail: `HERMES_PROTOCOL = 'hermes' missing (both schemes must be kept)` }
    }
    if (!DEEP_LINK_PROTOCOLS_RE.test(src)) {
      return { ok: false, detail: `DEEP_LINK_PROTOCOLS = [OTTO_PROTOCOL, HERMES_PROTOCOL] missing` }
    }
    return { ok: true }
  },
  write(d, { root }) {
    const p = path.join(root, MAIN_TS_FILE)
    const src = fs.readFileSync(p, 'utf8')
    let next = setAppName(src, d.displayName)
    next = setAppId(next, d.appId)
    next = setScheme(next, d.scheme)
    if (next === src) return { changed: false }
    fs.writeFileSync(p, next)
    return { changed: true, detail: p }
  }
}
