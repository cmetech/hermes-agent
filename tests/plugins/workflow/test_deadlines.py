from __future__ import annotations

import shlex
import sys

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.models import DeadlineBudget, WorkflowRuntimeConfig
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from tools.managed_process import ProcessResourceLimits


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
