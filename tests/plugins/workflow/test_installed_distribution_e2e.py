from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.integration
def test_extracted_wheel_registers_workflow_cli_from_a_clean_home(tmp_path: Path) -> None:
    """Exercise the installed-filesystem path used by pip and release updates."""
    artifacts = tmp_path / "artifacts"
    generated_paths = (REPO_ROOT / "build", REPO_ROOT / "hermes_agent.egg-info")
    preexisting = {path for path in generated_paths if path.exists()}
    try:
        build = subprocess.run(
            [
                "uv",
                "build",
                "--wheel",
                "--no-build-logs",
                "--out-dir",
                str(artifacts),
                ".",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )
    finally:
        for path in generated_paths:
            if path not in preexisting:
                shutil.rmtree(path, ignore_errors=True)
    assert build.returncode == 0, f"uv build failed:\n{build.stderr}"
    wheels = list(artifacts.glob("*.whl"))
    assert len(wheels) == 1

    site = tmp_path / "site"
    install = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--target",
            str(site),
            "--no-deps",
            str(wheels[0]),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert install.returncode == 0, f"wheel extraction failed:\n{install.stderr}"

    home = tmp_path / "home"
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    env["PYTHONPATH"] = str(site)
    env.pop("HERMES_BUNDLED_SKILLS_DIR", None)
    env.pop("HERMES_BUNDLED_PLUGINS_DIR", None)

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, plugins.workflow; "
                "print(pathlib.Path(plugins.workflow.__file__).resolve())"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert probe.returncode == 0, probe.stderr
    assert Path(probe.stdout.strip()).is_relative_to(site.resolve())

    command = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "workflow", "list", "--json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert command.returncode == 0, command.stderr
    envelope = json.loads(command.stdout)
    assert envelope["ok"] is True
    assert envelope["command"] == "workflow list"
    assert envelope["schema_version"] == 1
    assert isinstance(envelope["result"], list)
    assert home.is_dir()
