"""Immutable workflow catalog capture and pre-admission compilation."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

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

    def __post_init__(self) -> None:
        if not isinstance(self.package, WorkflowPackage):
            raise ValueError("compiled package must be a WorkflowPackage")
        if not isinstance(self.definition_bytes, bytes):
            raise ValueError("compiled definition must be immutable bytes")
        if not isinstance(self.active_policy_bytes, bytes):
            raise ValueError("compiled active policy must be immutable bytes")


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
    cached = _COMPILED_ROOT_CACHE.pop(cache_key, None)
    if cached is not None:
        _COMPILED_ROOT_CACHE[cache_key] = cached
        return cached

    from plugins.workflow.schema import _compile_workflow_source_document

    source_to_compile = root
    definition_bytes = root.definition_bytes
    has_includes = any(node.node_type == "include" for node in root.nodes)
    from plugins.workflow.includes import expand_workflow_source
    from plugins.workflow.language import (
        resolve_language_profile,
        select_normalizer_version,
        supports_phase4_semantics,
    )

    selection = resolve_language_profile(root.sidecar)
    selected_version = select_normalizer_version(selection, normalizer_version)
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
    compiled = WorkflowCompilation(
        package=package,
        definition_bytes=definition_bytes,
        active_policy_bytes=root.sidecar_bytes or b"",
    )
    if COMPILED_ROOT_CACHE_MAX_ENTRIES > 0:
        _COMPILED_ROOT_CACHE[cache_key] = compiled
        while len(_COMPILED_ROOT_CACHE) > COMPILED_ROOT_CACHE_MAX_ENTRIES:
            _COMPILED_ROOT_CACHE.popitem(last=False)
    return compiled
