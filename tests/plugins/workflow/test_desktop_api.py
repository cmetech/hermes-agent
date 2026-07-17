from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from plugins.workflow.showcase import run_showcase


def _module():
    path = Path(__file__).parents[3] / "plugins/workflow/dashboard/plugin_api.py"
    spec = importlib.util.spec_from_file_location("workflow_dashboard_api_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _router():
    return _module().router


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


def test_events_long_poll_returns_when_a_new_event_arrives(monkeypatch):
    module = _module()

    class FakeStore:
        calls = 0

        def get_run_status(self, _run_id, **_kwargs):
            return {"operator_scope_digest": None}

        def events_after(self, _run_id, *, after, **_kwargs):
            self.calls += 1
            events = [] if self.calls < 3 else [{"sequence": after + 1}]
            return {
                "schema_version": 1,
                "events": events,
                "next_cursor": after if not events else after + 1,
                "cursor_reset": False,
            }

    store = FakeStore()
    monkeypatch.setattr(module, "_store", lambda: store)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/plugins/workflow")

    response = TestClient(app).get(
        "/api/plugins/workflow/runs/run-1/events?after=7&wait_seconds=1"
    )

    assert response.status_code == 200
    assert response.json()["events"] == [{"sequence": 8}]
    assert store.calls == 3


def test_attention_includes_real_workflow_approval_interactions(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    started = run_showcase(
        "laptop-diagnostic", hermes_home=home, symptom="fictional attention"
    )
    app = FastAPI()
    app.include_router(_router(), prefix="/api/plugins/workflow")

    client = TestClient(app)
    response = client.get("/api/plugins/workflow/attention")

    assert response.status_code == 200
    item = next(
        item for item in response.json()["items"]
        if item["run_id"] == started["run_id"]
    )
    assert item["interaction"]["type"] == "workflow_approval"
    detail = client.get(f"/api/plugins/workflow/runs/{started['run_id']}").json()
    assert detail["pending_interaction"]["interaction_id"] == item["interaction"][
        "interaction_id"
    ]
    assert detail["pending_interaction"]["node_id"] == item["node_id"]
    assert detail["next_actions"] == [
        "status",
        "events",
        "approve",
        "reject",
        "cancel",
    ]
