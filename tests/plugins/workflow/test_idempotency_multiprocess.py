from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import multiprocessing

from plugins.workflow.cli import register_cli
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.schema import load_workflow
from plugins.workflow.models import LoopGroupChildScope
from plugins.workflow.store import RunStore
from tests.plugins.workflow.test_phase6_store import (
    _EXECUTION_AUTHORITY,
    _admit_group,
    _initialize,
    _scope,
)
from plugins.workflow.trust import (
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
)


def _run_cli_child(workdir: str, home: str, arguments: str, output, start=None) -> None:
    parser = argparse.ArgumentParser()
    register_cli(parser)
    argv = [
        "--workdir",
        workdir,
        "--hermes-home",
        home,
        "run",
        "multiprocess-idempotency",
        "--idempotency-key",
        "stable-key",
        "--arguments",
        arguments,
        "--foreground",
        "--json",
    ]
    stream = io.StringIO()
    args = parser.parse_args(argv)
    if start is not None:
        assert start.wait(timeout=20)
    with redirect_stdout(stream):
        exit_code = args.func(args)
    output.put((exit_code, json.loads(stream.getvalue())))


def _run_cli_process(workdir, home, *, arguments: str) -> tuple[int, dict]:
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    process = context.Process(
        target=_run_cli_child,
        args=(str(workdir), str(home), arguments, output),
    )
    process.start()
    result = output.get(timeout=20)
    process.join(timeout=20)
    assert process.exitcode == 0
    return result


def _claim_loop_group_child_process(
    home: str,
    scope: LoopGroupChildScope,
    state_version: int,
    start,
    output,
) -> None:
    store = RunStore(home)
    assert start.wait(timeout=20)
    try:
        claim = store.claim_loop_group_child(
            scope,
            "multiprocess-child",
            expected_state_version=state_version,
            execution_authority=_EXECUTION_AUTHORITY,
        )
    except RuntimeError as exc:
        output.put((None, str(exc)))
    else:
        output.put((claim.attempt_id if claim else None, None))


def _trusted_cli_fixture(tmp_path, workflow_writer):
    workdir = tmp_path / "repo"
    path = workflow_writer(
        workdir / ".hermes" / "workflows",
        name="multiprocess-idempotency",
        nodes=[{"id": "start", "bash": "printf stable"}],
    )
    package = load_workflow(path)
    profile = tmp_path / "profile"
    package_digest = compute_package_digest(package)
    risk = build_risk_summary(package, assess_compatibility(package))
    WorkflowTrustStore(profile).trust(
        package_digest.sha256,
        actor="test",
        risk_digest=risk.risk_digest,
    )
    return workdir, profile


def test_same_cli_key_from_new_process_joins_existing(
    tmp_path, workflow_writer
) -> None:
    workdir, profile = _trusted_cli_fixture(tmp_path, workflow_writer)

    first_code, first = _run_cli_process(workdir, profile, arguments="same")
    second_code, second = _run_cli_process(workdir, profile, arguments="same")

    assert first_code == second_code == 0
    assert first["result"]["run_id"] == second["result"]["run_id"]
    assert first["result"]["admission_disposition"] == "created"
    assert second["result"]["admission_disposition"] == "existing"


def test_same_cli_key_with_changed_inputs_conflicts_across_processes(
    tmp_path, workflow_writer
) -> None:
    workdir, profile = _trusted_cli_fixture(tmp_path, workflow_writer)

    first_code, _first = _run_cli_process(workdir, profile, arguments="first")
    second_code, second = _run_cli_process(workdir, profile, arguments="second")

    assert first_code == 0
    assert second_code == 5
    assert second["error"]["code"] == "idempotency_conflict"


def test_concurrent_same_semantic_start_is_created_once(
    tmp_path, workflow_writer
) -> None:
    workdir, profile = _trusted_cli_fixture(tmp_path, workflow_writer)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    output = context.Queue()
    processes = [
        context.Process(
            target=_run_cli_child,
            args=(str(workdir), str(profile), "same", output, start),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    child_results = [output.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    envelopes = [envelope for _code, envelope in child_results]
    assert sorted(
        envelope["result"]["admission_disposition"] for envelope in envelopes
    ) == ["created", "existing"]
    assert len({envelope["result"]["run_id"] for envelope in envelopes}) == 1
    assert all(code == 0 for code, _envelope in child_results)
    assert len(RunStore(profile).list_runs()) == 1


def test_concurrent_loop_group_child_claim_is_created_once(
    tmp_path, workflow_writer
) -> None:
    home, store, run_id, group = _admit_group(tmp_path, workflow_writer)
    initialized = _initialize(store, run_id, group)
    scope = _scope(run_id, "select")
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    output = context.Queue()
    processes = [
        context.Process(
            target=_claim_loop_group_child_process,
            args=(
                str(home),
                scope,
                initialized["state_version"],
                start,
                output,
            ),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    results = [output.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    assert len([attempt_id for attempt_id, _error in results if attempt_id]) == 1
    assert all(
        attempt_id is not None or error in {None, "stale loop group state version"}
        for attempt_id, error in results
    )
    with store._connect() as connection:
        rows = connection.execute(
            "SELECT node_id FROM worker_claims WHERE run_id=?",
            (run_id,),
        ).fetchall()
    assert [row["node_id"] for row in rows] == [scope.worker_node_id]
