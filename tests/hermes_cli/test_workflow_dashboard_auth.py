from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _workflow_router():
    path = Path(__file__).parents[2] / "plugins/workflow/dashboard/plugin_api.py"
    spec = importlib.util.spec_from_file_location("workflow_real_auth_api_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.router


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
