/**
 * Pure, Electron-free predicates for multi-brand coexistence on one machine.
 *
 * Two brands (e.g. OTTO + LOOP24) install to sibling per-brand homes under
 * %LOCALAPPDATA%\<brand>. Shared global state (the User PATH, a stale
 * HERMES_HOME) can leak across brands, making a second brand adopt the first
 * brand's backend clone. These helpers identify a path that belongs to a
 * DIFFERENT brand's %LOCALAPPDATA%\<seg> home so the resolver can reject it.
 *
 * Kept free of Electron/fs so it unit-tests under node:test / tsx. Windows
 * paths are compared string-wise (lower-cased, slash-normalized) so the tests
 * run on a POSIX dev box. Filesystem checks (does <home>\hermes-agent exist)
 * stay in main.ts. See the workspace CLAUDE.md and the design spec.
 */

function normalizeForCompare(p: string): string {
  return p.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
}

/**
 * The first path segment under `localAppData`. Returns null when the path is
 * not strictly under localAppData (external tool, other drive, POSIX home), or
 * when either input is empty.
 */
export function localAppDataBrandSegment(
  candidatePath: string | undefined | null,
  localAppData: string | undefined | null
): string | null {
  if (!candidatePath || !localAppData) {
    return null
  }

  const cand = normalizeForCompare(String(candidatePath))
  const base = normalizeForCompare(String(localAppData))

  if (!base) {
    return null
  }

  const prefix = `${base}/`

  if (!cand.startsWith(prefix)) {
    return null
  }

  const seg = cand.slice(prefix.length).split('/')[0]

  return seg || null
}

/**
 * True iff `candidate` and `ourHome` both resolve to a %LOCALAPPDATA%\<seg>
 * segment AND those segments differ — i.e. `candidate` lives under a different
 * brand's per-brand home than ours. False for our own home, external paths,
 * and any non-Windows path (localAppData undefined).
 */
export function isForeignBrandLocalAppDataPath(args: {
  candidate: string | undefined | null
  ourHome: string | undefined | null
  localAppData: string | undefined | null
}): boolean {
  const candSeg = localAppDataBrandSegment(args.candidate, args.localAppData)
  const ourSeg = localAppDataBrandSegment(args.ourHome, args.localAppData)

  return candSeg != null && ourSeg != null && candSeg !== ourSeg
}
