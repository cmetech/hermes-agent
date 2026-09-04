"""Distribution-bound marketplace workflow trust behavior."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pytest
import yaml

from hermes_cli.plugin_configuration import ConnectorCapabilitySnapshot
from hermes_cli.runtime_provider import classify_execution_runtime
from plugins.workflow.admission_service import assess_workflow_admission
from plugins.workflow.api_admission import (
    ApiAdmissionAuthority,
    ApiAdmissionError,
    start_api_run,
)
import plugins.workflow.api_admission as api_admission_module
import plugins.workflow.catalog_api as catalog_api
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.coordinator_store import CoordinatorIdentity, CoordinatorStore
from plugins.workflow.entitlement import AIEntitlementResolution
from plugins.workflow.language import supports_phase4_semantics
from plugins.workflow.marketplace.package import WorkflowMarketplaceError
from plugins.workflow.models import WorkflowMarketplaceBinding
from plugins.workflow.runner_binding import (
    RunnerCapabilities,
    WorkflowRunnerBinding,
    execution_capability_context,
)
from plugins.workflow.schema import parse_workflow_source_bytes
from plugins.workflow.store import InputSnapshotError, RunStore
from plugins.workflow.trust import (
    WORKFLOW_RESOURCE_MAX_FILE_BYTES,
    WORKFLOW_RESOURCE_MAX_FILES,
    WORKFLOW_RESOURCE_MAX_TOTAL_BYTES,
    WorkflowResourceReadBudget,
    WorkflowTrustStore,
    compute_package_digest,
)


_DISTRIBUTION_DOMAIN = b"hermes.workflow-package.v1\0"


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _publish(root: Path) -> str:
    files = [
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path.relative_to(root).as_posix() != "digests.json"
    ]
    digest = hashlib.sha256(_DISTRIBUTION_DOMAIN)
    records: list[dict[str, object]] = []
    for relative_path, content in sorted(files):
        path_bytes = relative_path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        content_digest = hashlib.sha256(content)
        digest.update(content_digest.digest())
        records.append({
            "path": relative_path,
            "sha256": content_digest.hexdigest(),
            "size": len(content),
        })
    package_digest = digest.hexdigest()
    (root / "digests.json").write_bytes(
        _json_bytes({
            "algorithm": "sha256",
            "contractVersion": 1,
            "files": records,
            "packageDigest": package_digest,
        })
    )
    return package_digest


def _workflow_bytes(name: str, *, script: bool = False) -> bytes:
    node = (
        {"id": "execute", "script": "helper", "runtime": "uv"}
        if script
        else {"id": "execute", "bash": "true"}
    )
    return yaml.safe_dump(
        {
            "name": name,
            "description": f"{name} workflow",
            "nodes": [node],
        },
        sort_keys=False,
    ).encode("utf-8")


def _package(root: Path) -> tuple[Path, str]:
    (root / "workflows").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "fixtures").mkdir()
    (root / "workflows" / "alpha.yaml").write_bytes(
        _workflow_bytes("marketplace-alpha", script=True)
    )
    (root / "workflows" / "beta.yaml").write_bytes(_workflow_bytes("marketplace-beta"))
    (root / "scripts" / "helper.py").write_bytes(b"print('alpha')\n")
    (root / "fixtures" / "unused.json").write_bytes(b'{"fixture":true}\n')
    (root / "workflow-package.json").write_bytes(
        _json_bytes({
            "schemaVersion": 1,
            "id": "package",
            "version": "1.0.0",
            "displayName": "Package",
            "description": "Package",
            "license": "MIT",
            "publisher": "Company",
            "tags": ["test"],
            "workflows": [
                {"definition": "workflows/alpha.yaml"},
                {"definition": "workflows/beta.yaml"},
            ],
            "externalRequirements": {
                "runtimes": [],
                "tools": [],
                "providers": [],
                "services": [],
                "secrets": [],
            },
        })
    )
    return root, _publish(root)


def _compile_member(root: Path, digest: str, relative_path: str):
    path = root / relative_path
    binding = WorkflowMarketplaceBinding(
        installation_key="company/package",
        source_name="company",
        package_id="package",
        package_version="1.0.0",
        workflow_relative_path=relative_path,
        distribution_digest=digest,
    )
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=None,
        source="profile",
        precedence=2,
        package_root=root,
        marketplace_binding=binding,
    )
    return compile_workflow(source, WorkflowCatalogSnapshot.capture((source,)))


def _add_root_definition_member(root: Path) -> tuple[bytes, str]:
    decoy = _workflow_bytes("marketplace-root-decoy")
    (root / "definition.yaml").write_bytes(decoy)
    manifest_path = root / "workflow-package.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["workflows"].append({"definition": "definition.yaml"})
    manifest_path.write_bytes(_json_bytes(manifest))
    return decoy, _publish(root)


def _context():
    runtime = classify_execution_runtime(
        provider="openrouter",
        model_config={
            "provider": "openrouter",
            "default": "openai/gpt-5.4",
        },
        provider_config={"base_url": "https://openrouter.ai/api/v1"},
    )
    return execution_capability_context(
        surface="background",
        entitlement=AIEntitlementResolution("deterministic"),
        runner_capabilities=RunnerCapabilities(starts_request_mcp=False),
        runtime_capabilities=runtime,
    )


def _runner_binding() -> WorkflowRunnerBinding:
    context = _context()
    return WorkflowRunnerBinding(
        real_runner=object(),
        deterministic_runner=object(),
        real_capabilities=context.runner_capabilities,
        deterministic_capabilities=context.runner_capabilities,
        runtime_capabilities=context.runtime_capabilities,
    )


def _budget() -> WorkflowResourceReadBudget:
    return WorkflowResourceReadBudget(
        max_file_bytes=WORKFLOW_RESOURCE_MAX_FILE_BYTES,
        max_total_bytes=WORKFLOW_RESOURCE_MAX_TOTAL_BYTES,
        max_files=WORKFLOW_RESOURCE_MAX_FILES,
    )


def _connector_snapshot() -> ConnectorCapabilitySnapshot:
    return ConnectorCapabilitySnapshot(
        ready_services=frozenset(),
        available_tools=frozenset(),
        fingerprint="0" * 64,
    )


def _healthy_coordinator(store: RunStore) -> None:
    acquired = CoordinatorStore(store.database).try_acquire(
        CoordinatorIdentity(
            owner_id="marketplace-trust-test",
            host_kind="web",
            host_instance_id="marketplace-trust-test",
            pid=1,
            process_start_time=None,
        ),
        now=datetime.now(timezone.utc),
        lease_seconds=60,
    )
    assert acquired.is_leader


def test_effective_marketplace_digest_uses_exact_domain_separated_identity():
    from plugins.workflow.marketplace.trust_binding import (
        effective_marketplace_digest,
    )

    assert (
        effective_marketplace_digest(
            distribution_digest="a" * 64,
            workflow_relative_path="workflows/demo.yaml",
            closure_digest="b" * 64,
        )
        == "703f9e18b420f54ed3e92a85cb1ddae34492acd4913237d4028a844619604e45"
    )


def test_marketplace_assessment_loads_distribution_once_and_binds_all_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from plugins.workflow.marketplace import trust_binding
    from plugins.workflow.marketplace.trust_binding import (
        effective_marketplace_digest,
    )

    root, distribution_digest = _package(tmp_path / "package")
    compilation = _compile_member(root, distribution_digest, "workflows/alpha.yaml")
    original = trust_binding.load_distribution
    calls: list[tuple[Path, str | None]] = []

    def recording_load(
        package_root: Path,
        *,
        expected_digest: str | None = None,
        read_budget: WorkflowResourceReadBudget | None = None,
    ):
        calls.append((package_root, expected_digest))
        return original(
            package_root,
            expected_digest=expected_digest,
            read_budget=read_budget,
        )

    monkeypatch.setattr(trust_binding, "load_distribution", recording_load)

    assessment = assess_workflow_admission(
        compilation, _context(), read_budget=_budget()
    )

    assert calls == [(root, distribution_digest)]
    assert assessment.package_digest.sha256 == effective_marketplace_digest(
        distribution_digest=distribution_digest,
        workflow_relative_path="workflows/alpha.yaml",
        closure_digest=compute_package_digest(compilation.package).sha256,
    )
    assert assessment.risk.package_digest == assessment.package_digest.sha256
    assert set(assessment.package_digest.covered_relative_paths) == {
        "fixtures/unused.json",
        "scripts/helper.py",
        "workflow-package.json",
        "workflows/alpha.yaml",
        "workflows/beta.yaml",
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda root: (root / "fixtures" / "unused.json").write_bytes(b"changed\n"),
        lambda root: (root / "scripts" / "helper.py").write_bytes(b"print('v2')\n"),
        lambda root: (root / "workflow-package.json").write_bytes(
            _json_bytes({
                **json.loads((root / "workflow-package.json").read_bytes()),
                "displayName": "Package v2",
            })
        ),
    ],
    ids=("unreferenced-fixture", "script", "manifest"),
)
def test_each_package_owned_byte_change_invalidates_marketplace_trust(
    tmp_path: Path,
    mutate: Callable[[Path], object],
):
    root, distribution_digest = _package(tmp_path / "package")
    initial = _compile_member(root, distribution_digest, "workflows/alpha.yaml")
    initial_assessment = assess_workflow_admission(
        initial, _context(), read_budget=_budget()
    )
    store = WorkflowTrustStore(tmp_path / "home")
    store.trust_origin(
        initial_assessment.package_digest.sha256,
        risk_digest=initial_assessment.risk.risk_digest,
        actor="desktop",
        origin="marketplace:company/package",
    )

    mutate(root)
    updated_digest = _publish(root)
    updated = _compile_member(root, updated_digest, "workflows/alpha.yaml")
    updated_assessment = assess_workflow_admission(
        updated, _context(), read_budget=_budget()
    )

    assert updated_assessment.package_digest != initial_assessment.package_digest
    assert updated_assessment.risk.risk_digest != initial_assessment.risk.risk_digest
    assert (
        store.check(
            updated_assessment.package_digest.sha256,
            risk_digest=updated_assessment.risk.risk_digest,
        )
        == "untrusted"
    )
    entry = catalog_api._catalog_entry(
        updated.package,
        store,
        store.snapshot_read_only(max_bytes=1024 * 1024),
        _budget(),
        compilation=updated,
        execution_context=_context(),
        connector_capabilities=_connector_snapshot(),
    )
    assert entry["trust_state"] == "untrusted"


def test_mismatched_binding_digest_blocks_with_stable_redacted_diagnostic(
    tmp_path: Path,
):
    root, distribution_digest = _package(tmp_path / "package")
    compilation = _compile_member(root, distribution_digest, "workflows/alpha.yaml")
    (root / "fixtures" / "unused.json").write_bytes(
        b"https://operator:SECRET_TOKEN@example.invalid\n"
    )

    with pytest.raises(WorkflowMarketplaceError) as error:
        assess_workflow_admission(compilation, _context(), read_budget=_budget())

    assert error.value.code == "package_digest_mismatch"
    assert "SECRET_TOKEN" not in str(error.value)


def test_api_admission_maps_mismatched_provenance_to_stable_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root, distribution_digest = _package(tmp_path / "package")
    compilation = _compile_member(root, distribution_digest, "workflows/alpha.yaml")
    (root / "fixtures" / "unused.json").write_bytes(b"changed after provenance\n")
    monkeypatch.setattr(
        api_admission_module,
        "_catalog_compilation",
        lambda *_args, **_kwargs: compilation,
    )

    with pytest.raises(ApiAdmissionError) as error:
        start_api_run(
            RunStore(tmp_path / "home"),
            hermes_home=tmp_path / "home",
            workdir=tmp_path,
            user_home=tmp_path,
            workflow_name="marketplace-alpha",
            values={},
            idempotency_key="marketplace-integrity",
            concurrency_policy="queue",
            authority=ApiAdmissionAuthority(
                principal="operator",
                namespace="operator",
                operator_scope=None,
                source_instance="desktop:test",
                assurance="local_admin_claim",
            ),
            catalog_source="profile",
            runner_binding=_runner_binding(),
        )

    assert error.value.code == "workflow_package_changed"
    assert error.value.status_code == 409


def test_legacy_marketplace_api_stages_selected_member_not_root_definition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root, _initial_digest = _package(tmp_path / "package")
    decoy, distribution_digest = _add_root_definition_member(root)
    compilation = _compile_member(root, distribution_digest, "workflows/alpha.yaml")
    assert not supports_phase4_semantics(
        compilation.package.language.effective_profile,
        compilation.package.language.normalizer_version,
    )
    assessment = assess_workflow_admission(
        compilation, _context(), read_budget=_budget()
    )
    assert assessment.package_digest.sha256 != compilation.composite_digest
    assert assessment.risk.package_digest == assessment.package_digest.sha256
    home = tmp_path / "home"
    store = RunStore(home)
    WorkflowTrustStore(home).trust_origin(
        assessment.package_digest.sha256,
        risk_digest=assessment.risk.risk_digest,
        actor="desktop",
        origin="marketplace:company/package",
    )
    _healthy_coordinator(store)
    monkeypatch.setattr(
        api_admission_module,
        "_catalog_compilation",
        lambda *_args, **_kwargs: compilation,
    )

    admitted = start_api_run(
        store,
        hermes_home=home,
        workdir=tmp_path,
        user_home=tmp_path,
        workflow_name="marketplace-alpha",
        values={},
        idempotency_key="marketplace-root-definition",
        concurrency_policy="queue",
        authority=ApiAdmissionAuthority(
            principal="operator",
            namespace="operator",
            operator_scope=None,
            source_instance="desktop:test",
            assurance="local_admin_claim",
        ),
        catalog_source="profile",
        runner_binding=_runner_binding(),
    )

    run_id = str(admitted["run_id"])
    run_directory = store.run_directory(run_id)
    assert (run_directory / "definition.yaml").read_bytes() == (
        root / "workflows" / "alpha.yaml"
    ).read_bytes()
    assert (run_directory / "definition.yaml").read_bytes() != decoy
    assert store.load_run(run_id)["definition_digest"] == (
        assessment.package_digest.sha256
    )


def test_legacy_marketplace_snapshot_rejects_reserved_closure_path(
    tmp_path: Path,
):
    root, _initial_digest = _package(tmp_path / "package")
    _decoy, distribution_digest = _add_root_definition_member(root)
    (root / "workflows" / "alpha.yaml").write_bytes(
        yaml.safe_dump(
            {
                "name": "marketplace-alpha",
                "description": "selected workflow",
                "nodes": [
                    {
                        "id": "execute",
                        "prompt": "use local MCP",
                        "mcp": "definition.yaml",
                    }
                ],
            },
            sort_keys=False,
        ).encode("utf-8")
    )
    distribution_digest = _publish(root)
    compilation = _compile_member(root, distribution_digest, "workflows/alpha.yaml")
    assert not supports_phase4_semantics(
        compilation.package.language.effective_profile,
        compilation.package.language.normalizer_version,
    )
    budget = _budget()
    assessment = assess_workflow_admission(compilation, _context(), read_budget=budget)
    store = RunStore(tmp_path / "home")

    with pytest.raises(InputSnapshotError, match="reserved snapshot path"):
        store.prepare_run_snapshot(
            compilation.package,
            resource_read_budget=budget,
            trusted_package_digest=assessment.package_digest,
        )

    assert list(store.staging_root.iterdir()) == []


def test_multi_workflow_package_has_distinct_effective_and_risk_identities(
    tmp_path: Path,
):
    root, distribution_digest = _package(tmp_path / "package")
    alpha_compilation = _compile_member(
        root, distribution_digest, "workflows/alpha.yaml"
    )
    beta_compilation = _compile_member(root, distribution_digest, "workflows/beta.yaml")
    alpha = assess_workflow_admission(
        alpha_compilation,
        _context(),
        read_budget=_budget(),
    )
    beta = assess_workflow_admission(
        beta_compilation,
        _context(),
        read_budget=_budget(),
    )

    assert alpha.package.marketplace_binding is not None
    assert beta.package.marketplace_binding is not None
    assert (
        alpha.package.marketplace_binding.distribution_digest
        == beta.package.marketplace_binding.distribution_digest
    )
    assert alpha.package_digest.sha256 != alpha_compilation.composite_digest
    assert beta.package_digest.sha256 != beta_compilation.composite_digest
    assert alpha.package_digest.sha256 != beta.package_digest.sha256
    assert alpha.risk.risk_digest != beta.risk.risk_digest


def test_admission_and_catalog_use_the_same_marketplace_trust_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from plugins.workflow.marketplace import trust_binding

    root, distribution_digest = _package(tmp_path / "package")
    compilation = _compile_member(root, distribution_digest, "workflows/alpha.yaml")
    original = trust_binding.load_distribution
    calls = 0

    def recording_load(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(trust_binding, "load_distribution", recording_load)
    assessment = assess_workflow_admission(
        compilation, _context(), read_budget=_budget()
    )
    assert calls == 1
    store = WorkflowTrustStore(tmp_path / "home")
    store.trust_origin(
        assessment.package_digest.sha256,
        risk_digest=assessment.risk.risk_digest,
        actor="desktop",
        origin="marketplace:company/package",
    )

    calls = 0
    entry = catalog_api._catalog_entry(
        compilation.package,
        store,
        store.snapshot_read_only(max_bytes=1024 * 1024),
        _budget(),
        compilation=compilation,
        execution_context=_context(),
        connector_capabilities=_connector_snapshot(),
    )

    assert calls == 1
    assert entry["trust_state"] == "trusted"


def test_loose_workflow_binding_preserves_existing_digest_without_distribution_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from plugins.workflow.marketplace import trust_binding
    from plugins.workflow.marketplace.trust_binding import (
        bind_marketplace_package_digest,
    )
    from plugins.workflow.trust import WorkflowPackageDigest

    root, distribution_digest = _package(tmp_path / "package")
    compilation = _compile_member(root, distribution_digest, "workflows/beta.yaml")
    loose = replace(compilation.package, marketplace_binding=None)
    base = WorkflowPackageDigest(
        compilation.composite_digest,
        compilation.covered_relative_paths,
    )
    monkeypatch.setattr(
        trust_binding,
        "load_distribution",
        lambda *_args, **_kwargs: pytest.fail(
            "loose workflow binding must not load a distribution"
        ),
    )

    assert (
        bind_marketplace_package_digest(
            loose,
            base,
            read_budget=_budget(),
        )
        is base
    )
