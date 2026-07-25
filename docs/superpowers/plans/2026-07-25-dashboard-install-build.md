# Dashboard Install-Time Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the browser dashboard during managed installation so the first `hermes dashboard`, `otto dashboard`, or `loop24 dashboard` launch does not download npm packages when the installed source is unchanged.

**Architecture:** Add a dashboard-only `--build-only` CLI mode that delegates to the existing `_build_web_ui(..., fatal=True)` path and exits before server initialization. Add an idempotent `dashboard-build` stage to the POSIX and Windows installers, call it with the install's managed Python after Python and Node dependencies are ready, and convert build failures into explicit deferred/skipped installer results while preserving the CLI's strict nonzero result.

**Tech Stack:** Python 3.11+, argparse, pytest, Bash, PowerShell 5.1+/pwsh, npm workspaces, Vite.

## Global Constraints

- Work on `base`; do not develop on literal `main`.
- Preserve the existing `_build_web_ui`, `_web_ui_build_needed`, content-hash stamp, lock, npm retry, stale-dist fallback, and update call sites. Do not add a second npm build implementation.
- Keep `--build-only` on `dashboard` only. `serve` remains headless and must never build or mount the SPA.
- Build-only must exit before FastAPI/uvicorn imports, profile rerouting, browser opening, skill sync, terminal config bridging, plugin discovery, MCP discovery, and `start_server`.
- Lifecycle actions retain precedence: `dashboard --status --build-only` reports status, and `dashboard --stop --build-only` stops processes.
- The build-only CLI is strict: missing npm or an unrecoverable build failure exits nonzero.
- Installer wrappers are tolerant: a dashboard build failure is visible but becomes `ok: true`, `skipped: true`, and does not block later stages or the completion marker.
- Invoke the installed source with its managed interpreter:
  `<managed-python> -m hermes_cli.main dashboard --build-only`.
  Do not depend on `hermes`, `otto`, or `loop24` already being on `PATH`.
- Apply equivalent dashboard-stage changes to both canonical and packaged installer copies:
  `scripts/install.sh`, `hermes_cli/scripts/install.sh`,
  `scripts/install.ps1`, and `hermes_cli/scripts/install.ps1`.
  These pairs already contain unrelated historical differences, so preserve
  those differences and test parity of the new dashboard contract rather than
  copying one whole file over the other.
- Keep PowerShell source ASCII-only for Windows PowerShell 5.1.
- Do not add brand-specific dashboard code. OTTO and LOOP24 continue to obtain
  this behavior through their `hermes_cli.main:main` aliases and brand-specific
  `HERMES_HOME`.
- Preserve unrelated working-tree changes, including the current
  `tools/browser_tool.py` modification.

## File Structure

### Modified production files

- `hermes_cli/subcommands/dashboard.py`
  - Register dashboard-only `--build-only`.
- `hermes_cli/main.py`
  - Dispatch build-only before all server/profile initialization.
- `scripts/install.sh`
  - Add the canonical POSIX `dashboard-build` stage, helper, manifest entry,
    deferred-result mapping, and monolithic call.
- `hermes_cli/scripts/install.sh`
  - Mirror the new POSIX dashboard-stage contract in the wheel-bundled copy.
- `scripts/install.ps1`
  - Add the canonical Windows dashboard build worker and manifest entry.
- `hermes_cli/scripts/install.ps1`
  - Mirror the new Windows dashboard-stage contract in the wheel-bundled copy.

### Modified/new test files

- `tests/hermes_cli/test_subcommands_batch.py`
  - Verify dashboard parses `--build-only` and serve does not expose it.
- `tests/hermes_cli/test_dashboard_build_only.py` (new)
  - Verify strict build-only dispatch and absence of server-side effects.
- `tests/test_install_dashboard_build_stage.py` (new)
  - Exercise both POSIX manifests and stage results, inspect both PowerShell
    manifests, verify managed-Python invocation, and enforce canonical/bundled
    parity for the new stage.
- `tests/hermes_cli/test_web_ui_build.py`
  - Add one integration contract proving a successful build writes the stamp
    and the next unchanged invocation performs no npm work.

---

## Task 1: Add the strict dashboard build-only CLI

**Files:**

- Modify: `hermes_cli/subcommands/dashboard.py`
- Modify: `hermes_cli/main.py`
- Modify: `tests/hermes_cli/test_subcommands_batch.py`
- Create: `tests/hermes_cli/test_dashboard_build_only.py`

**Interfaces:**

- Consumes: `_build_web_ui(PROJECT_ROOT / "web", fatal=True)`
- Produces: `hermes dashboard --build-only`
- Exit contract: `0` for current/successful build; `1` for unrecoverable build
- Must not affect: `hermes serve`, normal dashboard launch, `--skip-build`,
  `HERMES_WEB_DIST`, `--status`, or `--stop`

- [ ] **Step 1: Add failing parser coverage**

Extend `test_dashboard_builder_two_handlers()` in
`tests/hermes_cli/test_subcommands_batch.py`:

```python
def test_dashboard_builder_two_handlers():
    parser = argparse.ArgumentParser(prog="hermes")
    sub = parser.add_subparsers(dest="command")
    dash, reg = _h("dashboard"), _h("dashboard_register")
    build_dashboard_parser(sub, cmd_dashboard=dash, cmd_dashboard_register=reg)

    assert parser.parse_args(["dashboard"]).func is dash
    assert parser.parse_args(["dashboard", "register"]).func is reg

    build = parser.parse_args(["dashboard", "--build-only"])
    assert build.func is dash
    assert build.build_only is True

    with pytest.raises(SystemExit):
        parser.parse_args(["serve", "--build-only"])
```

- [ ] **Step 2: Add failing dispatch and isolation tests**

Create `tests/hermes_cli/test_dashboard_build_only.py`:

```python
from __future__ import annotations

import builtins
import types
from unittest.mock import Mock

import pytest

import hermes_cli.main as main_mod


def _args(**overrides):
    values = {
        "build_only": True,
        "status": False,
        "stop": False,
        "headless_backend": False,
        "host": "127.0.0.1",
        "port": 9119,
        "no_open": True,
    }
    values.update(overrides)
    return types.SimpleNamespace(**values)


def _forbid_server_imports(monkeypatch):
    original_import = builtins.__import__
    forbidden = {
        "fastapi",
        "uvicorn",
        "hermes_cli.web_server",
        "hermes_cli.plugins",
        "hermes_cli.mcp_startup",
    }

    def guarded_import(name, *args, **kwargs):
        if name in forbidden:
            raise AssertionError(f"build-only imported {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)


def test_build_only_calls_shared_builder_and_returns(monkeypatch):
    build = Mock(return_value=True)
    monkeypatch.setattr(main_mod, "_build_web_ui", build)
    sync_skills = Mock()
    monkeypatch.setattr(main_mod, "_sync_bundled_skills_quietly", sync_skills)
    _forbid_server_imports(monkeypatch)

    result = main_mod.cmd_dashboard(_args())

    assert result is None
    build.assert_called_once_with(main_mod.PROJECT_ROOT / "web", fatal=True)
    sync_skills.assert_not_called()


def test_build_only_exits_one_when_builder_fails(monkeypatch):
    build = Mock(return_value=False)
    monkeypatch.setattr(main_mod, "_build_web_ui", build)
    _forbid_server_imports(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        main_mod.cmd_dashboard(_args())

    assert exc.value.code == 1
    build.assert_called_once_with(main_mod.PROJECT_ROOT / "web", fatal=True)


def test_status_keeps_precedence_over_build_only(monkeypatch):
    build = Mock(return_value=True)
    monkeypatch.setattr(main_mod, "_build_web_ui", build)
    monkeypatch.setattr(main_mod, "_report_dashboard_status", lambda: 0)

    with pytest.raises(SystemExit) as exc:
        main_mod.cmd_dashboard(_args(status=True))

    assert exc.value.code == 0
    build.assert_not_called()
```

- [ ] **Step 3: Run the focused tests and confirm they fail**

```bash
source .venv/bin/activate 2>/dev/null || source venv/bin/activate
pytest -q \
  tests/hermes_cli/test_subcommands_batch.py::test_dashboard_builder_two_handlers \
  tests/hermes_cli/test_dashboard_build_only.py
```

Expected: parser rejects `dashboard --build-only`, and dispatch tests fall
through toward profile/server initialization.

- [ ] **Step 4: Register `--build-only` on dashboard only**

In `build_dashboard_parser()` after `--no-open`, add:

```python
dashboard_parser.add_argument(
    "--build-only",
    action="store_true",
    help="Build the browser dashboard assets and exit without starting the server",
)
```

Do not add this argument in `_add_server_runtime_args()`, because that helper is
also used by `serve`.

- [ ] **Step 5: Add the early-return handler**

In `cmd_dashboard()`, after the existing `--status` and `--stop` early exits
and before `_headless_backend` resolution/environment sanitization, add:

```python
if getattr(args, "build_only", False):
    if not _build_web_ui(PROJECT_ROOT / "web", fatal=True):
        raise SystemExit(1)
    return
```

This placement makes lifecycle flags authoritative and keeps build-only out of
the named-profile machine-dashboard reroute.

- [ ] **Step 6: Run focused and neighboring dashboard tests**

```bash
pytest -q \
  tests/hermes_cli/test_subcommands_batch.py \
  tests/hermes_cli/test_dashboard_build_only.py \
  tests/hermes_cli/test_dashboard_lifecycle_flags.py \
  tests/hermes_cli/test_dashboard_web_dist_validation.py \
  tests/hermes_cli/test_serve_command.py
```

Expected: all pass.

- [ ] **Step 7: Commit the CLI slice**

```bash
git add \
  hermes_cli/subcommands/dashboard.py \
  hermes_cli/main.py \
  tests/hermes_cli/test_subcommands_batch.py \
  tests/hermes_cli/test_dashboard_build_only.py
git commit -m "feat(dashboard): add build-only command"
```

---

## Task 2: Add the POSIX installer dashboard-build stage

**Files:**

- Modify: `scripts/install.sh`
- Modify: `hermes_cli/scripts/install.sh`
- Create: `tests/test_install_dashboard_build_stage.py`

**Interfaces:**

- Consumes: installed checkout, managed venv/system Python, existing Node/npm
- Produces: manifest stage `dashboard-build`
- Calls: `<managed-python> -m hermes_cli.main dashboard --build-only`
- Stage success: exit `0`, `ok: true`, `skipped: false`
- Stage failure/missing Node: exit `0`, `ok: true`, `skipped: true`, actionable
  deferred reason
- Monolithic install: warns and continues

- [ ] **Step 1: Add failing POSIX manifest-order tests**

Start `tests/test_install_dashboard_build_stage.py` with:

```python
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
POSIX_INSTALLERS = [
    ROOT / "scripts" / "install.sh",
    ROOT / "hermes_cli" / "scripts" / "install.sh",
]
POWERSHELL_INSTALLERS = [
    ROOT / "scripts" / "install.ps1",
    ROOT / "hermes_cli" / "scripts" / "install.ps1",
]
DEFERRED_REASON = (
    "Dashboard build deferred; dashboard startup will retry automatically"
)


def _last_json(stdout: str) -> dict:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no JSON result in output:\n{stdout}")


@pytest.mark.parametrize("script", POSIX_INSTALLERS, ids=lambda p: str(p.relative_to(ROOT)))
@pytest.mark.parametrize("include_desktop", [False, True])
def test_posix_manifest_orders_dashboard_build(script, include_desktop):
    command = ["bash", str(script), "--manifest"]
    if include_desktop:
        command.append("--include-desktop")
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    names = [stage["name"] for stage in json.loads(completed.stdout)["stages"]]

    assert names.index("node-deps") < names.index("dashboard-build")
    assert names.index("dashboard-build") < names.index("complete")
    if include_desktop:
        assert names.index("dashboard-build") < names.index("desktop")
```

- [ ] **Step 2: Add failing POSIX real stage-protocol tests**

Append:

```python
def _fake_posix_install(tmp_path: Path, exit_code: int) -> tuple[Path, dict[str, str]]:
    install_dir = tmp_path / "install"
    python = install_dir / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    log = tmp_path / "python-args.txt"
    python.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" > "$FAKE_DASHBOARD_LOG"\n'
        'exit "$FAKE_DASHBOARD_EXIT"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "HERMES_INSTALL_DIR": str(install_dir),
            "HERMES_HOME": str(tmp_path / "home"),
            "FAKE_DASHBOARD_LOG": str(log),
            "FAKE_DASHBOARD_EXIT": str(exit_code),
        }
    )
    return log, env


@pytest.mark.parametrize("script", POSIX_INSTALLERS, ids=lambda p: str(p.relative_to(ROOT)))
def test_posix_dashboard_stage_uses_managed_python_and_reports_success(
    script, tmp_path
):
    log, env = _fake_posix_install(tmp_path, exit_code=0)
    completed = subprocess.run(
        [
            "bash",
            str(script),
            "--stage",
            "dashboard-build",
            "--json",
            "--non-interactive",
        ],
        env=env,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert log.read_text(encoding="utf-8").strip() == (
        "-m hermes_cli.main dashboard --build-only"
    )
    assert _last_json(completed.stdout) == {
        "ok": True,
        "stage": "dashboard-build",
        "skipped": False,
    }


@pytest.mark.parametrize("script", POSIX_INSTALLERS, ids=lambda p: str(p.relative_to(ROOT)))
def test_posix_dashboard_stage_defers_failure(script, tmp_path):
    _, env = _fake_posix_install(tmp_path, exit_code=7)
    completed = subprocess.run(
        [
            "bash",
            str(script),
            "--stage",
            "dashboard-build",
            "--json",
            "--non-interactive",
        ],
        env=env,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    result = _last_json(completed.stdout)
    assert result == {
        "ok": True,
        "stage": "dashboard-build",
        "skipped": True,
        "reason": DEFERRED_REASON,
    }
    assert "dashboard --build-only" in completed.stdout
```

- [ ] **Step 3: Run tests and confirm the stage is missing**

```bash
pytest -q tests/test_install_dashboard_build_stage.py -k posix
```

Expected: both manifests lack `dashboard-build`; stage invocations report
unknown stage.

- [ ] **Step 4: Implement one strict POSIX helper in both installer copies**

Place an equivalent helper near `install_node_deps()` in both POSIX scripts:

```bash
DASHBOARD_BUILD_DEFERRED_REASON="Dashboard build deferred; dashboard startup will retry automatically"

install_dashboard_build() {
    local python_cmd=""

    if [ "$USE_VENV" = true ]; then
        python_cmd="$INSTALL_DIR/venv/bin/python"
    else
        if [ -z "${PYTHON_PATH:-}" ]; then
            install_uv
            check_python
        fi
        python_cmd="$PYTHON_PATH"
    fi

    if [ ! -x "$python_cmd" ]; then
        log_warn "Dashboard build deferred because managed Python was not found: $python_cmd"
        log_info "Dashboard startup will retry automatically."
        return 1
    fi

    log_info "Preparing browser dashboard..."
    if "$python_cmd" -m hermes_cli.main dashboard --build-only; then
        log_success "Browser dashboard ready"
        return 0
    fi

    log_warn "$DASHBOARD_BUILD_DEFERRED_REASON"
    log_info "Retry manually with: $python_cmd -m hermes_cli.main dashboard --build-only"
    return 1
}
```

Keep the helper strict so the stage protocol and monolithic caller can decide
how to convert failure.

- [ ] **Step 5: Register the POSIX stage in both manifests and stage bodies**

Add this manifest object immediately after `node-deps`:

```json
{"name":"dashboard-build","title":"Prepare browser dashboard","category":"runtime","needs_user_input":false}
```

Add this `run_stage_body()` branch immediately after `node-deps`:

```bash
dashboard-build)
    detect_os
    resolve_install_layout
    require_install_dir
    install_dashboard_build
    ;;
```

Do not duplicate Node detection in the installer helper. The shared Python
builder's `_resolve_node_runtime_npm()` already resolves both the
Hermes-managed runtime and system npm. If neither exists, build-only exits
nonzero and the installer wrapper defers the stage.

- [ ] **Step 6: Convert only dashboard-stage failure to deferred success**

In `run_stage_protocol()`, after capturing `code` and before the generic JSON
success/failure branch:

```bash
if [ "$stage" = "dashboard-build" ] && [ "$code" -ne 0 ]; then
    if [ "$JSON_OUTPUT" = true ]; then
        emit_stage_json "$stage" true true "$DASHBOARD_BUILD_DEFERRED_REASON"
    fi
    return 0
fi
```

All other installer stages retain their existing failure semantics.

- [ ] **Step 7: Link the monolithic POSIX install path**

Immediately after `install_node_deps` in `main()`:

```bash
if ! install_dashboard_build; then
    # The helper already printed the warning and recovery command.
    :
fi
```

This ensures the one-line installer gets the same behavior as manifest-driven
Desktop bootstrap and still reaches `print_success`/`.install_method`.

- [ ] **Step 8: Verify syntax and POSIX contracts**

```bash
bash -n scripts/install.sh
bash -n hermes_cli/scripts/install.sh
pytest -q tests/test_install_dashboard_build_stage.py -k posix
```

Expected: all pass, successful stages are not skipped, and failing stages are
deferred with process exit `0`.

- [ ] **Step 9: Commit the POSIX slice**

```bash
git add \
  scripts/install.sh \
  hermes_cli/scripts/install.sh \
  tests/test_install_dashboard_build_stage.py
git commit -m "feat(installer): prepare dashboard on POSIX install"
```

---

## Task 3: Add the Windows installer dashboard-build stage

**Files:**

- Modify: `scripts/install.ps1`
- Modify: `hermes_cli/scripts/install.ps1`
- Modify: `tests/test_install_dashboard_build_stage.py`

**Interfaces:**

- Consumes: `$InstallDir\venv\Scripts\python.exe`, or uv-resolved Python for
  `-NoVenv`
- Produces: manifest stage `dashboard-build`
- Uses: existing `$script:_StageSkippedReason` channel
- Must remain: ASCII-only and compatible with Windows PowerShell 5.1

- [ ] **Step 1: Add failing PowerShell manifest/parity tests**

Append to `tests/test_install_dashboard_build_stage.py`:

```python
import shutil
import sys


@pytest.mark.parametrize(
    "script", POWERSHELL_INSTALLERS, ids=lambda p: str(p.relative_to(ROOT))
)
def test_powershell_manifest_orders_dashboard_build(script):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is not available")

    completed = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(script), "-Manifest", "-IncludeDesktop"],
        check=True,
        capture_output=True,
        text=True,
    )
    names = [stage["name"] for stage in json.loads(completed.stdout)["stages"]]

    assert names.index("node-deps") < names.index("dashboard-build")
    assert names.index("dashboard-build") < names.index("desktop")
    assert names.index("dashboard-build") < names.index("bootstrap-marker")


@pytest.mark.parametrize(
    "script", POWERSHELL_INSTALLERS, ids=lambda p: str(p.relative_to(ROOT))
)
def test_powershell_dashboard_stage_contract_is_present(script):
    source = script.read_text(encoding="utf-8")
    assert 'Name = "dashboard-build"' in source
    assert 'Worker = "Stage-DashboardBuild"' in source
    assert "function Install-DashboardBuild" in source
    assert "-m hermes_cli.main dashboard --build-only" in source
    assert "$script:_StageSkippedReason" in source
    assert DEFERRED_REASON in source
```

- [ ] **Step 2: Add a real PowerShell stage outcome test**

Append this test harness. It uses the real managed-Python path expected by the
installer but places a fake `hermes_cli.main` first on `PYTHONPATH`, so it
exercises the PowerShell stage protocol without importing this checkout or
running npm:

```python
def _run_powershell_dashboard_stage(tmp_path: Path, exit_code: int):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is not available")

    install_dir = tmp_path / "install"
    windows_python = install_dir / "venv" / "Scripts" / "python.exe"
    if os.name == "nt":
        subprocess.run(
            [sys.executable, "-m", "venv", str(install_dir / "venv")],
            check=True,
        )
    else:
        windows_python.parent.mkdir(parents=True)
        windows_python.symlink_to(Path(sys.executable))

    fake_root = tmp_path / "fake-pythonpath"
    package = fake_root / "hermes_cli"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "main.py").write_text(
        "from pathlib import Path\n"
        "import os\n"
        "import sys\n"
        "Path(os.environ['FAKE_DASHBOARD_LOG']).write_text("
        "' '.join(sys.argv[1:]), encoding='utf-8')\n"
        "raise SystemExit(int(os.environ['FAKE_DASHBOARD_EXIT']))\n",
        encoding="utf-8",
    )

    log = tmp_path / f"powershell-python-args-{exit_code}.txt"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(fake_root),
            "FAKE_DASHBOARD_LOG": str(log),
            "FAKE_DASHBOARD_EXIT": str(exit_code),
        }
    )
    completed = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(ROOT / "scripts" / "install.ps1"),
            "-Stage",
            "dashboard-build",
            "-Json",
            "-NonInteractive",
            "-InstallDir",
            str(install_dir),
            "-HermesHome",
            str(tmp_path / "home"),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    return completed, log


@pytest.mark.parametrize(
    ("exit_code", "skipped"),
    [(0, False), (7, True)],
)
def test_powershell_dashboard_stage_outcome(tmp_path, exit_code, skipped):
    completed, log = _run_powershell_dashboard_stage(tmp_path, exit_code)

    assert completed.returncode == 0
    assert log.read_text(encoding="utf-8") == "dashboard --build-only"
    result = _last_json(completed.stdout)
    result.pop("duration_ms")
    expected = {
        "stage": "dashboard-build",
        "ok": True,
        "skipped": skipped,
        "reason": DEFERRED_REASON if skipped else None,
    }
    assert result == expected
```

The manifest and source-contract tests still run when PowerShell is unavailable;
only this native stage-protocol test is skipped.

- [ ] **Step 3: Run the focused tests and confirm failure**

```bash
pytest -q tests/test_install_dashboard_build_stage.py -k powershell
```

Expected: manifest and source-contract tests fail because the stage is absent.

- [ ] **Step 4: Implement `Install-DashboardBuild` in both PowerShell copies**

Place the equivalent function near `Install-NodeDeps`:

```powershell
function Install-DashboardBuild {
    if ($NoVenv) {
        Resolve-UvCmd
        $pythonExe = (& $script:UvCmd python find $PythonVersion)
    } else {
        $pythonExe = Join-Path $InstallDir "venv\Scripts\python.exe"
    }

    if (-not (Test-Path -LiteralPath $pythonExe)) {
        Write-Warn "Dashboard build deferred because managed Python was not found: $pythonExe"
        $script:_StageSkippedReason = "Dashboard build deferred; dashboard startup will retry automatically"
        return
    }

    Write-Info "Preparing browser dashboard..."
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $pythonExe -m hermes_cli.main dashboard --build-only
        $buildExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }

    if ($buildExitCode -eq 0) {
        Write-Success "Browser dashboard ready"
        return
    }

    Write-Warn "Dashboard build deferred; dashboard startup will retry automatically"
    Write-Info "Retry manually with: $pythonExe -m hermes_cli.main dashboard --build-only"
    $script:_StageSkippedReason = "Dashboard build deferred; dashboard startup will retry automatically"
}
```

The temporary `ErrorActionPreference` relaxation is required because native
stderr can otherwise become a terminating PowerShell error before
`$LASTEXITCODE` is inspected.

- [ ] **Step 5: Register the Windows stage before optional Desktop**

In `$InstallStages`, insert immediately after `node-deps` and before the array
is closed for conditional Desktop insertion:

```powershell
@{ Name = "dashboard-build"; Title = "Preparing browser dashboard"; Category = "install"; NeedsUserInput = $false; Worker = "Stage-DashboardBuild" }
```

Add the thin worker:

```powershell
function Stage-DashboardBuild { Install-DashboardBuild }
```

No special handling is needed in `Invoke-Stage`: it already translates
`$script:_StageSkippedReason` into `ok=true`, `skipped=true`, and continues.
The normal `Invoke-AllStages` loop automatically links this into monolithic
installation and still reaches `Stage-BootstrapMarker`.

- [ ] **Step 6: Verify PowerShell parsing, ASCII, and stage contracts**

```bash
pytest -q \
  tests/test_install_dashboard_build_stage.py \
  tests/test_install_ps1_ascii_only.py \
  tests/test_install_ps1_native_stderr_eap.py \
  tests/test_install_ps1_node_path_for_npm.py
```

When pwsh is available, also run:

```bash
pwsh -NoProfile -Command \
  "[void][scriptblock]::Create((Get-Content -Raw scripts/install.ps1)); 'ok'"
pwsh -NoProfile -Command \
  "[void][scriptblock]::Create((Get-Content -Raw hermes_cli/scripts/install.ps1)); 'ok'"
```

Expected: all pass, including `tests/test_install_ps1_ascii_only.py`.

- [ ] **Step 7: Commit the Windows slice**

```bash
git add \
  scripts/install.ps1 \
  hermes_cli/scripts/install.ps1 \
  tests/test_install_dashboard_build_stage.py
git commit -m "feat(installer): prepare dashboard on Windows install"
```

---

## Task 4: Prove build/stamp idempotency and run cross-path regressions

**Files:**

- Modify: `tests/hermes_cli/test_web_ui_build.py`
- Verify unchanged: update call sites in `hermes_cli/main.py`
- Verify unchanged: brand emitter in `scripts/brand/emitters/pyproject-scripts.mjs`

**Interfaces:**

- Consumes: `_build_web_ui`, `_write_web_ui_build_stamp`,
  `_web_ui_build_needed`
- Produces: evidence that install-time output makes first launch a no-op
- Preserves: direct update calls to `_build_web_ui(PROJECT_ROOT / "web")`

- [ ] **Step 1: Add a single end-to-end helper idempotency test**

Append inside `TestBuildWebUISkipsWhenFresh` in
`tests/hermes_cli/test_web_ui_build.py`:

```python
def test_successful_build_writes_stamp_and_next_call_runs_no_npm(self, tmp_path):
    web_dir, dist_dir = _make_web_dir(tmp_path)
    (web_dir / "src").mkdir(parents=True)
    (web_dir / "src" / "App.tsx").write_text("export const App = 1\n")

    install_ok = __import__("subprocess").CompletedProcess(
        [], 0, stdout="", stderr=""
    )
    build_ok = __import__("subprocess").CompletedProcess(
        [], 0, stdout="", stderr=""
    )

    def finish_vite_build(*_args, **_kwargs):
        dist_dir.mkdir(parents=True, exist_ok=True)
        (dist_dir / "index.html").write_text("<html></html>")
        return build_ok

    with patch("hermes_cli.main._resolve_node_runtime_npm", return_value="/usr/bin/npm"), \
         patch("hermes_cli.main.subprocess.run", return_value=install_ok) as install, \
         patch(
             "hermes_cli.main._run_with_idle_timeout",
             side_effect=finish_vite_build,
         ) as build:
        assert _build_web_ui(web_dir, fatal=True) is True
        assert _web_ui_stamp_path().is_file()
        assert _build_web_ui(web_dir, fatal=True) is True

    assert install.call_count == 1
    assert build.call_count == 1
```

This test represents the installer followed by first dashboard launch: the
first call creates dist/stamp, and the second unchanged call must not touch npm.

Also add the strict missing-runtime contract:

```python
def test_fatal_build_returns_false_when_npm_is_unavailable(self, tmp_path):
    web_dir, _ = _make_web_dir(tmp_path)

    with patch("hermes_cli.main._resolve_node_runtime_npm", return_value=None):
        assert _build_web_ui(web_dir, fatal=True) is False
```

Together with the installer stage failure tests, this proves missing Node/npm
becomes a deferred installer result rather than a falsely successful build.

- [ ] **Step 2: Run build integration tests**

```bash
pytest -q tests/hermes_cli/test_web_ui_build.py
```

Expected: all pass, including existing changed-source, retry, stale-dist
fallback, missing-dist failure, and POSIX locking tests.

- [ ] **Step 3: Verify both update paths still use the shared helper**

Read the two update call sites around the existing `_build_web_ui` calls in
`hermes_cli/main.py` and confirm they still call the function directly rather
than invoking `dashboard --build-only`.

Run:

```bash
pytest -q \
  tests/hermes_cli/test_cmd_update.py \
  tests/hermes_cli/test_update_autostash.py \
  tests/hermes_cli/test_update_stale_dashboard.py \
  tests/hermes_cli/test_update_interrupted_recovery.py
```

Expected: all pass. Do not edit update behavior unless one of these tests
exposes a regression caused by Tasks 1-3.

- [ ] **Step 4: Verify brand aliases remain shared**

Run the brand entry-point emitter tests:

```bash
node --test \
  --test-name-pattern="addBrandScripts inserts|addBrandScripts on the upstream-neutral" \
  scripts/brand/__tests__/pyproject-scripts.test.mjs
```

The relevant invariant is that generated `otto` and `loop24` scripts map to
`hermes_cli.main:main`; no dashboard implementation should appear under
`brands/` or `scripts/brand/`. The whole file currently has unrelated
active-brand-versus-neutral-base failures, so use the hermetic emitter cases
above rather than making this feature responsible for that pre-existing drift.

- [ ] **Step 5: Run the complete focused acceptance suite**

```bash
pytest -q \
  tests/hermes_cli/test_dashboard_build_only.py \
  tests/hermes_cli/test_subcommands_batch.py \
  tests/hermes_cli/test_dashboard_lifecycle_flags.py \
  tests/hermes_cli/test_dashboard_web_dist_validation.py \
  tests/hermes_cli/test_serve_command.py \
  tests/hermes_cli/test_web_ui_build.py \
  tests/test_install_dashboard_build_stage.py \
  tests/test_install_ps1_ascii_only.py

bash -n scripts/install.sh
bash -n hermes_cli/scripts/install.sh
```

Expected: all pass.

- [ ] **Step 6: Perform a no-network smoke test with a current bundle**

Using a temporary `HERMES_HOME`, first run the build-only command in a checkout
whose `hermes_cli/web_dist/index.html` and stamp are already current, while
placing an `npm` shim that fails if called ahead of the real PATH:

```bash
smoke_dir="$(mktemp -d)"
mkdir -p "$smoke_dir/bin"
printf '#!/bin/sh\nexit 97\n' > "$smoke_dir/bin/npm"
chmod +x "$smoke_dir/bin/npm"
HERMES_HOME="$smoke_dir/home" PATH="$smoke_dir/bin:$PATH" \
  python -m hermes_cli.main dashboard --build-only
```

Before running, seed the temporary stamp using the tested helper or perform one
successful build with the real npm. The second command must exit `0`; exit `97`
indicates an unexpected npm invocation.

- [ ] **Step 7: Inspect repository cleanliness**

```bash
git status --short
git diff --check
```

Expected:

- Only intended source/tests plus the user's pre-existing unrelated changes are
  present.
- `hermes_cli/web_dist/`, `.web_ui_build.lock`, and runtime stamp output remain
  ignored/untracked.
- No tracked `package-lock.json` churn was introduced.

- [ ] **Step 8: Commit the integration test**

```bash
git add tests/hermes_cli/test_web_ui_build.py
git commit -m "test(dashboard): prove install build skips first launch"
```

---

## Completion Checklist

- [ ] `dashboard --build-only` is accepted; `serve --build-only` is rejected.
- [ ] Build-only invokes `_build_web_ui(PROJECT_ROOT / "web", fatal=True)` once.
- [ ] Build-only exits before every server/profile/plugin/MCP side effect.
- [ ] Both POSIX manifests include `dashboard-build` after `node-deps`.
- [ ] Both PowerShell manifests include `dashboard-build` after `node-deps`.
- [ ] Optional Desktop stages occur after `dashboard-build`.
- [ ] All four installer copies invoke managed Python, never a public command
  shim.
- [ ] Successful stages report `ok=true`, `skipped=false`.
- [ ] Missing Node/build failure reports `ok=true`, `skipped=true`, with the
  deferred reason and process exit `0`.
- [ ] Monolithic installers continue after a deferred build and reach their
  completion marker.
- [ ] A successful build leaves `hermes_cli/web_dist/index.html` and a matching
  `$HERMES_HOME/web-ui-build-stamp.json`.
- [ ] An unchanged first dashboard launch performs no npm work.
- [ ] A deleted/stale dist still triggers launch-time recovery.
- [ ] Update, `serve`, `--skip-build`, `HERMES_WEB_DIST`, Docker/Nix assumptions,
  and brand aliases remain unchanged.
- [ ] Bash syntax, PowerShell parse/ASCII, focused pytest, and the hermetic
  brand-emitter cases pass.
