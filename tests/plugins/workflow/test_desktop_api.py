from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


def _router():
    path = Path(__file__).parents[3] / "plugins/workflow/dashboard/plugin_api.py"
    spec = importlib.util.spec_from_file_location("workflow_dashboard_api_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.router


def _start(store, package, key, *, scope=None):
    prepared = store.prepare_run_snapshot(package)
    return store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="desktop",
            idempotency_key=key,
            concurrency_key=package.definition.name,
            concurrency_policy="allow",
            operator_scope=scope,
        ),
        immutable_snapshot=prepared,
    )


def test_hidden_manifest_is_api_only():
    root = Path(__file__).parents[3] / "plugins/workflow/dashboard"
    assert '"hidden": true' in (root / "manifest.json").read_text()
    assert (root / "dist/index.js").read_text().strip() == "void 0;"


def test_runs_are_bounded_cursor_paginated_and_scope_authorized(
    tmp_path, monkeypatch, workflow_writer
):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(workflow_writer(tmp_path / "package", name="desktop"))
    store = RunStore(home)
    first = _start(store, package, "one", scope="alice/conversation")
    _start(store, package, "two", scope="bob/conversation")
    app = FastAPI()
    app.include_router(_router(), prefix="/api/plugins/workflow")
    client = TestClient(app)

    page = client.get(
        "/api/plugins/workflow/runs?limit=1",
        headers={"X-Hermes-Operator-Scope": "alice/conversation"},
    )
    assert page.status_code == 200
    body = page.json()
    assert len(body["runs"]) == 1
    assert body["runs"][0]["run_id"] == first.run_id
    assert "operator_scope_digest" not in str(body)
    detail = client.get(
        f"/api/plugins/workflow/runs/{first.run_id}",
        headers={"X-Hermes-Operator-Scope": "bob/conversation"},
    )
    assert detail.status_code == 404


def test_events_cursor_and_stale_action_conflict(tmp_path, monkeypatch, workflow_writer):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(workflow_writer(tmp_path / "package", name="events"))
    store = RunStore(home)
    run = _start(store, package, "one")
    app = FastAPI()
    app.include_router(_router(), prefix="/api/plugins/workflow")
    client = TestClient(app)

    events = client.get(f"/api/plugins/workflow/runs/{run.run_id}/events?after=0&limit=999")
    assert events.status_code == 200
    assert len(events.json()["events"]) <= 200
    assert events.json()["next_cursor"] >= 1

    stale = client.post(
        f"/api/plugins/workflow/runs/{run.run_id}/cancel",
        json={"expected_version": -1},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_state"
