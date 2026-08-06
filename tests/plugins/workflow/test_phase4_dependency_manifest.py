from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
import yaml

from plugins.workflow.dependency_manifest import (
    WorkflowDependencyManifest,
    composite_workflow_digest,
)


def _manifest_document() -> dict[str, object]:
    return {
        "schema_version": 1,
        "root": {
            "package_key": "project:root",
            "workflow_name": "root",
            "catalog_source": "project",
            "precedence": 1,
            "definition_location": "workflows/root.yaml",
            "definition_digest": "1" * 64,
            "sidecar_location": "workflows/root.hermes.yaml",
            "sidecar_digest": "2" * 64,
            "sidecar_status": "active",
            "ignored_policy_fields": [],
            "package_digest": "3" * 64,
            "covered_relative_paths": [
                "commands/review.md",
                "workflows/root.hermes.yaml",
                "workflows/root.yaml",
            ],
        },
        "dependencies": [
            {
                "package_key": "profile:child",
                "workflow_name": "child",
                "catalog_source": "profile",
                "precedence": 2,
                "definition_location": "library/child.yaml",
                "definition_digest": "4" * 64,
                "sidecar_location": "library/child.hermes.yaml",
                "sidecar_digest": "5" * 64,
                "sidecar_status": "authenticated_ignored",
                "ignored_policy_fields": ["language_compatibility"],
                "package_digest": "6" * 64,
                "covered_relative_paths": [
                    "commands/review.md",
                    "library/child.hermes.yaml",
                    "library/child.yaml",
                ],
            }
        ],
        "include_edges": [
            {
                "include_instance_path": ["checks"],
                "source_package_key": "project:root",
                "source_node_id": "checks",
                "target_package_key": "profile:child",
            }
        ],
        "node_origins": [
            {
                "include_instance_path": ["checks"],
                "package_key": "profile:child",
                "workflow_name": "child",
                "catalog_source": "profile",
                "precedence": 2,
                "definition_location": "library/child.yaml",
                "source_index": 0,
                "source_line": 4,
                "expanded_node_id": "checks__review",
            }
        ],
        "resources": [
            {
                "binding_id": "checks__review:command",
                "node_id": "checks__review",
                "resource_kind": "command",
                "package_key": "profile:child",
                "source_relative_path": "commands/review.md",
                "source_digest": "7" * 64,
                "compiled_digest": "8" * 64,
                "source_byte_size": 31,
                "compiled_byte_size": 39,
                "media_type": "text/markdown",
                "snapshot_path": (
                    "packages/" + "9" * 64 + "/" + "a" * 64
                    + "/commands/review.md"
                ),
            }
        ],
        "counts": {
            "dependency_packages": 1,
            "include_edges": 1,
            "expanded_nodes": 1,
            "authenticated_files": 6,
            "authenticated_bytes": 462,
            "sealed_files": 1,
            "sealed_bytes": 39,
        },
        "expanded_definition_digest": "b" * 64,
        "node_origins_digest": (
            "27d8124e34c9c0b59375293df4e17c1cdc29f843c9178064a9d049c780a565a8"
        ),
        "resource_bindings_digest": (
            "26590d181b0f3784db7cfeb9f7c17a1c10446c702cb711825d0f261555ea6181"
        ),
        "active_root_policy_digest": "e" * 64,
    }


def test_manifest_codec_round_trips_exact_bounded_fields_immutably() -> None:
    """Catch codecs dropping origin/policy/resource identity or retaining mutability."""
    raw = _manifest_document()

    manifest = WorkflowDependencyManifest.from_dict(raw)

    assert manifest.to_dict() == raw
    assert manifest.schema_version == 1
    assert manifest.dependencies[0].sidecar_status == "authenticated_ignored"
    assert manifest.resources[0].snapshot_path.startswith("packages/")
    with pytest.raises(FrozenInstanceError):
        manifest.dependencies[0].workflow_name = "changed"
    with pytest.raises(TypeError):
        manifest.node_origins[0]["expanded_node_id"] = "changed"
    with pytest.raises(AttributeError):
        manifest.node_origins[0]["include_instance_path"].append("changed")


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda raw: raw.__setitem__("unexpected", True), "exact fields"),
        (
            lambda raw: raw["root"].__setitem__(
                "definition_location", "/private/root.yaml"
            ),
            "logical path",
        ),
        (
            lambda raw: raw["dependencies"][0].__setitem__(
                "sidecar_location", "../child.hermes.yaml"
            ),
            "logical path",
        ),
        (
            lambda raw: raw["resources"][0].__setitem__(
                "source_relative_path", "commands\\review.md"
            ),
            "logical path",
        ),
        (
            lambda raw: raw["resources"][0].__setitem__(
                "snapshot_path", "resources/review.md"
            ),
            "packages/",
        ),
        (
            lambda raw: raw["root"].__setitem__("package_digest", "A" * 64),
            "SHA-256",
        ),
    ),
)
def test_manifest_codec_rejects_disclosure_and_schema_escapes(
    mutate, message: str
) -> None:
    """Catch format-2 metadata accepting host paths or loose/ambiguous fields."""
    raw = deepcopy(_manifest_document())
    mutate(raw)

    with pytest.raises(ValueError, match=message):
        WorkflowDependencyManifest.from_dict(raw)


def test_manifest_codec_rejects_noncanonical_order_and_inexact_counts() -> None:
    """Catch identities depending on caller iteration order or forged counts."""
    raw = _manifest_document()
    second = deepcopy(raw["dependencies"][0])
    second["package_key"] = "project:alpha"
    second["workflow_name"] = "alpha"
    second["catalog_source"] = "explicit"
    second["precedence"] = 1
    second["definition_location"] = "alpha.yaml"
    second["sidecar_location"] = None
    second["sidecar_digest"] = None
    second["sidecar_status"] = "absent"
    second["ignored_policy_fields"] = []
    raw["dependencies"].append(second)
    raw["counts"]["dependency_packages"] = 2

    with pytest.raises(ValueError, match="canonical order"):
        WorkflowDependencyManifest.from_dict(raw)

    forged = _manifest_document()
    forged["counts"]["expanded_nodes"] = 2
    with pytest.raises(ValueError, match="counts"):
        WorkflowDependencyManifest.from_dict(forged)

    forged = _manifest_document()
    forged["counts"]["sealed_bytes"] = 40
    with pytest.raises(ValueError, match="counts"):
        WorkflowDependencyManifest.from_dict(forged)


def test_composite_digest_uses_only_exact_canonical_digest_inputs() -> None:
    """Catch trust identity hashing bodies, paths, timestamps, or omitted components."""
    manifest = WorkflowDependencyManifest.from_dict(_manifest_document())

    assert composite_workflow_digest(manifest) == (
        "ca7598d0fd2cb0fb81ac8617e892c70d41f1ac1f4075de12958a458cdfa6096e"
    )


def _parse_source(
    path: Path,
    *,
    source: str,
    precedence: int,
    sidecar_bytes: bytes | None = None,
):
    from plugins.workflow.schema import parse_workflow_source_bytes

    return parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar_bytes,
        source=source,
        precedence=precedence,
    )


def test_compilation_binds_same_named_resources_to_their_exact_origins(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch included commands resolving from the root or colliding by basename."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow

    root_path = workflow_writer(
        tmp_path / "root/workflows",
        name="root",
        filename="root.yaml",
        nodes=[
            {"id": "root-review", "command": "review"},
            {"id": "checks", "include": "child", "depends_on": ["root-review"]},
        ],
    )
    root_sidecar = (
        b"language_compatibility: archon-2026-07\n"
        b"outward_action_nodes: [checks]\n"
    )
    root_path.with_name("root.hermes.yaml").write_bytes(root_sidecar)
    root_command = root_path.parents[1] / "commands/review.md"
    root_command.parent.mkdir()
    root_command.write_text("root review\n", encoding="utf-8")

    child_path = workflow_writer(
        tmp_path / "child/workflows",
        name="child",
        filename="child.yaml",
        nodes=[
            {"id": "producer", "prompt": "produce"},
            {
                "id": "review",
                "command": "review",
                "depends_on": ["producer"],
                "allowed_tools": ["read_file"],
                "skills": ["reviewing"],
                "provider": "child-provider",
            },
        ],
    )
    child_sidecar = b"language_compatibility: hermes-legacy\n"
    child_path.with_name("child.hermes.yaml").write_bytes(child_sidecar)
    child_command = child_path.parents[1] / "commands/review.md"
    child_command.parent.mkdir()
    child_command.write_text(
        "child $producer.output and $$HOME\n",
        encoding="utf-8",
    )
    root = _parse_source(
        root_path,
        source="project",
        precedence=1,
        sidecar_bytes=root_sidecar,
    )
    child = _parse_source(
        child_path,
        source="profile",
        precedence=2,
        sidecar_bytes=child_sidecar,
    )

    compilation = compile_workflow(
        root,
        WorkflowCatalogSnapshot.capture((root, child)),
        normalizer_version=4,
    )

    commands = {
        binding.node_id: binding
        for binding in compilation.dependency_manifest.resources
        if binding.resource_kind == "command"
    }
    root_binding = commands["root-review"]
    child_binding = commands["checks__review"]
    assert root_binding.source_relative_path == "commands/review.md"
    assert child_binding.source_relative_path == "commands/review.md"
    assert root_binding.package_key == "project:root"
    assert child_binding.package_key == "profile:child"
    assert root_binding.snapshot_path != child_binding.snapshot_path
    assert compilation.sealed_files[root_binding.snapshot_path] == b"root review\n"
    assert compilation.sealed_files[child_binding.snapshot_path] == (
        b"child $checks__producer.output and $$HOME\n"
    )
    from plugins.workflow.resources import ResourceResolver

    resolver = ResourceResolver(
        tmp_path,
        sealed_paths=compilation.covered_relative_paths,
        sealed_bytes=compilation.sealed_files,
    )
    assert resolver.command(child_binding.snapshot_path).body == (
        "child $checks__producer.output and $$HOME\n"
    )
    compiled_definition = yaml.safe_load(compilation.definition_bytes)
    compiled_nodes = {node["id"]: node for node in compiled_definition["nodes"]}
    assert compiled_nodes["root-review"]["command"] == root_binding.snapshot_path
    assert compiled_nodes["checks__review"]["command"] == child_binding.snapshot_path
    assert child_binding.source_digest != child_binding.compiled_digest
    assert compilation.dependency_manifest.dependencies[0].sidecar_status == (
        "authenticated_ignored"
    )
    assert set(compilation.covered_relative_paths) == set(compilation.sealed_files)
    assert compilation.composite_digest == composite_workflow_digest(
        compilation.dependency_manifest
    )
    assert all(str(tmp_path) not in repr(value) for value in (commands, compilation.dependency_manifest.to_dict()))


def test_compilation_risk_uses_composite_identity_and_origin_projection(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch child trust/policy being promoted instead of evaluating composition."""
    from plugins.workflow.compat import assess_compatibility
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
    from plugins.workflow.trust import build_risk_summary

    root_path = workflow_writer(
        tmp_path / "root/workflows",
        name="risk-root",
        filename="risk-root.yaml",
        nodes=[{"id": "checks", "include": "risk-child"}],
    )
    root_sidecar = (
        b"language_compatibility: archon-2026-07\n"
        b"outward_action_nodes: [checks]\n"
    )
    root_path.with_name("risk-root.hermes.yaml").write_bytes(root_sidecar)
    child_path = workflow_writer(
        tmp_path / "child/workflows",
        name="risk-child",
        filename="risk-child.yaml",
        nodes=[
            {
                "id": "shell",
                "bash": "true",
                "allowed_tools": ["Read"],
                "skills": ["reviewing"],
                "provider": "child-provider",
            }
        ],
    )
    child_sidecar = (
        b"language_compatibility: hermes-legacy\nrequired_secrets: [IGNORED]\n"
    )
    child_path.with_name("risk-child.hermes.yaml").write_bytes(child_sidecar)
    root = _parse_source(
        root_path,
        source="project",
        precedence=1,
        sidecar_bytes=root_sidecar,
    )
    child = _parse_source(
        child_path,
        source="profile",
        precedence=2,
        sidecar_bytes=child_sidecar,
    )
    compilation = compile_workflow(
        root,
        WorkflowCatalogSnapshot.capture((root, child)),
        normalizer_version=4,
    )

    summary = build_risk_summary(
        compilation.package,
        assess_compatibility(compilation.package),
        compilation=compilation,
    )

    assert summary.package_digest == compilation.composite_digest
    child_risk = next(
        item for item in summary.origin_risks if item.package_key == "profile:risk-child"
    )
    assert child_risk.shell_or_script_nodes == ("checks__shell",)
    assert child_risk.requested_tools == ("read_file",)
    assert child_risk.requested_skills == ("reviewing",)
    assert child_risk.providers == ("child-provider",)
    assert child_risk.outward_action_nodes == ("checks__shell",)
    assert summary.ignored_child_policies[0].package_key == "profile:risk-child"
    assert summary.ignored_child_policies[0].fields == (
        "language_compatibility",
        "required_secrets",
    )
    serialized = str(summary.to_dict())
    assert "IGNORED" not in serialized
    assert str(tmp_path) not in serialized


def test_sealer_binds_future_loop_command_handoff_without_live_reread(
    tmp_path: Path,
    workflow_writer,
) -> None:
    """Catch loop commands being omitted because authoring activates after sealing."""
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
    from plugins.workflow.dependency_manifest import seal_workflow_compilation

    path = workflow_writer(
        tmp_path / "loop/workflows",
        name="loop-root",
        filename="loop-root.yaml",
        nodes=[
            {
                "id": "refine",
                "loop": {
                    "prompt": "refine",
                    "until": "DONE",
                    "max_iterations": 2,
                },
            }
        ],
    )
    sidecar = b"language_compatibility: archon-2026-07\n"
    path.with_name("loop-root.hermes.yaml").write_bytes(sidecar)
    commands = tmp_path / "loop/commands"
    commands.mkdir()
    (commands / "refine.md").write_text("refine this\n", encoding="utf-8")
    source = _parse_source(
        path,
        source="project",
        precedence=1,
        sidecar_bytes=sidecar,
    )
    initial = compile_workflow(
        source,
        WorkflowCatalogSnapshot.capture((source,)),
        normalizer_version=4,
    )
    node = initial.package.definition.nodes[0]
    loop_value = dict(node.value)
    loop_value.pop("prompt")
    loop_value["command"] = "refine"
    command_node = replace(node, value=loop_value)
    command_definition = replace(
        initial.package.definition,
        nodes=(command_node,),
    )
    command_package = replace(initial.package, definition=command_definition)
    command_definition_bytes = yaml.safe_dump({
        "name": "loop-root",
        "description": "Portable workflow fixture",
        "nodes": [
            {
                "id": "refine",
                "loop": {
                    "command": "refine",
                    "until": "DONE",
                    "max_iterations": 2,
                },
            }
        ],
    }).encode("utf-8")

    manifest, sealed, _covered, _composite, bound_definition = (
        seal_workflow_compilation(
            root=source,
            dependencies=(),
            include_edges=(),
            package=command_package,
            definition_bytes=command_definition_bytes,
            active_policy_bytes=sidecar,
            bind_executable_resources=True,
        )
    )

    binding = next(
        item for item in manifest.resources if item.resource_kind == "loop_command"
    )
    assert binding.node_id == "refine"
    assert sealed[binding.snapshot_path] == b"refine this\n"
    assert yaml.safe_load(bound_definition)["nodes"][0]["loop"]["command"] == (
        binding.snapshot_path
    )
