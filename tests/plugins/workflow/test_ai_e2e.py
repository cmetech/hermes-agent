from __future__ import annotations

import time

import pytest

from agent.plugin_agent import PluginAgentRunResult
from hermes_cli.runtime_provider import ExecutionRuntimeCapabilities
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    execution_capability_context,
)
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


class RecordingRunner:
    def __init__(
        self,
        response="investigated",
        *,
        declaration_source="default_prompt_adapter",
    ):
        self.requests = []
        self.response = response
        self.declaration_source = declaration_source

    def run(self, request, **_kwargs):
        self.requests.append(request)
        evidence = None
        audit = {}
        if request.structured_output is not None:
            evidence = {
                "provider_attempts": 1,
                "model_calls": 1,
                "strategy": request.structured_output.strategy.value,
                "adapter_version": request.structured_output.adapter_version,
                "schema_fingerprint": (
                    request.structured_output.schema.schema_fingerprint
                ),
                "declaration_source": self.declaration_source,
            }
            audit.update(evidence)
        return PluginAgentRunResult(
            final_response=self.response,
            session_id="ai-session",
            provider=request.provider or "fake",
            model=request.model or "fake",
            status="completed",
            pending_interaction=None,
            usage={"input_tokens": 1, "output_tokens": 1},
            audit=audit,
            structured_output=evidence,
        )


def test_command_node_runs_from_immutable_snapshot_through_scheduler(
    tmp_path, workflow_writer
):
    root = tmp_path / "package"
    workflow = workflow_writer(
        root,
        name="ai-command",
        nodes=[{"id": "investigate", "command": "investigate"}],
    )
    (root / "commands").mkdir()
    (root / "commands" / "investigate.md").write_text(
        "---\ndescription: Investigate\n---\nInvestigate $ARGUMENTS"
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package, values={"arguments": "disk"})
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="ai-command",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="ai-one",
            concurrency_key="ai-command",
        ),
        immutable_snapshot=prepared,
    )
    runner = RecordingRunner()

    result = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)

    assert result["status"] == "succeeded"
    assert runner.requests[0].prompt == "Investigate disk"
    assert result["nodes"]["investigate"]["session_id"] == "ai-session"
    output = (
        store.run_directory(admitted.run_id) / result["artifacts"][0]["relative_path"]
    )
    assert output.read_text() == "investigated"


@pytest.mark.parametrize(
    ("run_metadata", "expected_status", "expected_error"),
    [
        ({"ai_entitlement": "unknown"}, "succeeded", None),
        (
            {"showcase_provenance": "verified_bundled"},
            "succeeded",
            None,
        ),
        (
            {
                "ai_entitlement": "real",
                "showcase_provenance": "verified_bundled",
                "showcase_id": "approval-gate",
                "showcase_version": "1.0.0",
                "bundle_digest": "a" * 64,
                "risk_digest": "b" * 64,
            },
            "failed",
            "execution_integrity",
        ),
    ],
    ids=("malformed", "current-marker-absence", "uncorroborated-real"),
)
def test_scheduler_enforces_derived_entitlement_before_real_runner(
    tmp_path,
    workflow_writer,
    run_metadata,
    expected_status,
    expected_error,
) -> None:
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="entitlement-scheduler",
            nodes=[{"id": "work", "prompt": "work"}],
        )
    )
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="derived-entitlement",
            concurrency_key=package.definition.name,
            run_metadata=run_metadata,
        ),
        immutable_snapshot=prepared,
    )
    runner = RecordingRunner()

    result = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)

    assert result["status"] == expected_status
    assert (
        result["last_error"]["code"] if result["last_error"] is not None else None
    ) == expected_error
    assert runner.requests == []


def test_scheduler_sidecar_caps_normal_ai_request_fields_exactly(
    tmp_path, workflow_writer
):
    workflow = workflow_writer(
        tmp_path / "package",
        name="bounded-ai",
        nodes=[{"id": "work", "prompt": "work", "idle_timeout": 99}],
    )
    workflow.with_name("example.hermes.yaml").write_text(
        "limits:\n"
        "  ai_idle_timeout_seconds: 10\n"
        "  ai_wall_timeout_seconds: 20\n"
        "  provider_request_timeout_seconds: 8\n"
        "  combined_retries: 3\n"
        "  cooperative_shutdown_seconds: 1\n"
        "  term_grace_seconds: 2\n"
        "  kill_reap_grace_seconds: 1\n"
        "resource_limits:\n"
        f"  process_tree_rss_bytes: {64 * 1024 * 1024}\n"
        "  process_tree_cpu_seconds: 9\n"
        "  max_descendants: 2\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="bounded-ai-request",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    runner = RecordingRunner()
    now = time.monotonic()

    result = RunScheduler(store, agent_runner=runner, monotonic=lambda: now).advance(
        admitted.run_id
    )

    assert result["status"] == "succeeded"
    request = runner.requests[0]
    assert request.idle_timeout_seconds == 10
    # DeadlineBudget stores an absolute deadline (now + requested) and reports
    # remaining_wall() as (deadline - now). That round-trip is inexact whenever
    # now + requested crosses a binary exponent boundary -- ~7% of freshly
    # booted CI clocks for requested=20, 0% at a long-uptime dev box, which is
    # why this only ever failed on macos-latest. The value is correct; exact
    # float equality is the wrong assertion.
    assert request.wall_timeout_seconds == pytest.approx(20, abs=1e-9)
    assert request.provider_request_timeout_seconds == 8
    assert request.max_api_attempts == 3
    assert request.max_process_tree_rss_bytes == 64 * 1024 * 1024
    assert request.max_process_tree_cpu_seconds == 9
    assert request.max_descendants == 2
    assert request.cooperative_shutdown_seconds == 1
    assert request.term_grace_seconds == 2
    assert request.kill_reap_grace_seconds == 1
    assert request.max_iterations == 90


def test_archon_scheduler_binds_sealed_structured_request_and_canonical_output(
    tmp_path, workflow_writer
):
    workflow = workflow_writer(
        tmp_path / "package",
        name="archon-structured-e2e",
        provider="fake-provider",
        model="fake-model",
        nodes=[
            {
                "id": "work",
                "prompt": "produce",
                "output_format": {
                    "type": "object",
                    "required": ["a", "b"],
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "boolean"},
                    },
                },
            }
        ],
    )
    workflow.with_name("example.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    execution_context = execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=ExecutionRuntimeCapabilities(
            api_mode="chat_completions",
            hermes_managed_tool_loop=True,
            effective_provider="fake-provider",
            model="fake-model",
        ),
    )
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="archon-structured",
            concurrency_key=package.definition.name,
            run_metadata=execution_context.structured_output_run_metadata(package),
        ),
        immutable_snapshot=prepared,
    )
    decision = execution_context.structured_output_decisions(package)["work"]
    runner = RecordingRunner(
        ' { "b": true, "a": 1 }\n',
        declaration_source=decision.declaration_source,
    )

    result = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)

    assert result["last_error"] is None, result["last_error"]
    assert result["status"] == "succeeded", result
    assert runner.requests[0].structured_output is not None
    assert runner.requests[0].structured_output.schema.schema_fingerprint == (
        package.language.structured_outputs["work"].schema_fingerprint
    )
    output = store.run_directory(admitted.run_id) / result["artifacts"][0][
        "relative_path"
    ]
    assert output.read_bytes() == b'{"a":1,"b":true}'
