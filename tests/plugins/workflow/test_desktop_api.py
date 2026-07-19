from __future__ import annotations

import importlib.util
import asyncio
import sys
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from hermes_cli.dashboard_auth.base import TokenPrincipal
import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.schema import load_workflow
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore
from plugins.workflow.showcase import run_showcase
from plugins.workflow.runtime import (
    WorkflowApiLimits,
    WorkflowApiRuntime,
    WorkflowStoreRegistry,
)


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


def _app(router, *, session=None, token=None, local_admin=None):
    app = FastAPI()

    @app.middleware("http")
    async def authenticated(request, call_next):
        if local_admin is True or (
            session is None and token is None and local_admin is None
        ):
            request.state.local_admin_authenticated = True
        elif session is not None:
            request.state.session = session
        elif token is not None:
            request.state.token_principal = token
            request.state.token_authenticated = True
        return await call_next(request)

    app.include_router(router, prefix="/api/plugins/workflow")
    return app


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


def test_router_rejects_requests_without_verified_authentication() -> None:
    app = FastAPI()
    app.include_router(_router(), prefix="/api/plugins/workflow")

    response = TestClient(app).get("/api/plugins/workflow/runs")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "authentication_required"


def test_runs_are_bounded_cursor_paginated_and_scope_authorized(
    tmp_path, monkeypatch, workflow_writer
):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(workflow_writer(tmp_path / "package", name="desktop"))
    store = RunStore(home)
    first = _start(store, package, "one", scope="alice/conversation")
    _start(store, package, "two", scope="bob/conversation")
    client = TestClient(_app(_router()))

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


def test_events_cursor_and_stale_action_conflict(
    tmp_path, monkeypatch, workflow_writer
):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(workflow_writer(tmp_path / "package", name="events"))
    store = RunStore(home)
    run = _start(store, package, "one")
    client = TestClient(_app(_router()))

    events = client.get(
        f"/api/plugins/workflow/runs/{run.run_id}/events?after=0&limit=999"
    )
    assert events.status_code == 200
    assert len(events.json()["events"]) <= 200
    assert events.json()["next_cursor"] >= 1

    stale = client.post(
        f"/api/plugins/workflow/runs/{run.run_id}/cancel",
        json={"expected_version": -1},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_state"

    evidence = client.get(
        f"/api/plugins/workflow/runs/{run.run_id}/evidence?kind=timeline"
    )
    assert evidence.status_code == 200
    assert evidence.json()["kind"] == "timeline"
    assert evidence.json()["items"][0]["event_type"] == "run_admitted"


@pytest.mark.parametrize(
    ("action", "interaction_id", "extra"),
    [
        ("approve", None, {}),
        ("reject", "", {"reason": "no"}),
        ("provide-input", None, {"value": "feedback"}),
        ("reconcile", "", {"outcome": "confirmed-failed"}),
    ],
)
def test_null_interaction_is_rejected_before_desktop_mutation(
    tmp_path,
    monkeypatch,
    workflow_writer,
    action,
    interaction_id,
    extra,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name=f"desktop-{action}",
            nodes=[{"id": "review", "approval": {"message": "Approve?"}}],
        )
    )
    store = RunStore(home)
    run = _start(store, package, action)
    paused = RunScheduler(store).advance(run.run_id)
    payload = {
        "expected_version": paused["state_version"],
        "interaction_id": interaction_id,
        **extra,
    }

    response = TestClient(_app(_router())).post(
        f"/api/plugins/workflow/runs/{run.run_id}/{action}", json=payload
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "interaction_id_required"
    assert store.load_run(run.run_id)["state_version"] == paused["state_version"]


def test_archive_restore_views_and_explicit_cleanup_api(
    tmp_path, monkeypatch, workflow_writer
):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(workflow_writer(tmp_path / "package", name="lifecycle"))
    store = RunStore(home)
    run = _start(store, package, "lifecycle")
    RunScheduler(store).advance(run.run_id)
    client = TestClient(_app(_router()))
    current = client.get(f"/api/plugins/workflow/runs/{run.run_id}").json()

    archived = client.post(
        f"/api/plugins/workflow/runs/{run.run_id}/archive",
        json={"expected_version": current["state_version"]},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "succeeded"
    assert archived.json()["next_actions"] == ["status", "events", "restore"]
    assert client.get("/api/plugins/workflow/runs?view=board").json()["runs"] == []
    assert [
        item["run_id"]
        for item in client.get("/api/plugins/workflow/runs?view=archive").json()[
            "runs"
        ]
    ] == [run.run_id]

    restored = client.post(
        f"/api/plugins/workflow/runs/{run.run_id}/restore",
        json={"expected_version": archived.json()["state_version"]},
    )
    assert restored.status_code == 200
    assert [
        item["run_id"]
        for item in client.get("/api/plugins/workflow/runs?view=history").json()[
            "runs"
        ]
    ] == [run.run_id]

    preview = client.get("/api/plugins/workflow/cleanup/preview?older_than=0d")
    assert preview.status_code == 200
    token = preview.json()["confirmation_token"]
    executed = client.post(
        "/api/plugins/workflow/cleanup/execute",
        json={"older_than": "0d", "confirmation_token": token},
    )
    assert executed.status_code == 200
    assert executed.json()["run_ids"] == [run.run_id]
    history = client.get("/api/plugins/workflow/cleanup/history")
    assert history.status_code == 200
    assert history.json()["items"][0]["run_id"] == run.run_id
    assert "source_path" not in str(history.json())


def test_remote_session_cannot_mint_admin_through_cleanup_binding_header(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    session = SimpleNamespace(provider="test", org_id="org", user_id="user-1")
    client = TestClient(_app(_router(), session=session))

    response = client.get(
        "/api/plugins/workflow/cleanup/preview?older_than=0d",
        headers={
            "X-Hermes-Operator-Scope": "dashboard:test:org:user-1:admin"
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "workflow_admin_required"


def test_cleanup_binding_minted_for_one_principal_fails_for_another(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    scope = "service:test:admin-a"
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="cleanup-binding")
    )
    store = RunStore(home)
    run = _start(store, package, "cleanup-binding", scope=scope)
    RunScheduler(store).advance(run.run_id)
    module = _module()
    admin_a = TokenPrincipal(
        principal="admin-a", provider="test", scopes=("workflow:admin",)
    )
    admin_b = TokenPrincipal(
        principal="admin-b", provider="test", scopes=("workflow:admin",)
    )
    preview = TestClient(_app(module.router, token=admin_a)).get(
        "/api/plugins/workflow/cleanup/preview?older_than=0d"
    )
    assert preview.status_code == 200

    executed = TestClient(_app(module.router, token=admin_b)).post(
        "/api/plugins/workflow/cleanup/execute",
        json={
            "older_than": "0d",
            "confirmation_token": preview.json()["confirmation_token"],
        },
    )

    assert executed.status_code == 409
    assert executed.json()["detail"]["code"] == "cleanup_confirmation_invalid"
    assert store.run_directory(run.run_id).is_dir()


def test_events_long_poll_returns_when_a_new_event_arrives(
    tmp_path, monkeypatch, workflow_writer
):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    module = _module()
    package = load_workflow(workflow_writer(tmp_path / "package", name="long-poll"))
    store = RunStore(home)
    run = _start(store, package, "long-poll")

    def append_event():
        time.sleep(0.05)
        store.append_event(run.run_id, "progress", {"step": 1})

    thread = threading.Thread(target=append_event)
    thread.start()
    response = TestClient(_app(module.router)).get(
        f"/api/plugins/workflow/runs/{run.run_id}/events?after=1&wait_seconds=1"
    )
    thread.join()

    assert response.status_code == 200
    assert response.json()["events"][0]["sequence"] == 2


def test_attention_includes_real_workflow_approval_interactions(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    started = run_showcase(
        "laptop-diagnostic", hermes_home=home, symptom="fictional attention"
    )
    client = TestClient(_app(_router()))
    response = client.get("/api/plugins/workflow/attention")

    assert response.status_code == 200
    item = next(
        item for item in response.json()["items"] if item["run_id"] == started["run_id"]
    )
    assert item["interaction"]["type"] == "workflow_approval"
    detail = client.get(f"/api/plugins/workflow/runs/{started['run_id']}").json()
    assert (
        detail["pending_interaction"]["interaction_id"]
        == item["interaction"]["interaction_id"]
    )
    assert detail["pending_interaction"]["node_id"] == item["node_id"]
    assert detail["next_actions"] == [
        "status",
        "events",
        "approve",
        "reject",
        "cancel",
    ]


def test_forged_scope_header_cannot_expand_verified_session(
    tmp_path, monkeypatch, workflow_writer
):
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(workflow_writer(tmp_path / "package", name="verified"))
    store = RunStore(home)
    scope = "dashboard:test:org:user-1"
    run = _start(store, package, "verified", scope=scope)
    session = SimpleNamespace(provider="test", org_id="org", user_id="user-1")
    client = TestClient(_app(_router(), session=session))

    allowed = client.get(f"/api/plugins/workflow/runs/{run.run_id}")
    forged = client.get(
        f"/api/plugins/workflow/runs/{run.run_id}",
        headers={"X-Hermes-Operator-Scope": "dashboard:test:org:user-2"},
    )

    assert allowed.status_code == 200
    assert forged.status_code == 403
    assert forged.json()["detail"]["code"] == "operator_scope_not_authorized"


def test_seventeenth_event_wait_is_refused_while_status_remains_responsive(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    package = load_workflow(workflow_writer(tmp_path / "package", name="capacity"))
    store = RunStore(home)
    run = _start(store, package, "capacity")
    module = _module()
    app = _app(module.router)

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            path = (
                f"/api/plugins/workflow/runs/{run.run_id}/events"
                "?after=1&wait_seconds=0.2"
            )
            waits = [asyncio.create_task(client.get(path)) for _ in range(17)]
            await asyncio.sleep(0.03)
            status = await client.get(f"/api/plugins/workflow/runs/{run.run_id}")
            responses = await asyncio.gather(*waits)
            return status, responses

    status, responses = asyncio.run(exercise())

    assert status.status_code == 200
    assert sum(response.status_code == 429 for response in responses) == 1
    refused = next(response for response in responses if response.status_code == 429)
    assert refused.json()["detail"]["code"] == "event_wait_capacity"


def test_repeated_api_reads_initialize_one_store_for_the_profile(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    module = _module()
    runtime = WorkflowApiRuntime(WorkflowApiLimits())
    created = []

    def factory(path):
        created.append(path)
        return RunStore(path)

    runtime.stores = WorkflowStoreRegistry(max_profiles=8, store_factory=factory)
    module._RUNTIME = runtime
    client = TestClient(_app(module.router))

    assert client.get("/api/plugins/workflow/runs").status_code == 200
    assert client.get("/api/plugins/workflow/runs").status_code == 200

    assert created == [home.resolve()]
    assert runtime.stores.snapshot()["profiles"] == 1
    runtime.close()
