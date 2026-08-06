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
    assert installed_contract["normalizer_version"] == 4
    assert timeout_schema["x-hermes-unit"] == "milliseconds"
    assert timeout_schema["x-hermes-semantics"]["omitted"] == 120_000
    assert bash_schema["x-hermes-semantics"] == {
        "inline_utf8_bytes": 32_768,
        "rendered_command_utf8_bytes": 98_304,
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

    phase4_root = tmp_path / "phase4-root"
    phase4_child = tmp_path / "phase4-child"
    (phase4_root / "workflows").mkdir(parents=True)
    (phase4_child / "workflows").mkdir(parents=True)
    (phase4_child / "commands").mkdir()
    root_workflow = phase4_root / "workflows" / "installed-v4-root.yaml"
    child_workflow = phase4_child / "workflows" / "installed-v4-child.yaml"
    root_workflow.write_text(
        """name: installed-v4-root
description: Installed explicit-v4 root
interactive: true
nodes:
  - id: child
    include: installed-v4-child
""",
        encoding="utf-8",
    )
    root_workflow.with_name("installed-v4-root.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    child_workflow.write_text(
        """name: installed-v4-child
description: Installed explicit-v4 child
nodes:
  - id: refine
    loop:
      command: refine
      until: DONE
      max_iterations: 2
      interactive: true
      gate_message: Accept this sealed result or provide feedback
""",
        encoding="utf-8",
    )
    child_workflow.with_name("installed-v4-child.hermes.yaml").write_text(
        "required_secrets: [IGNORED_CHILD_SECRET]\n",
        encoding="utf-8",
    )
    (phase4_child / "commands" / "refine.md").write_text(
        "---\ndescription: Refine\n---\nUse the sealed command body.\n",
        encoding="utf-8",
    )
    phase4_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            r"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.language_schema import workflow_authoring_contract
from plugins.workflow.models import WorkflowLanguageProfile
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import parse_workflow_source_bytes, validate_package
from plugins.workflow.store import RunStore
from plugins.workflow.trust import (
    WorkflowPackageDigest,
    WorkflowTrustStore,
    build_risk_summary,
)


class SignalRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, request, **_kwargs):
        self.calls += 1
        return PluginAgentRunResult(
            final_response="installed result <promise>DONE</promise>",
            session_id=f"installed-v4-{self.calls}",
            provider=request.provider or "installed-provider",
            model=request.model or "installed-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={"provider_attempts": 1},
        )


root_path = Path(sys.argv[1])
child_path = Path(sys.argv[2])
home = Path(sys.argv[3])
root = parse_workflow_source_bytes(
    root_path,
    workflow_bytes=root_path.read_bytes(),
    sidecar_bytes=root_path.with_name("installed-v4-root.hermes.yaml").read_bytes(),
    source="project",
    precedence=1,
)
child = parse_workflow_source_bytes(
    child_path,
    workflow_bytes=child_path.read_bytes(),
    sidecar_bytes=child_path.with_name("installed-v4-child.hermes.yaml").read_bytes(),
    source="profile",
    precedence=2,
)
compilation = compile_workflow(
    root,
    WorkflowCatalogSnapshot.capture((root, child)),
)
package = compilation.package
validation = validate_package(package)
compatibility = assess_compatibility(package)
risk = build_risk_summary(package, compatibility, compilation=compilation)
trust = WorkflowTrustStore(home)
trust.trust(compilation.composite_digest, actor="installed-test", risk_digest=risk.risk_digest)
store = RunStore(home)
prepared = store.prepare_run_snapshot(
    package,
    compilation=compilation,
    trusted_package_digest=WorkflowPackageDigest(
        compilation.composite_digest,
        compilation.covered_relative_paths,
    ),
)
admitted = store.start_run(
    RunAdmissionRequest(
        workflow_name=package.definition.name,
        definition_digest=prepared.definition_digest,
        policy_digest=prepared.policy_digest,
        input_manifest_digest=prepared.input_manifest_digest,
        trigger_source="cli",
        idempotency_key="installed-v4",
        concurrency_key=package.definition.name,
    ),
    immutable_snapshot=prepared,
)
runner = SignalRunner()
scheduler = RunScheduler(store, agent_runner=runner)
try:
    scheduler.advance(admitted.run_id)
finally:
    scheduler.shutdown(deadline_seconds=2)
paused = store.get_run_status(admitted.run_id)
pending = paused["pending_interaction"]
shutil.rmtree(root_path.parent.parent)
shutil.rmtree(child_path.parent.parent)
resumed_store = RunStore(home)
resumed_scheduler = RunScheduler(resumed_store)
try:
    resumed_package = resumed_scheduler._load_run_package(admitted.run_id)
finally:
    resumed_scheduler.shutdown(deadline_seconds=2)
resumed_store.approve_run(
    admitted.run_id,
    expected_state_version=paused["state_version"],
    interaction_id=pending["interaction_id"],
    actor="installed-operator",
    channel="cli",
)
completed = resumed_store.load_run(admitted.run_id)
evidence = EvidenceReader(resumed_store).query(
    admitted.run_id,
    kind="interactions",
    limit=20,
)
default_contract = workflow_authoring_contract(
    WorkflowLanguageProfile.ARCHON_2026_07
)
explicit_contract = workflow_authoring_contract(
    WorkflowLanguageProfile.ARCHON_2026_07,
    normalizer_version=4,
)
print(json.dumps({
    "default_normalizer": default_contract["normalizer_version"],
    "explicit_normalizer": explicit_contract["normalizer_version"],
    "phase4_codes": sorted(
        code
        for code, entry in explicit_contract["compatibility_codes"].items()
        if entry.get("normalizer_versions") == [4]
    ),
    "validation_blockers": [issue.code for issue in validation if issue.blocking],
    "compatibility_runnable": compatibility.runnable,
    "compatibility_codes": sorted(finding.code for finding in compatibility.findings),
    "trust": trust.check(
        compilation.composite_digest,
        risk_digest=risk.risk_digest,
    ),
    "expanded_nodes": [node.id for node in package.definition.nodes],
    "paused_status": paused["status"],
    "pending_type": pending["type"],
    "pending_actions": paused["next_actions"],
    "provider_calls": runner.calls,
    "sources_removed": not root_path.exists() and not child_path.exists(),
    "resumed_normalizer": resumed_package.language.normalizer_version,
    "completed_status": completed["status"],
    "event_types": [
        item["event_type"]
        for item in resumed_store.tail_events(admitted.run_id, limit=50)
        if item["event_type"].startswith("loop_signal_")
    ],
    "evidence_event_types": [
        item.get("event_type") for item in evidence["items"]
    ],
}))
""",
            str(root_workflow),
            str(child_workflow),
            str(home / "phase4"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert phase4_probe.returncode == 0, phase4_probe.stderr
    phase4_result = json.loads(phase4_probe.stdout)
    assert phase4_result["default_normalizer"] == 4
    assert phase4_result["explicit_normalizer"] == 4
    assert {
        "include_not_found",
        "include_cycle",
        "include_resource_invalid",
    } <= set(phase4_result["phase4_codes"])
    assert phase4_result["validation_blockers"] == []
    assert phase4_result["compatibility_runnable"] is True
    assert {
        "phase4_loop_prompt_sealed",
        "phase4_signal_confirmation",
    } <= set(phase4_result["compatibility_codes"])
    assert phase4_result["trust"] == "trusted"
    assert phase4_result["expanded_nodes"] == ["child__refine"]
    assert phase4_result["paused_status"] == "paused"
    assert phase4_result["pending_type"] == "loop_signal_confirmation"
    assert phase4_result["pending_actions"] == [
        "status",
        "events",
        "approve",
        "provide-input",
        "cancel",
    ]
    assert phase4_result["provider_calls"] == 1
    assert phase4_result["sources_removed"] is True
    assert phase4_result["resumed_normalizer"] == 4
    assert phase4_result["completed_status"] == "succeeded"
    assert phase4_result["event_types"] == [
        "loop_signal_confirmation_required",
        "loop_signal_accepted",
    ]
    assert phase4_result["evidence_event_types"] == [
        "loop_signal_confirmation_required",
        "loop_signal_accepted",
    ]

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
