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

    schema_command = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.main",
            "workflow",
            "schema",
            "--profile",
            "archon-2026-07",
            "--json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert schema_command.returncode == 0, schema_command.stderr
    installed_contract = json.loads(schema_command.stdout)
    bash_schema = installed_contract["definition_schema"]["properties"]["nodes"][
        "items"
    ]["properties"]["bash"]
    timeout_schema = installed_contract["definition_schema"]["properties"]["nodes"][
        "items"
    ]["properties"]["timeout"]
    assert installed_contract["normalizer_version"] == 3
    assert timeout_schema["x-hermes-unit"] == "milliseconds"
    assert timeout_schema["x-hermes-semantics"]["omitted"] == 120_000
    assert bash_schema["x-hermes-semantics"] == {
        "inline_utf8_bytes": 32_768,
        "spill_value_utf8_bytes": 500_000,
        "spill_files": 64,
        "spill_total_utf8_bytes": 2_000_000,
        "large_values": "contents",
        "contexts": {
            "unquoted_token": "substitute",
            "double_quoted_token": "substitute",
            "single_quoted_token": "safe_quote_boundary",
            "escaped_or_comment": "literal",
        },
        "unsupported_context": "fail",
    }

    installed_flow = tmp_path / "installed-official.yaml"
    installed_flow.write_text(
        """name: installed-official
description: Installed Archon authoring flow
nodes:
  - id: prepare
    bash: 'marker="$ARTIFACTS_DIR/retry-marker"; if [ ! -f "$marker" ]; then : > "$marker"; exit 1; fi; printf 2'
    timeout: 120000
    retry: {max_attempts: 1, delay_ms: 1000, on_error: all}
  - id: consume
    bash: printf consumed
    depends_on: [prepare]
    when: $prepare.output >= 2
""",
        encoding="utf-8",
    )
    installed_flow.with_name("installed-official.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    installed_execution = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from datetime import datetime, timezone
import json
import sys

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.schema import load_workflow
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore

package = load_workflow(sys.argv[1])
store = RunStore(sys.argv[2])
snapshot = store.prepare_run_snapshot(package)
admitted = store.start_run(
    RunAdmissionRequest(
        workflow_name=package.definition.name,
        definition_digest=snapshot.definition_digest,
        policy_digest=snapshot.policy_digest,
        input_manifest_digest=snapshot.input_manifest_digest,
        trigger_source="cli",
        idempotency_key="installed",
        concurrency_key="installed",
    ),
    immutable_snapshot=snapshot,
)
clock = [datetime.now(timezone.utc)]
scheduler = RunScheduler(store, utcnow=lambda: clock[0])
shutdown = False
try:
    result = scheduler.advance(admitted.run_id)
    clock[0] = datetime.fromisoformat(
        next(
            node["next_attempt_at"]
            for node in result["nodes"].values()
            if node.get("next_attempt_at")
        )
    )
    result = scheduler.advance(admitted.run_id)
finally:
    scheduler.shutdown(deadline_seconds=2)
    shutdown = True
print(json.dumps({
    "status": result["status"],
    "attempts": len(result["nodes"]["prepare"]["attempts"]),
    "consumer": result["nodes"]["consume"]["state"],
    "shutdown": shutdown,
}))
""",
            str(installed_flow),
            str(home),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert installed_execution.returncode == 0, installed_execution.stderr
    assert json.loads(installed_execution.stdout) == {
        "status": "succeeded",
        "attempts": 2,
        "consumer": "succeeded",
        "shutdown": True,
    }

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
    installed_probe = probe_root / "installed_probe.py"
    installed_probe.write_text(
        """
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys

from agent.plugin_agent import PluginAgentRunResult
from hermes_cli.runtime_provider import ExecutionRuntimeCapabilities
import plugins.workflow
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.cli import doctor_package
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    execution_capability_context,
)
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


class RecordingRunner:
    def __init__(self, *, declaration_source: str, api_mode: str) -> None:
        self.calls = 0
        self.declaration_source = declaration_source
        self.api_mode = api_mode
        self.structured_request_bound = False

    def run(self, request, **_kwargs):
        self.calls += 1
        structured = request.structured_output
        self.structured_request_bound = structured is not None
        assert structured is not None
        evidence = {
            "provider_attempts": 1,
            "model_calls": 1,
            "strategy": structured.strategy.value,
            "adapter_version": structured.adapter_version,
            "schema_fingerprint": structured.schema.schema_fingerprint,
            "declaration_source": self.declaration_source,
        }
        return PluginAgentRunResult(
            final_response=' { "answer": "ready" }\\n',
            session_id=f"installed-{self.calls}",
            provider=request.provider or "installed-provider",
            model=request.model or "installed-model",
            status="completed",
            pending_interaction=None,
            usage={"input_tokens": 1, "output_tokens": 1},
            audit={**evidence, "api_calls": 1, "api_mode": self.api_mode},
            structured_output=evidence,
        )


def admit_and_advance(package, store, *, idempotency_key: str):
    execution_context = execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=ExecutionRuntimeCapabilities(
            api_mode="chat_completions",
            hermes_managed_tool_loop=True,
            effective_provider="installed-provider",
            model="installed-model",
        ),
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=idempotency_key,
            concurrency_key=package.definition.name,
            run_metadata=execution_context.structured_output_run_metadata(package),
        ),
        immutable_snapshot=prepared,
    )
    decision = execution_context.structured_output_decisions(package)["producer"]
    runner = RecordingRunner(
        declaration_source=decision.declaration_source,
        api_mode=decision.api_mode,
    )
    result = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)
    return result, runner, store.run_directory(admitted.run_id)


stage, schemaless_path, structured_path, installed_home = sys.argv[1:]
store = RunStore(installed_home)
structured = load_workflow(structured_path)
report = doctor_package(structured, hermes_home=installed_home)
payload = {
    "validator_present": importlib.util.find_spec("jsonschema") is not None,
    "workflow_module": str(Path(plugins.workflow.__file__).resolve()),
}

if stage == "pre":
    plain = load_workflow(schemaless_path)
    snapshot = store.prepare_run_snapshot(plain)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=plain.definition.name,
            definition_digest=snapshot.definition_digest,
            policy_digest=snapshot.policy_digest,
            input_manifest_digest=snapshot.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="lean",
            concurrency_key=plain.definition.name,
        ),
        immutable_snapshot=snapshot,
    )
    payload["schemaless_status"] = RunScheduler(store).advance(
        admitted.run_id
    )["status"]
    result, runner, _run_directory = admit_and_advance(
        structured, store, idempotency_key="structured-pre"
    )
    attempt = result["nodes"]["producer"]["attempts"][-1]
    payload.update({
        "structured_status": result["status"],
        "structured_failure": attempt["error_message"],
        "provider_calls": runner.calls,
    })
else:
    result, runner, run_directory = admit_and_advance(
        structured, store, idempotency_key="structured-post"
    )
    attempt = result["nodes"]["producer"]["attempts"][-1]
    candidate = attempt["metadata"]["primary_output_candidate"]
    canonical = (run_directory / candidate["attempt_relative_path"]).read_bytes()
    requirements = importlib.metadata.requires("hermes-agent") or ()
    payload.update({
        "mcp_extra_declared": any(
            item.startswith("mcp==") and 'extra == "mcp"' in item
            for item in requirements
        ),
        "runnable": report.runnable,
        "blocking_codes": [item.code for item in report.findings if item.blocking],
        "structured_nodes": sorted(structured.language.structured_outputs),
        "structured_status": result["status"],
        "provider_calls": runner.calls,
        "structured_request_bound": runner.structured_request_bound,
        "canonical_output": canonical.decode("utf-8"),
        "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
    })

print(json.dumps(payload))
""".strip()
        + "\n",
        encoding="utf-8",
    )
    lean_probe = subprocess.run(
        [
            str(installed_python),
            str(installed_probe),
            "pre",
            str(schemaless_path),
            str(structured_path),
            str(installed_home),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=installed_env,
        timeout=180,
    )
    assert lean_probe.returncode == 0, lean_probe.stderr
    lean_result = json.loads(lean_probe.stdout)
    assert Path(lean_result.pop("workflow_module")).is_relative_to(
        installed_venv.resolve()
    )
    assert lean_result == {
        "schemaless_status": "succeeded",
        "validator_present": False,
        "structured_status": "failed",
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
            str(installed_probe),
            "post",
            str(schemaless_path),
            str(structured_path),
            str(installed_home),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=installed_env,
        timeout=180,
    )
    assert extra_probe.returncode == 0, extra_probe.stderr
    extra_result = json.loads(extra_probe.stdout)
    assert Path(extra_result.pop("workflow_module")).is_relative_to(
        installed_venv.resolve()
    )
    assert extra_result == {
        "validator_present": True,
        "mcp_extra_declared": True,
        "runnable": True,
        "blocking_codes": [],
        "structured_nodes": ["producer"],
        "structured_status": "succeeded",
        "provider_calls": 1,
        "structured_request_bound": True,
        "canonical_output": '{"answer":"ready"}',
        "canonical_sha256": hashlib.sha256(b'{"answer":"ready"}').hexdigest(),
    }
