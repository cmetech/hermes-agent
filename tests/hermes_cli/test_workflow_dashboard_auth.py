from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import yaml

from hermes_cli.dashboard_auth import (
    DashboardAuthProvider,
    LoginStart,
    Session,
    TokenPrincipal,
    clear_providers,
    register_provider,
)
from hermes_cli.dashboard_auth import token_auth
from plugins.workflow.actions import MUTATION_ACTIONS
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore


class _WorkflowTokenProvider(DashboardAuthProvider):
    name = "workflow-token"
    display_name = "Workflow token test provider"
    supports_session = False
    supports_token = True

    def __init__(self, principals: dict[str, TokenPrincipal]) -> None:
        self._principals = principals

    def start_login(self, *, redirect_uri: str) -> LoginStart:
        raise NotImplementedError

    def complete_login(
        self,
        *,
        code: str,
        state: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> Session:
        raise NotImplementedError

    def verify_session(self, *, access_token: str) -> Optional[Session]:
        return None

    def refresh_session(self, *, refresh_token: str) -> Session:
        raise NotImplementedError

    def revoke_session(self, *, refresh_token: str) -> None:
        return None

    def verify_token(self, *, token: str) -> Optional[TokenPrincipal]:
        return self._principals.get(token)


@pytest.fixture(autouse=True)
def _reset_dashboard_token_auth():
    clear_providers()
    token_auth.clear_token_routes()
    yield
    clear_providers()
    token_auth.clear_token_routes()


def _workflow_router():
    path = Path(__file__).parents[2] / "plugins/workflow/dashboard/plugin_api.py"
    spec = importlib.util.spec_from_file_location("workflow_real_auth_api_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.router


def _start_scoped_run(home: Path, *, scope: str, key: str):
    package_root = home.parent / key
    package_root.mkdir(parents=True)
    workflow_path = package_root / "workflow.yaml"
    workflow_path.write_text(
        yaml.safe_dump(
            {
                "name": key,
                "description": "Workflow authorization fixture",
                "nodes": [{"id": "start", "bash": "true"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    package = load_workflow(workflow_path)
    store = RunStore(home)
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


def _real_token_app(*, paths: set[str], principals: dict[str, TokenPrincipal]):
    from hermes_cli import web_server

    register_provider(_WorkflowTokenProvider(principals))
    for path in paths:
        token_auth.register_token_route(path)
    app = FastAPI()
    app.state.auth_required = False
    app.middleware("http")(web_server.auth_middleware)
    app.middleware("http")(token_auth.token_auth_middleware)
    app.include_router(_workflow_router(), prefix="/api/plugins/workflow")
    return app


def test_real_dashboard_token_middleware_is_workflow_identity_boundary(
    tmp_path, monkeypatch
):
    from hermes_cli import web_server

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "exact-dashboard-token")
    app = FastAPI()
    app.state.auth_required = False
    app.middleware("http")(web_server.auth_middleware)
    app.include_router(_workflow_router(), prefix="/api/plugins/workflow")
    client = TestClient(app)

    denied = client.get("/api/plugins/workflow/runs")
    allowed = client.get(
        "/api/plugins/workflow/runs",
        headers={"Authorization": "Bearer exact-dashboard-token"},
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["runs"] == []


def test_read_token_cannot_mutate_any_run_action(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    scope = "service:workflow-token:reader"
    admitted = _start_scoped_run(home, scope=scope, key="read-token-run")
    paths = {
        f"/api/plugins/workflow/runs/{admitted.run_id}/{action}"
        for action in MUTATION_ACTIONS
    }
    app = _real_token_app(
        paths=paths,
        principals={
            "read-secret": TokenPrincipal(
                principal="reader",
                provider="workflow-token",
                scopes=("workflow:read",),
            )
        },
    )
    client = TestClient(app)

    for action in sorted(MUTATION_ACTIONS):
        response = client.post(
            f"/api/plugins/workflow/runs/{admitted.run_id}/{action}",
            headers={"Authorization": "Bearer read-secret"},
            json={"expected_version": 0},
        )
        assert response.status_code == 403, action
        assert response.json()["detail"]["code"] == "workflow_write_required"


def test_write_token_cannot_execute_cleanup(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = "/api/plugins/workflow/cleanup/preview"
    app = _real_token_app(
        paths={path},
        principals={
            "write-secret": TokenPrincipal(
                principal="writer",
                provider="workflow-token",
                scopes=("workflow:write",),
            )
        },
    )

    response = TestClient(app).get(
        f"{path}?older_than=0d",
        headers={"Authorization": "Bearer write-secret"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "workflow_admin_required"


def test_post_runs_real_middleware_requires_workflow_write_scope(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    path = "/api/plugins/workflow/runs"
    app = _real_token_app(
        paths={path},
        principals={
            "read-secret": TokenPrincipal(
                principal="reader",
                provider="workflow-token",
                scopes=("workflow:read",),
            ),
            "write-secret": TokenPrincipal(
                principal="writer",
                provider="workflow-token",
                scopes=("workflow:write",),
            ),
        },
    )
    body = {
        "workflow": "missing-catalog-workflow",
        "values": {},
        "idempotency_key": "real-middleware",
        "concurrency_policy": "queue",
    }

    denied = TestClient(app).post(
        path,
        headers={"Authorization": "Bearer read-secret"},
        json=body,
    )
    allowed_to_resolve = TestClient(app).post(
        path,
        headers={"Authorization": "Bearer write-secret"},
        json=body,
    )

    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "workflow_write_required"
    assert allowed_to_resolve.status_code == 404
    assert allowed_to_resolve.json()["detail"]["code"] == "workflow_not_found"
