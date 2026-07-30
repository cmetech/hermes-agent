from __future__ import annotations

from pathlib import Path
import threading

from fastapi.testclient import TestClient

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


def _workflow_store_io_workers() -> list[str]:
    return sorted(
        thread.name
        for thread in threading.enumerate()
        if thread.name.startswith("workflow-store-io")
    )


def test_testclient_lifespan_closes_workflow_store_workers(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))

    from hermes_cli import web_server

    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    assert _workflow_store_io_workers() == []
    with TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    ) as client:
        response = client.get("/api/plugins/workflow/runs/not-a-real-run/events")
        assert response.status_code == 404
        assert _workflow_store_io_workers()
    assert _workflow_store_io_workers() == []


def test_language_status_crosses_real_desktop_middleware_without_mutation(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "project"
    workdir.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.chdir(workdir)

    archon = workflow_writer(
        workdir / ".hermes" / "workflows",
        name="archon-deferred",
        filename="archon-deferred.yaml",
        nodes=[{"id": "start", "bash": "true", "timeout": 5}],
    )
    archon.with_name("archon-deferred.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    workflow_writer(
        home / "workflows",
        name="legacy-preserved",
        filename="legacy-preserved.yaml",
    )
    WorkflowTrustStore(home).trust(
        "a" * 64,
        actor="language-desktop-e2e",
        risk_digest="b" * 64,
    )

    from hermes_cli import web_server

    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    with TestClient(
        web_server.app,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    ) as client:
        before_home = _tree_snapshot(home)
        before_project = _tree_snapshot(workdir)
        catalog_response = client.get("/api/plugins/workflow/workflows")
        assert catalog_response.status_code == 200
        rows = {
            (item.get("source"), item.get("name")): item
            for item in catalog_response.json()["items"]
        }
        archon_row = rows[("project", "archon-deferred")]
        legacy_row = rows[("profile", "legacy-preserved")]

        assert archon_row["language"] == {
            "effective_profile": "archon-2026-07",
            "legacy": False,
        }
        assert archon_row["compatibility"] == {
            "level": "unsupported",
            "runnable": False,
        }
        assert legacy_row["language"] == {
            "effective_profile": "hermes-legacy",
            "legacy": True,
        }
        assert legacy_row["compatibility"] == {
            "level": "mapped",
            "runnable": True,
        }
        assert {
            key: archon_row[key]
            for key in (
                "source",
                "precedence",
                "trust_state",
                "inputs",
                "supported_inputs",
                "run_support",
            )
        } == {
            "source": "project",
            "precedence": 1,
            "trust_state": "untrusted",
            "inputs": [],
            "supported_inputs": {"supported": True, "reason": "parameterless"},
            "run_support": {"supported": True, "reason": "supported"},
        }

        detail_response = client.get(
            "/api/plugins/workflow/workflows/archon-deferred",
            params={"catalog_source": "project"},
        )
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["language"]["declared_profile"] == "archon-2026-07"
        assert detail["language"]["effective_profile"] == "archon-2026-07"
        assert detail["language"]["legacy"] is False
        assert detail["language"]["normalizer_version"] == 1
        assert len(detail["language"]["normalized_definition_digest"]) == 64
        assert set(detail["language"]) == {
            "declared_profile",
            "effective_profile",
            "legacy",
            "normalizer_version",
            "normalized_definition_digest",
        }
        assert detail["compatibility"]["runnable"] is False
        assert any(
            finding["blocking"]
            for finding in detail["compatibility"]["findings"]
        )
        assert "authoring_schema" not in detail
        assert _tree_snapshot(home) == before_home
        assert _tree_snapshot(workdir) == before_project
