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
import { homeEmitter } from './emitters/home.mjs'

export const DEFAULT_EMITTERS = [
  providerEmitter,
  authNoauthEmitter,
  pyprojectScriptsEmitter,
  skinEmitter,
  packageJsonEmitter,
  mainIdentityEmitter,
  brandConfigEmitter,
  introEmitter,
  homeEmitter
]

export function runEmitters(descriptor, { root, mode, emitters = DEFAULT_EMITTERS, write: applyWrite = false }) {
  const results = emitters.map(e => {
    if (mode === 'check') {
      const { ok, detail } = e.check(descriptor, { root })
      return { id: e.id, ok, detail }
    }
    if (mode === 'neutralize') {
      // dryRun by default: without --write, neutralize() computes what it
      // WOULD change but must not touch the filesystem.
      const { changed, detail } = e.neutralize(descriptor, { root, dryRun: !applyWrite })
      return { id: e.id, changed, detail }
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
// with "--" is a flag (--write / --check / --neutralize); the first
// non-flag arg is the slug. Flags and the slug may appear in either order.
// If no slug is given, it resolves to the active brand (env OTTO_BRAND >
// brand/active marker > 'otto' — see resolveActiveBrand). No flag, or any
// flag other than --write/--neutralize, means mode 'check'.
//
// --neutralize sets those same emitters to their neutral/upstream values
// (the inverse of --write) — brand-independent in spirit (the neutral
// values don't depend on the descriptor for most emitters), but a
// descriptor/slug is still resolved and loaded so provider/skin (which key
// off the slug to find what to remove) know which applied brand to strip.
// Without --write it is a dry run: emitters compute and report what they
// WOULD change but do not touch the filesystem. With --write, applies.
export function parseArgs(argv, { root }) {
  const flags = argv.filter(a => a.startsWith('--'))
  const positional = argv.filter(a => !a.startsWith('--'))
  const write = flags.includes('--write')
  const mode = flags.includes('--neutralize') ? 'neutralize' : (write ? 'write' : 'check')
  const slug = positional[0] || resolveActiveBrand({ root })
  return { slug, mode, write }
}

async function main() {
  const root = repoRoot()
  const { slug, mode, write } = parseArgs(process.argv.slice(2), { root })
  const descriptor = loadDescriptor(slug, { root })
  const { results } = runEmitters(descriptor, { root, mode, write })
  if (mode === 'neutralize' && !write) {
    console.log('(dry run — pass --write to apply)')
  }
  for (const r of results) {
    const status = mode === 'check' ? (r.ok ? 'OK ' : 'XX ') : (r.changed ? '~~ ' : '== ')
    console.log(`${status}${r.id}${r.detail ? ' — ' + r.detail : ''}`)
  }
  if (mode === 'check' && results.some(r => !r.ok)) process.exit(1)
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(err => { console.error(err); process.exit(1) })
}
