"""Manifest-aware workflow candidate enumeration."""

from __future__ import annotations

import heapq
import os
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from plugins.workflow.models import WorkflowMarketplaceBinding
from plugins.workflow.trust import WorkflowResourceReadBudget

from .package import WorkflowMarketplaceError, load_distribution
from .provenance import InstalledPackageStore


_MANIFEST_NAME = "workflow-package.json"
_YAML_SUFFIXES = frozenset({".yaml", ".yml"})
_CATALOG_CAPACITY_CODES = frozenset({
    "package_file_count_limit",
    "package_file_size_limit",
    "package_total_size_limit",
    "package_traversal_limit",
})


class _DirectoryEntry(Protocol):
    name: str
    path: str

    def is_dir(self, *, follow_symlinks: bool = True) -> bool: ...

    def is_file(self, *, follow_symlinks: bool = True) -> bool: ...


WorkflowBindingResolver = Callable[[Path, str], WorkflowMarketplaceBinding | None]


def installed_binding_resolver(hermes_home: Path) -> WorkflowBindingResolver:
    """Return the profile's exact provenance-backed binding resolver."""

    return InstalledPackageStore(hermes_home).binding_for_workflow


@dataclass(frozen=True, slots=True)
class WorkflowCandidate:
    """One loose path or authenticated manifest-declared definition."""

    workflow_path: Path
    package_root: Path | None = None
    definition_bytes: bytes | None = None
    sidecar_path: Path | None = None
    sidecar_bytes: bytes | None = None
    marketplace_binding: WorkflowMarketplaceBinding | None = None

    def __post_init__(self) -> None:
        if self.package_root is None:
            if any(
                value is not None
                for value in (
                    self.definition_bytes,
                    self.sidecar_path,
                    self.sidecar_bytes,
                    self.marketplace_binding,
                )
            ):
                raise ValueError("loose workflow candidates cannot carry package data")
            return
        if not isinstance(self.definition_bytes, bytes):
            raise ValueError("package workflow candidates require immutable bytes")
        if (self.sidecar_path is None) != (self.sidecar_bytes is None):
            raise ValueError(
                "package companion path and bytes must be present together"
            )
        if self.sidecar_bytes is not None and not isinstance(self.sidecar_bytes, bytes):
            raise ValueError("package companion bytes must be immutable")
        if self.marketplace_binding is not None and not isinstance(
            self.marketplace_binding, WorkflowMarketplaceBinding
        ):
            raise ValueError("marketplace binding must be immutable workflow identity")


@dataclass(frozen=True, slots=True)
class WorkflowCandidateFailure:
    """One invalid package root isolated from sibling discovery."""

    package_root: Path
    error: WorkflowMarketplaceError
    catalog_capacity: bool = False


def _directory_entries(directory: Path) -> Iterable[_DirectoryEntry]:
    with os.scandir(directory) as entries:
        yield from entries


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _is_package_marker(name: str) -> bool:
    return unicodedata.normalize("NFC", name).casefold() == _MANIFEST_NAME


def _contains_workflow_package_marker(location: Path) -> bool:
    with os.scandir(location) as entries:
        return any(_is_package_marker(entry.name) for entry in entries)


def _is_yaml_definition(path: Path) -> bool:
    return path.suffix.lower() in _YAML_SUFFIXES and not path.name.endswith(
        ".hermes.yaml"
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _scan_location(
    location: Path,
    *,
    excluded_top_level: frozenset[str],
    consume_entry: Callable[[], None] | None,
    directory_entries: Callable[[Path], Iterable[_DirectoryEntry]],
    follow_file_symlinks: bool,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    try:
        if location.is_file():
            return (), (location,) if location.suffix.lower() in _YAML_SUFFIXES else ()
        if not location.is_dir():
            return (), ()
    except OSError:
        raise

    package_roots: set[Path] = set()
    yaml_paths: list[Path] = []
    pending = [location]
    while pending:
        directory = pending.pop()
        entries: list[_DirectoryEntry] = []
        for entry in directory_entries(directory):
            if consume_entry is not None:
                consume_entry()
            entries.append(entry)
        child_directories: list[Path] = []
        for entry in sorted(entries, key=lambda item: item.name):
            if directory == location and entry.name in excluded_top_level:
                continue
            path = Path(entry.path)
            if _is_package_marker(entry.name):
                package_roots.add(directory)
                continue
            if entry.is_dir(follow_symlinks=False):
                child_directories.append(path)
                continue
            if entry.is_file(follow_symlinks=follow_file_symlinks) and (
                _is_yaml_definition(path)
            ):
                yaml_paths.append(path)
        pending.extend(reversed(child_directories))
    return (
        tuple(
            sorted(package_roots, key=lambda item: (len(item.parts), item.as_posix()))
        ),
        tuple(sorted(yaml_paths, key=lambda item: item.as_posix())),
    )


def _outer_package_roots(package_roots: tuple[Path, ...]) -> tuple[Path, ...]:
    outer: list[Path] = []
    for root in package_roots:
        if not any(_is_within(root, parent) for parent in outer):
            outer.append(root)
    return tuple(outer)


def _enumerate_workflow_candidate_records(
    location: Path,
    *,
    excluded_top_level: frozenset[str],
    binding_resolver: WorkflowBindingResolver | None,
    consume_entry: Callable[[], None] | None,
    directory_entries: Callable[[Path], Iterable[_DirectoryEntry]],
    follow_file_symlinks: bool,
    result_limit: int | None,
    read_budget: WorkflowResourceReadBudget | None,
) -> tuple[tuple[WorkflowCandidate | WorkflowCandidateFailure, ...], bool]:
    if result_limit is not None and (
        isinstance(result_limit, bool) or result_limit < 0
    ):
        raise ValueError("workflow candidate result limit must be non-negative")
    absolute_location = _absolute(Path(location).expanduser())
    package_roots, yaml_paths = _scan_location(
        absolute_location,
        excluded_top_level=excluded_top_level,
        consume_entry=consume_entry,
        directory_entries=directory_entries,
        follow_file_symlinks=follow_file_symlinks,
    )
    outer_roots = _outer_package_roots(package_roots)
    loose_paths = tuple(
        path
        for path in yaml_paths
        if not any(_is_within(path, package_root) for package_root in outer_roots)
    )
    entries: list[tuple[str, int, Path, WorkflowCandidate | None]] = [
        (package_root.as_posix(), 0, package_root, None) for package_root in outer_roots
    ]
    entries.extend(
        (
            path.as_posix(),
            1,
            path,
            WorkflowCandidate(workflow_path=path),
        )
        for path in loose_paths
    )
    heapq.heapify(entries)
    results: list[WorkflowCandidate | WorkflowCandidateFailure] = []
    while entries:
        if result_limit is not None and len(results) >= result_limit:
            return tuple(results), True
        _sort_path, entry_kind, path, candidate = heapq.heappop(entries)
        if entry_kind == 1:
            assert candidate is not None
            results.append(candidate)
            continue
        package_root = path
        try:
            distribution = load_distribution(package_root, read_budget=read_budget)
            files = {item.relative_path: item for item in distribution.files}
            package_entries: list[tuple[str, int, Path, WorkflowCandidate | None]] = []
            for member in sorted(
                distribution.manifest.workflows,
                key=lambda item: item.definition,
            ):
                binding = (
                    binding_resolver(distribution.root, member.definition)
                    if binding_resolver is not None
                    else None
                )
                if binding is not None and not isinstance(
                    binding, WorkflowMarketplaceBinding
                ):
                    raise ValueError(
                        "workflow marketplace binding resolver returned invalid identity"
                    )
                definition = files[member.definition]
                companion = (
                    files[member.companion] if member.companion is not None else None
                )
                workflow_candidate = WorkflowCandidate(
                    workflow_path=definition.path,
                    package_root=distribution.root,
                    definition_bytes=definition.content,
                    sidecar_path=companion.path if companion is not None else None,
                    sidecar_bytes=companion.content if companion is not None else None,
                    marketplace_binding=binding,
                )
                pending_entry: tuple[str, int, Path, WorkflowCandidate | None] = (
                    definition.path.as_posix(),
                    1,
                    definition.path,
                    workflow_candidate,
                )
                package_entries.append(pending_entry)
            entries.extend(package_entries)
            heapq.heapify(entries)
        except WorkflowMarketplaceError as exc:
            catalog_capacity = exc.code in _CATALOG_CAPACITY_CODES
            results.append(
                WorkflowCandidateFailure(
                    package_root=package_root,
                    error=exc,
                    catalog_capacity=catalog_capacity,
                )
            )
            if read_budget is not None and exc.message.startswith(
                "shared package resource"
            ):
                return tuple(results), True
    return tuple(results), False


def enumerate_workflow_candidates_isolated(
    location: Path,
    *,
    excluded_top_level: frozenset[str] = frozenset(),
    binding_resolver: WorkflowBindingResolver | None = None,
    consume_entry: Callable[[], None] | None = None,
    directory_entries: Callable[[Path], Iterable[_DirectoryEntry]] = _directory_entries,
    follow_file_symlinks: bool = True,
) -> tuple[WorkflowCandidate | WorkflowCandidateFailure, ...]:
    """Return candidates while representing each invalid package independently."""

    results, truncated = _enumerate_workflow_candidate_records(
        location,
        excluded_top_level=excluded_top_level,
        binding_resolver=binding_resolver,
        consume_entry=consume_entry,
        directory_entries=directory_entries,
        follow_file_symlinks=follow_file_symlinks,
        result_limit=None,
        read_budget=None,
    )
    assert truncated is False
    return results


def _enumerate_workflow_candidates_for_catalog(
    location: Path,
    *,
    excluded_top_level: frozenset[str],
    result_limit: int,
    read_budget: WorkflowResourceReadBudget,
    consume_entry: Callable[[], None],
    directory_entries: Callable[[Path], Iterable[_DirectoryEntry]],
    follow_file_symlinks: bool,
    binding_resolver: WorkflowBindingResolver | None = None,
) -> tuple[tuple[WorkflowCandidate | WorkflowCandidateFailure, ...], bool]:
    """Return a bounded catalog batch without loading later package roots."""

    return _enumerate_workflow_candidate_records(
        location,
        excluded_top_level=excluded_top_level,
        binding_resolver=binding_resolver,
        consume_entry=consume_entry,
        directory_entries=directory_entries,
        follow_file_symlinks=follow_file_symlinks,
        result_limit=result_limit,
        read_budget=read_budget,
    )


def enumerate_workflow_candidates(
    location: Path,
    *,
    excluded_top_level: frozenset[str] = frozenset(),
    binding_resolver: WorkflowBindingResolver | None = None,
) -> tuple[WorkflowCandidate, ...]:
    """Return declared package definitions plus loose YAML outside package roots."""

    results = enumerate_workflow_candidates_isolated(
        location,
        excluded_top_level=excluded_top_level,
        binding_resolver=binding_resolver,
    )
    for item in results:
        if isinstance(item, WorkflowCandidateFailure):
            raise item.error
    return tuple(item for item in results if isinstance(item, WorkflowCandidate))


__all__ = [
    "WorkflowBindingResolver",
    "WorkflowCandidate",
    "WorkflowCandidateFailure",
    "enumerate_workflow_candidates",
    "enumerate_workflow_candidates_isolated",
    "installed_binding_resolver",
]
