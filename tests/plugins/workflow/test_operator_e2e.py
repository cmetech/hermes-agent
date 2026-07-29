from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.cli import register_cli
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.schema import load_workflow
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.showcase import build_showcase_report, run_showcase
from plugins.workflow.store import ForegroundExecutionConflict, RunStore
from plugins.workflow.trust import (
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
)


def test_operator_views_agree_on_wait_attention_identity_and_artifacts(
    tmp_path,
) -> None:
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
    always_run_nodes = RunScheduler(store).verified_always_run_nodes(
        admitted.run_id
    )

    with pytest.raises(ForegroundExecutionConflict, match="foreground owner conflict"):
        store.resume_run(
            admitted.run_id,
            always_run_nodes=always_run_nodes,
        )

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


def test_post_runs_api_admission_returns_before_blocking_advance(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(
        workflow_writer(
            home / "workflows",
            name="api-background-only",
            filename="api-background-only.yaml",
            nodes=[{"id": "blocking", "bash": "sleep 30"}],
        )
    )
    digest = compute_package_digest(package)
    risk = build_risk_summary(package, assess_compatibility(package))
    WorkflowTrustStore(home).trust(
        digest.sha256,
        actor="test-operator",
        risk_digest=risk.risk_digest,
    )
    store = RunStore(home)
    assert (
        CoordinatorStore(store.database)
        .try_acquire(
            CoordinatorIdentity(
                owner_id="api-e2e",
                host_kind="web",
                host_instance_id="api-e2e",
                pid=1,
                process_start_time=None,
            ),
            now=datetime.now(timezone.utc),
            lease_seconds=60,
        )
        .is_leader
    )

    def forbidden_advance(*_args, **_kwargs):
        raise AssertionError("REST admission must not execute workflow tails")

    monkeypatch.setattr(RunScheduler, "advance", forbidden_advance)
    path = Path(__file__).parents[3] / "plugins/workflow/dashboard/plugin_api.py"
    spec = importlib.util.spec_from_file_location("workflow_api_e2e_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    app = FastAPI()

    @app.middleware("http")
    async def local_admin(request, call_next):
        request.state.local_admin_authenticated = True
        return await call_next(request)

    app.include_router(module.router, prefix="/api/plugins/workflow")
    started_at = time.monotonic()
    response = TestClient(app).post(
        "/api/plugins/workflow/runs",
        json={
            "workflow": "api-background-only",
            "values": {},
            "idempotency_key": "background-only",
            "concurrency_policy": "queue",
        },
    )
    elapsed = time.monotonic() - started_at

    assert response.status_code == 202
    assert elapsed < 1
    run = store.get_run_status(response.json()["result"]["run_id"])
    assert run["status"] == "running"
    assert run["execution_mode"] == "background"
    assert run["nodes"]["blocking"]["state"] == "ready"
    assert run["trigger"] == "desktop"
    assert run["provenance"]["source"] == "desktop"
    assert run["provenance"]["assurance"] == "local_admin_claim"
    assert run["provenance"]["claimed_actor"] == "profile-local-dashboard"
    assert run["provenance"]["source_instance"] == "api:local-admin"
    assert run["provenance"]["return_route"] is None
