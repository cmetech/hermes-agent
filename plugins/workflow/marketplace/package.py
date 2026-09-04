"""Safe exact-byte loading for workflow package distributions."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable, Iterable, NoReturn
import unicodedata

from pydantic import ValidationError

from plugins.workflow.trust import (
    WorkflowResourceCacheMissError,
    WorkflowResourceCapacityError,
    WorkflowResourceReadBudget,
)

from .contract import load_package_contract
from .models import (
    WorkflowPackageContract,
    WorkflowPackageDigests,
    WorkflowPackageIndex,
    WorkflowPackageManifest,
)


_MANIFEST_NAME = "workflow-package.json"
_DIGESTS_NAME = "digests.json"
_INDEX_PATH = (".well-known", "hermes-workflows", "index.json")
_WINDOWS_DRIVE_COMPONENT = re.compile(r"^[A-Za-z]:")
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(
    stat,
    "FILE_ATTRIBUTE_REPARSE_POINT",
    0x0400,
)
_HAS_DESCRIPTOR_WALK = (
    hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.scandir in os.supports_fd
)


class WorkflowMarketplaceError(RuntimeError):
    """Stable failure returned at a workflow marketplace trust boundary."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class PackageFile:
    """One safely read package file and its exact-byte identity."""

    relative_path: str
    path: Path
    content: bytes
    size: int
    sha256: str
    included: bool


@dataclass(frozen=True, slots=True)
class WorkflowDistribution:
    """A fully verified package distribution."""

    root: Path
    manifest: WorkflowPackageManifest
    publisher_digests: WorkflowPackageDigests
    files: tuple[PackageFile, ...]
    digest: str

    @property
    def covered_paths(self) -> tuple[str, ...]:
        return tuple(item.relative_path for item in self.files if item.included)


@dataclass(slots=True)
class _ScanLimits:
    contract: WorkflowPackageContract
    read_budget: WorkflowResourceReadBudget | None
    traversal_entries: int = 0
    included_files: int = 0
    included_bytes: int = 0

    def inspect_entry(self) -> None:
        self.traversal_entries += 1
        if self.traversal_entries > self.contract.resource_rules.max_traversal_entries:
            _fail(
                "package_traversal_limit",
                "package exceeds the filesystem-entry traversal limit",
            )

    def inspect_file(
        self,
        relative_path: str,
        metadata: os.stat_result,
        *,
        included: bool,
    ) -> None:
        rules = self.contract.resource_rules
        if not included:
            return
        self.included_files += 1
        if self.included_files > rules.max_files:
            _fail(
                "package_file_count_limit",
                f"package exceeds the {rules.max_files}-file contract limit",
            )
        if metadata.st_size > rules.max_file_bytes:
            _fail(
                "package_file_size_limit",
                f"package file {relative_path!r} exceeds the per-file contract limit",
            )
        self.included_bytes += metadata.st_size
        if self.included_bytes > rules.max_total_bytes:
            _fail(
                "package_total_size_limit",
                "package exceeds the aggregate byte contract limit",
            )

    def authenticate(self, path: Path, content: bytes, *, included: bool) -> None:
        if included and self.read_budget is not None:
            _charge_read_budget(self.read_budget, path, content)


@lru_cache(maxsize=1)
def _contract() -> WorkflowPackageContract:
    return load_package_contract()


def _fail(code: str, message: str) -> NoReturn:
    raise WorkflowMarketplaceError(code, message)


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _is_reparse_point(metadata: os.stat_result) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _open_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= getattr(os, name, 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    else:
        flags |= getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_BINARY", 0)
    return flags


def _read_descriptor(descriptor: int, expected_size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = expected_size + 1
    while remaining:
        chunk = os.read(descriptor, min(remaining, 64 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _root_metadata(root: Path, *, missing_code: str) -> os.stat_result:
    metadata = _lstat(
        root,
        code=missing_code,
        message="package or repository root is unavailable",
    )
    if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(metadata):
        _fail("package_symlink_unsupported", "package roots must not be links")
    if not stat.S_ISDIR(metadata.st_mode):
        _fail(missing_code, "package or repository root must be a directory")
    return metadata


def _lstat(path: Path, *, code: str, message: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise WorkflowMarketplaceError(code, message) from exc


def _canonical_path_error(relative_path: str) -> tuple[str, str] | None:
    if "\x00" in relative_path:
        return "package_path_nul", "package paths must not contain NUL bytes"
    parts = relative_path.split("/")
    if relative_path.startswith("/") or any(
        _WINDOWS_DRIVE_COMPONENT.match(part) for part in parts
    ):
        return "package_path_absolute", "package paths must be relative"
    if "\\" in relative_path:
        return "package_path_backslash", "package paths must use forward slashes"
    if not relative_path or any(part in {"", ".", ".."} for part in parts):
        return "package_path_traversal", "package paths must not contain dot segments"
    try:
        relative_path.encode("utf-8")
    except UnicodeEncodeError:
        return "package_path_collision", "package paths must be canonical UTF-8"
    if unicodedata.normalize("NFC", relative_path) != relative_path:
        return "package_path_collision", "package paths must use NFC normalization"
    if any(part.casefold() == ".git" for part in parts):
        return (
            "package_repository_metadata",
            "package paths must not contain repository metadata",
        )
    return None


def _validate_relative_path(relative_path: str) -> str:
    error = _canonical_path_error(relative_path)
    if error is not None:
        _fail(*error)
    return relative_path


def _register_path(
    relative_path: str,
    identities: dict[str, str],
) -> None:
    _validate_relative_path(relative_path)
    canonical = unicodedata.normalize("NFC", relative_path).casefold()
    previous = identities.get(canonical)
    if previous is not None and previous != relative_path:
        _fail(
            "package_path_collision",
            f"package paths {previous!r} and {relative_path!r} collide",
        )
    identities[canonical] = relative_path


def _check_nested_manifest(relative_path: str) -> None:
    parts = relative_path.split("/")
    if len(parts) > 1 and parts[-1].casefold() == _MANIFEST_NAME:
        _fail("package_root_nested", "package roots must not be nested")


def _ensure_safe_node(metadata: os.stat_result, relative_path: str) -> None:
    if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(metadata):
        _fail(
            "package_symlink_unsupported",
            f"package path {relative_path!r} is a link or reparse point",
        )
    if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
        _fail(
            "package_symlink_unsupported",
            f"package path {relative_path!r} is not a regular file or directory",
        )


def _stat_at(
    directory_descriptor: int,
    name: str,
    *,
    code: str,
    message: str,
) -> os.stat_result:
    try:
        return os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise WorkflowMarketplaceError(code, message) from exc


def _fstat(
    descriptor: int,
    *,
    code: str,
    message: str,
) -> os.stat_result:
    try:
        return os.fstat(descriptor)
    except OSError as exc:
        raise WorkflowMarketplaceError(code, message) from exc


def _open_directory_at(
    parent_descriptor: int,
    name: str,
    before: os.stat_result,
    *,
    code: str,
    message: str,
) -> tuple[int, os.stat_result]:
    try:
        descriptor = os.open(
            name,
            _open_flags(directory=True),
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        raise WorkflowMarketplaceError(code, message) from exc
    try:
        opened = os.fstat(descriptor)
        path_after = _stat_at(
            parent_descriptor,
            name,
            code=code,
            message=message,
        )
    except (OSError, WorkflowMarketplaceError):
        os.close(descriptor)
        raise
    if (
        not stat.S_ISDIR(opened.st_mode)
        or _is_reparse_point(opened)
        or _identity(before) != _identity(opened)
        or _identity(opened) != _identity(path_after)
    ):
        os.close(descriptor)
        _fail(code, message)
    return descriptor, opened


def _recheck_directory_at(
    parent_descriptor: int,
    name: str,
    descriptor: int,
    opened: os.stat_result,
    *,
    code: str,
    message: str,
) -> None:
    try:
        descriptor_after = os.fstat(descriptor)
        path_after = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as exc:
        raise WorkflowMarketplaceError(code, message) from exc
    if _identity(opened) != _identity(descriptor_after) or _identity(
        opened
    ) != _identity(path_after):
        _fail(code, message)


def _included(relative_path: str, contract: WorkflowPackageContract) -> bool:
    return relative_path not in frozenset(contract.digest_rules.excluded_paths)


def _charge_read_budget(
    budget: WorkflowResourceReadBudget,
    path: Path,
    content: bytes,
) -> None:
    try:
        if budget.has_cached(path):
            if budget.read_cached(path) != content:
                _fail(
                    "package_digest_mismatch",
                    f"authenticated bytes changed for {path.name!r}",
                )
            return
        if budget.files_read >= budget.max_files:
            _fail(
                "package_file_count_limit",
                "shared package resource file budget is exhausted",
            )
        if len(content) > budget.max_file_bytes:
            _fail(
                "package_file_size_limit",
                f"{path.name!r} exceeds the shared per-file byte budget",
            )
        if budget.bytes_read + len(content) > budget.max_total_bytes:
            _fail(
                "package_total_size_limit",
                "shared package resource byte budget is exhausted",
            )
        budget.remember_authenticated(path, content)
    except WorkflowMarketplaceError:
        raise
    except WorkflowResourceCapacityError as exc:
        _fail("package_total_size_limit", str(exc))
    except WorkflowResourceCacheMissError as exc:
        raise WorkflowMarketplaceError(
            "package_digest_mismatch",
            "sealed package bytes do not cover the distribution",
        ) from exc
    except OSError as exc:
        raise WorkflowMarketplaceError(
            "package_digest_mismatch",
            "authenticated package bytes changed",
        ) from exc


def _package_file(
    root: Path,
    relative_path: str,
    content: bytes,
    *,
    contract: WorkflowPackageContract,
    limits: _ScanLimits,
) -> PackageFile:
    included = _included(relative_path, contract)
    path = root.joinpath(*relative_path.split("/"))
    limits.authenticate(path, content, included=included)
    return PackageFile(
        relative_path=relative_path,
        path=path,
        content=content,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        included=included,
    )


def _read_opened_file(
    descriptor: int,
    before: os.stat_result,
    relative_path: str,
    *,
    current_stat: Callable[[], os.stat_result],
    unsafe_code: str,
) -> bytes:
    try:
        opened = os.fstat(descriptor)
        after_open = current_stat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse_point(opened)
            or _identity(before) != _identity(opened)
            or _identity(opened) != _identity(after_open)
        ):
            _fail(
                unsafe_code,
                f"package file {relative_path!r} changed during open",
            )
        content = _read_descriptor(descriptor, opened.st_size)
        descriptor_after = os.fstat(descriptor)
        path_after = current_stat()
        if (
            len(content) != opened.st_size
            or _identity(opened) != _identity(descriptor_after)
            or _identity(opened) != _identity(path_after)
        ):
            _fail(
                unsafe_code,
                f"package file {relative_path!r} changed during read",
            )
        return content
    except WorkflowMarketplaceError:
        raise
    except OSError as exc:
        raise WorkflowMarketplaceError(
            unsafe_code,
            f"package file {relative_path!r} changed during read",
        ) from exc


def _check_file_size(
    before: os.stat_result,
    relative_path: str,
    maximum_size: int,
    size_code: str | None,
) -> None:
    if before.st_size <= maximum_size:
        return
    code = size_code or (
        "package_digest_invalid"
        if relative_path == _DIGESTS_NAME
        else "package_file_size_limit"
    )
    _fail(code, f"package file {relative_path!r} exceeds its byte limit")


def _read_file_at(
    directory_descriptor: int,
    name: str,
    before: os.stat_result,
    relative_path: str,
    *,
    maximum_size: int,
    unsafe_code: str = "package_digest_mismatch",
    size_code: str | None = None,
) -> bytes:
    _check_file_size(before, relative_path, maximum_size, size_code)
    try:
        descriptor = os.open(name, _open_flags(), dir_fd=directory_descriptor)
    except OSError as exc:
        raise WorkflowMarketplaceError(
            unsafe_code,
            f"package file {relative_path!r} could not be safely opened",
        ) from exc
    try:
        return _read_opened_file(
            descriptor,
            before,
            relative_path,
            current_stat=lambda: os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            ),
            unsafe_code=unsafe_code,
        )
    finally:
        os.close(descriptor)


@dataclass(slots=True)
class _DirectoryScan:
    descriptor: int
    entries: Any
    parts: tuple[str, ...]
    parent_descriptor: int | None = None
    name: str | None = None
    opened: os.stat_result | None = None


def _scan_directory(descriptor: int) -> Any:
    try:
        return os.scandir(descriptor)
    except OSError as exc:
        raise WorkflowMarketplaceError(
            "package_digest_mismatch",
            "package directory changed during scan",
        ) from exc


def _scan_descriptor_tree(
    root: Path,
    contract: WorkflowPackageContract,
    limits: _ScanLimits,
    *,
    anchored_descriptor: int | None = None,
) -> tuple[PackageFile, ...]:
    root_before = _root_metadata(root, missing_code="package_manifest_invalid")
    try:
        root_descriptor = (
            os.open(root, _open_flags(directory=True))
            if anchored_descriptor is None
            else os.dup(anchored_descriptor)
        )
    except OSError as exc:
        raise WorkflowMarketplaceError(
            "package_manifest_invalid",
            "package root could not be safely opened",
        ) from exc
    files: list[PackageFile] = []
    identities: dict[str, str] = {}
    scans: list[_DirectoryScan] = []
    try:
        root_opened = _fstat(
            root_descriptor,
            code="package_digest_mismatch",
            message="package root changed during open",
        )
        if (
            not stat.S_ISDIR(root_opened.st_mode)
            or _is_reparse_point(root_opened)
            or _identity(root_before) != _identity(root_opened)
        ):
            _fail("package_digest_mismatch", "package root changed during open")

        scans.append(
            _DirectoryScan(root_descriptor, _scan_directory(root_descriptor), ())
        )
        while scans:
            current = scans[-1]
            try:
                entry = next(current.entries)
            except StopIteration:
                current.entries.close()
                scans.pop()
                if current.parent_descriptor is not None:
                    assert current.name is not None
                    assert current.opened is not None
                    try:
                        _recheck_directory_at(
                            current.parent_descriptor,
                            current.name,
                            current.descriptor,
                            current.opened,
                            code="package_digest_mismatch",
                            message=(
                                "package directory changed during descriptor scan"
                            ),
                        )
                    finally:
                        os.close(current.descriptor)
                continue
            except OSError as exc:
                raise WorkflowMarketplaceError(
                    "package_digest_mismatch",
                    "package directory changed during scan",
                ) from exc
            limits.inspect_entry()
            name = entry.name
            relative_parts = (*current.parts, name)
            relative_path = "/".join(relative_parts)
            _register_path(relative_path, identities)
            _check_nested_manifest(relative_path)
            before = _stat_at(
                current.descriptor,
                name,
                code="package_digest_mismatch",
                message=f"package path {relative_path!r} changed during scan",
            )
            _ensure_safe_node(before, relative_path)
            if stat.S_ISDIR(before.st_mode):
                message = f"package directory {relative_path!r} changed during scan"
                child_descriptor, opened = _open_directory_at(
                    current.descriptor,
                    name,
                    before,
                    code="package_digest_mismatch",
                    message=message,
                )
                try:
                    child_entries = _scan_directory(child_descriptor)
                except WorkflowMarketplaceError:
                    os.close(child_descriptor)
                    raise
                scans.append(
                    _DirectoryScan(
                        child_descriptor,
                        child_entries,
                        relative_parts,
                        current.descriptor,
                        name,
                        opened,
                    )
                )
                continue
            included = _included(relative_path, contract)
            limits.inspect_file(relative_path, before, included=included)
            content = _read_file_at(
                current.descriptor,
                name,
                before,
                relative_path,
                maximum_size=contract.resource_rules.max_file_bytes,
            )
            files.append(
                _package_file(
                    root,
                    relative_path,
                    content,
                    contract=contract,
                    limits=limits,
                )
            )

        root_descriptor_after = _fstat(
            root_descriptor,
            code="package_digest_mismatch",
            message="package root changed during scan",
        )
        root_path_after = _lstat(
            root,
            code="package_digest_mismatch",
            message="package root changed during scan",
        )
        if _identity(root_opened) != _identity(root_descriptor_after) or _identity(
            root_opened
        ) != _identity(root_path_after):
            _fail("package_digest_mismatch", "package root changed during scan")
    finally:
        for current in reversed(scans):
            current.entries.close()
            if current.descriptor != root_descriptor:
                os.close(current.descriptor)
        os.close(root_descriptor)
    return tuple(sorted(files, key=lambda item: item.relative_path))


def _scan_package_files(
    root: Path,
    *,
    contract: WorkflowPackageContract,
    read_budget: WorkflowResourceReadBudget | None,
    anchored_descriptor: int | None = None,
) -> tuple[PackageFile, ...]:
    if read_budget is not None and not isinstance(
        read_budget, WorkflowResourceReadBudget
    ):
        raise TypeError("read_budget must be a WorkflowResourceReadBudget")
    absolute_root = _absolute(root)
    limits = _ScanLimits(contract=contract, read_budget=read_budget)
    if not _HAS_DESCRIPTOR_WALK:
        _fail(
            "package_digest_mismatch",
            "descriptor-safe package traversal is unavailable",
        )
    return _scan_descriptor_tree(
        absolute_root,
        contract,
        limits,
        anchored_descriptor=anchored_descriptor,
    )


def scan_package_files(
    root: Path,
    *,
    read_budget: WorkflowResourceReadBudget | None = None,
) -> tuple[PackageFile, ...]:
    """Read every package file exactly once without following links."""

    return _scan_package_files(root, contract=_contract(), read_budget=read_budget)


def compute_distribution_digest(files: Iterable[PackageFile]) -> str:
    """Compute the contract-defined composite over exact included bytes."""

    contract = _contract()
    try:
        domain = base64.b64decode(
            contract.digest_rules.domain_separator_base64,
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            "workflow package contract digest domain is invalid"
        ) from exc
    digest = hashlib.sha256(domain)
    for item in sorted(
        (
            candidate
            for candidate in files
            if candidate.relative_path
            not in frozenset(contract.digest_rules.excluded_paths)
        ),
        key=lambda candidate: candidate.relative_path,
    ):
        path_bytes = item.relative_path.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(item.content).to_bytes(8, "big"))
        digest.update(hashlib.sha256(item.content).digest())
    return digest.hexdigest()


class _DuplicateJsonKey(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON value: {value}")


def _parse_json(content: bytes, *, code: str, label: str) -> Any:
    try:
        return json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise WorkflowMarketplaceError(code, f"{label} is not canonical JSON") from exc


def _file_by_path(
    files: tuple[PackageFile, ...],
    relative_path: str,
    *,
    code: str,
) -> PackageFile:
    for item in files:
        if item.relative_path == relative_path:
            return item
    _fail(code, f"required package file {relative_path!r} is missing")


def _unsupported_version(
    value: Any,
    supported: list[int],
    *,
    field_present: bool,
) -> bool:
    return (
        field_present
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value not in supported
    )


def _validate_claimed_paths(value: Any, *, container: str) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        if not isinstance(item, dict):
            continue
        fields = ("definition", "companion") if container == "manifest" else ("path",)
        for field in fields:
            candidate = item.get(field)
            if isinstance(candidate, str):
                _validate_relative_path(candidate)


def _parse_manifest(
    files: tuple[PackageFile, ...],
    contract: WorkflowPackageContract,
) -> WorkflowPackageManifest:
    manifest_file = _file_by_path(
        files,
        _MANIFEST_NAME,
        code="package_manifest_invalid",
    )
    raw = _parse_json(
        manifest_file.content,
        code="package_manifest_invalid",
        label="workflow package manifest",
    )
    if isinstance(raw, dict):
        if _unsupported_version(
            raw.get("schemaVersion"),
            contract.compatibility_rules.supported_manifest_schema_versions,
            field_present="schemaVersion" in raw,
        ):
            _fail(
                "package_contract_unsupported",
                "workflow package manifest contract version is unsupported",
            )
        _validate_claimed_paths(raw.get("workflows"), container="manifest")
    try:
        return WorkflowPackageManifest.model_validate(raw)
    except ValidationError as exc:
        raise WorkflowMarketplaceError(
            "package_manifest_invalid",
            "workflow package manifest is structurally invalid",
        ) from exc


def _parse_digests(
    files: tuple[PackageFile, ...],
    contract: WorkflowPackageContract,
) -> WorkflowPackageDigests:
    digest_file = _file_by_path(
        files,
        _DIGESTS_NAME,
        code="package_digest_invalid",
    )
    raw = _parse_json(
        digest_file.content,
        code="package_digest_invalid",
        label="workflow package digests",
    )
    if isinstance(raw, dict):
        if _unsupported_version(
            raw.get("contractVersion"),
            contract.compatibility_rules.supported_contract_versions,
            field_present="contractVersion" in raw,
        ):
            _fail(
                "package_contract_unsupported",
                "workflow package digest contract version is unsupported",
            )
        _validate_claimed_paths(raw.get("files"), container="digests")
    try:
        return WorkflowPackageDigests.model_validate(raw)
    except ValidationError as exc:
        raise WorkflowMarketplaceError(
            "package_digest_invalid",
            "workflow package digest claims are structurally invalid",
        ) from exc


def _verify_membership_and_claims(
    manifest: WorkflowPackageManifest,
    published: WorkflowPackageDigests,
    files: tuple[PackageFile, ...],
    actual: str,
    expected_digest: str | None,
) -> None:
    included = {item.relative_path: item for item in files if item.included}
    for member in manifest.workflows:
        for relative_path in (member.definition, member.companion):
            if relative_path is not None and relative_path not in included:
                _fail(
                    "package_member_missing",
                    f"manifest member {relative_path!r} is missing",
                )
    claims = {record.path: record for record in published.files}
    if claims.keys() != included.keys():
        _fail(
            "package_digest_mismatch",
            "publisher digest paths do not exactly cover included package files",
        )
    for relative_path, item in included.items():
        claim = claims[relative_path]
        if claim.size != item.size or claim.sha256 != item.sha256:
            _fail(
                "package_digest_mismatch",
                f"publisher digest claim for {relative_path!r} does not match",
            )
    if published.package_digest != actual:
        _fail(
            "package_digest_mismatch",
            "publisher package digest does not match package bytes",
        )
    if expected_digest is not None and expected_digest != actual:
        _fail(
            "package_digest_mismatch",
            "repository index digest does not match package bytes",
        )


def _load_distribution(
    root: Path,
    *,
    expected_digest: str | None,
    read_budget: WorkflowResourceReadBudget | None,
    contract: WorkflowPackageContract,
    anchored_descriptor: int | None = None,
) -> WorkflowDistribution:
    absolute_root = _absolute(root)
    root_before = _root_metadata(
        absolute_root,
        missing_code="package_manifest_invalid",
    )
    files = _scan_package_files(
        absolute_root,
        contract=contract,
        read_budget=read_budget,
        anchored_descriptor=anchored_descriptor,
    )
    manifest = _parse_manifest(files, contract)
    published = _parse_digests(files, contract)
    actual = compute_distribution_digest(files)
    _verify_membership_and_claims(
        manifest,
        published,
        files,
        actual,
        expected_digest,
    )
    before_resolve = _lstat(
        absolute_root,
        code="package_digest_mismatch",
        message="package root changed after validation",
    )
    try:
        canonical_root = absolute_root.resolve(strict=True)
    except OSError as exc:
        raise WorkflowMarketplaceError(
            "package_digest_mismatch",
            "package root changed after validation",
        ) from exc
    after_resolve = _lstat(
        absolute_root,
        code="package_digest_mismatch",
        message="package root changed after validation",
    )
    if (
        stat.S_ISLNK(before_resolve.st_mode)
        or _is_reparse_point(before_resolve)
        or _identity(root_before) != _identity(before_resolve)
        or _identity(before_resolve) != _identity(after_resolve)
    ):
        _fail("package_digest_mismatch", "package root changed after validation")
    return WorkflowDistribution(
        root=canonical_root,
        manifest=manifest,
        publisher_digests=published,
        files=files,
        digest=actual,
    )


def load_distribution(
    root: Path,
    *,
    expected_digest: str | None = None,
    read_budget: WorkflowResourceReadBudget | None = None,
) -> WorkflowDistribution:
    """Load one complete package and verify every publisher claim."""

    return _load_distribution(
        root,
        expected_digest=expected_digest,
        read_budget=read_budget,
        contract=_contract(),
    )


def _open_root_descriptor(
    root: Path,
    *,
    code: str,
) -> tuple[int, os.stat_result]:
    root_before = _root_metadata(root, missing_code=code)
    try:
        root_descriptor = os.open(root, _open_flags(directory=True))
    except OSError as exc:
        raise WorkflowMarketplaceError(
            code, "repository root changed during open"
        ) from exc
    try:
        root_opened = _fstat(
            root_descriptor,
            code=code,
            message="repository root changed during open",
        )
    except WorkflowMarketplaceError:
        os.close(root_descriptor)
        raise
    if (
        not stat.S_ISDIR(root_opened.st_mode)
        or _is_reparse_point(root_opened)
        or _identity(root_before) != _identity(root_opened)
    ):
        os.close(root_descriptor)
        _fail(code, "repository root changed during open")
    return root_descriptor, root_opened


def _open_directory_chain(
    root_descriptor: int,
    components: tuple[str, ...],
    *,
    code: str,
) -> list[tuple[int, str, int, os.stat_result]]:
    chain: list[tuple[int, str, int, os.stat_result]] = []
    directory_descriptor = root_descriptor
    try:
        for component in components:
            before = _stat_at(
                directory_descriptor,
                component,
                code=code,
                message="repository directory path is unavailable",
            )
            if stat.S_ISLNK(before.st_mode) or _is_reparse_point(before):
                _fail(
                    "package_symlink_unsupported",
                    "repository package paths must not contain links",
                )
            if not stat.S_ISDIR(before.st_mode):
                _fail(code, "repository directory path is invalid")
            parent_descriptor = directory_descriptor
            directory_descriptor, opened = _open_directory_at(
                parent_descriptor,
                component,
                before,
                code=code,
                message="repository directory path changed during open",
            )
            chain.append((
                parent_descriptor,
                component,
                directory_descriptor,
                opened,
            ))
        return chain
    except BaseException:
        for _, _, descriptor, _ in reversed(chain):
            os.close(descriptor)
        raise


def _close_directory_chain(
    chain: list[tuple[int, str, int, os.stat_result]],
) -> None:
    for _, _, descriptor, _ in reversed(chain):
        os.close(descriptor)


def _recheck_directory_chain(
    chain: list[tuple[int, str, int, os.stat_result]],
    *,
    code: str,
) -> None:
    for parent, component, descriptor, opened in reversed(chain):
        _recheck_directory_at(
            parent,
            component,
            descriptor,
            opened,
            code=code,
            message="repository directory path changed during read",
        )


def _recheck_root(
    root: Path,
    root_descriptor: int,
    opened: os.stat_result,
    *,
    code: str,
) -> None:
    descriptor_after = _fstat(
        root_descriptor,
        code=code,
        message="repository root changed during read",
    )
    path_after = _lstat(
        root,
        code=code,
        message="repository root changed during read",
    )
    if _identity(opened) != _identity(descriptor_after) or _identity(
        opened
    ) != _identity(path_after):
        _fail(code, "repository root changed during read")


def _read_repository_index_at(
    root: Path,
    root_descriptor: int,
    root_opened: os.stat_result,
    contract: WorkflowPackageContract,
) -> bytes:
    chain = _open_directory_chain(
        root_descriptor,
        _INDEX_PATH[:-1],
        code="package_index_invalid",
    )
    directory_descriptor = chain[-1][2]
    try:
        filename = _INDEX_PATH[-1]
        index_before = _stat_at(
            directory_descriptor,
            filename,
            code="package_index_invalid",
            message="repository marketplace index is unavailable",
        )
        if stat.S_ISLNK(index_before.st_mode) or _is_reparse_point(index_before):
            _fail(
                "package_symlink_unsupported",
                "repository marketplace index must not be a link",
            )
        if not stat.S_ISREG(index_before.st_mode):
            _fail("package_index_invalid", "repository marketplace index is invalid")
        maximum = contract.resource_rules.max_index_bytes
        content = _read_file_at(
            directory_descriptor,
            filename,
            index_before,
            "/".join(_INDEX_PATH),
            maximum_size=maximum,
            unsafe_code="package_index_invalid",
            size_code="package_index_size_limit",
        )
        _recheck_directory_chain(chain, code="package_index_invalid")
        _recheck_root(
            root,
            root_descriptor,
            root_opened,
            code="package_index_invalid",
        )
        return content
    finally:
        _close_directory_chain(chain)


def _parse_index(
    content: bytes,
    contract: WorkflowPackageContract,
) -> WorkflowPackageIndex:
    raw = _parse_json(
        content,
        code="package_index_invalid",
        label="repository marketplace index",
    )
    if isinstance(raw, dict):
        if _unsupported_version(
            raw.get("schemaVersion"),
            contract.compatibility_rules.supported_index_schema_versions,
            field_present="schemaVersion" in raw,
        ):
            _fail(
                "package_contract_unsupported",
                "repository marketplace index contract version is unsupported",
            )
        packages = raw.get("packages")
        if isinstance(packages, list):
            if len(packages) > contract.resource_rules.max_catalog_entries:
                _fail(
                    "package_catalog_entry_limit",
                    "repository marketplace index has too many entries",
                )
            paths: list[str] = []
            for entry in packages:
                if not isinstance(entry, dict):
                    continue
                if _unsupported_version(
                    entry.get("contractVersion"),
                    contract.compatibility_rules.supported_contract_versions,
                    field_present="contractVersion" in entry,
                ):
                    _fail(
                        "package_contract_unsupported",
                        "indexed package contract version is unsupported",
                    )
                package_path = entry.get("packagePath")
                if isinstance(package_path, str):
                    paths.append(_validate_relative_path(package_path))
            identities = [
                unicodedata.normalize("NFC", path).casefold() for path in paths
            ]
            for index, parent in enumerate(identities):
                if any(
                    child.startswith(f"{parent}/") for child in identities[index + 1 :]
                ) or any(
                    parent.startswith(f"{child}/") for child in identities[index + 1 :]
                ):
                    _fail("package_root_nested", "package roots must not be nested")
    try:
        return WorkflowPackageIndex.model_validate(raw)
    except ValidationError as exc:
        raise WorkflowMarketplaceError(
            "package_index_invalid",
            "repository marketplace index is structurally invalid",
        ) from exc


def _verify_index_entry(
    root: Path,
    root_descriptor: int,
    entry: Any,
    contract: WorkflowPackageContract,
    roots: list[tuple[tuple[int, int], ...]],
) -> tuple[tuple[int, int], ...]:
    package_root = root.joinpath(*entry.package_path.split("/"))
    chain = _open_directory_chain(
        root_descriptor,
        tuple(entry.package_path.split("/")),
        code="package_index_invalid",
    )
    try:
        directory_identities = tuple(
            (opened.st_dev, opened.st_ino) for *_, opened in chain
        )
        _reject_resolved_root_overlap(roots, directory_identities)
        distribution = _load_distribution(
            package_root,
            expected_digest=entry.package_digest,
            read_budget=None,
            contract=contract,
            anchored_descriptor=chain[-1][2],
        )
        _recheck_directory_chain(chain, code="package_index_invalid")
        manifest = distribution.manifest
        projected = (
            manifest.id,
            manifest.version,
            manifest.display_name,
            manifest.description,
            manifest.license,
            manifest.publisher,
            manifest.tags,
            contract.contract_version,
        )
        claimed = (
            entry.id,
            entry.version,
            entry.display_name,
            entry.description,
            entry.license,
            entry.publisher,
            entry.tags,
            entry.contract_version,
        )
        if claimed != projected:
            _fail(
                "package_index_invalid",
                f"repository metadata for package {entry.id!r} is stale",
            )
        return directory_identities
    finally:
        _close_directory_chain(chain)


def _reject_resolved_root_overlap(
    roots: list[tuple[tuple[int, int], ...]],
    candidate: tuple[tuple[int, int], ...],
) -> None:
    for existing in roots:
        if candidate[-1] in existing or existing[-1] in candidate:
            _fail("package_root_nested", "resolved package roots must not overlap")


def load_repository_index(root: Path) -> WorkflowPackageIndex:
    """Load a bounded repository index and verify every package claim."""

    contract = _contract()
    absolute_root = _absolute(root)
    if not _HAS_DESCRIPTOR_WALK:
        _fail(
            "package_index_invalid",
            "descriptor-safe repository traversal is unavailable",
        )
    root_descriptor, root_opened = _open_root_descriptor(
        absolute_root,
        code="package_index_invalid",
    )
    try:
        content = _read_repository_index_at(
            absolute_root,
            root_descriptor,
            root_opened,
            contract,
        )
        index = _parse_index(content, contract)
        roots: list[tuple[tuple[int, int], ...]] = []
        for entry in index.packages:
            directory_identities = _verify_index_entry(
                absolute_root,
                root_descriptor,
                entry,
                contract,
                roots,
            )
            roots.append(directory_identities)
        _recheck_root(
            absolute_root,
            root_descriptor,
            root_opened,
            code="package_index_invalid",
        )
        return index
    finally:
        os.close(root_descriptor)


__all__ = [
    "PackageFile",
    "WorkflowDistribution",
    "WorkflowMarketplaceError",
    "compute_distribution_digest",
    "load_distribution",
    "load_repository_index",
    "scan_package_files",
]
