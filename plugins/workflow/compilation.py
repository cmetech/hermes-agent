"""Immutable workflow catalog capture and pre-admission compilation."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from plugins.workflow.dependency_manifest import (
    WorkflowDependencyManifest,
    composite_workflow_digest,
    digest_expanded_compilation,
)
from plugins.workflow.models import WorkflowPackage, WorkflowSourceDocument


def _source_signature(source: WorkflowSourceDocument) -> str:
    identity = {
        "definition_digest": hashlib.sha256(source.definition_bytes).hexdigest(),
        "definition_location": source.definition_location,
        "name": source.name,
        "precedence": source.precedence,
        "sidecar_digest": (
            hashlib.sha256(source.sidecar_bytes).hexdigest()
            if source.sidecar_bytes is not None
            else None
        ),
        "sidecar_location": source.sidecar_location,
        "source": source.source,
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class WorkflowCatalogSnapshot:
    selected: Mapping[str, WorkflowSourceDocument]
    ambiguous_names: frozenset[str]
    signatures: Mapping[str, str]

    def __post_init__(self) -> None:
        selected = MappingProxyType(dict(sorted(self.selected.items())))
        signatures = MappingProxyType(dict(sorted(self.signatures.items())))
        ambiguous = frozenset(self.ambiguous_names)
        if set(selected) != set(signatures):
            raise ValueError("catalog signatures must cover every selected source")
        if set(selected).intersection(ambiguous):
            raise ValueError("ambiguous sources cannot also be selected")
        for name, source in selected.items():
            if name != source.name:
                raise ValueError("catalog selection name does not match its source")
            if signatures[name] != _source_signature(source):
                raise ValueError("catalog source signature does not match its content")
        object.__setattr__(self, "selected", selected)
        object.__setattr__(self, "ambiguous_names", ambiguous)
        object.__setattr__(self, "signatures", signatures)

    @classmethod
    def capture(
        cls, sources: Iterable[WorkflowSourceDocument]
    ) -> "WorkflowCatalogSnapshot":
        by_name: dict[str, list[WorkflowSourceDocument]] = {}
        for source in sources:
            if not isinstance(source, WorkflowSourceDocument):
                raise ValueError("catalog sources must be workflow source documents")
            by_name.setdefault(source.name, []).append(source)
        selected: dict[str, WorkflowSourceDocument] = {}
        ambiguous: set[str] = set()
        for name, candidates in by_name.items():
            candidates_by_precedence: dict[int, list[WorkflowSourceDocument]] = {}
            for candidate in candidates:
                candidates_by_precedence.setdefault(candidate.precedence, []).append(
                    candidate
                )
            if any(
                len(precedence_candidates) != 1
                for precedence_candidates in candidates_by_precedence.values()
            ):
                ambiguous.add(name)
                continue
            winning_precedence = min(item.precedence for item in candidates)
            selected[name] = candidates_by_precedence[winning_precedence][0]
        return cls(
            selected=selected,
            ambiguous_names=frozenset(ambiguous),
            signatures={
                name: _source_signature(source) for name, source in selected.items()
            },
        )

    def select(
        self, name: str, catalog_source: str | None = None
    ) -> WorkflowSourceDocument:
        if name in self.ambiguous_names:
            raise KeyError(name)
        source = self.selected.get(name)
        if source is None or (
            catalog_source is not None and source.source != catalog_source
        ):
            raise KeyError(name)
        return source


@dataclass(frozen=True, slots=True)
class WorkflowCompilation:
    package: WorkflowPackage
    definition_bytes: bytes
    active_policy_bytes: bytes
    dependency_manifest: WorkflowDependencyManifest
    sealed_files: Mapping[str, bytes]
    composite_digest: str
    covered_relative_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.package, WorkflowPackage):
            raise ValueError("compiled package must be a WorkflowPackage")
        if not isinstance(self.definition_bytes, bytes):
            raise ValueError("compiled definition must be immutable bytes")
        if not isinstance(self.active_policy_bytes, bytes):
            raise ValueError("compiled active policy must be immutable bytes")
        if not isinstance(self.dependency_manifest, WorkflowDependencyManifest):
            raise ValueError("compiled dependency manifest must be exact and immutable")
        if (
            digest_expanded_compilation(self.definition_bytes, self.package)
            != self.dependency_manifest.expanded_definition_digest
        ):
            raise ValueError(
                "compiled definition or package graph does not match its manifest"
            )
        if (
            hashlib.sha256(self.active_policy_bytes).hexdigest()
            != self.dependency_manifest.active_root_policy_digest
        ):
            raise ValueError("compiled active policy does not match its manifest")
        sealed_files = MappingProxyType({
            str(path): bytes(data) for path, data in self.sealed_files.items()
        })
        if tuple(sorted(sealed_files)) != tuple(self.covered_relative_paths):
            raise ValueError("compiled covered paths must match every sealed file")
        bindings_by_path = {
            binding.snapshot_path: binding
            for binding in self.dependency_manifest.resources
        }
        if set(bindings_by_path) != set(sealed_files):
            raise ValueError("compiled manifest must bind every sealed file")
        for path, data in sealed_files.items():
            binding = bindings_by_path[path]
            if (
                len(data) != binding.compiled_byte_size
                or hashlib.sha256(data).hexdigest() != binding.compiled_digest
            ):
                raise ValueError("compiled sealed file digest does not match manifest")
        if self.composite_digest != composite_workflow_digest(
            self.dependency_manifest
        ):
            raise ValueError("compiled composite digest does not match its manifest")
        object.__setattr__(self, "sealed_files", sealed_files)
        object.__setattr__(
            self,
            "covered_relative_paths",
            tuple(self.covered_relative_paths),
        )


COMPILED_ROOT_CACHE_MAX_ENTRIES = 128

_COMPILED_ROOT_CACHE: OrderedDict[
    tuple[
        str,
        str,
        int | None,
        tuple[tuple[str, str, str], ...],
    ],
    WorkflowCompilation,
] = OrderedDict()


def clear_compilation_cache() -> None:
    """Release cached compiled packages and their authenticated byte streams."""
    _COMPILED_ROOT_CACHE.clear()


def compile_workflow(
    root: WorkflowSourceDocument,
    catalog: WorkflowCatalogSnapshot,
    normalizer_version: int | None = None,
) -> WorkflowCompilation:
    """Compile one bounded root closure from an immutable catalog snapshot."""
    if not isinstance(root, WorkflowSourceDocument):
        raise ValueError("root must be a workflow source document")
    if not isinstance(catalog, WorkflowCatalogSnapshot):
        raise ValueError("catalog must be an immutable workflow catalog snapshot")
    cache_key = (
        str(root.workflow_path),
        _source_signature(root),
        normalizer_version,
        tuple(
            (
                name,
                signature,
                str(catalog.selected[name].workflow_path),
            )
            for name, signature in catalog.signatures.items()
        ),
    )
    from plugins.workflow.language import (
        resolve_language_profile,
        select_normalizer_version,
        supports_phase4_semantics,
    )

    cached = _COMPILED_ROOT_CACHE.pop(cache_key, None)
    if cached is not None and not supports_phase4_semantics(
        cached.package.language.effective_profile,
        cached.package.language.normalizer_version,
    ):
        _COMPILED_ROOT_CACHE[cache_key] = cached
        return cached

    from plugins.workflow.schema import _compile_workflow_source_document

    source_to_compile = root
    definition_bytes = root.definition_bytes
    has_includes = any(node.node_type == "include" for node in root.nodes)
    from plugins.workflow.includes import expand_workflow_source
    selection = resolve_language_profile(root.sidecar)
    selected_version = select_normalizer_version(selection, normalizer_version)
    expanded = None
    if supports_phase4_semantics(selection.effective_profile, selected_version):
        expanded = expand_workflow_source(root, catalog)
        if has_includes:
            definition_bytes = expanded.canonical_definition_bytes
            source_to_compile = replace(
                root,
                nodes=expanded.nodes,
                definition_bytes=definition_bytes,
            )

    package = _compile_workflow_source_document(
        source_to_compile,
        normalizer_version=normalizer_version,
    )
    from plugins.workflow.dependency_manifest import (
        composite_workflow_digest,
        digest_expanded_compilation,
        seal_workflow_compilation,
    )
    from plugins.workflow.includes import collect_include_edges

    active_policy_bytes = (
        root.sidecar_bytes if root.sidecar_bytes is not None else b"{}\n"
    )
    (
        dependency_manifest,
        sealed_files,
        covered_paths,
        composite_digest,
        bound_definition_bytes,
    ) = (
        seal_workflow_compilation(
            root=root,
            dependencies=expanded.dependencies if expanded is not None else (),
            include_edges=(
                collect_include_edges(root, catalog)
                if expanded is not None and has_includes
                else ()
            ),
            package=package,
            definition_bytes=definition_bytes,
            active_policy_bytes=active_policy_bytes,
            bind_executable_resources=supports_phase4_semantics(
                selection.effective_profile,
                selected_version,
            ),
        )
    )
    if supports_phase4_semantics(selection.effective_profile, selected_version):
        from plugins.workflow.language import bind_v4_loop_command_semantics

        package = bind_v4_loop_command_semantics(
            package,
            {
                binding.node_id: binding.snapshot_path
                for binding in dependency_manifest.resources
                if binding.resource_kind == "loop_command"
                and binding.node_id is not None
            },
        )
        dependency_manifest = replace(
            dependency_manifest,
            expanded_definition_digest=digest_expanded_compilation(
                bound_definition_bytes,
                package,
            ),
        )
        composite_digest = composite_workflow_digest(dependency_manifest)
    compiled = WorkflowCompilation(
        package=package,
        definition_bytes=bound_definition_bytes,
        active_policy_bytes=active_policy_bytes,
        dependency_manifest=dependency_manifest,
        sealed_files=sealed_files,
        composite_digest=composite_digest,
        covered_relative_paths=covered_paths,
    )
    if cached is not None and compiled == cached:
        compiled = cached
    if COMPILED_ROOT_CACHE_MAX_ENTRIES > 0:
        _COMPILED_ROOT_CACHE[cache_key] = compiled
        while len(_COMPILED_ROOT_CACHE) > COMPILED_ROOT_CACHE_MAX_ENTRIES:
            _COMPILED_ROOT_CACHE.popitem(last=False)
    return compiled
