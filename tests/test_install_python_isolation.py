"""The installers must deliver their own Python and ignore the machine's.

A first install on a corporate Windows laptop died at the ``dependencies``
stage with::

    File "C:\\Python\\Python310\\lib\\sre_compile.py", line 17
        assert _sre.MAGIC == MAGIC, "SRE module mismatch"
    AssertionError: SRE module mismatch

Two independent defects combined:

1. ``uv python find <version>`` also matches interpreters discovered on PATH,
   so the installer adopted the machine's ``C:\\Python311\\python.exe`` instead
   of provisioning one. The requirement is the opposite: use the Python we
   deliver for every phase.
2. A machine-scope ``PYTHONHOME`` pointed at a *different* minor version's
   stdlib. ``PYTHONHOME`` overrides an interpreter's own stdlib location, so it
   poisons even a correctly-chosen interpreter -- including uv's isolated build
   backend, a grandchild process. Clearing it in a shell does not help, because
   the Electron app that spawns the install inherits Machine/User-scope
   environment directly.

Both installers must therefore (a) prefer a uv-MANAGED interpreter and (b)
scrub the inherited Python environment for the whole run.

The ``check_python`` tests are behavioural: the shell functions are extracted
from ``install.sh`` and executed against a stub ``uv``, so they assert the
resolution *decision* rather than the presence of a string. The PowerShell
assertions are source-level because CI cannot execute install.ps1 (same
constraint as test_install_ps1_ascii_only.py).
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"

# The Python family that must never survive into the install. Mirrors
# INHERITED_PYTHON_ENV_VARS (apps/desktop/electron/backend-env.ts) and the
# Python entries of _ENV_VAR_NAME_DENYLIST (hermes_cli/config.py).
INHERITED_PYTHON_ENV_VARS = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONEXECUTABLE",
    "PYTHONUSERBASE",
)


def _extract_shell_function(text: str, name: str) -> str:
    """Return one top-level ``name() { ... }`` block from a shell script."""
    match = re.search(rf"^{re.escape(name)}\(\) \{{$.*?^\}}$", text, re.MULTILINE | re.DOTALL)
    assert match, f"{name}() not found in install.sh"
    return match.group(0)


def _extract_ps_function(text: str, name: str) -> str:
    """Return one top-level ``function Name { ... }`` block from install.ps1."""
    match = re.search(
        rf"^function {re.escape(name)} \{{$.*?^\}}$", text, re.MULTILINE | re.DOTALL
    )
    assert match, f"function {name} not found in install.ps1"
    return match.group(0)


def _write_stub_interpreter(path: Path, version_line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho '{version_line}'\n")
    path.chmod(0o755)


def _run_check_python(tmp_path: Path, *, managed_available: bool) -> dict[str, str]:
    """Execute install.sh's check_python against a stub uv; return its results.

    The stub models the real hazard: a system 3.11 is always discoverable, so a
    ``uv python find 3.11`` that is not restricted to managed interpreters will
    happily return it.
    """
    text = INSTALL_SH.read_text()
    state = tmp_path / "state"
    state.mkdir()

    _write_stub_interpreter(state / "managed" / "python3.11", "Python 3.11.15")
    _write_stub_interpreter(state / "system" / "python3.11", "Python 3.11.0")

    uv_stub = tmp_path / "uv"
    uv_stub.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            STATE="{state}"
            MANAGED_AVAILABLE="{'1' if managed_available else ''}"
            if [ "$1" = "python" ] && [ "$2" = "install" ]; then
                echo "$*" >> "$STATE/install-calls"
                exit 0
            fi
            if [ "$1" = "python" ] && [ "$2" = "find" ]; then
                if [ "$3" = "--managed-python" ]; then
                    [ -n "$MANAGED_AVAILABLE" ] || exit 2
                    echo "$STATE/managed/python3.11"
                    exit 0
                fi
                # Unrestricted find always matches the machine's interpreter.
                echo "$STATE/system/python3.11"
                exit 0
            fi
            exit 1
            """
        )
    )
    uv_stub.chmod(0o755)

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -e\n"
        "DISTRO=linux\n"
        'PYTHON_VERSION="3.11"\n'
        f'UV_CMD="{uv_stub}"\n'
        "log_info() { echo \"INFO: $*\"; }\n"
        "log_success() { echo \"OK: $*\"; }\n"
        "log_warn() { echo \"WARN: $*\"; }\n"
        "log_error() { echo \"ERR: $*\"; }\n"
        + _extract_shell_function(text, "find_managed_python")
        + "\n"
        + _extract_shell_function(text, "check_python")
        + "\n"
        "check_python\n"
        'echo "RESULT_PYTHON_PATH=$PYTHON_PATH"\n'
    )

    proc = subprocess.run(
        ["bash", str(harness)], capture_output=True, text=True, cwd=tmp_path, check=False
    )
    assert proc.returncode == 0, f"check_python failed:\n{proc.stdout}\n{proc.stderr}"

    results = dict(
        line.split("=", 1)
        for line in proc.stdout.splitlines()
        if line.startswith("RESULT_")
    )
    results["_stdout"] = proc.stdout
    results["_install_calls"] = (
        (state / "install-calls").read_text() if (state / "install-calls").exists() else ""
    )
    return results


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh missing")
def test_check_python_prefers_managed_interpreter_over_a_present_system_one(tmp_path: Path) -> None:
    result = _run_check_python(tmp_path, managed_available=True)

    # The regression: a system 3.11 IS discoverable here, and must lose.
    assert result["RESULT_PYTHON_PATH"].endswith("/managed/python3.11"), result["_stdout"]
    assert "/system/" not in result["RESULT_PYTHON_PATH"]

    # Provisioning is attempted before resolution, so a machine with no managed
    # Python yet still gets one instead of silently adopting the system's.
    assert "python install" in result["_install_calls"]

    # The log names the interpreter AND its provenance. "Python found: Python
    # 3.11.0" said neither, which is what made the original failure so hard to
    # spot in the bootstrap log.
    assert "Using uv-managed Python" in result["_stdout"]
    assert result["RESULT_PYTHON_PATH"] in result["_stdout"]


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh missing")
def test_check_python_falls_back_to_system_python_but_warns(tmp_path: Path) -> None:
    """A blocked python-build-standalone download must not brick the install."""
    result = _run_check_python(tmp_path, managed_available=False)

    assert result["RESULT_PYTHON_PATH"].endswith("/system/python3.11")
    # Loud, and by path: this is the configuration the SRE mismatch came from,
    # so a later crash needs a log line saying which interpreter was used.
    assert "WARN: Could not provision a uv-managed Python" in result["_stdout"]
    assert result["RESULT_PYTHON_PATH"] in result["_stdout"]


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh missing")
def test_install_sh_creates_the_venv_from_a_resolved_managed_interpreter() -> None:
    text = INSTALL_SH.read_text()

    # `uv venv --python 3.11` re-opens the door to a system interpreter, so the
    # venv must be created from a resolved absolute path. Stages run in separate
    # processes, so it re-resolves rather than trusting an exported PYTHON_PATH.
    assert 'venv_python="$(find_managed_python "$PYTHON_VERSION")"' in text
    assert '$UV_CMD venv venv --python "$venv_python"' in text


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh missing")
def test_install_sh_scrubs_the_whole_inherited_python_family() -> None:
    text = INSTALL_SH.read_text()

    for name in INHERITED_PYTHON_ENV_VARS:
        assert f"_hermes_scrub_python_var {name} " in text, f"{name} is not scrubbed"

    assert "export PYTHONNOUSERSITE=1" in text
    # uv's own interpreter policy is inheritable too, and only-system would both
    # re-force the machine's Python and make --managed-python an argument error.
    assert "unset UV_PYTHON_PREFERENCE" in text
    assert "unset UV_NO_MANAGED_PYTHON" in text


@pytest.mark.skipif(not INSTALL_PS1.exists(), reason="install.ps1 missing")
def test_install_ps1_scrubs_the_whole_inherited_python_family() -> None:
    text = INSTALL_PS1.read_text()

    for name in INHERITED_PYTHON_ENV_VARS:
        assert f"'{name}'" in text, f"{name} is not scrubbed"

    assert '$env:PYTHONNOUSERSITE = "1"' in text
    assert "Remove-Item Env:UV_PYTHON_PREFERENCE" in text


@pytest.mark.skipif(not INSTALL_PS1.exists(), reason="install.ps1 missing")
def test_install_ps1_resolves_and_uses_a_managed_interpreter() -> None:
    text = INSTALL_PS1.read_text()

    assert "function Get-ManagedPythonPath" in text
    # Restricted to uv-managed interpreters -- an unrestricted `python find`
    # matches the machine's Python, which is the defect being fixed.
    assert "python find --managed-python" in text
    assert "python install --managed-python" in text

    # Test-Python must provision before it resolves, so an unprovisioned machine
    # gets our interpreter rather than adopting whichever one it already has.
    test_python = text[text.index("function Test-Python") : text.index("$script:GitInstallFailureReason")]
    assert "python install --managed-python" in test_python
    assert test_python.index("python install --managed-python") < test_python.index(
        "Get-ManagedPythonPath"
    ), "Test-Python must install the managed Python before resolving one"

    # The venv must be built from the resolved managed path, not `--python 3.11`
    # (which lets uv match a system interpreter all over again).
    install_venv = text[text.index("function Install-Venv") : text.index("function Install-Dependencies")]
    assert "Get-ManagedPythonPath" in install_venv
    assert "venv venv --python $ManagedPythonExe" in install_venv


@pytest.mark.skipif(
    shutil.which("pwsh") is None or not INSTALL_PS1.exists(),
    reason="PowerShell unavailable (Linux CI); source-level assertions cover this case",
)
def test_get_managed_python_path_never_returns_a_discovered_interpreter(tmp_path: Path) -> None:
    """Behavioural check of the PowerShell resolver, when a host can run it.

    The whole defect was a resolver that answered with the machine's
    interpreter. Asserting the source mentions ``--managed-python`` does not
    prove the function refuses to fall through to an unrestricted find, so run
    it against a stub uv where a system interpreter IS discoverable.
    """
    fn = re.search(
        r"^function Get-ManagedPythonPath \{$.*?^\}$", INSTALL_PS1.read_text(), re.MULTILINE | re.DOTALL
    )
    assert fn, "Get-ManagedPythonPath() not found in install.ps1"
    fn_file = tmp_path / "fn.ps1"
    fn_file.write_text(fn.group(0))

    _write_stub_interpreter(tmp_path / "managed-python", "Python 3.11.15")
    _write_stub_interpreter(tmp_path / "system-python", "Python 3.11.0")

    def _probe(managed_available: bool) -> str:
        uv_stub = tmp_path / f"uv-{managed_available}"
        uv_stub.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "python" ] && [ "$2" = "find" ] && [ "$3" = "--managed-python" ]; then\n'
            + (f'  echo "{tmp_path}/managed-python"; exit 0\n' if managed_available else "  exit 2\n")
            + "fi\n"
            'if [ "$1" = "python" ] && [ "$2" = "find" ]; then\n'
            f'  echo "{tmp_path}/system-python"; exit 0\n'
            "fi\n"
            "exit 1\n"
        )
        uv_stub.chmod(0o755)

        proc = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-Command",
                f'. "{fn_file}"; $UvCmd = "{uv_stub}"; $PythonVersion = "3.11"; '
                'Write-Host "RESULT=[$(Get-ManagedPythonPath "3.11")]"',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        match = re.search(r"RESULT=\[(.*)\]", proc.stdout)
        assert match, proc.stdout
        return match.group(1)

    assert _probe(managed_available=True) == f"{tmp_path}/managed-python"
    # A system interpreter is discoverable here and must NOT be returned; the
    # caller degrades explicitly and warns instead of being handed one silently.
    assert _probe(managed_available=False) == ""


# ---------------------------------------------------------------------------
# Launcher shims: the entry point a user TYPES must scrub too
# ---------------------------------------------------------------------------
# The install-time scrub and the desktop backend spawn were not the whole
# surface. A branded CLI invoked from a shell -- `loop24 config edit` -- runs
# pip's console script, which starts the venv interpreter with whatever Python
# environment that shell carries. A corporate PYTHONPATH naming a complete 3.10
# stdlib is prepended AHEAD of the venv's own Lib, and the process dies in
# sre_compile.py with "AssertionError: SRE module mismatch" inside site.py --
# before any hermes_cli code exists to defend itself.


@pytest.mark.skipif(not INSTALL_PS1.exists(), reason="install.ps1 missing")
def test_install_ps1_installs_env_scrubbing_command_shims() -> None:
    text = INSTALL_PS1.read_text()

    assert "function Get-ConsoleScriptNames" in text
    assert "function Install-CommandShims" in text

    set_path = _extract_ps_function(text, "Set-PathVariable")
    assert "Install-CommandShims" in set_path, "Set-PathVariable must write the launchers"

    # The shims are useless unless their directory is searched BEFORE
    # venv\Scripts: within one directory PATHEXT resolves .EXE ahead of .CMD, so
    # pip's exe would keep winning. Across directories the first PATH entry
    # holding a match wins, which is the only reason this works.
    assert "@($shimDir, $hermesBin)" in set_path, (
        "shim dir must be ordered ahead of the venv Scripts dir"
    )


@pytest.mark.skipif(
    shutil.which("pwsh") is None or not INSTALL_PS1.exists(),
    reason="PowerShell unavailable; source-level assertions cover this case",
)
def test_command_shims_clear_every_inherited_python_var(tmp_path: Path) -> None:
    """Behavioural: run the real shim writer and read what it produced.

    Asserting that install.ps1 mentions PYTHONPATH proves nothing about the file
    the user's shell actually executes, so generate the launchers and inspect
    them.
    """
    text = INSTALL_PS1.read_text()

    decl = re.search(r"^\$InheritedPythonEnvVars = @\([^)]*\)$", text, re.MULTILINE)
    assert decl, "$InheritedPythonEnvVars must be a hoisted list shared with the scrub block"

    fn_file = tmp_path / "fn.ps1"
    fn_file.write_text(
        decl.group(0)
        + "\n"
        + _extract_ps_function(text, "Get-ConsoleScriptNames")
        + "\n"
        + _extract_ps_function(text, "Install-CommandShims")
        + "\n"
    )

    scripts_dir = tmp_path / "venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    # A brand branch adds its own scripts to [project.scripts]; the writer must
    # pick them up from there rather than from a hardcoded brand list.
    for name in ("hermes", "loop24", "loop24-acp"):
        (scripts_dir / f"{name}.exe").write_text("stub")

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        textwrap.dedent(
            """\
            [project]
            name = "hermes-agent"

            [project.scripts]
            hermes = "hermes_cli.main:main"
            loop24 = "hermes_cli.main:main"
            loop24-acp = "acp_adapter.entry:main"
            never-built = "nope:main"

            [tool.setuptools]
            packages = ["hermes_cli"]
            """
        )
    )

    shim_dir = tmp_path / "bin"
    proc = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-Command",
            f'. "{fn_file}"; '
            f'$names = Get-ConsoleScriptNames "{pyproject}"; '
            f'Install-CommandShims -ShimDir "{shim_dir}" -ScriptsDir "{scripts_dir}" -Names $names',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr

    for name in ("hermes", "loop24", "loop24-acp"):
        shim = shim_dir / f"{name}.cmd"
        assert shim.exists(), f"no launcher written for {name}"
        body = shim.read_text()
        for var in INHERITED_PYTHON_ENV_VARS:
            assert f'set "{var}="' in body, f"{name}.cmd does not clear {var}"
        assert 'set "PYTHONNOUSERSITE=1"' in body
        # Quoted, so a HERMES_HOME containing a space still launches.
        assert f'"{scripts_dir / (name + ".exe")}" %*' in body

    # A declared script with no built .exe must not get a launcher that would
    # shadow nothing and fail confusingly.
    assert not (shim_dir / "never-built.cmd").exists()


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh missing")
def test_install_sh_launcher_clears_the_whole_inherited_family() -> None:
    """The POSIX shim predates this fix but only cleared two of the five vars."""
    setup_path = _extract_shell_function(INSTALL_SH.read_text(), "setup_path")

    for name in INHERITED_PYTHON_ENV_VARS:
        assert f"unset {name}" in setup_path, f"the hermes launcher does not clear {name}"
    assert "export PYTHONNOUSERSITE=1" in setup_path
