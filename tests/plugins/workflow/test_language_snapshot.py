from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import threading

import pytest
import yaml

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.language import (
    WorkflowLanguageCompatibilityError,
    make_language_snapshot,
    read_language_snapshot,
)
from plugins.workflow.resources import ResourceResolver
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow, load_workflow_snapshot
from plugins.workflow.scheduled_revalidation import sealed_snapshot_digest
from plugins.workflow.store import RunStore


def _profile_package(workflow_writer, root: Path, *, profile: str):
    path = workflow_writer(root / "package", name=f"{profile}-snapshot")
    if profile == "archon-2026-07":
        path.with_name(f"{path.stem}.hermes.yaml").write_text(
            "language_compatibility: archon-2026-07\n", encoding="utf-8"
        )
    return load_workflow(path)


def _start(store: RunStore, package, *, key: str):
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    return prepared, admitted


def _prepare_pre_language_snapshot(store: RunStore, package):
    prepared = store.prepare_run_snapshot(package)
    resources_path = prepared.staging_directory / "resources.json"
    resources = json.loads(resources_path.read_bytes())
    resources.pop("language")
    resources.pop("sealed_paths")
    encoded = json.dumps(resources, sort_keys=True, separators=(",", ":")).encode()
    resources_path.write_bytes(encoded)
    reserved_bytes = sum(
        path.stat().st_size
        for path in prepared.staging_directory.rglob("*")
        if path.is_file()
    )
    return replace(
        prepared,
        input_manifest_digest=sha256(encoded).hexdigest(),
        reserved_bytes=reserved_bytes,
        language=None,
        sealed_snapshot_digest=None,
    )


def _rewrite_resources(store: RunStore, run_id: str, mutate) -> None:
    resources_path = store.run_directory(run_id) / "resources.json"
    resources = json.loads(resources_path.read_bytes())
    mutate(resources)
    resources_path.write_text(json.dumps(resources), encoding="utf-8")


def _rewrite_language_for_sealed_package(store: RunStore, run_id: str) -> None:
    run_directory = store.run_directory(run_id)
    definition = run_directory / "definition.yaml"
    policy = run_directory / "policy.yaml"
    package = load_workflow_snapshot(
        definition,
        workflow_bytes=definition.read_bytes(),
        sidecar_bytes=policy.read_bytes() if policy.is_file() else None,
    )
    projection = store.load_run(run_id)
    resources_path = run_directory / "resources.json"
    resources = json.loads(resources_path.read_bytes())
    resources["language"] = make_language_snapshot(
        package, str(projection["definition_digest"])
    ).to_dict()
    resources_path.write_bytes(
        json.dumps(resources, sort_keys=True, separators=(",", ":")).encode()
    )


def _load_with_scheduler(store: RunStore, run_id: str):
    scheduler = RunScheduler(store)
    try:
        return scheduler._load_run_package(run_id)
    finally:
        scheduler.shutdown(deadline_seconds=2)


def test_admission_seals_package_bound_language_metadata(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    resources = json.loads(
        (prepared.staging_directory / "resources.json").read_text()
    )

    assert resources["language"]["effective_profile"] == "archon-2026-07"
    assert resources["language"]["normalizer_version"] == 1
    assert len(resources["language"]["normalized_definition_digest"]) == 64
    assert len(resources["language"]["semantic_fingerprint"]) == 64
    assert prepared.language == resources["language"]
    assert len(prepared.sealed_snapshot_digest) == 64
    assert resources["sealed_paths"] == sorted(resources["sealed_paths"])
    assert {
        "definition.yaml",
        "inputs.json",
        "policy.yaml",
        "resources.json",
    }.issubset(
        resources["sealed_paths"]
    )
    assert len(resources["sealed_paths"]) <= 4096


def test_admission_projects_the_same_bounded_language_metadata(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    prepared, admitted = _start(store, package, key="language-projection")

    run = store.load_run(admitted.run_id)

    assert run["language"] == prepared.language
    assert run["sealed_snapshot_digest"] == prepared.sealed_snapshot_digest
    assert set(run["language"]) == {
        "effective_profile",
        "normalizer_version",
        "normalized_definition_digest",
        "semantic_fingerprint",
    }


def test_language_snapshot_identity_is_independent_of_install_path(
    tmp_path, workflow_writer
):
    left = _profile_package(
        workflow_writer, tmp_path / "left", profile="archon-2026-07"
    )
    right = _profile_package(
        workflow_writer, tmp_path / "right", profile="archon-2026-07"
    )
    store = RunStore(tmp_path / "home")

    left_snapshot = store.prepare_run_snapshot(left)
    right_snapshot = store.prepare_run_snapshot(right)

    assert left_snapshot.definition_digest == right_snapshot.definition_digest
    assert left_snapshot.language == right_snapshot.language


def test_clone_prepared_snapshot_preserves_language_metadata(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)

    cloned = store.clone_prepared_snapshot(prepared)

    assert cloned.language == prepared.language
    assert cloned.sealed_snapshot_digest == prepared.sealed_snapshot_digest
    assert json.loads((cloned.staging_directory / "resources.json").read_bytes())[
        "language"
    ] == prepared.language


def test_resume_reads_sealed_definition_after_installed_source_changes(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    _prepared, admitted = _start(store, package, key="sealed-definition")
    package.workflow_path.write_text("not: [valid", encoding="utf-8")

    loaded = _load_with_scheduler(store, admitted.run_id)

    assert loaded.definition.name == "archon-2026-07-snapshot"
    assert loaded.language.effective_profile.value == "archon-2026-07"


def test_resume_ignores_post_admission_artifacts_outside_sealed_paths(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    _prepared, admitted = _start(store, package, key="post-admission-artifact")
    artifact = store.run_directory(admitted.run_id) / "artifacts" / "output.txt"
    artifact.parent.mkdir()
    artifact.write_text("legitimate runtime output\n", encoding="utf-8")
    node_output = (
        store.run_directory(admitted.run_id)
        / "nodes"
        / "start"
        / "attempt-1"
        / "stdout.txt"
    )
    node_output.parent.mkdir(parents=True)
    node_output.write_text("legitimate node output\n", encoding="utf-8")

    loaded = _load_with_scheduler(store, admitted.run_id)

    assert loaded.definition.name == "archon-2026-07-snapshot"


def test_resume_rejects_symlink_inside_mutable_output_root(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    _prepared, admitted = _start(store, package, key="mutable-output-symlink")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    artifacts = store.run_directory(admitted.run_id) / "artifacts"
    artifacts.mkdir()
    (artifacts / "escape").symlink_to(outside)

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


@pytest.mark.parametrize(
    ("resource_kind", "shadow_relative"),
    [
        pytest.param("script", "scripts/helper", id="script-extensionless"),
        pytest.param("mcp", "mcp/echo", id="mcp-extensionless"),
    ],
)
def test_resume_rejects_unsealed_resource_precedence_shadow(
    tmp_path, workflow_writer, resource_kind, shadow_relative
):
    root = tmp_path / resource_kind
    if resource_kind == "script":
        nodes = [{"id": "use", "script": "helper", "runtime": "uv"}]
        resource = root / "scripts" / "helper.py"
        resource_data = "print('sealed')\n"
        shadow_data = "print('shadow')\n"
    else:
        nodes = [{"id": "use", "prompt": "Use MCP", "mcp": "echo"}]
        resource = root / "mcp" / "echo.yaml"
        resource_data = "command: python\nargs: [-c, 'print(1)']\n"
        shadow_data = "command: python\nargs: [-c, 'print(2)']\n"
    resource.parent.mkdir(parents=True)
    resource.write_text(resource_data, encoding="utf-8")
    package = load_workflow(
        workflow_writer(root, name=f"{resource_kind}-shadow", nodes=nodes)
    )
    store = RunStore(tmp_path / "home")
    _prepared, admitted = _start(
        store, package, key=f"{resource_kind}-precedence-shadow"
    )
    shadow = store.run_directory(admitted.run_id) / shadow_relative
    shadow.write_text(shadow_data, encoding="utf-8")

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_concurrent_same_run_preparation_returns_independent_sealed_paths(
    tmp_path, workflow_writer, monkeypatch
):
    root = tmp_path / "concurrent-prepare"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    sealed = scripts / "helper.py"
    sealed.write_text("print('sealed')\n", encoding="utf-8")
    package = load_workflow(
        workflow_writer(
            root,
            name="concurrent-prepare",
            nodes=[{"id": "use", "script": "helper", "runtime": "uv"}],
        )
    )
    store = RunStore(tmp_path / "home")
    _prepared, admitted = _start(store, package, key="concurrent-prepare")
    scheduler = RunScheduler(store)
    barrier = threading.Barrier(2)
    original_load = scheduler._load_verified_run_package

    def synchronized_load(run_id: str):
        loaded = original_load(run_id)
        barrier.wait(timeout=5)
        return loaded

    monkeypatch.setattr(
        scheduler, "_load_verified_run_package", synchronized_load
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(
                future.result(timeout=10)
                for future in (
                    pool.submit(
                        scheduler._prepare_run_package, admitted.run_id, None
                    ),
                    pool.submit(
                        scheduler._prepare_run_package, admitted.run_id, None
                    ),
                )
            )
    finally:
        scheduler.shutdown(deadline_seconds=2)

    sealed_path_sets = tuple(result[2] for result in results)
    assert sealed_path_sets[0] is not None
    assert sealed_path_sets[0] == sealed_path_sets[1]
    run_directory = store.run_directory(admitted.run_id)
    (run_directory / "scripts" / "helper").write_text(
        "print('shadow')\n", encoding="utf-8"
    )
    assert {
        ResourceResolver(
            run_directory, sealed_paths=paths
        ).script("helper", runtime="uv").path.name
        for paths in sealed_path_sets
    } == {"helper.py"}


def test_resume_rejects_unjournaled_unknown_normalizer_rewrite(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    _prepared, admitted = _start(store, package, key="unknown-version")
    _rewrite_resources(
        store,
        admitted.run_id,
        lambda resources: resources["language"].__setitem__(
            "normalizer_version", 99
        ),
    )

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_resume_rejects_unknown_journaled_normalizer(tmp_path, workflow_writer):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    language = {**prepared.language, "normalizer_version": 99}
    resources_path = prepared.staging_directory / "resources.json"
    resources = json.loads(resources_path.read_bytes())
    resources["language"] = language
    encoded = json.dumps(resources, sort_keys=True, separators=(",", ":")).encode()
    resources_path.write_bytes(encoded)
    prepared = replace(
        prepared,
        input_manifest_digest=sha256(encoded).hexdigest(),
        language=language,
        sealed_snapshot_digest=sealed_snapshot_digest(
            prepared.staging_directory
        ),
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="unknown-journaled-normalizer",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_normalizer_version_unsupported"


def test_resume_rejects_sealed_profile_change(tmp_path, workflow_writer):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    _prepared, admitted = _start(store, package, key="profile-change")
    policy_path = store.run_directory(admitted.run_id) / "policy.yaml"
    policy_path.write_text(
        "language_compatibility: hermes-legacy\n", encoding="utf-8"
    )

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_resume_rejects_coherent_policy_downgrade_and_language_rewrite(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    _prepared, admitted = _start(store, package, key="coherent-policy-downgrade")
    policy_path = store.run_directory(admitted.run_id) / "policy.yaml"
    policy_path.write_text(
        "language_compatibility: hermes-legacy\n", encoding="utf-8"
    )
    _rewrite_language_for_sealed_package(store, admitted.run_id)

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_resume_rejects_coherent_definition_and_language_rewrite(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    _prepared, admitted = _start(store, package, key="coherent-definition-rewrite")
    definition_path = store.run_directory(admitted.run_id) / "definition.yaml"
    definition = yaml.safe_load(definition_path.read_text(encoding="utf-8"))
    definition["nodes"][0]["bash"] = "printf forged"
    definition_path.write_text(
        yaml.safe_dump(definition, sort_keys=False), encoding="utf-8"
    )
    _rewrite_language_for_sealed_package(store, admitted.run_id)

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_resume_rejects_reencoded_policy_with_unchanged_semantics(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    _prepared, admitted = _start(store, package, key="reencoded-policy")
    policy_path = store.run_directory(admitted.run_id) / "policy.yaml"
    policy_path.write_text(
        policy_path.read_text(encoding="utf-8") + "# unchanged semantics\n",
        encoding="utf-8",
    )

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_resume_rejects_reencoded_definition_with_unchanged_semantics(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    _prepared, admitted = _start(store, package, key="reencoded-definition")
    definition_path = store.run_directory(admitted.run_id) / "definition.yaml"
    definition_path.write_text(
        definition_path.read_text(encoding="utf-8") + "# unchanged semantics\n",
        encoding="utf-8",
    )

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_resume_rejects_reencoded_resources_with_unchanged_metadata(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    _prepared, admitted = _start(store, package, key="reencoded-resources")
    resources_path = store.run_directory(admitted.run_id) / "resources.json"
    resources = json.loads(resources_path.read_bytes())
    resources_path.write_text(json.dumps(resources, indent=2), encoding="utf-8")

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_resume_rejects_tampered_digest_covered_package_resource(
    tmp_path, workflow_writer
):
    root = tmp_path / "package-resource"
    path = workflow_writer(
        root,
        name="archon-resource-snapshot",
        nodes=[{"id": "start", "command": "inspect"}],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    (root / "commands").mkdir()
    (root / "commands" / "inspect.md").write_text(
        "Inspect admitted bytes.\n", encoding="utf-8"
    )
    package = load_workflow(path)
    store = RunStore(tmp_path / "home")
    _prepared, admitted = _start(store, package, key="tampered-package-resource")
    (store.run_directory(admitted.run_id) / "commands" / "inspect.md").write_text(
        "Forged command bytes.\n", encoding="utf-8"
    )

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


@pytest.mark.parametrize(
    "field",
    ["normalized_definition_digest", "semantic_fingerprint"],
)
def test_resume_rejects_changed_language_digest(
    tmp_path, workflow_writer, field
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    _prepared, admitted = _start(store, package, key=f"changed-{field}")
    _rewrite_resources(
        store,
        admitted.run_id,
        lambda resources: resources["language"].__setitem__(field, "0" * 64),
    )

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_legacy_snapshot_without_language_metadata_still_loads(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="hermes-legacy")
    store = RunStore(tmp_path / "home")
    prepared = _prepare_pre_language_snapshot(store, package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="legacy-v0",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None

    loaded = _load_with_scheduler(store, admitted.run_id)

    assert loaded.language.effective_profile.value == "hermes-legacy"


def test_pre_language_legacy_snapshot_rejects_executable_shadow(
    tmp_path, workflow_writer
):
    root = tmp_path / "legacy-script"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "helper.py").write_text("print('sealed')\n", encoding="utf-8")
    package = load_workflow(
        workflow_writer(
            root,
            name="legacy-script-shadow",
            nodes=[{"id": "use", "script": "helper", "runtime": "uv"}],
        )
    )
    store = RunStore(tmp_path / "home")
    prepared = _prepare_pre_language_snapshot(store, package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="legacy-script-shadow",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    (store.run_directory(admitted.run_id) / "scripts" / "helper").write_text(
        "print('shadow')\n", encoding="utf-8"
    )

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_new_legacy_snapshot_without_language_metadata_fails_closed(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="hermes-legacy")
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    resources_path = prepared.staging_directory / "resources.json"
    resources = json.loads(resources_path.read_bytes())
    resources.pop("language")
    encoded = json.dumps(resources, sort_keys=True, separators=(",", ":")).encode()
    resources_path.write_bytes(encoded)
    prepared = replace(
        prepared,
        input_manifest_digest=sha256(encoded).hexdigest(),
        language=None,
        sealed_snapshot_digest=sealed_snapshot_digest(
            prepared.staging_directory,
            relative_paths=resources["sealed_paths"],
        ),
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="new-legacy-without-language",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_language_snapshot_missing"


def test_resume_rejects_projected_and_sealed_language_disagreement(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package)
    prepared = replace(prepared, language=None)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="projection-language-disagreement",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_archon_snapshot_without_language_metadata_fails_closed(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    prepared = _prepare_pre_language_snapshot(store, package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="archon-without-language",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_language_snapshot_missing"


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda value: value.__setitem__("unexpected", "field"),
            "workflow_language_snapshot_invalid",
        ),
        (
            lambda value: value.__setitem__("effective_profile", "future-profile"),
            "workflow_language_profile_unsupported",
        ),
        (
            lambda value: value.__setitem__("normalizer_version", True),
            "workflow_normalizer_version_unsupported",
        ),
        (
            lambda value: value.__setitem__(
                "normalized_definition_digest", "A" * 64
            ),
            "workflow_language_snapshot_invalid",
        ),
    ],
)
def test_language_snapshot_parser_rejects_untrusted_shape(mutate, expected_code):
    value = {
        "effective_profile": "archon-2026-07",
        "normalizer_version": 1,
        "normalized_definition_digest": "a" * 64,
        "semantic_fingerprint": "b" * 64,
    }
    mutate(value)

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        read_language_snapshot(value)

    assert exc.value.code == expected_code
