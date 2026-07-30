from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading

import pytest

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.scheduler import FailureClass, RunScheduler, classify_failure
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore, StorageQuotaError


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("provider_timeout", FailureClass.TRANSIENT),
        ("network_disconnect", FailureClass.TRANSIENT),
        ("rate_limit", FailureClass.TRANSIENT),
        ("authentication", FailureClass.FATAL),
        ("authorization", FailureClass.FATAL),
        ("credit_exhausted", FailureClass.FATAL),
        ("validation", FailureClass.FATAL),
        ("cancelled", FailureClass.CANCELLED),
        ("unknown_side_effect", FailureClass.RECONCILE),
        ("something_new", FailureClass.FATAL),
    ],
)
def test_provider_failures_map_to_typed_retry_outcomes(code, expected):
    assert classify_failure(code) is expected


def test_internal_provider_attempts_consume_combined_attempt_budget():
    assert (
        classify_failure(
            "provider_timeout", workflow_attempt=2, provider_attempts=3, maximum=5
        )
        is FailureClass.EXHAUSTED
    )


def test_structured_result_rejects_attempt_evidence_that_disagrees_with_audit():
    with pytest.raises(ValueError, match="structured output evidence.*provider_attempts"):
        PluginAgentRunResult(
            final_response="",
            session_id="",
            provider="fake",
            model="fake",
            status="failed",
            pending_interaction=None,
            usage={},
            audit={
                "failure_kind": "structured_output_capability_drift",
                "provider_attempts": 1,
                "model_calls": 0,
                "strategy": "prompt_json_schema",
                "adapter_version": 1,
                "schema_fingerprint": "a" * 64,
                "declaration_source": "managed_loop_default",
            },
            structured_output={
                "provider_attempts": 0,
                "model_calls": 0,
                "strategy": "prompt_json_schema",
                "adapter_version": 1,
                "schema_fingerprint": "a" * 64,
                "declaration_source": "managed_loop_default",
            },
        )


def test_run_combined_retries_bound_provider_and_workflow_attempts(
    tmp_path, workflow_writer
):
    workflow = workflow_writer(
        tmp_path / "package",
        name="run-budget",
        nodes=[{"id": "work", "prompt": "work"}],
    )
    workflow.with_name("example.hermes.yaml").write_text(
        "limits: {combined_retries: 2}\n", encoding="utf-8"
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
            idempotency_key="combined-run-budget",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )

    class ProviderTimeoutRunner:
        def __init__(self):
            self.requests = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            return PluginAgentRunResult(
                final_response="",
                session_id="",
                provider="fake",
                model="fake",
                status="failed",
                pending_interaction=None,
                usage={},
                audit={"failure_kind": "provider_timeout"},
            )

    runner = ProviderTimeoutRunner()
    result = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)

    assert result["status"] == "failed"
    assert len(runner.requests) == 1
    assert runner.requests[0].max_api_attempts == 2
    assert result["nodes"]["work"]["retry_consumed"] == 2


def test_partial_provider_count_reduces_next_request_grant(
    tmp_path, workflow_writer
) -> None:
    workflow = workflow_writer(
        tmp_path / "remaining-budget",
        name="remaining-budget",
        nodes=[{
            "id": "work",
            "prompt": "work",
            "retry": {"max_attempts": 5, "delay_ms": 1000},
        }],
    )
    workflow.with_name("example.hermes.yaml").write_text(
        "limits: {combined_retries: 3}\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "remaining-budget-home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="remaining-budget",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )

    class PartiallyConsumedRunner:
        def __init__(self) -> None:
            self.requests = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            provider_retries = 1 if len(self.requests) == 1 else 0
            return PluginAgentRunResult(
                final_response="",
                session_id="",
                provider="fake",
                model="fake",
                status="failed",
                pending_interaction=None,
                usage={},
                audit={
                    "failure_kind": "provider_timeout",
                    "provider_attempts": provider_retries,
                },
            )

    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    runner = PartiallyConsumedRunner()
    scheduler = RunScheduler(
        store,
        agent_runner=runner,
        utcnow=lambda: now,
        jitter=lambda: 0.5,
    )
    assert scheduler.advance(admitted.run_id)["status"] == "waiting_retry"
    now += timedelta(seconds=1)

    result = scheduler.advance(admitted.run_id)

    assert [request.max_api_attempts for request in runner.requests] == [3, 1]
    assert result["status"] == "failed"
    assert result["nodes"]["work"]["retry_consumed"] == 3


def test_lower_node_retry_cap_controls_first_provider_grant(
    tmp_path, workflow_writer
) -> None:
    workflow = workflow_writer(
        tmp_path / "node-budget",
        name="node-budget",
        nodes=[{
            "id": "work",
            "prompt": "work",
            "retry": {"max_attempts": 2, "delay_ms": 1000},
        }],
    )
    workflow.with_name("example.hermes.yaml").write_text(
        "limits: {combined_retries: 5}\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "node-budget-home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="node-budget",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )

    class SuccessfulRunner:
        def __init__(self) -> None:
            self.requests = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            return PluginAgentRunResult(
                final_response="done",
                session_id="node-budget",
                provider="fake",
                model="fake",
                status="completed",
                pending_interaction=None,
                usage={},
                audit={},
            )

    runner = SuccessfulRunner()
    result = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)

    assert result["status"] == "succeeded"
    assert runner.requests[0].max_api_attempts == 2


@pytest.mark.parametrize(
    "audit",
    [
        {"failure_kind": "agent_failed"},
        {"failure_kind": "agent_failed", "provider_attempts": -1},
    ],
    ids=("missing-count", "invalid-count"),
)
def test_generic_ai_failure_charges_unknown_provider_attempts_under_retry_all(
    tmp_path, workflow_writer, audit
) -> None:
    workflow = workflow_writer(
        tmp_path / "generic-ai-failure",
        name="generic-ai-failure",
        nodes=[{
            "id": "work",
            "prompt": "work",
            "retry": {
                "max_attempts": 2,
                "delay_ms": 1000,
                "on_error": "all",
            },
        }],
    )
    workflow.with_name("example.hermes.yaml").write_text(
        "limits: {combined_retries: 2}\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "generic-ai-failure-home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="generic-ai-failure",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )

    class GenericFailureRunner:
        def __init__(self) -> None:
            self.requests = []

        def run(self, request, **_kwargs):
            self.requests.append(request)
            return PluginAgentRunResult(
                final_response="",
                session_id="",
                provider="fake",
                model="fake",
                status="failed",
                pending_interaction=None,
                usage={},
                audit=audit,
            )

    runner = GenericFailureRunner()
    result = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)

    assert result["status"] == "failed"
    assert len(runner.requests) == 1
    assert runner.requests[0].max_api_attempts == 2
    assert result["last_error"]["code"] == "agent_failed"
    assert result["nodes"]["work"]["retry_consumed"] == 2


def test_host_pressure_refuses_before_worker_allocation(
    tmp_path, workflow_writer, monkeypatch
):
    package = load_workflow(workflow_writer(tmp_path / "package"))
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="pressure",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    monkeypatch.setattr(
        store,
        "_ensure_free_disk",
        lambda: (_ for _ in ()).throw(StorageQuotaError("free disk low")),
    )

    class MustNotRun:
        def execute(self, _context):
            raise AssertionError("worker allocated under host pressure")

    scheduler = RunScheduler(store)
    scheduler.executors["bash"] = MustNotRun()
    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "interrupted"
    assert result["last_error"]["code"] == "host_pressure"


def test_journal_quota_refuses_before_worker_allocation(tmp_path, workflow_writer):
    package = load_workflow(workflow_writer(tmp_path / "package"))
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="journal-pressure",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    journal = store.run_directory(admitted.run_id) / "events.jsonl"
    store.max_journal_bytes = journal.stat().st_size + 10_000

    class MustNotRun:
        def execute(self, _context):
            raise AssertionError("worker allocated past journal quota")

    scheduler = RunScheduler(store)
    scheduler.executors["bash"] = MustNotRun()
    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "interrupted"
    assert result["last_error"]["code"] == "host_pressure"
    assert "event_journal_quota" in result["last_error"]["message"]


def test_paused_run_capacity_is_enforced_at_transition(tmp_path, workflow_writer):
    store = RunStore(
        tmp_path / "home",
        max_executing_runs=2,
        max_total_workers=2,
        max_paused_runs=1,
    )
    admitted = []
    for name in ("first-pause", "second-pause"):
        package = load_workflow(
            workflow_writer(
                tmp_path / name, name=name, nodes=[{"id": "wait", "bash": "true"}]
            )
        )
        prepared = store.prepare_run_snapshot(package)
        admitted.append(
            store.start_run(
                RunAdmissionRequest(
                    workflow_name=name,
                    definition_digest=prepared.definition_digest,
                    policy_digest=prepared.policy_digest,
                    input_manifest_digest=prepared.input_manifest_digest,
                    trigger_source="cli",
                    idempotency_key=name,
                    concurrency_key=name,
                ),
                immutable_snapshot=prepared,
            )
        )

    barrier = threading.Barrier(3)

    class Pause:
        def execute(self, _context):
            barrier.wait(timeout=2)
            return NodeExecutionResult(
                "paused", metadata={"pending_interaction": {"kind": "approval"}}
            )

    schedulers = [RunScheduler(store), RunScheduler(store)]
    for scheduler in schedulers:
        scheduler.executors["bash"] = Pause()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(scheduler.advance, result.run_id)
            for scheduler, result in zip(schedulers, admitted)
        ]
        barrier.wait(timeout=2)
        outcomes = [future.result(timeout=3) for future in futures]

    assert {outcome["status"] for outcome in outcomes} == {"paused", "failed"}
    failed = next(outcome for outcome in outcomes if outcome["status"] == "failed")
    assert failed["last_error"]["code"] == "paused_capacity"
