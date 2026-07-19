from plugins.workflow.admission import RunAdmissionRequest
import pytest

from plugins.workflow.schema import load_workflow
from plugins.workflow.store import RunStore
from fastapi.testclient import TestClient


def test_explicit_run_id_does_not_bypass_operator_scope(tmp_path):
    store = RunStore(tmp_path)
    package_root = tmp_path / "package"
    package_root.mkdir()
    workflow_path = package_root / "demo.yaml"
    workflow_path.write_text(
        "version: '1'\nname: demo\ndescription: Scope test\nnodes:\n  - id: start\n    bash: echo ok\n",
        encoding="utf-8",
    )
    package = load_workflow(workflow_path)
    prepared = store.prepare_run_snapshot(package)
    result = store.start_run(
        RunAdmissionRequest(
            workflow_name="demo",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="chat",
            idempotency_key="message-1",
            concurrency_key="demo",
            operator_scope="profile:alice:conversation:one",
        ),
        immutable_snapshot=prepared,
    )
    assert result.run_id
    assert store.get_run_status(
        result.run_id, operator_scope="profile:alice:conversation:one"
    )
    with pytest.raises(KeyError):
        store.get_run_status(
            result.run_id, operator_scope="profile:alice:conversation:two"
        )


def test_real_dashboard_auth_boundary_marks_only_verified_local_admin_requests(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    from hermes_cli import web_server

    web_server.app.state.auth_required = False
    client = TestClient(web_server.app)

    denied = client.get("/api/plugins/workflow/runs")
    allowed = client.get(
        "/api/plugins/workflow/runs",
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    )

    assert denied.status_code == 401
    assert allowed.status_code == 200
