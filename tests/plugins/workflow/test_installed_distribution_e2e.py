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

    installed_venv = tmp_path / "installed-venv"
    create_venv = subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(installed_venv)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert create_venv.returncode == 0, create_venv.stderr
    installed_python = installed_venv / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    lean_install = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(installed_python),
            str(wheels[0]),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert lean_install.returncode == 0, lean_install.stderr

    probe_root = tmp_path / "validator-probe"
    probe_root.mkdir()
    schemaless_path = probe_root / "schemaless.yaml"
    schemaless_path.write_text(
        json.dumps({
            "name": "installed-schemaless",
            "description": "Installed schemaless workflow",
            "nodes": [{"id": "run", "bash": "printf installed"}],
        }),
        encoding="utf-8",
    )
    structured_path = probe_root / "structured.yaml"
    structured_path.write_text(
        json.dumps({
            "name": "installed-structured",
            "description": "Installed structured workflow",
            "nodes": [
                {
                    "id": "producer",
                    "prompt": "Return a result",
                    "output_format": {
                        "type": "object",
                        "required": ["answer"],
                        "properties": {"answer": {"type": "string"}},
                    },
                }
            ],
        }),
        encoding="utf-8",
    )
    structured_path.with_name("structured.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    installed_home = tmp_path / "installed-home"
    installed_env = os.environ.copy()
    installed_env["HERMES_HOME"] = str(installed_home)
    installed_env.pop("PYTHONPATH", None)
    lean_probe = subprocess.run(
        [
            str(installed_python),
            "-c",
            (
                "import importlib.util,json; "
                "from plugins.workflow.admission import RunAdmissionRequest; "
                "from plugins.workflow.cli import doctor_package; "
                "from plugins.workflow.schema import load_workflow; "
                "from plugins.workflow.scheduler import RunScheduler; "
                "from plugins.workflow.store import RunStore; "
                f"plain=load_workflow({str(schemaless_path)!r}); "
                f"store=RunStore({str(installed_home)!r}); "
                "snap=store.prepare_run_snapshot(plain); "
                "run=store.start_run(RunAdmissionRequest("
                "workflow_name=plain.definition.name,"
                "definition_digest=snap.definition_digest,"
                "policy_digest=snap.policy_digest,"
                "input_manifest_digest=snap.input_manifest_digest,"
                "trigger_source='cli',idempotency_key='lean',"
                "concurrency_key=plain.definition.name),immutable_snapshot=snap); "
                "status=RunScheduler(store).advance(run.run_id)['status']; "
                f"structured=load_workflow({str(structured_path)!r}); "
                f"report=doctor_package(structured,hermes_home={str(installed_home)!r}); "
                "failure=next(item.message for item in report.findings "
                "if item.code=='structured_output_unavailable'); provider_calls=0; "
                "print(json.dumps({'schemaless_status':status,"
                "'validator_present':importlib.util.find_spec('jsonschema') is not None,"
                "'structured_failure':failure,'provider_calls':provider_calls}))"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=installed_env,
        timeout=180,
    )
    assert lean_probe.returncode == 0, lean_probe.stderr
    lean_result = json.loads(lean_probe.stdout)
    assert lean_result == {
        "schemaless_status": "succeeded",
        "validator_present": False,
        "structured_failure": (
            "jsonschema is required; install the Hermes mcp or all extra"
        ),
        "provider_calls": 0,
    }

    extra_install = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(installed_python),
            f"{wheels[0]}[mcp]",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert extra_install.returncode == 0, extra_install.stderr
    extra_probe = subprocess.run(
        [
            str(installed_python),
            "-c",
            (
                "import importlib.metadata,importlib.util,json; "
                "from plugins.workflow.cli import doctor_package; "
                "from plugins.workflow.schema import load_workflow; "
                f"package=load_workflow({str(structured_path)!r}); "
                f"report=doctor_package(package,hermes_home={str(installed_home)!r}); "
                "declared=any('extra == \"mcp\"' in item and "
                "item.startswith('mcp==') for item in "
                "importlib.metadata.requires('hermes-agent')); "
                "print(json.dumps({'validator_present':"
                "importlib.util.find_spec('jsonschema') is not None,"
                "'mcp_extra_declared':declared,'runnable':report.runnable,"
                "'blocking_codes':[item.code for item in report.findings "
                "if item.blocking],"
                "'structured_nodes':sorted(package.language.structured_outputs)}))"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=installed_env,
        timeout=180,
    )
    assert extra_probe.returncode == 0, extra_probe.stderr
    assert json.loads(extra_probe.stdout) == {
        "validator_present": True,
        "mcp_extra_declared": True,
        "runnable": True,
        "blocking_codes": [],
        "structured_nodes": ["producer"],
    }
