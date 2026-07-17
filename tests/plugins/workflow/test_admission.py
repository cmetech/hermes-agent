from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def _prepared(store: RunStore, workflow_writer, tmp_path, *, name="demo"):
    path = workflow_writer(
        tmp_path / f"package-{name}",
        name=name,
        nodes=[{"id": "first", "bash": "printf first"}],
    )
    package = load_workflow(path)
    return store.prepare_run_snapshot(package)


def _request(snapshot, *, key="delivery-1", policy="queue", name="demo"):
    return RunAdmissionRequest(
        workflow_name=name,
        definition_digest=snapshot.definition_digest,
        policy_digest=snapshot.policy_digest,
        input_manifest_digest=snapshot.input_manifest_digest,
        trigger_source="cli",
        idempotency_key=key,
        concurrency_key=name,
        concurrency_policy=policy,
    )


def test_duplicate_start_is_atomic_and_returns_one_run(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    prepared = _prepared(store, workflow_writer, tmp_path)
    request = _request(prepared)

    def start(_):
        snapshot = store.clone_prepared_snapshot(prepared)
        return store.start_run(request, immutable_snapshot=snapshot)

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(start, range(100)))

    assert {result.run_id for result in results} == {results[0].run_id}
    assert sum(result.disposition == "created" for result in results) == 1
    assert sum(result.disposition == "existing" for result in results) == 99
    assert len(store.list_runs()) == 1


def test_reused_key_with_changed_digest_conflicts(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    prepared = _prepared(store, workflow_writer, tmp_path)
    first = store.start_run(_request(prepared), immutable_snapshot=prepared)
    changed = store.prepare_empty_snapshot(
        definition_digest="f" * 64,
        policy_digest="0" * 64,
        input_manifest_digest="1" * 64,
    )
    second = store.start_run(_request(changed), immutable_snapshot=changed)

    assert first.disposition == "created"
    assert second.disposition == "rejected"
    assert second.reason_code == "idempotency_conflict"
    assert len(store.list_runs()) == 1


def test_overlap_policies_queue_forbid_and_allow(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    prepared = _prepared(store, workflow_writer, tmp_path)
    snapshots = [store.clone_prepared_snapshot(prepared) for _ in range(4)]
    first = store.start_run(
        _request(prepared, key="one"), immutable_snapshot=snapshots[0]
    )
    queued = store.start_run(
        _request(prepared, key="two"),
        immutable_snapshot=snapshots[1],
    )
    forbidden = store.start_run(
        _request(prepared, key="three", policy="forbid"),
        immutable_snapshot=snapshots[2],
    )
    allowed = store.start_run(
        _request(prepared, key="four", policy="allow"),
        immutable_snapshot=snapshots[3],
    )

    assert first.disposition == "created"
    assert queued.disposition == "queued"
    assert queued.blocked_by_run_id == first.run_id
    assert forbidden == forbidden.__class__(None, "rejected", "overlap_forbidden")
    assert allowed.disposition == "created"
