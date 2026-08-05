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

/**
 * Hermes-managed Node.js directories, in preferred lookup order.
 *
 * There are two on-disk layouts. `scripts/install.ps1` unpacks portable Node
 * straight into `%LOCALAPPDATA%\hermes\node` (node.exe at the root, no `bin\`);
 * `scripts/install.sh` and the node-bootstrap helper use the POSIX
 * `$HERMES_HOME/node/bin`. Emit BOTH on every platform so mixed and migrated
 * installs resolve, leading with the layout native to the current platform.
 *
 * This is the single source of truth for the ordering rule on the Node side —
 * `main.ts` imports it rather than keeping its own copy. Mirrors
 * `iter_hermes_node_dirs()` in hermes_constants.py, which the Electron main
 * process cannot import.
 */
function hermesManagedNodePathEntries(
  hermesHome,
  { platform = process.platform, pathModule = pathModuleForPlatform(platform) }: any = {}
) {
  if (!hermesHome) {
    return []
  }

  const root = pathModule.join(hermesHome, 'node')
  const bin = pathModule.join(root, 'bin')

  return platform === 'win32' ? [root, bin] : [bin, root]
}

function buildDesktopBackendPath({
  hermesHome,
  venvRoot,
  currentPath = '',
  platform = process.platform,
  pathModule = pathModuleForPlatform(platform)
}: any = {}) {
  const delimiter = delimiterForPlatform(platform)
  const hermesNodeDirs = hermesManagedNodePathEntries(hermesHome, { platform, pathModule })
  const venvBin = venvRoot ? pathModule.join(venvRoot, platform === 'win32' ? 'Scripts' : 'bin') : null
  const saneEntries = platform === 'win32' ? [] : POSIX_SANE_PATH_ENTRIES

  return appendUniquePathEntries([hermesNodeDirs, venvBin, currentPath, saneEntries], { delimiter })
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
    // Force PEP 540 UTF-8 mode in the spawned Python backend so its stdio and
    // subprocess defaults are UTF-8 even on non-UTF-8 Windows locales (GBK,
    // cp1252, ...). hermes_bootstrap sets this inside the child too, but only
    // after import — anything emitted earlier (interpreter startup errors,
    // pre-bootstrap tracebacks) still decodes with the locale default without
    // this. User's explicit setting wins. Re-port of PR #56499 (echoriver89).
    PYTHONUTF8: currentEnv?.PYTHONUTF8 ?? '1',
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
  hermesManagedNodePathEntries,
  INHERITED_PYTHON_ENV_VARS,
  normalizeHermesHomeRoot,
  pathEnvKey,
  POSIX_SANE_PATH_ENTRIES,
  scrubInheritedPythonEnv
}
