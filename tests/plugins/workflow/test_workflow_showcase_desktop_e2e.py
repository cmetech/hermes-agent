from __future__ import annotations

from contextlib import contextmanager
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import yaml

from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore
import plugins.workflow.showcase as showcase_module
from plugins.workflow.trust import WorkflowTrustStore


@contextmanager
def _test_bundle_path(root: Path):
    yield root.resolve()


def _restamp_showcase_package(root: Path, showcase_id: str) -> None:
    manifest_path = root / "digests.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["packages"][showcase_id] = showcase_module._tree_digest(
        root / "packages" / showcase_id
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
    copied = tmp_path / "showcases"
    shutil.copytree(Path(showcase_module.__file__).with_name("showcases"), copied)
    definition_path = (
        copied / "packages" / "approval-gate" / "workflows" / "approval-gate.yaml"
    )
    definition = yaml.safe_load(definition_path.read_text(encoding="utf-8"))
    definition.update({f"future_{index:04d}": "x" * 32 for index in range(600)})
    definition_path.write_text(
        yaml.safe_dump(definition, sort_keys=False), encoding="utf-8"
    )
    _restamp_showcase_package(copied, "approval-gate")
    showcase_module._clear_verified_showcase_cache_for_tests()
    monkeypatch.setattr(
        showcase_module,
        "_bundle_path",
        lambda explicit=None: _test_bundle_path(copied),
    )
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
    with TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    ) as client:
        store_before_reads = _tree_snapshot(home / "workflows")
        trust_before_reads = trust_store.path.read_bytes()

        catalog_response = client.get("/api/plugins/workflow/workflows")
        assert catalog_response.status_code == 200
        row = next(
            item
            for item in catalog_response.json()["items"]
            if item.get("source") == "showcase" and item.get("name") == "approval-gate"
        )
        assert row["trust_state"] == "verified_bundled"
        assert row["run_support"] == {"supported": True, "reason": "supported"}
        compatibility = row["compatibility"]
        assert compatibility["level"] == "unsupported"
        assert compatibility["runnable"] is True
        assert compatibility["findings_truncated"] is True
        assert compatibility["finding_count"] == 601
        assert len(compatibility["findings"]) == 512
        assert compatibility["findings"][-1]["code"] == (
            "compatibility_findings_truncated"
        )

        detail_response = client.get(
            "/api/plugins/workflow/workflows/approval-gate",
            params={"catalog_source": "showcase"},
        )
        assert detail_response.status_code == 200
        detail = detail_response.json()
        detail_compatibility = detail["compatibility"]
        for field in (
            "level",
            "runnable",
            "findings_truncated",
            "finding_count",
        ):
            assert detail_compatibility[field] == compatibility[field]
        catalog_findings = compatibility["findings"]
        detail_findings = detail_compatibility["findings"]
        assert len(catalog_findings) == len(detail_findings) == 512
        assert all("migration" not in finding for finding in catalog_findings)
        assert [
            {key: value for key, value in finding.items() if key != "migration"}
            for finding in detail_findings
        ] == catalog_findings
        assert detail_findings[0]["code"] == "legacy_language_profile"
        assert detail_findings[0]["migration"] == (
            'Run "hermes workflow doctor" and resolve compatibility code '
            '"legacy_language_profile" before relying on this field in the '
            "selected profile."
        )
        assert detail_findings[-1]["code"] == "compatibility_findings_truncated"
        assert "migration" not in detail_findings[-1]
        assert len(detail_response.content) < 1024 * 1024
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

        resilience_response = client.post(
            "/api/plugins/workflow/runs",
            json={
                "workflow": "resilience",
                "catalog_source": "showcase",
                "values": {},
                "idempotency_key": str(uuid4()),
                "concurrency_policy": "queue",
            },
        )
        assert resilience_response.status_code == 202
        resilience = resilience_response.json()["result"]
        resilience_run = store.get_run_status(resilience["run_id"])
        assert resilience_run["execution_mode"] == "background"
        scheduler = RunScheduler(store)
        try:
            approval_limits = scheduler._run_execution_limits(
                scheduler._load_run_package(admitted["run_id"])
            )
            resilience_limits = scheduler._run_execution_limits(
                scheduler._load_run_package(resilience["run_id"])
            )
        finally:
            scheduler.shutdown()
        assert approval_limits.max_parallel_nodes == 1
        assert approval_limits.max_total_workers == 1
        assert approval_limits.subprocess_timeout_seconds == 30
        assert approval_limits.max_descendants == 1
        assert resilience_limits.max_parallel_nodes == 2
        assert resilience_limits.max_total_workers == 2
        assert resilience_limits.subprocess_timeout_seconds == 120
        assert resilience_limits.max_descendants == 8
        assert _tree_snapshot(home / "workflows") != store_before_reads
        assert trust_store.path.read_bytes() == trust_before_reads

        board_response = client.get("/api/plugins/workflow/runs?view=board")
        assert board_response.status_code == 200
        assert admitted["run_id"] in {
            item["run_id"] for item in board_response.json()["runs"]
        }


def test_ai_showcase_desktop_projection_keeps_mcp_and_skills_on_ai_nodes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import web_server

    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    with TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    ) as client:
        response = client.get(
            "/api/plugins/workflow/workflows/ai-extensions",
            params={"catalog_source": "showcase"},
        )

    assert response.status_code == 200
    nodes = response.json()["definition"]["nodes"]
    assert nodes
    mcp_nodes = [node for node in nodes if "mcp" in node["options"]]
    skill_nodes = [node for node in nodes if "skills" in node["options"]]
    extension_nodes = [*mcp_nodes, *skill_nodes]
    assert mcp_nodes
    assert skill_nodes
    assert all(node["type"] in {"command", "prompt"} for node in extension_nodes)
    assert all(
        isinstance(node["options"]["mcp"], str) and node["options"]["mcp"]
        for node in mcp_nodes
    )
    assert all(
        isinstance(node["options"]["skills"], list)
        and node["options"]["skills"]
        and all(isinstance(skill, str) and skill for skill in node["options"]["skills"])
        for node in skill_nodes
    )
    assert {"mcp", "skills"}.isdisjoint(node["type"] for node in nodes)
