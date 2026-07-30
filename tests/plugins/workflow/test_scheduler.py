from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import multiprocessing
import threading

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.output_resolution import ResolvedNodeOutput
from plugins.workflow.scheduler import RunScheduler, evaluate_condition
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from plugins.workflow import locks


def _claim_process(home, run_id, owner, start, output):
    store = RunStore(home)
    start.wait()
    output.put(store.claim_node(run_id, "start", owner) is not None)


def test_only_one_owner_claims_a_ready_node(tmp_path, workflow_writer):
    for repetition in range(20):
        store = RunStore(tmp_path / f"home-{repetition}")
        package = load_workflow(workflow_writer(tmp_path / f"package-{repetition}"))
        prepared = store.prepare_run_snapshot(package)
        result = store.start_run(
            RunAdmissionRequest(
                workflow_name="example",
                definition_digest=prepared.definition_digest,
                policy_digest=prepared.policy_digest,
                input_manifest_digest=prepared.input_manifest_digest,
                trigger_source="cli",
                idempotency_key="claim",
                concurrency_key="example",
            ),
            immutable_snapshot=prepared,
        )

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(
                pool.map(
                    lambda owner: store.claim_node(result.run_id, "start", owner),
                    ("owner-a", "owner-b"),
                )
            )

        assert sum(claim is not None for claim in claims) == 1


def test_only_one_process_claims_a_ready_node(tmp_path, workflow_writer):
    store = RunStore(tmp_path / "home")
    package = load_workflow(workflow_writer(tmp_path / "package"))
    prepared = store.prepare_run_snapshot(package)
    result = store.start_run(
        RunAdmissionRequest(
            workflow_name="example",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="process-claim",
            concurrency_key="example",
        ),
        immutable_snapshot=prepared,
    )
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    output = context.Queue()
    processes = [
        context.Process(
            target=_claim_process,
            args=(str(tmp_path / "home"), result.run_id, owner, start, output),
        )
        for owner in ("process-a", "process-b")
    ]
    for process in processes:
        process.start()
    start.set()
    outcomes = [output.get(timeout=5) for _ in processes]
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0

    assert outcomes.count(True) == 1


def test_windows_lock_backend_uses_one_byte_region(tmp_path, monkeypatch):
    calls = []

    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(fd, mode, size):
            calls.append((fd, mode, size))

    monkeypatch.setattr(locks, "fcntl", None)
    monkeypatch.setattr(locks, "msvcrt", FakeMsvcrt)

    with locks.workflow_lock(tmp_path / "state.lock"):
        pass

    assert [call[1:] for call in calls] == [(1, 1), (2, 1)]


def test_workflow_lock_timeout_is_bounded_and_does_not_steal_owner(tmp_path):
    lock_path = tmp_path / "bounded.lock"
    acquired = threading.Event()
    release = threading.Event()

    def owner():
        with locks.workflow_lock(lock_path):
            acquired.set()
            assert release.wait(timeout=2)

    thread = threading.Thread(target=owner)
    thread.start()
    assert acquired.wait(timeout=1)
    try:
        with pytest.raises(locks.WorkflowLockTimeout):
            with locks.workflow_lock(lock_path, timeout_seconds=0.05):
                pytest.fail("contender stole the workflow lock")
    finally:
        release.set()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_archon_consumers_select_winning_canonical_output_once(tmp_path):
    canonical = b'{"summary":{"count":3},"ok":true}'
    provider_text = b'{ "ok": false, "items": [{"count": 999}] }\n'
    loser_text = b'{"items":[{"count":1}],"ok":false}'
    paths = {
        "winner": tmp_path / "nodes" / "winner" / "output.json",
        "provider": tmp_path / "artifacts" / "winner" / "stdout.txt",
        "loser": tmp_path / "nodes" / "loser" / "output.json",
    }
    for name, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            canonical
            if name == "winner"
            else provider_text
            if name == "provider"
            else loser_text
        )

    def descriptor(name, *, attempt_id, media_type):
        data = paths[name].read_bytes()
        return {
            "node_id": "collect",
            "attempt_id": attempt_id,
            "relative_path": paths[name].relative_to(tmp_path).as_posix(),
            "media_type": media_type,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "publication_id": "publication-1" if name == "winner" else None,
        }

    projection = {
        "run_id": "run-1",
        "language": {
            "effective_profile": "archon-2026-07",
            "normalizer_version": 2,
            "normalized_definition_digest": "1" * 64,
            "semantic_fingerprint": "2" * 64,
            "structured_outputs": {},
        },
        "nodes": {
            "collect": {
                "attempts": [
                    {"attempt_id": "attempt-loser", "state": "failed"},
                    {"attempt_id": "attempt-winner", "state": "succeeded"},
                ]
            }
        },
        "artifacts": [
            descriptor(
                "loser", attempt_id="attempt-loser", media_type="application/json"
            ),
            descriptor(
                "winner", attempt_id="attempt-winner", media_type="application/json"
            ),
            descriptor(
                "provider", attempt_id="attempt-winner", media_type="text/plain"
            ),
        ],
    }
    scheduler = RunScheduler.__new__(RunScheduler)

    outputs = scheduler._output_values(projection, tmp_path)
    resolved = outputs["collect"]
    variables = scheduler._variables(projection, tmp_path)

    assert isinstance(resolved, ResolvedNodeOutput)
    assert resolved.canonical_bytes == canonical
    assert resolved.value["summary"]["count"] == 3
    assert resolved.text == canonical.decode("utf-8")
    assert resolved.media_type == "application/json"
    assert resolved.sha256 == hashlib.sha256(canonical).hexdigest()
    assert resolved.node_id == "collect"
    assert resolved.attempt_id == "attempt-winner"
    assert resolved.publication_id == "publication-1"
    assert evaluate_condition("$collect.output.summary.count == 3", outputs)
    assert variables.render_prompt("$collect.output") == resolved.text
    assert variables.render_prompt("$collect.output.summary.count") == "3"
    assert variables.node_outputs["collect"] == resolved
    evidence = scheduler._predecessor_results(projection, ("collect",), outputs)
    assert evidence["collect"]["output_evidence"] == {
        "media_type": resolved.media_type,
        "size_bytes": len(resolved.canonical_bytes),
        "sha256": resolved.sha256,
        "node_id": resolved.node_id,
        "attempt_id": resolved.attempt_id,
        "publication_id": resolved.publication_id,
    }


def test_legacy_output_scanning_and_parsing_order_remains_unchanged(tmp_path):
    first = tmp_path / "first" / "output.json"
    last = tmp_path / "last" / "stdout.txt"
    first.parent.mkdir()
    last.parent.mkdir()
    first.write_text('{"value": 1}', encoding="utf-8")
    last.write_text("legacy-last", encoding="utf-8")
    projection = {
        "language": {
            "effective_profile": "hermes-legacy",
            "normalizer_version": 1,
            "normalized_definition_digest": "1" * 64,
            "semantic_fingerprint": "2" * 64,
        },
        "artifacts": [
            {"node_id": "collect", "relative_path": "first/output.json"},
            {"node_id": "collect", "relative_path": "last/stdout.txt"},
            {"node_id": "ignored", "relative_path": "last/stderr.txt"},
        ],
    }
    scheduler = RunScheduler.__new__(RunScheduler)

    outputs = scheduler._output_values(projection, tmp_path)

    assert outputs == {"collect": "legacy-last"}
    assert json.loads(first.read_text()) == {"value": 1}
