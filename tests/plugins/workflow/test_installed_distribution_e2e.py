from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.integration
def test_extracted_wheel_registers_workflow_cli_from_a_clean_home(
    tmp_path: Path,
) -> None:
    """Exercise installed-filesystem layout through the authorized Nix build path."""
    artifacts = tmp_path / "artifacts"
    generated_paths = (REPO_ROOT / "build", REPO_ROOT / "hermes_agent.egg-info")
    preexisting = {path for path in generated_paths if path.exists()}
    build_env = os.environ.copy()
    build_env["HERMES_NIX_BUILD"] = "1"
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
            env=build_env,
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

    showcase_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import hashlib, json; "
                "from plugins.workflow.showcase import "
                "load_verified_showcase_package; "
                "from plugins.workflow.trust import WorkflowResourceReadBudget; "
                "budget=WorkflowResourceReadBudget("
                "max_file_bytes=1048576, max_total_bytes=8388608, max_files=512); "
                "record=load_verified_showcase_package("
                "'laptop-diagnostic', read_budget=budget); "
                "fixture=budget.read_cached(record.package.root / "
                "record.scenario.input_fixtures['evidence']); "
                "print(json.dumps({'id': record.scenario.id, "
                "'verified': record.scenario.verified_bundled_provenance, "
                "'fixtures': dict(record.scenario.input_fixtures), "
                "'bindings': dict(record.scenario.input_value_bindings), "
                "'fixture_sha256': hashlib.sha256(fixture).hexdigest()}))"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert showcase_probe.returncode == 0, showcase_probe.stderr
    installed_showcase = json.loads(showcase_probe.stdout)
    bundled_fixture = (
        REPO_ROOT
        / "plugins"
        / "workflow"
        / "showcases"
        / "packages"
        / "laptop-diagnostic"
        / "fixtures"
        / "laptop-snapshot.json"
    )
    assert installed_showcase == {
        "id": "laptop-diagnostic",
        "verified": True,
        "fixtures": {"evidence": "fixtures/laptop-snapshot.json"},
        "bindings": {"symptom": "arguments"},
        "fixture_sha256": hashlib.sha256(bundled_fixture.read_bytes()).hexdigest(),
    }

    fixture = (
        REPO_ROOT
        / "tests"
        / "plugins"
        / "workflow"
        / "fixtures"
        / "store"
        / "pre-production-amendment-v2.0.9"
    )
    fixture_manifest = json.loads(
        (fixture / "fixture-manifest.json").read_text(encoding="utf-8")
    )
    workflows = home / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    shutil.copy2(fixture / "admission.db", workflows / "admission.sqlite3")
    shutil.copytree(fixture / "runs", workflows / "runs", dirs_exist_ok=True)
    with sqlite3.connect(workflows / "admission.sqlite3") as connection:
        legacy_directory = connection.execute(
            "SELECT run_directory FROM runs WHERE run_id='migration-run'"
        ).fetchone()[0]
        legacy_prefix = str(fixture_manifest["legacy_run_directory_prefix"])
        relocated = str(workflows) + legacy_directory[len(legacy_prefix) :]
        connection.execute(
            "UPDATE runs SET run_directory=? WHERE run_id='migration-run'",
            (relocated,),
        )

    migration_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sqlite3; "
                "from plugins.workflow.store import RunStore; "
                f"store=RunStore({str(home)!r}); "
                "connection=sqlite3.connect(store.database); "
                "columns=[row[1] for row in connection.execute("
                "'PRAGMA table_info(runs)')]; "
                "version=connection.execute('PRAGMA user_version').fetchone()[0]; "
                "connection.close(); "
                "print(json.dumps({'version': version, 'columns': columns}))"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert migration_probe.returncode == 0, migration_probe.stderr
    migrated = json.loads(migration_probe.stdout)
    assert migrated["version"] == 14
    assert "scheduled_at" in migrated["columns"]
    assert {
        "foreground_boot_id",
        "foreground_heartbeat_monotonic",
        "foreground_lease_seconds",
    } <= set(migrated["columns"])
