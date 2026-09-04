"""Deterministic explicit/project/profile workflow discovery."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from plugins.workflow.compilation import (
    WorkflowCatalogSnapshot,
    clear_compilation_cache,
    compile_workflow,
)
from plugins.workflow.marketplace.discovery import (
    WorkflowBindingResolver,
    WorkflowCandidate,
    enumerate_workflow_candidates,
)
from plugins.workflow.models import (
    ValidationIssue,
    WorkflowMarketplaceBinding,
    WorkflowPackage,
    WorkflowSourceDocument,
    WorkflowValidationError,
)
from plugins.workflow.schema import parse_workflow_source_bytes

_PARSE_CACHE: dict[
    tuple[str, str, int],
    tuple[
        tuple[
            str,
            str | None,
            str | None,
            str | None,
            WorkflowMarketplaceBinding | None,
        ],
        WorkflowSourceDocument,
    ],
] = {}
_PROFILE_STATE_DIRECTORIES = frozenset({"runs", ".staging", ".quarantine", ".locks"})


def clear_discovery_cache() -> None:
    _PARSE_CACHE.clear()
    clear_compilation_cache()


def _load_cached(
    candidate: WorkflowCandidate, *, source: str, precedence: int
) -> WorkflowSourceDocument:
    resolved = (
        candidate.workflow_path.resolve(strict=True)
        if candidate.package_root is None
        else candidate.workflow_path
    )
    workflow_bytes = (
        resolved.read_bytes()
        if candidate.definition_bytes is None
        else candidate.definition_bytes
    )
    workflow_digest = hashlib.sha256(workflow_bytes).hexdigest()
    companion = (
        resolved.with_name(f"{resolved.stem}.hermes.yaml")
        if candidate.package_root is None
        else candidate.sidecar_path
    )
    if candidate.package_root is not None:
        sidecar_bytes = candidate.sidecar_bytes
        sidecar_digest = (
            hashlib.sha256(sidecar_bytes).hexdigest()
            if sidecar_bytes is not None
            else None
        )
    elif companion is not None and companion.is_file():
        sidecar_bytes = companion.read_bytes()
        sidecar_digest = hashlib.sha256(sidecar_bytes).hexdigest()
    else:
        sidecar_bytes = None
        sidecar_digest = None
    signature = (
        workflow_digest,
        sidecar_digest,
        str(candidate.package_root) if candidate.package_root is not None else None,
        str(companion) if companion is not None else None,
        candidate.marketplace_binding,
    )
    key = (str(resolved), source, precedence)
    cached = _PARSE_CACHE.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    source_document = parse_workflow_source_bytes(
        resolved,
        workflow_bytes=workflow_bytes,
        sidecar_bytes=sidecar_bytes,
        source=source,
        precedence=precedence,
        package_root=candidate.package_root,
        sidecar_path=companion if sidecar_bytes is not None else None,
        marketplace_binding=candidate.marketplace_binding,
    )
    _PARSE_CACHE[key] = (signature, source_document)
    return source_document


def discover_workflows(
    workdir: str | Path,
    hermes_home: str | Path,
    user_home: str | Path,
    *,
    explicit_path: str | Path | None = None,
    binding_resolver: WorkflowBindingResolver | None = None,
) -> tuple[WorkflowPackage, ...]:
    """Discover workflows without creating directories or mutating profile state."""
    del (
        user_home
    )  # Reserved for portable resource resolution; never used for branded discovery.
    locations: list[tuple[str, int, Path]] = []
    if explicit_path is not None:
        locations.append(("explicit", 0, Path(explicit_path).expanduser()))
    locations.extend([
        ("project", 1, Path(workdir).expanduser() / ".hermes" / "workflows"),
        ("profile", 2, Path(hermes_home).expanduser() / "workflows"),
    ])
    source_documents: list[WorkflowSourceDocument] = []
    for source, precedence, location in locations:
        scan_location = location
        if (
            source == "explicit"
            and location.is_dir()
            and not os.path.lexists(location / "workflow-package.json")
            and (location / "workflows").is_dir()
        ):
            scan_location = location / "workflows"
        for candidate in enumerate_workflow_candidates(
            scan_location,
            excluded_top_level=(
                _PROFILE_STATE_DIRECTORIES if source == "profile" else frozenset()
            ),
            binding_resolver=binding_resolver,
        ):
            source_documents.append(
                _load_cached(candidate, source=source, precedence=precedence)
            )
    snapshot = WorkflowCatalogSnapshot.capture(source_documents)
    if snapshot.ambiguous_names:
        name = min(snapshot.ambiguous_names)
        candidates = [
            source_document
            for source_document in source_documents
            if source_document.name == name
        ]
        candidates_by_precedence: dict[int, list[WorkflowSourceDocument]] = {}
        for candidate in candidates:
            candidates_by_precedence.setdefault(candidate.precedence, []).append(
                candidate
            )
        duplicate_precedence = min(
            precedence
            for precedence, precedence_candidates in candidates_by_precedence.items()
            if len(precedence_candidates) > 1
        )
        duplicates = candidates_by_precedence[duplicate_precedence]
        raise WorkflowValidationError(
            ValidationIssue(
                path=duplicates[1].definition_location,
                code="duplicate_workflow_name",
                message=(
                    f"duplicate workflow name at {duplicates[0].source} "
                    f"precedence: {name}"
                ),
            )
        )
    for source_document in source_documents:
        compile_workflow(source_document, snapshot)
    return tuple(
        compile_workflow(snapshot.selected[name], snapshot).package
        for name in sorted(snapshot.selected)
    )
