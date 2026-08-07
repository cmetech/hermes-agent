"""Bounded, redacted workflow catalog projection for authenticated APIs."""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Literal, NotRequired, TypedDict

from plugins.workflow.compilation import (
    WorkflowCatalogSnapshot,
    WorkflowCompilation,
    compile_workflow,
)
from plugins.workflow.cli import (
    WorkflowDefinitionProjectionCapacityError,
    show_package,
)
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.input_contract import (
    API_INPUT_VALUE_MAX_BYTES,
    WorkflowInputContractError,
    workflow_input_declarations,
)
from plugins.workflow.language import language_projection, supports_phase4_semantics
from plugins.workflow.models import (
    WorkflowPackage,
    WorkflowSourceDocument,
    WorkflowValidationError,
)
from plugins.workflow.projection_limits import (
    WORKFLOW_DEFINITION_MAX_EDGES,
    WORKFLOW_DEFINITION_MAX_NODES,
)
from plugins.workflow.runner_binding import (
    ExecutionCapabilityContext,
    WorkflowRunnerBinding,
    assess_package_execution,
    background_execution_context,
    production_workflow_runner_binding,
)
from plugins.workflow.schema import parse_workflow_source_bytes
from plugins.workflow.sanitize import (
    sanitize_projection,
    sanitize_text,
    workflow_input_name_is_portable,
    workflow_input_names_are_portable,
)
from plugins.workflow.trust import (
    WORKFLOW_RESOURCE_MAX_FILE_BYTES,
    WORKFLOW_RESOURCE_MAX_FILES,
    WORKFLOW_RESOURCE_MAX_TOTAL_BYTES,
    WorkflowResourceCapacityError,
    WorkflowResourceReadBudget,
    WorkflowTrustError,
    WorkflowTrustStore,
    build_risk_summary,
)


logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from plugins.workflow.showcase import ShowcaseScenario, VerifiedShowcasePackage


CATALOG_LIMIT = 500
CATALOG_MAX_SCAN_ENTRIES = 4096
CATALOG_MAX_DEFINITION_FILE_BYTES = 2 * 1024 * 1024
CATALOG_MAX_DEFINITION_TOTAL_BYTES = 16 * 1024 * 1024
CATALOG_MAX_RESOURCE_FILE_BYTES = WORKFLOW_RESOURCE_MAX_FILE_BYTES
CATALOG_MAX_RESOURCE_TOTAL_BYTES = WORKFLOW_RESOURCE_MAX_TOTAL_BYTES
CATALOG_MAX_RESOURCE_FILES = WORKFLOW_RESOURCE_MAX_FILES
CATALOG_MAX_RESOURCE_REQUEST_BYTES = 2 * CATALOG_MAX_RESOURCE_TOTAL_BYTES
CATALOG_MAX_TRUST_STORE_BYTES = 4 * 1024 * 1024
_PROFILE_STATE_DIRECTORIES = frozenset({"runs", ".staging", ".quarantine", ".locks"})
_SUPPORTED_INPUT_TYPES = frozenset({"text", "string", "number", "boolean", "enum"})
_RICH_INPUT_FIELDS = frozenset({"items", "properties", "schema"})
_ENUM_INPUT_FIELDS = ("values", "enum", "options", "choices")
_ENUM_MAX_CHOICES = 128
_ENUM_MAX_CHOICE_LENGTH = 512
_STRUCTURED_OUTPUT_SUMMARY_LIMIT = 16
_STRUCTURED_OUTPUT_SUMMARY_TEXT_MAX_CHARS = 64
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400

CatalogSource = Literal["project", "profile", "showcase"]
CatalogTrustState = Literal["trusted", "untrusted", "verified_bundled"]
CatalogRunSupportReason = Literal[
    "supported",
    "unsupported_inputs",
    "schedule_required",
    "showcase_cli_required",
]


class CatalogInput(TypedDict):
    name: str
    type: str
    required: bool
    max_bytes: NotRequired[int]


class SupportedInputs(TypedDict):
    supported: bool
    reason: Literal[
        "parameterless",
        "flat_inputs",
        "unsupported_input_type",
        "unsupported_input_shape",
    ]


class CatalogRunSupport(TypedDict):
    supported: bool
    reason: CatalogRunSupportReason


class CatalogEntry(TypedDict):
    name: str
    version: str
    description: str
    requires_ai: bool
    source: CatalogSource
    precedence: int
    trust_state: CatalogTrustState
    inputs: list[CatalogInput]
    supported_inputs: SupportedInputs
    run_support: CatalogRunSupport
    language: dict[str, object]
    compatibility: NotRequired[dict[str, object]]
    structured_output_capability: NotRequired[dict[str, object]]


class InvalidCatalogEntry(TypedDict):
    name: str
    error: Literal["invalid_definition", "catalog_capacity"]


CatalogItem = CatalogEntry | InvalidCatalogEntry


class WorkflowCatalogCapacityError(RuntimeError):
    """The catalog cannot be enumerated within its fixed work budget."""


class WorkflowCatalogUnavailableError(RuntimeError):
    """The catalog filesystem cannot be enumerated safely."""


class WorkflowCatalogTrustUnavailableError(RuntimeError):
    """The trust store cannot classify a catalog entry safely."""


class WorkflowCatalogInvalidDefinitionError(RuntimeError):
    """The requested workflow references an invalid package resource."""


class WorkflowDetailNotFoundError(LookupError):
    """The requested workflow is absent from the bounded catalog."""


class WorkflowShowcaseVerificationError(RuntimeError):
    """The authenticated bundled showcase distribution failed verification."""


@dataclass(slots=True)
class _DirectoryScanBudget:
    max_entries: int
    entries_seen: int = 0

    def consume(self) -> None:
        self.entries_seen += 1
        if self.entries_seen >= self.max_entries:
            raise WorkflowCatalogCapacityError(
                "workflow catalog scan entry limit exceeded"
            )


@dataclass(slots=True)
class _DefinitionReadBudget:
    bytes_reserved: int = 0

    def reserve(self, sizes: tuple[int, ...]) -> None:
        for size in sizes:
            if size > CATALOG_MAX_DEFINITION_FILE_BYTES:
                raise WorkflowResourceCapacityError(
                    "workflow definition file limit exceeded"
                )
        reserved = sum(sizes)
        if self.bytes_reserved + reserved > CATALOG_MAX_DEFINITION_TOTAL_BYTES:
            raise WorkflowResourceCapacityError(
                "workflow definition byte limit exceeded"
            )
        self.bytes_reserved += reserved


class _UnsafeCatalogPath(OSError):
    """A catalog candidate could not be proven to be a contained regular file."""


def _catalog_file_identity(
    file_stat: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        stat.S_IFMT(file_stat.st_mode),
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _catalog_open_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    else:
        flags |= getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_BINARY", 0)
    return flags


def _contained_catalog_path(root: Path, candidate: Path) -> tuple[Path, Path]:
    root_absolute = Path(os.path.abspath(root))
    candidate_absolute = Path(os.path.abspath(candidate))
    try:
        relative = candidate_absolute.relative_to(root_absolute)
    except ValueError as exc:
        raise _UnsafeCatalogPath("workflow catalog file is unsafe") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise _UnsafeCatalogPath("workflow catalog file is unsafe")
    return root_absolute, relative


def _read_catalog_descriptor(file_descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = os.read(file_descriptor, min(remaining, 64 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _close_catalog_descriptors(descriptors: list[int]) -> None:
    while descriptors:
        descriptor = descriptors.pop()
        try:
            os.close(descriptor)
        except OSError:
            pass


def _reject_catalog_reparse_components(
    root: Path,
    relative: Path,
    *,
    missing_ok: bool,
) -> os.stat_result | None:
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise _UnsafeCatalogPath("workflow catalog root is unavailable") from exc
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or stat.S_ISLNK(root_stat.st_mode)
        or _is_reparse_point(root_stat)
    ):
        raise _UnsafeCatalogPath("workflow catalog root is unsafe")

    current = root
    for index, component in enumerate(relative.parts):
        current /= component
        try:
            component_stat = current.lstat()
        except FileNotFoundError as exc:
            if missing_ok and index == len(relative.parts) - 1:
                return None
            raise _UnsafeCatalogPath("workflow catalog file is unavailable") from exc
        except OSError as exc:
            raise _UnsafeCatalogPath("workflow catalog file is unavailable") from exc
        if stat.S_ISLNK(component_stat.st_mode) or _is_reparse_point(component_stat):
            raise _UnsafeCatalogPath("workflow catalog file is unsafe")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(
            component_stat.st_mode
        ):
            raise _UnsafeCatalogPath("workflow catalog file is unsafe")
    return component_stat


@dataclass(slots=True)
class _OpenedCatalogFile:
    descriptors: list[int]
    file_descriptor: int
    opened_stat: os.stat_result
    root: Path
    relative: Path
    parent_descriptor: int | None = None

    def read_stable(self) -> bytes:
        try:
            data = _read_catalog_descriptor(
                self.file_descriptor,
                self.opened_stat.st_size + 1,
            )
            descriptor_after = os.fstat(self.file_descriptor)
            if self.parent_descriptor is not None:
                path_after = os.stat(
                    self.relative.parts[-1],
                    dir_fd=self.parent_descriptor,
                    follow_symlinks=False,
                )
            else:
                path_after = _reject_catalog_reparse_components(
                    self.root,
                    self.relative,
                    missing_ok=False,
                )
            if (
                path_after is None
                or len(data) != self.opened_stat.st_size
                or _catalog_file_identity(self.opened_stat)
                != _catalog_file_identity(descriptor_after)
                or _catalog_file_identity(self.opened_stat)
                != _catalog_file_identity(path_after)
            ):
                raise _UnsafeCatalogPath("workflow catalog file changed during read")
            return data
        except _UnsafeCatalogPath:
            raise
        except (OSError, ValueError) as exc:
            raise _UnsafeCatalogPath("workflow catalog file is unavailable") from exc

    def close(self) -> None:
        _close_catalog_descriptors(self.descriptors)


def _open_posix_catalog_file(
    root: Path,
    relative: Path,
    *,
    missing_ok: bool,
) -> _OpenedCatalogFile | None:
    descriptors: list[int] = []
    try:
        directory_descriptor = os.open(root, _catalog_open_flags(directory=True))
        descriptors.append(directory_descriptor)
        root_stat = os.fstat(directory_descriptor)
        if not stat.S_ISDIR(root_stat.st_mode) or _is_reparse_point(root_stat):
            raise _UnsafeCatalogPath("workflow catalog root is unsafe")

        for component in relative.parts[:-1]:
            directory_descriptor = os.open(
                component,
                _catalog_open_flags(directory=True),
                dir_fd=directory_descriptor,
            )
            descriptors.append(directory_descriptor)
            directory_stat = os.fstat(directory_descriptor)
            if not stat.S_ISDIR(directory_stat.st_mode) or _is_reparse_point(
                directory_stat
            ):
                raise _UnsafeCatalogPath("workflow catalog directory is unsafe")

        filename = relative.parts[-1]
        try:
            before = os.stat(
                filename,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            if missing_ok:
                _close_catalog_descriptors(descriptors)
                return None
            raise
        if not stat.S_ISREG(before.st_mode) or _is_reparse_point(before):
            raise _UnsafeCatalogPath("workflow catalog file is unsafe")

        file_descriptor = os.open(
            filename,
            _catalog_open_flags(),
            dir_fd=directory_descriptor,
        )
        descriptors.append(file_descriptor)
        opened = os.fstat(file_descriptor)
        after_open = os.stat(
            filename,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse_point(opened)
            or _catalog_file_identity(before) != _catalog_file_identity(opened)
            or _catalog_file_identity(opened) != _catalog_file_identity(after_open)
        ):
            raise _UnsafeCatalogPath("workflow catalog file changed during open")
        return _OpenedCatalogFile(
            descriptors=descriptors,
            file_descriptor=file_descriptor,
            opened_stat=opened,
            root=root,
            relative=relative,
            parent_descriptor=directory_descriptor,
        )
    except _UnsafeCatalogPath:
        _close_catalog_descriptors(descriptors)
        raise
    except (OSError, ValueError) as exc:
        _close_catalog_descriptors(descriptors)
        raise _UnsafeCatalogPath("workflow catalog file is unavailable") from exc


def _open_fallback_catalog_file(
    root: Path,
    relative: Path,
    *,
    missing_ok: bool,
) -> _OpenedCatalogFile | None:
    file_descriptor: int | None = None
    try:
        before = _reject_catalog_reparse_components(
            root,
            relative,
            missing_ok=missing_ok,
        )
        if before is None:
            return None
        if not stat.S_ISREG(before.st_mode):
            raise _UnsafeCatalogPath("workflow catalog file is unsafe")
        file_descriptor = os.open(root / relative, _catalog_open_flags())
        opened = os.fstat(file_descriptor)
        after_open = _reject_catalog_reparse_components(
            root,
            relative,
            missing_ok=False,
        )
        if (
            after_open is None
            or not stat.S_ISREG(opened.st_mode)
            or _is_reparse_point(opened)
            or _catalog_file_identity(before) != _catalog_file_identity(opened)
            or _catalog_file_identity(opened) != _catalog_file_identity(after_open)
        ):
            raise _UnsafeCatalogPath("workflow catalog file changed during open")
        return _OpenedCatalogFile(
            descriptors=[file_descriptor],
            file_descriptor=file_descriptor,
            opened_stat=opened,
            root=root,
            relative=relative,
        )
    except _UnsafeCatalogPath:
        if file_descriptor is not None:
            _close_catalog_descriptors([file_descriptor])
        raise
    except (OSError, ValueError) as exc:
        if file_descriptor is not None:
            _close_catalog_descriptors([file_descriptor])
        raise _UnsafeCatalogPath("workflow catalog file is unavailable") from exc


def _open_contained_catalog_file(
    root: Path,
    candidate: Path,
    *,
    missing_ok: bool,
) -> _OpenedCatalogFile | None:
    root_absolute, relative = _contained_catalog_path(root, candidate)
    descriptor_relative_supported = (
        os.name != "nt"
        and hasattr(os, "O_NOFOLLOW")
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
    )
    if descriptor_relative_supported:
        return _open_posix_catalog_file(
            root_absolute,
            relative,
            missing_ok=missing_ok,
        )
    return _open_fallback_catalog_file(
        root_absolute,
        relative,
        missing_ok=missing_ok,
    )


def _error_entry(
    name: str,
    error: Literal["invalid_definition", "catalog_capacity"],
) -> InvalidCatalogEntry:
    return {"name": name[:128] or "invalid-workflow", "error": error}


def _directory_entries(directory: Path) -> Iterator[os.DirEntry[str]]:
    with os.scandir(directory) as entries:
        yield from entries


def _yaml_paths(
    location: Path,
    *,
    profile: bool,
    scan_budget: _DirectoryScanBudget,
) -> Iterator[Path]:
    try:
        mode = location.stat().st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        raise WorkflowCatalogUnavailableError(
            "workflow catalog root is unavailable"
        ) from exc
    if not stat.S_ISDIR(mode):
        return

    pending = [location]
    while pending:
        directory = pending.pop()
        children: list[tuple[str, Path, bool, bool]] = []
        try:
            for entry in _directory_entries(directory):
                scan_budget.consume()
                is_directory = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
                children.append((entry.name, Path(entry.path), is_directory, is_file))
        except WorkflowCatalogCapacityError:
            raise
        except OSError as exc:
            raise WorkflowCatalogUnavailableError(
                "workflow catalog enumeration is unavailable"
            ) from exc

        directories: list[Path] = []
        for name, path, is_directory, is_file in sorted(children):
            if is_directory:
                if not (
                    profile
                    and directory == location
                    and name in _PROFILE_STATE_DIRECTORIES
                ):
                    directories.append(path)
                continue
            if (
                is_file
                and path.suffix.lower() in {".yaml", ".yml"}
                and not name.endswith(".hermes.yaml")
            ):
                yield path
        pending.extend(reversed(directories))


def _catalog_candidates(
    workdir: Path, hermes_home: Path
) -> tuple[list[tuple[str, int, Path]], bool]:
    locations = (
        ("project", 1, workdir / ".hermes" / "workflows", False),
        ("profile", 2, hermes_home / "workflows", True),
    )
    scan_budget = _DirectoryScanBudget(CATALOG_MAX_SCAN_ENTRIES)
    candidates = [
        (source, precedence, path)
        for source, precedence, location, profile in locations
        for path in _yaml_paths(location, profile=profile, scan_budget=scan_budget)
    ]
    candidates.sort(key=lambda item: (item[1], item[2].as_posix()))
    return candidates[:CATALOG_LIMIT], len(candidates) > CATALOG_LIMIT


def _capture_catalog_source_documents(
    workdir: Path,
    hermes_home: Path,
) -> tuple[
    tuple[WorkflowSourceDocument, ...],
    tuple[InvalidCatalogEntry, ...],
    bool,
]:
    """Read one bounded, immutable project/profile source view."""
    invalid: list[InvalidCatalogEntry] = []
    candidates, truncated = _catalog_candidates(workdir, hermes_home)
    definition_budget = _DefinitionReadBudget()
    catalog_roots = {
        "project": workdir / ".hermes" / "workflows",
        "profile": hermes_home / "workflows",
    }
    by_location: dict[tuple[str, int], list[Path]] = {}
    for source, precedence, path in candidates:
        by_location.setdefault((source, precedence), []).append(path)
    source_documents: list[WorkflowSourceDocument] = []
    for (source, precedence), paths in by_location.items():
        level: dict[str, WorkflowSourceDocument] = {}
        duplicate_names: set[str] = set()
        for path in paths:
            workflow_file: _OpenedCatalogFile | None = None
            sidecar_file: _OpenedCatalogFile | None = None
            try:
                root = catalog_roots[source]
                workflow_file = _open_contained_catalog_file(
                    root,
                    path,
                    missing_ok=False,
                )
                if workflow_file is None:
                    raise _UnsafeCatalogPath("workflow catalog file is unavailable")
                sidecar_path = path.with_name(f"{path.stem}.hermes.yaml")
                sidecar_file = _open_contained_catalog_file(
                    root,
                    sidecar_path,
                    missing_ok=True,
                )
                definition_budget.reserve(
                    (
                        workflow_file.opened_stat.st_size,
                        *(
                            (sidecar_file.opened_stat.st_size,)
                            if sidecar_file is not None
                            else ()
                        ),
                    )
                )
                workflow_bytes = workflow_file.read_stable()
                sidecar_bytes = (
                    sidecar_file.read_stable() if sidecar_file is not None else None
                )
                source_document = parse_workflow_source_bytes(
                    path,
                    workflow_bytes=workflow_bytes,
                    sidecar_bytes=sidecar_bytes,
                    source=source,
                    precedence=precedence,
                )
            except WorkflowResourceCapacityError:
                invalid.append(_error_entry(path.stem, "catalog_capacity"))
                continue
            except (OSError, UnicodeError, WorkflowValidationError, ValueError):
                invalid.append(_error_entry(path.stem, "invalid_definition"))
                continue
            finally:
                if sidecar_file is not None:
                    sidecar_file.close()
                if workflow_file is not None:
                    workflow_file.close()
            name = source_document.name
            if not name.strip() or len(name) > 128:
                invalid.append(_error_entry(name, "invalid_definition"))
                continue
            if name in level:
                duplicate_names.add(name)
                level.pop(name, None)
                continue
            if name not in duplicate_names:
                level[name] = source_document
        invalid.extend(
            _error_entry(name, "invalid_definition") for name in sorted(duplicate_names)
        )
        source_documents.extend(level.values())

    return tuple(source_documents), tuple(invalid), truncated


def capture_workflow_catalog_snapshot(
    *,
    workdir: Path,
    hermes_home: Path,
    additional_sources: tuple[WorkflowSourceDocument, ...] = (),
) -> WorkflowCatalogSnapshot:
    """Capture the bounded project/profile catalog plus admission-local sources."""
    source_documents, _invalid, _truncated = _capture_catalog_source_documents(
        workdir,
        hermes_home,
    )
    return WorkflowCatalogSnapshot.capture((*source_documents, *additional_sources))


def _discover_catalog_compilations(
    workdir: Path,
    hermes_home: Path,
    *,
    normalizer_version: int | None = None,
) -> tuple[tuple[WorkflowCompilation | InvalidCatalogEntry, ...], bool]:
    source_documents, invalid_documents, truncated = (
        _capture_catalog_source_documents(workdir, hermes_home)
    )
    invalid = list(invalid_documents)

    raw_snapshot = WorkflowCatalogSnapshot.capture(source_documents)
    compiled_sources: list[WorkflowSourceDocument] = []
    compiled_by_source: dict[tuple[str, int, str], WorkflowCompilation] = {}
    for source_document in source_documents:
        if (
            len(source_document.nodes) > WORKFLOW_DEFINITION_MAX_NODES
            or sum(len(node.depends_on) for node in source_document.nodes)
            > WORKFLOW_DEFINITION_MAX_EDGES
        ):
            invalid.append(_error_entry(source_document.name, "catalog_capacity"))
            continue
        try:
            compiled = compile_workflow(
                source_document,
                raw_snapshot,
                normalizer_version=normalizer_version,
            )
        except (OSError, TypeError, UnicodeError, WorkflowValidationError, ValueError):
            invalid.append(_error_entry(source_document.name, "invalid_definition"))
            continue
        compiled_name = compiled.package.definition.name
        if (
            not compiled_name.strip()
            or len(compiled_name) > 128
            or compiled_name != source_document.name
        ):
            invalid.append(_error_entry(compiled_name, "invalid_definition"))
            continue
        compiled_sources.append(source_document)
        compiled_by_source[
            (
                source_document.source,
                source_document.precedence,
                str(source_document.workflow_path),
            )
        ] = compiled

    selected_snapshot = WorkflowCatalogSnapshot.capture(compiled_sources)
    selected = [
        compiled_by_source[(source.source, source.precedence, str(source.workflow_path))]
        for source in selected_snapshot.selected.values()
    ]
    return (
        tuple(
            sorted(
                [*selected, *invalid],
                key=lambda item: (
                    item.package.definition.name
                    if isinstance(item, WorkflowCompilation)
                    else item["name"]
                ),
            )
        ),
        truncated,
    )


def resolve_workflow_catalog_compilation(
    name: str,
    *,
    hermes_home: str | Path,
    workdir: str | Path,
    catalog_source: Literal["project", "profile"] | None = None,
    normalizer_version: int | None = None,
) -> WorkflowCompilation | None:
    """Resolve and compile one catalog closure exactly once for admission."""
    if not isinstance(name, str) or not name.strip() or len(name) > 128:
        return None
    discovered, _truncated = _discover_catalog_compilations(
        Path(workdir).expanduser().resolve(),
        Path(hermes_home).expanduser().resolve(),
        normalizer_version=normalizer_version,
    )
    for item in discovered:
        if (
            isinstance(item, WorkflowCompilation)
            and item.package.definition.name == name
        ):
            if catalog_source is not None and item.package.source != catalog_source:
                return None
            qualify_workflow_catalog_package(
                item.package,
                compatibility=assess_compatibility(item.package),
            )
            return item
        if isinstance(item, dict) and item.get("name") == name:
            if item.get("error") == "catalog_capacity":
                raise WorkflowCatalogCapacityError(
                    "workflow catalog entry exceeds a fixed capacity"
                )
            raise WorkflowCatalogInvalidDefinitionError(
                "workflow catalog entry is invalid"
            )
    return None


def resolve_workflow_catalog_package(
    name: str,
    *,
    hermes_home: str | Path,
    workdir: str | Path,
    catalog_source: Literal["project", "profile"] | None = None,
) -> WorkflowPackage | None:
    """Resolve one runnable catalog entry with list/detail failure isolation."""
    compilation = resolve_workflow_catalog_compilation(
        name,
        hermes_home=hermes_home,
        workdir=workdir,
        catalog_source=catalog_source,
    )
    return compilation.package if compilation is not None else None


def _input_projection(
    package: WorkflowPackage,
) -> tuple[list[CatalogInput], SupportedInputs]:
    delivery = package.sidecar.get("delivery_defaults")
    if delivery is None:
        return [], {"supported": True, "reason": "parameterless"}
    if not isinstance(delivery, Mapping):
        return [], {"supported": False, "reason": "unsupported_input_shape"}
    raw_inputs = delivery.get("inputs")
    if raw_inputs is None or raw_inputs == {}:
        return [], {"supported": True, "reason": "parameterless"}
    if not isinstance(raw_inputs, Mapping) or len(raw_inputs) > 64:
        return [], {"supported": False, "reason": "unsupported_input_shape"}

    try:
        declarations = workflow_input_declarations(package)
    except WorkflowInputContractError:
        declarations = None

    inputs: list[CatalogInput] = []
    unsupported_type = False
    unsupported_shape = declarations is None or not workflow_input_names_are_portable(
        raw_inputs
    )
    for raw_name, raw in sorted(raw_inputs.items(), key=lambda item: str(item[0])):
        if (
            not isinstance(raw_name, str)
            or not desktop_input_name_is_representable(raw_name)
            or not isinstance(raw, Mapping)
        ):
            unsupported_shape = True
            continue
        declaration = declarations.get(raw_name) if declarations is not None else None
        declared_type = raw.get("type", raw.get("kind"))
        if "type" in raw and "kind" in raw and raw.get("type") != raw.get("kind"):
            unsupported_shape = True
        if (
            not isinstance(declared_type, str)
            or not declared_type
            or len(declared_type) > 64
        ):
            declared_type = "unknown"
            unsupported_shape = True
        required = raw.get("required", True)
        if not isinstance(required, bool):
            required = True
            unsupported_shape = True
        if _RICH_INPUT_FIELDS.intersection(raw):
            unsupported_shape = True
        if declared_type not in _SUPPORTED_INPUT_TYPES:
            unsupported_type = True
        if declared_type == "enum" and not _enum_choices_supported(raw):
            unsupported_shape = True
        projected: CatalogInput = {
            "name": raw_name,
            "type": declared_type,
            "required": required,
        }
        if declaration is not None and declared_type == "text":
            projected["max_bytes"] = declaration.byte_bound(
                channel="text",
                store_limit=API_INPUT_VALUE_MAX_BYTES,
            )
        inputs.append(projected)

    if unsupported_type:
        classification: SupportedInputs = {
            "supported": False,
            "reason": "unsupported_input_type",
        }
    elif unsupported_shape:
        classification = {
            "supported": False,
            "reason": "unsupported_input_shape",
        }
    else:
        classification = {"supported": True, "reason": "flat_inputs"}
    return inputs, classification


def workflow_catalog_run_support(
    package: WorkflowPackage,
    *,
    showcase_scenario: "ShowcaseScenario | None" = None,
    input_support: SupportedInputs | None = None,
    schedule_at: str | None = None,
) -> CatalogRunSupport:
    """Derive Desktop background-run support from authenticated server data."""
    if input_support is None:
        if showcase_scenario is None:
            _inputs, input_support = _input_projection(package)
        else:
            _inputs, input_support = _showcase_input_projection(
                package, showcase_scenario
            )
    if not input_support["supported"]:
        return {"supported": False, "reason": "unsupported_inputs"}
    if showcase_scenario is not None:
        from plugins.workflow.showcase import showcase_background_api_eligible

        if showcase_scenario.requires_network:
            return {"supported": False, "reason": "showcase_cli_required"}
        if showcase_scenario.interaction_mode == "schedule":
            if schedule_at is None:
                return {"supported": False, "reason": "schedule_required"}
        elif not showcase_background_api_eligible(showcase_scenario):
            return {"supported": False, "reason": "showcase_cli_required"}
    return {"supported": True, "reason": "supported"}


def _showcase_input_projection(
    package: WorkflowPackage,
    scenario: "ShowcaseScenario",
) -> tuple[list[CatalogInput], SupportedInputs]:
    """Project only digest-authenticated public fields and bundled fixtures."""
    inputs, support = _input_projection(package)
    bindings = dict(scenario.input_value_bindings)
    fixtures = frozenset(scenario.input_fixtures)
    target_to_public = {target: public for public, target in bindings.items()}
    projected: list[CatalogInput] = []
    unsupported = support["reason"] == "unsupported_input_shape"
    for item in inputs:
        rendered = dict(item)
        internal_name = rendered["name"]
        rendered["name"] = target_to_public.get(internal_name, internal_name)
        if (
            rendered["type"] not in _SUPPORTED_INPUT_TYPES
            and not (internal_name in fixtures and rendered["type"] == "file")
        ):
            unsupported = True
        projected.append(rendered)
    if unsupported:
        return projected, {
            "supported": False,
            "reason": (
                "unsupported_input_shape"
                if support["reason"] == "unsupported_input_shape"
                else "unsupported_input_type"
            ),
        }
    return sorted(projected, key=lambda item: item["name"]), {
        "supported": True,
        "reason": "flat_inputs" if projected else "parameterless",
    }


def _compatibility_projection(compatibility) -> dict[str, object]:
    return {
        "level": compatibility.level.value,
        "runnable": compatibility.runnable,
        "findings_truncated": compatibility.findings_truncated,
        "finding_count": compatibility.finding_count,
        "findings": [
            {
                "path": finding.path,
                "level": finding.level.value,
                "message": finding.message,
                "blocking": finding.blocking,
                "code": finding.code,
            }
            for finding in compatibility.findings
        ],
    }


def _compatibility_summary(compatibility) -> dict[str, object]:
    return {
        "level": compatibility.level.value,
        "runnable": compatibility.runnable,
    }


def _structured_output_capability_summary(
    package: WorkflowPackage,
    execution_context: ExecutionCapabilityContext,
) -> dict[str, object] | None:
    decisions = execution_context.structured_output_decisions(package)
    if not decisions:
        return None

    unique = sorted(
        {
            (
                decision.strategy.value,
                decision.effective_provider,
                decision.api_mode,
                decision.adapter_version,
            )
            for decision in decisions.values()
        }
    )

    def bounded_text(value: str) -> str:
        cleaned, _truncated = sanitize_text(value)
        if len(cleaned) <= _STRUCTURED_OUTPUT_SUMMARY_TEXT_MAX_CHARS:
            return cleaned
        suffix = "…#" + hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:12]
        prefix_length = _STRUCTURED_OUTPUT_SUMMARY_TEXT_MAX_CHARS - len(suffix)
        return cleaned[:prefix_length] + suffix

    return {
        "mixed": len(unique) > 1,
        "summary_count": len(unique),
        "summaries_truncated": len(unique) > _STRUCTURED_OUTPUT_SUMMARY_LIMIT,
        "summaries": [
            {
                "strategy": bounded_text(strategy),
                "provider": bounded_text(provider),
                "api_mode": bounded_text(api_mode),
                "adapter_version": adapter_version,
            }
            for strategy, provider, api_mode, adapter_version in unique[
                :_STRUCTURED_OUTPUT_SUMMARY_LIMIT
            ]
        ],
    }


def _catalog_language_projection(
    package: WorkflowPackage, *, detail: bool = False
) -> dict[str, object]:
    language = package.language
    status: dict[str, object] = {
        "effective_profile": language.effective_profile.value,
        "legacy": language.effective_profile.value == "hermes-legacy",
    }
    if detail:
        expanded = language_projection(language)
        expanded["legacy"] = status["legacy"]
        return expanded
    return status


def qualify_workflow_catalog_package(
    package: WorkflowPackage,
    *,
    compatibility,
    compilation: WorkflowCompilation | None = None,
) -> dict[str, object]:
    """Apply the shared bounded show/detail/admission projection contract."""
    try:
        return show_package(
            package,
            compatibility_report=compatibility,
            include_argument_hints=False,
            compilation=compilation,
        )
    except WorkflowDefinitionProjectionCapacityError as exc:
        raise WorkflowCatalogCapacityError(
            "workflow catalog definition projection limit exceeded"
        ) from exc


def _enum_choices_supported(specification: Mapping[object, object]) -> bool:
    choice_fields = [field for field in _ENUM_INPUT_FIELDS if field in specification]
    if len(choice_fields) != 1:
        return False
    choices = specification[choice_fields[0]]
    if (
        not isinstance(choices, (list, tuple))
        or not choices
        or len(choices) > _ENUM_MAX_CHOICES
    ):
        return False
    projected: set[str] = set()
    for choice in choices:
        if not isinstance(choice, str):
            return False
        label = choice
        if not label or len(label) > _ENUM_MAX_CHOICE_LENGTH or label in projected:
            return False
        projected.add(label)
    return True


def desktop_input_name_is_representable(name: object) -> bool:
    """Match Desktop values to storage paths and the redacted detail projection."""
    if not workflow_input_name_is_portable(name):
        return False
    cleaned, truncated = sanitize_text(name, max_chars=128)
    projected = sanitize_projection({name: None})
    return (
        not truncated
        and cleaned == name
        and isinstance(projected, Mapping)
        and name in projected
        and projected[name] is None
    )


def _catalog_entry(
    package: WorkflowPackage,
    trust_store: WorkflowTrustStore | None,
    trust_snapshot: Mapping[str, object] | None,
    resource_budget: WorkflowResourceReadBudget,
    *,
    compilation: WorkflowCompilation | None = None,
    verified_showcase: "VerifiedShowcasePackage | None" = None,
    execution_context: ExecutionCapabilityContext,
) -> CatalogEntry:
    # The CLI show projection is the established body-free catalog contract.
    if compilation is None:
        compatibility, risk = assess_package_execution(
            package,
            execution_context,
            read_budget=resource_budget,
        )
    else:
        compatibility, risk = assess_package_execution(
            package,
            execution_context,
            read_budget=resource_budget,
            compilation=compilation,
        )
    if (
        verified_showcase is not None
        and risk.package_digest != verified_showcase.package_digest
    ):
        raise WorkflowCatalogInvalidDefinitionError(
            "verified showcase projection does not match authenticated package"
        )
    shown = qualify_workflow_catalog_package(
        package,
        compatibility=compatibility,
        compilation=compilation,
    )
    if verified_showcase is None:
        assert trust_store is not None and trust_snapshot is not None
        trust_state: CatalogTrustState = trust_store.check_snapshot(
            trust_snapshot,
            risk.package_digest,
            risk_digest=risk.risk_digest,
        )
        version = "1"
        showcase_scenario = None
    else:
        trust_state = "verified_bundled"
        version = str(verified_showcase.scenario.package_version)
        showcase_scenario = verified_showcase.scenario
    if showcase_scenario is None:
        inputs, supported_inputs = _input_projection(package)
    else:
        inputs, supported_inputs = _showcase_input_projection(
            package, showcase_scenario
        )
    entry: CatalogEntry = {
        "name": (
            str(verified_showcase.scenario.id)
            if verified_showcase is not None
            else str(shown["name"])
        ),
        "version": version,
        "description": str(shown["definition"]["description"]),
        "requires_ai": bool(
            showcase_scenario is not None and showcase_scenario.requires_ai
        ),
        "source": str(shown["source"]),
        "precedence": int(shown["precedence"]),
        "trust_state": trust_state,
        "inputs": inputs,
        "supported_inputs": supported_inputs,
        "run_support": workflow_catalog_run_support(
            package,
            showcase_scenario=showcase_scenario,
            input_support=supported_inputs,
        ),
        "language": _catalog_language_projection(package),
    }
    if verified_showcase is not None:
        entry["compatibility"] = _compatibility_projection(compatibility)
    else:
        entry["compatibility"] = _compatibility_summary(compatibility)
    structured_output_capability = _structured_output_capability_summary(
        package, execution_context
    )
    if structured_output_capability is not None:
        entry["structured_output_capability"] = structured_output_capability
    return entry


def build_workflow_catalog(
    *,
    hermes_home: str | Path,
    workdir: str | Path,
    runner_binding: WorkflowRunnerBinding | None = None,
) -> tuple[list[CatalogItem], bool]:
    """Return at most 500 stable entries without executing workflow code."""
    home = Path(hermes_home).expanduser().resolve()
    binding = runner_binding or production_workflow_runner_binding()
    discovered, truncated = _discover_catalog_compilations(
        Path(workdir).expanduser().resolve(), home
    )
    showcase_budget = WorkflowResourceReadBudget(
        max_file_bytes=CATALOG_MAX_RESOURCE_FILE_BYTES,
        max_total_bytes=CATALOG_MAX_RESOURCE_TOTAL_BYTES,
        max_files=CATALOG_MAX_RESOURCE_FILES,
    )
    verified_showcases = {}
    try:
        from plugins.workflow.showcase import load_verified_showcase_packages

        verified_showcases = load_verified_showcase_packages(
            read_budget=showcase_budget,
        )
    except WorkflowResourceCapacityError:
        truncated = True
    except FileNotFoundError as exc:
        logger.info(
            "workflow showcase catalog verification unavailable: %s",
            type(exc).__name__,
        )
        # A stripped distribution is trusted for nothing, but it must not
        # suppress independently discovered user workflows.
        verified_showcases = {}
    except (OSError, UnicodeError, ValueError) as exc:
        logger.warning(
            "workflow showcase catalog verification failed: %s",
            type(exc).__name__,
        )
        # A stripped or integrity-failed distribution is trusted for nothing,
        # but it must not suppress independently discovered user workflows.
        verified_showcases = {}
    trust_store = WorkflowTrustStore(home)
    try:
        trust_snapshot = trust_store.snapshot_read_only(
            max_bytes=CATALOG_MAX_TRUST_STORE_BYTES
        )
    except WorkflowTrustError as exc:
        raise WorkflowCatalogTrustUnavailableError(
            "workflow catalog trust classification is unavailable"
        ) from exc
    showcase_items: list[CatalogEntry] = []
    try:
        for verified in verified_showcases.values():
            showcase_compilation = (
                verified.compilation
                if supports_phase4_semantics(
                    verified.package.language.effective_profile,
                    verified.package.language.normalizer_version,
                )
                else None
            )
            showcase_items.append(
                _catalog_entry(
                    verified.package,
                    None,
                    None,
                    showcase_budget,
                    compilation=showcase_compilation,
                    verified_showcase=verified,
                    execution_context=background_execution_context(
                        binding,
                        requires_ai=verified.scenario.requires_ai,
                    ),
                )
            )
    except (
        WorkflowCatalogCapacityError,
        WorkflowCatalogInvalidDefinitionError,
        WorkflowResourceCapacityError,
        WorkflowValidationError,
        OSError,
    ) as exc:
        logger.warning(
            "workflow showcase catalog projection verification failed: %s",
            type(exc).__name__,
        )
        showcase_items = []

    user_limit = max(0, CATALOG_LIMIT - len(showcase_items))
    if len(discovered) > user_limit:
        truncated = True
    items: list[CatalogItem] = []
    resource_bytes_read = showcase_budget.bytes_read
    for discovered_item in discovered[:user_limit]:
        if (
            resource_bytes_read + CATALOG_MAX_RESOURCE_TOTAL_BYTES
            > CATALOG_MAX_RESOURCE_REQUEST_BYTES
            and items
        ):
            truncated = True
            break
        if not isinstance(discovered_item, WorkflowCompilation):
            items.append(discovered_item)
            continue
        package = discovered_item.package
        compilation = (
            discovered_item
            if supports_phase4_semantics(
                package.language.effective_profile,
                package.language.normalizer_version,
            )
            else None
        )
        resource_budget = WorkflowResourceReadBudget(
            max_file_bytes=CATALOG_MAX_RESOURCE_FILE_BYTES,
            max_total_bytes=CATALOG_MAX_RESOURCE_TOTAL_BYTES,
            max_files=CATALOG_MAX_RESOURCE_FILES,
        )
        try:
            items.append(
                _catalog_entry(
                    package,
                    trust_store,
                    trust_snapshot,
                    resource_budget,
                    compilation=compilation,
                    execution_context=background_execution_context(
                        binding,
                        requires_ai=None,
                    ),
                )
            )
        except (WorkflowCatalogCapacityError, WorkflowResourceCapacityError):
            items.append(
                _error_entry(package.definition.name, "catalog_capacity")
            )
        except WorkflowTrustError as exc:
            raise WorkflowCatalogTrustUnavailableError(
                "workflow catalog trust classification is unavailable"
            ) from exc
        except (OSError, UnicodeError, WorkflowValidationError, ValueError):
            items.append(
                _error_entry(package.definition.name, "invalid_definition")
            )
        finally:
            resource_bytes_read += resource_budget.bytes_read
    items.extend(showcase_items)
    items.sort(
        key=lambda item: (
            item["name"],
            item.get("precedence", 0),
            item.get("source", ""),
        )
    )
    return items, truncated


def _coordinator_projection(hermes_home: Path) -> dict[str, object]:
    database = hermes_home / "workflows" / "admission.sqlite3"
    if not database.is_file():
        return {
            "healthy": False,
            "status": "unavailable",
            "reason": "coordinator_missing",
        }
    try:
        from plugins.workflow.coordinator_store import CoordinatorStore

        health = CoordinatorStore(database).health_read_only(
            now=datetime.now(timezone.utc)
        )
    except (
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
        IndexError,
        KeyError,
        AssertionError,
        OverflowError,
    ):
        return {
            "healthy": False,
            "status": "unavailable",
            "reason": "coordinator_health_unavailable",
        }
    return {
        "healthy": health.status == "healthy",
        "status": health.status,
        "reason": health.reason_code,
    }


def build_workflow_detail(
    name: str,
    *,
    hermes_home: str | Path,
    workdir: str | Path,
    catalog_source: CatalogSource | None = None,
    runner_binding: WorkflowRunnerBinding | None = None,
) -> dict[str, object]:
    """Return one bounded, redacted, read-only workflow preflight projection."""
    if not isinstance(name, str) or not name.strip() or len(name) > 128:
        raise WorkflowDetailNotFoundError(name)
    if catalog_source not in {None, "project", "profile", "showcase"}:
        raise WorkflowDetailNotFoundError(name)
    home = Path(hermes_home).expanduser().resolve()
    binding = runner_binding or production_workflow_runner_binding()
    resource_budget = WorkflowResourceReadBudget(
        max_file_bytes=CATALOG_MAX_RESOURCE_FILE_BYTES,
        max_total_bytes=CATALOG_MAX_RESOURCE_TOTAL_BYTES,
        max_files=CATALOG_MAX_RESOURCE_FILES,
    )
    verified_showcase = None
    compilation: WorkflowCompilation | None = None
    if catalog_source == "showcase":
        try:
            from plugins.workflow.showcase import load_verified_showcase_packages

            verified_showcase = load_verified_showcase_packages(
                read_budget=resource_budget,
            ).get(name)
        except WorkflowResourceCapacityError as exc:
            raise WorkflowCatalogCapacityError(
                "workflow showcase detail verification limit exceeded"
            ) from exc
        except (OSError, UnicodeError, WorkflowValidationError, ValueError) as exc:
            raise WorkflowShowcaseVerificationError(
                "workflow showcase distribution verification failed"
            ) from exc
        if verified_showcase is None:
            raise WorkflowDetailNotFoundError(name)
        package = verified_showcase.package
    else:
        user_source = catalog_source if catalog_source in {"project", "profile"} else None
        discovered, _truncated = _discover_catalog_compilations(
            Path(workdir).expanduser().resolve(), home
        )
        selected_compilation = next(
            (
                item
                for item in discovered
                if isinstance(item, WorkflowCompilation)
                and item.package.definition.name == name
                and (
                    user_source is None
                    or item.package.source == user_source
                )
            ),
            None,
        )
        if selected_compilation is None:
            if any(
                isinstance(item, dict)
                and item.get("name") == name
                and item.get("error") == "catalog_capacity"
                for item in discovered
            ):
                raise WorkflowCatalogCapacityError(
                    "workflow detail definition limit exceeded"
                )
            raise WorkflowDetailNotFoundError(name)
        package = selected_compilation.package
        if supports_phase4_semantics(
            package.language.effective_profile,
            package.language.normalizer_version,
        ):
            compilation = selected_compilation

    execution_context = background_execution_context(
        binding,
        requires_ai=(
            verified_showcase.scenario.requires_ai
            if verified_showcase is not None
            else None
        ),
    )
    try:
        compatibility, risk = assess_package_execution(
            package,
            execution_context,
            read_budget=resource_budget,
            compilation=compilation,
        )
    except WorkflowResourceCapacityError as exc:
        raise WorkflowCatalogCapacityError(
            "workflow detail resource limit exceeded"
        ) from exc
    except WorkflowValidationError as exc:
        raise WorkflowCatalogInvalidDefinitionError(
            "workflow detail contains an invalid package resource"
        ) from exc
    shown = qualify_workflow_catalog_package(
        package,
        compatibility=compatibility,
        compilation=compilation,
    )
    if verified_showcase is None:
        try:
            trust_store = WorkflowTrustStore(home)
            trust_snapshot = trust_store.snapshot_read_only(
                max_bytes=CATALOG_MAX_TRUST_STORE_BYTES
            )
            trust_state: CatalogTrustState = trust_store.check_snapshot(
                trust_snapshot,
                risk.package_digest,
                risk_digest=risk.risk_digest,
            )
        except WorkflowTrustError as exc:
            raise WorkflowCatalogTrustUnavailableError(
                "workflow trust classification is unavailable"
            ) from exc
        version = "1"
        showcase_scenario = None
    else:
        trust_state = "verified_bundled"
        version = str(verified_showcase.scenario.package_version)
        showcase_scenario = verified_showcase.scenario
    if showcase_scenario is None:
        inputs, supported_inputs = _input_projection(package)
    else:
        inputs, supported_inputs = _showcase_input_projection(
            package, showcase_scenario
        )
    warnings = [str(item) for item in shown["topology_warnings"]]
    mermaid = shown["topology_mermaid"]
    omitted = None
    if mermaid is None:
        omitted = next(
            (
                warning
                for warning in warnings
                if warning.startswith("topology_mermaid_")
            ),
            "topology_mermaid_omitted",
        )
    detail = {
        "name": (
            str(verified_showcase.scenario.id)
            if verified_showcase is not None
            else str(shown["name"])
        ),
        "version": version,
        "description": str(shown["definition"]["description"]),
        "requires_ai": bool(
            showcase_scenario is not None and showcase_scenario.requires_ai
        ),
        "source": str(shown["source"]),
        "precedence": int(shown["precedence"]),
        "trust_state": trust_state,
        "inputs": inputs,
        "supported_inputs": supported_inputs,
        "run_support": workflow_catalog_run_support(
            package,
            showcase_scenario=showcase_scenario,
            input_support=supported_inputs,
        ),
        "language": _catalog_language_projection(package, detail=True),
        "risk_summary": risk.to_dict(),
        "compatibility": _compatibility_projection(compatibility),
        "coordinator": _coordinator_projection(home),
        "topology": {
            "text": shown["topology_text"],
            "mermaid": mermaid,
            "warnings": warnings,
            "omitted": omitted,
        },
        "definition": shown["definition"],
    }
    if "compilation" in shown:
        detail["compilation"] = shown["compilation"]
    return detail


__all__ = [
    "CatalogRunSupport",
    "CatalogSource",
    "CatalogTrustState",
    "CATALOG_LIMIT",
    "CATALOG_MAX_SCAN_ENTRIES",
    "CATALOG_MAX_DEFINITION_FILE_BYTES",
    "CATALOG_MAX_DEFINITION_TOTAL_BYTES",
    "CATALOG_MAX_RESOURCE_FILE_BYTES",
    "CATALOG_MAX_RESOURCE_FILES",
    "CATALOG_MAX_RESOURCE_REQUEST_BYTES",
    "CATALOG_MAX_RESOURCE_TOTAL_BYTES",
    "CATALOG_MAX_TRUST_STORE_BYTES",
    "WorkflowCatalogCapacityError",
    "WorkflowCatalogInvalidDefinitionError",
    "WorkflowCatalogTrustUnavailableError",
    "WorkflowCatalogUnavailableError",
    "WorkflowDetailNotFoundError",
    "WorkflowShowcaseVerificationError",
    "build_workflow_catalog",
    "build_workflow_detail",
    "capture_workflow_catalog_snapshot",
    "desktop_input_name_is_representable",
    "qualify_workflow_catalog_package",
    "resolve_workflow_catalog_compilation",
    "resolve_workflow_catalog_package",
    "workflow_catalog_run_support",
]
