from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, fields
from datetime import datetime, timedelta, timezone
import shlex
import sys
import time

import pytest
import yaml

import plugins.workflow.models as workflow_models
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.models import (
    DeadlineBudget,
    RunExecutionLimits,
    WorkflowRuntimeConfig,
)
from plugins.workflow.scheduler import RunScheduler
from tests.plugins.workflow_history import load_recorded_v4_workflow as load_workflow
from plugins.workflow.store import RunStore
from tools.managed_process import ProcessResourceLimits


def test_run_execution_limits_is_frozen_and_contains_only_per_run_fields():
    record_type = getattr(workflow_models, "RunExecutionLimits", None)

    assert record_type is not None, "RunExecutionLimits is not implemented"
    assert tuple(field.name for field in fields(record_type)) == (
        "max_parallel_nodes",
        "max_total_workers",
        "ai_idle_timeout_seconds",
        "ai_wall_timeout_seconds",
        "provider_request_timeout_seconds",
        "combined_retries",
        "subprocess_timeout_seconds",
        "process_tree_rss_bytes",
        "process_tree_cpu_seconds",
        "max_descendants",
        "cooperative_shutdown_seconds",
        "term_grace_seconds",
        "kill_reap_grace_seconds",
    )
    limits = record_type()
    with pytest.raises(FrozenInstanceError):
        limits.max_parallel_nodes = 99


def test_run_execution_limits_resolve_every_field_by_tightening_profile():
    profile = WorkflowRuntimeConfig(
        max_parallel_nodes=10,
        max_total_workers=20,
        ai_idle_timeout_seconds=200,
        ai_wall_timeout_seconds=400,
        provider_request_timeout_seconds=180,
        combined_retries=5,
        subprocess_timeout_seconds=100,
        process_tree_rss_bytes=1024 * 1024 * 1024,
        process_tree_cpu_seconds=500,
        max_descendants=20,
        cooperative_shutdown_seconds=8,
        term_grace_seconds=7,
        kill_reap_grace_seconds=6,
    )
    sidecar_limits = {
        "max_parallel_nodes": 3,
        "max_total_workers": 30,
        "ai_idle_timeout_seconds": 150,
        "ai_wall_timeout_seconds": 120,
        "provider_request_timeout_seconds": 130,
        "combined_retries": 4,
        "subprocess_timeout_seconds": 90,
        "cooperative_shutdown_seconds": 4,
        "term_grace_seconds": 9,
        "kill_reap_grace_seconds": 3,
    }
    sidecar_resources = {
        "process_tree_rss_bytes": 2 * 1024 * 1024 * 1024,
        "process_tree_cpu_seconds": 300,
        "max_descendants": 0,
    }

    limits = workflow_models.RunExecutionLimits.resolve(
        profile,
        sidecar_limits=sidecar_limits,
        sidecar_resources=sidecar_resources,
    )
    sidecar_limits["max_parallel_nodes"] = 1
    sidecar_resources["max_descendants"] = 19

    assert asdict(limits) == {
        "max_parallel_nodes": 3,
        "max_total_workers": 20,
        "ai_idle_timeout_seconds": 120,
        "ai_wall_timeout_seconds": 120,
        "provider_request_timeout_seconds": 120,
        "combined_retries": 4,
        "subprocess_timeout_seconds": 90,
        "process_tree_rss_bytes": 1024 * 1024 * 1024,
        "process_tree_cpu_seconds": 300,
        "max_descendants": 0,
        "cooperative_shutdown_seconds": 4,
        "term_grace_seconds": 7,
        "kill_reap_grace_seconds": 3,
    }


def test_deadline_inheritance_uses_absolute_monotonic_minimum():
    parent = DeadlineBudget.create(
        now=100.0,
        wall_seconds=30,
        idle_seconds=10,
        provider_seconds=8,
    )
    child = parent.child(
        now=112.0,
        requested_wall_seconds=40,
        workflow_cap_seconds=9,
        idle_seconds=5,
        provider_seconds=20,
    )

    assert child.wall_deadline == 121.0
    assert child.provider_deadline(now=112.0) == 120.0


def test_semantic_progress_resets_idle_but_heartbeat_does_not_extend_wall():
    budget = DeadlineBudget.create(
        now=10.0, wall_seconds=20, idle_seconds=5, provider_seconds=3
    )
    budget.heartbeat(13.0)
    assert budget.idle_expired(15.1)
    budget.semantic_progress(15.1)
    assert not budget.idle_expired(20.0)
    assert budget.wall_expired(30.0)


def test_runtime_config_is_bounded_and_sidecar_can_only_tighten():
    config = WorkflowRuntimeConfig.from_mapping(
        {
            "max_parallel_nodes": 3,
            "ai_wall_timeout_seconds": 100,
            "ai_idle_timeout_seconds": 50,
            "provider_request_timeout_seconds": 40,
        },
        sidecar_limits={
            "max_parallel_nodes": 2,
            "ai_wall_timeout_seconds": 200,
        },
        sidecar_resources={"max_descendants": 4},
    )

    assert config.max_parallel_nodes == 2
    assert config.ai_wall_timeout_seconds == 100
    assert config.max_descendants == 4
    assert config.cooperative_shutdown_seconds == 5
    assert config.term_grace_seconds == 5
    assert config.kill_reap_grace_seconds == 2
    with pytest.raises(ValueError, match="finite"):
        WorkflowRuntimeConfig(ai_wall_timeout_seconds=float("inf"))


def test_scheduler_threads_limits_from_sealed_snapshot_to_process_boundary(
    tmp_path, workflow_writer
):
    workflow = workflow_writer(tmp_path / "package", filename="bounded.yaml")
    sidecar = workflow.with_name("bounded.hermes.yaml")
    sidecar.write_text(
        "limits:\n"
        "  max_parallel_nodes: 1\n"
        "  max_total_workers: 2\n"
        "  ai_idle_timeout_seconds: 10\n"
        "  ai_wall_timeout_seconds: 20\n"
        "  provider_request_timeout_seconds: 8\n"
        "  combined_retries: 3\n"
        "  subprocess_timeout_seconds: 7\n"
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
    store = RunStore(tmp_path / "home", max_total_workers=6)
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="sealed-limits",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    observed = []

    class CaptureProcessBoundary:
        def execute(self, context):
            observed.append(context)
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(
        store,
        max_parallel_nodes=5,
        ai_idle_timeout_seconds=50,
        ai_wall_timeout_seconds=100,
        provider_request_timeout_seconds=40,
        subprocess_timeout_seconds=99,
        default_max_attempts=5,
        cooperative_shutdown_seconds=5,
        term_grace_seconds=5,
        kill_reap_grace_seconds=2,
        resource_limits=ProcessResourceLimits(
            max_rss_bytes=512 * 1024 * 1024,
            max_cpu_seconds=90,
            max_descendants=20,
        ),
    )
    scheduler.executors["bash"] = CaptureProcessBoundary()
    sidecar.write_text("limits: {subprocess_timeout_seconds: 1}\n", encoding="utf-8")
    scheduler.subprocess_timeout_seconds = 1
    scheduler.resource_limits = ProcessResourceLimits(max_descendants=0)

    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "succeeded"
    context = observed[0]
    assert context.timeout_seconds == 7
    assert asdict(context.execution_limits) == {
        "max_parallel_nodes": 1,
        "max_total_workers": 2,
        "ai_idle_timeout_seconds": 10,
        "ai_wall_timeout_seconds": 20,
        "provider_request_timeout_seconds": 8,
        "combined_retries": 3,
        "subprocess_timeout_seconds": 7,
        "process_tree_rss_bytes": 64 * 1024 * 1024,
        "process_tree_cpu_seconds": 9,
        "max_descendants": 2,
        "cooperative_shutdown_seconds": 1,
        "term_grace_seconds": 2,
        "kill_reap_grace_seconds": 1,
    }
    assert context.resource_limits.max_rss_bytes == 64 * 1024 * 1024
    assert context.resource_limits.max_cpu_seconds == 9
    assert context.resource_limits.max_descendants == 2
    assert context.termination_policy.cooperative_grace_seconds == 1
    assert context.termination_policy.term_grace_seconds == 2
    assert context.termination_policy.kill_grace_seconds == 1


@pytest.mark.parametrize(
    ("node_type", "node", "subprocess_ceiling", "expected_wall"),
    [
        ("bash", {"id": "shell", "bash": "true", "timeout": 1_500.5}, 30.0, 1.5005),
        ("bash", {"id": "shell", "bash": "true"}, 60.0, 60.0),
        ("bash", {"id": "shell", "bash": "true"}, 120.0, 120.0),
        ("bash", {"id": "shell", "bash": "true"}, 240.0, 120.0),
        (
            "script",
            {
                "id": "shell",
                "script": "print('ok')",
                "runtime": "uv",
                "timeout": 1_500.5,
            },
            30.0,
            1.5005,
        ),
        (
            "script",
            {"id": "shell", "script": "print('ok')", "runtime": "uv"},
            60.0,
            60.0,
        ),
        (
            "script",
            {"id": "shell", "script": "print('ok')", "runtime": "uv"},
            120.0,
            120.0,
        ),
        (
            "script",
            {"id": "shell", "script": "print('ok')", "runtime": "uv"},
            240.0,
            120.0,
        ),
    ],
)
def test_archon_claim_uses_sealed_per_attempt_subprocess_wall(
    tmp_path,
    workflow_writer,
    monkeypatch,
    node_type,
    node,
    subprocess_ceiling,
    expected_wall,
):
    workflow = workflow_writer(tmp_path / "package", nodes=[node])
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        yaml.safe_dump({"language_compatibility": "archon-2026-07"}),
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(
        package,
        execution_limits=RunExecutionLimits(
            subprocess_timeout_seconds=subprocess_ceiling,
        ),
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=f"sealed-{subprocess_ceiling}-{expected_wall}",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    observed = []

    class CaptureBoundary:
        def execute(self, context):
            observed.append(context)
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(store, subprocess_timeout_seconds=1.0)
    scheduler.executors[node_type] = CaptureBoundary()
    monkeypatch.setattr(
        scheduler,
        "_run_execution_limits",
        lambda _package: (_ for _ in ()).throw(
            AssertionError("resumed v3 consulted current timeout config")
        ),
    )
    try:
        result = scheduler.advance(
            admitted.run_id,
            max_nodes=1 if "timeout" in node else None,
        )
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert result["status"] == "succeeded"
    context = observed[0]
    assert context.timeout_seconds == pytest.approx(expected_wall)
    assert context.deadline_budget is not None
    assert context.deadline_budget.remaining_wall(
        context.deadline_budget.last_semantic_progress
    ) == pytest.approx(expected_wall)


def test_archon_claim_uses_sealed_ai_wall_idle_and_provider_intersection(
    tmp_path, workflow_writer
):
    workflow = workflow_writer(
        tmp_path / "package",
        nodes=[{"id": "agent", "prompt": "work", "idle_timeout": 1_250.5}],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        yaml.safe_dump({"language_compatibility": "archon-2026-07"}),
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(
        package,
        execution_limits=RunExecutionLimits(
            ai_idle_timeout_seconds=3.0,
            ai_wall_timeout_seconds=4.0,
            provider_request_timeout_seconds=2.0,
        ),
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="sealed-ai-timeouts",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    observed = []

    class CaptureBoundary:
        def execute(self, context):
            observed.append(context)
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(
        store,
        ai_idle_timeout_seconds=100.0,
        ai_wall_timeout_seconds=100.0,
        provider_request_timeout_seconds=100.0,
    )
    scheduler.executors["prompt"] = CaptureBoundary()
    try:
        result = scheduler.advance(admitted.run_id)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert result["status"] == "succeeded"
    context = observed[0]
    assert context.timeout_seconds == 4.0
    assert context.deadline_budget is not None
    assert context.deadline_budget.idle_seconds == pytest.approx(1.2505)
    assert context.deadline_budget.provider_seconds == 2.0


def test_archon_workflow_retry_gets_a_fresh_sealed_attempt_budget_after_backoff(
    tmp_path, workflow_writer, monkeypatch
):
    workflow = workflow_writer(
        tmp_path / "package",
        nodes=[{
            "id": "shell",
            "bash": "true",
            "timeout": 1_500.5,
            "retry": {"max_attempts": 1},
        }],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        yaml.safe_dump({"language_compatibility": "archon-2026-07"}),
        encoding="utf-8",
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(
        package,
        execution_limits=RunExecutionLimits(subprocess_timeout_seconds=30.0),
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="retry-fresh-attempt-budget",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    wall_clock = {"now": datetime(2030, 1, 1, tzinfo=timezone.utc)}
    first_origin = time.monotonic()
    monotonic_clock = {"now": first_origin}
    observed = []

    class RetryOnce:
        def execute(self, context):
            observed.append(context.deadline_budget)
            if len(observed) == 1:
                return NodeExecutionResult(
                    "failed", error_code="timeout", error_message="retry me"
                )
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(
        store,
        utcnow=lambda: wall_clock["now"],
        monotonic=lambda: monotonic_clock["now"],
    )
    scheduler.executors["bash"] = RetryOnce()
    monkeypatch.setattr(scheduler, "_renew_execution_owner", lambda _run_id: True)
    try:
        first_result = scheduler.advance(admitted.run_id, max_nodes=1)
        assert first_result["nodes"]["shell"]["state"] == "waiting_retry"
        retry_at = datetime.fromisoformat(
            first_result["nodes"]["shell"]["next_attempt_at"].replace(
                "Z", "+00:00"
            )
        )
        wall_clock["now"] = retry_at - timedelta(milliseconds=1)
        monotonic_clock["now"] = first_origin + 1.75
        assert store.wake_due_retries(
            admitted.run_id, now=wall_clock["now"]
        ) == ()
        assert len(observed) == 1
        wall_clock["now"] = retry_at + timedelta(milliseconds=1)
        monotonic_clock["now"] = first_origin + 2.0
        assert store.wake_due_retries(
            admitted.run_id, now=wall_clock["now"]
        ) == ("shell",)
        if store.load_run(admitted.run_id)["status"] == "queued":
            assert store.try_promote_run(
                admitted.run_id, now=wall_clock["now"]
            )
        final = scheduler.advance(admitted.run_id, max_nodes=1)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert len(observed) == 2
    assert final["status"] == "succeeded"
    assert observed[0].wall_deadline == pytest.approx(first_origin + 1.5005)
    assert observed[1].wall_deadline == pytest.approx(first_origin + 3.5005)
    assert observed[0].remaining_wall(first_origin) == pytest.approx(1.5005)
    assert observed[1].remaining_wall(first_origin + 2.0) == pytest.approx(1.5005)


@pytest.mark.parametrize("entrypoint", ("advance", "advance_all"))
def test_archon_attempt_wall_starts_at_successful_claim_before_dispatch(
    tmp_path, workflow_writer, monkeypatch, entrypoint
):
    workflow = workflow_writer(
        tmp_path / "package",
        nodes=[{"id": "shell", "bash": "true", "timeout": 1_000}],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
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
            idempotency_key=f"claim-origin-{entrypoint}",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    claim_origin = time.monotonic()
    clock = {"now": claim_origin}
    observed_budgets = []

    scheduler = RunScheduler(store, monotonic=lambda: clock["now"])
    monkeypatch.setattr(scheduler, "_renew_execution_owner", lambda _run_id: True)
    monkeypatch.setattr(
        scheduler,
        "_execute_claim",
        lambda *_args: observed_budgets.append(_args[-1]),
    )
    original_claim = store.claim_node

    def claim_then_delay(*args, **kwargs):
        claim = original_claim(*args, **kwargs)
        if claim is not None:
            clock["now"] = claim_origin + 1.0
        return claim

    monkeypatch.setattr(store, "claim_node", claim_then_delay)
    try:
        if entrypoint == "advance":
            scheduler.advance(admitted.run_id, max_nodes=1)
        else:
            scheduler.advance_all([admitted.run_id])
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert len(observed_budgets) == 1
    assert observed_budgets[0].wall_deadline == claim_origin + 1.0
    assert observed_budgets[0].wall_expired(claim_origin + 1.0)


def test_advance_all_releases_prior_zero_effect_claim_after_fence_loss(
    tmp_path, workflow_writer, monkeypatch
):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            nodes=[
                {"id": "first", "bash": "true"},
                {"id": "second", "bash": "true"},
            ],
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
            idempotency_key="batch-fence-cleanup",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    scheduler = RunScheduler(store, max_parallel_nodes=2)
    original_claim = store.claim_node
    claim_calls = 0
    executor_calls = []

    def lose_second_claim(*args, **kwargs):
        nonlocal claim_calls
        claim_calls += 1
        if claim_calls == 2:
            raise RuntimeError("execution fence changed")
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(store, "claim_node", lose_second_claim)
    monkeypatch.setattr(scheduler, "_is_execution_fence_loss", lambda _exc: True)
    monkeypatch.setattr(
        scheduler,
        "_execute_claim",
        lambda *_args, **_kwargs: executor_calls.append(True),
    )
    try:
        result = scheduler.advance_all([admitted.run_id])[admitted.run_id]
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert result["nodes"]["first"]["state"] == "ready"
    assert result["nodes"]["second"]["state"] == "ready"
    assert executor_calls == []
    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE run_id=?",
            (admitted.run_id,),
        ).fetchone()[0] == 0


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_deadlines_reject_non_positive_or_non_finite_values(value):
    with pytest.raises(ValueError):
        DeadlineBudget.create(
            now=1.0, wall_seconds=value, idle_seconds=1, provider_seconds=1
        )


@pytest.mark.live_system_guard_bypass
def test_deterministic_subprocess_tree_enforces_descendant_limit(
    tmp_path, workflow_writer
):
    code = "import subprocess,time;subprocess.Popen(['sleep','5']);time.sleep(5)"
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            nodes=[
                {
                    "id": "bounded",
                    "bash": f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}",
                }
            ],
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
            idempotency_key="resource-limit",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )

    result = RunScheduler(
        store,
        resource_limits=ProcessResourceLimits(max_descendants=0),
    ).advance(admitted.run_id)

    assert result["status"] == "failed"
    assert result["last_error"]["code"] == "resource_limit"
