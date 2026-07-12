// scripts/brand/generate.mjs
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'
import { loadDescriptor } from './descriptor.mjs'
import { resolveActiveBrand } from './active.mjs'
import { providerEmitter } from './emitters/provider.mjs'
import { authNoauthEmitter } from './emitters/auth-noauth.mjs'
import { pyprojectScriptsEmitter } from './emitters/pyproject-scripts.mjs'
import { skinEmitter } from './emitters/skin.mjs'
import { packageJsonEmitter } from './emitters/package-json.mjs'
import { mainIdentityEmitter } from './emitters/main-identity.mjs'
import { brandConfigEmitter } from './emitters/brand-config.mjs'
import { introEmitter } from './emitters/intro.mjs'

export const DEFAULT_EMITTERS = [
  providerEmitter,
  authNoauthEmitter,
  pyprojectScriptsEmitter,
  skinEmitter,
  packageJsonEmitter,
  mainIdentityEmitter,
  brandConfigEmitter,
  introEmitter
]

export function runEmitters(descriptor, { root, mode, emitters = DEFAULT_EMITTERS }) {
  const results = emitters.map(e => {
    if (mode === 'check') {
      const { ok, detail } = e.check(descriptor, { root })
      return { id: e.id, ok, detail }
    }
    const { changed, detail } = e.write(descriptor, { root })
    return { id: e.id, changed, detail }
  })
  return { mode, results }
}

function repoRoot() {
  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
}

// Parses CLI args for the generator. The slug is OPTIONAL: any arg starting
// with "--" is a flag (--write / --check); the first non-flag arg is the
// slug. Flags and the slug may appear in either order. If no slug is given,
// it resolves to the active brand (env OTTO_BRAND > brand/active marker >
// 'otto' — see resolveActiveBrand). No flag, or any flag other than
// --write, means mode 'check'.
export function parseArgs(argv, { root }) {
  const flags = argv.filter(a => a.startsWith('--'))
  const positional = argv.filter(a => !a.startsWith('--'))
  const mode = flags.includes('--write') ? 'write' : 'check'
  const slug = positional[0] || resolveActiveBrand({ root })
  return { slug, mode }
}

async function main() {
  const root = repoRoot()
  const { slug, mode } = parseArgs(process.argv.slice(2), { root })
  const descriptor = loadDescriptor(slug, { root })
  const { results } = runEmitters(descriptor, { root, mode })
  for (const r of results) {
    const status = mode === 'check' ? (r.ok ? 'OK ' : 'XX ') : (r.changed ? '~~ ' : '== ')
    console.log(`${status}${r.id}${r.detail ? ' — ' + r.detail : ''}`)
  }
  if (mode === 'check' && results.some(r => !r.ok)) process.exit(1)
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(err => { console.error(err); process.exit(1) })
}
