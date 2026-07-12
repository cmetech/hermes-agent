// scripts/brand/emitters/pyproject-scripts.mjs
import fs from 'node:fs'
import path from 'node:path'

const FILE = 'pyproject.toml'
const ANCHOR = 'hermes-acp = "acp_adapter.entry:main"\n'

export function hasBrandScripts(source, slug) {
  return source.includes(`\n${slug} = "hermes_cli.main:main"`)
}

export function addBrandScripts(source, slug) {
  if (hasBrandScripts(source, slug)) return source
  const block =
    `${ANCHOR}` +
    `${slug} = "hermes_cli.main:main"\n` +
    `${slug}-agent = "run_agent:main"\n` +
    `${slug}-acp = "acp_adapter.entry:main"\n`
  return source.replace(ANCHOR, block)
}

export const pyprojectScriptsEmitter = {
  id: 'pyproject-scripts',
  check(d, { root }) {
    const src = fs.readFileSync(path.join(root, FILE), 'utf8')
    return hasBrandScripts(src, d.slug) ? { ok: true } : { ok: false, detail: `${d.slug} scripts missing` }
  },
  write(d, { root }) {
    const p = path.join(root, FILE)
    const src = fs.readFileSync(p, 'utf8')
    const next = addBrandScripts(src, d.slug)
    if (next === src) return { changed: false }
    fs.writeFileSync(p, next)
    return { changed: true, detail: p }
  }
}
