"""POSIX installs must expose every console script, not just ``hermes``.

``scripts/install.sh`` wrote exactly one launcher -- ``$command_link_dir/hermes``
-- and never put ``venv/bin`` on PATH. The brand branches declare their own entry
points in ``[project.scripts]`` (``loop24``, ``loop24-agent``, ``loop24-acp``),
so pip builds ``venv/bin/loop24`` and nothing ever makes it reachable. The
branded command simply does not exist on Linux or macOS, which is why the
checkpoint runbook's ``otto config edit`` works on Windows and not on a Mac.

This is the mirror image of the Windows defect fixed alongside it: Windows put
the whole ``venv\\Scripts`` directory on PATH (every command reachable, none
protected from an inherited PYTHONPATH), POSIX linked one name through a
scrubbing shim (protected, but only one command).

The launcher writer is a standalone shell function so it can be executed here
rather than asserted about as source text -- the file the user's shell runs is
the thing under test.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import textwrap

import pytest

from hermes_cli.uninstall import remove_wrapper_script


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"

INHERITED_PYTHON_ENV_VARS = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONEXECUTABLE",
    "PYTHONUSERBASE",
)

LAUNCHER_MARKER = "hermes-agent command launcher"

PYPROJECT_FIXTURE = textwrap.dedent(
    """\
    [project]
    name = "hermes-agent"

    [project.scripts]
    hermes = "hermes_cli.main:main"
    hermes-agent = "run_agent:main"
    loop24 = "hermes_cli.main:main"
    loop24-acp = "acp_adapter.entry:main"
    never-built = "nope:main"

    [tool.setuptools]
    packages = ["hermes_cli"]
    """
)


def _extract_shell_function(text: str, name: str) -> str:
    import re

    match = re.search(rf"^{name}\(\) \{{$.*?^\}}$", text, re.MULTILINE | re.DOTALL)
    assert match, f"{name}() not found in install.sh"
    return match.group(0)


def _run_launcher_writer(tmp_path: Path) -> Path:
    """Run install.sh's launcher writer against a stub venv; return the link dir."""
    text = INSTALL_SH.read_text()

    bin_dir = tmp_path / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    python = bin_dir / "python"
    python.write_text("#!/bin/sh\necho python stub\n")
    python.chmod(0o755)
    # Only these were actually built by pip. `never-built` is declared in
    # [project.scripts] but absent, and must not get a launcher.
    for name in ("hermes", "hermes-agent", "loop24", "loop24-acp"):
        exe = bin_dir / name
        exe.write_text("#!/bin/sh\necho stub\n")
        exe.chmod(0o755)

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT_FIXTURE)

    link_dir = tmp_path / "link"

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -e\n"
        'log_info() { :; }\n'
        'log_success() { :; }\n'
        'log_warn() { :; }\n'
        + _extract_shell_function(text, "console_script_names")
        + "\n"
        + _extract_shell_function(text, "write_command_launchers")
        + "\n"
        f'names="$(console_script_names "{pyproject}")"\n'
        f'write_command_launchers "{link_dir}" "{bin_dir}" "{python}" $names\n'
    )

    proc = subprocess.run(
        ["bash", str(harness)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    return link_dir


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh missing")
def test_every_built_console_script_gets_a_launcher(tmp_path: Path) -> None:
    link_dir = _run_launcher_writer(tmp_path)

    # The regression: only `hermes` existed here, so a branded install had no
    # branded command at all.
    for name in ("hermes", "hermes-agent", "loop24", "loop24-acp"):
        launcher = link_dir / name
        assert launcher.exists(), f"no launcher for {name}"
        assert launcher.stat().st_mode & 0o111, f"{name} launcher is not executable"

    assert not (link_dir / "never-built").exists()


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh missing")
def test_each_launcher_scrubs_the_env_and_execs_its_own_entry_point(tmp_path: Path) -> None:
    link_dir = _run_launcher_writer(tmp_path)

    for name in ("hermes", "loop24"):
        body = (link_dir / name).read_text()
        for var in INHERITED_PYTHON_ENV_VARS:
            assert f"unset {var}" in body, f"{name} launcher does not clear {var}"
        assert "export PYTHONNOUSERSITE=1" in body
        # Each launcher must exec ITS OWN entry point, not hermes for everything.
        assert (
            f'exec "{tmp_path / "venv" / "bin" / "python"}" '
            f'"{tmp_path / "venv" / "bin" / name}" "$@"'
        ) in body
        # exec, not a plain call: the wrapper must not stay resident.
        assert "exec " in body
        assert LAUNCHER_MARKER in body, "launcher is not identifiable to the uninstaller"


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh missing")
def test_setup_path_delegates_to_the_launcher_writer() -> None:
    setup_path = _extract_shell_function(INSTALL_SH.read_text(), "setup_path")
    assert "write_command_launchers" in setup_path
    assert "console_script_names" in setup_path


def test_uninstall_removes_every_installed_launcher(tmp_path: Path, monkeypatch) -> None:
    """Otherwise each brand command is orphaned on PATH after uninstall."""
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    for name in ("hermes", "loop24", "loop24-acp"):
        launcher = bin_dir / name
        launcher.write_text(
            "#!/usr/bin/env bash\n"
            f"# {LAUNCHER_MARKER} (generated by scripts/install.sh)\n"
            "unset PYTHONPATH\n"
            f'exec "{home}/.loop24/hermes-agent/venv/bin/{name}" "$@"\n'
        )
        launcher.chmod(0o755)

    # A same-named file we did NOT write must survive: uninstall deletes on
    # content, never on name alone.
    foreign = bin_dir / "otto"
    foreign.write_text("#!/bin/sh\necho not ours\n")

    removed = remove_wrapper_script()

    for name in ("hermes", "loop24", "loop24-acp"):
        assert not (bin_dir / name).exists(), f"{name} launcher survived uninstall"
    assert {p.name for p in removed} == {"hermes", "loop24", "loop24-acp"}
    assert foreign.exists(), "uninstall deleted a file it did not create"


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="install.sh missing")
def test_launcher_writer_refuses_to_shim_a_directory_into_itself(tmp_path: Path) -> None:
    """A self-exec'ing launcher would loop forever instead of failing.

    With ``--no-venv`` the entry point is found via ``which hermes``, which can
    already resolve inside the link dir.
    """
    text = INSTALL_SH.read_text()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "hermes"
    exe.write_text("#!/bin/sh\necho real entry point\n")
    exe.chmod(0o755)

    harness = tmp_path / "harness.sh"
    harness.write_text(
        "set -e\n"
        + _extract_shell_function(text, "write_command_launchers")
        + "\n"
        f'write_command_launchers "{bin_dir}" "{bin_dir}" "" hermes\n'
    )
    proc = subprocess.run(["bash", str(harness)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    # The real entry point must still be there, untouched.
    assert exe.read_text() == "#!/bin/sh\necho real entry point\n"
