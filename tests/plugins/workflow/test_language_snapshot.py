from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.language import (
    WorkflowLanguageCompatibilityError,
    read_language_snapshot,
)
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
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
    )


def _rewrite_resources(store: RunStore, run_id: str, mutate) -> None:
    resources_path = store.run_directory(run_id) / "resources.json"
    resources = json.loads(resources_path.read_bytes())
    mutate(resources)
    resources_path.write_text(json.dumps(resources), encoding="utf-8")


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


def test_admission_projects_the_same_bounded_language_metadata(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store = RunStore(tmp_path / "home")
    prepared, admitted = _start(store, package, key="language-projection")

    run = store.load_run(admitted.run_id)

    assert run["language"] == prepared.language
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


def test_resume_rejects_unknown_pinned_normalizer(tmp_path, workflow_writer):
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

    assert exc.value.code == "workflow_language_snapshot_mismatch"


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

    assert exc.value.code == "workflow_language_snapshot_mismatch"


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
