import path from 'node:path'

// Match the POSIX fallback surface used by the Python terminal environment.
// macOS apps launched from Finder/Dock often inherit only /usr/bin:/bin:/usr/sbin:/sbin,
// which misses Apple Silicon Homebrew and user-installed CLI tools such as codex.
const POSIX_SANE_PATH_ENTRIES = Object.freeze([
  '/opt/homebrew/bin',
  '/opt/homebrew/sbin',
  '/usr/local/sbin',
  '/usr/local/bin',
  '/usr/sbin',
  '/usr/bin',
  '/sbin',
  '/bin'
])

// Inherited Python environment that must never reach a Hermes Python process.
//
// A managed corporate baseline commonly sets PYTHONHOME (sometimes PYTHONPATH)
// at Machine scope. PYTHONHOME OVERRIDES an interpreter's own stdlib location,
// so every Python subprocess -- including uv's isolated build backend -- is
// dragged onto whatever stdlib that path holds. When the versions disagree the
// process dies with `AssertionError: SRE module mismatch` (the compiled _sre
// extension's MAGIC vs sre_compile.py's), which is what killed a first install
// of the desktop app on a laptop carrying both C:\Python311 and
// C:\Python\Python310.
//
// The user's shell is NOT the fix surface: the Electron app inherits
// Machine/User-scope environment directly, so clearing these in a terminal
// never reaches the backend it spawns. We deliver our own interpreter and run
// it against our own modules, so none of these has a legitimate use here.
//
// Mirrors the Python-family entries of _ENV_VAR_NAME_DENYLIST in
// hermes_cli/config.py -- keep the two lists in sync.
const INHERITED_PYTHON_ENV_VARS = Object.freeze([
  'PYTHONHOME',
  'PYTHONPATH',
  'PYTHONSTARTUP',
  'PYTHONEXECUTABLE',
  'PYTHONUSERBASE'
])

/**
 * Remove every inherited Python variable from `env` in place and disable user
 * site-packages, returning the names that were actually present.
 *
 * Called once on `process.env` at Electron startup so the ~15 spawn sites that
 * splat `{ ...process.env }` are all covered by construction -- a new spawn
 * site added later cannot reintroduce the leak.
 */
function scrubInheritedPythonEnv(env: Record<string, string | undefined> = process.env) {
  const removed: string[] = []

  for (const name of INHERITED_PYTHON_ENV_VARS) {
    if (env[name] !== undefined) {
      removed.push(name)
      delete env[name]
    }
  }

  // A poisoned PYTHONUSERBASE is gone above, but the DEFAULT user site dir can
  // hold incompatible builds too; the backend only ever needs our venv.
  env.PYTHONNOUSERSITE = '1'

  return removed
}

function delimiterForPlatform(platform = process.platform) {
  return platform === 'win32' ? ';' : ':'
}

function pathModuleForPlatform(platform = process.platform) {
  return platform === 'win32' ? path.win32 : path.posix
}

function pathEnvKey(env = process.env, platform = process.platform) {
  if (platform !== 'win32') {
    return 'PATH'
  }

  return Object.keys(env || {}).find(key => key.toUpperCase() === 'PATH') || 'PATH'
}

function currentPathValue(env = process.env, platform = process.platform) {
  const key = pathEnvKey(env, platform)

  return env?.[key] || ''
}

function appendUniquePathEntries(entries, { delimiter = path.delimiter } = {}) {
  const seen = new Set()
  const ordered = []

  for (const entry of entries) {
    if (!entry) {
      continue
    }

    const parts = Array.isArray(entry) ? entry : String(entry).split(delimiter)

    for (const part of parts) {
      if (!part || seen.has(part)) {
        continue
      }

      seen.add(part)
      ordered.push(part)
    }
  }

  return ordered.join(delimiter)
}

function buildDesktopBackendPath({
  hermesHome,
  venvRoot,
  currentPath = '',
  platform = process.platform,
  pathModule = pathModuleForPlatform(platform)
}: any = {}) {
  const delimiter = delimiterForPlatform(platform)
  const hermesNodeBin = hermesHome ? pathModule.join(hermesHome, 'node', 'bin') : null
  const venvBin = venvRoot ? pathModule.join(venvRoot, platform === 'win32' ? 'Scripts' : 'bin') : null
  const saneEntries = platform === 'win32' ? [] : POSIX_SANE_PATH_ENTRIES

  return appendUniquePathEntries([hermesNodeBin, venvBin, currentPath, saneEntries], { delimiter })
}

function normalizeHermesHomeRoot(hermesHome, { pathModule = pathModuleForPlatform(process.platform) }: any = {}) {
  if (!hermesHome) {
    return hermesHome
  }

  const resolved = pathModule.resolve(String(hermesHome))
  const parent = pathModule.dirname(resolved)

  if (pathModule.basename(parent).toLowerCase() === 'profiles') {
    return pathModule.dirname(parent)
  }

  return resolved
}

function buildDesktopBackendEnv({
  hermesHome,
  pythonPathEntries = [],
  venvRoot,
  currentEnv = process.env,
  platform = process.platform,
  pathModule = pathModuleForPlatform(platform)
}: any = {}) {
  const delimiter = delimiterForPlatform(platform)
  const key = pathEnvKey(currentEnv, platform)

  // Deliberately NOT `[...pythonPathEntries, currentEnv.PYTHONPATH]`: appending
  // the inherited value is what propagated a corporate PYTHONPATH into the
  // backend. See INHERITED_PYTHON_ENV_VARS. The explicit `undefined`s below are
  // belt-and-braces for callers that build a spawn env from something other
  // than the already-scrubbed process.env -- node's spawn skips undefined
  // values rather than exporting them empty, so this drops the variable.
  const scrubbed: Record<string, string | undefined> = {}

  for (const name of INHERITED_PYTHON_ENV_VARS) {
    scrubbed[name] = undefined
  }

  return {
    ...scrubbed,
    PYTHONNOUSERSITE: '1',
    PYTHONPATH: appendUniquePathEntries(pythonPathEntries, { delimiter }),
    [key]: buildDesktopBackendPath({
      hermesHome,
      venvRoot,
      currentPath: currentPathValue(currentEnv, platform),
      platform,
      pathModule
    })
  }
}

export {
  appendUniquePathEntries,
  buildDesktopBackendEnv,
  buildDesktopBackendPath,
  delimiterForPlatform,
  INHERITED_PYTHON_ENV_VARS,
  normalizeHermesHomeRoot,
  pathEnvKey,
  POSIX_SANE_PATH_ENTRIES,
  scrubInheritedPythonEnv
}
