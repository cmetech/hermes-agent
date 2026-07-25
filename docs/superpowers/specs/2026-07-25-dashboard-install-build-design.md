# Dashboard Install-Time Build Design

**Date:** 2026-07-25

**Status:** Approved for implementation planning

**Applies to:** `base` and the generated OTTO/LOOP24 brand branches

## Problem

The browser dashboard source and FastAPI backend are present in the managed
source checkout, but the production frontend bundle is not. The generated
`hermes_cli/web_dist/` directory is intentionally gitignored. On a fresh
OTTO/LOOP24 installation, the first `otto dashboard` or `loop24 dashboard`
therefore runs npm and builds the frontend before starting the server.

That behavior is correct as a recovery mechanism, but it makes a successfully
completed installation appear incomplete. It also moves network activity and
build latency into the first dashboard launch, where users reasonably expect
the product to be ready.

## Goals

- Build the browser dashboard during a fresh managed installation.
- Reuse the existing dashboard build implementation used by updates and
  dashboard startup.
- Preserve the current launch-time freshness check as self-healing recovery.
- Keep OTTO and LOOP24 on the same neutral implementation; brand command names
  remain aliases of `hermes_cli.main:main`.
- Preserve the narrow-waist architecture: this is CLI/install behavior, not a
  model tool or new core capability.
- Do not make an optional browser-dashboard build failure brick an otherwise
  usable CLI or Desktop installation.

## Non-Goals

- Shipping a second copy of the browser dashboard inside the Electron renderer.
- Replacing the source-clone bootstrap model with a self-contained backend.
- Removing npm or Node.js from source-based installs.
- Removing the dashboard's launch-time rebuild and recovery behavior.
- Changing Docker or Nix packaging, which already supplies a prebuilt
  `HERMES_WEB_DIST`.
- Redesigning the dashboard UI or backend API.

## Considered Approaches

### 1. Build locally during installation — selected

The managed installer builds the dashboard after Python and Node dependencies
are available. This matches the current source-based distribution and the
existing update behavior.

Advantages:

- Uses the exact source commit installed on the machine.
- Reuses the existing deterministic npm install and Vite build logic.
- Keeps release artifacts and Electron packaging unchanged.
- Produces the existing content-hash stamp, so later launches skip work.
- Works identically for neutral Hermes, OTTO, and LOOP24.

Cost:

- Installation takes longer and still requires npm registry access when the
  required packages are not cached.

### 2. Ship a precompiled dashboard in each Desktop release

CI could build the browser dashboard and add it to Electron `extraResources`.
The backend bootstrap would then copy or reference that bundle.

This improves first-install speed but couples the independently versioned
Electron shell to backend source assets, adds artifact-routing and compatibility
logic, and conflicts with the current source-clone update model. It is not
selected.

### 3. Keep build-on-first-dashboard-launch

This is the current behavior. It keeps installation shorter but causes
surprising post-install downloads and makes offline first use fail. It remains
only as a recovery path.

## Architecture

### Shared build entry point

Add a build-only mode to the existing dashboard command:

```text
hermes dashboard --build-only
otto dashboard --build-only
loop24 dashboard --build-only
```

`--build-only` calls the same `_build_web_ui(PROJECT_ROOT / "web", fatal=True)`
implementation used by dashboard startup and exits without:

- importing or starting the web server,
- opening a browser,
- loading dashboard plugins,
- starting MCP discovery, or
- creating a long-running process.

The mode exits `0` when the bundle is current or builds successfully and exits
nonzero when a required build cannot complete. It must be handled before the
normal FastAPI/server startup path so the operation remains narrowly scoped.

The build continues to:

1. Resolve Hermes-managed or system npm through the existing resolver.
2. Run the lockfile-preserving npm install for the `web` workspace.
3. Run `npm run build` in `web/`.
4. Produce `hermes_cli/web_dist/`.
5. Write `$HERMES_HOME/web-ui-build-stamp.json` with the source content hash.

No second npm command sequence is added to either installer.

### Installer stage

Add an idempotent `dashboard-build` stage to both canonical installer
implementations and their bundled copies:

- `scripts/install.sh`
- `hermes_cli/scripts/install.sh`
- `scripts/install.ps1`
- `hermes_cli/scripts/install.ps1`

The stage runs after Python dependencies and Node dependencies are installed,
and before the install/bootstrap completion marker. Where an optional Desktop
build is requested, the dashboard build should run before that larger Desktop
build so it has an independent progress and failure result.

Conceptual order:

```text
repository
→ virtual environment
→ Python dependencies
→ Node dependencies
→ dashboard-build
→ optional Desktop build
→ PATH/configuration
→ install-complete marker
```

The installer invokes the installed source directly through its managed Python,
because the public command shim may not be on `PATH` yet:

```text
<managed-python> -m hermes_cli.main dashboard --build-only
```

The stage must be listed in the manifest so the Electron bootstrap UI reports
dashboard preparation as its own step. The normal monolithic installer path
must call the same stage helper in the same order; this cannot be limited to
manifest-driven Desktop bootstrap.

### Update behavior

Both current update paths already call `_build_web_ui()`. They remain on the
same shared function and do not spawn a nested CLI process. This preserves
existing update output and avoids unnecessary subprocess layering.

The implementation should verify that both update call sites continue to:

- build when the source hash changes,
- skip when the dist and stamp are current, and
- leave the existing usable dist in place if a rebuild fails.

### Dashboard launch behavior

Normal `dashboard` startup retains its current freshness check. In the normal
case, the installer-created dist and stamp match, so startup performs no npm
work. If files are missing, stale, or manually deleted, startup retries the
build exactly as it does today.

`--skip-build` and caller-managed `HERMES_WEB_DIST` behavior are unchanged.

## Failure Handling

Dashboard preparation is expected to succeed, but it is an optional capability
relative to the core CLI and Desktop backend. A failure must therefore be
visible without making the whole installation unusable.

On build failure:

1. The build-only command exits nonzero and prints the existing actionable npm
   or Vite error guidance.
2. The installer stage converts that failure into an explicit
   successful-but-skipped/deferred stage result with a reason such as:
   `Dashboard build deferred; dashboard startup will retry automatically`.
3. Interactive installers print a prominent warning and the manual recovery
   command.
4. The remaining install stages continue and the bootstrap-complete marker may
   still be written.
5. The first normal dashboard launch retries the build.

Windows should use its existing `_StageSkippedReason` channel. POSIX should
map a nonzero `dashboard-build` stage result to
`ok: true, skipped: true, reason: ...` and return process status `0` to the
stage driver. That conversion belongs in the installer wrapper only; the
underlying build-only CLI remains strict and returns nonzero. The monolithic
POSIX installer similarly catches the helper failure, prints the warning, and
continues.

Missing Node.js follows the same deferred path. It must not be reported as a
successful dashboard build.

## Idempotency and Concurrency

- Re-running the installer is safe: `_web_ui_build_needed()` skips when the
  dist and content-hash stamp are current.
- Existing cross-process locking continues to serialize concurrent dashboard
  builds on POSIX.
- Windows retains the existing build behavior; this change does not introduce
  a second concurrency mechanism.
- The deterministic npm helper must continue to preserve `package-lock.json`.
- Installer execution must not leave the managed checkout dirty except for
  ignored build output and runtime stamp files.

## Testing

### CLI tests

- `dashboard --build-only` invokes `_build_web_ui(..., fatal=True)` once.
- A successful/current build exits zero.
- A failed build exits nonzero.
- Build-only mode never calls `start_server`, browser opening, plugin discovery,
  or MCP discovery.
- Existing normal `dashboard`, `--skip-build`, `HERMES_WEB_DIST`, `serve`, and
  lifecycle flag tests remain unchanged and passing.

### Installer tests

- POSIX and Windows manifests contain `dashboard-build` after Node dependencies
  and before completion/optional Desktop finalization.
- The stage invokes the managed Python interpreter rather than relying on
  `hermes`, `otto`, or `loop24` being on `PATH`.
- Success is reported as `ok: true, skipped: false`.
- Missing Node or a failed build is reported as
  `ok: true, skipped: true` with a useful reason.
- A deferred dashboard build does not prevent later stages or the completion
  marker.
- Root and bundled installer copies remain synchronized.

### Build integration tests

- With a temporary `HERMES_HOME` and controlled npm subprocesses, a successful
  build creates/recognizes the dist and writes a matching build stamp.
- A second invocation with unchanged sources performs no npm install or Vite
  build.
- A changed source hash triggers one rebuild.
- A failed rebuild with an existing dist preserves the stale-dist fallback.

### Brand coverage

Existing brand-emitter tests should continue proving that `otto` and `loop24`
are aliases of `hermes_cli.main:main`. No brand-specific dashboard
implementation or installer branch is introduced.

## Acceptance Criteria

- A fresh OTTO or LOOP24 managed installation whose dashboard-build stage
  succeeds finishes with `hermes_cli/web_dist/index.html` present.
- The matching `web-ui-build-stamp.json` exists under the active brand's
  `HERMES_HOME`.
- The first `otto dashboard` or `loop24 dashboard` launch performs no npm work
  when sources are unchanged.
- Installation remains usable when dashboard preparation is deferred.
- Dashboard startup retries and can recover a deferred or deleted build.
- Update behavior, Docker, Nix, Desktop `serve`, and caller-managed web dist
  behavior do not regress.
