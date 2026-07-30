from __future__ import annotations

import threading
import time

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def _start(store, package, *, key="e2e", values=None):
    prepared = store.prepare_run_snapshot(package, values=values)
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


def test_two_dependent_bash_nodes_execute_and_persist_artifacts(
    tmp_path, workflow_writer
):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="bash-dag",
            nodes=[
                {"id": "first", "bash": "printf first"},
                {
                    "id": "second",
                    "bash": "printf second",
                    "depends_on": ["first"],
                },
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)

    result = RunScheduler(store).advance(admitted.run_id)

    assert result["status"] == "succeeded"
    status = store.get_run_status(admitted.run_id)
    assert status["previous_node"] == "second"
    assert all(
        attempt["started_at"] <= attempt["completed_at"]
        for node in status["nodes"].values()
        for attempt in node["attempts"]
    )
    assert [result["nodes"][node]["state"] for node in ("first", "second")] == [
        "succeeded",
        "succeeded",
    ]
    artifacts = {artifact["node_id"]: artifact for artifact in result["artifacts"]}
    assert (
        store.run_directory(admitted.run_id) / artifacts["first"]["relative_path"]
    ).read_text() == "first"
    assert (
        store.run_directory(admitted.run_id) / artifacts["second"]["relative_path"]
    ).read_text() == "second"
    event_types = [event["event_type"] for event in store.tail_events(admitted.run_id)]
    assert event_types[0] == "run_admitted"
    assert event_types[-1] == "run_succeeded"
    assert event_types.count("node_claimed") == 2
    assert event_types.count("node_started") == 2
    assert event_types.count("process_started") == 2
    assert event_types.count("process_reaped") == 2
    assert event_types.count("node_succeeded") == 2
    assert event_types.index("node_ready") < event_types.index(
        "node_claimed", event_types.index("node_claimed") + 1
    )


def test_archon_bash_declared_output_publishes_real_stdout(
    tmp_path, workflow_writer
) -> None:
    workflow = workflow_writer(
        tmp_path / "package",
        name="bash-publication",
        nodes=[
            {
                "id": "produce",
                "bash": "printf 'bash output'",
                "output_type": "BashReport",
            }
        ],
    )
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(workflow)
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package, key="bash-publication")

    result = RunScheduler(store).advance(admitted.run_id)

    published = [
        artifact
        for artifact in result["artifacts"]
        if artifact.get("publication_id") is not None
    ]
    assert len(published) == 1
    artifact = published[0]
    bundle = (
        store.run_directory(admitted.run_id)
        / "publications"
        / artifact["publication_id"]
    )
    assert artifact["relative_path"].endswith("/stdout.txt")
    assert artifact["media_type"] == "text/markdown; charset=utf-8"
    assert (bundle / "content.md").read_bytes() == b"bash output"


def test_bash_nodes_substitute_arguments_predecessor_output_and_run_id_safely(
    tmp_path, workflow_writer
):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="bash-variables",
            nodes=[
                {"id": "first", "bash": "printf '{\"value\":\"node output\"}'"},
                {
                    "id": "second",
                    "bash": (
                        "printf '<%s>|<%s>|<%s>' "
                        '"$ARGUMENTS" "$first.output.value" "$WORKFLOW_ID"'
                    ),
                    "depends_on": ["first"],
                },
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(
        store,
        package,
        values={"arguments": "$(touch injected) 'quoted'"},
    )

    result = RunScheduler(store).advance(admitted.run_id)

    assert result["status"] == "succeeded"
    artifact = next(
        item
        for item in result["artifacts"]
        if item["node_id"] == "second" and "stdout" in item["relative_path"]
    )
    output = (store.run_directory(admitted.run_id) / artifact["relative_path"]).read_text()
    assert output == (
        "<$(touch injected) 'quoted'>|<node output>|" f"<{admitted.run_id}>"
    )
    assert not (store.run_directory(admitted.run_id) / "injected").exists()


def test_resume_does_not_rerun_completed_node(tmp_path, workflow_writer):
    marker = tmp_path / "marker.txt"
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="resume-dag",
            nodes=[
                {
                    "id": "first",
                    "bash": f"printf x >> {marker}; printf first",
                },
                {"id": "second", "bash": "printf second", "depends_on": ["first"]},
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    scheduler = RunScheduler(store)

    scheduler.advance(admitted.run_id, max_nodes=1)
    assert marker.read_text() == "x"
    scheduler.advance(admitted.run_id)

    assert marker.read_text() == "x"
    assert store.load_run(admitted.run_id)["status"] == "succeeded"


def test_timeout_terminates_the_bash_process_tree(tmp_path, workflow_writer):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="timeout-dag",
            nodes=[
                {
                    "id": "wait",
                    "bash": "sleep 2 &",
                    "timeout": 0.1,
                    "retry": {"max_attempts": 1},
                }
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)

    result = RunScheduler(store).advance(admitted.run_id)

    assert result["status"] == "failed"
    assert result["last_error"]["code"] == "timeout"


def test_output_limit_caps_persisted_stdout(tmp_path, workflow_writer):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="bounded-output",
            nodes=[
                {
                    "id": "noisy",
                    "bash": (
                        "i=0; while [ $i -lt 200000 ]; do "
                        "printf 1234567890; i=$((i+1)); done"
                    ),
                }
            ],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)

    result = RunScheduler(store).advance(admitted.run_id)

    assert result["status"] == "failed"
    assert result["last_error"]["code"] == "output_limit"
    assert (
        sum(artifact["size_bytes"] for artifact in result["artifacts"]) <= 1024 * 1024
    )


def test_cancel_stops_a_running_bash_process(tmp_path, workflow_writer):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="cancel-dag",
            nodes=[{"id": "wait", "bash": "sleep 5 & wait"}],
        )
    )
    store = RunStore(tmp_path / "home")
    admitted = _start(store, package)
    scheduler = RunScheduler(
        store,
        cooperative_shutdown_seconds=0.1,
        term_grace_seconds=0.5,
        kill_reap_grace_seconds=0.5,
    )
    thread = threading.Thread(target=scheduler.advance, args=(admitted.run_id,))
    thread.start()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if store.load_run(admitted.run_id)["nodes"]["wait"]["state"] == "running":
            break
        time.sleep(0.01)

    store.cancel_run(admitted.run_id)
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert store.load_run(admitted.run_id)["status"] == "cancelled"


def test_queued_run_promotes_after_blocking_run_finishes(tmp_path, workflow_writer):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="queued-dag",
            nodes=[{"id": "only", "bash": "printf queued"}],
        )
    )
    store = RunStore(tmp_path / "home")
    first = _start(store, package, key="first")
    second = _start(store, package, key="second")
    assert second.disposition == "queued"

    RunScheduler(store).advance(first.run_id)
    result = RunScheduler(store).advance(second.run_id)

    assert result["status"] == "succeeded"
