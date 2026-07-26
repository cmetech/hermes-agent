"""Importing config must not spawn a subprocess.

`platform.system()` looks free but is not: on Windows it routes through
platform.uname() -> win32_ver() -> _syscmd_ver(), which runs `cmd /c ver` in a
subprocess. Both hermes_cli.config and hermes_cli.dep_ensure evaluated it at
module scope, so importing either one spawned a process on Windows -- CLI
startup cost on every invocation, a hard failure anywhere process creation is
restricted, and the cause of a Windows-only CI failure in
test_bedrock_classification_is_pure_in_a_fresh_process.

These tests are platform-independent on purpose. The existing purity test only
catches this on Windows, because platform.system() is subprocess-free on macOS
and Linux -- which is exactly why the regression sat unnoticed on the dev
platform.
"""
from __future__ import annotations

import platform
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", ["hermes_cli.config", "hermes_cli.dep_ensure"])
def test_importing_module_does_not_shell_out(module: str) -> None:
    probe = (
        "import platform, subprocess, sys\n"
        "def forbidden(*a, **k):\n"
        "    raise AssertionError('import spawned a subprocess')\n"
        "def no_uname(*a, **k):\n"
        "    raise AssertionError('import called platform.uname()')\n"
        "subprocess.Popen = forbidden\n"
        "platform.uname = no_uname\n"
        # platform caches uname(); clear it so a warm cache cannot mask the call.
        "platform._uname_cache = None\n"
        f"import {module}\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


@pytest.mark.parametrize("module", ["hermes_cli.config", "hermes_cli.dep_ensure"])
def test_is_windows_constant_matches_platform(module: str) -> None:
    """The cheap check must stay equivalent to the expensive one it replaced."""
    imported = __import__(module, fromlist=["_IS_WINDOWS"])

    assert imported._IS_WINDOWS == (platform.system() == "Windows")
