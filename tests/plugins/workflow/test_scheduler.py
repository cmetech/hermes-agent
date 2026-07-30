from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
import errno
import hashlib
import json
import multiprocessing
import threading

import pytest

from plugins.workflow import output_resolution
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.models import RunExecutionLimits, WorkflowNode
from plugins.workflow.output_resolution import PrimaryOutputCandidate, ResolvedNodeOutput
from plugins.workflow.scheduler import RunScheduler, evaluate_condition
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import ArtifactRef, NodeClaim, RunStore
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
                "type": "prompt",
                "attempts": [
                    {"attempt_id": "attempt-loser", "state": "failed"},
                    {
                        "attempt_id": "attempt-winner",
                        "state": "succeeded",
                        "metadata": {
                            "primary_output_candidate": {
                                "attempt_relative_path": (
                                    paths["winner"].relative_to(tmp_path).as_posix()
                                ),
                                "media_type": "application/json",
                                "size_bytes": len(canonical),
                                "sha256": hashlib.sha256(canonical).hexdigest(),
                                "schema_fingerprint": "3" * 64,
                                "canonicalization_version": 1,
                                "output_type": None,
                            }
                        },
                    },
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
    paths["winner"].write_bytes(b'{"summary":{"count":999}}')
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
    assert variables.node_outputs["collect"] is resolved
    evidence = scheduler._predecessor_results(projection, ("collect",), outputs)
    assert evidence["collect"]["output_evidence"] == {
        "media_type": resolved.media_type,
        "size_bytes": len(resolved.canonical_bytes),
        "sha256": resolved.sha256,
        "node_id": resolved.node_id,
        "attempt_id": resolved.attempt_id,
        "publication_id": resolved.publication_id,
    }
    replacement = b'{"summary":{"count":4},"ok":true}'
    paths["winner"].write_bytes(replacement)
    replacement_digest = hashlib.sha256(replacement).hexdigest()
    winner_descriptor = projection["artifacts"][1]
    winner_descriptor["size_bytes"] = len(replacement)
    winner_descriptor["sha256"] = replacement_digest
    winner_candidate = projection["nodes"]["collect"]["attempts"][-1]["metadata"][
        "primary_output_candidate"
    ]
    winner_candidate["size_bytes"] = len(replacement)
    winner_candidate["sha256"] = replacement_digest

    replacement_resolved = scheduler._output_values(projection, tmp_path)["collect"]

    assert replacement_resolved is not resolved
    assert replacement_resolved.value["summary"]["count"] == 4
    assert scheduler._output_resolution_cache_bytes <= (
        scheduler._output_resolution_cache_max_bytes
    )


@pytest.mark.parametrize("candidate_state", ("missing", "disagrees"))
def test_archon_ai_requires_correlated_primary_candidate(
    tmp_path, candidate_state
):
    canonical = b'{"ok":true}'
    output = tmp_path / "output.json"
    output.write_bytes(canonical)
    metadata = {}
    if candidate_state == "disagrees":
        metadata["primary_output_candidate"] = {
            "attempt_relative_path": "output.json",
            "media_type": "application/json",
            "size_bytes": len(canonical),
            "sha256": "0" * 64,
            "schema_fingerprint": "3" * 64,
            "canonicalization_version": 1,
            "output_type": None,
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
                "type": "prompt",
                "attempts": [{
                    "attempt_id": "attempt-winner",
                    "state": "succeeded",
                    "metadata": metadata,
                }],
            }
        },
        "artifacts": [{
            "node_id": "collect",
            "attempt_id": "attempt-winner",
            "relative_path": "output.json",
            "media_type": "application/json",
            "size_bytes": len(canonical),
            "sha256": hashlib.sha256(canonical).hexdigest(),
        }],
    }
    scheduler = RunScheduler.__new__(RunScheduler)

    assert scheduler._output_values(projection, tmp_path) == {}


def test_archon_ai_never_falls_back_to_stdout_when_canonical_output_is_missing(
    tmp_path,
):
    stdout = tmp_path / "stdout.txt"
    stdout.write_text("provider response", encoding="utf-8")
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
                "type": "prompt",
                "attempts": [{
                    "attempt_id": "attempt-winner",
                    "state": "succeeded",
                    "metadata": {
                        "primary_output_candidate": {
                            "attempt_relative_path": "output.json",
                            "media_type": "text/plain",
                            "size_bytes": 17,
                            "sha256": "3" * 64,
                            "schema_fingerprint": None,
                            "canonicalization_version": 1,
                            "output_type": None,
                        }
                    },
                }],
            }
        },
        "artifacts": [{
            "node_id": "collect",
            "attempt_id": "attempt-winner",
            "relative_path": "stdout.txt",
            "media_type": "text/plain",
            "size_bytes": len(stdout.read_bytes()),
            "sha256": hashlib.sha256(stdout.read_bytes()).hexdigest(),
        }],
    }
    scheduler = RunScheduler.__new__(RunScheduler)

    assert scheduler._output_values(projection, tmp_path) == {}


def test_archon_shell_output_retains_stdout_compatibility(tmp_path):
    stdout = tmp_path / "stdout.txt"
    stdout.write_text('{"ok":true}', encoding="utf-8")
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
                "type": "bash",
                "attempts": [{
                    "attempt_id": "attempt-winner",
                    "state": "succeeded",
                }],
            }
        },
        "artifacts": [{
            "node_id": "collect",
            "attempt_id": "attempt-winner",
            "relative_path": "stdout.txt",
            "media_type": "text/plain",
            "size_bytes": len(stdout.read_bytes()),
            "sha256": hashlib.sha256(stdout.read_bytes()).hexdigest(),
        }],
    }
    scheduler = RunScheduler.__new__(RunScheduler)

    resolved = scheduler._output_values(projection, tmp_path)["collect"]

    assert resolved.value["ok"] is True


def test_archon_transient_read_failure_is_retried_for_the_same_identity(
    tmp_path, monkeypatch
):
    canonical = b'{"ok":true}'
    output = tmp_path / "output.json"
    output.write_bytes(canonical)
    digest = hashlib.sha256(canonical).hexdigest()
    candidate = {
        "attempt_relative_path": "output.json",
        "media_type": "application/json",
        "size_bytes": len(canonical),
        "sha256": digest,
        "schema_fingerprint": "3" * 64,
        "canonicalization_version": 1,
        "output_type": None,
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
                "type": "prompt",
                "attempts": [{
                    "attempt_id": "attempt-winner",
                    "state": "succeeded",
                    "metadata": {"primary_output_candidate": candidate},
                }],
            }
        },
        "artifacts": [{
            "node_id": "collect",
            "attempt_id": "attempt-winner",
            "relative_path": "output.json",
            "media_type": "application/json",
            "size_bytes": len(canonical),
            "sha256": digest,
        }],
    }
    original_read = output_resolution.os.read
    attempts = 0

    def fail_once(descriptor, size):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError(errno.EIO, "temporary read failure")
        return original_read(descriptor, size)

    monkeypatch.setattr(output_resolution.os, "read", fail_once)
    scheduler = RunScheduler.__new__(RunScheduler)

    first = scheduler._output_values(projection, tmp_path)
    second = scheduler._output_values(projection, tmp_path)

    assert first == {}
    assert second["collect"].value["ok"] is True
    assert attempts >= 2


def test_archon_resolution_failure_is_stable_for_one_descriptor_identity(tmp_path):
    canonical = b'{"ok":true}'
    output = tmp_path / "output.json"
    output.write_bytes(b'{"no":true}')
    digest = hashlib.sha256(canonical).hexdigest()
    candidate = {
        "attempt_relative_path": "output.json",
        "media_type": "application/json",
        "size_bytes": len(canonical),
        "sha256": digest,
        "schema_fingerprint": "3" * 64,
        "canonicalization_version": 1,
        "output_type": None,
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
                "type": "prompt",
                "attempts": [{
                    "attempt_id": "attempt-winner",
                    "state": "succeeded",
                    "metadata": {"primary_output_candidate": candidate},
                }],
            }
        },
        "artifacts": [{
            "node_id": "collect",
            "attempt_id": "attempt-winner",
            "relative_path": "output.json",
            "media_type": "application/json",
            "size_bytes": len(canonical),
            "sha256": digest,
        }],
    }
    scheduler = RunScheduler.__new__(RunScheduler)

    first = scheduler._output_values(projection, tmp_path)
    output.write_bytes(canonical)
    second = scheduler._output_values(projection, tmp_path)

    assert first == second == {}


def test_stale_parallel_projection_cannot_prune_newer_resolved_output(tmp_path):
    paths = {node_id: tmp_path / node_id / "output.json" for node_id in ("a", "b")}
    for node_id, path in paths.items():
        path.parent.mkdir()
        path.write_bytes(json.dumps({"node": node_id}).encode())

    def descriptor(node_id):
        data = paths[node_id].read_bytes()
        return {
            "node_id": node_id,
            "attempt_id": f"attempt-{node_id}",
            "relative_path": paths[node_id].relative_to(tmp_path).as_posix(),
            "media_type": "application/json",
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
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
            node_id: {
                "type": "bash",
                "attempts": [{
                    "attempt_id": f"attempt-{node_id}",
                    "state": "succeeded",
                }],
            }
            for node_id in paths
        },
        "artifacts": [descriptor(node_id) for node_id in paths],
    }
    stale = copy.deepcopy(projection)
    stale["nodes"].pop("b")
    stale["artifacts"] = [stale["artifacts"][0]]
    scheduler = RunScheduler.__new__(RunScheduler)
    authoritative = scheduler._output_values(projection, tmp_path)
    paths["b"].write_bytes(b'{"node":"mutated"}')

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(scheduler._output_values, stale, tmp_path).result()
    after_stale = scheduler._output_values(projection, tmp_path)

    assert after_stale["b"] is authoritative["b"]


def test_resolved_output_cache_uses_a_shared_byte_weighted_lru(tmp_path):
    def projection(node_id):
        data = json.dumps({"node": node_id, "value": "x" * 64}).encode()
        path = tmp_path / node_id / "output.json"
        path.parent.mkdir()
        path.write_bytes(data)
        return data, path, {
            "run_id": "run-1",
            "language": {
                "effective_profile": "archon-2026-07",
                "normalizer_version": 2,
                "normalized_definition_digest": "1" * 64,
                "semantic_fingerprint": "2" * 64,
                "structured_outputs": {},
            },
            "nodes": {
                node_id: {
                    "type": "bash",
                    "attempts": [{
                        "attempt_id": f"attempt-{node_id}",
                        "state": "succeeded",
                    }],
                }
            },
            "artifacts": [{
                "node_id": node_id,
                "attempt_id": f"attempt-{node_id}",
                "relative_path": path.relative_to(tmp_path).as_posix(),
                "media_type": "application/json",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }],
        }

    entries = {node_id: projection(node_id) for node_id in ("a", "b", "c")}
    scheduler = RunScheduler.__new__(RunScheduler)
    scheduler._ensure_output_resolution_state()
    scheduler._output_resolution_cache_max_bytes = 1_000_000
    first = scheduler._output_values(entries["a"][2], tmp_path)["a"]
    single_weight = scheduler._output_resolution_cache_bytes
    scheduler._output_resolution_cache_max_bytes = single_weight * 2

    scheduler._output_values(entries["b"][2], tmp_path)
    assert scheduler._output_values(entries["a"][2], tmp_path)["a"] is first
    entries["a"][1].write_bytes(b"x" * len(entries["a"][0]))
    entries["b"][1].write_bytes(b"x" * len(entries["b"][0]))
    scheduler._output_values(entries["c"][2], tmp_path)

    assert single_weight >= len(entries["a"][0]) * 3
    assert scheduler._output_resolution_cache_bytes <= (
        scheduler._output_resolution_cache_max_bytes
    )
    assert scheduler._output_values(entries["a"][2], tmp_path)["a"] is first
    assert scheduler._output_values(entries["b"][2], tmp_path) == {}


def test_candidate_registration_is_linearized_with_durable_completion(tmp_path):
    canonical = b'{"ok":true}'
    output = tmp_path / "output.json"
    output.write_bytes(canonical)
    digest = hashlib.sha256(canonical).hexdigest()
    artifact = ArtifactRef("output.json", "application/json", len(canonical), digest)
    candidate = PrimaryOutputCandidate(
        attempt_relative_path="output.json",
        media_type="application/json",
        size_bytes=len(canonical),
        sha256=digest,
        structured_value={"ok": True},
        schema_fingerprint="3" * 64,
        canonicalization_version=1,
        output_type="Result",
    )
    claim = NodeClaim(
        run_id="run-1",
        node_id="collect",
        attempt_id="attempt-1",
        owner_id="owner",
        lease_expires_at=datetime.now(timezone.utc),
    )
    visible = threading.Event()
    release = threading.Event()
    resolver_started = threading.Event()
    resolver_done = threading.Event()

    class BlockingStore:
        def __init__(self):
            self.projection = {
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
                        "type": "prompt",
                        "attempts": [{
                            "attempt_id": "attempt-1",
                            "state": "running",
                        }],
                    }
                },
                "artifacts": [],
            }

        def complete_node(self, completed_claim, **kwargs):
            attempt = self.projection["nodes"]["collect"]["attempts"][-1]
            attempt.update({
                "state": kwargs["status"],
                "metadata": copy.deepcopy(kwargs["metadata"]),
            })
            self.projection["artifacts"] = [{
                "node_id": completed_claim.node_id,
                "attempt_id": completed_claim.attempt_id,
                "relative_path": artifact.relative_path,
                "media_type": artifact.media_type,
                "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256,
            }]
            visible.set()
            assert release.wait(timeout=2)

        def load_run(self, _run_id):
            return copy.deepcopy(self.projection)

    scheduler = RunScheduler.__new__(RunScheduler)
    scheduler.store = BlockingStore()
    scheduler._ensure_output_resolution_state()
    result = NodeExecutionResult(
        "succeeded",
        (artifact,),
        primary_output=candidate,
    )
    node = WorkflowNode("collect", "prompt", "produce", (), 0, None, {})

    def resolve_visible_output():
        resolver_started.set()
        try:
            return scheduler._output_values(
                scheduler.store.load_run("run-1"), tmp_path
            )
        finally:
            resolver_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        persist = pool.submit(
            scheduler._persist_result,
            claim,
            node,
            result,
            RunExecutionLimits(),
        )
        assert visible.wait(timeout=1)
        resolve = pool.submit(resolve_visible_output)
        assert resolver_started.wait(timeout=1)
        try:
            assert not resolver_done.wait(timeout=0.1)
        finally:
            release.set()
        persist.result(timeout=1)
        resolved = resolve.result(timeout=1)["collect"]

    assert resolved.value is candidate.structured_value


def test_failed_durable_completion_rolls_back_registered_candidate(tmp_path):
    canonical = b'{"ok":true}'
    digest = hashlib.sha256(canonical).hexdigest()
    candidate = PrimaryOutputCandidate(
        attempt_relative_path="output.json",
        media_type="application/json",
        size_bytes=len(canonical),
        sha256=digest,
        structured_value={"ok": True},
        schema_fingerprint="3" * 64,
        canonicalization_version=1,
        output_type=None,
    )
    claim = NodeClaim(
        "run-1",
        "collect",
        "attempt-1",
        "owner",
        datetime.now(timezone.utc),
    )

    class FailingStore:
        @staticmethod
        def complete_node(*_args, **_kwargs):
            raise RuntimeError("durable completion failed")

    scheduler = RunScheduler.__new__(RunScheduler)
    scheduler.store = FailingStore()
    scheduler._ensure_output_resolution_state()
    result = NodeExecutionResult(
        "succeeded",
        (ArtifactRef("output.json", "application/json", len(canonical), digest),),
        primary_output=candidate,
    )
    node = WorkflowNode("collect", "prompt", "produce", (), 0, None, {})

    with pytest.raises(RuntimeError, match="durable completion failed"):
        scheduler._persist_result(
            claim,
            node,
            result,
            RunExecutionLimits(),
        )

    assert scheduler._primary_output_candidates == {}
    assert scheduler._resolved_output_cache == {}
    assert scheduler._output_resolution_cache_bytes == 0


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
