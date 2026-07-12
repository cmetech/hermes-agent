# OTTO Desktop — Install & Build Guide

How the OTTO desktop app is built, released, and installed. Read this first if you're
new to the project and need to understand the distribution model.

---

## TL;DR

- OTTO releases are built by **CI in a separate, releases-only repo: [`cmetech/otto`](https://github.com/cmetech/otto)** (public).
- CI checks out the **source** from `cmetech/hermes-agent`@`otto`, builds the Windows + macOS installers, and publishes them as **GitHub Release assets** on `cmetech/otto`.
- A user downloads the installer, runs it, and on **first launch** the app clones the source and builds itself locally (see "The bootstrap model" below).
- Self-update = `git pull` + rebuild. There is **no** `electron-updater` (yet).

---

## The two repositories

| Repo | What it holds | Who writes to it |
|---|---|---|
| **`cmetech/hermes-agent`** @ `otto` branch | **All source code.** `main` tracks upstream Hermes; **our work lives on `otto`.** | Developers |
| **`cmetech/otto`** | **Releases only:** the release workflow, install/download scripts, README. The built installers live in this repo's **GitHub Releases**. No source. | CI (and maintainers) |

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

### To cut a release
```bash
# from anywhere with gh + access to cmetech/otto
gh workflow run release.yml -R cmetech/otto \
  -f ref=otto -f version=0.1.0 -f prerelease=true
gh run watch -R cmetech/otto   # follow progress
```
The artifacts land at `https://github.com/cmetech/otto/releases`.

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

| Surface | Where | Behavior |
|---|---|---|
| **Self-update branch** | `main.ts` `readDesktopUpdateConfig` | Fresh installs track the branch from `install-stamp.json` (`otto`), not the hardcoded `main`. Prevents the desktop from showing a phantom "update available" (comparing `otto` against upstream `main`) and from self-updating OTTO onto upstream. |
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
