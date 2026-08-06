from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from plugins.workflow.dependency_manifest import WorkflowDependencyManifest
from plugins.workflow.models import WorkflowValidationError


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
