from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import multiprocessing

from plugins.workflow.admission import RunAdmissionRequest
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
