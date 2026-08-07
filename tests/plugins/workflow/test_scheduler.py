from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
import errno
import hashlib
import json
import multiprocessing
import sys
import threading
from types import MappingProxyType

import pytest

from agent.plugin_agent import PluginAgentRunResult
from hermes_cli.runtime_provider import ExecutionRuntimeCapabilities
from plugins.workflow import output_resolution
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.models import (
    RunExecutionLimits,
    WorkflowNode,
    WorkflowValidationError,
)
from plugins.workflow.output_resolution import PrimaryOutputCandidate, ResolvedNodeOutput
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    execution_capability_context,
)
from plugins.workflow.scheduler import RunScheduler, evaluate_condition
from plugins.workflow.schema import load_workflow, load_workflow_snapshot
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


@pytest.mark.parametrize(
    "error_number",
    (errno.EIO, errno.ENOMEM),
    ids=("io", "host-memory"),
)
def test_archon_transient_read_failure_is_retried_for_the_same_identity(
    tmp_path, monkeypatch, error_number
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
            raise OSError(error_number, "temporary read failure")
        return original_read(descriptor, size)

    monkeypatch.setattr(output_resolution.os, "read", fail_once)
    scheduler = RunScheduler.__new__(RunScheduler)

    first = scheduler._output_values(projection, tmp_path)
    second = scheduler._output_values(projection, tmp_path)

    assert first == {}
    assert second["collect"].value["ok"] is True
    assert attempts >= 2


def test_archon_missing_completed_output_is_stable_for_descriptor_identity(tmp_path):
    canonical = b'{"ok":true}'
    digest = hashlib.sha256(canonical).hexdigest()
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
            "relative_path": "output.json",
            "media_type": "application/json",
            "size_bytes": len(canonical),
            "sha256": digest,
        }],
    }
    scheduler = RunScheduler.__new__(RunScheduler)

    first = scheduler._output_values(projection, tmp_path)
    (tmp_path / "output.json").write_bytes(canonical)
    second = scheduler._output_values(projection, tmp_path)

    assert first == second == {}


@pytest.mark.parametrize("relative_path", ("output.json\0", "./output.json"))
def test_archon_invalid_descriptor_path_is_a_stable_missing_output(
    tmp_path, relative_path
):
    canonical = b'{"ok":true}'
    (tmp_path / "output.json").write_bytes(canonical)
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
            "relative_path": relative_path,
            "media_type": "application/json",
            "size_bytes": len(canonical),
            "sha256": hashlib.sha256(canonical).hexdigest(),
        }],
    }
    scheduler = RunScheduler.__new__(RunScheduler)

    first = scheduler._output_values(projection, tmp_path)
    second = scheduler._output_values(projection, tmp_path)

    assert first == second == {}


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


def test_resolved_output_cache_does_not_retain_overweight_proxy_graph(tmp_path):
    item_count = 2_048
    canonical = ("[" + ",".join("{}" for _ in range(item_count)) + "]").encode()
    output = tmp_path / "output.json"
    output.write_bytes(canonical)
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
            "relative_path": "output.json",
            "media_type": "application/json",
            "size_bytes": len(canonical),
            "sha256": hashlib.sha256(canonical).hexdigest(),
        }],
    }
    # Deliberately incomplete retained-size lower bound: body bytes + text,
    # tuple storage, and each proxy plus an equivalent empty backing dict.
    retained_lower_bound = (
        len(canonical) * 2
        + sys.getsizeof(tuple(None for _ in range(item_count)))
        + item_count
        * (sys.getsizeof(MappingProxyType({})) + sys.getsizeof({}))
    )
    scheduler = RunScheduler.__new__(RunScheduler)
    scheduler._ensure_output_resolution_state()
    scheduler._output_resolution_cache_max_bytes = retained_lower_bound - 1

    resolved = scheduler._output_values(projection, tmp_path)["collect"]

    assert len(resolved.value) == item_count
    assert scheduler._resolved_output_cache == {}
    assert scheduler._output_resolution_cache_bytes == 0


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


class _RecordingSchedulerRunner:
    def __init__(
        self,
        response: str = "done",
        *,
        declaration_source: str = "default_prompt_adapter",
    ) -> None:
        self.response = response
        self.declaration_source = declaration_source
        self.requests = []

    def run(self, request, **_kwargs):
        self.requests.append(request)
        structured = None
        audit = {}
        if request.structured_output is not None:
            structured = {
                "provider_attempts": 1,
                "model_calls": 1,
                "strategy": request.structured_output.strategy.value,
                "adapter_version": request.structured_output.adapter_version,
                "schema_fingerprint": (
                    request.structured_output.schema.schema_fingerprint
                ),
                "declaration_source": self.declaration_source,
            }
            audit = {
                **structured,
                "api_calls": 1,
                "api_mode": "chat_completions",
            }
        return PluginAgentRunResult(
            final_response=self.response,
            session_id=f"session-{len(self.requests)}",
            provider=request.provider or "fake-provider",
            model=request.model or "fake-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit=audit,
            structured_output=structured,
        )


def _start_archon_scheduler_run(
    tmp_path,
    workflow_writer,
    *,
    name: str,
    nodes: list[dict[str, object]],
    commands: dict[str, str] | None = None,
):
    root = tmp_path / name
    workflow = workflow_writer(
        root / "workflows",
        name=name,
        filename=f"{name}.yaml",
        nodes=nodes,
    )
    if commands:
        directory = root / "commands"
        directory.mkdir()
        for command_name, body in commands.items():
            (directory / f"{command_name}.md").write_text(body, encoding="utf-8")
    workflow.with_name(f"{workflow.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow_snapshot(
        workflow,
        workflow_bytes=workflow.read_bytes(),
        sidecar_bytes=workflow.with_name(
            f"{workflow.stem}.hermes.yaml"
        ).read_bytes(),
        normalizer_version=3,
    )
    execution_context = execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("real"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=True),
        runtime_capabilities=ExecutionRuntimeCapabilities(
            api_mode="chat_completions",
            hermes_managed_tool_loop=True,
            effective_provider="fake-provider",
            model="fake-model",
        ),
    )
    store = RunStore(tmp_path / f"home-{name}")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=name,
            concurrency_key=name,
            run_metadata=execution_context.structured_output_run_metadata(package),
        ),
        immutable_snapshot=prepared,
    )
    decisions = execution_context.structured_output_decisions(package)
    return (
        store,
        admitted.run_id,
        {node_id: decision.declaration_source for node_id, decision in decisions.items()},
    )


@pytest.mark.parametrize("producer_kind", ("bash", "script"))
@pytest.mark.parametrize(
    "text",
    ('{"answer":42}', '["answer"]', "42", "true", "null"),
)
def test_v3_real_deterministic_producer_preserves_schemaless_json_text(
    tmp_path,
    workflow_writer,
    producer_kind: str,
    text: str,
) -> None:
    producer = {
        "id": "producer",
        producer_kind: (
            f"printf '%s' '{text}'"
            if producer_kind == "bash"
            else f"print({text!r})"
        ),
    }
    if producer_kind == "script":
        producer["runtime"] = "uv"
    name = f"strict-{producer_kind}-{hashlib.sha256(text.encode()).hexdigest()[:8]}"
    store, run_id, _declaration_sources = _start_archon_scheduler_run(
        tmp_path,
        workflow_writer,
        name=name,
        nodes=[
            producer,
            {
                "id": "consumer",
                "prompt": "consume [$producer.output]",
                "depends_on": ["producer"],
            },
        ],
    )
    runner = _RecordingSchedulerRunner()
    scheduler = RunScheduler(store, agent_runner=runner)

    try:
        result = scheduler.advance(run_id, max_nodes=10)
        outputs = scheduler._output_values(result, store.run_directory(run_id))
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert result["status"] == "succeeded"
    assert [request.prompt for request in runner.requests] == [f"consume [{text}]"]
    resolved = outputs["producer"]
    assert isinstance(resolved, output_resolution.ResolvedNodeOutput)
    assert resolved.value == text
    assert resolved.text == text
    assert resolved.schema_fingerprint is None


@pytest.mark.parametrize("surface", ("prompt", "command"))
def test_v3_scheduler_keeps_ai_reference_failure_terminal_before_provider(
    tmp_path,
    workflow_writer,
    surface: str,
) -> None:
    consumer = {
        "id": "consumer",
        surface: (
            "consume" if surface == "command" else "Use $producer.output.missing"
        ),
        "depends_on": ["producer"],
        "retry": {"max_attempts": 2, "on_error": "all", "delay_ms": 1000},
    }
    commands = (
        {"consume": "Use $producer.output.missing\n"}
        if surface == "command"
        else None
    )
    store, run_id, declaration_sources = _start_archon_scheduler_run(
        tmp_path,
        workflow_writer,
        name=f"terminal-reference-{surface}",
        nodes=[
            {
                "id": "producer",
                "prompt": "Produce",
                "output_format": {
                    "type": "object",
                    "properties": {
                        "present": {"type": "string"},
                        "missing": {"type": "string"},
                    },
                },
            },
            consumer,
        ],
        commands=commands,
    )
    runner = _RecordingSchedulerRunner(
        '{"present":"ready"}',
        declaration_source=declaration_sources["producer"],
    )
    scheduler = RunScheduler(store, agent_runner=runner)

    try:
        result = scheduler.advance(run_id, max_nodes=10)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    producer_state = result["nodes"]["producer"]
    assert producer_state["state"] == "succeeded", producer_state
    state = result["nodes"]["consumer"]
    assert result["status"] == "failed"
    assert state["state"] == "failed"
    assert state["retry_consumed"] == 0
    assert state["attempts"] == []
    assert [request.prompt for request in runner.requests] == ["Produce"]
    assert result["last_error"]["code"] == "output_reference_field_missing"


class _PackageValidationRunnerTrap:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request, *, is_cancelled=None):
        self.requests.append(request)
        raise AssertionError("provider ran before authenticated package validation")


def _write_impossible_authenticated_command(tmp_path, workflow_writer, *, name: str):
    root = tmp_path / name
    commands = root / "commands"
    commands.mkdir(parents=True)
    command = commands / "consume.md"
    command.write_text("Use $producer.output.missing\n", encoding="utf-8")
    workflow = workflow_writer(
        root / "workflows",
        name=name,
        filename=f"{name}.yaml",
        nodes=[
            {
                "id": "producer",
                "prompt": "Produce",
                "output_format": {
                    "type": "object",
                    "properties": {"present": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            {
                "id": "consumer",
                "command": "consume",
                "depends_on": ["producer"],
            },
        ],
    )
    sidecar = workflow.with_name(f"{name}.hermes.yaml")
    sidecar.write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    return workflow, sidecar, command


def _admit_v2_impossible_authenticated_command(
    tmp_path, workflow_writer, *, name: str
):
    workflow, sidecar, command = _write_impossible_authenticated_command(
        tmp_path,
        workflow_writer,
        name=name,
    )
    package = load_workflow_snapshot(
        workflow,
        workflow_bytes=workflow.read_bytes(),
        sidecar_bytes=sidecar.read_bytes(),
        normalizer_version=2,
    )
    assert package.language.normalizer_version == 2
    store = RunStore(tmp_path / f"home-{name}")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
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
    assert admitted.run_id is not None
    command.write_text("Use $producer.output.present\n", encoding="utf-8")
    return store, admitted.run_id


def test_v3_admission_rejects_impossible_authenticated_command_before_snapshot(
    tmp_path, workflow_writer
):
    workflow, _sidecar, _command = _write_impossible_authenticated_command(
        tmp_path,
        workflow_writer,
        name="package-validation-v3",
    )
    package = load_workflow_snapshot(
        workflow,
        workflow_bytes=workflow.read_bytes(),
        sidecar_bytes=workflow.with_name(
            f"{workflow.stem}.hermes.yaml"
        ).read_bytes(),
        normalizer_version=3,
    )
    store = RunStore(tmp_path / "home-package-validation-v3")

    with pytest.raises(WorkflowValidationError) as exc_info:
        store.prepare_run_snapshot(package)

    assert [(issue.path, issue.code) for issue in exc_info.value.issues] == [
        ("nodes[1].command", "structured_output_field_impossible")
    ]
    assert store.list_runs() == ()


@pytest.mark.parametrize("entrypoint", ("advance", "advance_all"))
def test_public_scheduler_durably_fails_admitted_v2_impossible_command_before_claim(
    tmp_path, workflow_writer, monkeypatch, entrypoint
):
    store, run_id = _admit_v2_impossible_authenticated_command(
        tmp_path,
        workflow_writer,
        name=f"package-validation-{entrypoint}",
    )
    runner = _PackageValidationRunnerTrap()
    scheduler = RunScheduler(store, agent_runner=runner)
    executor_calls = []

    def reject_execution(*args, **kwargs):
        executor_calls.append((args, kwargs))
        raise AssertionError("executor ran before authenticated package validation")

    monkeypatch.setattr(scheduler, "_execute_claim", reject_execution)
    try:
        if entrypoint == "advance":
            failed = scheduler.advance(run_id)
            replay = scheduler.advance(run_id)
        else:
            failed = scheduler.advance_all([run_id])[run_id]
            replay = scheduler.advance_all([run_id])[run_id]
    finally:
        scheduler.shutdown(deadline_seconds=2)

    expected_error = {
        "code": "structured_output_field_impossible",
        "path": "nodes[1].command",
        "message": "structured output field missing is impossible for node producer",
    }
    assert failed["status"] == replay["status"] == "failed"
    assert failed["last_error"] == replay["last_error"] == expected_error
    assert len(json.dumps(expected_error).encode("utf-8")) < 4_096
    assert failed["event_sequence"] == replay["event_sequence"]
    assert runner.requests == []
    assert executor_calls == []

    events = store.tail_events(run_id, limit=20)
    failures = [event for event in events if event["event_type"] == "run_failed"]
    assert [event["payload"] for event in failures] == [{
        "reason_code": "package_validation_failed",
        "validation_code": "structured_output_field_impossible",
        "validation_path": "nodes[1].command",
    }]
    with store._connect() as connection:
        indexed = connection.execute(
            "SELECT status, projection_state_version FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        assert (indexed["status"], indexed["projection_state_version"]) == (
            "failed",
            failed["state_version"],
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM worker_claims WHERE run_id=?", (run_id,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM coordinator_wakes "
            "WHERE run_id=? AND reason_code='run_failed'",
            (run_id,),
        ).fetchone()[0] == 1
