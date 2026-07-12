// scripts/brand/create-clone.mjs
//
// Stamp a new brand off the neutral `base` branch. Local-only: writes the
// descriptor, creates the <slug> brand branch, runs the generator, sets the
// brand/active marker, commits, and stages ../<slug>-releases/. It performs NO
// push, NO `gh`, NO network action — those are human-confirmed follow-ups the
// create-clone SKILL documents.
import { fileURLToPath, pathToFileURL } from 'node:url'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

const SLUG_RE = /^[a-z][a-z0-9-]*$/
export const RESERVED_SLUGS = new Set(['base', 'default', 'mono'])

export function buildDescriptor(slug, { wordmark, tagline } = {}) {
  if (!SLUG_RE.test(slug)) throw new Error(`invalid slug: ${JSON.stringify(slug)} (must match ${SLUG_RE})`)
  if (RESERVED_SLUGS.has(slug)) throw new Error(`reserved slug: ${slug}`)
  const displayName = slug.toUpperCase()
  return {
    slug,
    displayName,
    wordmark: wordmark || `${displayName} COWORKER`,
    tagline: tagline || `${displayName} orchestrates your thoughts and tasks into effective outcomes.`,
    appId: `io.cmetech.${slug}`,
    scheme: slug,
    homeDir: `.${slug}`,
    releasesRepo: `cmetech/${slug}`,
    updateCommand: `${slug} update`,
    theme: 'otto',
    gateway: 'otto',
    curation: { skills: { exclude: [], disabledByDefault: [] }, tools: { excludeToolsets: [], disabledByDefault: [] } },
    capabilitySets: [],
    personaSets: [],
    cli: { bannerLogo: '', bannerHero: '' }
  }
}

// Ordered, case-sensitive. Longest / most-specific tokens first so a broad
// `OTTO`→display swap never eats `OTTO.app` / `OTTO-` / `cmetech/otto`, and so
// lowercase shared refs (`cmetech/hermes-agent`, `otto-desktop-release-install`,
// `otto_hermes`) are never touched.
function swapPairs(slug, display) {
  return [
    ['cmetech/otto', `cmetech/${slug}`],
    ['cmetech/hermes-agent@otto', `cmetech/hermes-agent@${slug}`],
    ['hermes-agent@otto', `hermes-agent@${slug}`],
    ['ref=otto', `ref=${slug}`],
    ['OTTO.AppImage', `${display}.AppImage`],
    ['OTTO.app', `${display}.app`],
    ['OTTO-', `${display}-`],
    ['OTTO Desktop', `${display} Desktop`],
    ['OTTO', display],
    ['[otto]', `[${slug}]`],
    ['otto-installer', `${slug}-installer`],
    ['otto-release-', `${slug}-release-`],
    ['otto-${{ matrix.os }}', `${slug}-\${{ matrix.os }}`],
    ['default: "otto"', `default: "${slug}"`],
    ['`otto` branch', '`' + slug + '` branch']
  ]
}

export function applyReleaseSwaps(content, { slug, displayName }) {
  let out = content
  for (const [from, to] of swapPairs(slug, displayName)) out = out.split(from).join(to)
  return out
}

const RELEASE_FILES = ['install.sh', 'install.ps1', 'README.md', path.join('.github', 'workflows', 'release.yml')]

export function stageReleases({ repoRoot, releasesDir, srcReleasesDir, slug, displayName }) {
  const src = srcReleasesDir || path.join(repoRoot, '..', 'otto-releases')
  const written = []
  for (const rel of RELEASE_FILES) {
    const srcFile = path.join(src, rel)
    if (!fs.existsSync(srcFile)) continue
    const swapped = applyReleaseSwaps(fs.readFileSync(srcFile, 'utf8'), { slug, displayName })
    const dstFile = path.join(releasesDir, rel)
    fs.mkdirSync(path.dirname(dstFile), { recursive: true })
    fs.writeFileSync(dstFile, swapped)
    written.push(dstFile)
  }
  return written
}

function git(root, args) {
  return execFileSync('git', args, { cwd: root, encoding: 'utf8' }).trim()
}
function branchExists(root, name) {
  try { git(root, ['rev-parse', '--verify', '--quiet', `refs/heads/${name}`]); return true } catch { return false }
}

export async function main(argv) {
  const flags = argv.filter(a => a.startsWith('--'))
  const positional = argv.filter(a => !a.startsWith('--'))
  const slug = positional[0]
  const wordmark = positional[1]
  const tagline = positional[2]
  if (!slug) throw new Error('usage: create-clone <slug> [wordmark] [tagline] [--releases-dir <path>] [--force]')
  const force = flags.includes('--force')
  const relIdx = argv.indexOf('--releases-dir')
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
  const descriptor = buildDescriptor(slug, { wordmark, tagline })
  const releasesDir = relIdx >= 0 ? path.resolve(argv[relIdx + 1]) : path.join(root, '..', `${slug}-releases`)

  // Preconditions
  if (git(root, ['status', '--porcelain']) !== '') throw new Error('working tree is not clean; commit or stash first')
  if (!branchExists(root, 'base')) throw new Error('base branch not found (create-clone stamps off base)')

  // 1) brand branch off base (or checkout existing)
  if (branchExists(root, slug)) git(root, ['checkout', slug])
  else git(root, ['checkout', '-b', slug, 'base'])

  // 2) descriptor (never clobber a hand-tuned one unless --force)
  const descFile = path.join(root, 'brands', `${slug}.json`)
  const wroteDesc = force || !fs.existsSync(descFile)
  if (wroteDesc) fs.writeFileSync(descFile, JSON.stringify(descriptor, null, 2) + '\n')

  // 3) generate overlay + active marker
  execFileSync('node', [path.join(root, 'scripts', 'brand', 'generate.mjs'), slug, '--write'], { cwd: root, stdio: 'inherit' })
  fs.writeFileSync(path.join(root, 'brand', 'active'), `${slug}\n`)

  // 4) commit (generic tool commit — no personal trailers). Skip if nothing changed.
  git(root, ['add', '-A'])
  if (git(root, ['status', '--porcelain']) !== '') {
    git(root, ['commit', '-m', `feat(brand): ${slug} brand branch (base + generated overlay)`])
  }

  // 5) gate
  execFileSync('node', [path.join(root, 'scripts', 'brand', 'generate.mjs'), slug, '--check'], { cwd: root, stdio: 'inherit' })

  // 6) stage releases
  const written = stageReleases({ repoRoot: root, releasesDir, slug, displayName: descriptor.displayName })

  // 7) next steps
  console.log(`\n✔ ${slug} brand branch ready (local). Staged releases → ${releasesDir}`)
  console.log('\nNEXT STEPS (human-confirmed, not run by this tool):')
  console.log(`  a) Fill in cli.bannerLogo / cli.bannerHero in brands/${slug}.json, then: node scripts/brand/generate.mjs ${slug} --write && git commit -am "${slug}: banner art"`)
  console.log(`  b) git push origin ${slug}`)
  console.log(`  c) gh repo create ${descriptor.releasesRepo} --public`)
  console.log(`  d) cd ${releasesDir} && git init && git add -A && git commit -m "init ${slug} releases" && git push`)
  console.log(`  e) cut the first prerelease via release.yml (gh workflow run)`)
  console.log(`  f) verify the artifact stamp (slug/branch/productVersion)`)
  console.log(`\nStaged files:\n${written.map(w => '  ' + w).join('\n')}`)
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2)).catch(err => { console.error(String(err.message || err)); process.exit(1) })
}
