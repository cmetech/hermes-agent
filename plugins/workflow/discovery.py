"""Deterministic explicit/project/profile workflow discovery."""

from __future__ import annotations

import hashlib
from pathlib import Path

from plugins.workflow.compilation import (
    WorkflowCatalogSnapshot,
    clear_compilation_cache,
    compile_workflow,
)
from plugins.workflow.models import (
    ValidationIssue,
    WorkflowPackage,
    WorkflowSourceDocument,
    WorkflowValidationError,
)
from plugins.workflow.schema import parse_workflow_source_bytes

_PARSE_CACHE: dict[
    tuple[str, str, int],
    tuple[tuple[str, str | None], WorkflowSourceDocument],
] = {}
_PROFILE_STATE_DIRECTORIES = frozenset({"runs", ".staging", ".quarantine", ".locks"})


def clear_discovery_cache() -> None:
    _PARSE_CACHE.clear()
    clear_compilation_cache()


def _yaml_paths(
    location: Path,
    *,
    excluded_top_level: frozenset[str] = frozenset(),
) -> tuple[Path, ...]:
    if location.is_file():
        return (location,) if location.suffix.lower() in {".yaml", ".yml"} else ()
    if not location.is_dir():
        return ()
    paths = (
        path
        for path in location.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".yaml", ".yml"}
        and not path.name.endswith(".hermes.yaml")
        and (
            not excluded_top_level
            or path.relative_to(location).parts[0] not in excluded_top_level
        )
    )
    return tuple(sorted(paths, key=lambda item: item.resolve().as_posix()))


def _load_cached(
    path: Path, *, source: str, precedence: int
) -> WorkflowSourceDocument:
    resolved = path.resolve(strict=True)
    workflow_bytes = resolved.read_bytes()
    workflow_digest = hashlib.sha256(workflow_bytes).hexdigest()
    companion = resolved.with_name(f"{resolved.stem}.hermes.yaml")
    if companion.is_file():
        sidecar_bytes = companion.read_bytes()
        sidecar_digest = hashlib.sha256(sidecar_bytes).hexdigest()
    else:
        sidecar_bytes = None
        sidecar_digest = None
    signature = (workflow_digest, sidecar_digest)
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
    )
    _PARSE_CACHE[key] = (signature, source_document)
    return source_document


def discover_workflows(
    workdir: str | Path,
    hermes_home: str | Path,
    user_home: str | Path,
    *,
    explicit_path: str | Path | None = None,
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
            and (location / "workflows").is_dir()
        ):
            scan_location = location / "workflows"
        for path in _yaml_paths(
            scan_location,
            excluded_top_level=(
                _PROFILE_STATE_DIRECTORIES if source == "profile" else frozenset()
            ),
        ):
            source_documents.append(
                _load_cached(path, source=source, precedence=precedence)
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
