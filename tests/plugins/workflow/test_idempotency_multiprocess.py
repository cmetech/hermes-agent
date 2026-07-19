from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
import multiprocessing

from plugins.workflow.cli import register_cli
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.schema import load_workflow
from plugins.workflow.trust import (
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
)


def _run_cli_child(workdir: str, home: str, arguments: str, output) -> None:
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
