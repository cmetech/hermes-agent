from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading

import pytest
import yaml

import plugins.workflow.scheduler as scheduler_module
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.catalog_api import (
    CATALOG_MAX_RESOURCE_FILE_BYTES,
    CATALOG_MAX_RESOURCE_FILES,
    CATALOG_MAX_RESOURCE_TOTAL_BYTES,
)
from plugins.workflow.language import (
    WorkflowLanguageCompatibilityError,
    WorkflowLanguageProfile,
    make_language_snapshot,
    read_language_snapshot,
)
from plugins.workflow.resources import (
    AuthenticatedExecutionMaterializer,
    ResourceResolver,
)
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow, load_workflow_snapshot
from plugins.workflow.scheduled_revalidation import sealed_snapshot_digest
from plugins.workflow.store import RunStore
from plugins.workflow.trust import WorkflowResourceReadBudget


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


def _legacy_two_node_package(workflow_writer, root: Path) -> Path:
    return workflow_writer(
        root / "package",
        name="legacy-two-node",
        nodes=[
            {"id": "cached", "bash": "true"},
            {
                "id": "fail",
                "bash": "false",
                "depends_on": ["cached"],
            },
        ],
    )


def _admit(package, hermes_home: Path) -> tuple[RunStore, str]:
    store = RunStore(hermes_home)
    _prepared, admitted = _start(
        store,
        package,
        key=f"{package.definition.name}-admission",
    )
    assert admitted.run_id is not None
    return store, admitted.run_id


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


def _legacy_skill_package(workflow_writer, root: Path, monkeypatch):
    monkeypatch.setattr(
        "agent.skill_commands.build_preloaded_skills_prompt",
        lambda names, task_id=None: (f"SEALED:{','.join(names)}", names, []),
    )
    return load_workflow(
        workflow_writer(
            root,
            name="legacy-skill-auth",
            filename="legacy-skill-auth.yaml",
            nodes=[
                {
                    "id": "analyze",
                    "prompt": "Analyze",
                    "skills": ["reports"],
                    "agents": {
                        "reviewer": {
                            "description": "review",
                            "prompt": "inspect",
                            "skills": ["reviewing"],
                        }
                    },
                }
            ],
        )
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


def test_verified_load_rejects_normalizer_drift_for_authenticated_bytes(
    tmp_path, workflow_writer, monkeypatch
):
    package = _profile_package(workflow_writer, tmp_path, profile="archon-2026-07")
    store, run_id = _admit(package, tmp_path / "home")
    scheduler = RunScheduler(store)
    original_parser = scheduler_module.load_workflow_snapshot

    def parse_with_normalizer_drift(*args, **kwargs):
        parsed = original_parser(*args, **kwargs)
        drifted_digest = "0" * 64
        assert parsed.language.normalized_definition_digest != drifted_digest
        return replace(
            parsed,
            language=replace(
                parsed.language,
                normalized_definition_digest=drifted_digest,
            ),
        )

    monkeypatch.setattr(
        scheduler_module,
        "load_workflow_snapshot",
        parse_with_normalizer_drift,
    )
    try:
        with pytest.raises(WorkflowLanguageCompatibilityError) as exc_info:
            scheduler._load_verified_run_package(run_id)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert exc_info.value.code == "workflow_language_snapshot_mismatch"


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


def test_legacy_node_run_root_write_does_not_break_next_verified_load(
    tmp_path, workflow_writer
):
    package = load_workflow(_legacy_two_node_package(workflow_writer, tmp_path))
    store, run_id = _admit(package, tmp_path / "home")
    scheduler = RunScheduler(store)
    run_directory = store.run_directory(run_id)
    state = run_directory / "state.txt"
    state.write_text("legacy state\n", encoding="utf-8")

    try:
        loaded = scheduler._load_verified_run_package(run_id)[0]
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert (
        loaded.language.effective_profile
        is WorkflowLanguageProfile.HERMES_LEGACY
    )
    assert state.read_text(encoding="utf-8") == "legacy state\n"


@pytest.mark.parametrize("entry_kind", ["symlink", "special"])
def test_legacy_unsealed_non_regular_entry_still_fails_verified_load(
    tmp_path, workflow_writer, entry_kind
):
    package = load_workflow(_legacy_two_node_package(workflow_writer, tmp_path))
    store, run_id = _admit(package, tmp_path / "home")
    scheduler = RunScheduler(store)
    run_directory = store.run_directory(run_id)
    entry = run_directory / f"unsealed-{entry_kind}"
    if entry_kind == "symlink":
        outside = tmp_path / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        try:
            entry.symlink_to(outside)
        except OSError:
            pytest.skip("symlinks unavailable")
    else:
        try:
            os.mkfifo(entry)
        except (AttributeError, OSError):
            pytest.skip("special files unavailable")

    try:
        with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
            scheduler._load_verified_run_package(run_id)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_legacy_changed_sealed_member_still_fails_verified_load(
    tmp_path, workflow_writer
):
    package = load_workflow(_legacy_two_node_package(workflow_writer, tmp_path))
    store, run_id = _admit(package, tmp_path / "home")
    scheduler = RunScheduler(store)
    definition = store.run_directory(run_id) / "definition.yaml"
    definition.write_bytes(definition.read_bytes() + b"# changed\n")

    try:
        with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
            scheduler._load_verified_run_package(run_id)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_legacy_unsealed_regular_file_never_becomes_execution_authority(
    tmp_path, workflow_writer
):
    package = load_workflow(_legacy_two_node_package(workflow_writer, tmp_path))
    store, run_id = _admit(package, tmp_path / "home")
    scheduler = RunScheduler(store)
    run_directory = store.run_directory(run_id)
    shadow = run_directory / "commands" / "shadow.md"
    shadow.parent.mkdir()
    shadow.write_text("Unsealed command.\n", encoding="utf-8")

    try:
        _loaded, sealed_paths, sealed_bytes = (
            scheduler._load_verified_run_package(run_id)
        )
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert "commands/shadow.md" not in sealed_paths
    assert "commands/shadow.md" not in sealed_bytes
    with pytest.raises(FileNotFoundError, match="command resource is missing"):
        ResourceResolver(
            run_directory,
            sealed_paths=sealed_paths,
            sealed_bytes=sealed_bytes,
        ).command("shadow")


def test_verified_always_run_nodes_uses_authenticated_definition(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path / "always-run-package",
        name="verified-always-run",
        nodes=[
            {"id": "cached", "bash": "true"},
            {"id": "refresh", "bash": "true", "always_run": True},
        ],
    )
    store, run_id = _admit(load_workflow(path), tmp_path / "home")
    scheduler = RunScheduler(store)

    try:
        always_run_nodes = scheduler.verified_always_run_nodes(run_id)
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert always_run_nodes == frozenset({"refresh"})


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
        loaded, sealed_paths, sealed_bytes = scheduler._load_verified_run_package(
            admitted.run_id
        )
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
            "inputs.json",
            "resources.json",
        }
    )
    assert frozenset(sealed_bytes) == sealed_paths | {
        "inputs.json",
        "resources.json",
    }


@pytest.mark.parametrize(
    ("relative", "mutation"),
    [
        ("node-skills/analyze.md", "changed"),
        ("node-skills/analyze.md", "missing"),
        ("node-skills/extra.md", "extra"),
        ("node-agent-skills/analyze/reviewer.md", "changed"),
        ("node-agent-skills/analyze/reviewer.md", "symlink"),
    ],
)
def test_pre_language_snapshot_authenticates_exact_skill_bytes_before_parse(
    tmp_path, workflow_writer, monkeypatch, relative, mutation
):
    package = _legacy_skill_package(workflow_writer, tmp_path / "package", monkeypatch)
    store = RunStore(tmp_path / "home")
    prepared = _prepare_pre_language_snapshot(store, package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=f"legacy-skill-{mutation}-{relative}",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    target = store.run_directory(admitted.run_id) / relative
    if mutation == "changed":
        target.write_text("FORGED", encoding="utf-8")
    elif mutation == "missing":
        target.unlink()
    elif mutation == "extra":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("INJECTED", encoding="utf-8")
    else:
        original = target.read_bytes()
        external = tmp_path / "external-skill.md"
        external.write_bytes(original)
        target.unlink()
        try:
            target.symlink_to(external)
        except OSError:
            pytest.skip("symlinks unavailable")

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id, historical_projection=True)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_pre_language_snapshot_returns_authenticated_skill_bytes(
    tmp_path, workflow_writer, monkeypatch
):
    package = _legacy_skill_package(workflow_writer, tmp_path / "package", monkeypatch)
    store = RunStore(tmp_path / "home")
    prepared = _prepare_pre_language_snapshot(store, package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="legacy-skill-byte-authority",
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
        _loaded, sealed_paths, sealed_bytes = scheduler._load_verified_run_package(
            admitted.run_id
        )
    finally:
        store.load_run = original_load_run  # type: ignore[method-assign]
        scheduler.shutdown(deadline_seconds=2)

    expected = {
        "node-skills/analyze.md",
        "node-agent-skills/analyze/reviewer.md",
    }
    assert expected.issubset(sealed_paths)
    assert sealed_bytes["node-skills/analyze.md"] == b"SEALED:reports"
    assert sealed_bytes["node-agent-skills/analyze/reviewer.md"] == b"SEALED:reviewing"


@pytest.mark.parametrize("seal_mode", ["raw", "direct", "whole"])
def test_pre_language_same_size_skill_substitution_fails_for_every_seal_mode(
    tmp_path, workflow_writer, monkeypatch, seal_mode
):
    package = _legacy_skill_package(workflow_writer, tmp_path / "package", monkeypatch)
    store = RunStore(tmp_path / "home")
    prepared = _prepare_pre_language_snapshot(store, package)
    metadata = None
    if seal_mode == "direct":
        metadata = {
            key: value
            for key, value in _pre_language_seals(prepared).items()
            if key != "sealed_snapshot_digest"
        }
    elif seal_mode == "whole":
        metadata = _pre_language_seals(prepared)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=f"legacy-same-size-skill-{seal_mode}",
            concurrency_key=package.definition.name,
            run_metadata=metadata,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    skill = store.run_directory(admitted.run_id) / "node-skills" / "analyze.md"
    assert len(skill.read_bytes()) == len(b"FORGED:reports")
    skill.write_bytes(b"FORGED:reports")

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id, historical_projection=True)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


@pytest.mark.parametrize("seal_mode", ["raw", "direct", "whole"])
@pytest.mark.parametrize("target_kind", ["mcp", "server"])
def test_historical_mcp_consumers_rebind_same_size_bytes_for_every_seal_mode(
    tmp_path, workflow_writer, seal_mode, target_kind
):
    package = _legacy_transitive_package(workflow_writer, tmp_path / "package")
    store = RunStore(tmp_path / "home")
    prepared = _prepare_pre_language_snapshot(store, package)
    metadata = None
    if seal_mode == "direct":
        metadata = {
            key: value
            for key, value in _pre_language_seals(prepared).items()
            if key != "sealed_snapshot_digest"
        }
    elif seal_mode == "whole":
        metadata = _pre_language_seals(prepared)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=f"legacy-mcp-rebind-{seal_mode}-{target_kind}",
            concurrency_key=package.definition.name,
            run_metadata=metadata,
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
        _loaded, sealed_paths, sealed_bytes = scheduler._load_verified_run_package(
            admitted.run_id
        )
    finally:
        store.load_run = original_load_run  # type: ignore[method-assign]
        scheduler.shutdown(deadline_seconds=2)
    run = store.run_directory(admitted.run_id)
    if target_kind == "mcp":
        target = run / "mcp" / "echo.yaml"
        original = target.read_bytes()
        forged = original.replace(b"python", b"pyth0n")
    else:
        target = run / "servers" / "echo.py"
        original = target.read_bytes()
        forged = original.replace(b"sealed", b"forged")
    assert forged != original
    assert len(forged) == len(original)
    target.write_bytes(forged)

    materializer = AuthenticatedExecutionMaterializer()
    try:
        servers = ResourceResolver(
            run,
            sealed_paths=sealed_paths,
            sealed_bytes=sealed_bytes,
        ).mcp_servers("mcp/echo.yaml", materializer=materializer)
        authority = next(iter(servers.values()))[
            "__hermes_authenticated_local_mcp"
        ]
        private_target = (
            Path(authority["root"])
            / authority["payload"]
            / target.relative_to(run)
        )
        assert private_target.read_bytes() == original
    finally:
        materializer.cleanup()


@pytest.mark.parametrize("invalid_name", [r"node\\skill", "node\0skill"])
def test_pre_language_skill_digest_map_rejects_noncanonical_names(
    tmp_path, invalid_name
):
    run = tmp_path / "run"
    run.mkdir()
    inputs = b"{}"
    (run / "inputs.json").write_bytes(inputs)
    resources = {
        "inputs_sha256": sha256(inputs).hexdigest(),
        "node_skills": {invalid_name: "0" * 64},
        "node_agent_skills": {},
    }

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        RunScheduler._legacy_auxiliary_bytes(run, resources, b"resources")

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


@pytest.mark.parametrize("mutation", ["changed", "missing", "extra", "symlink"])
def test_pre_language_snapshot_authenticates_exact_input_bytes(
    tmp_path, workflow_writer, mutation
):
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name="legacy-input-auth",
            filename="legacy-input-auth.yaml",
        )
    )
    store = RunStore(tmp_path / "home")
    prepared = store.prepare_run_snapshot(package, values={"arguments": "sealed"})
    resources_path = prepared.staging_directory / "resources.json"
    resources = json.loads(resources_path.read_bytes())
    resources.pop("language")
    resources.pop("sealed_paths")
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
            idempotency_key=f"legacy-input-{mutation}",
            concurrency_key=package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    run = store.run_directory(admitted.run_id)
    target = run / "inputs" / "arguments.txt"
    if mutation == "changed":
        target.write_text("forged", encoding="utf-8")
    elif mutation == "missing":
        target.unlink()
    elif mutation == "extra":
        (run / "inputs" / "injected.txt").write_text("forged", encoding="utf-8")
    else:
        external = tmp_path / "external-input.txt"
        external.write_bytes(target.read_bytes())
        target.unlink()
        try:
            target.symlink_to(external)
        except OSError:
            pytest.skip("symlinks unavailable")

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        _load_with_scheduler(store, admitted.run_id, historical_projection=True)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"


def test_legacy_raw_package_bytes_accepts_exact_authoritative_boundaries(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    count_paths = []
    for index in range(CATALOG_MAX_RESOURCE_FILES):
        path = run / f"count-{index:03}.bin"
        path.write_bytes(b"")
        count_paths.append(path.name)
    assert len(
        RunScheduler._legacy_raw_package_bytes(run, frozenset(count_paths))
    ) == CATALOG_MAX_RESOURCE_FILES

    boundary = run / "boundary.bin"
    boundary.write_bytes(b"x" * CATALOG_MAX_RESOURCE_FILE_BYTES)
    assert RunScheduler._legacy_raw_package_bytes(
        run, frozenset({boundary.name})
    )[boundary.name] == b"x" * CATALOG_MAX_RESOURCE_FILE_BYTES

    total_paths = []
    chunk_size = CATALOG_MAX_RESOURCE_FILE_BYTES
    for index in range(CATALOG_MAX_RESOURCE_TOTAL_BYTES // chunk_size):
        path = run / f"total-{index}.bin"
        path.write_bytes(bytes([index]) * chunk_size)
        total_paths.append(path.name)
    result = RunScheduler._legacy_raw_package_bytes(run, frozenset(total_paths))
    assert sum(map(len, result.values())) == CATALOG_MAX_RESOURCE_TOTAL_BYTES


@pytest.mark.parametrize("overflow", ["files", "file-bytes", "total-bytes"])
def test_legacy_raw_package_bytes_rejects_authoritative_capacity_overflow(
    tmp_path, overflow
):
    run = tmp_path / "run"
    run.mkdir()
    paths = []
    if overflow == "files":
        for index in range(CATALOG_MAX_RESOURCE_FILES + 1):
            path = run / f"resource-{index:03}.bin"
            path.write_bytes(b"")
            paths.append(path.name)
    elif overflow == "file-bytes":
        path = run / "oversized.bin"
        path.write_bytes(b"x" * (CATALOG_MAX_RESOURCE_FILE_BYTES + 1))
        paths.append(path.name)
    else:
        for index in range(
            CATALOG_MAX_RESOURCE_TOTAL_BYTES // CATALOG_MAX_RESOURCE_FILE_BYTES
        ):
            path = run / f"resource-{index}.bin"
            path.write_bytes(b"x" * CATALOG_MAX_RESOURCE_FILE_BYTES)
            paths.append(path.name)
        path = run / "overflow.bin"
        path.write_bytes(b"x")
        paths.append(path.name)

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        RunScheduler._legacy_raw_package_bytes(run, frozenset(paths))

    assert exc.value.code == "workflow_legacy_snapshot_unverifiable"
    assert "re-trust" in str(exc.value)
    assert "new run" in str(exc.value)


def _authority_budget() -> WorkflowResourceReadBudget:
    return WorkflowResourceReadBudget(
        max_file_bytes=CATALOG_MAX_RESOURCE_FILE_BYTES,
        max_total_bytes=CATALOG_MAX_RESOURCE_TOTAL_BYTES,
        max_files=CATALOG_MAX_RESOURCE_FILES,
    )


@pytest.mark.parametrize("mutation", ["delete", "rename", "replace"])
def test_scheduler_input_variables_use_authenticated_bytes_without_reopening_source(
    tmp_path, mutation
):
    run = tmp_path / "run"
    inputs = run / "inputs"
    inputs.mkdir(parents=True)
    manifest = b'{"arguments":{"relative_path":"inputs/arguments.txt"}}'
    arguments = b"authenticated input"
    manifest_path = run / "inputs.json"
    argument_path = inputs / "arguments.txt"
    manifest_path.write_bytes(manifest)
    argument_path.write_bytes(arguments)
    if mutation == "delete":
        argument_path.unlink()
    elif mutation == "rename":
        argument_path.rename(argument_path.with_suffix(".gone"))
    else:
        argument_path.write_text("forged input", encoding="utf-8")
    scheduler = RunScheduler(RunStore(tmp_path / "home"))
    try:
        variables = scheduler._variables(
            {"run_id": "authority-input", "artifacts": []},
            run,
            sealed_resource_paths=frozenset(
                {"inputs.json", "inputs/arguments.txt"}
            ),
            sealed_resource_bytes={
                "inputs.json": manifest,
                "inputs/arguments.txt": arguments,
            },
        )
    finally:
        scheduler.shutdown(deadline_seconds=2)

    assert variables.arguments == "authenticated input"


@pytest.mark.parametrize("boundary", ["files", "file-bytes", "total-bytes"])
def test_modern_snapshot_authority_accepts_exact_shared_budget_boundaries(
    tmp_path, boundary
):
    run = tmp_path / "run"
    run.mkdir()
    if boundary == "files":
        paths = []
        for index in range(CATALOG_MAX_RESOURCE_FILES):
            path = run / f"resource-{index:03}.bin"
            path.write_bytes(b"")
            paths.append(path.name)
    elif boundary == "file-bytes":
        path = run / "resource.bin"
        path.write_bytes(b"x" * CATALOG_MAX_RESOURCE_FILE_BYTES)
        paths = [path.name]
    else:
        paths = []
        for index in range(
            CATALOG_MAX_RESOURCE_TOTAL_BYTES // CATALOG_MAX_RESOURCE_FILE_BYTES
        ):
            path = run / f"resource-{index}.bin"
            path.write_bytes(bytes([index]) * CATALOG_MAX_RESOURCE_FILE_BYTES)
            paths.append(path.name)

    authenticated = RunScheduler._stable_snapshot_bytes(
        run,
        paths,
        read_budget=_authority_budget(),
    )

    assert len(authenticated) == len(paths)
    assert sum(map(len, authenticated.values())) <= CATALOG_MAX_RESOURCE_TOTAL_BYTES


def test_historical_package_and_auxiliary_reads_share_one_total_budget(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    package_paths = []
    for index in range(4):
        path = run / f"package-{index}.bin"
        path.write_bytes(b"p" * CATALOG_MAX_RESOURCE_FILE_BYTES)
        package_paths.append(path.name)
    (run / "inputs.json").write_bytes(b"{}")
    skill_digests = {}
    for index in range(4):
        relative = f"node-skills/node-{index}.md"
        path = run / relative
        path.parent.mkdir(exist_ok=True)
        data = b"s" * CATALOG_MAX_RESOURCE_FILE_BYTES
        path.write_bytes(data)
        skill_digests[f"node-{index}"] = sha256(data).hexdigest()
    resources = {
        "inputs_sha256": sha256(b"{}").hexdigest(),
        "node_skills": skill_digests,
        "node_agent_skills": {},
    }
    budget = _authority_budget()

    RunScheduler._legacy_raw_package_bytes(
        run,
        frozenset(package_paths),
        read_budget=budget,
    )
    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        RunScheduler._legacy_auxiliary_bytes(
            run,
            resources,
            b"resources",
            read_budget=budget,
        )

    assert exc.value.code == "workflow_legacy_snapshot_unverifiable"
    assert "re-trust" in str(exc.value)


def test_shared_snapshot_budget_deduplicates_same_canonical_file(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    path = run / "definition.yaml"
    path.write_bytes(b"x" * CATALOG_MAX_RESOURCE_FILE_BYTES)
    budget = WorkflowResourceReadBudget(
        max_file_bytes=CATALOG_MAX_RESOURCE_FILE_BYTES,
        max_total_bytes=CATALOG_MAX_RESOURCE_FILE_BYTES,
        max_files=1,
    )

    first = RunScheduler._stable_snapshot_bytes(
        run, (path.name,), read_budget=budget
    )
    second = RunScheduler._legacy_raw_package_bytes(
        run, frozenset({path.name}), read_budget=budget
    )

    assert first[path.name] == second[path.name]
    assert budget.files_read == 1
    assert budget.bytes_read == CATALOG_MAX_RESOURCE_FILE_BYTES


def test_modern_oversized_file_is_rejected_before_open(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    oversized = run / "oversized.bin"
    oversized.write_bytes(b"")
    with oversized.open("r+b") as stream:
        stream.truncate(CATALOG_MAX_RESOURCE_FILE_BYTES + 1)

    def forbidden_open(*_args, **_kwargs):
        raise AssertionError("oversized snapshot file was opened")

    monkeypatch.setattr(Path, "open", forbidden_open)

    with pytest.raises(WorkflowLanguageCompatibilityError, match="resource limit"):
        RunScheduler._stable_snapshot_bytes(
            run,
            (oversized.name,),
            read_budget=_authority_budget(),
        )


@pytest.mark.parametrize("overflow", ["files", "total-bytes"])
def test_modern_snapshot_authority_rejects_shared_budget_overflow(
    tmp_path, overflow
):
    run = tmp_path / "run"
    run.mkdir()
    paths = []
    if overflow == "files":
        for index in range(CATALOG_MAX_RESOURCE_FILES + 1):
            path = run / f"resource-{index:03}.bin"
            path.write_bytes(b"")
            paths.append(path.name)
    else:
        for index in range(
            CATALOG_MAX_RESOURCE_TOTAL_BYTES // CATALOG_MAX_RESOURCE_FILE_BYTES
        ):
            path = run / f"resource-{index}.bin"
            path.write_bytes(b"x" * CATALOG_MAX_RESOURCE_FILE_BYTES)
            paths.append(path.name)
        overflow_path = run / "overflow.bin"
        overflow_path.write_bytes(b"x")
        paths.append(overflow_path.name)

    with pytest.raises(WorkflowLanguageCompatibilityError, match="resource limit"):
        RunScheduler._stable_snapshot_bytes(
            run,
            paths,
            read_budget=_authority_budget(),
        )


def test_historical_package_and_inputs_share_one_file_count_budget(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    package_paths = []
    for index in range(CATALOG_MAX_RESOURCE_FILES):
        path = run / f"package-{index:03}.bin"
        path.write_bytes(b"")
        package_paths.append(path.name)
    (run / "inputs.json").write_bytes(b"{}")
    resources = {
        "inputs_sha256": sha256(b"{}").hexdigest(),
        "node_skills": {},
        "node_agent_skills": {},
    }
    budget = _authority_budget()
    RunScheduler._legacy_raw_package_bytes(
        run,
        frozenset(package_paths),
        read_budget=budget,
    )

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        RunScheduler._legacy_auxiliary_bytes(
            run,
            resources,
            b"resources",
            read_budget=budget,
        )

    assert exc.value.code == "workflow_legacy_snapshot_unverifiable"


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
