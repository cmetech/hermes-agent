// scripts/brand/generate.mjs
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { loadDescriptor } from './descriptor.mjs'
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

async function main() {
  const [slug, flag] = process.argv.slice(2)
  const mode = flag === '--write' ? 'write' : 'check'
  if (!slug) {
    console.error('usage: node generate.mjs <slug> [--check|--write]')
    process.exit(2)
  }
  const root = repoRoot()
  const descriptor = loadDescriptor(slug, { root })
  const { results } = runEmitters(descriptor, { root, mode })
  for (const r of results) {
    const status = mode === 'check' ? (r.ok ? 'OK ' : 'XX ') : (r.changed ? '~~ ' : '== ')
    console.log(`${status}${r.id}${r.detail ? ' — ' + r.detail : ''}`)
  }
  if (mode === 'check' && results.some(r => !r.ok)) process.exit(1)
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch(err => { console.error(err); process.exit(1) })
}
