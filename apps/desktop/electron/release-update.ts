/**
 * Pure helpers for the OTTO release-based update path.
 *
 * OTTO desktop is distributed as packaged installers (nsis/dmg) published to
 * cmetech/otto GitHub Releases. Unlike a source install (git clone + build,
 * self-updates via `git pull`), a *release* install should update by checking
 * the latest published release and downloading the next installer — the
 * git-pull path causes GUI/backend skew on a packaged binary.
 *
 * These helpers are intentionally free of Electron imports so they unit-test
 * under `node:test` / tsx. Network + IPC wiring lives in electron/main.ts.
 * See docs/otto-desktop-release-install.md and the workspace CLAUDE.md.
 */

export interface LatestRelease {
  version: string
  releaseUrl: string
  assetUrl: string | null
}

/**
 * A CI/release build is the only thing that carries `productVersion` in its
 * install-stamp (source rebuilds via `hermes update` don't set
 * $OTTO_PRODUCT_VERSION). Require `isPackaged` too, so a dev run that happens to
 * read a stray stamp never counts as a release install.
 */
export function isReleaseInstall(stamp: { productVersion?: string | null } | null, isPackaged: boolean): boolean {
  return Boolean(isPackaged && stamp && stamp.productVersion)
}

function normalize(v: string): number[] {
  return String(v)
    .replace(/^v/, '')
    .split('.')
    .map(n => Number.parseInt(n, 10) || 0)
}

/** Numeric (not lexical) compare of dotted versions. Tolerates a leading `v`. */
export function compareSemver(a: string, b: string): -1 | 0 | 1 {
  const pa = normalize(a)
  const pb = normalize(b)
  const len = Math.max(pa.length, pb.length)
  for (let i = 0; i < len; i++) {
    const da = pa[i] ?? 0
    const db = pb[i] ?? 0
    if (da > db) return 1
    if (da < db) return -1
  }
  return 0
}

function assetPattern(platform: string, arch: string): RegExp {
  const os = platform === 'win32' ? 'win' : platform === 'darwin' ? 'mac' : 'linux'
  const ext = platform === 'win32' ? 'exe' : platform === 'darwin' ? 'dmg' : 'AppImage'
  return new RegExp(`OTTO-.*-${os}-${arch}\\.${ext}$`)
}

/**
 * Pick the newest release (GitHub returns newest-first) and, within it, the
 * installer asset for this platform/arch. `assetUrl` is null when the newest
 * release has no matching asset (caller can still open the release page).
 */
export function parseLatestRelease(releases: unknown, platform: string, arch: string): LatestRelease | null {
  if (!Array.isArray(releases) || releases.length === 0) {
    return null
  }

  const rel = releases.find(r => r && typeof r.tag_name === 'string') || null
  if (!rel) {
    return null
  }

  const version = String(rel.tag_name).replace(/^v/, '')
  const pat = assetPattern(platform, arch)
  const asset = Array.isArray(rel.assets) ? rel.assets.find((a: { name?: string }) => pat.test(a?.name || '')) : null

  return {
    version,
    releaseUrl: rel.html_url || `https://github.com/cmetech/otto/releases/tag/${rel.tag_name}`,
    assetUrl: asset?.browser_download_url || null
  }
}

export interface ReleaseUpdateStatus {
  supported: true
  mode: 'release'
  currentVersion: string
  latestVersion: string | null
  updateAvailable: boolean
  releaseUrl: string | null
  assetUrl: string | null
  behind: number
  branch: string
}

/**
 * Shape a release-mode status for the update store. `behind` is 0/1 purely so
 * the footer's existing `behind > 0` styling keeps working; the UI keys off
 * `mode === 'release'` + `updateAvailable`.
 */
export function buildReleaseUpdateStatus(
  current: string,
  latest: LatestRelease | null,
  branch: string
): ReleaseUpdateStatus {
  const updateAvailable = Boolean(latest && compareSemver(latest.version, current) > 0)

  return {
    supported: true,
    mode: 'release',
    currentVersion: current,
    latestVersion: latest?.version ?? null,
    updateAvailable,
    releaseUrl: latest?.releaseUrl ?? null,
    assetUrl: latest?.assetUrl ?? null,
    behind: updateAvailable ? 1 : 0,
    branch
  }
}
