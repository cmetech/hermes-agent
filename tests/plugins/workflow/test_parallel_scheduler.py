from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.scheduler import (
    ConditionEvaluationError,
    RunScheduler,
    evaluate_condition,
    evaluate_trigger_rule,
)
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def test_directory_capacity_scan_tolerates_atomic_temp_rename(
    tmp_path, monkeypatch
) -> None:
    store = RunStore(tmp_path / "home")
    transient = store.runs_root / ".run.json.transient"
    transient.write_text("temporary", encoding="utf-8")
    original_stat = type(transient).stat

    def racing_stat(path, *args, **kwargs):
        if path == transient:
            transient.unlink(missing_ok=True)
            raise FileNotFoundError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(type(transient), "stat", racing_stat)

    assert store._directory_bytes(store.runs_root) == 0


def _start(store, package, key="parallel"):
    prepared = store.prepare_run_snapshot(package)
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )


@pytest.mark.parametrize(
    ("rule", "states", "expected"),
    [
        ("all_success", ["succeeded", "succeeded"], True),
        ("all_success", ["succeeded", "skipped"], False),
        ("one_success", ["failed", "succeeded"], True),
        ("one_success", ["failed", "cancelled"], False),
        ("none_failed_min_one_success", ["succeeded", "skipped"], True),
        ("none_failed_min_one_success", ["succeeded", "failed"], False),
        ("all_done", ["failed", "cancelled", "skipped"], True),
        ("all_done", ["succeeded", "running"], None),
    ],
)
def test_archon_trigger_rules(rule, states, expected):
    assert evaluate_trigger_rule(rule, states) is expected


def test_conditions_support_json_numeric_strings_and_boolean_precedence():
    outputs = {
        "build": {"score": 12, "kind": "release"},
        "lint": {"warnings": 0},
    }
    assert evaluate_condition(
        "$build.output.score >= 10 || $lint.output.warnings > 0 && "
        "$build.output.kind == 'draft'",
        outputs,
    )
    assert evaluate_condition("$build.output.kind != 'draft'", outputs)
    with pytest.raises(ConditionEvaluationError, match="finite numeric"):
        evaluate_condition(
            "$build.output.score > 1", {"build": {"score": float("inf")}}
        )
    with pytest.raises(ConditionEvaluationError, match="numeric"):
        evaluate_condition("$build.output.kind > 1", outputs)
    with pytest.raises(ConditionEvaluationError, match="finite numeric"):
        evaluate_condition(
            "$build.output.score != 1", {"build": {"score": float("nan")}}
        )


def test_independent_nodes_overlap_with_bounded_parallelism(tmp_path, workflow_writer):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="parallel",
            nodes=[{"id": f"n{i}", "bash": "true"} for i in range(6)],
        )
    )
    store = RunStore(tmp_path / "home", max_total_workers=3)
    admitted = _start(store, package)
    lock = threading.Lock()
    active = 0
    maximum = 0
    starts = defaultdict(int)

    class BlockingExecutor:
        def execute(self, context):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                starts[context.node.id] += 1
            time.sleep(0.06)
            with lock:
                active -= 1
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(store, max_parallel_nodes=2)
    scheduler.executors["bash"] = BlockingExecutor()
    result = scheduler.advance(admitted.run_id)

    assert result["status"] == "succeeded"
    assert maximum == 2
    assert starts == {f"n{i}": 1 for i in range(6)}


def test_ready_layers_are_replenished_fairly_across_active_runs(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home", max_total_workers=2)
    admitted = []
    for name in ("first-run", "second-run"):
        package = load_workflow(
            workflow_writer(
                tmp_path / name,
                name=name,
                nodes=[{"id": "a", "bash": "true"}, {"id": "b", "bash": "true"}],
            )
        )
        admitted.append(_start(store, package))
    lock = threading.Lock()
    first_wave = []
    active = 0
    maximum = 0
    first_wave_barrier = threading.Barrier(2)

    class RecordingExecutor:
        def execute(self, context):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                first_wave.append(context.workflow_name)
            if len(first_wave) <= 2:
                first_wave_barrier.wait(timeout=10)
            with lock:
                active -= 1
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(store, max_parallel_nodes=2)
    scheduler.executors["bash"] = RecordingExecutor()
    results = scheduler.advance_all(result.run_id for result in admitted)

    assert {result["status"] for result in results.values()} == {"succeeded"}
    assert maximum == 2
    assert set(first_wave[:2]) == {"first-run", "second-run"}


def test_mixed_run_limits_share_profile_pool_without_leaking(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home", max_total_workers=5)
    runs = []
    for name, parallel, workers in (("run-a", 1, 2), ("run-b", 4, 4)):
        workflow = workflow_writer(
            tmp_path / name,
            name=name,
            nodes=[{"id": f"n{i}", "bash": "true"} for i in range(8)],
        )
        workflow.with_name("example.hermes.yaml").write_text(
            "limits:\n"
            f"  max_parallel_nodes: {parallel}\n"
            f"  max_total_workers: {workers}\n",
            encoding="utf-8",
        )
        runs.append(_start(store, load_workflow(workflow), key=name).run_id)
    lock = threading.Lock()
    active = defaultdict(int)
    maximum = defaultdict(int)
    maximum_total = 0
    observed_limits = defaultdict(set)
    run_b_starts = 0
    first_run_b_wave = threading.Barrier(4, timeout=10)

    class MixedLimitExecutor:
        def execute(self, context):
            nonlocal maximum_total, run_b_starts
            # Model one lazy pool worker entering after its peers can finish.
            if context.workflow_name == "run-b" and context.node.id == "n3":
                time.sleep(0.2)
            with lock:
                active[context.workflow_name] += 1
                if context.workflow_name == "run-b":
                    run_b_starts += 1
                    wait_for_first_wave = run_b_starts <= 4
                else:
                    wait_for_first_wave = False
                maximum[context.workflow_name] = max(
                    maximum[context.workflow_name], active[context.workflow_name]
                )
                maximum_total = max(maximum_total, sum(active.values()))
                observed_limits[context.workflow_name].add((
                    context.execution_limits.max_parallel_nodes,
                    context.execution_limits.max_total_workers,
                ))
            if wait_for_first_wave:
                first_run_b_wave.wait()
            time.sleep(0.06)
            with lock:
                active[context.workflow_name] -= 1
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(store, max_parallel_nodes=5)
    scheduler.executors["bash"] = MixedLimitExecutor()

    results = scheduler.advance_all(runs)

    assert {result["status"] for result in results.values()} == {"succeeded"}
    assert maximum["run-a"] == 1
    assert maximum["run-b"] == 4
    assert maximum_total <= 5
    assert observed_limits == {
        "run-a": {(1, 2)},
        "run-b": {(4, 4)},
    }


def test_store_composes_per_run_worker_ceiling_with_profile_ceiling(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home", max_total_workers=3)
    runs = []
    for name in ("run-a", "run-b"):
        package = load_workflow(
            workflow_writer(
                tmp_path / name,
                name=name,
                nodes=[{"id": f"n{i}", "bash": "true"} for i in range(3)],
            )
        )
        run_id = _start(store, package, key=name).run_id
        store.transition_pending_nodes(
            run_id,
            {f"n{i}": ("ready", None) for i in range(3)},
        )
        runs.append(run_id)

    first_a = store.claim_node(
        runs[0], "n0", "owner", max_run_workers=2
    )
    second_a = store.claim_node(
        runs[0], "n1", "owner", max_run_workers=2
    )
    blocked_by_run = store.claim_node(
        runs[0], "n2", "owner", max_run_workers=2
    )
    first_b = store.claim_node(
        runs[1], "n0", "owner", max_run_workers=3
    )
    blocked_by_profile = store.claim_node(
        runs[1], "n1", "owner", max_run_workers=3
    )

    assert first_a is not None
    assert second_a is not None
    assert blocked_by_run is None
    assert first_b is not None
    assert blocked_by_profile is None


def test_profile_worker_cap_holds_across_scheduler_instances(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home", max_total_workers=2)
    runs = []
    for name in ("one", "two"):
        package = load_workflow(
            workflow_writer(
                tmp_path / name,
                name=name,
                nodes=[{"id": "a", "bash": "true"}, {"id": "b", "bash": "true"}],
            )
        )
        runs.append(_start(store, package).run_id)
    lock = threading.Lock()
    active = 0
    maximum = 0

    class SlowExecutor:
        def execute(self, _context):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.08)
            with lock:
                active -= 1
            return NodeExecutionResult("succeeded")

    schedulers = [RunScheduler(store), RunScheduler(store)]
    for scheduler in schedulers:
        scheduler.executors["bash"] = SlowExecutor()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda pair: pair[0].advance(pair[1]), zip(schedulers, runs)))
    for scheduler, run_id in zip(schedulers, runs):
        if store.load_run(run_id)["status"] == "running":
            scheduler.advance(run_id)

    assert maximum == 2
    assert {store.load_run(run_id)["status"] for run_id in runs} == {"succeeded"}


def test_runnable_request_does_not_jump_an_older_fifo_waiter(
    tmp_path, workflow_writer
) -> None:
    home = tmp_path / "home"
    setup_store = RunStore(home, max_executing_runs=2)
    lane_package = load_workflow(
        workflow_writer(tmp_path / "lane", name="fifo-lane")
    )
    resume_package = load_workflow(
        workflow_writer(tmp_path / "resume", name="fifo-resume")
    )

    blocker = _start(setup_store, lane_package, key="blocker")
    older = _start(setup_store, lane_package, key="older")
    resumable = _start(setup_store, resume_package, key="resumable")
    assert older.disposition == "queued"
    setup_store.interrupt_for_host_pressure(
        resumable.run_id, message="synthetic fairness setup"
    )
    resume_state = setup_store.load_run(resumable.run_id)
    assert RunScheduler(setup_store).advance(blocker.run_id)["status"] == "succeeded"

    store = RunStore(home, max_executing_runs=1)
    always_run_nodes = RunScheduler(store).verified_always_run_nodes(
        resumable.run_id
    )
    resumed = store.resume_run(
        resumable.run_id,
        always_run_nodes=always_run_nodes,
        expected_state_version=resume_state["state_version"],
    )

    assert resumed["status"] == "queued"
    assert resumed["queue_sequence"] > store.load_run(older.run_id)[
        "queue_sequence"
    ]
    fresh = _start(store, resume_package, key="fresh")
    assert fresh.disposition == "queued"
    assert store.load_run(fresh.run_id)["queue_sequence"] > resumed[
        "queue_sequence"
    ]
    assert store.try_promote_run(older.run_id)


def test_finished_slot_is_replenished_while_slow_peer_is_still_running(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home", max_total_workers=2)
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="replenish",
            nodes=[{"id": node_id, "bash": "true"} for node_id in ("a", "b", "c")],
        )
    )
    admitted = _start(store, package)
    b_entered = threading.Event()
    release_b = threading.Event()
    c_started = threading.Event()

    class StaggeredExecutor:
        def execute(self, context):
            if context.node.id == "b":
                b_entered.set()
                release_b.wait(2)
            elif context.node.id == "a":
                assert b_entered.wait(1)
            else:
                c_started.set()
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(store, max_parallel_nodes=2)
    scheduler.executors["bash"] = StaggeredExecutor()
    thread = threading.Thread(target=scheduler.advance_all, args=([admitted.run_id],))
    thread.start()
    try:
        assert c_started.wait(1), "freed worker slot was not replenished"
    finally:
        release_b.set()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_replenishment_rotates_to_later_runs_before_refilling_early_run(
    tmp_path, workflow_writer
):
    store = RunStore(tmp_path / "home", max_total_workers=2)
    runs = []
    for name in ("first", "second", "third"):
        package = load_workflow(
            workflow_writer(
                tmp_path / name,
                name=name,
                nodes=[{"id": "a", "bash": "true"}, {"id": "b", "bash": "true"}],
            )
        )
        runs.append(_start(store, package).run_id)
    first_two_entered = threading.Barrier(3)
    release_first_two = threading.Event()
    starts = []
    lock = threading.Lock()

    class RotatingExecutor:
        def execute(self, context):
            with lock:
                starts.append(context.workflow_name)
                position = len(starts)
            if position <= 2:
                first_two_entered.wait(timeout=2)
                release_first_two.wait(2)
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(store, max_parallel_nodes=2)
    scheduler.executors["bash"] = RotatingExecutor()
    thread = threading.Thread(target=scheduler.advance_all, args=(runs,))
    thread.start()
    first_two_entered.wait(timeout=2)
    release_first_two.set()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert "third" in starts[2:4]


def test_false_or_invalid_condition_skips_and_unblocks_all_done(
    tmp_path, workflow_writer
):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="conditions",
            nodes=[
                {"id": "source", "bash": "printf '{\"score\": 1}'"},
                {
                    "id": "conditional",
                    "bash": "false",
                    "depends_on": ["source"],
                    "when": "$source.output.score > 5",
                },
                {
                    "id": "cleanup",
                    "bash": "true",
                    "depends_on": ["conditional"],
                    "trigger_rule": "all_done",
                },
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)

    result = RunScheduler(store).advance(admitted.run_id)

    assert result["status"] == "succeeded"
    assert result["nodes"]["conditional"]["state"] == "skipped"
    assert result["nodes"]["cleanup"]["state"] == "succeeded"
    assert any(
        event["event_type"] == "node_skipped"
        for event in store.tail_events(admitted.run_id)
    )
