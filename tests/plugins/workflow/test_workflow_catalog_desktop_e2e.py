from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from plugins.workflow.compat import assess_compatibility
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.schema import load_workflow
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore
from plugins.workflow.trust import (
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
)


def test_desktop_catalog_detail_and_trigger_cross_real_auth_boundary(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_path = workflow_writer(
        home / "workflows",
        name="desktop-catalog-e2e",
        filename="desktop-catalog-e2e.yaml",
        nodes=[{"id": "still-pending", "bash": "sleep 30"}],
    )
    workflow_writer(
        home / "workflows",
        name="bounded-findings-e2e",
        filename="bounded-findings-e2e.yaml",
        nodes=[
            {
                "id": "agent",
                "prompt": "bounded",
                "allowed_tools": ["UnknownTool"],
            }
        ],
        **{f"future_{index:04d}": "x" * 32 for index in range(600)},
    )
    (home / "workflows" / "broken-neighbor.yaml").write_text(
        "name: broken-neighbor\nnodes: [invalid\n", encoding="utf-8"
    )
    workflow_writer(home / "workflows" / "duplicate-a", name="duplicate-neighbor")
    workflow_writer(home / "workflows" / "duplicate-b", name="duplicate-neighbor")
    (home / "workflows" / "oversized-neighbor.yaml").write_bytes(
        b" " * (2 * 1024 * 1024 + 1)
    )
    package = load_workflow(workflow_path)
    package_digest = compute_package_digest(package)
    risk = build_risk_summary(package, assess_compatibility(package))
    WorkflowTrustStore(home).trust(
        package_digest.sha256,
        actor="desktop-e2e",
        risk_digest=risk.risk_digest,
    )
    store = RunStore(home)
    acquired = CoordinatorStore(store.database).try_acquire(
        CoordinatorIdentity(
            owner_id="desktop-e2e",
            host_kind="web",
            host_instance_id="desktop-e2e",
            pid=1,
            process_start_time=None,
        ),
        now=datetime.now(timezone.utc),
        lease_seconds=60,
    )
    assert acquired.is_leader

    def forbidden_advance(*_args, **_kwargs):
        raise AssertionError("Desktop REST admission must not execute workflow nodes")

    monkeypatch.setattr(RunScheduler, "advance", forbidden_advance)

    from hermes_cli import web_server

    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    with TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    ) as client:
        catalog_response = client.get("/api/plugins/workflow/workflows")
        assert catalog_response.status_code == 200
        catalog_items = catalog_response.json()["items"]
        user_items = [
            item for item in catalog_items if item.get("source") != "showcase"
        ]
        assert [item["name"] for item in user_items] == [
            "bounded-findings-e2e",
            "broken-neighbor",
            "desktop-catalog-e2e",
            "duplicate-neighbor",
            "oversized-neighbor",
        ]
        assert user_items[1] == {
            "name": "broken-neighbor",
            "error": "invalid_definition",
        }
        assert user_items[3] == {
            "name": "duplicate-neighbor",
            "error": "invalid_definition",
        }
        assert user_items[4] == {
            "name": "oversized-neighbor",
            "error": "catalog_capacity",
        }
        approval_showcase = next(
            item
            for item in catalog_items
            if item.get("source") == "showcase" and item.get("name") == "approval-gate"
        )
        assert approval_showcase["trust_state"] == "verified_bundled"

        detail_response = client.get(
            "/api/plugins/workflow/workflows/desktop-catalog-e2e"
        )
        assert detail_response.status_code == 200
        topology = detail_response.json()["topology"]
        assert topology["text"]
        assert topology["mermaid"]

        bounded_response = client.get(
            "/api/plugins/workflow/workflows/bounded-findings-e2e"
        )
        assert bounded_response.status_code == 200
        assert len(bounded_response.content) < 1024 * 1024
        bounded = bounded_response.json()["compatibility"]
        assert bounded["level"] == "unsupported"
        assert bounded["runnable"] is False
        assert bounded["findings_truncated"] is True
        assert bounded["finding_count"] == 602
        assert len(bounded["findings"]) == 512
        assert bounded["findings"][-1]["code"] == "compatibility_findings_truncated"

        for invalid_name, expected_status, expected_code, retryable in (
            ("broken-neighbor", 422, "workflow_invalid_definition", False),
            ("duplicate-neighbor", 422, "workflow_invalid_definition", False),
            ("oversized-neighbor", 503, "workflow_catalog_capacity", True),
        ):
            rejected_response = client.post(
                "/api/plugins/workflow/runs",
                json={
                    "workflow": invalid_name,
                    "values": {},
                    "idempotency_key": str(uuid4()),
                    "concurrency_policy": "queue",
                },
            )
            assert rejected_response.status_code == expected_status
            assert rejected_response.json() == {
                "detail": {"code": expected_code, "retryable": retryable}
            }

        idempotency_key = str(uuid4())
        admitted_response = client.post(
            "/api/plugins/workflow/runs",
            json={
                "workflow": "desktop-catalog-e2e",
                "values": {},
                "idempotency_key": idempotency_key,
                "concurrency_policy": "queue",
            },
        )
        assert admitted_response.status_code == 202
        admitted = admitted_response.json()["result"]
        assert admitted["admission_disposition"] == "created"

        run = store.get_run_status(admitted["run_id"])
        assert run["status"] in {"queued", "running"}
        assert run["trigger"] == "desktop"
        assert run["provenance"]["source"] == "desktop"
        assert run["provenance"]["assurance"] == "local_admin_claim"
        assert run["nodes"]["still-pending"]["state"] == "ready"

        board_response = client.get("/api/plugins/workflow/runs?view=board")
        assert board_response.status_code == 200
        assert admitted["run_id"] in {
            item["run_id"] for item in board_response.json()["runs"]
        }
