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

    class RecordingExecutor:
        def execute(self, context):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                first_wave.append(context.workflow_name)
            time.sleep(0.05)
            with lock:
                active -= 1
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(store, max_parallel_nodes=2)
    scheduler.executors["bash"] = RecordingExecutor()
    results = scheduler.advance_all(result.run_id for result in admitted)

    assert {result["status"] for result in results.values()} == {"succeeded"}
    assert maximum == 2
    assert set(first_wave[:2]) == {"first-run", "second-run"}


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
