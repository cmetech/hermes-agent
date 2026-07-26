from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import threading

import pytest
import yaml

import plugins.workflow.scheduler as scheduler_module
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


LEGACY_STORE_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "store"
    / "pre-production-amendment-v2.0.9"
)


def _copy_v209_store(tmp_path: Path) -> RunStore:
    manifest = json.loads(
        (LEGACY_STORE_FIXTURE / "fixture-manifest.json").read_text(encoding="utf-8")
    )
    home = tmp_path / "v209-home"
    workflows = home / "workflows"
    workflows.mkdir(parents=True)
    shutil.copy2(
        LEGACY_STORE_FIXTURE / "admission.db",
        workflows / "admission.sqlite3",
    )
    shutil.copytree(LEGACY_STORE_FIXTURE / "runs", workflows / "runs")
    database = workflows / "admission.sqlite3"
    with sqlite3.connect(database) as connection:
        legacy_directory = connection.execute(
            "SELECT run_directory FROM runs WHERE run_id='migration-run'"
        ).fetchone()[0]
        legacy_prefix = str(manifest["legacy_run_directory_prefix"])
        relocated = str(workflows) + legacy_directory[len(legacy_prefix) :]
        connection.execute(
            "UPDATE runs SET run_directory=? WHERE run_id='migration-run'",
            (relocated,),
        )
    return RunStore(home)


def _legacy_transitive_package(workflow_writer, root: Path):
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "helper.py").write_text(
        "print('sealed script')\n", encoding="utf-8"
    )
    (root / "mcp").mkdir()
    (root / "mcp" / "echo.yaml").write_text(
        "command: python\nargs: [servers/echo.py]\n", encoding="utf-8"
    )
    (root / "servers").mkdir()
    (root / "servers" / "echo.py").write_text(
        "print('sealed server')\n", encoding="utf-8"
    )
    path = workflow_writer(
        root / "workflows",
        name="legacy-transitive",
        filename="legacy-transitive.yaml",
        nodes=[
            {"id": "script", "script": "helper", "runtime": "uv"},
            {
                "id": "mcp",
                "prompt": "Use MCP",
                "mcp": "mcp/echo.yaml",
                "depends_on": ["script"],
            },
        ],
    )
    return load_workflow(path)


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


def _pre_language_seals(prepared) -> dict[str, str]:
    staging = prepared.staging_directory
    return {
        "sealed_definition_digest": sha256(
            (staging / "definition.yaml").read_bytes()
        ).hexdigest(),
        "sealed_policy_digest": sha256(
            (staging / "policy.yaml").read_bytes()
            if (staging / "policy.yaml").is_file()
            else b"{}\n"
        ).hexdigest(),
        "sealed_input_digest": sha256(
            (staging / "resources.json").read_bytes()
        ).hexdigest(),
        "sealed_snapshot_digest": sealed_snapshot_digest(staging),
    }
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


def _load_with_scheduler(
    store: RunStore, run_id: str, *, historical_projection: bool = False
):
    scheduler = RunScheduler(store)
    original_load_run = store.load_run
    if historical_projection:
        projection = json.loads(json.dumps(original_load_run(run_id)))
        projection.pop("snapshot_format_version", None)

        def load_historical(candidate: str):
            if candidate == run_id:
                return json.loads(json.dumps(projection))
            return original_load_run(candidate)

        store.load_run = load_historical  # type: ignore[method-assign]
    try:
        return scheduler._load_run_package(run_id)
    finally:
        store.load_run = original_load_run  # type: ignore[method-assign]
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


def test_pre_language_snapshot_with_reconstructible_package_digest_still_loads(
    tmp_path, workflow_writer
):
    name = "reconstructible-legacy"
    package = load_workflow(
        workflow_writer(
            tmp_path / "package", name=name, filename=f"{name}.yaml"
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
            idempotency_key="legacy-v0",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None

    loaded = _load_with_scheduler(
        store, admitted.run_id, historical_projection=True
    )

    assert loaded.language.effective_profile.value == "hermes-legacy"


def test_unverifiable_pre_language_snapshot_never_reaches_workflow_parser(
    tmp_path, workflow_writer, monkeypatch
):
    package = _profile_package(workflow_writer, tmp_path, profile="hermes-legacy")
    store = RunStore(tmp_path / "home")
    prepared = replace(
        _prepare_pre_language_snapshot(store, package),
        definition_digest="0" * 64,
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="legacy-parser-order",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    parse_calls = 0

    def forbidden_parser(*_args, **_kwargs):
        nonlocal parse_calls
        parse_calls += 1
        raise AssertionError("unverified workflow bytes reached the YAML parser")

    monkeypatch.setattr(scheduler_module, "load_workflow_snapshot", forbidden_parser)

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(
            store, admitted.run_id, historical_projection=True
        )

    assert exc.value.code == "workflow_legacy_snapshot_unverifiable"
    assert parse_calls == 0


def test_reconstructible_pre_language_snapshot_is_verified_before_parser(
    tmp_path, workflow_writer, monkeypatch
):
    name = "legacy-parser-order-valid"
    package = load_workflow(
        workflow_writer(
            tmp_path / "package", name=name, filename=f"{name}.yaml"
        )
    )
    store = RunStore(tmp_path / "home")
    prepared = _prepare_pre_language_snapshot(store, package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="legacy-parser-order-valid",
            concurrency_key=name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    events: list[str] = []
    original_digest = RunScheduler._legacy_package_digest_for_identity
    original_parser = scheduler_module.load_workflow_snapshot

    def recording_digest(*args, **kwargs):
        digest = original_digest(*args, **kwargs)
        if digest == prepared.definition_digest:
            events.append("verified")
        return digest

    def recording_parser(*args, **kwargs):
        events.append("parsed")
        return original_parser(*args, **kwargs)

    monkeypatch.setattr(
        RunScheduler,
        "_legacy_package_digest_for_identity",
        staticmethod(recording_digest),
    )
    monkeypatch.setattr(
        scheduler_module, "load_workflow_snapshot", recording_parser
    )

    loaded = _load_with_scheduler(
        store, admitted.run_id, historical_projection=True
    )

    assert loaded.definition.name == name
    assert events.index("verified") < events.index("parsed")


def test_pre_language_file_replacement_cannot_authenticate_different_parser_bytes(
    tmp_path, workflow_writer, monkeypatch
):
    name = "legacy-parser-byte-binding"
    package = load_workflow(
        workflow_writer(
            tmp_path / "package", name=name, filename=f"{name}.yaml"
        )
    )
    store = RunStore(tmp_path / "home")
    prepared = _prepare_pre_language_snapshot(store, package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="legacy-parser-byte-binding",
            concurrency_key=name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    definition = store.run_directory(admitted.run_id) / "definition.yaml"
    authenticated_bytes = definition.read_bytes()
    document = yaml.safe_load(authenticated_bytes)
    document["nodes"][0]["bash"] = "printf forged"
    definition.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    original_inventory = RunScheduler._legacy_raw_package_paths
    original_parser = scheduler_module.load_workflow_snapshot
    parse_calls = 0

    def restore_after_initial_read(run_directory):
        paths = original_inventory(run_directory)
        definition.write_bytes(authenticated_bytes)
        return paths

    def recording_parser(*args, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        return original_parser(*args, **kwargs)

    monkeypatch.setattr(
        RunScheduler,
        "_legacy_raw_package_paths",
        staticmethod(restore_after_initial_read),
    )
    monkeypatch.setattr(
        scheduler_module, "load_workflow_snapshot", recording_parser
    )

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(
            store, admitted.run_id, historical_projection=True
        )

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"
    assert parse_calls == 0


def test_checked_in_v209_snapshot_loads_through_full_scheduler_path(
    tmp_path, monkeypatch
):
    store = _copy_v209_store(tmp_path)
    expected_digest = str(store.load_run("migration-run")["definition_digest"])
    events: list[str] = []
    original_digest = RunScheduler._legacy_package_digest_for_identity
    original_parser = scheduler_module.load_workflow_snapshot

    def recording_digest(*args, **kwargs):
        digest = original_digest(*args, **kwargs)
        if digest == expected_digest:
            events.append("verified")
        return digest

    def recording_parser(*args, **kwargs):
        events.append("parsed")
        return original_parser(*args, **kwargs)

    monkeypatch.setattr(
        RunScheduler,
        "_legacy_package_digest_for_identity",
        staticmethod(recording_digest),
    )
    monkeypatch.setattr(
        scheduler_module, "load_workflow_snapshot", recording_parser
    )
    scheduler = RunScheduler(store)
    try:
        package = scheduler._load_run_package("migration-run")
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert package.definition.name == "migration-fixture"
    assert package.sidecar["outward_action_nodes"] == ("start",)
    assert events.index("verified") < events.index("parsed")


def test_pre_language_raw_authentication_covers_transitive_package_resources(
    tmp_path, workflow_writer
):
    package = _legacy_transitive_package(workflow_writer, tmp_path / "package")
    store = RunStore(tmp_path / "home")
    prepared = _prepare_pre_language_snapshot(store, package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="legacy-transitive-valid",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    scheduler = RunScheduler(store)
    original_load_run = store.load_run
    projection = json.loads(json.dumps(original_load_run(admitted.run_id)))
    projection.pop("snapshot_format_version", None)
    store.load_run = lambda _run_id: json.loads(json.dumps(projection))  # type: ignore[method-assign]
    try:
        loaded, sealed_paths = scheduler._load_verified_run_package(admitted.run_id)
    finally:
        store.load_run = original_load_run  # type: ignore[method-assign]
        scheduler.shutdown(deadline_seconds=2)

    assert loaded.definition.name == "legacy-transitive"
    assert sealed_paths == frozenset(
        {
            "definition.yaml",
            "mcp/echo.yaml",
            "scripts/helper.py",
            "servers/echo.py",
        }
    )


@pytest.mark.parametrize(
    "mutation",
    ["omit-script", "omit-mcp-transitive", "inject-package-resource"],
)
def test_changed_pre_language_transitive_closure_is_rejected_before_parser(
    tmp_path, workflow_writer, monkeypatch, mutation
):
    package = _legacy_transitive_package(workflow_writer, tmp_path / "package")
    store = RunStore(tmp_path / "home")
    prepared = _prepare_pre_language_snapshot(store, package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=f"legacy-transitive-{mutation}",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    run_directory = store.run_directory(admitted.run_id)
    if mutation == "omit-script":
        (run_directory / "scripts" / "helper.py").unlink()
    elif mutation == "omit-mcp-transitive":
        (run_directory / "servers" / "echo.py").unlink()
    else:
        (run_directory / "scripts" / "injected.py").write_text(
            "print('injected')\n", encoding="utf-8"
        )
    parse_calls = 0

    def forbidden_parser(*_args, **_kwargs):
        nonlocal parse_calls
        parse_calls += 1
        raise AssertionError("changed package closure reached the YAML parser")

    monkeypatch.setattr(scheduler_module, "load_workflow_snapshot", forbidden_parser)

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(
            store, admitted.run_id, historical_projection=True
        )

    assert exc.value.code == "workflow_legacy_snapshot_unverifiable"
    assert parse_calls == 0


def test_checked_in_v209_snapshot_package_identity_is_reconstructible():
    run_directory = (
        LEGACY_STORE_FIXTURE
        / "runs"
        / "migration-fixture"
        / "migration-run"
    )
    projection = json.loads((run_directory / "run.json").read_bytes())

    digest = RunScheduler._legacy_package_digest_for_identity(
        run_directory,
        sealed_paths=frozenset({"definition.yaml", "policy.yaml"}),
        workflow_identity="migration-fixture.yaml",
    )

    assert digest == projection["definition_digest"]


@pytest.mark.parametrize(
    "identity",
    ["wrong-name.yaml", "../migration-fixture.yaml", "/migration-fixture.yaml"],
)
def test_v209_snapshot_rejects_wrong_or_escaping_path_identity(identity):
    run_directory = (
        LEGACY_STORE_FIXTURE
        / "runs"
        / "migration-fixture"
        / "migration-run"
    )
    projection = json.loads((run_directory / "run.json").read_bytes())

    digest = RunScheduler._legacy_package_digest_for_identity(
        run_directory,
        sealed_paths=frozenset({"definition.yaml", "policy.yaml"}),
        workflow_identity=identity,
    )

    assert digest != projection["definition_digest"]


def test_v209_snapshot_reconstruction_rejects_altered_definition(tmp_path):
    source = (
        LEGACY_STORE_FIXTURE
        / "runs"
        / "migration-fixture"
        / "migration-run"
    )
    run_directory = tmp_path / "migration-run"
    run_directory.mkdir()
    for relative in ("definition.yaml", "policy.yaml", "run.json"):
        (run_directory / relative).write_bytes((source / relative).read_bytes())
    projection = json.loads((run_directory / "run.json").read_bytes())
    (run_directory / "definition.yaml").write_text(
        "name: migration-fixture\nnodes: [{id: start, bash: forged}]\n",
        encoding="utf-8",
    )

    digest = RunScheduler._legacy_package_digest_for_identity(
        run_directory,
        sealed_paths=frozenset({"definition.yaml", "policy.yaml"}),
        workflow_identity="migration-fixture.yaml",
    )

    assert digest != projection["definition_digest"]


def test_verifiable_pre_language_scheduled_snapshot_still_loads(
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
            trigger_source="cron",
            idempotency_key="legacy-scheduled-v0",
            concurrency_key=package.definition.name,
            run_metadata=_pre_language_seals(prepared),
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    output = store.run_directory(admitted.run_id) / "nodes" / "start" / "stdout.txt"
    output.parent.mkdir(parents=True)
    output.write_text("legitimate mutable output\n", encoding="utf-8")

    loaded = _load_with_scheduler(
        store, admitted.run_id, historical_projection=True
    )

    assert loaded.language.effective_profile.value == "hermes-legacy"


def test_pre_language_snapshot_rejects_altered_definition(
    tmp_path, workflow_writer
):
    name = "legacy-definition-integrity"
    path = workflow_writer(tmp_path / "package", name=name, filename=f"{name}.yaml")
    package = load_workflow(path)
    store = RunStore(tmp_path / "home")
    prepared = _prepare_pre_language_snapshot(store, package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="legacy-definition-integrity",
            concurrency_key=name,
            run_metadata=_pre_language_seals(prepared),
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    definition_path = store.run_directory(admitted.run_id) / "definition.yaml"
    definition = yaml.safe_load(definition_path.read_text(encoding="utf-8"))
    definition["nodes"][0]["bash"] = "printf forged"
    definition_path.write_text(
        yaml.safe_dump(definition, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(
            store, admitted.run_id, historical_projection=True
        )

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_pre_language_snapshot_rejects_altered_package_resource(
    tmp_path, workflow_writer
):
    name = "legacy-resource-integrity"
    root = tmp_path / "package"
    command = root / "commands" / "inspect.md"
    command.parent.mkdir(parents=True)
    command.write_text("Inspect admitted bytes.\n", encoding="utf-8")
    package = load_workflow(
        workflow_writer(
            root,
            name=name,
            filename=f"{name}.yaml",
            nodes=[{"id": "inspect", "command": "inspect"}],
        )
    )
    store = RunStore(tmp_path / "home")
    prepared = _prepare_pre_language_snapshot(store, package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="legacy-resource-integrity",
            concurrency_key=name,
            run_metadata=_pre_language_seals(prepared),
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    sealed_command = store.run_directory(admitted.run_id) / "commands" / "inspect.md"
    sealed_command.write_text("Execute forged instructions.\n", encoding="utf-8")

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(
            store, admitted.run_id, historical_projection=True
        )

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_unverifiable_pre_language_snapshot_requires_readmission(
    tmp_path, workflow_writer
):
    package = _profile_package(workflow_writer, tmp_path, profile="hermes-legacy")
    store = RunStore(tmp_path / "home")
    prepared = replace(
        _prepare_pre_language_snapshot(store, package),
        definition_digest="0" * 64,
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="legacy-unverifiable",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    assert store.load_run(admitted.run_id)["snapshot_format_version"] == 1

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(
            store, admitted.run_id, historical_projection=True
        )

    assert exc.value.code == "workflow_legacy_snapshot_unverifiable"
    assert "re-trust" in str(exc.value)
    assert "new run" in str(exc.value)


def test_pre_language_snapshot_rejects_ambiguous_reconstructed_identities(
    tmp_path, workflow_writer, monkeypatch
):
    name = "ambiguous-legacy"
    package = load_workflow(
        workflow_writer(
            tmp_path / "package", name=name, filename=f"{name}.yaml"
        )
    )
    store = RunStore(tmp_path / "home")
    prepared = _prepare_pre_language_snapshot(store, package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="ambiguous-legacy",
            concurrency_key=name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    monkeypatch.setattr(
        RunScheduler,
        "_legacy_package_digest_for_identity",
        staticmethod(lambda *_args, **_kwargs: prepared.definition_digest),
    )

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(
            store, admitted.run_id, historical_projection=True
        )

    assert exc.value.code == "workflow_legacy_snapshot_unverifiable"


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
        _load_with_scheduler(
            store, admitted.run_id, historical_projection=True
        )

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
    assert store.load_run(admitted.run_id)["snapshot_format_version"] == 1

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id)

    assert exc.value.code == "workflow_language_snapshot_missing"


def test_current_snapshot_cannot_downgrade_into_pre_language_fallback(
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
        sealed_snapshot_digest=None,
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="current-format-downgrade",
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
