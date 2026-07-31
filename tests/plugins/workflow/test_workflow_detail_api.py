from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
import sqlite3
import shutil
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hermes_cli.dashboard_auth.base import TokenPrincipal
import pytest
import yaml

from plugins.workflow.cli import register_cli
from plugins.workflow.compat import CompatibilityLevel, assess_compatibility
from plugins.workflow.catalog_api import build_workflow_detail
import plugins.workflow.coordinator_store as coordinator_store_module
from plugins.workflow.coordinator_store import (
    CoordinatorIdentity,
    CoordinatorStore,
    install_coordinator_schema,
)
from plugins.workflow.projection_limits import WORKFLOW_DEFINITION_MAX_NODES
from plugins.workflow.schema import load_workflow
import plugins.workflow.showcase as showcase_module
from plugins.workflow.topology import sanitize_topology_label
from plugins.workflow.trust import (
    WorkflowTrustStore,
    build_risk_summary,
    compute_package_digest,
)


def _module():
    path = Path(__file__).parents[3] / "plugins/workflow/dashboard/plugin_api.py"
    spec = importlib.util.spec_from_file_location("workflow_detail_api_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _app(router, *, token=None):
    app = FastAPI()

    @app.middleware("http")
    async def authenticated(request, call_next):
        if token is not None:
            request.state.token_principal = token
            request.state.token_authenticated = True
        return await call_next(request)

    app.include_router(router, prefix="/api/plugins/workflow")
    return app


def _reader() -> TokenPrincipal:
    return TokenPrincipal(
        principal="reader", provider="test", scopes=("workflow:read",)
    )


def _detail_get(
    router,
    name: str,
    *,
    token=None,
    catalog_source: str | None = None,
    raise_server_exceptions=True,
):
    return TestClient(
        _app(router, token=token),
        raise_server_exceptions=raise_server_exceptions,
    ).get(
        f"/api/plugins/workflow/workflows/{name}",
        params={"catalog_source": catalog_source} if catalog_source else None,
    )


@contextmanager
def _test_bundle_path(root: Path):
    yield root.resolve()


def _cli_show_json(
    *, workdir: Path, home: Path, name: str, capsys
) -> dict[str, object]:
    parser = argparse.ArgumentParser()
    register_cli(parser)
    args = parser.parse_args([
        "--workdir",
        str(workdir),
        "--hermes-home",
        str(home),
        "show",
        name,
        "--json",
    ])
    assert args.topology is None
    assert args.func(args) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is True
    return envelope["result"]


def _trust(home: Path, workflow_path: Path) -> None:
    package = load_workflow(workflow_path, source="profile", precedence=2)
    risk = build_risk_summary(package, assess_compatibility(package))
    WorkflowTrustStore(home).trust(
        compute_package_digest(package).sha256,
        actor="detail-test",
        risk_digest=risk.risk_digest,
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


def test_workflow_detail_requires_verified_authentication() -> None:
    response = _detail_get(_module().router, "sample")

    assert response.status_code == 401
    assert response.json() == {"detail": {"code": "authentication_required"}}


def test_workflow_detail_requires_read_capability() -> None:
    token = TokenPrincipal(principal="none", provider="test", scopes=())

    response = _detail_get(_module().router, "sample", token=token)

    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "workflow_read_required"}}


def test_workflow_detail_unknown_name_is_typed_not_found(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))

    response = _detail_get(_module().router, "missing", token=_reader())

    assert response.status_code == 404
    assert response.json() == {"detail": {"code": "workflow_not_found"}}


def test_workflow_detail_source_disambiguates_user_and_verified_showcase(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "project"
    workdir.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.chdir(workdir)
    workflow_writer(
        workdir / ".hermes" / "workflows",
        name="approval-gate",
        description="User-authored collision",
    )
    showcase_module._clear_verified_showcase_cache_for_tests()
    router = _module().router

    catalog = TestClient(_app(router, token=_reader())).get(
        "/api/plugins/workflow/workflows"
    )

    bare = _detail_get(router, "approval-gate", token=_reader())
    selected_user = _detail_get(
        router, "approval-gate", token=_reader(), catalog_source="project"
    )
    selected_showcase = _detail_get(
        router, "approval-gate", token=_reader(), catalog_source="showcase"
    )

    assert bare.status_code == selected_user.status_code == 200
    assert catalog.status_code == 200
    assert sum(
        item.get("name") == "approval-gate"
        and item.get("source") in {"project", "showcase"}
        for item in catalog.json()["items"]
    ) == 2
    assert bare.json()["source"] == selected_user.json()["source"] == "project"
    assert bare.json()["description"] == "User-authored collision"
    assert selected_showcase.status_code == 200
    assert selected_showcase.json()["source"] == "showcase"
    assert selected_showcase.json()["trust_state"] == "verified_bundled"
    assert selected_showcase.json()["run_support"] == {
        "supported": True,
        "reason": "supported",
    }


def test_catalog_and_detail_models_accept_all_authoritative_source_projections(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "project"
    workdir.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.chdir(workdir)
    workflow_writer(
        workdir / ".hermes" / "workflows",
        name="project-model",
        filename="project-model.yaml",
    )
    workflow_writer(
        home / "workflows",
        name="profile-model",
        filename="profile-model.yaml",
    )
    showcase_module._clear_verified_showcase_cache_for_tests()
    module = _module()
    router = module.router

    catalog = TestClient(_app(router, token=_reader())).get(
        "/api/plugins/workflow/workflows"
    )

    assert catalog.status_code == 200
    rows = [item for item in catalog.json()["items"] if "source" in item]
    assert {item["source"] for item in rows} == {"project", "profile", "showcase"}
    for row in rows:
        module.WorkflowCatalogEntry.model_validate(row)

    for name, source in (
        ("project-model", "project"),
        ("profile-model", "profile"),
        ("approval-gate", "showcase"),
    ):
        response = _detail_get(
            router,
            name,
            token=_reader(),
            catalog_source=source,
        )
        assert response.status_code == 200
        module.WorkflowDetailResponse.model_validate(response.json())


def test_workflow_detail_model_accepts_real_maximum_node_compatibility_report(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = workflow_writer(
        home / "workflows",
        name="maximum-language-shape",
        filename="maximum-language-shape.yaml",
        nodes=[
            {
                "id": f"node-{index:03d}",
                "bash": "true",
                "timeout": 1,
                "retry": {"max_attempts": 1},
                "output_type": "text",
                "maxBudgetUsd": 1,
                "sandbox": {"enabled": True},
            }
            for index in range(WORKFLOW_DEFINITION_MAX_NODES)
        ],
    )
    path.with_name("maximum-language-shape.hermes.yaml").write_text(
        yaml.safe_dump({"language_compatibility": "archon-2026-07"}),
        encoding="utf-8",
    )
    module = _module()

    detail = build_workflow_detail(
        "maximum-language-shape",
        hermes_home=home,
        workdir=tmp_path,
        catalog_source="profile",
    )
    findings = detail["compatibility"]["findings"]

    assert len(findings) > 200
    assert len(findings) <= module.WORKFLOW_COMPATIBILITY_FINDINGS_MAX
    module.WorkflowDetailResponse.model_validate(detail)

    response = _detail_get(
        module.router,
        "maximum-language-shape",
        token=_reader(),
        catalog_source="profile",
    )
    assert response.status_code == 200
    assert len(response.json()["compatibility"]["findings"]) == len(findings)


def test_workflow_compatibility_models_accept_real_producer_state_variants(
    tmp_path, workflow_writer
) -> None:
    module = _module()
    cases = [
        (
            "portable",
            {"language_compatibility": "archon-2026-07"},
            {},
            CompatibilityLevel.PORTABLE,
            True,
        ),
        ("mapped", None, {}, CompatibilityLevel.MAPPED, True),
        (
            "blocking",
            {"language_compatibility": "archon-2026-07"},
            {"nodes": [{"id": "start", "bash": "true", "timeout": 1}]},
            CompatibilityLevel.UNSUPPORTED,
            False,
        ),
        (
            "nonblocking-unsupported",
            None,
            {"legacy_extension": True},
            CompatibilityLevel.UNSUPPORTED,
            True,
        ),
    ]

    for name, sidecar, options, expected_level, expected_runnable in cases:
        path = workflow_writer(tmp_path / name, name=name, **options)
        if sidecar is not None:
            path.with_name(f"{path.stem}.hermes.yaml").write_text(
                yaml.safe_dump(sidecar), encoding="utf-8"
            )
        report = assess_compatibility(load_workflow(path))
        projection = {
            "level": report.level.value,
            "runnable": report.runnable,
            "findings": [
                {
                    "path": finding.path,
                    "level": finding.level.value,
                    "message": finding.message,
                    "blocking": finding.blocking,
                    "code": finding.code,
                }
                for finding in report.findings
            ],
        }

        assert report.level is expected_level
        assert report.runnable is expected_runnable
        module.WorkflowCompatibilityFull.model_validate(projection)


@pytest.mark.parametrize(
    ("case_name", "unsafe_key"),
    (
        ("empty", ""),
        ("root", "/"),
        ("dot", "."),
        ("ansi-only", "\x1b[31m"),
    ),
)
def test_workflow_detail_normalizes_sanitizer_empty_compatibility_paths(
    tmp_path, monkeypatch, case_name, unsafe_key
) -> None:
    home = tmp_path / "home"
    workflow_root = home / "workflows"
    workflow_root.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = workflow_root / f"unsafe-path-{case_name}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": f"unsafe-path-{case_name}",
                "description": "Compatibility path sanitizer regression",
                "nodes": [{"id": "start", "bash": "true"}],
                unsafe_key: True,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    module = _module()

    response = _detail_get(
        module.router,
        f"unsafe-path-{case_name}",
        token=_reader(),
        catalog_source="profile",
        raise_server_exceptions=False,
    )

    assert response.status_code == 200
    payload = response.json()
    finding = next(
        item
        for item in payload["compatibility"]["findings"]
        if item["code"] == "unknown_top_level_field"
    )
    assert finding == {
        "path": module.WORKFLOW_COMPATIBILITY_UNKNOWN_PATH,
        "level": "unsupported",
        "message": (
            "unknown top-level field: "
            f"{module.WORKFLOW_COMPATIBILITY_UNKNOWN_PATH}"
        ),
        "blocking": False,
        "code": "unknown_top_level_field",
    }
    if unsafe_key:
        assert unsafe_key not in finding["path"]
        assert unsafe_key not in finding["message"]
    assert len(response.content) < 65_536
    module.WorkflowDetailResponse.model_validate(payload)


def test_workflow_detail_omitted_or_wrong_source_never_falls_through_to_showcase(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    showcase_module._clear_verified_showcase_cache_for_tests()
    router = _module().router

    omitted = _detail_get(router, "resilience", token=_reader())
    wrong = _detail_get(
        router, "resilience", token=_reader(), catalog_source="project"
    )

    assert omitted.status_code == wrong.status_code == 404
    assert omitted.json() == wrong.json() == {
        "detail": {"code": "workflow_not_found"}
    }


def test_workflow_detail_showcase_integrity_failure_is_typed_and_read_only(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    trust = home / "workflow" / "trust.json"
    trust.parent.mkdir()
    trust.write_text('{"version":1,"entries":{}}\n', encoding="utf-8")
    copied = tmp_path / "showcases"
    shutil.copytree(Path(showcase_module.__file__).with_name("showcases"), copied)
    workflow = (
        copied
        / "packages"
        / "approval-gate"
        / "workflows"
        / "approval-gate.yaml"
    )
    workflow.write_text(workflow.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    showcase_module._clear_verified_showcase_cache_for_tests()
    monkeypatch.setattr(
        showcase_module,
        "_bundle_path",
        lambda explicit=None: _test_bundle_path(copied),
    )
    before_home = _tree_snapshot(home)
    before_bundle = _tree_snapshot(copied)

    response = _detail_get(
        _module().router,
        "approval-gate",
        token=_reader(),
        catalog_source="showcase",
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "workflow_showcase_verification_failed",
            "retryable": False,
        }
    }
    assert _tree_snapshot(home) == before_home
    assert _tree_snapshot(copied) == before_bundle


def test_workflow_detail_showcase_verification_budget_is_typed_capacity(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    showcase_module._clear_verified_showcase_cache_for_tests()
    import plugins.workflow.catalog_api as catalog_api

    monkeypatch.setattr(catalog_api, "CATALOG_MAX_RESOURCE_FILE_BYTES", 1)

    response = _detail_get(
        _module().router,
        "approval-gate",
        token=_reader(),
        catalog_source="showcase",
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {"code": "workflow_catalog_capacity", "retryable": True}
    }


def test_first_workflow_detail_read_creates_no_profile_state(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(
        home / "workflows",
        name="first-read",
        filename="first-read.yaml",
    )
    database = home / "workflows" / "admission.sqlite3"
    trust = home / "workflow" / "trust.json"
    absent_before = {
        database,
        database.with_name(database.name + "-wal"),
        database.with_name(database.name + "-shm"),
        trust,
        trust.with_suffix(".lock"),
    }
    assert not any(path.exists() for path in absent_before)
    before = _tree_snapshot(home)

    response = _detail_get(_module().router, "first-read", token=_reader())

    assert response.status_code == 200
    assert response.json()["trust_state"] == "untrusted"
    assert response.json()["coordinator"] == {
        "healthy": False,
        "status": "unavailable",
        "reason": "coordinator_missing",
    }
    assert _tree_snapshot(home) == before
    assert not any(path.exists() for path in absent_before)


def test_workflow_detail_first_reads_closed_wal_database_without_sidecars_or_mutation(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(
        home / "workflows",
        name="closed-wal",
        filename="closed-wal.yaml",
    )
    database = home / "workflows" / "admission.sqlite3"
    anchor = sqlite3.connect(database, isolation_level=None)
    anchor.row_factory = sqlite3.Row
    try:
        assert anchor.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        install_coordinator_schema(anchor)
        anchor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        now = datetime.now(timezone.utc)
        store = CoordinatorStore(database)
        acquired = store.try_acquire(
            CoordinatorIdentity(
                owner_id="closed-wal-test",
                host_kind="web",
                host_instance_id="closed-wal-instance",
                pid=os.getpid(),
                process_start_time=None,
            ),
            now=now,
            lease_seconds=60,
        )
        assert acquired.is_leader
        anchor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        anchor.close()
    wal = database.with_name(database.name + "-wal")
    shm = database.with_name(database.name + "-shm")
    # Closing the LAST connection to a WAL database makes SQLite checkpoint and
    # REMOVE both sidecars, so whether they still exist here depends on whether
    # any other connection (CoordinatorStore's) happens to be alive -- which is
    # refcount/GC timing, not behaviour under test. Asserting they exist made
    # this fail with FileNotFoundError on macOS and Linux CI alike.
    #
    # The state this test needs is simply "no sidecars before the request", and
    # SQLite having already produced it is a pass, not a failure. When a -wal
    # does survive, the truncating checkpoint above must still have emptied it.
    if wal.exists():
        assert wal.stat().st_size == 0
        wal.unlink()
    if shm.exists():
        shm.unlink()
    assert not wal.exists()
    assert not shm.exists()
    before_request = _tree_snapshot(home)

    response = _detail_get(_module().router, "closed-wal", token=_reader())

    assert response.status_code == 200
    assert response.json()["coordinator"] == {
        "healthy": True,
        "status": "healthy",
        "reason": "coordinator_heartbeat_fresh",
    }
    assert _tree_snapshot(home) == before_request
    assert not wal.exists()
    assert not shm.exists()


def test_workflow_detail_first_reads_active_wal_without_mutating_profile(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(
        home / "workflows",
        name="active-wal",
        filename="active-wal.yaml",
    )
    database = home / "workflows" / "admission.sqlite3"
    anchor = sqlite3.connect(database, isolation_level=None)
    anchor.row_factory = sqlite3.Row
    try:
        assert anchor.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        install_coordinator_schema(anchor)
        anchor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        now = datetime.now(timezone.utc)
        store = CoordinatorStore(database)
        acquired = store.try_acquire(
            CoordinatorIdentity(
                owner_id="active-wal-test",
                host_kind="web",
                host_instance_id="active-wal-instance",
                pid=os.getpid(),
                process_start_time=None,
            ),
            now=now,
            lease_seconds=60,
        )
        assert acquired.is_leader
        assert acquired.lease.owner_id == "active-wal-test"
        assert acquired.lease.lease_expires_at > now
        assert database.with_name(database.name + "-wal").stat().st_size > 0
        before_request = _tree_snapshot(home)

        response = _detail_get(_module().router, "active-wal", token=_reader())

        assert response.status_code == 200
        assert response.json()["coordinator"] == {
            "healthy": True,
            "status": "healthy",
            "reason": "coordinator_heartbeat_fresh",
        }
        assert _tree_snapshot(home) == before_request
    finally:
        anchor.close()


def test_workflow_detail_degrades_after_bounded_snapshot_race_and_cleans_temp(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    temp_root = tmp_path / "health-temp"
    temp_root.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    workflow_writer(
        home / "workflows",
        name="snapshot-race",
        filename="snapshot-race.yaml",
    )
    database = home / "workflows" / "admission.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        install_coordinator_schema(connection)
    original_signature = CoordinatorStore._snapshot_signature
    database_signatures = 0

    def unstable_signature(path: Path):
        nonlocal database_signatures
        signature = original_signature(path)
        if path == database and signature is not None:
            database_signatures += 1
            if database_signatures % 2 == 0:
                return replace(
                    signature,
                    modified_ns=signature.modified_ns + database_signatures,
                )
        return signature

    monkeypatch.setattr(
        CoordinatorStore, "_snapshot_signature", staticmethod(unstable_signature)
    )
    before = _tree_snapshot(home)

    response = _detail_get(_module().router, "snapshot-race", token=_reader())

    assert response.status_code == 200
    assert response.json()["coordinator"] == {
        "healthy": False,
        "status": "unavailable",
        "reason": "coordinator_health_unavailable",
    }
    assert database_signatures == 6
    assert str(tmp_path) not in response.text
    assert _tree_snapshot(home) == before
    assert list(temp_root.iterdir()) == []


def test_workflow_detail_rejects_same_metadata_recycled_wal_content(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(
        home / "workflows",
        name="recycled-wal",
        filename="recycled-wal.yaml",
    )
    database = home / "workflows" / "admission.sqlite3"
    wal = database.with_name(database.name + "-wal")
    anchor = sqlite3.connect(database, isolation_level=None)
    anchor.row_factory = sqlite3.Row
    generation_a = b""
    try:
        assert anchor.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        install_coordinator_schema(anchor)
        anchor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        now = datetime.now(timezone.utc)
        CoordinatorStore(database).try_acquire(
            CoordinatorIdentity(
                owner_id="recycled-wal-test",
                host_kind="web",
                host_instance_id="recycled-wal-instance",
                pid=os.getpid(),
                process_start_time=None,
            ),
            now=now,
            lease_seconds=60,
        )
        generation_a = wal.read_bytes()
        assert generation_a
        generation_b = generation_a[:-1] + bytes([generation_a[-1] ^ 1])
        fixed_database_signature = CoordinatorStore._snapshot_signature(database)
        fixed_wal_signature = CoordinatorStore._snapshot_signature(wal)
        original_signature = CoordinatorStore._snapshot_signature
        wal_copies = 0

        def coarse_signature(path: Path):
            if path == database:
                return fixed_database_signature
            if path == wal:
                return fixed_wal_signature
            return original_signature(path)

        def recycling_copy(
            source: Path,
            destination: Path,
            *,
            expected,
            max_bytes: int,
        ):
            nonlocal wal_copies
            data = source.read_bytes()
            assert len(data) <= max_bytes
            destination.write_bytes(data)
            if source == wal:
                wal_copies += 1
                source.write_bytes(
                    generation_b if data == generation_a else generation_a
                )
            return len(data), hashlib.sha256(data).hexdigest()

        monkeypatch.setattr(
            CoordinatorStore, "_snapshot_signature", staticmethod(coarse_signature)
        )
        monkeypatch.setattr(
            CoordinatorStore, "_copy_snapshot_file", staticmethod(recycling_copy)
        )

        response = _detail_get(_module().router, "recycled-wal", token=_reader())

        assert response.status_code == 200
        assert response.json()["coordinator"] == {
            "healthy": False,
            "status": "unavailable",
            "reason": "coordinator_health_unavailable",
        }
        assert wal_copies == 6
        assert str(tmp_path) not in response.text
    finally:
        if generation_a:
            wal.write_bytes(generation_a)
        anchor.close()


def test_workflow_detail_degrades_when_snapshot_exceeds_byte_budget(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    temp_root = tmp_path / "health-temp"
    temp_root.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))
    workflow_writer(
        home / "workflows",
        name="snapshot-capacity",
        filename="snapshot-capacity.yaml",
    )
    database = home / "workflows" / "admission.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        install_coordinator_schema(connection)
    monkeypatch.setattr(
        coordinator_store_module,
        "_HEALTH_SNAPSHOT_MAX_DATABASE_BYTES",
        database.stat().st_size - 1,
    )
    before = _tree_snapshot(home)

    response = _detail_get(_module().router, "snapshot-capacity", token=_reader())

    assert response.status_code == 200
    assert response.json()["coordinator"] == {
        "healthy": False,
        "status": "unavailable",
        "reason": "coordinator_health_unavailable",
    }
    assert str(tmp_path) not in response.text
    assert _tree_snapshot(home) == before
    assert list(temp_root.iterdir()) == []


def test_workflow_detail_is_full_read_only_preflight_with_coordinator_down(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = workflow_writer(
        home / "workflows",
        name="reviewable",
        filename="reviewable.yaml",
        description="Review this workflow",
        nodes=[
            {"id": "collect", "bash": "ULTRA_SECRET_BASH_BODY"},
            {
                "id": "publish",
                "prompt": "ULTRA_SECRET_PROMPT_BODY",
                "depends_on": ["collect"],
                "allowed_tools": ["Read"],
            },
        ],
    )
    path.with_name("reviewable.hermes.yaml").write_text(
        yaml.safe_dump(
            {
                "delivery_defaults": {
                    "inputs": {
                        "title": {"type": "string", "required": True},
                        "count": {"type": "number", "required": False},
                    }
                },
                "overlap_policy": "queue",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _trust(home, path)
    database = home / "workflows" / "admission.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        install_coordinator_schema(connection)
    trust_path = WorkflowTrustStore(home).path
    assert not database.with_name(database.name + "-wal").exists()
    assert not database.with_name(database.name + "-shm").exists()
    before = _tree_snapshot(home)

    response = _detail_get(_module().router, "reviewable", token=_reader())

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "reviewable"
    assert payload["trust_state"] == "trusted"
    assert payload["inputs"] == [
        {"name": "count", "type": "number", "required": False},
        {"name": "title", "type": "string", "required": True},
    ]
    assert payload["supported_inputs"] == {
        "supported": True,
        "reason": "flat_inputs",
    }
    assert payload["risk_summary"]["package_digest"]
    assert payload["risk_summary"]["risk_digest"]
    assert payload["compatibility"]["level"]
    assert isinstance(payload["compatibility"]["findings"], list)
    assert payload["language"] == {
        "declared_profile": None,
        "effective_profile": "hermes-legacy",
        "legacy": True,
        "normalizer_version": 2,
        "normalized_definition_digest": load_workflow(path).language.normalized_definition_digest,
    }
    assert "semantic_fingerprint" not in payload["language"]
    assert payload["coordinator"]["healthy"] is False
    assert payload["coordinator"]["reason"] == "coordinator_missing"
    assert payload["topology"] == {
        "text": "collect -> publish",
        "mermaid": payload["topology"]["mermaid"],
        "warnings": [],
        "omitted": None,
    }
    assert set(payload["definition"]) == {
        "name",
        "description",
        "nodes",
        "edges",
        "inputs",
        "policies",
    }
    assert payload["definition"]["edges"] == [
        {"from": "collect", "to": "publish"}
    ]
    assert b"ULTRA_SECRET_BASH_BODY" not in response.content
    assert b"ULTRA_SECRET_PROMPT_BODY" not in response.content
    assert trust_path.is_file()
    assert _tree_snapshot(home) == before


def test_workflow_detail_matches_catalog_declared_text_projection(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = workflow_writer(
        home / "workflows", name="bounded-text", filename="bounded-text.yaml"
    )
    path.with_name("bounded-text.hermes.yaml").write_text(
        yaml.safe_dump(
            {
                "delivery_defaults": {
                    "inputs": {
                        "arguments": {
                            "kind": "text",
                            "required": True,
                            "max_bytes": 80 * 1024,
                            "default": "SECRET_ARGUMENT_DEFAULT",
                        },
                        "summary": {
                            "kind": "text",
                            "required": False,
                            "max_bytes": 2048,
                        },
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    router = _module().router
    client = TestClient(_app(router, token=_reader()))

    catalog_response = client.get("/api/plugins/workflow/workflows")
    detail_response = client.get(
        "/api/plugins/workflow/workflows/bounded-text",
        params={"catalog_source": "profile"},
    )

    assert catalog_response.status_code == detail_response.status_code == 200
    catalog_row = next(
        item
        for item in catalog_response.json()["items"]
        if item.get("source") == "profile" and item.get("name") == "bounded-text"
    )
    detail = detail_response.json()
    assert (
        detail["inputs"]
        == catalog_row["inputs"]
        == [
            {
                "name": "arguments",
                "type": "text",
                "required": True,
                "max_bytes": 64 * 1024,
            },
            {
                "name": "summary",
                "type": "text",
                "required": False,
                "max_bytes": 2048,
            },
        ]
    )
    assert (
        detail["supported_inputs"]
        == catalog_row["supported_inputs"]
        == {
            "supported": True,
            "reason": "flat_inputs",
        }
    )
    assert (
        detail["run_support"]
        == catalog_row["run_support"]
        == {
            "supported": True,
            "reason": "supported",
        }
    )
    assert b"SECRET_ARGUMENT_DEFAULT" not in catalog_response.content
    assert b"SECRET_ARGUMENT_DEFAULT" not in detail_response.content


def test_workflow_detail_topology_matches_plain_cli_show_json(
    tmp_path, monkeypatch, workflow_writer, capsys
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    workdir.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(
        home / "workflows",
        name="topology-parity",
        filename="topology-parity.yaml",
        nodes=[
            {"id": "start", "bash": "true"},
            {"id": "left", "bash": "true", "depends_on": ["start"]},
            {"id": "right", "bash": "true", "depends_on": ["start"]},
        ],
    )

    response = _detail_get(_module().router, "topology-parity", token=_reader())
    cli = _cli_show_json(
        workdir=workdir, home=home, name="topology-parity", capsys=capsys
    )

    assert response.status_code == 200
    topology = response.json()["topology"]
    assert topology["mermaid"] == cli["topology_mermaid"]
    assert topology["text"] == cli["topology_text"]
    assert topology["omitted"] is None
    assert cli["topology_warnings"] == []


def test_workflow_detail_over_bounds_topology_matches_cli_and_explains_omission(
    tmp_path, monkeypatch, workflow_writer, capsys
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    workdir.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    nodes = [
        {
            "id": f"node-{index:03d}",
            "bash": "true",
            **(
                {"depends_on": [f"node-{index - 1:03d}"]}
                if index
                else {}
            ),
        }
        for index in range(101)
    ]
    workflow_writer(
        home / "workflows",
        name="large-topology",
        filename="large-topology.yaml",
        nodes=nodes,
    )

    response = _detail_get(_module().router, "large-topology", token=_reader())
    cli = _cli_show_json(
        workdir=workdir, home=home, name="large-topology", capsys=capsys
    )

    assert response.status_code == 200
    topology = response.json()["topology"]
    assert topology["mermaid"] is None
    assert topology["mermaid"] == cli["topology_mermaid"]
    assert topology["text"] == cli["topology_text"]
    assert topology["text"]
    assert topology["omitted"] == "topology_mermaid_too_many_nodes"
    assert topology["omitted"] in cli["topology_warnings"]


def test_workflow_detail_preserves_all_ordered_topology_warnings(
    tmp_path, monkeypatch, workflow_writer, capsys
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    workdir.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    nodes = [
        {
            "id": f"dense-{index:03d}",
            "bash": "true",
            **(
                {
                    "depends_on": [
                        f"dense-{source:03d}"
                        for source in range(max(0, index - 3), index)
                    ]
                }
                if index
                else {}
            ),
        }
        for index in range(101)
    ]
    workflow_writer(
        home / "workflows",
        name="dense-topology",
        filename="dense-topology.yaml",
        nodes=nodes,
    )

    response = _detail_get(_module().router, "dense-topology", token=_reader())
    cli = _cli_show_json(
        workdir=workdir, home=home, name="dense-topology", capsys=capsys
    )

    assert response.status_code == 200
    topology = response.json()["topology"]
    assert cli["topology_warnings"] == [
        "topology_mermaid_too_many_nodes",
        "topology_mermaid_too_many_edges",
    ]
    assert topology["warnings"] == cli["topology_warnings"]
    assert topology["omitted"] == cli["topology_warnings"][0]
    assert len(response.json()["definition"]["edges"]) > 200


def test_workflow_detail_mermaid_sanitizes_hostile_node_labels(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    hostile = 'hostile"`<script>%%{init}'
    workflow_writer(
        home / "workflows",
        name="hostile-topology",
        filename="hostile-topology.yaml",
        nodes=[
            {"id": hostile, "bash": "true"},
            {"id": "safe", "bash": "true", "depends_on": [hostile]},
        ],
    )

    response = _detail_get(_module().router, "hostile-topology", token=_reader())

    assert response.status_code == 200
    mermaid = response.json()["topology"]["mermaid"]
    assert mermaid is not None
    assert hostile not in mermaid
    assert "`" not in mermaid
    assert "<script>" not in mermaid
    assert "%%{init}" not in mermaid
    sanitized, truncated = sanitize_topology_label(f"{hostile} (bash)")
    assert truncated is False
    assert sanitized in mermaid


def test_workflow_detail_and_cli_semantically_redact_nested_bodies_and_paths(
    tmp_path, monkeypatch, workflow_writer, capsys
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    workdir.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    hook_body = "HOOK_SYSTEM_MESSAGE_CANARY"
    nested_body = "HOOK_NESTED_CONTEXT_CANARY"
    nested_content = "HOOK_CONTENT_CANARY"
    policy_path = "/private/workflow-policy-canary"
    node_path = "/private/node-policy-canary"
    workflow_writer(
        home / "workflows",
        name="semantic-redaction",
        filename="semantic-redaction.yaml",
        modelReasoningEffort="high",
        sandbox={
            "backend": "docker",
            "mode": "read-only",
            "workspace": policy_path,
            "credential": "SANDBOX_SECRET_CANARY",
        },
        nodes=[
            {
                "id": "agent",
                "prompt": "TOP_LEVEL_PROMPT_CANARY",
                "sandbox": {"arbitrary": node_path, "enabled": True},
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Read",
                            "response": {
                                "continue": True,
                                "systemMessage": hook_body,
                                "hookSpecificOutput": {
                                    "hookEventName": "PreToolUse",
                                    "additionalContext": nested_body,
                                    "updatedInput": {
                                        "innocent": policy_path,
                                        "nested": {"content": nested_content},
                                    },
                                    "content": nested_content,
                                },
                            },
                        }
                    ]
                },
            },
            {"id": "cancel", "cancel": "CANCEL_FREE_FORM_BODY_CANARY"},
        ],
    )

    response = _detail_get(_module().router, "semantic-redaction", token=_reader())
    cli = _cli_show_json(
        workdir=workdir, home=home, name="semantic-redaction", capsys=capsys
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["definition"] == cli["definition"]
    response_bytes = response.content
    cli_bytes = json.dumps(cli, sort_keys=True).encode()
    for canary in (
        hook_body,
        nested_body,
        nested_content,
        policy_path,
        node_path,
        "TOP_LEVEL_PROMPT_CANARY",
        "CANCEL_FREE_FORM_BODY_CANARY",
        "SANDBOX_SECRET_CANARY",
    ):
        assert canary.encode() not in response_bytes
        assert canary.encode() not in cli_bytes
    assert b"[REDACTED]" in response_bytes
    assert payload["definition"]["nodes"][1]["value"] == "[REDACTED]"
    assert payload["definition"]["policies"]["workflow"] == {
        "modelReasoningEffort": "high",
        "sandbox": {
            "backend": "docker",
            "mode": "read-only",
            "workspace": "[REDACTED]",
            "credential": "[REDACTED]",
        },
    }


def test_workflow_detail_preserves_benign_policy_and_sandbox_values(
    tmp_path, monkeypatch, workflow_writer, capsys
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    workdir.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    absolute_canary = "/private/sandbox-workspace-canary"
    secret_canary = "SANDBOX_EXPLICIT_SECRET_CANARY"
    workflow_writer(
        home / "workflows",
        name="benign-policy",
        filename="benign-policy.yaml",
        modelReasoningEffort="high",
        sandbox={
            "backend": "docker",
            "mode": "read-only",
            "workspace": absolute_canary,
            "token": secret_canary,
        },
    )

    response = _detail_get(_module().router, "benign-policy", token=_reader())
    cli = _cli_show_json(
        workdir=workdir, home=home, name="benign-policy", capsys=capsys
    )

    assert response.status_code == 200
    assert response.json()["definition"] == cli["definition"]
    workflow_policy = response.json()["definition"]["policies"]["workflow"]
    assert workflow_policy == {
        "modelReasoningEffort": "high",
        "sandbox": {
            "backend": "docker",
            "mode": "read-only",
            "workspace": "[REDACTED]",
            "token": "[REDACTED]",
        },
    }
    serialized = response.content + json.dumps(cli, sort_keys=True).encode()
    assert absolute_canary.encode() not in serialized
    assert secret_canary.encode() not in serialized


def test_workflow_detail_maps_resource_budget_exhaustion_to_typed_capacity(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(
        home / "workflows",
        name="oversized-resource",
        filename="oversized-resource.yaml",
        nodes=[{"id": "run", "command": "large"}],
    )
    commands = home / "commands"
    commands.mkdir()
    (commands / "large.md").write_bytes(b"SENSITIVE_RESOURCE" * (64 * 1024 + 1))

    response = _detail_get(
        _module().router,
        "oversized-resource",
        token=_reader(),
        raise_server_exceptions=False,
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {"code": "workflow_catalog_capacity", "retryable": True}
    }
    assert b"SENSITIVE_RESOURCE" not in response.content


def test_workflow_detail_maps_missing_executable_resource_to_typed_error(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(
        home / "workflows",
        name="missing-resource",
        filename="missing-resource.yaml",
        nodes=[{"id": "run", "command": "absent-command"}],
    )

    response = _detail_get(
        _module().router,
        "missing-resource",
        token=_reader(),
        raise_server_exceptions=False,
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {"code": "workflow_invalid_definition", "retryable": False}
    }
    assert b"absent-command" not in response.content
    assert b"Traceback" not in response.content


def test_workflow_detail_corrupt_coordinator_row_degrades_without_mutation_or_text(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(
        home / "workflows",
        name="corrupt-coordinator",
        filename="corrupt-coordinator.yaml",
    )
    database = home / "workflows" / "admission.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE coordinator_lease (singleton INTEGER PRIMARY KEY, "
            "hostile_column TEXT)"
        )
        connection.execute(
            "INSERT INTO coordinator_lease VALUES (1, 'CORRUPT_ROW_CANARY')"
        )
    before = _tree_snapshot(home)

    response = _detail_get(
        _module().router,
        "corrupt-coordinator",
        token=_reader(),
        raise_server_exceptions=False,
    )

    assert response.status_code == 200
    assert response.json()["coordinator"] == {
        "healthy": False,
        "status": "unavailable",
        "reason": "coordinator_health_unavailable",
    }
    assert b"CORRUPT_ROW_CANARY" not in response.content
    assert _tree_snapshot(home) == before


def test_workflow_detail_long_description_is_suffix_bounded(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    workflow_writer(
        home / "workflows",
        name="long-description",
        filename="long-description.yaml",
        description="d" * 20_000,
    )

    response = _detail_get(
        _module().router,
        "long-description",
        token=_reader(),
        raise_server_exceptions=False,
    )

    assert response.status_code == 200
    description = response.json()["description"]
    assert len(description) <= 16_384
    assert description.endswith("…[TRUNCATED]")
    assert response.json()["definition"]["description"] == description


def test_workflow_detail_preserves_complete_definition_above_generic_list_limit(
    tmp_path, monkeypatch, workflow_writer, capsys
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    workdir.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    nodes = [{"id": f"node-{index:03d}", "bash": "true"} for index in range(201)]
    workflow_writer(
        home / "workflows",
        name="complete-definition",
        filename="complete-definition.yaml",
        nodes=nodes,
    )

    response = _detail_get(_module().router, "complete-definition", token=_reader())
    cli = _cli_show_json(
        workdir=workdir, home=home, name="complete-definition", capsys=capsys
    )

    assert response.status_code == 200
    definition = response.json()["definition"]
    assert len(definition["nodes"]) == 201
    assert definition["nodes"][-1]["id"] == "node-200"
    assert definition == cli["definition"]


def test_workflow_detail_accepts_exact_node_and_edge_bounds_and_rejects_next(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))

    nodes_at_limit = [
        {"id": f"node-{index:03d}", "bash": "true"} for index in range(512)
    ]
    workflow_writer(
        home / "workflows",
        name="nodes-at-limit",
        filename="nodes-at-limit.yaml",
        nodes=nodes_at_limit,
    )
    nodes_response = _detail_get(
        _module().router, "nodes-at-limit", token=_reader()
    )
    assert nodes_response.status_code == 200
    assert len(nodes_response.json()["definition"]["nodes"]) == 512

    workflow_writer(
        home / "workflows",
        name="nodes-over-limit",
        filename="nodes-over-limit.yaml",
        nodes=[{"id": f"node-{index:03d}", "bash": "true"} for index in range(513)],
    )
    nodes_over = _detail_get(
        _module().router, "nodes-over-limit", token=_reader()
    )
    assert nodes_over.status_code == 503
    assert nodes_over.json() == {
        "detail": {"code": "workflow_catalog_capacity", "retryable": True}
    }
    assert str(tmp_path) not in nodes_over.text

    def edge_nodes(edge_count: int) -> list[dict[str, object]]:
        remaining = edge_count
        result: list[dict[str, object]] = []
        for index in range(512):
            count = min(index, remaining)
            dependencies = [f"edge-{source:03d}" for source in range(count)]
            remaining -= count
            result.append({
                "id": f"edge-{index:03d}",
                "bash": "true",
                **({"depends_on": dependencies} if dependencies else {}),
            })
        assert remaining == 0
        return result

    workflow_writer(
        home / "workflows",
        name="edges-at-limit",
        filename="edges-at-limit.yaml",
        nodes=edge_nodes(4_096),
    )
    edges_response = _detail_get(
        _module().router, "edges-at-limit", token=_reader()
    )
    assert edges_response.status_code == 200
    assert len(edges_response.json()["definition"]["edges"]) == 4_096

    workflow_writer(
        home / "workflows",
        name="edges-over-limit",
        filename="edges-over-limit.yaml",
        nodes=edge_nodes(4_097),
    )
    edges_over = _detail_get(
        _module().router, "edges-over-limit", token=_reader()
    )
    assert edges_over.status_code == 503
    assert edges_over.json() == {
        "detail": {"code": "workflow_catalog_capacity", "retryable": True}
    }
    assert str(tmp_path) not in edges_over.text


def test_workflow_detail_accepts_exact_byte_bound_and_rejects_next_byte(
    tmp_path, monkeypatch, workflow_writer, capsys
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    workdir.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    full_tag = "x" * 16_384
    path = workflow_writer(
        home / "workflows",
        name="bytes-at-limit",
        filename="bytes-at-limit.yaml",
        tags=[full_tag] * 31 + ["z"],
    )
    baseline = _cli_show_json(
        workdir=workdir, home=home, name="bytes-at-limit", capsys=capsys
    )["definition"]
    baseline_size = len(
        json.dumps(
            baseline, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    )
    final_length = 1 + (512 * 1024 - baseline_size)
    assert 1 <= final_length <= 16_384
    document = {
        "name": "bytes-at-limit",
        "description": "Portable workflow fixture",
        "nodes": [{"id": "start", "bash": "true"}],
        "tags": [full_tag] * 31 + ["z" * final_length],
    }
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    at_limit = _detail_get(_module().router, "bytes-at-limit", token=_reader())

    assert at_limit.status_code == 200
    definition = at_limit.json()["definition"]
    assert len(
        json.dumps(
            definition, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ) == 512 * 1024

    document["tags"] = [full_tag] * 31 + ["z" * (final_length + 1)]
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    over_limit = _detail_get(_module().router, "bytes-at-limit", token=_reader())

    assert over_limit.status_code == 503
    assert over_limit.json() == {
        "detail": {"code": "workflow_catalog_capacity", "retryable": True}
    }
    assert str(tmp_path) not in over_limit.text


def test_workflow_detail_definition_uses_cli_redaction_and_never_leaks_defaults(
    tmp_path, monkeypatch, workflow_writer, capsys
) -> None:
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    workdir.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = workflow_writer(
        home / "workflows",
        name="definition-parity",
        filename="definition-parity.yaml",
        nodes=[{"id": "start", "prompt": "DO_NOT_LEAK_PROMPT_BODY"}],
        provider="test-provider",
    )
    path.with_name("definition-parity.hermes.yaml").write_text(
        yaml.safe_dump(
            {
                "delivery_defaults": {
                    "inputs": {
                        "title": {
                            "type": "string",
                            "required": True,
                            "default": "DO_NOT_LEAK_DEFAULT_BYTES",
                        }
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    response = _detail_get(_module().router, "definition-parity", token=_reader())
    cli = _cli_show_json(
        workdir=workdir, home=home, name="definition-parity", capsys=capsys
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["definition"] == cli["definition"]
    assert payload["definition"]["inputs"]["title"] == {
        "type": "string",
        "required": True,
        "default": "[REDACTED]",
    }
    assert payload["definition"]["nodes"][0]["value"] == "[REDACTED]"
    serialized = json.dumps(payload, sort_keys=True)
    assert "DO_NOT_LEAK_DEFAULT_BYTES" not in serialized
    assert "DO_NOT_LEAK_PROMPT_BODY" not in serialized
