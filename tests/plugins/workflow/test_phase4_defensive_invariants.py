from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import hashlib
import json
from copy import deepcopy
from pathlib import Path
import shutil
import threading

import pytest
import yaml

from agent.plugin_agent import PluginAgentRunResult
from plugins.workflow.dependency_manifest import WorkflowDependencyManifest
from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.machine_contract import WorkflowConflict
from plugins.workflow.models import LoopSignalConfirmation, WorkflowValidationError
from plugins.workflow.evidence import EvidenceReader
from plugins.workflow.notifications import NotificationOutbox
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.schema import load_workflow
from plugins.workflow.store import ArtifactRef, RunStore
from plugins.workflow.trust import WorkflowPackageDigest


def _parse(path: Path, *, sidecar: bytes | None, source: str, precedence: int):
    from plugins.workflow.schema import parse_workflow_source_bytes

    return parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar,
        source=source,
        precedence=precedence,
    )


def _compile(root, *dependencies):
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    return compile_workflow(
        root,
        WorkflowCatalogSnapshot.capture((root, *dependencies)),
        normalizer_version=4,
    )


class _TraversalBomb:
    """Fail the test if an over-limit v4 node reaches materialization."""


def test_current_v3_large_root_projects_while_v4_rejects_before_node_513(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch Phase 4 closure bounds leaking into current v3 compilation."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
    from plugins.workflow.topology import project_topology

    root_path = workflow_writer(
        tmp_path / "versioned-large-root/workflows",
        name="versioned-large-root",
        filename="versioned-large-root.yaml",
        nodes=[
            {"id": f"node-{index:03d}", "bash": "true"}
            for index in range(513)
        ],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    source = _parse(
        root_path,
        sidecar=sidecar,
        source="project",
        precedence=1,
    )
    catalog = WorkflowCatalogSnapshot.capture((source,))

    current = compile_workflow(source, catalog)
    projection = project_topology(current.package.definition)

    assert current.package.language.normalizer_version == 3
    assert projection.node_count == 513

    bomb_source = replace(
        source,
        nodes=(
            *source.nodes[:512],
            replace(source.nodes[512], value=_TraversalBomb()),
        ),
    )
    with pytest.raises(WorkflowValidationError) as exc:
        compile_workflow(
            bomb_source,
            WorkflowCatalogSnapshot.capture((bomb_source,)),
            normalizer_version=4,
        )

    assert exc.value.issues[0].code == "include_expansion_limit"


def _root_command_compilation(tmp_path: Path, workflow_writer):
    root_path = workflow_writer(
        tmp_path / "authority/workflows",
        name="authority-root",
        filename="authority-root.yaml",
        nodes=[{"id": "review", "command": "review"}],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    root_path.with_name("authority-root.hermes.yaml").write_bytes(sidecar)
    command = tmp_path / "authority/commands/review.md"
    command.parent.mkdir()
    command.write_text("first review\n", encoding="utf-8")
    source = _parse(
        root_path,
        sidecar=sidecar,
        source="project",
        precedence=1,
    )
    return _compile(source), source, command


def test_phase4_diagnostics_defensively_hide_private_source_and_runtime_values(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
    capsys,
) -> None:
    """Catch diagnostics exposing host paths or authenticated private bodies."""
    from plugins.workflow.cli import _doctor_payload, register_cli, show_package

    workflow = workflow_writer(
        tmp_path / "private/workflows",
        name="defensive-private-projection",
        filename="defensive-private-projection.yaml",
        nodes=[
            {
                "id": "draft",
                "prompt": "DEFENSIVE_PRIVATE_PROMPT_BODY",
            },
            {"id": "review", "command": "review", "depends_on": ["draft"]},
        ],
    )
    sidecar = (
        b"language_compatibility: archon-2026-07\n"
        b"required_secrets: [DEFENSIVE_API_KEY]\n"
    )
    workflow.with_name("defensive-private-projection.hermes.yaml").write_bytes(
        sidecar
    )
    commands = tmp_path / "private/commands"
    commands.mkdir()
    (commands / "review.md").write_text(
        "DEFENSIVE_PRIVATE_COMMAND_BODY\n",
        encoding="utf-8",
    )
    compilation = _compile(
        _parse(
            workflow,
            sidecar=sidecar,
            source="project",
            precedence=1,
        )
    )
    monkeypatch.setenv("DEFENSIVE_API_KEY", "DEFENSIVE_PRIVATE_SECRET_VALUE")
    monkeypatch.setattr(
        "plugins.workflow.cli._resolve_compilation",
        lambda *_args, **_kwargs: compilation,
    )
    monkeypatch.setattr(
        "plugins.workflow.cli._resolve",
        lambda *_args, **_kwargs: compilation.package,
    )
    shown = show_package(compilation.package, compilation=compilation)
    doctor = _doctor_payload(
        compilation.package,
        compilation=compilation,
        hermes_home=tmp_path / "home",
        compat_report=True,
    )
    parser = argparse.ArgumentParser()
    register_cli(parser)
    text_outputs = []
    for command in ("show", "validate", "doctor"):
        args = parser.parse_args([
            "--hermes-home",
            str(tmp_path / "home"),
            "--workdir",
            str(tmp_path),
            command,
            "defensive-private-projection",
        ])
        assert args.func(args) == 0
        text_outputs.append(capsys.readouterr().out)

    for output in (
        json.dumps(shown, sort_keys=True),
        json.dumps(doctor, sort_keys=True),
        *text_outputs,
    ):
        for private_value in (
            str(tmp_path),
            "DEFENSIVE_PRIVATE_PROMPT_BODY",
            "DEFENSIVE_PRIVATE_COMMAND_BODY",
            "DEFENSIVE_PRIVATE_SECRET_VALUE",
        ):
            assert private_value not in output


def _defensive_signal_pause(
    tmp_path: Path,
    workflow_writer,
    *,
    key: str,
    iteration: int = 1,
    maximum: int = 2,
    result: bytes = b"defensive cleaned result\n",
):
    store = RunStore(tmp_path / "signal-home", max_executing_runs=20)
    package = load_workflow(
        workflow_writer(
            tmp_path / key,
            name=f"defensive-{key}",
            nodes=[{"id": "refine", "bash": "true"}],
        )
    )
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
            concurrency_policy="allow",
        ),
        immutable_snapshot=prepared,
    )
    claim = store.claim_node(admitted.run_id, "refine", f"worker-{key}")
    assert claim is not None
    store.mark_node_started(claim)
    digest = hashlib.sha256(result).hexdigest()
    relative = (
        Path("nodes") / "refine" / claim.attempt_id / "iteration-output.txt"
    )
    path = store.run_directory(admitted.run_id) / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(result)
    pending = LoopSignalConfirmation.create(
        run_id=admitted.run_id,
        node_id="refine",
        message="Accept or refine",
        iteration=iteration,
        max_iterations=maximum,
        result_artifact=relative.as_posix(),
        result_sha256=digest,
    ).to_dict()
    store.complete_node(
        claim,
        status="paused",
        artifacts=(
            ArtifactRef(relative.as_posix(), "text/plain", len(result), digest),
        ),
        metadata={
            "pending_interaction": pending,
            "loop_state": {"iteration": iteration},
        },
    )
    return store, admitted.run_id, pending


def test_public_projections_hide_authentic_loop_feedback_and_provider_result(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch real stored runtime bodies crossing metadata-only projections."""
    feedback = "DEFENSIVE_PRIVATE_FEEDBACK"
    provider_result = b"DEFENSIVE_PRIVATE_PROVIDER_RESPONSE\n"
    store, run_id, pending = _defensive_signal_pause(
        tmp_path,
        workflow_writer,
        key="private-runtime-values",
        result=provider_result,
    )
    paused = store.get_run_status(run_id)
    result_path = store.run_directory(run_id) / str(pending["result_artifact"])
    assert result_path.read_bytes() == provider_result

    before = (
        paused,
        NotificationOutbox(store).history(run_id=run_id),
        EvidenceReader(store).query(run_id, kind="interactions"),
    )
    store.provide_loop_input(
        run_id,
        feedback,
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
    )
    internal = store.load_run(run_id)
    feedback_path = internal["nodes"]["refine"]["loop_user_input_artifact"]
    assert (store.run_directory(run_id) / feedback_path).read_text() == feedback
    after = (
        store.get_run_status(run_id),
        NotificationOutbox(store).history(run_id=run_id),
        EvidenceReader(store).query(run_id, kind="interactions"),
    )

    for public_projection in (*before, *after):
        encoded = json.dumps(public_projection, sort_keys=True)
        assert feedback not in encoded
        assert provider_result.decode().strip() not in encoded


class _CountedSignalRunner:
    def __init__(self) -> None:
        self.requests = []

    def run(self, request, **_kwargs) -> PluginAgentRunResult:
        self.requests.append(request)
        return PluginAgentRunResult(
            final_response="defensive result <promise>DONE</promise>",
            session_id="defensive-session",
            provider=request.provider or "fake-provider",
            model=request.model or "fake-model",
            status="completed",
            pending_interaction=None,
            usage={},
            audit={},
        )


def test_compilation_rejects_swapped_definition_bytes(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch callers replacing executable definition bytes under a trusted digest."""
    compilation, _source, _command = _root_command_compilation(
        tmp_path, workflow_writer
    )

    with pytest.raises(ValueError, match="definition.*manifest"):
        replace(compilation, definition_bytes=b"{}")


def test_compilation_rejects_swapped_active_policy_bytes(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch callers replacing active root policy under a trusted digest."""
    compilation, _source, _command = _root_command_compilation(
        tmp_path, workflow_writer
    )

    with pytest.raises(ValueError, match="policy.*manifest"):
        replace(compilation, active_policy_bytes=b"required_secrets: [FORGED]\n")


def test_compilation_rejects_swapped_final_package_graph(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch callers replacing normalized execution semantics under trusted identity."""
    compilation, _source, _command = _root_command_compilation(
        tmp_path, workflow_writer
    )
    forged_definition = replace(
        compilation.package.definition,
        description="forged graph",
    )
    forged_package = replace(
        compilation.package,
        definition=forged_definition,
    )

    with pytest.raises(ValueError, match="package graph.*manifest"):
        replace(compilation, package=forged_package)


def test_phase4_compilation_cache_reauthenticates_changed_resources(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch warm compilation cache hits retaining stale executable identity."""
    from plugins.workflow.compilation import clear_compilation_cache

    clear_compilation_cache()
    first, source, command = _root_command_compilation(tmp_path, workflow_writer)
    command.write_text("second review\n", encoding="utf-8")

    second = _compile(source)

    first_binding = next(
        item
        for item in first.dependency_manifest.resources
        if item.resource_kind == "command"
    )
    second_binding = next(
        item
        for item in second.dependency_manifest.resources
        if item.resource_kind == "command"
    )
    assert first.composite_digest != second.composite_digest
    assert first.sealed_files[first_binding.snapshot_path] == b"first review\n"
    assert second.sealed_files[second_binding.snapshot_path] == b"second review\n"


def test_origin_resource_symlink_escape_fails_without_host_path_disclosure(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch included resources escaping containment or leaking their host path."""
    root_path = workflow_writer(
        tmp_path / "root/workflows",
        name="root",
        filename="root.yaml",
        nodes=[{"id": "checks", "include": "child"}],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    root_path.with_name("root.hermes.yaml").write_bytes(sidecar)
    child_path = workflow_writer(
        tmp_path / "child/workflows",
        name="child",
        filename="child.yaml",
        nodes=[{"id": "consume", "command": "consume"}],
    )
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    commands = tmp_path / "child/commands"
    commands.mkdir()
    (commands / "consume.md").symlink_to(outside)
    root = _parse(root_path, sidecar=sidecar, source="project", precedence=1)
    child = _parse(child_path, sidecar=None, source="profile", precedence=2)

    with pytest.raises(WorkflowValidationError) as exc:
        _compile(root, child)

    issue = exc.value.issues[0]
    assert issue.code == "include_resource_invalid"
    assert issue.path == "workflows/child.yaml:commands/consume.md"
    assert str(tmp_path) not in issue.path
    assert str(tmp_path) not in issue.message


def test_same_name_resources_keep_distinct_root_and_dependency_origins(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch same-name resources collapsing onto one package or sealed path."""
    root_path = workflow_writer(
        tmp_path / "same-name-root/workflows",
        name="same-name-root",
        filename="same-name-root.yaml",
        nodes=[
            {"id": "root-review", "command": "review"},
            {
                "id": "checks",
                "include": "same-name-child",
                "depends_on": ["root-review"],
            },
        ],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    root_path.with_name("same-name-root.hermes.yaml").write_bytes(sidecar)
    root_commands = tmp_path / "same-name-root/commands"
    root_commands.mkdir()
    (root_commands / "review.md").write_bytes(b"root review\n")

    child_path = workflow_writer(
        tmp_path / "same-name-child/workflows",
        name="same-name-child",
        filename="same-name-child.yaml",
        nodes=[{"id": "review", "command": "review"}],
    )
    child_commands = tmp_path / "same-name-child/commands"
    child_commands.mkdir()
    (child_commands / "review.md").write_bytes(b"child review\n")

    compilation = _compile(
        _parse(root_path, sidecar=sidecar, source="project", precedence=1),
        _parse(child_path, sidecar=None, source="profile", precedence=2),
    )
    bindings = {
        binding.node_id: binding
        for binding in compilation.dependency_manifest.resources
        if binding.resource_kind == "command"
    }
    root_binding = bindings["root-review"]
    child_binding = bindings["checks__review"]

    assert root_binding.source_relative_path == child_binding.source_relative_path
    assert root_binding.package_key != child_binding.package_key
    assert root_binding.snapshot_path != child_binding.snapshot_path
    assert compilation.sealed_files[root_binding.snapshot_path] == b"root review\n"
    assert compilation.sealed_files[child_binding.snapshot_path] == b"child review\n"


def test_manifest_is_complete_and_detects_changed_digests_or_sealed_bytes(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch authenticated paths omitted from identity or sealed bytes diverging."""
    root_path = workflow_writer(
        tmp_path / "root/workflows",
        name="root",
        filename="root.yaml",
        nodes=[{"id": "review", "command": "review"}],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    root_path.with_name("root.hermes.yaml").write_bytes(sidecar)
    commands = tmp_path / "root/commands"
    commands.mkdir()
    (commands / "review.md").write_text("review\n", encoding="utf-8")
    compilation = _compile(
        _parse(root_path, sidecar=sidecar, source="project", precedence=1)
    )
    manifest = compilation.dependency_manifest
    package_records = (manifest.root, *manifest.dependencies)
    authenticated = {
        (record.package_key, relative)
        for record in package_records
        for relative in record.covered_relative_paths
    }
    represented = {
        (binding.package_key, binding.source_relative_path)
        for binding in manifest.resources
    }

    assert authenticated == represented
    assert {binding.snapshot_path for binding in manifest.resources} == set(
        compilation.sealed_files
    )
    for binding in manifest.resources:
        encoded = compilation.sealed_files[binding.snapshot_path]
        assert len(encoded) == binding.compiled_byte_size
        assert hashlib.sha256(encoded).hexdigest() == binding.compiled_digest

    changed_manifest = manifest.to_dict()
    changed_manifest["resources"][0]["compiled_digest"] = "f" * 64
    with pytest.raises(ValueError, match="resource bindings digest"):
        WorkflowDependencyManifest.from_dict(changed_manifest)

    changed_files = dict(compilation.sealed_files)
    first_path = next(iter(changed_files))
    changed_files[first_path] += b"changed"
    with pytest.raises(ValueError, match="sealed file digest"):
        replace(compilation, sealed_files=changed_files)


def test_named_script_and_mcp_local_resources_bind_to_child_origin(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch scripts/MCP children retaining mutable or root-relative references."""
    root_path = workflow_writer(
        tmp_path / "root/workflows",
        name="root",
        filename="root.yaml",
        nodes=[{"id": "checks", "include": "child"}],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    root_path.with_name("root.hermes.yaml").write_bytes(sidecar)
    child_path = workflow_writer(
        tmp_path / "child/workflows",
        name="child",
        filename="child.yaml",
        nodes=[
            {"id": "producer", "prompt": "produce"},
            {
                "id": "consume",
                "script": "consume.py",
                "runtime": "uv",
                "depends_on": ["producer"],
                "mcp": "servers.yaml",
            },
        ],
    )
    scripts = tmp_path / "child/scripts"
    scripts.mkdir()
    (scripts / "consume.py").write_text(
        "print('$producer.output')\n", encoding="utf-8"
    )
    (scripts / "server.py").write_text("print('server')\n", encoding="utf-8")
    mcp = tmp_path / "child/mcp"
    mcp.mkdir()
    (mcp / "servers.yaml").write_text(
        yaml.safe_dump({
            "mcp_servers": {
                "local": {
                    "command": "python",
                    "args": ["./scripts/server.py"],
                }
            }
        }),
        encoding="utf-8",
    )
    compilation = _compile(
        _parse(root_path, sidecar=sidecar, source="project", precedence=1),
        _parse(child_path, sidecar=None, source="profile", precedence=2),
    )
    bindings = {
        binding.resource_kind: binding
        for binding in compilation.dependency_manifest.resources
        if binding.node_id == "checks__consume"
    }

    assert compilation.sealed_files[bindings["named_script"].snapshot_path] == (
        b"print('$checks__producer.output')\n"
    )
    from plugins.workflow.resources import ResourceResolver

    resolver = ResourceResolver(
        tmp_path,
        sealed_paths=compilation.covered_relative_paths,
        sealed_bytes=compilation.sealed_files,
    )
    assert resolver.script(
        bindings["named_script"].snapshot_path,
        runtime="uv",
    ).authenticated_bytes == b"print('$checks__producer.output')\n"
    local_path = bindings["mcp_resource"].snapshot_path
    compiled_mcp = compilation.sealed_files[bindings["mcp"].snapshot_path]
    assert local_path.encode("utf-8") in compiled_mcp
    assert b"./scripts/server.py" not in compiled_mcp.replace(
        local_path.encode("utf-8"), b""
    )
    assert bindings["mcp"].source_digest != bindings["mcp"].compiled_digest
    assert str(tmp_path).encode("utf-8") not in compiled_mcp


def test_sealed_aggregate_authority_rejects_changed_identity_and_cache_miss(
    tmp_path: Path,
) -> None:
    """Catch post-authentication reads silently reopening changed source files."""
    from plugins.workflow.trust import (
        WorkflowResourceCacheMissError,
        WorkflowResourceReadBudget,
    )

    resource = tmp_path / "resource.txt"
    resource.write_bytes(b"first")
    budget = WorkflowResourceReadBudget(
        max_file_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        max_files=512,
    )
    assert budget.read(resource) == b"first"
    budget.seal()
    resource.write_bytes(b"changed")

    with pytest.raises(OSError, match="changed after shared read"):
        budget.read(resource)
    with pytest.raises(WorkflowResourceCacheMissError):
        budget.read(tmp_path / "missing.txt")


@dataclass(frozen=True, slots=True)
class _AdmittedComposition:
    run_id: str
    hermes_home: Path
    root_package_root: Path
    dependency_root: Path


def admit_composed_workflow(
    tmp_path: Path,
    workflow_writer,
) -> _AdmittedComposition:
    """Admit one root/dependency composition under a sealed format-2 snapshot."""
    root_package_root = tmp_path / "root"
    dependency_root = tmp_path / "child"
    root_path = workflow_writer(
        root_package_root / "workflows",
        name="defensive-root",
        filename="defensive-root.yaml",
        nodes=[{"id": "child", "include": "defensive-child"}],
    )
    root_policy = b"language_compatibility: archon-2026-07\n"
    root_path.with_name("defensive-root.hermes.yaml").write_bytes(root_policy)
    child_path = workflow_writer(
        dependency_root / "workflows",
        name="defensive-child",
        filename="defensive-child.yaml",
        nodes=[{"id": "execute", "bash": "true"}],
    )
    compilation = _compile(
        _parse(root_path, sidecar=root_policy, source="project", precedence=1),
        _parse(child_path, sidecar=None, source="profile", precedence=2),
    )
    hermes_home = tmp_path / "home"
    store = RunStore(hermes_home)
    prepared = store.prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        ),
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="defensive-root",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="defensive-no-live-read",
            concurrency_key="defensive-root",
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    return _AdmittedComposition(
        run_id=admitted.run_id,
        hermes_home=hermes_home,
        root_package_root=root_package_root,
        dependency_root=dependency_root,
    )


def restart_scheduler_and_complete(
    admitted: _AdmittedComposition,
) -> dict[str, object]:
    """Restart storage/scheduling and drive an admitted composition terminal."""
    return RunScheduler(RunStore(admitted.hermes_home)).advance(admitted.run_id)


def test_admitted_run_never_reopens_deleted_dependency(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
) -> None:
    """Independently gate recovery against accidental live-package fallback."""
    admitted = admit_composed_workflow(tmp_path, workflow_writer)
    quarantined_child_root = tmp_path / "quarantined-child"
    quarantined_root = tmp_path / "quarantined-root"
    shutil.move(admitted.dependency_root, quarantined_child_root)
    shutil.move(admitted.root_package_root, quarantined_root)

    removed_dependency_root = admitted.dependency_root.absolute()
    live_source_open_attempts: list[Path] = []
    path_open = Path.open

    def track_removed_dependency_open(path: Path, *args, **kwargs):
        candidate = path.absolute()
        if (
            candidate == removed_dependency_root
            or removed_dependency_root in candidate.parents
        ):
            live_source_open_attempts.append(candidate)
        return path_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track_removed_dependency_open)

    terminal = restart_scheduler_and_complete(admitted)

    assert terminal["status"] == "succeeded"
    assert live_source_open_attempts == []


def test_future_compilation_changes_when_dependency_precedence_is_shadowed(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Independently gate complete-closure identity on catalog precedence."""
    from plugins.workflow.catalog_api import resolve_workflow_catalog_compilation

    home = tmp_path / "home"
    workdir = tmp_path / "project"
    root_path = workflow_writer(
        workdir / ".hermes/workflows",
        name="defensive-shadow-root",
        filename="defensive-shadow-root.yaml",
        nodes=[{"id": "child", "include": "defensive-shadow-child"}],
    )
    root_path.with_name("defensive-shadow-root.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    workflow_writer(
        home / "workflows",
        name="defensive-shadow-child",
        filename="defensive-shadow-child.yaml",
        nodes=[{"id": "execute", "bash": "printf profile"}],
    )
    before = resolve_workflow_catalog_compilation(
        "defensive-shadow-root",
        hermes_home=home,
        workdir=workdir,
        normalizer_version=4,
    )
    assert before is not None

    workflow_writer(
        workdir / ".hermes/workflows",
        name="defensive-shadow-child",
        filename="defensive-shadow-child.yaml",
        nodes=[{"id": "execute", "bash": "printf project"}],
    )
    after = resolve_workflow_catalog_compilation(
        "defensive-shadow-root",
        hermes_home=home,
        workdir=workdir,
        normalizer_version=4,
    )
    assert after is not None

    assert before.composite_digest != after.composite_digest
    assert (
        before.dependency_manifest.dependencies[0].catalog_source,
        before.dependency_manifest.dependencies[0].precedence,
    ) == ("profile", 2)
    assert (
        after.dependency_manifest.dependencies[0].catalog_source,
        after.dependency_manifest.dependencies[0].precedence,
    ) == ("project", 1)


@pytest.mark.parametrize("symlink_owner", ["root", "included_child"])
def test_catalog_rejects_external_sidecar_before_phase4_authentication(
    tmp_path: Path,
    workflow_writer,
    symlink_owner: str,
) -> None:
    """Catch adjacent sidecars escaping catalog containment before capture."""
    from plugins.workflow.catalog_api import (
        WorkflowCatalogInvalidDefinitionError,
        resolve_workflow_catalog_compilation,
    )

    home = tmp_path / "home"
    workdir = tmp_path / "project"
    root_path = workflow_writer(
        workdir / ".hermes/workflows",
        name="defensive-sidecar-root",
        filename="defensive-sidecar-root.yaml",
        nodes=(
            [{"id": "child", "include": "defensive-sidecar-child"}]
            if symlink_owner == "included_child"
            else [{"id": "execute", "bash": "true"}]
        ),
    )
    root_sidecar = root_path.with_name("defensive-sidecar-root.hermes.yaml")
    external_sidecar = tmp_path / f"outside-{symlink_owner}.hermes.yaml"
    if symlink_owner == "root":
        external_sidecar.write_text(
            "language_compatibility: archon-2026-07\n",
            encoding="utf-8",
        )
        escaped_sidecar = root_sidecar
    else:
        root_sidecar.write_text(
            "language_compatibility: archon-2026-07\n",
            encoding="utf-8",
        )
        child_path = workflow_writer(
            home / "workflows",
            name="defensive-sidecar-child",
            filename="defensive-sidecar-child.yaml",
            nodes=[{"id": "execute", "bash": "true"}],
        )
        external_sidecar.write_text(
            "required_secrets: [DEFENSIVE_EXTERNAL_CHILD_SECRET]\n",
            encoding="utf-8",
        )
        escaped_sidecar = child_path.with_name(
            "defensive-sidecar-child.hermes.yaml"
        )
    try:
        escaped_sidecar.symlink_to(external_sidecar)
    except OSError:
        pytest.skip("symlinks unavailable")

    with pytest.raises(WorkflowCatalogInvalidDefinitionError) as exc:
        resolve_workflow_catalog_compilation(
            "defensive-sidecar-root",
            hermes_home=home,
            workdir=workdir,
            normalizer_version=4,
        )

    assert str(external_sidecar) not in str(exc.value)
    assert "DEFENSIVE_EXTERNAL_CHILD_SECRET" not in str(exc.value)


@pytest.mark.parametrize("read_mode", ["descriptor_relative", "fallback"])
@pytest.mark.parametrize("replaced_name", ["definition", "sidecar"])
def test_catalog_rejects_definition_or_sidecar_replaced_during_capture(
    tmp_path: Path,
    monkeypatch,
    workflow_writer,
    replaced_name: str,
    read_mode: str,
) -> None:
    """Catch a regular catalog candidate becoming an external symlink mid-read."""
    import os

    import plugins.workflow.catalog_api as catalog_api

    home = tmp_path / "home"
    workdir = tmp_path / "project"
    workflow_path = workflow_writer(
        workdir / ".hermes/workflows",
        name="defensive-replaced-catalog-source",
        filename="defensive-replaced-catalog-source.yaml",
        nodes=[{"id": "execute", "bash": "true"}],
    )
    sidecar_path = workflow_path.with_name(
        "defensive-replaced-catalog-source.hermes.yaml"
    )
    sidecar_path.write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    target = workflow_path if replaced_name == "definition" else sidecar_path
    external = tmp_path / f"outside-replaced-{target.name}"
    external.write_bytes(b"DEFENSIVE_EXTERNAL_CATALOG_CONTENT\n")
    symlink_probe = tmp_path / "catalog-symlink-probe"
    try:
        symlink_probe.symlink_to(external)
    except OSError:
        pytest.skip("symlinks unavailable")
    symlink_probe.unlink()
    original_open = os.open
    original_close = os.close
    replaced = False
    opened_descriptors: set[int] = set()

    descriptor_relative_supported = (
        os.name != "nt"
        and hasattr(os, "O_NOFOLLOW")
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
    )
    if read_mode == "descriptor_relative" and not descriptor_relative_supported:
        pytest.skip("descriptor-relative no-follow reads unavailable")
    if read_mode == "fallback":
        monkeypatch.setattr(catalog_api.os, "supports_dir_fd", set())
        monkeypatch.setattr(catalog_api.os, "O_NOFOLLOW", 0, raising=False)

    def replace_before_catalog_open(path, flags, *args, **kwargs):
        nonlocal replaced
        descriptor_target = (
            kwargs.get("dir_fd") is not None and path == target.name
        )
        fallback_target = (
            kwargs.get("dir_fd") is None
            and Path(os.path.abspath(path)) == Path(os.path.abspath(target))
        )
        if not replaced and (descriptor_target or fallback_target):
            replaced = True
            target.unlink()
            target.symlink_to(external)
        descriptor = original_open(path, flags, *args, **kwargs)
        opened_descriptors.add(descriptor)
        return descriptor

    def track_catalog_close(descriptor):
        opened_descriptors.discard(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(catalog_api.os, "open", replace_before_catalog_open)
    monkeypatch.setattr(catalog_api.os, "close", track_catalog_close)

    with pytest.raises(catalog_api.WorkflowCatalogInvalidDefinitionError) as exc:
        catalog_api.resolve_workflow_catalog_compilation(
            "defensive-replaced-catalog-source",
            hermes_home=home,
            workdir=workdir,
            normalizer_version=4,
        )

    assert replaced is True
    assert opened_descriptors == set()
    assert str(external) not in str(exc.value)
    assert "DEFENSIVE_EXTERNAL_CATALOG_CONTENT" not in str(exc.value)


def test_signal_stale_and_cross_run_decisions_leave_defensive_state_unchanged(
    tmp_path: Path,
    workflow_writer,
) -> None:
    store, first_run, first_pending = _defensive_signal_pause(
        tmp_path,
        workflow_writer,
        key="stale-first",
    )
    store, _second_run, second_pending = _defensive_signal_pause(
        tmp_path,
        workflow_writer,
        key="stale-second",
    )
    first = store.load_run(first_run)

    with pytest.raises(WorkflowConflict):
        store.provide_loop_input(
            first_run,
            "stale",
            expected_state_version=first["state_version"] - 1,
            interaction_id=first_pending["interaction_id"],
        )
    with pytest.raises(ValueError):
        store.provide_loop_input(
            first_run,
            "cross-run",
            expected_state_version=first["state_version"],
            interaction_id=second_pending["interaction_id"],
        )

    current = store.load_run(first_run)
    assert current["state_version"] == first["state_version"]
    assert current["nodes"]["refine"]["pending_interaction"] == first_pending


def test_counted_signal_acceptance_survives_store_restart_without_provider_replay(
    tmp_path: Path,
    workflow_writer,
) -> None:
    workflow = workflow_writer(
        tmp_path / "restart-signal-source" / "workflows",
        name="defensive-restart-signal",
        interactive=True,
        nodes=[{
            "id": "refine",
            "loop": {
                "prompt": "Refine",
                "until": "DONE",
                "max_iterations": 2,
                "interactive": True,
                "gate_message": "Accept or refine",
            },
        }],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    compilation = _compile(
        _parse(
            workflow,
            sidecar=sidecar,
            source="project",
            precedence=1,
        )
    )
    home = tmp_path / "restart-signal-home"
    store = RunStore(home)
    prepared = store.prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        ),
    )
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=compilation.package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="defensive-restart-signal",
            concurrency_key=compilation.package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    runner = _CountedSignalRunner()
    paused = RunScheduler(store, agent_runner=runner).advance(admitted.run_id)
    pending = paused["nodes"]["refine"]["pending_interaction"]

    restarted = RunStore(home)
    decision = restarted.approve_run(
        admitted.run_id,
        expected_state_version=paused["state_version"],
        interaction_id=pending["interaction_id"],
    )

    assert decision.outcome == "applied"
    assert restarted.get_run_status(admitted.run_id)["status"] == "succeeded"
    assert len(runner.requests) == 1


def test_final_signal_feedback_is_defensively_rejected_without_mutation(
    tmp_path: Path,
    workflow_writer,
) -> None:
    store, run_id, pending = _defensive_signal_pause(
        tmp_path,
        workflow_writer,
        key="final-feedback",
        iteration=2,
        maximum=2,
    )
    paused = store.load_run(run_id)

    with pytest.raises(ValueError, match="final iteration"):
        store.provide_loop_input(
            run_id,
            "try again",
            expected_state_version=paused["state_version"],
            interaction_id=pending["interaction_id"],
        )

    assert store.load_run(run_id)["state_version"] == paused["state_version"]


def test_concurrent_signal_decisions_have_one_defensive_winner(
    tmp_path: Path,
    workflow_writer,
) -> None:
    store, run_id, pending = _defensive_signal_pause(
        tmp_path,
        workflow_writer,
        key="concurrent",
    )
    paused = store.load_run(run_id)
    barrier = threading.Barrier(3)

    def approve() -> str:
        barrier.wait()
        return store.approve_run(
            run_id,
            expected_state_version=paused["state_version"],
            interaction_id=pending["interaction_id"],
        ).outcome

    def feedback() -> str:
        barrier.wait()
        try:
            store.provide_loop_input(
                run_id,
                "continue",
                expected_state_version=paused["state_version"],
                interaction_id=pending["interaction_id"],
            )
        except WorkflowConflict:
            return "conflict"
        return "applied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(approve), pool.submit(feedback)]
        barrier.wait()
        outcomes = [future.result(timeout=10) for future in futures]

    assert outcomes.count("applied") == 1
    events = store.tail_events(run_id, limit=30)
    assert sum(
        event["event_type"]
        in {"loop_signal_accepted", "loop_feedback_provided"}
        for event in events
    ) == 1
