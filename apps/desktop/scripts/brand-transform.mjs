// brand-transform.mjs — build-time branding for the OTTO desktop app.
//
// Rewrites the user-facing product name ("Hermes" -> brand name) in the BUILT
// output only. Source .ts/.tsx files are NEVER modified on disk, so merging
// upstream Hermes changes never produces branding conflicts. The single source
// of truth is apps/desktop/brand.config.json.
//
// Used by:
//   - vite.config.ts            (renderer: brandVitePlugin)
//   - scripts/bundle-electron-main.mjs (main process: applyBrand on output)
//
// Safety: rules are case-sensitive. The display word is title-case "Hermes";
// functional identifiers are lowercase (hermes_cli, .hermes), UPPER
// (HERMES_HOME), or camelCase-glued (resolveHermesBackend) and so never match
// \bHermes\b. Comments that contain "Hermes" may be rewritten but are stripped
// from the production bundle and never affect runtime.
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const configPath = resolve(here, '..', 'brand.config.json')

let _config = null

function loadConfig() {
  if (_config) return _config
  let raw
  try {
    raw = JSON.parse(readFileSync(configPath, 'utf8'))
  } catch {
    raw = { name: 'Hermes', rules: [] } // missing/invalid config -> no-op
  }
  const rules = Array.isArray(raw.rules) ? raw.rules : []
  _config = {
    name: raw.name || 'Hermes',
    compiled: rules
      .filter(r => Array.isArray(r) && r.length === 2)
      .map(([pattern, replacement]) => [new RegExp(pattern, 'g'), replacement]),
  }
  return _config
}

/** The configured brand name (e.g. "OTTO"). */
export function brandName() {
  return loadConfig().name
}

/**
 * Apply the branding rules to a chunk of source/bundle text.
 * No-op when the brand is stock "Hermes" or no rules are defined.
 */
export function applyBrand(code) {
  const cfg = loadConfig()
  if (!cfg.compiled.length || cfg.name === 'Hermes') return code
  let out = code
  for (const [re, replacement] of cfg.compiled) out = out.replace(re, replacement)
  return out
}

/**
 * Vite plugin — rewrites renderer source at build time (in memory only).
 * Scoped to apps/desktop/src, skips test files.
 */
export function brandVitePlugin({
  // Renderer source + imported data files (e.g. intro-copy.jsonl?raw). Allows a
  // trailing Vite query like ?raw. Case-sensitive rules keep data files safe.
  include = /[\\/]src[\\/].*\.(tsx?|jsx?|jsonl?)(\?[^?]*)?$/,
  exclude = /\.(test|spec)\.|[\\/]__tests__[\\/]/,
} = {}) {
  return {
    name: 'otto-brand',
    enforce: 'pre',
    transform(code, id) {
      if (!include.test(id) || exclude.test(id)) return null
      const out = applyBrand(code)
      return out === code ? null : { code: out, map: null }
    },
  }
}
