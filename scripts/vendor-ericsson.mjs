// Vendor the ericsson-capabilities set into this repo (base). Manifest-driven:
// reads sets/ericsson.json and copies exactly what it lists. See CLAUDE.md brand row.
import fs from 'node:fs'
import path from 'node:path'
import { execSync } from 'node:child_process'

function copyRec(src, dst) {
  fs.mkdirSync(path.dirname(dst), { recursive: true })
  const st = fs.statSync(src)
  if (st.isDirectory()) {
    fs.mkdirSync(dst, { recursive: true })
    for (const e of fs.readdirSync(src)) {
      if (['__pycache__', '.venv', '.pytest_cache', '.git'].includes(e)) continue
      copyRec(path.join(src, e), path.join(dst, e))
    }
  } else {
    fs.copyFileSync(src, dst)
  }
}

// mcpLocal dirs land under plugins/<basename>; skills/plugins/workflows keep their relative path.
export function vendor({ sourceDir, destRoot, sourceCommit }) {
  const manifest = JSON.parse(fs.readFileSync(path.join(sourceDir, 'sets/ericsson.json'), 'utf8'))
  const copyList = [
    ...(manifest.skills || []).map(rel => [rel, rel]),
    ...(manifest.plugins || []).map(rel => [rel, rel]),
    ...(manifest.mcpLocal || []).map(rel => [rel, path.posix.join('plugins', path.basename(rel))]),
    ...(manifest.workflows || []).map(rel => [rel, path.posix.join('capabilities/workflows', path.basename(rel))]),
  ]
  for (const [rel, destRel] of copyList) {
    const s = path.join(sourceDir, rel)
    if (!fs.existsSync(s)) throw new Error(`manifest lists missing path: ${rel}`)
    copyRec(s, path.join(destRoot, destRel))
  }
  // the mcpServers yaml fragment (glean template etc.)
  if (manifest.mcpServers) copyRec(path.join(sourceDir, manifest.mcpServers),
    path.join(destRoot, 'capabilities', path.basename(manifest.mcpServers)))
  // vendor the manifest itself + stamp the source commit; REWRITE workflow paths to the
  // vendored location so the seed (capability_staging) reads them from capabilities/workflows/.
  const vendored = { ...manifest, vendoredFrom: sourceCommit,
    mcpServersFile: path.basename(manifest.mcpServers || 'mcp-servers.yaml'),
    workflows: (manifest.workflows || []).map(rel => path.posix.join('capabilities/workflows', path.basename(rel))) }
  fs.mkdirSync(path.join(destRoot, 'capabilities'), { recursive: true })
  fs.writeFileSync(path.join(destRoot, 'capabilities/ericsson.json'), JSON.stringify(vendored, null, 2) + '\n')
}

function main() {
  const sourceDir = process.env.ERICSSON_CAPABILITIES_DIR
    || path.resolve(process.cwd(), '..', 'ericsson-capabilities')
  if (!fs.existsSync(path.join(sourceDir, 'sets/ericsson.json')))
    throw new Error(`ericsson-capabilities not found at ${sourceDir} (set ERICSSON_CAPABILITIES_DIR)`)
  let sourceCommit = 'unknown'
  try { sourceCommit = execSync('git rev-parse --short HEAD', { cwd: sourceDir }).toString().trim() } catch {}
  vendor({ sourceDir, destRoot: process.cwd(), sourceCommit })
  console.log(`vendored ericsson-capabilities @ ${sourceCommit} into ${process.cwd()}`)
}

if (import.meta.url === `file://${process.argv[1]}`) main()
