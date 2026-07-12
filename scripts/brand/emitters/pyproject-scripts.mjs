// scripts/brand/emitters/pyproject-scripts.mjs
import fs from 'node:fs'
import path from 'node:path'

const FILE = 'pyproject.toml'
const ANCHOR = 'hermes-acp = "acp_adapter.entry:main"\n'

export function hasBrandScripts(source, slug) {
  return source.includes(`\n${slug} = "hermes_cli.main:main"`)
}

// Brand-templated comment (real em-dash, U+2014) that precedes the additive
// script aliases — reproduces the PRECUT otto block byte-for-byte when
// displayName/slug are substituted in.
function brandBlock(displayName, slug) {
  return (
    `# ${displayName} branding — same entry points under the ${displayName} command name. The \`hermes*\`\n` +
    `# commands are kept intact (the desktop backend spawns \`hermes serve\` and\n` +
    `# upstream merges expect them); \`${slug}*\` are additive aliases.\n` +
    `${slug} = "hermes_cli.main:main"\n` +
    `${slug}-agent = "run_agent:main"\n` +
    `${slug}-acp = "acp_adapter.entry:main"\n`
  )
}

export function addBrandScripts(source, slug, displayName) {
  if (hasBrandScripts(source, slug)) return source
  const block = `${ANCHOR}${brandBlock(displayName, slug)}`
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
    const next = addBrandScripts(src, d.slug, d.displayName)
    if (next === src) return { changed: false }
    fs.writeFileSync(p, next)
    return { changed: true, detail: p }
  }
}
