from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore
import plugins.workflow.showcase as showcase_module
from plugins.workflow.trust import WorkflowTrustStore


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | str | None]]:
    if not root.exists():
        return {}
    snapshot: dict[str, tuple[str, bytes | str | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", str(path.readlink()))
        elif path.is_dir():
            snapshot[relative] = ("directory", None)
        else:
            snapshot[relative] = ("file", path.read_bytes())
    return snapshot


def test_bundled_showcase_catalog_detail_and_admission_cross_real_middleware(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    showcase_module._clear_verified_showcase_cache_for_tests()
    store = RunStore(home)
    acquired = CoordinatorStore(store.database).try_acquire(
        CoordinatorIdentity(
            owner_id="showcase-desktop-e2e",
            host_kind="web",
            host_instance_id="showcase-desktop-e2e",
            pid=1,
            process_start_time=None,
        ),
        now=datetime.now(timezone.utc),
        lease_seconds=60,
    )
    assert acquired.is_leader
    trust_store = WorkflowTrustStore(home)
    trust_store.trust("a" * 64, actor="existing-operator", risk_digest="b" * 64)

    def forbidden_advance(*_args, **_kwargs):
        raise AssertionError("Desktop REST showcase admission executed a workflow node")

    monkeypatch.setattr(RunScheduler, "advance", forbidden_advance)

    from hermes_cli import web_server

    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    mounted_route = next(
        route
        for route in web_server.app.routes
        if getattr(route, "path", None) == "/api/plugins/workflow/workflows"
    )
    mounted_plugin = sys.modules[mounted_route.endpoint.__module__]
    mounted_plugin._close_runtime()
    mounted_plugin._runtime()
    client = TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    )
    try:
        store_before_reads = _tree_snapshot(home / "workflows")
        trust_before_reads = trust_store.path.read_bytes()

        catalog_response = client.get("/api/plugins/workflow/workflows")
        assert catalog_response.status_code == 200
        row = next(
            item
            for item in catalog_response.json()["items"]
            if item.get("source") == "showcase"
            and item.get("name") == "approval-gate"
        )
        assert row["trust_state"] == "verified_bundled"
        assert row["run_support"] == {"supported": True, "reason": "supported"}

        detail_response = client.get(
            "/api/plugins/workflow/workflows/approval-gate",
            params={"catalog_source": "showcase"},
        )
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["topology"]["mermaid"]
        assert detail["definition"]["nodes"][0]["value"] == "[REDACTED]"
        assert _tree_snapshot(home / "workflows") == store_before_reads
        assert trust_store.path.read_bytes() == trust_before_reads

        admitted_response = client.post(
            "/api/plugins/workflow/runs",
            json={
                "workflow": "approval-gate",
                "catalog_source": "showcase",
                "values": {},
                "idempotency_key": str(uuid4()),
                "concurrency_policy": "queue",
            },
        )
        assert admitted_response.status_code == 202
        admitted = admitted_response.json()["result"]
        assert admitted["admission_disposition"] == "created"

        run = store.get_run_status(admitted["run_id"])
        assert run["status"] in {"queued", "running"}
        assert run["execution_mode"] == "background"
        assert run["trigger"] == "desktop"
        assert run["provenance"]["source"] == "desktop"
        assert run["provenance"]["assurance"] == "local_admin_claim"
        assert run["run_metadata"]["showcase_id"] == "approval-gate"
        assert run["run_metadata"]["showcase_provenance"] == "verified_bundled"
        assert len(run["run_metadata"]["bundle_digest"]) == 64
        assert len(run["run_metadata"]["risk_digest"]) == 64
        assert run["nodes"]["operator-approval"]["state"] == "ready"
        assert _tree_snapshot(home / "workflows") != store_before_reads
        assert trust_store.path.read_bytes() == trust_before_reads

        board_response = client.get("/api/plugins/workflow/runs?view=board")
        assert board_response.status_code == 200
        assert admitted["run_id"] in {
            item["run_id"] for item in board_response.json()["runs"]
        }
    finally:
        client.close()
        mounted_plugin._close_runtime()
        assert mounted_plugin._RUNTIME is None
