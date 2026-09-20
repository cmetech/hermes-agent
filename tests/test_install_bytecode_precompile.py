"""Exercise installer completion with a real, disposable native Python venv."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import venv

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _runtime(tmp_path, fail):
    root = tmp_path / "runtime with spaces"
    (root / "hermes_cli").mkdir(parents=True)
    venv.EnvBuilder(with_pip=False).create(root / "venv")
    executable = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    probe = subprocess.run([str(executable), "-c", "import sys; print(sys.prefix)"], capture_output=True, text=True)
    assert probe.returncode == 0, f"fixture Python failed ({probe.returncode}): {probe.stderr}"
    # Boundary stand-in: prove the real completion stage invokes the selected
    # Python before publishing completion, without running the full installer.
    (root / "hermes_cli" / "bytecode_cache.py").write_text(
        "import json, pathlib, sys\n"
        "root = pathlib.Path(__file__).resolve().parents[1]\n"
        "assert not (root / '.hermes-bootstrap-complete').exists()\n"
        "(root / 'prepared.json').write_text(json.dumps({'prefix': sys.prefix}))\n"
        f"raise SystemExit({1 if fail else 0})\n", encoding="utf-8"
    )
    return root


def _assert_prepared(root, result):
    assert result.returncode == 0, result.stdout + result.stderr
    prepared = root / "prepared.json"
    assert prepared.is_file(), "installer completion did not prepare Python bytecode\n" + result.stdout + result.stderr
    assert Path(json.loads(prepared.read_text())["prefix"]).resolve() == (root / "venv").resolve()
    assert (root / ".hermes-bootstrap-complete").is_file()


@pytest.mark.windows_only
@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("no_venv", [False, True])
def test_windows_completion_prepares_bytecode_without_blocking_install(tmp_path, fail, no_venv):
    root = _runtime(tmp_path, fail)
    quote = lambda path: "'" + str(path).replace("'", "''") + "'"
    command = (
        f". {quote(ROOT / 'scripts' / 'install.ps1')}; "
        f"$InstallDir = {quote(root)}; $Commit = '{'a' * 40}'; "
        f"$NoVenv = ${str(no_venv).lower()}; "
        "function Resolve-UvCmd {} "
        "function Resolve-AvailablePythonVersion { '3.12' } "
        f"function Get-ManagedPythonPath {{ param($Version); if ($Version -eq '3.12') {{ {quote(root / 'venv' / 'Scripts' / 'python.exe')} }} }} "
        "Stage-BootstrapMarker"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        # The hermetic runner drops PATHEXT. A newly started Windows PowerShell
        # then treats .exe as a document, not a synchronous native command.
        env={**os.environ, "PATHEXT": ".COM;.EXE;.BAT;.CMD"},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
    )
    _assert_prepared(root, result)
    if fail:
        assert "compile on demand" in result.stdout


def _posix_completion(tmp_path, fail, no_venv):
    root = _runtime(tmp_path, fail)
    managed_home = tmp_path / "managed home"
    uv = managed_home / "bin" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text("#!/bin/sh\n" + f"printf '%s' {shlex.quote(str(root / 'venv' / 'bin' / 'python'))}\n")
    uv.chmod(0o755)
    command = (
        f"source {shlex.quote(str(ROOT / 'scripts' / 'install.sh'))} --manifest >/dev/null\n"
        f"INSTALL_DIR={shlex.quote(str(root))}\n"
        f"INSTALL_COMMIT={'a' * 40}\nUSE_VENV={str(not no_venv).lower()}\nunset PYTHON_PATH\n"
        f"HERMES_HOME={shlex.quote(str(managed_home))}\nunset UV_CMD\n"
        "detect_os() { :; }\nresolve_install_layout() { :; }\nprint_success() { :; }\n"
        "run_stage_body complete\n"
    )
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=45)
    _assert_prepared(root, result)
    if fail:
        assert "compile on demand" in result.stdout + result.stderr


@pytest.mark.linux_only
@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("no_venv", [False, True])
def test_linux_completion_prepares_bytecode_without_blocking_install(tmp_path, fail, no_venv):
    _posix_completion(tmp_path, fail, no_venv)


@pytest.mark.macos_only
@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("no_venv", [False, True])
def test_macos_completion_prepares_bytecode_without_blocking_install(tmp_path, fail, no_venv):
    _posix_completion(tmp_path, fail, no_venv)
