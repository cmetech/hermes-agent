"""Importing our modules must not spawn a subprocess.

`platform.system()` looks free but is not: on Windows it routes through
platform.uname() -> win32_ver() -> _syscmd_ver(), which runs `cmd /c ver` in a
subprocess. Seven of our modules evaluated it at module scope, so importing
hermes_cli.config, tools.managed_process or tools.process_registry spawned a
process on every Windows CLI invocation -- and it broke
test_bedrock_classification_is_pure_in_a_fresh_process, which forbids
subprocess during runtime classification.

Note this cannot be expressed as "import must not call platform.uname()": the
stdlib's own `uuid` module calls platform.system() at import on Linux, and
there it is subprocess-free and harmless. So the two invariants are tested
separately:

* the behavioural one -- no subprocess during import -- which bites on Windows;
* the source-level one -- our own Windows detection never goes through
  platform.system() -- which is deterministic on every platform, and is what
  actually stops the regression coming back on a macOS or Linux dev box where
  the behavioural test passes vacuously.
"""
from __future__ import annotations

import platform
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Modules whose import path is hot enough that a subprocess is unacceptable.
IMPORT_HOT_MODULES = (
    "hermes_cli.config",
    "hermes_cli.dep_ensure",
    "tools.environments.local",
    "tools.process_registry",
    "tools.managed_process",
    "tools.code_execution_tool",
)

# `_IS_WINDOWS = platform.system() == "Windows"` at column 0, i.e. evaluated at
# import rather than inside a function.
MODULE_SCOPE_PLATFORM_SYSTEM = re.compile(
    r"^_?[A-Z_a-z]\w*\s*=\s*platform\.system\(\)", re.MULTILINE
)


@pytest.mark.parametrize("module", IMPORT_HOT_MODULES)
def test_importing_module_does_not_spawn_a_subprocess(module: str) -> None:
    probe = (
        "import subprocess\n"
        "def forbidden(*a, **k):\n"
        "    raise AssertionError('import spawned a subprocess')\n"
        "subprocess.Popen = forbidden\n"
        f"import {module}\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_no_module_scope_windows_detection_via_platform_system() -> None:
    """Deterministic everywhere, unlike the subprocess probe above."""
    offenders = []
    for directory in ("hermes_cli", "tools", "agent", "plugins"):
        for path in (ROOT / directory).rglob("*.py"):
            if MODULE_SCOPE_PLATFORM_SYSTEM.search(path.read_text(encoding="utf-8")):
                offenders.append(path.relative_to(ROOT).as_posix())

    assert not offenders, (
        "platform.system() at module scope spawns `cmd /c ver` on Windows every "
        f"time these are imported; use os.name == 'nt' instead: {sorted(offenders)}"
    )


@pytest.mark.parametrize("module", IMPORT_HOT_MODULES)
def test_is_windows_constant_matches_platform(module: str) -> None:
    """The cheap check must stay equivalent to the expensive one it replaced."""
    imported = __import__(module, fromlist=["_IS_WINDOWS"])

    assert imported._IS_WINDOWS == (platform.system() == "Windows")
