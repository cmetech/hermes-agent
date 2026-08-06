"""Exact immutable dependency identity for compiled workflow closures."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Iterable, Mapping

import yaml

from plugins.workflow.models import (
    WorkflowNodeOrigin,
    WorkflowPackage,
    WorkflowSourceDocument,
    WorkflowValidationError,
)
from plugins.workflow.resources import parse_command_resource
from plugins.workflow.schema import (
    is_inline_script,
    validate_authenticated_resource_references,
)
from plugins.workflow.trust import (
    WORKFLOW_RESOURCE_MAX_FILE_BYTES,
    WORKFLOW_RESOURCE_MAX_FILES,
    WORKFLOW_RESOURCE_MAX_TOTAL_BYTES,
    WorkflowResourceCapacityError,
    WorkflowResourceReadBudget,
    _contained_resource,
    _validation_error,
    _walk_strings,
)


WORKFLOW_DEPENDENCY_MANIFEST_SCHEMA_VERSION = 1
WORKFLOW_MANIFEST_MAX_DEPENDENCIES = 64
WORKFLOW_MANIFEST_MAX_INCLUDE_EDGES = 4096
WORKFLOW_MANIFEST_MAX_EXPANDED_NODES = 512
WORKFLOW_MANIFEST_MAX_FILES = 512
WORKFLOW_MANIFEST_MAX_FILE_BYTES = 1 * 1024 * 1024
WORKFLOW_MANIFEST_MAX_TOTAL_BYTES = 8 * 1024 * 1024
WORKFLOW_MANIFEST_MAX_IGNORED_POLICY_FIELDS = 64

_SHA256 = re.compile(r"[0-9a-f]{64}")
_PACKAGE_FIELDS = frozenset({
    "package_key",
    "workflow_name",
    "catalog_source",
    "precedence",
    "definition_location",
    "definition_digest",
    "sidecar_location",
    "sidecar_digest",
    "sidecar_status",
    "ignored_policy_fields",
    "package_digest",
    "covered_relative_paths",
})
_INCLUDE_EDGE_FIELDS = frozenset({
    "include_instance_path",
    "source_package_key",
    "source_node_id",
    "target_package_key",
})
_NODE_ORIGIN_FIELDS = frozenset({
    "include_instance_path",
    "package_key",
    "workflow_name",
    "catalog_source",
    "precedence",
    "definition_location",
    "source_index",
    "source_line",
    "expanded_node_id",
})
_RESOURCE_FIELDS = frozenset({
    "binding_id",
    "node_id",
    "resource_kind",
    "package_key",
    "source_relative_path",
    "source_digest",
    "compiled_digest",
    "source_byte_size",
    "compiled_byte_size",
    "media_type",
    "snapshot_path",
})
_COUNT_FIELDS = frozenset({
    "dependency_packages",
    "include_edges",
    "expanded_nodes",
    "authenticated_files",
    "authenticated_bytes",
    "sealed_files",
    "sealed_bytes",
})
_MANIFEST_FIELDS = frozenset({
    "schema_version",
    "root",
    "dependencies",
    "include_edges",
    "node_origins",
    "resources",
    "counts",
    "expanded_definition_digest",
    "node_origins_digest",
    "resource_bindings_digest",
    "active_root_policy_digest",
})
_SIDECAR_STATUSES = frozenset({"absent", "active", "authenticated_ignored"})
_RESOURCE_KINDS = frozenset({
    "definition",
    "sidecar",
    "command",
    "named_script",
    "mcp",
    "mcp_resource",
    "loop_command",
})


def _exact_mapping(
    value: object,
    fields: frozenset[str],
    label: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{label} must contain exact fields")
    return value


def _bounded_text(value: object, label: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 4096 or "\0" in value:
        raise ValueError(f"{label} must be bounded non-empty text")
    return value


def _logical_path(value: object, label: str) -> str:
    text = _bounded_text(value, label)
    assert isinstance(text, str)
    candidate = PurePosixPath(text)
    if (
        "\\" in text
        or text.startswith("~")
        or candidate.is_absolute()
        or candidate.as_posix() != text
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError(f"{label} must be a contained logical path")
    return text


def _sha256(value: object, label: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _bounded_integer(
    value: object,
    label: str,
    *,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > maximum
    ):
        raise ValueError(f"{label} exceeds its exact bound")
    return value


def _bounded_sequence(
    value: object,
    label: str,
    *,
    maximum: int,
) -> list | tuple:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{label} must be an ordered sequence")
    if len(value) > maximum:
        raise ValueError(f"{label} collection exceeds its exact bound")
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _logical_graph_value(value: object) -> object:
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _logical_graph_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, tuple | list):
        return [_logical_graph_value(item) for item in value]
    if isinstance(value, set | frozenset):
        projected = [_logical_graph_value(item) for item in value]
        return sorted(projected, key=_canonical_json)
    if is_dataclass(value):
        return {
            field.name: _logical_graph_value(getattr(value, field.name))
            for field in fields(value)
        }
    raise TypeError(
        f"workflow package graph contains unsupported {type(value).__name__} value"
    )


def _definition_graph(definition) -> dict[str, object]:
    return {
        "name": definition.name,
        "description": definition.description,
        "nodes": [
            {
                "id": node.id,
                "node_type": node.node_type,
                "value": _logical_graph_value(node.value),
                "depends_on": list(node.depends_on),
                "source_index": node.source_index,
                "source_line": node.source_line,
                "options": _logical_graph_value(node.options),
                "origin": (
                    _origin_to_dict(node.origin)
                    if node.origin is not None
                    else None
                ),
            }
            for node in definition.nodes
        ],
        "options": _logical_graph_value(definition.options),
    }


def digest_workflow_package_graph(package: WorkflowPackage) -> str:
    """Hash the complete logical package graph without host filesystem paths."""
    if not isinstance(package, WorkflowPackage):
        raise ValueError("package graph digest requires a WorkflowPackage")
    language = package.language
    graph = {
        "source_definition": _definition_graph(package.source_definition),
        "definition": _definition_graph(package.definition),
        "sidecar": _logical_graph_value(package.sidecar),
        "source": package.source,
        "precedence": package.precedence,
        "language": {
            "declared_profile": (
                language.declared_profile.value
                if language.declared_profile is not None
                else None
            ),
            "effective_profile": language.effective_profile.value,
            "normalizer_version": language.normalizer_version,
            "normalized_definition_digest": language.normalized_definition_digest,
            "structured_outputs": _logical_graph_value(
                language.structured_outputs
            ),
            "node_semantics": _logical_graph_value(language.node_semantics),
        },
        "compatibility_findings": _logical_graph_value(
            package.compatibility_findings
        ),
        "validation_issues": _logical_graph_value(package.validation_issues),
    }
    return hashlib.sha256(_canonical_json(graph)).hexdigest()


def digest_expanded_compilation(
    definition_bytes: bytes,
    package: WorkflowPackage,
) -> str:
    """Bind exact executable bytes and the final logical graph in one digest."""
    if not isinstance(definition_bytes, bytes):
        raise ValueError("expanded compilation definition must be immutable bytes")
    identity = {
        "definition_bytes_digest": hashlib.sha256(definition_bytes).hexdigest(),
        "package_graph_digest": digest_workflow_package_graph(package),
    }
    return hashlib.sha256(_canonical_json(identity)).hexdigest()


@dataclass(frozen=True, slots=True)
class WorkflowDependencyRecord:
    package_key: str
    workflow_name: str
    catalog_source: str
    precedence: int
    definition_location: str
    definition_digest: str
    sidecar_location: str | None
    sidecar_digest: str | None
    sidecar_status: str
    ignored_policy_fields: tuple[str, ...]
    package_digest: str
    covered_relative_paths: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: object) -> "WorkflowDependencyRecord":
        value = _exact_mapping(raw, _PACKAGE_FIELDS, "dependency record")
        status = _bounded_text(value["sidecar_status"], "sidecar status")
        if status not in _SIDECAR_STATUSES:
            raise ValueError("sidecar status is unsupported")
        sidecar_location = value["sidecar_location"]
        sidecar_digest = value["sidecar_digest"]
        if status == "absent":
            if sidecar_location is not None or sidecar_digest is not None:
                raise ValueError("absent sidecar status cannot carry sidecar identity")
        elif sidecar_location is None or sidecar_digest is None:
            raise ValueError("authenticated sidecar status requires sidecar identity")
        paths_value = _bounded_sequence(
            value["covered_relative_paths"],
            "covered relative path",
            maximum=WORKFLOW_MANIFEST_MAX_FILES,
        )
        paths = tuple(
            _logical_path(path, "covered relative path") for path in paths_value
        )
        if not paths or paths != tuple(sorted(set(paths))):
            raise ValueError("covered relative paths must use canonical order")
        ignored_value = _bounded_sequence(
            value["ignored_policy_fields"],
            "ignored policy field",
            maximum=WORKFLOW_MANIFEST_MAX_IGNORED_POLICY_FIELDS,
        )
        ignored_fields = tuple(
            str(_bounded_text(item, "ignored policy field"))
            for item in ignored_value
        )
        if ignored_fields != tuple(sorted(set(ignored_fields))):
            raise ValueError("ignored policy fields must use canonical order")
        if status != "authenticated_ignored" and ignored_fields:
            raise ValueError("only ignored sidecars may carry ignored policy fields")
        return cls(
            package_key=str(_bounded_text(value["package_key"], "package key")),
            workflow_name=str(
                _bounded_text(value["workflow_name"], "workflow name")
            ),
            catalog_source=str(
                _bounded_text(value["catalog_source"], "catalog source")
            ),
            precedence=_bounded_integer(
                value["precedence"], "precedence", maximum=2**31 - 1
            ),
            definition_location=_logical_path(
                value["definition_location"], "definition location"
            ),
            definition_digest=str(
                _sha256(value["definition_digest"], "definition digest")
            ),
            sidecar_location=(
                _logical_path(sidecar_location, "sidecar location")
                if sidecar_location is not None
                else None
            ),
            sidecar_digest=(
                str(_sha256(sidecar_digest, "sidecar digest"))
                if sidecar_digest is not None
                else None
            ),
            sidecar_status=str(status),
            ignored_policy_fields=ignored_fields,
            package_digest=str(
                _sha256(value["package_digest"], "package digest")
            ),
            covered_relative_paths=paths,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "package_key": self.package_key,
            "workflow_name": self.workflow_name,
            "catalog_source": self.catalog_source,
            "precedence": self.precedence,
            "definition_location": self.definition_location,
            "definition_digest": self.definition_digest,
            "sidecar_location": self.sidecar_location,
            "sidecar_digest": self.sidecar_digest,
            "sidecar_status": self.sidecar_status,
            "ignored_policy_fields": list(self.ignored_policy_fields),
            "package_digest": self.package_digest,
            "covered_relative_paths": list(self.covered_relative_paths),
        }


@dataclass(frozen=True, slots=True)
class WorkflowResourceBinding:
    binding_id: str
    node_id: str | None
    resource_kind: str
    package_key: str
    source_relative_path: str
    source_digest: str
    compiled_digest: str
    source_byte_size: int
    compiled_byte_size: int
    media_type: str | None
    snapshot_path: str

    @classmethod
    def from_dict(cls, raw: object) -> "WorkflowResourceBinding":
        value = _exact_mapping(raw, _RESOURCE_FIELDS, "resource binding")
        kind = _bounded_text(value["resource_kind"], "resource kind")
        if kind not in _RESOURCE_KINDS:
            raise ValueError("resource kind is unsupported")
        snapshot_path = _logical_path(value["snapshot_path"], "snapshot path")
        if not snapshot_path.startswith("packages/"):
            raise ValueError("snapshot path must start with packages/")
        return cls(
            binding_id=str(_bounded_text(value["binding_id"], "binding id")),
            node_id=_bounded_text(value["node_id"], "node id", nullable=True),
            resource_kind=str(kind),
            package_key=str(_bounded_text(value["package_key"], "package key")),
            source_relative_path=_logical_path(
                value["source_relative_path"], "source relative path"
            ),
            source_digest=str(_sha256(value["source_digest"], "source digest")),
            compiled_digest=str(
                _sha256(value["compiled_digest"], "compiled digest")
            ),
            source_byte_size=_bounded_integer(
                value["source_byte_size"],
                "source byte size",
                maximum=WORKFLOW_MANIFEST_MAX_FILE_BYTES,
            ),
            compiled_byte_size=_bounded_integer(
                value["compiled_byte_size"],
                "compiled byte size",
                maximum=WORKFLOW_MANIFEST_MAX_FILE_BYTES,
            ),
            media_type=_bounded_text(
                value["media_type"], "media type", nullable=True
            ),
            snapshot_path=snapshot_path,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "binding_id": self.binding_id,
            "node_id": self.node_id,
            "resource_kind": self.resource_kind,
            "package_key": self.package_key,
            "source_relative_path": self.source_relative_path,
            "source_digest": self.source_digest,
            "compiled_digest": self.compiled_digest,
            "source_byte_size": self.source_byte_size,
            "compiled_byte_size": self.compiled_byte_size,
            "media_type": self.media_type,
            "snapshot_path": self.snapshot_path,
        }


@dataclass(frozen=True, slots=True)
class _WorkflowIncludeEdge:
    include_instance_path: tuple[str, ...]
    source_package_key: str
    source_node_id: str
    target_package_key: str

    @classmethod
    def from_dict(cls, raw: object) -> "_WorkflowIncludeEdge":
        value = _exact_mapping(raw, _INCLUDE_EDGE_FIELDS, "include edge")
        path_value = value["include_instance_path"]
        if not isinstance(path_value, list | tuple) or not 1 <= len(path_value) <= 3:
            raise ValueError("include instance path must use bounded logical segments")
        path = tuple(
            str(_bounded_text(item, "include instance id")) for item in path_value
        )
        return cls(
            include_instance_path=path,
            source_package_key=str(
                _bounded_text(value["source_package_key"], "source package key")
            ),
            source_node_id=str(
                _bounded_text(value["source_node_id"], "source node id")
            ),
            target_package_key=str(
                _bounded_text(value["target_package_key"], "target package key")
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "include_instance_path": list(self.include_instance_path),
            "source_package_key": self.source_package_key,
            "source_node_id": self.source_node_id,
            "target_package_key": self.target_package_key,
        }


@dataclass(frozen=True, slots=True)
class _WorkflowManifestCounts:
    dependency_packages: int
    include_edges: int
    expanded_nodes: int
    authenticated_files: int
    authenticated_bytes: int
    sealed_files: int
    sealed_bytes: int

    @classmethod
    def from_dict(cls, raw: object) -> "_WorkflowManifestCounts":
        value = _exact_mapping(raw, _COUNT_FIELDS, "manifest counts")
        return cls(
            dependency_packages=_bounded_integer(
                value["dependency_packages"],
                "dependency package count",
                maximum=WORKFLOW_MANIFEST_MAX_DEPENDENCIES,
            ),
            include_edges=_bounded_integer(
                value["include_edges"],
                "include edge count",
                maximum=WORKFLOW_MANIFEST_MAX_INCLUDE_EDGES,
            ),
            expanded_nodes=_bounded_integer(
                value["expanded_nodes"],
                "expanded node count",
                maximum=WORKFLOW_MANIFEST_MAX_EXPANDED_NODES,
            ),
            authenticated_files=_bounded_integer(
                value["authenticated_files"],
                "authenticated file count",
                maximum=WORKFLOW_MANIFEST_MAX_FILES,
            ),
            authenticated_bytes=_bounded_integer(
                value["authenticated_bytes"],
                "authenticated byte count",
                maximum=WORKFLOW_MANIFEST_MAX_TOTAL_BYTES,
            ),
            sealed_files=_bounded_integer(
                value["sealed_files"],
                "sealed file count",
                maximum=WORKFLOW_MANIFEST_MAX_FILES,
            ),
            sealed_bytes=_bounded_integer(
                value["sealed_bytes"],
                "sealed byte count",
                maximum=WORKFLOW_MANIFEST_MAX_TOTAL_BYTES,
            ),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "dependency_packages": self.dependency_packages,
            "include_edges": self.include_edges,
            "expanded_nodes": self.expanded_nodes,
            "authenticated_files": self.authenticated_files,
            "authenticated_bytes": self.authenticated_bytes,
            "sealed_files": self.sealed_files,
            "sealed_bytes": self.sealed_bytes,
        }


def _origin_from_dict(raw: object) -> WorkflowNodeOrigin:
    value = _exact_mapping(raw, _NODE_ORIGIN_FIELDS, "node origin")
    path_value = _bounded_sequence(
        value["include_instance_path"],
        "node origin include path",
        maximum=3,
    )
    return WorkflowNodeOrigin(
        include_instance_path=tuple(path_value),
        package_key=value["package_key"],
        workflow_name=value["workflow_name"],
        catalog_source=value["catalog_source"],
        precedence=value["precedence"],
        definition_location=value["definition_location"],
        source_index=value["source_index"],
        source_line=value["source_line"],
        expanded_node_id=value["expanded_node_id"],
    )


def _origin_to_dict(origin: WorkflowNodeOrigin) -> dict[str, object]:
    return {
        "include_instance_path": list(origin.include_instance_path),
        "package_key": origin.package_key,
        "workflow_name": origin.workflow_name,
        "catalog_source": origin.catalog_source,
        "precedence": origin.precedence,
        "definition_location": origin.definition_location,
        "source_index": origin.source_index,
        "source_line": origin.source_line,
        "expanded_node_id": origin.expanded_node_id,
    }


@dataclass(frozen=True, slots=True)
class WorkflowDependencyManifest:
    schema_version: int
    root: WorkflowDependencyRecord
    dependencies: tuple[WorkflowDependencyRecord, ...]
    include_edges: tuple[_WorkflowIncludeEdge, ...]
    node_origins: tuple[Mapping[str, object], ...]
    resources: tuple[WorkflowResourceBinding, ...]
    counts: _WorkflowManifestCounts
    expanded_definition_digest: str
    node_origins_digest: str
    resource_bindings_digest: str
    active_root_policy_digest: str

    @classmethod
    def from_dict(cls, raw: object) -> "WorkflowDependencyManifest":
        value = _exact_mapping(raw, _MANIFEST_FIELDS, "dependency manifest")
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"]
            != WORKFLOW_DEPENDENCY_MANIFEST_SCHEMA_VERSION
        ):
            raise ValueError("dependency manifest schema version is unsupported")
        dependencies_value = _bounded_sequence(
            value["dependencies"],
            "dependency",
            maximum=WORKFLOW_MANIFEST_MAX_DEPENDENCIES,
        )
        edges_value = _bounded_sequence(
            value["include_edges"],
            "include edge",
            maximum=WORKFLOW_MANIFEST_MAX_INCLUDE_EDGES,
        )
        origins_value = _bounded_sequence(
            value["node_origins"],
            "node origin",
            maximum=WORKFLOW_MANIFEST_MAX_EXPANDED_NODES,
        )
        resources_value = _bounded_sequence(
            value["resources"],
            "resource binding",
            maximum=WORKFLOW_MANIFEST_MAX_FILES,
        )
        dependencies = tuple(
            WorkflowDependencyRecord.from_dict(item)
            for item in dependencies_value
        )
        dependency_key = lambda item: (
            item.catalog_source,
            item.precedence,
            item.workflow_name,
            item.definition_location,
            item.package_digest,
        )
        if dependencies != tuple(sorted(dependencies, key=dependency_key)):
            raise ValueError("dependencies must use canonical order")
        edges = tuple(
            _WorkflowIncludeEdge.from_dict(item) for item in edges_value
        )
        edge_key = lambda item: (
            item.include_instance_path,
            item.source_package_key,
            item.source_node_id,
            item.target_package_key,
        )
        if edges != tuple(sorted(edges, key=edge_key)):
            raise ValueError("include edges must use canonical order")
        origins = tuple(_origin_from_dict(item) for item in origins_value)
        if origins != tuple(sorted(origins, key=lambda item: item.expanded_node_id)):
            raise ValueError("node origins must use canonical order")
        resources = tuple(
            WorkflowResourceBinding.from_dict(item) for item in resources_value
        )
        if resources != tuple(sorted(resources, key=lambda item: item.binding_id)):
            raise ValueError("resource bindings must use canonical order")
        if (
            len({binding.binding_id for binding in resources}) != len(resources)
            or len({binding.snapshot_path for binding in resources}) != len(resources)
        ):
            raise ValueError("resource bindings must use unique logical identities")
        node_origins_digest = str(
            _sha256(value["node_origins_digest"], "node origins digest")
        )
        if node_origins_digest != digest_node_origins(origins):
            raise ValueError("node origins digest does not match canonical origins")
        resource_bindings_digest = str(
            _sha256(value["resource_bindings_digest"], "resource bindings digest")
        )
        if resource_bindings_digest != digest_resource_bindings(resources):
            raise ValueError(
                "resource bindings digest does not match canonical bindings"
            )
        counts = _WorkflowManifestCounts.from_dict(value["counts"])
        root = WorkflowDependencyRecord.from_dict(value["root"])
        package_keys = {root.package_key, *(item.package_key for item in dependencies)}
        if len(package_keys) != len(dependencies) + 1:
            raise ValueError("manifest package keys must be unique")
        if any(binding.package_key not in package_keys for binding in resources):
            raise ValueError("resource binding refers to an unknown package")
        if any(
            edge.source_package_key not in package_keys
            or edge.target_package_key not in package_keys
            for edge in edges
        ):
            raise ValueError("include edge refers to an unknown package key")
        if any(origin.package_key not in package_keys for origin in origins):
            raise ValueError("node origin refers to an unknown package key")
        source_identities: dict[tuple[str, str], tuple[str, int]] = {}
        for binding in resources:
            key = (binding.package_key, binding.source_relative_path)
            identity = (binding.source_digest, binding.source_byte_size)
            previous = source_identities.setdefault(key, identity)
            if previous != identity:
                raise ValueError(
                    "resource bindings disagree on authenticated source identity"
                )
        covered_paths = {
            (record.package_key, path)
            for record in (root, *dependencies)
            for path in record.covered_relative_paths
        }
        if covered_paths != set(source_identities):
            raise ValueError(
                "manifest covered paths do not match unique resource bindings"
            )
        if (
            counts.dependency_packages != len(dependencies)
            or counts.include_edges != len(edges)
            or counts.expanded_nodes != len(origins)
            or counts.authenticated_files != len(source_identities)
            or counts.authenticated_bytes
            != sum(identity[1] for identity in source_identities.values())
            or counts.sealed_files != len(resources)
            or counts.sealed_bytes
            != sum(binding.compiled_byte_size for binding in resources)
        ):
            raise ValueError("manifest counts do not match canonical entries")
        if root.sidecar_status not in {"active", "absent"}:
            raise ValueError("root sidecar status must be active or absent")
        if any(
            dependency.sidecar_status not in {"authenticated_ignored", "absent"}
            for dependency in dependencies
        ):
            raise ValueError("dependency sidecars must be authenticated and ignored")
        frozen_origins = tuple(
            MappingProxyType({
                **_origin_to_dict(origin),
                "include_instance_path": tuple(origin.include_instance_path),
            })
            for origin in origins
        )
        return cls(
            schema_version=WORKFLOW_DEPENDENCY_MANIFEST_SCHEMA_VERSION,
            root=root,
            dependencies=dependencies,
            include_edges=edges,
            node_origins=frozen_origins,
            resources=resources,
            counts=counts,
            expanded_definition_digest=str(
                _sha256(
                    value["expanded_definition_digest"],
                    "expanded definition digest",
                )
            ),
            node_origins_digest=node_origins_digest,
            resource_bindings_digest=resource_bindings_digest,
            active_root_policy_digest=str(
                _sha256(
                    value["active_root_policy_digest"],
                    "active root policy digest",
                )
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "root": self.root.to_dict(),
            "dependencies": [item.to_dict() for item in self.dependencies],
            "include_edges": [item.to_dict() for item in self.include_edges],
            "node_origins": [
                {
                    **dict(item),
                    "include_instance_path": list(item["include_instance_path"]),
                }
                for item in self.node_origins
            ],
            "resources": [item.to_dict() for item in self.resources],
            "counts": self.counts.to_dict(),
            "expanded_definition_digest": self.expanded_definition_digest,
            "node_origins_digest": self.node_origins_digest,
            "resource_bindings_digest": self.resource_bindings_digest,
            "active_root_policy_digest": self.active_root_policy_digest,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.to_dict())


def composite_workflow_digest(manifest: WorkflowDependencyManifest) -> str:
    """Hash only the approved logical components of one compiled closure."""
    if not isinstance(manifest, WorkflowDependencyManifest):
        raise ValueError("composite digest requires a dependency manifest")
    dependencies = [
        {
            "workflow_name": item.workflow_name,
            "catalog_source": item.catalog_source,
            "precedence": item.precedence,
            "definition_location": item.definition_location,
            "package_digest": item.package_digest,
            "sidecar_status": item.sidecar_status,
        }
        for item in manifest.dependencies
    ]
    identity = {
        "schema_version": manifest.schema_version,
        "root_package_digest": manifest.root.package_digest,
        "dependencies": dependencies,
        "expanded_definition_digest": manifest.expanded_definition_digest,
        "node_origins_digest": manifest.node_origins_digest,
        "resource_bindings_digest": manifest.resource_bindings_digest,
        "active_root_policy_digest": manifest.active_root_policy_digest,
    }
    return hashlib.sha256(_canonical_json(identity)).hexdigest()


def digest_node_origins(origins: tuple[WorkflowNodeOrigin, ...]) -> str:
    """Return the canonical logical provenance digest used by format 2."""
    return hashlib.sha256(
        _canonical_json([_origin_to_dict(origin) for origin in origins])
    ).hexdigest()


def digest_resource_bindings(
    bindings: tuple[WorkflowResourceBinding, ...],
) -> str:
    """Return the canonical resource-binding digest used by format 2."""
    return hashlib.sha256(
        _canonical_json([binding.to_dict() for binding in bindings])
    ).hexdigest()
@dataclass(frozen=True, slots=True)
class _CompilationResourceUse:
    binding_id: str
    node_id: str | None
    resource_kind: str
    package_key: str
    source_relative_path: str
    source_bytes: bytes
    compiled_bytes: bytes
    media_type: str | None


def _source_package_key(source: WorkflowSourceDocument) -> str:
    return f"{source.source}:{source.name}"


def _digest_package_contents(contents: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(contents):
        data = contents[relative]
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def _source_resource_path(
    source: WorkflowSourceDocument,
    candidates: Iterable[Path],
    read_budget: WorkflowResourceReadBudget,
    *,
    kind: str,
    reference: str,
) -> Path:
    for candidate in candidates:
        if read_budget.has_cached(candidate) or candidate.exists() or candidate.is_symlink():
            return candidate
    raise _validation_error(
        reference,
        f"missing_{kind}",
        f"{kind.replace('_', ' ')} resource is missing: {reference}",
    )


def _source_command_path(
    source: WorkflowSourceDocument,
    name: str,
    read_budget: WorkflowResourceReadBudget,
) -> Path:
    base = source.root / "commands" / name
    return _source_resource_path(
        source,
        (base, base.with_suffix(".md")),
        read_budget,
        kind="command",
        reference=name,
    )


def _source_script_path(
    source: WorkflowSourceDocument,
    name: str,
    runtime: str,
    read_budget: WorkflowResourceReadBudget,
) -> Path:
    extension = ".py" if runtime == "uv" else ".ts"
    base = source.root / "scripts" / name
    candidates = (
        (base, base.with_suffix(extension), base.with_suffix(".js"))
        if runtime == "bun"
        else (base, base.with_suffix(extension))
    )
    return _source_resource_path(
        source,
        candidates,
        read_budget,
        kind="named_script",
        reference=name,
    )


def _source_mcp_path(
    source: WorkflowSourceDocument,
    reference: str,
    read_budget: WorkflowResourceReadBudget,
) -> Path:
    direct = source.root / reference
    return _source_resource_path(
        source,
        (
            direct,
            source.root / "mcp" / reference,
            (source.root / "mcp" / reference).with_suffix(".yaml"),
        ),
        read_budget,
        kind="mcp",
        reference=reference,
    )


def _read_source_resource(
    source: WorkflowSourceDocument,
    path: Path,
    read_budget: WorkflowResourceReadBudget,
) -> tuple[str, bytes]:
    try:
        logical_relative = path.relative_to(source.root).as_posix()
    except ValueError as exc:
        raise _validation_error(
            source.definition_location,
            "include_resource_invalid",
            "origin-bound workflow resource escapes its package",
        ) from exc
    try:
        root = source.root.resolve(strict=True)
    except OSError as exc:
        raise _validation_error(
            source.definition_location,
            "include_resource_invalid",
            "workflow package resource root is unavailable",
        ) from exc
    try:
        return _contained_resource(root, path, read_budget=read_budget)
    except WorkflowValidationError as exc:
        raise _validation_error(
            f"{source.definition_location}:{logical_relative}",
            "include_resource_invalid",
            f"origin-bound workflow resource is unsafe: {logical_relative}",
        ) from exc


def _command_compiled_bytes(raw: bytes, source_body: str, compiled_body: str) -> bytes:
    text = raw.decode("utf-8")
    if not text.endswith(source_body):
        raise ValueError("authenticated command body does not match source bytes")
    prefix = text[: len(text) - len(source_body)] if source_body else text
    return (prefix + compiled_body).encode("utf-8")


def _media_type(kind: str, relative: str) -> str | None:
    if kind == "command" or kind == "loop_command":
        return "text/markdown"
    if kind in {"definition", "sidecar", "mcp"}:
        return "application/yaml"
    if kind == "named_script":
        suffix = PurePosixPath(relative).suffix.lower()
        return {
            ".py": "text/x-python",
            ".js": "text/javascript",
            ".ts": "text/typescript",
        }.get(suffix, "text/plain")
    return None


def _binding_snapshot_path(
    source: WorkflowSourceDocument,
    package_digest: str,
    use: _CompilationResourceUse,
) -> str:
    package_identity = hashlib.sha256(
        json.dumps(
            {
                "catalog_source": source.source,
                "package_digest": package_digest,
                "precedence": source.precedence,
                "workflow_name": source.name,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    binding_identity = hashlib.sha256(
        json.dumps(
            {
                "binding_id": use.binding_id,
                "compiled_digest": hashlib.sha256(use.compiled_bytes).hexdigest(),
                "package_key": use.package_key,
                "resource_kind": use.resource_kind,
                "source_relative_path": use.source_relative_path,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return (
        f"packages/{package_identity}/{binding_identity}/"
        f"{use.source_relative_path}"
    )


def _rewrite_mcp_local_paths(value: object, paths: Mapping[str, str]) -> object:
    if isinstance(value, str):
        direct = paths.get(value)
        if direct is not None:
            return direct
        if value.startswith("-") and "=" in value:
            prefix, candidate = value.split("=", 1)
            rewritten = paths.get(candidate)
            if rewritten is not None:
                return f"{prefix}={rewritten}"
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _rewrite_mcp_local_paths(item, paths)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_rewrite_mcp_local_paths(item, paths) for item in value]
    return value


def _bind_definition_resources(
    definition_bytes: bytes,
    bindings: tuple[WorkflowResourceBinding, ...],
) -> bytes:
    executable = tuple(binding for binding in bindings if binding.node_id is not None)
    if not executable:
        return definition_bytes
    document = yaml.safe_load(definition_bytes)
    if not isinstance(document, dict) or not isinstance(document.get("nodes"), list):
        raise ValueError("expanded workflow definition must contain ordered nodes")
    by_node: dict[str, dict[str, list[WorkflowResourceBinding]]] = {}
    for binding in executable:
        assert binding.node_id is not None
        by_kind = by_node.setdefault(binding.node_id, {})
        by_kind.setdefault(binding.resource_kind, []).append(binding)
    for raw_node in document["nodes"]:
        if not isinstance(raw_node, dict) or not isinstance(raw_node.get("id"), str):
            raise ValueError("expanded workflow definition node is invalid")
        node_bindings = by_node.get(raw_node["id"], {})
        commands = node_bindings.get("command", ())
        if commands:
            raw_node["command"] = commands[0].snapshot_path
        scripts = node_bindings.get("named_script", ())
        if scripts:
            raw_node["script"] = scripts[0].snapshot_path
        mcp_bindings = sorted(
            node_bindings.get("mcp", ()),
            key=lambda binding: binding.binding_id,
        )
        if mcp_bindings:
            original = raw_node.get("mcp")
            bound_paths = [binding.snapshot_path for binding in mcp_bindings]
            raw_node["mcp"] = bound_paths[0] if isinstance(original, str) else bound_paths
        loop_commands = node_bindings.get("loop_command", ())
        if loop_commands:
            loop = raw_node.get("loop")
            if not isinstance(loop, dict):
                raise ValueError("loop command binding requires a loop mapping")
            loop["command"] = loop_commands[0].snapshot_path
    encoded = _canonical_json(document)
    if len(encoded) > 2 * 1024 * 1024:
        raise WorkflowResourceCapacityError(
            "expanded bound definition exceeds the compilation limit"
        )
    return encoded


def seal_workflow_compilation(
    *,
    root: WorkflowSourceDocument,
    dependencies: tuple[WorkflowSourceDocument, ...],
    include_edges: tuple[Mapping[str, object], ...],
    package: WorkflowPackage,
    definition_bytes: bytes,
    active_policy_bytes: bytes,
    bind_executable_resources: bool,
):
    """Authenticate and bind one immutable closure under one aggregate budget."""
    sources = (root, *dependencies)
    sources_by_key = {_source_package_key(source): source for source in sources}
    if len(sources_by_key) != len(sources):
        raise ValueError("workflow closure package keys must be unique")
    budget = WorkflowResourceReadBudget(
        max_file_bytes=WORKFLOW_RESOURCE_MAX_FILE_BYTES,
        max_total_bytes=WORKFLOW_RESOURCE_MAX_TOTAL_BYTES,
        max_files=WORKFLOW_RESOURCE_MAX_FILES,
    )
    contents_by_package: dict[str, dict[str, bytes]] = {}
    uses: list[_CompilationResourceUse] = []
    mcp_local_aliases: dict[str, set[str]] = {}
    command_source_bodies: dict[str, str] = {}
    command_raw: dict[str, tuple[bytes, str, str]] = {}
    script_raw: dict[str, bytes] = {}

    for source in sources:
        package_key = _source_package_key(source)
        contents: dict[str, bytes] = {}
        definition = budget.remember_authenticated(
            source.root / source.definition_location,
            source.definition_bytes,
        )
        contents[source.definition_location] = definition
        uses.append(
            _CompilationResourceUse(
                binding_id=f"{package_key}:definition",
                node_id=None,
                resource_kind="definition",
                package_key=package_key,
                source_relative_path=source.definition_location,
                source_bytes=definition,
                compiled_bytes=definition,
                media_type="application/yaml",
            )
        )
        if source.sidecar_bytes is not None:
            if source.sidecar_location is None:
                raise ValueError("authenticated sidecar bytes require a logical path")
            sidecar = budget.remember_authenticated(
                source.root / source.sidecar_location,
                source.sidecar_bytes,
            )
            contents[source.sidecar_location] = sidecar
            uses.append(
                _CompilationResourceUse(
                    binding_id=f"{package_key}:sidecar",
                    node_id=None,
                    resource_kind="sidecar",
                    package_key=package_key,
                    source_relative_path=source.sidecar_location,
                    source_bytes=sidecar,
                    compiled_bytes=sidecar,
                    media_type="application/yaml",
                )
            )
        contents_by_package[package_key] = contents

    for node in package.definition.nodes if bind_executable_resources else ():
        origin = node.origin
        if origin is None:
            raise ValueError("compiled Phase 4 nodes require exact logical origins")
        source = sources_by_key.get(origin.package_key)
        if source is None:
            raise ValueError("compiled node refers to an unknown source package")
        package_contents = contents_by_package[origin.package_key]
        if node.node_type == "command":
            path = _source_command_path(source, str(node.value), budget)
            relative, raw = _read_source_resource(source, path, budget)
            package_contents[relative] = raw
            try:
                body = parse_command_resource(source.root / relative, raw.decode("utf-8")).body
            except (UnicodeError, ValueError, yaml.YAMLError) as exc:
                raise _validation_error(
                    f"nodes[{node.source_index}].command",
                    "invalid_command_resource",
                    "authenticated command resource is invalid",
                ) from exc
            command_source_bodies[node.id] = body
            command_raw[node.id] = (raw, relative, "command")
        elif (
            node.node_type == "loop"
            and isinstance(node.value, Mapping)
            and isinstance(node.value.get("command"), str)
        ):
            path = _source_command_path(source, str(node.value["command"]), budget)
            relative, raw = _read_source_resource(source, path, budget)
            package_contents[relative] = raw
            try:
                body = parse_command_resource(
                    source.root / relative, raw.decode("utf-8")
                ).body
            except (UnicodeError, ValueError, yaml.YAMLError) as exc:
                raise _validation_error(
                    f"nodes[{node.source_index}].loop.command",
                    "invalid_command_resource",
                    "authenticated loop command resource is invalid",
                ) from exc
            command_source_bodies[node.id] = body
            command_raw[node.id] = (raw, relative, "loop_command")
        elif node.node_type == "script" and isinstance(node.value, str):
            if not is_inline_script(node.value):
                path = _source_script_path(
                    source,
                    node.value,
                    str(node.options["runtime"]),
                    budget,
                )
                relative, raw = _read_source_resource(source, path, budget)
                package_contents[relative] = raw
                script_raw[node.id] = raw

        mcp_value = node.options.get("mcp")
        references = (
            (mcp_value,)
            if isinstance(mcp_value, str)
            else tuple(mcp_value or ())
            if isinstance(mcp_value, tuple)
            else ()
        )
        for index, reference in enumerate(references):
            if not isinstance(reference, str):
                continue
            path = _source_mcp_path(source, reference, budget)
            relative, raw = _read_source_resource(source, path, budget)
            package_contents[relative] = raw
            uses.append(
                _CompilationResourceUse(
                    binding_id=f"{node.id}:mcp:{index}",
                    node_id=node.id,
                    resource_kind="mcp",
                    package_key=origin.package_key,
                    source_relative_path=relative,
                    source_bytes=raw,
                    compiled_bytes=raw,
                    media_type="application/yaml",
                )
            )
            try:
                document = yaml.safe_load(raw) or {}
            except yaml.YAMLError as exc:
                raise _validation_error(
                    reference,
                    "invalid_mcp",
                    f"invalid MCP definition: {reference}",
                ) from exc
            seen_local: set[str] = set()
            for raw_candidate in _walk_strings(document):
                candidates = [raw_candidate]
                if raw_candidate.startswith("-") and "=" in raw_candidate:
                    candidates.append(raw_candidate.split("=", 1)[1])
                for candidate in candidates:
                    if not candidate or candidate.startswith(("$", "-")):
                        continue
                    candidate_path = source.root / candidate
                    if not (
                        budget.has_cached(candidate_path)
                        or candidate_path.exists()
                        or candidate_path.is_symlink()
                    ):
                        continue
                    local_relative, local_raw = _read_source_resource(
                        source, candidate_path, budget
                    )
                    package_contents[local_relative] = local_raw
                    local_binding_id = (
                        f"{node.id}:mcp_resource:{index}:{local_relative}"
                    )
                    mcp_local_aliases.setdefault(local_binding_id, set()).add(
                        candidate
                    )
                    if local_relative in seen_local:
                        continue
                    seen_local.add(local_relative)
                    uses.append(
                        _CompilationResourceUse(
                            binding_id=local_binding_id,
                            node_id=node.id,
                            resource_kind="mcp_resource",
                            package_key=origin.package_key,
                            source_relative_path=local_relative,
                            source_bytes=local_raw,
                            compiled_bytes=local_raw,
                            media_type=None,
                        )
                    )

    validated = validate_authenticated_resource_references(
        package,
        command_bodies=command_source_bodies,
        named_script_bodies={
            node_id: raw.decode("utf-8", errors="surrogateescape")
            for node_id, raw in script_raw.items()
        },
    )
    for node_id, (raw, relative, resource_kind) in command_raw.items():
        node = next(item for item in package.definition.nodes if item.id == node_id)
        assert node.origin is not None
        compiled_body = (
            validated.command_bodies[node_id]
            if resource_kind == "command"
            else command_source_bodies[node_id]
        )
        uses.append(
            _CompilationResourceUse(
                binding_id=f"{node.id}:{resource_kind}",
                node_id=node.id,
                resource_kind=resource_kind,
                package_key=node.origin.package_key,
                source_relative_path=relative,
                source_bytes=raw,
                compiled_bytes=_command_compiled_bytes(
                    raw,
                    command_source_bodies[node_id],
                    compiled_body,
                ),
                media_type="text/markdown",
            )
        )
    for node_id, raw in script_raw.items():
        node = next(item for item in package.definition.nodes if item.id == node_id)
        assert node.origin is not None
        source = sources_by_key[node.origin.package_key]
        path = _source_script_path(
            source,
            str(node.value),
            str(node.options["runtime"]),
            budget,
        )
        relative = path.relative_to(source.root).as_posix()
        uses.append(
            _CompilationResourceUse(
                binding_id=f"{node.id}:named_script",
                node_id=node.id,
                resource_kind="named_script",
                package_key=node.origin.package_key,
                source_relative_path=relative,
                source_bytes=raw,
                compiled_bytes=validated.named_script_bodies[node_id].encode(
                    "utf-8", errors="surrogateescape"
                ),
                media_type=_media_type("named_script", relative),
            )
        )

    budget.seal()
    # Re-enter every live resource through the sealed authority so a changed
    # identity or cache miss cannot be hidden by the manifest construction.
    for source in sources:
        for relative in contents_by_package[_source_package_key(source)]:
            budget.read(source.root / relative)

    package_digests = {
        package_key: _digest_package_contents(contents)
        for package_key, contents in contents_by_package.items()
    }
    rewritten_uses: list[_CompilationResourceUse] = []
    for use in uses:
        if use.resource_kind != "mcp" or use.node_id is None:
            rewritten_uses.append(use)
            continue
        index = use.binding_id.rsplit(":", 1)[-1]
        prefix = f"{use.node_id}:mcp_resource:{index}:"
        local_uses = tuple(
            candidate
            for candidate in uses
            if candidate.binding_id.startswith(prefix)
        )
        local_paths: dict[str, str] = {}
        for candidate in local_uses:
            snapshot_path = _binding_snapshot_path(
                sources_by_key[candidate.package_key],
                package_digests[candidate.package_key],
                candidate,
            )
            local_paths[candidate.source_relative_path] = snapshot_path
            for alias in mcp_local_aliases.get(candidate.binding_id, ()):
                local_paths[alias] = snapshot_path
        if not local_paths:
            rewritten_uses.append(use)
            continue
        document = yaml.safe_load(use.source_bytes) or {}
        compiled = yaml.safe_dump(
            _rewrite_mcp_local_paths(document, local_paths),
            allow_unicode=True,
            sort_keys=True,
        ).encode("utf-8")
        rewritten_uses.append(
            _CompilationResourceUse(
                binding_id=use.binding_id,
                node_id=use.node_id,
                resource_kind=use.resource_kind,
                package_key=use.package_key,
                source_relative_path=use.source_relative_path,
                source_bytes=use.source_bytes,
                compiled_bytes=compiled,
                media_type=use.media_type,
            )
        )
    uses = rewritten_uses
    bindings: list[WorkflowResourceBinding] = []
    sealed_files: dict[str, bytes] = {}
    for use in uses:
        source = sources_by_key[use.package_key]
        snapshot_path = _binding_snapshot_path(
            source,
            package_digests[use.package_key],
            use,
        )
        if snapshot_path in sealed_files:
            raise ValueError("workflow resource snapshot path collision")
        if len(sealed_files) >= WORKFLOW_RESOURCE_MAX_FILES:
            raise WorkflowResourceCapacityError("package resource file limit exceeded")
        if len(use.compiled_bytes) > WORKFLOW_RESOURCE_MAX_FILE_BYTES:
            raise WorkflowResourceCapacityError(
                "package resource per-file limit exceeded"
            )
        if sum(len(data) for data in sealed_files.values()) + len(use.compiled_bytes) > WORKFLOW_RESOURCE_MAX_TOTAL_BYTES:
            raise WorkflowResourceCapacityError("package resource byte limit exceeded")
        sealed_files[snapshot_path] = use.compiled_bytes
        bindings.append(
            WorkflowResourceBinding(
                binding_id=use.binding_id,
                node_id=use.node_id,
                resource_kind=use.resource_kind,
                package_key=use.package_key,
                source_relative_path=use.source_relative_path,
                source_digest=hashlib.sha256(use.source_bytes).hexdigest(),
                compiled_digest=hashlib.sha256(use.compiled_bytes).hexdigest(),
                source_byte_size=len(use.source_bytes),
                compiled_byte_size=len(use.compiled_bytes),
                media_type=use.media_type,
                snapshot_path=snapshot_path,
            )
        )
    bindings_tuple = tuple(sorted(bindings, key=lambda item: item.binding_id))
    authenticated_sources = {
        (binding.package_key, binding.source_relative_path): (
            binding.source_digest,
            binding.source_byte_size,
        )
        for binding in bindings_tuple
    }
    bound_definition_bytes = _bind_definition_resources(
        definition_bytes,
        bindings_tuple,
    )
    origins = tuple(
        sorted(
            (node.origin for node in package.definition.nodes if node.origin is not None),
            key=lambda origin: origin.expanded_node_id,
        )
    )

    def package_document(
        source: WorkflowSourceDocument,
        *,
        root_package: bool,
    ) -> dict[str, object]:
        package_key = _source_package_key(source)
        return {
            "package_key": package_key,
            "workflow_name": source.name,
            "catalog_source": source.source,
            "precedence": source.precedence,
            "definition_location": source.definition_location,
            "definition_digest": hashlib.sha256(source.definition_bytes).hexdigest(),
            "sidecar_location": source.sidecar_location,
            "sidecar_digest": (
                hashlib.sha256(source.sidecar_bytes).hexdigest()
                if source.sidecar_bytes is not None
                else None
            ),
            "sidecar_status": (
                "active"
                if root_package and source.sidecar_bytes is not None
                else "authenticated_ignored"
                if source.sidecar_bytes is not None
                else "absent"
            ),
            "ignored_policy_fields": (
                sorted(str(field) for field in source.sidecar)
                if not root_package and source.sidecar_bytes is not None
                else []
            ),
            "package_digest": package_digests[package_key],
            "covered_relative_paths": sorted(contents_by_package[package_key]),
        }

    dependency_documents = sorted(
        (package_document(source, root_package=False) for source in dependencies),
        key=lambda item: (
            item["catalog_source"],
            item["precedence"],
            item["workflow_name"],
            item["definition_location"],
            item["package_digest"],
        ),
    )
    manifest_raw = {
        "schema_version": 1,
        "root": package_document(root, root_package=True),
        "dependencies": dependency_documents,
        "include_edges": sorted(
            (dict(edge) for edge in include_edges),
            key=lambda item: (
                tuple(item["include_instance_path"]),
                item["source_package_key"],
                item["source_node_id"],
                item["target_package_key"],
            ),
        ),
        "node_origins": [
            {
                "include_instance_path": list(origin.include_instance_path),
                "package_key": origin.package_key,
                "workflow_name": origin.workflow_name,
                "catalog_source": origin.catalog_source,
                "precedence": origin.precedence,
                "definition_location": origin.definition_location,
                "source_index": origin.source_index,
                "source_line": origin.source_line,
                "expanded_node_id": origin.expanded_node_id,
            }
            for origin in origins
        ],
        "resources": [binding.to_dict() for binding in bindings_tuple],
        "counts": {
            "dependency_packages": len(dependencies),
            "include_edges": len(include_edges),
            "expanded_nodes": len(origins),
            "authenticated_files": len(authenticated_sources),
            "authenticated_bytes": sum(
                identity[1] for identity in authenticated_sources.values()
            ),
            "sealed_files": len(sealed_files),
            "sealed_bytes": sum(len(data) for data in sealed_files.values()),
        },
        "expanded_definition_digest": digest_expanded_compilation(
            bound_definition_bytes,
            package,
        ),
        "node_origins_digest": digest_node_origins(origins),
        "resource_bindings_digest": digest_resource_bindings(bindings_tuple),
        "active_root_policy_digest": hashlib.sha256(active_policy_bytes).hexdigest(),
    }
    manifest = WorkflowDependencyManifest.from_dict(manifest_raw)
    return (
        manifest,
        sealed_files,
        tuple(sorted(sealed_files)),
        composite_workflow_digest(manifest),
        bound_definition_bytes,
    )
