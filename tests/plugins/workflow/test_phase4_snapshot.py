from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.language import WorkflowLanguageCompatibilityError
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore
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


def _root_child_compilation(tmp_path: Path, workflow_writer):
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    root_path = workflow_writer(
        tmp_path / "root/workflows",
        name="sealed-root",
        filename="sealed-root.yaml",
        nodes=[{"id": "checks", "include": "sealed-child"}],
    )
    root_policy = (
        b"language_compatibility: archon-2026-07\n"
        b"outward_action_nodes: [checks]\n"
    )
    root_path.with_name("sealed-root.hermes.yaml").write_bytes(root_policy)
    child_path = workflow_writer(
        tmp_path / "child/workflows",
        name="sealed-child",
        filename="sealed-child.yaml",
        nodes=[{"id": "execute", "bash": "printf child"}],
    )
    child_policy = b"required_secrets: [IGNORED_CHILD_SECRET]\n"
    child_path.with_name("sealed-child.hermes.yaml").write_bytes(child_policy)
    root = _parse(root_path, sidecar=root_policy, source="project", precedence=1)
    child = _parse(child_path, sidecar=child_policy, source="profile", precedence=2)
    compilation = compile_workflow(
        root,
        WorkflowCatalogSnapshot.capture((root, child)),
        normalizer_version=4,
    )
    return compilation


def test_compilation_canonicalizes_absent_root_policy_before_sealing(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch an absent sidecar binding a different digest than format-2 policy.yaml."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    root_path = workflow_writer(
        tmp_path / "policyless/workflows",
        name="policyless",
        filename="policyless.yaml",
        nodes=[{"id": "execute", "bash": "true"}],
    )
    root = _parse(root_path, sidecar=None, source="project", precedence=1)

    compilation = compile_workflow(
        root,
        WorkflowCatalogSnapshot.capture((root,)),
    )

    assert compilation.active_policy_bytes == b"{}\n"
    assert compilation.dependency_manifest.active_root_policy_digest == (
        hashlib.sha256(b"{}\n").hexdigest()
    )


def test_catalog_resolution_returns_one_complete_compilation(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch admission resolving only the root package or recompiling after lookup."""
    from plugins.workflow.catalog_api import resolve_workflow_catalog_compilation

    workdir = tmp_path / "project"
    hermes_home = tmp_path / "home"
    root_path = workflow_writer(
        workdir / ".hermes/workflows",
        name="catalog-root",
        filename="catalog-root.yaml",
        nodes=[{"id": "child", "include": "catalog-child"}],
    )
    root_path.with_name("catalog-root.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n",
        encoding="utf-8",
    )
    child_path = workflow_writer(
        hermes_home / "workflows",
        name="catalog-child",
        filename="catalog-child.yaml",
        nodes=[{"id": "execute", "bash": "printf sealed"}],
    )

    compilation = resolve_workflow_catalog_compilation(
        "catalog-root",
        hermes_home=hermes_home,
        workdir=workdir,
        catalog_source="project",
        normalizer_version=4,
    )

    assert compilation is not None
    assert compilation.package.workflow_path == root_path.resolve()
    nodes = {node.id: node for node in compilation.package.definition.nodes}
    assert nodes["child__execute"].value == "printf sealed"
    assert tuple(
        dependency.package_key
        for dependency in compilation.dependency_manifest.dependencies
    ) == ("profile:catalog-child",)
    assert child_path.resolve().as_posix() not in compilation.definition_bytes.decode()


def _prepare_format2(store: RunStore, compilation):
    return store.prepare_run_snapshot(
        compilation.package,
        compilation=compilation,
        trusted_package_digest=WorkflowPackageDigest(
            compilation.composite_digest,
            compilation.covered_relative_paths,
        ),
    )


def _admit(store: RunStore, compilation, *, key: str):
    prepared = _prepare_format2(store, compilation)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=compilation.package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=key,
            concurrency_key=compilation.package.definition.name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    return prepared, admitted.run_id


def test_format2_snapshot_seals_complete_compilation_and_publishes_identity(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch v4 admission dropping closure bytes or publishing root-only identity."""
    compilation = _root_child_compilation(tmp_path, workflow_writer)
    store = RunStore(tmp_path / "home")

    prepared, run_id = _admit(store, compilation, key="format2-layout")

    run_directory = store.run_directory(run_id)
    manifest_bytes = compilation.dependency_manifest.canonical_bytes()
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    resources = json.loads((run_directory / "resources.json").read_bytes())
    expected_paths = sorted({
        "definition.yaml",
        "policy.yaml",
        "dependencies.json",
        "inputs.json",
        "resources.json",
        *compilation.sealed_files,
    })
    projection = store.load_run(run_id)

    assert (run_directory / "definition.yaml").read_bytes() == (
        compilation.definition_bytes
    )
    assert (run_directory / "policy.yaml").read_bytes() == (
        compilation.active_policy_bytes
    )
    assert (run_directory / "dependencies.json").read_bytes() == manifest_bytes
    assert {
        relative: (run_directory / relative).read_bytes()
        for relative in compilation.sealed_files
    } == dict(compilation.sealed_files)
    assert resources["snapshot_format_version"] == 2
    assert resources["dependency_manifest_digest"] == manifest_digest
    assert resources["sealed_paths"] == expected_paths
    assert resources["language"]["normalizer_version"] == 4
    assert prepared.definition_digest == compilation.composite_digest
    assert projection["snapshot_format_version"] == 2
    assert projection["definition_digest"] == compilation.composite_digest
    assert projection["policy_digest"] == hashlib.sha256(
        compilation.active_policy_bytes
    ).hexdigest()
    assert projection["dependency_manifest_digest"] == manifest_digest
    assert projection["expanded_nodes"] == ["checks__execute"]


def test_format2_reload_and_execution_never_reopen_deleted_source_packages(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch resume repairing a sealed closure from installed root or child files."""
    compilation = _root_child_compilation(tmp_path, workflow_writer)
    store = RunStore(tmp_path / "reload-home")
    _prepared, run_id = _admit(store, compilation, key="format2-reload")
    shutil.rmtree(tmp_path / "root")
    shutil.rmtree(tmp_path / "child")

    restarted = RunStore(tmp_path / "reload-home")
    scheduler = RunScheduler(restarted)
    package = scheduler._load_run_package(run_id)

    assert package.root == restarted.run_directory(run_id)
    assert [node.id for node in package.definition.nodes] == ["checks__execute"]
    assert package.definition.nodes[0].value == "printf child"
    assert scheduler.advance(run_id)["status"] == "succeeded"


def test_format2_reload_rebinds_child_command_script_and_local_mcp_resources(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch runtime reconstruction hiding resource-binding semantic drift."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
    from plugins.workflow.resources import ResourceResolver

    root_path = workflow_writer(
        tmp_path / "resource-root/workflows",
        name="resource-root",
        filename="resource-root.yaml",
        nodes=[{"id": "child", "include": "resource-child"}],
    )
    root_policy = b"language_compatibility: archon-2026-07\n"
    root_path.with_name("resource-root.hermes.yaml").write_bytes(root_policy)
    child_path = workflow_writer(
        tmp_path / "resource-child/workflows",
        name="resource-child",
        filename="resource-child.yaml",
        nodes=[
            {"id": "review", "command": "review"},
            {
                "id": "consume",
                "script": "consume.py",
                "runtime": "uv",
                "depends_on": ["review"],
                "mcp": "servers.yaml",
            },
            {
                "id": "publish",
                "script": "publish.py",
                "runtime": "uv",
                "depends_on": ["consume"],
                "mcp": "tools.yaml",
            },
        ],
    )
    child_root = child_path.parent.parent
    (child_root / "commands").mkdir()
    (child_root / "commands/review.md").write_text(
        "Review the sealed input.\n",
        encoding="utf-8",
    )
    (child_root / "scripts").mkdir()
    (child_root / "scripts/consume.py").write_text(
        "print('consume')\n",
        encoding="utf-8",
    )
    (child_root / "scripts/publish.py").write_text(
        "print('publish')\n",
        encoding="utf-8",
    )
    (child_root / "scripts/server.py").write_text(
        "print('server')\n",
        encoding="utf-8",
    )
    (child_root / "scripts/tool.py").write_text(
        "print('tool')\n",
        encoding="utf-8",
    )
    (child_root / "mcp").mkdir()
    (child_root / "mcp/servers.yaml").write_text(
        "mcp_servers:\n"
        "  server:\n"
        "    command: python\n"
        "    args: [./scripts/server.py]\n",
        encoding="utf-8",
    )
    (child_root / "mcp/tools.yaml").write_text(
        "mcp_servers:\n"
        "  tool:\n"
        "    command: python\n"
        "    args: [./scripts/tool.py]\n",
        encoding="utf-8",
    )
    root = _parse(root_path, sidecar=root_policy, source="project", precedence=1)
    child = _parse(child_path, sidecar=None, source="profile", precedence=2)
    compilation = compile_workflow(
        root,
        WorkflowCatalogSnapshot.capture((root, child)),
        normalizer_version=4,
    )
    store = RunStore(tmp_path / "resource-home")
    _prepared, run_id = _admit(store, compilation, key="sealed-resources")
    shutil.rmtree(root_path.parent.parent)
    shutil.rmtree(child_root)

    scheduler = RunScheduler(RunStore(tmp_path / "resource-home"))
    package, sealed_paths, sealed_bytes = scheduler._load_verified_run_package(
        run_id
    )

    assert package.root == store.run_directory(run_id)
    nodes = {node.id: node for node in package.definition.nodes}
    command_path = nodes["child__review"].value
    script_path = nodes["child__consume"].value
    mcp_paths = (
        nodes["child__consume"].options["mcp"],
        nodes["child__publish"].options["mcp"],
    )
    assert isinstance(command_path, str) and command_path.startswith("packages/")
    assert isinstance(script_path, str) and script_path.startswith("packages/")
    assert len(mcp_paths) == 2
    assert all(path.startswith("packages/") for path in mcp_paths)
    resolver = ResourceResolver(
        package.root,
        sealed_paths=sealed_paths,
        sealed_bytes=sealed_bytes,
    )
    assert resolver.command(command_path).body == "Review the sealed input.\n"
    assert resolver.script(
        script_path,
        runtime="uv",
    ).authenticated_bytes == b"print('consume')\n"
    for mcp_path in mcp_paths:
        compiled_mcp = resolver.text(mcp_path)
        assert "./scripts/" not in compiled_mcp
        assert "packages/" in compiled_mcp
    local_bindings = [
        binding
        for binding in compilation.dependency_manifest.resources
        if binding.resource_kind == "mcp_resource"
    ]
    assert len(local_bindings) == 2
    assert {
        resolver.read_bytes(binding.snapshot_path)
        for binding in local_bindings
    } == {b"print('server')\n", b"print('tool')\n"}


@pytest.mark.parametrize(
    "target_kind",
    ("definition", "policy", "manifest", "origin_file", "resources"),
)
def test_format2_snapshot_tampering_fails_closed_before_reload(
    tmp_path: Path,
    workflow_writer,
    target_kind: str,
) -> None:
    """Catch any authenticated format-2 authority changing before execution."""
    compilation = _root_child_compilation(tmp_path, workflow_writer)
    store = RunStore(tmp_path / f"home-{target_kind}")
    _prepared, run_id = _admit(store, compilation, key=f"tamper-{target_kind}")
    run_directory = store.run_directory(run_id)
    targets = {
        "definition": "definition.yaml",
        "policy": "policy.yaml",
        "manifest": "dependencies.json",
        "origin_file": next(iter(compilation.sealed_files)),
        "resources": "resources.json",
    }
    target = run_directory / targets[target_kind]
    original = target.read_bytes()
    tampered = (
        original.replace(b"printf child", b"printf forged")
        if target_kind == "definition"
        else original + b"\ntampered"
    )
    assert tampered != original
    target.write_bytes(tampered)

    with pytest.raises(WorkflowLanguageCompatibilityError) as exc:
        RunScheduler(store)._load_run_package(run_id)

    assert exc.value.code == "workflow_snapshot_integrity_mismatch"
