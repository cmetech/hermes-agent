// scripts/brand/check-neutral.mjs
//
// Guards the invariant that the NEUTRAL branch stays brand-neutral.
//
// Why this exists: `apps/desktop/package.json`'s `build` script runs
// `scripts/brand/generate.mjs --write` as its FIRST step, so anything that
// builds the desktop app -- `pack`, `dist:*`, `test:desktop:*`, and therefore
// the `check:test:desktop:all` CI job -- stamps the active brand across ~16
// tracked files, including pyproject.toml, hermes_constants.py and both
// installers. `brand/active` reads `otto` on the neutral branch, so that stamp
// is an OTTO stamp.
//
// In CI that is harmless: the checkout is thrown away, and the branded build is
// exactly what `check:test:desktop:all` needs to validate. Locally it silently
// dirties the working tree, and a `git commit -a` afterwards would land an
// OTTO-stamped neutral branch. That is the failure this guard catches: the
// neutral branch is the merge base every brand rebuilds from, so once it stops
// being neutral, upstream merges stop being clean and every brand inherits the
// wrong values with no conflict and no test failure.
//
// Recovery when this fails locally:
//     node scripts/brand/generate.mjs --neutralize --write
//
// Only the neutral branch is checked. Brand branches are SUPPOSED to carry a
// committed stamp; `generate.mjs <brand> --check` is their gate, not this.
import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
const NEUTRAL_BRANCH = 'base'

function currentBranch() {
  // GITHUB_REF_NAME is set for both push and pull_request events; fall back to
  // git so the check behaves the same when run by hand.
  if (process.env.GITHUB_REF_NAME) return process.env.GITHUB_REF_NAME
  try {
    return execFileSync('git', ['rev-parse', '--abbrev-ref', 'HEAD'], {
      cwd: ROOT,
      encoding: 'utf8'
    }).trim()
  } catch {
    return ''
  }
}

const branch = currentBranch()

if (branch !== NEUTRAL_BRANCH) {
  console.log(
    `brand-neutral: skipped — on '${branch || 'unknown'}', not the neutral ` +
      `branch '${NEUTRAL_BRANCH}'. Brand branches are gated by ` +
      `\`generate.mjs <brand> --check\` instead.`
  )
  process.exit(0)
}

// Dry-run neutralization reports `==` for a surface already neutral and `~~`
// for one that would change. On the neutral branch every surface must be `==`.
let output
try {
  output = execFileSync(
    process.execPath,
    [path.join(ROOT, 'scripts', 'brand', 'generate.mjs'), '--neutralize'],
    { cwd: ROOT, encoding: 'utf8' }
  )
} catch (err) {
  console.error('brand-neutral: could not run the generator')
  console.error(err.stdout || err.message)
  process.exit(1)
}

const branded = output
  .split('\n')
  .filter(line => line.startsWith('~~'))
  .map(line => line.slice(2).trim())

if (branded.length > 0) {
  console.error(
    `\nbrand-neutral: FAIL — '${NEUTRAL_BRANCH}' carries a brand stamp on ` +
      `${branded.length} surface(s):\n`
  )
  for (const surface of branded) console.error(`  ~~ ${surface}`)
  console.error(
    '\nThis is almost always a desktop build (npm run pack / dist:* /' +
      ' test:desktop:*) that ran on the neutral branch and was committed by' +
      ' accident — the build stamps the active brand from brand/active before' +
      ' compiling.\n\nRestore neutrality with:\n' +
      '    node scripts/brand/generate.mjs --neutralize --write\n'
  )
  process.exit(1)
}

console.log(`brand-neutral: OK — '${NEUTRAL_BRANCH}' carries no brand stamp.`)
