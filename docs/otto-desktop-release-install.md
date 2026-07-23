# OTTO Desktop — Install & Build Guide

How the OTTO desktop app is built, released, and installed. Read this first if you're
new to the project and need to understand the distribution model.

---

## TL;DR

- OTTO and LOOP24 releases are built by **CI in separate, releases-only repos:** [`cmetech/otto`](https://github.com/cmetech/otto) and [`cmetech/loop24`](https://github.com/cmetech/loop24).
- Each release workflow checks out its branded source branch from `cmetech/hermes-agent`, builds the Windows + macOS installers, and publishes them as GitHub Release assets in the matching releases repo.
- **Never create a product release or product-version tag in `cmetech/hermes-agent`.** It is the source repository, not the distribution repository. Its inherited PyPI, Vercel, and Docker release workflows are not the OTTO/LOOP24 desktop pipeline.
- A user downloads the installer, runs it, and on **first launch** the app clones the source and builds itself locally (see "The bootstrap model" below).
- Self-update = `git pull` + rebuild. There is **no** `electron-updater` (yet).

---

## Release safety invariant

The release flow used for `v1.1.6` and later paired branded releases is:

1. Finish and test shared work on neutral `base`.
2. Discover every brand from `brands/*.json`; do not hardcode only OTTO.
3. Merge the exact tested `base` commit into each brand branch, run
   `scripts/brand/generate.mjs <brand> --write`, and pass the generator,
   brand, workflow-merge, and build gates.
4. Push `base`, `otto`, and `loop24` forward-only.
5. Dispatch each brand's existing `release.yml` in its releases-only repo at
   the same version and prerelease state.
6. Monitor both runs and verify each release body names the expected source
   commit and each release contains the full Windows/macOS asset set.

| Brand | Source ref | Releases repo | Stamp branch | Artifact prefix |
|---|---|---|---|---|
| OTTO | `cmetech/hermes-agent@otto` | `cmetech/otto` | `otto` | `OTTO-` |
| LOOP24 | `cmetech/hermes-agent@loop24` | `cmetech/loop24` | `loop24` | `LOOP24-` |

Do not substitute any of these actions:

- Do not tag or publish `cmetech/hermes-agent` as the product release.
- Do not run its `Publish to PyPI` workflow for a branded desktop release.
- Do not treat a Vercel or Docker workflow triggered from that source repo as
  evidence that either branded installer was built.
- Do not build one brand and call the paired delivery complete unless the user
  explicitly excluded the other brand.
- Do not create a new installer script. The release repositories' existing
  downloader scripts select the installers produced by `release.yml`.

## The source and release repositories

| Repo | What it holds | Who writes to it |
|---|---|---|
| **`cmetech/hermes-agent`** | **All source code.** Shared work lands on neutral `base`; generated overlays live on `otto` and `loop24`. It is not a product-release repository. | Developers |
| **`cmetech/otto`** | **Releases only:** the release workflow, install/download scripts, README. The built installers live in this repo's **GitHub Releases**. No source. | CI (and maintainers) |
| **`cmetech/loop24`** | LOOP24 equivalent of `cmetech/otto`; its workflow checks out the `loop24` source branch and publishes `LOOP24-*` installers. | CI (and maintainers) |

Why split them? Three reasons:
1. **Clean public front door** — the releases repo is where users go to download; source churn stays out of it.
2. **No cross-repo token needed** — the workflow runs *in* `cmetech/otto`, so its built-in `GITHUB_TOKEN` can publish to its own Releases. Building from the (public) source needs no auth.
3. **Zero merge surface** — the release workflow adds **no files to the source repo**, so merging upstream Hermes into `otto` never conflicts with release tooling.

---

## The bootstrap model (the single most important concept)

**The OTTO `.dmg`/`.exe` is not a self-contained app.** It is a thin **Electron shell** plus one bundled metadata file, `install-stamp.json`, that pins the source `commit` + `branch`.

```
Download OTTO-<ver>-win-x64.exe  ──►  run installer  ──►  Electron shell installed
                                                              │
                                                    first launch triggers:
                                                              ▼
   bootstrap-runner.ts reads install-stamp.json (pinned commit + branch=otto)
       1. installs a managed uv into ~/.hermes/bin/uv.exe   (OTTO owns its own uv)
       2. git clone cmetech/hermes-agent @ otto  (at the pinned commit)
       3. uv creates the Python venv + installs the backend
       4. builds the desktop
       5. writes BOOTSTRAP_COMPLETE marker
   later launches skip bootstrap ──► resolveHermesBackend() ──► app runs
```

**Consequences:**
- **First launch needs a toolchain** (git / Python / Node) on the user's machine. OTTO provisions its own `uv`; the rest the user already has (this is fine for the OTTO audience — primarily Windows developers). The bootstrap streams progress in the UI; first launch takes a few minutes.
- **The full source lands on the user's machine** at `~/.hermes/...` — the binary just *bootstraps* it, it doesn't ship it.
- **Self-update = `git pull` the `otto` branch + rebuild.** So shipping an upstream bugfix to users is: merge `main`→`otto`, tag, and users pull. Same flow as before binaries existed.
- A **fully self-contained binary** (freezing the Python backend with PyInstaller) is intentionally **out of scope** — it's a large re-architecture and would break the git-pull update/merge model.

### Why the "OTTO fork" flips exist
For a downloaded OTTO binary to bootstrap **OTTO** (not upstream Hermes), four hardcoded
`NousResearch/hermes-agent` references were flipped to `cmetech/hermes-agent`. They are
listed in the OTTO customization surface table in the workspace `CLAUDE.md`:

- `apps/desktop/electron/bootstrap-runner.ts` — where the install script is downloaded from
- `scripts/install.ps1`, `scripts/install.sh` — the clone URL
- `apps/desktop/electron/update-remote.ts` — the self-update "official remote" recognition

The **branch** (`otto`) needs no code change — `install-stamp.json` carries it and the
bootstrap passes `--branch <stamp.branch>`. CI stamps `otto`.

---

## How a release is built (CI)

Workflow: **`cmetech/otto/.github/workflows/release.yml`**

- **Trigger:** manual `workflow_dispatch` with inputs:
  - `ref` — source branch/tag/SHA to build (default `otto`)
  - `version` — release version, e.g. `0.1.0` (tag becomes `v0.1.0`)
  - `prerelease` — publish as a prerelease/draft for testing (default `true`)
- **Runners (matrix):** `windows-latest` (nsis + msi) and `macos-latest` (dmg + zip). Free — the repos are public.
- **Per-runner steps:**
  1. checkout `cmetech/hermes-agent`@`<ref>` into `source/`
  2. setup Node + Python; install `uv`
  3. `npm ci` at the source root
  4. **stamp the build** with the source checkout's real commit + `branch=otto` (so the shell bootstraps the OTTO fork, regardless of the releases-repo ref that triggered the run)
  5. `cd apps/desktop && npm run dist:win` / `dist:mac`
  6. upload `apps/desktop/release/OTTO-*` to a GitHub Release on `cmetech/otto` (tag `v<version>`)

### To cut a paired branded release
```bash
# First prove the pushed source refs are the exact gated commits.
git fetch origin base otto loop24
git merge-base --is-ancestor origin/base origin/otto
git merge-base --is-ancestor origin/base origin/loop24
OTTO_SHA=$(git rev-parse origin/otto)
LOOP24_SHA=$(git rev-parse origin/loop24)

# Use the same version and prerelease state for every brand. The version input
# has no leading "v"; release.yml creates the v<version> tag. Pass the exact
# gated source SHA, as the v1.1.6 releases did, never a moving branch name.
gh workflow run release.yml -R cmetech/otto \
  -f ref="$OTTO_SHA" -f stamp_branch=otto -f version=2.0.0 -f prerelease=false
gh workflow run release.yml -R cmetech/loop24 \
  -f ref="$LOOP24_SHA" -f stamp_branch=loop24 -f version=2.0.0 -f prerelease=false

# Capture the two run URLs/IDs returned by GitHub, then monitor each exact run.
gh run watch <otto-run-id> -R cmetech/otto --exit-status
gh run watch <loop24-run-id> -R cmetech/loop24 --exit-status

# Verify the releases and their stamped source commits/assets.
gh release view v2.0.0 -R cmetech/otto
gh release view v2.0.0 -R cmetech/loop24
```
The artifacts land in the two brand release repositories, never in
`cmetech/hermes-agent`.

---

## How a user installs

### macOS
1. Download `OTTO-<ver>-mac-<arch>.dmg` from the release page.
2. Open it, drag **OTTO** to Applications.
3. First launch: because the build is **unsigned** for now, macOS Gatekeeper will warn —
   right-click the app → **Open**, then confirm. First launch bootstraps (a few minutes).

### Windows (primary target)
1. Download `OTTO-<ver>-win-x64.exe` (nsis) — or the `.msi` for silent/enterprise deploys.
2. Run it. Because the build is **unsigned**, SmartScreen shows "Windows protected your PC" →
   **More info → Run anyway**.
3. First launch bootstraps (installs managed uv, clones, builds — a few minutes).

### One-command install scripts (convenience)
`cmetech/otto` also ships downloader scripts that fetch the latest release asset:
```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/cmetech/otto/main/install.sh | sh
```
```powershell
# Windows
irm https://raw.githubusercontent.com/cmetech/otto/main/install.ps1 | iex
```
These are thin wrappers over the GitHub "latest release" API — they download the right
asset for the OS/arch and launch the platform installer.

---

## Building locally (for maintainers / debugging)

You rarely need this — CI is the source of truth — but to reproduce a build:
```bash
cd hermes-agent            # the source repo, on the otto branch
npm ci                     # root workspace install
cd apps/desktop
npm run dist:mac           # or dist:win on Windows
# artifacts → apps/desktop/release/OTTO-*
```
`npm run build` alone produces an unpacked app (no installer); `dist:*` produces the
installers. The `install-stamp.json` is written by `scripts/write-build-stamp.mjs` from the
current git commit/branch — so build from a clean `otto` checkout.

---

## Follow-ups (not in milestone ①)

- **Code signing / notarization.** Builds are currently **unsigned**.
  - *Windows:* an **EV code-signing cert** clears SmartScreen immediately; an OV cert builds
    reputation over time. For a managed fleet, the `.msi` pushed via **Intune/GPO** bypasses
    SmartScreen without a cert.
  - *macOS:* an Apple Developer ID cert + **notarization** clears Gatekeeper (the electron-builder
    `APPLE_API_KEY…` envs are already supported).
- **Tag-push trigger** for `release.yml` (in addition to manual dispatch).
- **`electron-updater`** for in-place auto-update (upgrade from the current `git pull` model).
- **Milestone ②:** a unified wrapper installer for desktop **+** `otto-gateway` with per-component upgrades.
- **Milestone ③:** extend `otto-tray` to start/stop the desktop app alongside the gateway.

---

## Branded update / About / uninstall surfaces

Real install testing surfaced several places that pointed at upstream Hermes. All are now branded:

### Two update modes

- **Release install** (packaged nsis/dmg — `install-stamp.json` has `productVersion`): the desktop checks the **latest `cmetech/otto` release** and, if newer, shows "Update available → **Update now**" which downloads the next installer. On the next launch after installing it, the backend clone **fast-forwards** to the shell's pinned commit (a one-time "updating…") so shell and backend stay paired. Logic: `isReleaseInstall` branches in `checkUpdates`/`applyUpdates`/`isBootstrapComplete` (see `electron/release-update.ts`).
- **Source install** (git clone + build): self-updates by `git pull` of the `otto` branch + rebuild (the original Hermes path), tracking the branch from the install-stamp.

| Surface | Where | Behavior |
|---|---|---|
| **Self-update branch** (source) | `main.ts` `readDesktopUpdateConfig` | Source installs track the branch from `install-stamp.json` (`otto`), not the hardcoded `main`. Prevents a phantom "update available" (comparing `otto` against upstream `main`) and self-updating OTTO onto upstream. |
| **Release notes** link | `about-settings.tsx` `RELEASE_NOTES_URL` | Opens `https://github.com/cmetech/otto/releases`. |
| **"See what's new"** | `updates-overlay.tsx` | Renders `git log HEAD..origin/otto` — the actual OTTO commits you'd pull. No config needed beyond the correct update branch. |
| **Uninstall** (Win) | `desktop-uninstall.ts` | The removable-dir guard is `/Hermes$/` in source but ships as `/OTTO$/` via the build transform, matching the `…\OTTO` install dir. `HERMES_HOME` drives data removal dynamically, so all 3 modes (GUI-only / +agent / everything) clean up correctly. |

### How to test release notes + "what's new"

- **Release notes** always shows in Settings → About. Click it → it should open the OTTO releases page. Ensure the release has a body (the CI workflow writes one).
- **"See what's new" / "Update now"** only appear when the install is *behind* `origin/otto`. To exercise them:
  1. Install a build (its stamp pins commit *C*).
  2. Push one more commit to `otto` (e.g. a `CHANGELOG.md` entry) so `origin/otto` is ahead of *C*.
  3. In the app, Settings → About → **Check now** → it shows "1 behind", and **See what's new** lists that commit; **Release notes** opens `cmetech/otto/releases`; **Update now** performs the real `git pull otto` + rebuild self-update.

  This is the intended end-to-end test of the corrected update path.

## See also
- Design of record: `docs/superpowers/specs/2026-07-12-otto-desktop-release-install-design.md`
- OTTO customization surface + merge rules: workspace `CLAUDE.md`
- Run OTTO from source (dev): `hermes-agent/DEV.md`
- Workflow v3.0.3 Windows release UAT:
  `docs/2026-07-23-workflow-v3.0.3-windows-uat.md`
