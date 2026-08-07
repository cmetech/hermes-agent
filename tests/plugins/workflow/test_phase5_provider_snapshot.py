from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hermes_cli.runtime_provider import classify_execution_runtime
from hermes_cli.workflow_model_resolution import parse_workflow_model_config
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
from plugins.workflow.language import WorkflowLanguageCompatibilityError
from plugins.workflow.provider_authority import (
    ProviderAuthorityEnvironment,
    read_workflow_provider_authority_bytes,
    resolve_workflow_provider_authority,
)
from plugins.workflow.resources import read_snapshot_provider_authority
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.scheduled_revalidation import verify_sealed_snapshot
from plugins.workflow.schema import parse_workflow_source_bytes
from plugins.workflow.store import RunStore
from plugins.workflow.trust import WorkflowPackageDigest


def _v5_compilation(tmp_path: Path, workflow_writer):
    path = workflow_writer(
        tmp_path / "source/workflows",
        name="provider-snapshot",
        filename="provider-snapshot.yaml",
        model="@primary",
        nodes=[{"id": "ask", "prompt": "hello", "effort": "high"}],
    )
    policy = b"language_compatibility: archon-2026-07\n"
    path.with_name("provider-snapshot.hermes.yaml").write_bytes(policy)
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=policy,
        source="project",
        precedence=1,
    )
    return compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=5,
    )


def _authority(package):
    config = parse_workflow_model_config({
        "model": {
            "provider": "openrouter",
            "default": "openai/gpt-5.4",
            "base_url": "https://openrouter.ai/api/v1",
        },
        "model_aliases": {
            "primary": {
                "provider": "openrouter",
                "model": "openai/gpt-5.4",
            }
        },
    })
    runtime = classify_execution_runtime(
        provider="openrouter",
        model_config={
            "provider": "openrouter",
            "default": "openai/gpt-5.4",
        },
        provider_config={"base_url": "https://openrouter.ai/api/v1"},
    )
    return resolve_workflow_provider_authority(
        package,
        model_config=config,
        default_runtime=runtime,
        environment=ProviderAuthorityEnvironment(
            session_store_available=True,
            mcp_available=True,
            hook_lifecycle_available=True,
            inline_agent_available=True,
            web_service_available=True,
            authoritative_cost_available=False,
        ),
    )


def _prepare(store: RunStore, compilation, authority):
    return store.prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        ),
        provider_authority=authority,
    )


def _admit(store: RunStore, compilation, authority, *, key: str):
    prepared = _prepare(store, compilation, authority)
    staging = prepared.staging_directory
    result = store.start_run(
        RunAdmissionRequest(
            workflow_name=compilation.package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=compilation.package.definition.name,
            run_metadata={
                "sealed_definition_digest": hashlib.sha256(
                    (staging / "definition.yaml").read_bytes()
                ).hexdigest(),
                "sealed_policy_digest": prepared.policy_digest,
                "sealed_input_digest": prepared.input_manifest_digest,
                "sealed_snapshot_digest": str(prepared.sealed_snapshot_digest),
            },
        ),
        immutable_snapshot=prepared,
    )
    assert result.run_id is not None
    return prepared, result.run_id


def test_v5_format2_seals_exact_canonical_provider_authority(tmp_path, workflow_writer):
    compilation = _v5_compilation(tmp_path, workflow_writer)
    authority = _authority(compilation.package)
    store = RunStore(tmp_path / "home")

    prepared, run_id = _admit(store, compilation, authority, key="sealed-provider")

    run_directory = store.run_directory(run_id)
    provider_bytes = (run_directory / "provider-resolution.json").read_bytes()
    resources = json.loads((run_directory / "resources.json").read_bytes())
    projection = store.load_run(run_id)
    recovered = read_workflow_provider_authority_bytes(provider_bytes)
    digest = hashlib.sha256(provider_bytes).hexdigest()

    assert provider_bytes == authority.canonical_bytes()
    assert recovered == authority
    assert "provider-resolution.json" in resources["sealed_paths"]
    assert resources["provider_resolution_sha256"] == digest
    assert prepared.provider_resolution_sha256 == digest
    assert projection["provider_resolution_sha256"] == digest
    verify_sealed_snapshot(projection, run_directory=run_directory)


def test_v5_restart_recovers_authority_only_from_authenticated_snapshot_bytes(
    tmp_path, workflow_writer
):
    compilation = _v5_compilation(tmp_path, workflow_writer)
    authority = _authority(compilation.package)
    home = tmp_path / "home"
    store = RunStore(home)
    _prepared, run_id = _admit(store, compilation, authority, key="recover-provider")

    package, sealed_paths, sealed_bytes = RunScheduler(
        RunStore(home)
    )._load_verified_run_package(run_id)
    recovered = read_workflow_provider_authority_bytes(
        sealed_bytes["provider-resolution.json"]
    )

    assert package.language.normalizer_version == 5
    assert "provider-resolution.json" in sealed_paths
    assert recovered.authority_digest == authority.authority_digest


@pytest.mark.parametrize("mutation", ["tamper", "omit", "extra"])
def test_v5_provider_authority_tree_mutation_fails_closed_before_reload(
    tmp_path, workflow_writer, mutation
):
    compilation = _v5_compilation(tmp_path, workflow_writer)
    authority = _authority(compilation.package)
    store = RunStore(tmp_path / f"home-{mutation}")
    _prepared, run_id = _admit(store, compilation, authority, key=mutation)
    run_directory = store.run_directory(run_id)
    target = run_directory / "provider-resolution.json"
    if mutation == "tamper":
        target.write_bytes(target.read_bytes() + b"\n")
    elif mutation == "omit":
        target.unlink()
    else:
        (run_directory / "unsealed-provider.json").write_text("{}", encoding="utf-8")

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        RunScheduler(store)._load_run_package(run_id)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_v5_snapshot_requires_authority_while_v4_snapshot_forbids_it(
    tmp_path, workflow_writer
):
    v5 = _v5_compilation(tmp_path / "v5", workflow_writer)
    authority = _authority(v5.package)
    store = RunStore(tmp_path / "home")

    with pytest.raises(ValueError, match="provider authority"):
        _prepare(store, v5, None)

    path = workflow_writer(
        tmp_path / "v4/source/workflows",
        name="legacy-format2",
        filename="legacy-format2.yaml",
        nodes=[{"id": "ask", "prompt": "hello", "model": "literal"}],
    )
    policy = b"language_compatibility: archon-2026-07\n"
    path.with_name("legacy-format2.hermes.yaml").write_bytes(policy)
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=policy,
        source="project",
        precedence=1,
    )
    v4 = compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=4,
    )

    with pytest.raises(ValueError, match="provider authority"):
        store.prepare_run_snapshot(
            v4.package,
            compilation=v4,
            trusted_package_digest=WorkflowPackageDigest(
                v4.composite_digest,
                v4.covered_relative_paths,
            ),
            provider_authority=authority,
        )


def test_conditional_reader_requires_exact_v5_members_and_rejects_v4_marker(
    tmp_path, workflow_writer
):
    v5 = _v5_compilation(tmp_path / "v5", workflow_writer)
    authority = _authority(v5.package)
    encoded = authority.canonical_bytes()
    digest = hashlib.sha256(encoded).hexdigest()

    assert (
        read_snapshot_provider_authority(
            language_snapshot=v5.package.language,
            resources={"provider_resolution_sha256": digest},
            authenticated_bytes={"provider-resolution.json": encoded},
            projected_digest=digest,
        )
        == authority
    )
    with pytest.raises(ValueError, match="identity changed"):
        read_snapshot_provider_authority(
            language_snapshot=v5.package.language,
            resources={},
            authenticated_bytes={"provider-resolution.json": encoded},
            projected_digest=digest,
        )

    path = workflow_writer(
        tmp_path / "v4/source/workflows",
        name="v4-reader",
        filename="v4-reader.yaml",
        nodes=[{"id": "ask", "prompt": "hello"}],
    )
    policy = b"language_compatibility: archon-2026-07\n"
    path.with_name("v4-reader.hermes.yaml").write_bytes(policy)
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=policy,
        source="project",
        precedence=1,
    )
    v4 = compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=4,
    )
    with pytest.raises(ValueError, match="forbidden"):
        read_snapshot_provider_authority(
            language_snapshot=v4.package.language,
            resources={"provider_resolution_sha256": digest},
            authenticated_bytes={"provider-resolution.json": encoded},
            projected_digest=digest,
        )


def test_v5_provider_projection_survives_checked_journal_crash_recovery(
    tmp_path, workflow_writer
):
    compilation = _v5_compilation(tmp_path, workflow_writer)
    authority = _authority(compilation.package)
    store = RunStore(tmp_path / "home")
    _prepared, run_id = _admit(store, compilation, authority, key="crash-recovery")
    run_directory = store.run_directory(run_id)
    expected = store.load_run(run_id)
    (run_directory / "run.json").write_text("{broken", encoding="utf-8")

    rebuilt = store.load_run(run_id)

    assert rebuilt == expected
    assert (
        rebuilt["provider_resolution_sha256"]
        == hashlib.sha256(
            (run_directory / "provider-resolution.json").read_bytes()
        ).hexdigest()
    )
    assert list(run_directory.glob("run.json.corrupt-*"))
