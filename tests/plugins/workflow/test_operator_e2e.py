from __future__ import annotations

import argparse
import json
import time

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.cli import register_cli
from plugins.workflow.schema import load_workflow
from plugins.workflow.showcase import build_showcase_report, run_showcase
from plugins.workflow.store import ForegroundExecutionConflict, RunStore


def test_operator_views_agree_on_wait_attention_identity_and_artifacts(tmp_path) -> None:
    started = run_showcase(
        "laptop-diagnostic", hermes_home=tmp_path, symptom="fictional operator check"
    )
    run_id = started["run_id"]
    store = RunStore(tmp_path)
    listed = next(item for item in store.list_runs() if item["run_id"] == run_id)
    status = store.get_run_status(run_id)
    report = build_showcase_report(run_id, hermes_home=tmp_path)

    assert listed["status"] == status["status"] == report.terminal_outcome == "paused"
    assert status["health"] == "user_wait"
    assert status["progress"]["kind"] == "graph"
    assert "eta" not in status and "completion_eta" not in status
    assert status["definition_digest"] == report.definition_digest
    assert status["run_metadata"]["showcase_id"] == report.showcase_id
    assert report.interactions[0]["type"] == "workflow_approval"
    assert all(item["verified"] for item in report.artifacts)
    assert status["next_actions"] == [
        "status",
        "events",
        "approve",
        "reject",
        "cancel",
    ]


def test_foreground_owner_stall_does_not_advertise_noop_resume(
    tmp_path, workflow_writer, capsys
) -> None:
    store = RunStore(tmp_path / "home")
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="stalled-foreground")
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="stalled-foreground",
            concurrency_key=package.definition.name,
            execution_mode="foreground",
            foreground_owner_id="dead-owner",
            foreground_lease_seconds=0.01,
        ),
        immutable_snapshot=prepared,
    )
    time.sleep(0.02)
    status = store.get_run_status(admitted.run_id)
    assert status["health"] == "stalled"
    assert status["blocking_reason"] == "foreground_owner_unavailable"
    assert "resume" not in status["next_actions"]
    before = status["state_version"]

    with pytest.raises(ForegroundExecutionConflict, match="foreground owner conflict"):
        store.resume_run(admitted.run_id)

    assert store.load_run(admitted.run_id)["state_version"] == before

    parser = argparse.ArgumentParser()
    register_cli(parser)
    args = parser.parse_args([
        "--hermes-home",
        str(tmp_path / "home"),
        "--workdir",
        str(tmp_path),
        "resume",
        admitted.run_id,
        "--json",
    ])
    assert args.func(args) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is True
    assert envelope["result"]["status"] == "succeeded"
    assert envelope["result"]["state_version"] > before
