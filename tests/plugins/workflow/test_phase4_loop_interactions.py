from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
from pathlib import Path
import threading

import pytest

from plugins.workflow.actions import available_actions
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.machine_contract import WorkflowConflict
from plugins.workflow.models import LoopSignalConfirmation
from plugins.workflow.store import ArtifactRef, RunStore
from plugins.workflow.trust import WorkflowPackageDigest


def _compile_version(path: Path, version: int):
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
    from plugins.workflow.schema import parse_workflow_source_bytes

    sidecar = b"language_compatibility: archon-2026-07\n"
    path.with_name(f"{path.stem}.hermes.yaml").write_bytes(sidecar)
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar,
        source="project",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=version,
    )


def _compile_v4(path: Path):
    return _compile_version(path, 4)


def _pause_signal(
    tmp_path: Path,
    workflow_writer,
    *,
    key: str,
    iteration: int = 2,
    max_iterations: int = 3,
    result: bytes = b"cleaned result\n",
    pending_mutation=None,
):
    workflow = workflow_writer(
        tmp_path / key / "workflows",
        name=f"signal-{key}",
        filename=f"signal-{key}.yaml",
        interactive=True,
        nodes=[
            {
                "id": "refine",
                "loop": {
                    "prompt": "Refine",
                    "until": "DONE",
                    "max_iterations": max_iterations,
                    "interactive": True,
                    "gate_message": "Accept this result or provide feedback",
                },
            }
        ],
    )
    compilation = _compile_v4(workflow)
    store = RunStore(tmp_path / "home", max_executing_runs=20)
    prepared = store.prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        ),
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=compilation.package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=compilation.package.definition.name,
            concurrency_policy="allow",
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    claim = store.claim_node(admitted.run_id, "refine", f"worker-{key}")
    assert claim is not None
    store.mark_node_started(claim)
    relative = (
        Path("nodes")
        / "refine"
        / claim.attempt_id
        / f"iteration-{iteration:04d}"
        / "output.txt"
    )
    result_path = store.run_directory(admitted.run_id) / relative
    result_path.parent.mkdir(parents=True)
    result_path.write_bytes(result)
    result_sha256 = hashlib.sha256(result).hexdigest()
    confirmation = LoopSignalConfirmation.create(
        run_id=admitted.run_id,
        node_id="refine",
        message="Accept this result or provide feedback",
        iteration=iteration,
        max_iterations=max_iterations,
        result_artifact=relative.as_posix(),
        result_sha256=result_sha256,
    ).to_dict()
    if pending_mutation is not None:
        pending_mutation(confirmation)
    store.complete_node(
        claim,
        status="paused",
        artifacts=(
            ArtifactRef(
                relative_path=relative.as_posix(),
                media_type="text/plain",
                size_bytes=len(result),
                sha256=result_sha256,
            ),
        ),
        metadata={
            "pending_interaction": confirmation,
            "loop_state": {"iteration": iteration},
        },
    )
    return store, admitted.run_id, confirmation, result_path


def _pending() -> dict[str, object]:
    return {
        "type": "loop_signal_confirmation",
        "interaction_id": "a" * 64,
        "message": "Accept this result or provide feedback",
        "iteration": 2,
        "max_iterations": 5,
        "result_artifact": "nodes/refine/attempt-2/output.txt",
        "result_sha256": "b" * 64,
    }


def test_signal_confirmation_advertises_only_backend_authorized_actions() -> None:
    pending = _pending()

    assert available_actions("paused", pending) == [
        "status",
        "events",
        "approve",
        "provide-input",
        "cancel",
    ]
    assert available_actions(
        "paused",
        {**pending, "iteration": 5},
    ) == ["status", "events", "approve", "cancel"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"extra": True}),
        lambda value: value.pop("interaction_id"),
        lambda value: value.update({"interaction_id": "A" * 64}),
        lambda value: value.update({"message": ""}),
        lambda value: value.update({"message": "x" * 16_385}),
        lambda value: value.update({"iteration": True}),
        lambda value: value.update({"iteration": 0}),
        lambda value: value.update({"iteration": 6}),
        lambda value: value.update({"max_iterations": 101}),
        lambda value: value.update({"result_artifact": "/tmp/result.txt"}),
        lambda value: value.update({"result_artifact": "nodes/../result.txt"}),
        lambda value: value.update({"result_artifact": "nodes\\result.txt"}),
        lambda value: value.update({"result_sha256": "b" * 63}),
    ],
)
def test_malformed_signal_confirmation_never_advertises_a_mutation(mutate) -> None:
    pending = deepcopy(_pending())
    mutate(pending)

    assert available_actions("paused", pending) == ["status", "events", "cancel"]


def test_signal_identity_ignores_artifact_path_but_binds_logical_result() -> None:
    base = {
        "run_id": "run-1",
        "node_id": "refine",
        "iteration": 2,
        "result_sha256": "b" * 64,
        "message": "Accept this result",
    }

    first = LoopSignalConfirmation.create(
        **base,
        max_iterations=5,
        result_artifact="nodes/refine/attempt-a/output.txt",
    )
    moved = LoopSignalConfirmation.create(
        **base,
        max_iterations=9,
        result_artifact="nodes/refine/attempt-b/output.txt",
    )

    assert first.interaction_id == moved.interaction_id
    for changed in (
        {**base, "run_id": "run-2"},
        {**base, "node_id": "other"},
        {**base, "iteration": 3},
        {**base, "result_sha256": "c" * 64},
        {**base, "message": "Different gate"},
    ):
        assert LoopSignalConfirmation.create(
            **changed,
            max_iterations=5,
            result_artifact="nodes/refine/attempt-a/output.txt",
        ).interaction_id != first.interaction_id


def test_store_rejects_an_unbound_signal_identity_before_publishing_pause(
    tmp_path: Path,
    workflow_writer,
) -> None:
    with pytest.raises(ValueError, match="identity"):
        _pause_signal(
            tmp_path,
            workflow_writer,
            key="bad-identity",
            pending_mutation=lambda pending: pending.update(
                {"interaction_id": "f" * 64}
            ),
        )


def test_signal_approval_authenticates_result_and_never_parses_definition(
    tmp_path: Path,
    workflow_writer,
    monkeypatch,
) -> None:
    store, run_id, pending, _result_path = _pause_signal(
        tmp_path,
        workflow_writer,
        key="approve",
    )
    paused = store.load_run(run_id)

    def forbidden_definition_load(*_args, **_kwargs):
        raise AssertionError("signal acceptance must not parse a definition")

    monkeypatch.setattr(
        "plugins.workflow.schema.load_workflow",
        forbidden_definition_load,
    )
    decision = store.approve_run(
        run_id,
        comment="accepted result, not feedback",
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
    )

    current = store.load_run(run_id)
    node = current["nodes"]["refine"]
    assert decision.outcome == "applied"
    assert node["state"] == "succeeded"
    assert node["attempts"][-1]["state"] == "succeeded"
    assert "pending_interaction" not in node
    assert "loop_user_input_artifact" not in node
    events = store.tail_events(run_id, limit=20)
    accepted_events = [
        event for event in events if event["event_type"] == "loop_signal_accepted"
    ]
    assert len(accepted_events) == 1
    assert accepted_events[0]["payload"]["comment"] == (
        "accepted result, not feedback"
    )


def test_signal_approval_rejects_result_tampering_without_mutation(
    tmp_path: Path,
    workflow_writer,
) -> None:
    store, run_id, pending, result_path = _pause_signal(
        tmp_path,
        workflow_writer,
        key="tamper",
    )
    paused = store.load_run(run_id)
    result_path.write_bytes(b"tampered\n")

    with pytest.raises(ValueError, match="result"):
        store.approve_run(
            run_id,
            expected_state_version=paused["state_version"],
            interaction_id=pending["interaction_id"],
        )

    assert store.load_run(run_id)["state_version"] == paused["state_version"]


def test_signal_feedback_is_compare_and_set_and_consumed_by_next_iteration(
    tmp_path: Path,
    workflow_writer,
) -> None:
    store, run_id, pending, _result_path = _pause_signal(
        tmp_path,
        workflow_writer,
        key="feedback",
    )
    paused = store.load_run(run_id)

    with pytest.raises(WorkflowConflict):
        store.provide_loop_input(
            run_id,
            "stale",
            expected_state_version=paused["state_version"] - 1,
            interaction_id=pending["interaction_id"],
        )
    unchanged = store.load_run(run_id)
    assert unchanged["state_version"] == paused["state_version"]

    continued = store.provide_loop_input(
        run_id,
        "tighten the evidence",
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
    )

    node = continued["nodes"]["refine"]
    assert node["state"] == "ready"
    assert "pending_interaction" not in node
    input_path = store.run_directory(run_id) / node["loop_user_input_artifact"]
    assert input_path.read_text(encoding="utf-8") == "tighten the evidence"
    events = store.tail_events(run_id, limit=20)
    assert events[-2]["event_type"] == "loop_feedback_provided"
    assert "tighten the evidence" not in str(events)


def test_final_signal_feedback_and_empty_feedback_mutate_nothing(
    tmp_path: Path,
    workflow_writer,
) -> None:
    store, run_id, pending, _result_path = _pause_signal(
        tmp_path,
        workflow_writer,
        key="final",
        iteration=3,
        max_iterations=3,
    )
    paused = store.load_run(run_id)

    for feedback in ("more work", ""):
        with pytest.raises(ValueError):
            store.provide_loop_input(
                run_id,
                feedback,
                expected_state_version=paused["state_version"],
                interaction_id=pending["interaction_id"],
            )
        assert store.load_run(run_id)["state_version"] == paused["state_version"]


def test_wrong_and_cross_run_signal_ids_mutate_nothing(
    tmp_path: Path,
    workflow_writer,
) -> None:
    store, first_id, first_pending, _ = _pause_signal(
        tmp_path,
        workflow_writer,
        key="first",
    )
    store, second_id, second_pending, _ = _pause_signal(
        tmp_path,
        workflow_writer,
        key="second",
    )
    first = store.load_run(first_id)

    for interaction_id in ("0" * 64, second_pending["interaction_id"]):
        with pytest.raises(ValueError):
            store.approve_run(
                first_id,
                expected_state_version=first["state_version"],
                interaction_id=interaction_id,
            )
        current = store.load_run(first_id)
        assert current["state_version"] == first["state_version"]
        assert current["nodes"]["refine"]["pending_interaction"] == first_pending

    assert second_id != first_id


def test_signal_approval_requires_version_and_duplicate_is_idempotent(
    tmp_path: Path,
    workflow_writer,
) -> None:
    store, run_id, pending, _ = _pause_signal(
        tmp_path,
        workflow_writer,
        key="duplicate",
    )
    paused = store.load_run(run_id)

    with pytest.raises(ValueError, match="expected state version"):
        store.approve_run(
            run_id,
            interaction_id=pending["interaction_id"],
        )
    assert store.load_run(run_id)["state_version"] == paused["state_version"]

    applied = store.approve_run(
        run_id,
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
    )
    decided = store.load_run(run_id)
    duplicate = store.approve_run(
        run_id,
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
    )

    assert applied.outcome == "applied"
    assert duplicate.outcome == "already_decided"
    assert duplicate.decision == "approved"
    assert store.load_run(run_id)["state_version"] == decided["state_version"]


def test_approve_feedback_race_commits_exactly_one_signal_decision(
    tmp_path: Path,
    workflow_writer,
) -> None:
    store, run_id, pending, _ = _pause_signal(
        tmp_path,
        workflow_writer,
        key="decision-race",
    )
    paused = store.load_run(run_id)
    barrier = threading.Barrier(3)

    def approve() -> tuple[str, str]:
        barrier.wait()
        decision = store.approve_run(
            run_id,
            expected_state_version=paused["state_version"],
            interaction_id=pending["interaction_id"],
        )
        return "approve", decision.outcome

    def feedback() -> tuple[str, str]:
        barrier.wait()
        try:
            store.provide_loop_input(
                run_id,
                "continue once",
                expected_state_version=paused["state_version"],
                interaction_id=pending["interaction_id"],
            )
        except WorkflowConflict:
            return "feedback", "conflict"
        return "feedback", "applied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve), pool.submit(feedback)]
        barrier.wait()
        outcomes = [future.result(timeout=10) for future in futures]

    assert [outcome for _action, outcome in outcomes].count("applied") == 1
    assert sorted(outcome for _action, outcome in outcomes) in (
        ["already_decided", "applied"],
        ["applied", "conflict"],
    )
    current = store.load_run(run_id)
    assert current["nodes"]["refine"]["state"] in {"ready", "succeeded"}
    event_types = [
        event["event_type"] for event in store.tail_events(run_id, limit=30)
    ]
    assert sum(
        event_type in {"loop_signal_accepted", "loop_feedback_provided"}
        for event_type in event_types
    ) == 1


def test_signal_projection_and_recovery_retain_exact_backend_state(
    tmp_path: Path,
    workflow_writer,
) -> None:
    store, run_id, pending, _ = _pause_signal(
        tmp_path,
        workflow_writer,
        key="recovery",
    )

    detail = store.get_run_status(run_id)
    assert detail["pending_interaction"] == {**pending, "node_id": "refine"}
    assert detail["next_actions"] == [
        "status",
        "events",
        "approve",
        "provide-input",
        "cancel",
    ]
    store.run_directory(run_id).joinpath("run.json").unlink()

    recovered = RunStore(tmp_path / "home").get_run_status(run_id)

    assert recovered["pending_interaction"] == detail["pending_interaction"]
    assert recovered["next_actions"] == detail["next_actions"]


@pytest.mark.parametrize("normalizer_version", [1, 2, 3])
def test_v1_through_v3_loop_input_keeps_historical_empty_text_behavior(
    tmp_path: Path,
    workflow_writer,
    normalizer_version: int,
) -> None:
    workflow = workflow_writer(
        tmp_path / f"legacy-{normalizer_version}" / "workflows",
        name=f"legacy-loop-{normalizer_version}",
        nodes=[
            {
                "id": "refine",
                "loop": {
                    "prompt": "Refine",
                    "until": "DONE",
                    "max_iterations": 2,
                },
            }
        ],
    )
    package = _compile_version(workflow, normalizer_version).package
    store = RunStore(tmp_path / f"legacy-home-{normalizer_version}")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=f"legacy-input-{normalizer_version}",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    claim = store.claim_node(admitted.run_id, "refine", "legacy-worker")
    assert claim is not None
    store.mark_node_started(claim)
    interaction_id = f"legacy-loop-input-{normalizer_version}"
    store.complete_node(
        claim,
        status="paused",
        metadata={
            "pending_interaction": {
                "type": "loop_input",
                "interaction_id": interaction_id,
                "message": "Review",
            },
            "loop_state": {"iteration": 1},
        },
    )
    paused = store.load_run(admitted.run_id)

    continued = store.provide_loop_input(
        admitted.run_id,
        "",
        expected_state_version=paused["state_version"],
        interaction_id=interaction_id,
    )

    node = continued["nodes"]["refine"]
    assert node["state"] == "ready"
    assert (
        store.run_directory(admitted.run_id)
        .joinpath(node["loop_user_input_artifact"])
        .read_bytes()
        == b""
    )
    assert "loop_input_provided" in {
        event["event_type"]
        for event in store.tail_events(admitted.run_id, limit=20)
    }
