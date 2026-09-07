"""Two-phase, journaled workflow package filesystem transactions."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
from typing import Iterator, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hermes_constants import get_hermes_home
from plugins.workflow.locks import WorkflowLockTimeout, workflow_lock
from plugins.workflow.trust import WorkflowTrustError, WorkflowTrustStore
from utils import (
    _is_reparse_point,
    _open_no_follow_directory,
    _open_windows_directory_guard,
    _validate_no_follow_parent_path,
    atomic_write_text,
)

from .models import InstalledPackageIdentity, InstalledPackageProvenance
from .package import (
    WorkflowDistribution,
    WorkflowMarketplaceError,
    load_distribution,
)
from .provenance import InstalledPackageStore
from .source_store import (
    WorkflowSourceStore,
    _path_entry_exists,
    _read_bounded,
    _strict_json,
)


_STATE_VERSION = 1
_MAX_PREPARED = 256
_MAX_JOURNALS = 64
_MAX_STATE_BYTES = 8 * 1024 * 1024
_MAX_MARKER_BYTES = 32 * 1024
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,256}$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_TRANSACTION_ID = re.compile(r"^[0-9a-f]{32}$", re.ASCII)
_TRUST_ORIGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}", re.ASCII)
_OWNER = "hermes-workflow-marketplace"
_CONFIRMATION_DOMAIN = b"hermes.workflow-marketplace.confirmation.v1\0"
_CONSUMED_LEASE_SECONDS = 300
_DIRECTORY_FD_SUPPORTED = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and os.open in os.supports_dir_fd
    and os.mkdir in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and os.rename in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and shutil.rmtree.avoids_symlink_attacks
    and hasattr(os, "fchmod")
)

JournalPhase = Literal[
    "install_consumed",
    "install_backup_move_pending",
    "install_candidate_swap_pending",
    "install_provenance_write_pending",
    "install_trust_revoke_pending",
    "install_retire_pending",
    "install_rollback_pending",
    "remove_consumed",
    "remove_backup_move_pending",
    "remove_provenance_write_pending",
    "remove_trust_revoke_pending",
    "remove_retire_pending",
    "remove_rollback_pending",
]


@dataclass(frozen=True, slots=True)
class TransactionCandidate:
    """A fully verified fetched package plus its exact source destination."""

    distribution: WorkflowDistribution
    identity: InstalledPackageIdentity
    source_name: str
    repository_url: str
    configured_ref: str | None
    resolved_commit: str
    package_path: str
    destination: Path
    operation: Literal["install", "remove"] = "install"
    installed_provenance: InstalledPackageProvenance | None = None


@dataclass(frozen=True, slots=True)
class PreparedTransaction:
    """One prepared confirmation, with the raw token returned only to its caller."""

    transaction_id: str
    token: str
    consumed: bool
    operation: Literal["install", "remove"]
    identity: InstalledPackageIdentity
    source_name: str
    repository_url: str
    configured_ref: str | None
    resolved_commit: str
    package_path: str
    package_version: str
    contract_version: int
    distribution_digest: str
    workflow_paths: tuple[str, ...]
    destination: Path
    staging_path: Path
    review_digest: str
    confirmation_digest: str
    actor: str
    profile: str
    created_at: str
    expires_at: str
    consumed_at: str | None
    consumed_expires_at: str | None
    installed_provenance: InstalledPackageProvenance | None


@dataclass(frozen=True, slots=True)
class PreparedTransactionMetadata:
    """Non-secret logical identity for a live actor/profile-bound token."""

    operation: Literal["install", "update", "remove"]
    identity: InstalledPackageIdentity
    review_digest: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class TransactionJournal:
    transaction_id: str
    operation: Literal["install", "remove"]
    phase: JournalPhase
    identity: InstalledPackageIdentity
    destination: Path
    staging_path: Path
    quarantine_path: Path
    consumed_at: str
    consumed_expires_at: str
    candidate_provenance: InstalledPackageProvenance | None
    previous_provenance: InstalledPackageProvenance | None


@dataclass(frozen=True, slots=True)
class _RecoveryEntry:
    """Finite confirmation scope, without filesystem targets or review secrets."""

    transaction_id: str
    identity: InstalledPackageIdentity
    kind: Literal["journal", "expired_preparation", "abandoned_staging"]
    classification: Literal[
        "owned", "ownership_mismatch", "active_writer", "active_preparation"
    ]
    phase: JournalPhase | None = None


@dataclass(frozen=True, slots=True)
class _RecoveryInspection:
    entries: tuple[_RecoveryEntry, ...]
    active_writer: bool
    has_live_preparations: bool
    complete: bool = True


class _StateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _PreparedRecord(_StateModel):
    transaction_id: str = Field(alias="transactionId", pattern=_TRANSACTION_ID.pattern)
    token_digest: str = Field(alias="tokenDigest", pattern=_SHA256.pattern)
    operation: Literal["install", "remove"]
    identity: InstalledPackageIdentity
    source_name: str = Field(alias="sourceName", min_length=1, max_length=64)
    repository_url: str = Field(alias="repositoryUrl", min_length=1, max_length=4096)
    configured_ref: str | None = Field(
        default=None, alias="configuredRef", max_length=1024
    )
    resolved_commit: str = Field(alias="resolvedCommit", pattern=r"^[0-9a-f]{40}$")
    package_path: str = Field(alias="packagePath", min_length=1, max_length=1024)
    package_version: str = Field(alias="packageVersion", min_length=1, max_length=128)
    contract_version: int = Field(alias="contractVersion")
    distribution_digest: str = Field(
        alias="distributionDigest", pattern=_SHA256.pattern
    )
    workflow_paths: list[str] = Field(
        alias="workflowPaths", min_length=1, max_length=512
    )
    destination: str = Field(min_length=1, max_length=4096)
    staging_path: str = Field(alias="stagingPath", min_length=1, max_length=4096)
    review_digest: str = Field(alias="reviewDigest", pattern=_SHA256.pattern)
    confirmation_digest: str = Field(
        alias="confirmationDigest", pattern=_SHA256.pattern
    )
    actor: str = Field(min_length=1, max_length=256)
    profile: str = Field(min_length=1, max_length=256)
    created_at: str = Field(alias="createdAt", min_length=20, max_length=64)
    expires_at: str = Field(alias="expiresAt", min_length=20, max_length=64)
    installed_provenance: InstalledPackageProvenance | None = Field(
        default=None, alias="installedProvenance"
    )

    @model_validator(mode="after")
    def require_operation_shape(self) -> "_PreparedRecord":
        if self.operation == "remove" and self.installed_provenance is None:
            raise ValueError("prepared transaction operation is inconsistent")
        if (
            self.installed_provenance is not None
            and self.installed_provenance.identity != self.identity
        ):
            raise ValueError("prepared installed identity is inconsistent")
        if any(
            value != value.strip() or "\x00" in value
            for value in (self.actor, self.profile)
        ):
            raise ValueError("transaction actor and profile must be canonical")
        installed_at = (
            self.installed_provenance.installed_at
            if self.installed_provenance is not None
            else self.created_at
        )
        provenance_actor = (
            self.installed_provenance.actor
            if self.installed_provenance is not None
            else self.actor
        )
        candidate = InstalledPackageProvenance.model_validate({
            "schemaVersion": 1,
            "identity": self.identity.model_dump(mode="json", by_alias=True),
            "sourceName": self.source_name,
            "repositoryUrl": self.repository_url,
            "configuredRef": self.configured_ref,
            "resolvedCommit": self.resolved_commit,
            "packagePath": self.package_path,
            "packageVersion": self.package_version,
            "contractVersion": self.contract_version,
            "distributionDigest": self.distribution_digest,
            "installedAt": installed_at,
            "actor": provenance_actor,
            "workflowPaths": self.workflow_paths,
        })
        if (
            self.operation == "remove"
            and self.installed_provenance is not None
            and candidate != self.installed_provenance
        ):
            raise ValueError("removal candidate does not match installed provenance")
        return self


class _PreparedState(_StateModel):
    schema_version: int = Field(alias="schemaVersion")
    transactions: list[_PreparedRecord] = Field(max_length=_MAX_PREPARED)

    @model_validator(mode="after")
    def require_version_and_unique_records(self) -> "_PreparedState":
        if self.schema_version != _STATE_VERSION:
            raise ValueError("transaction schema version is unsupported")
        ids = [item.transaction_id for item in self.transactions]
        digests = [item.token_digest for item in self.transactions]
        if (
            ids != sorted(ids)
            or len(ids) != len(set(ids))
            or len(digests) != len(set(digests))
        ):
            raise ValueError("prepared transactions must be unique and sorted")
        return self


class _JournalRecord(_StateModel):
    transaction_id: str = Field(alias="transactionId", pattern=_TRANSACTION_ID.pattern)
    operation: Literal["install", "remove"]
    phase: JournalPhase
    identity: InstalledPackageIdentity
    destination: str = Field(min_length=1, max_length=4096)
    staging_path: str = Field(alias="stagingPath", min_length=1, max_length=4096)
    quarantine_path: str = Field(alias="quarantinePath", min_length=1, max_length=4096)
    consumed_at: str = Field(alias="consumedAt", min_length=20, max_length=64)
    consumed_expires_at: str = Field(
        alias="consumedExpiresAt", min_length=20, max_length=64
    )
    candidate_provenance: InstalledPackageProvenance | None = Field(
        default=None, alias="candidateProvenance"
    )
    previous_provenance: InstalledPackageProvenance | None = Field(
        default=None, alias="previousProvenance"
    )
    trust_origin: str | None = Field(
        default=None,
        alias="trustOrigin",
        max_length=256,
        pattern=_TRUST_ORIGIN.pattern,
    )

    @model_validator(mode="after")
    def require_operation_shape(self) -> "_JournalRecord":
        if not self.phase.startswith(f"{self.operation}_"):
            raise ValueError("journal phase does not match its operation")
        if self.operation == "install" and self.candidate_provenance is None:
            raise ValueError("install journal requires candidate provenance")
        if self.operation == "remove" and (
            self.candidate_provenance is not None or self.previous_provenance is None
        ):
            raise ValueError("remove journal requires only prior provenance")
        expected_origin = _marketplace_trust_origin(self.identity)
        if self.trust_origin is not None and self.trust_origin != expected_origin:
            raise ValueError("journal trust origin does not match its identity")
        if self.phase.endswith("_trust_revoke_pending") and self.trust_origin is None:
            raise ValueError("trust revocation phase requires an exact origin")
        consumed_at = _parse_timestamp(self.consumed_at)
        consumed_expires_at = _parse_timestamp(self.consumed_expires_at)
        if not (
            consumed_at
            < consumed_expires_at
            <= consumed_at + timedelta(seconds=_CONSUMED_LEASE_SECONDS)
        ):
            raise ValueError("consumed transaction lease is invalid")
        for provenance in (
            self.candidate_provenance,
            self.previous_provenance,
        ):
            if provenance is not None:
                _parse_timestamp(provenance.installed_at)
                if provenance.identity != self.identity:
                    raise ValueError("journal provenance identity is inconsistent")
        return self


class _JournalState(_StateModel):
    schema_version: int = Field(alias="schemaVersion")
    journals: list[_JournalRecord] = Field(max_length=_MAX_JOURNALS)

    @model_validator(mode="after")
    def require_version_and_unique_records(self) -> "_JournalState":
        if self.schema_version != _STATE_VERSION:
            raise ValueError("journal schema version is unsupported")
        ids = [item.transaction_id for item in self.journals]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ValueError("journals must be unique and sorted")
        return self


class _OwnerMarker(_StateModel):
    schema_version: int = Field(alias="schemaVersion")
    owner: Literal["hermes-workflow-marketplace"]
    transaction_id: str = Field(alias="transactionId", pattern=_TRANSACTION_ID.pattern)
    kind: Literal["staging", "quarantine"]
    identity: InstalledPackageIdentity
    destination: str = Field(min_length=1, max_length=4096)
    package_digest: str | None = Field(
        default=None, alias="packageDigest", pattern=_SHA256.pattern
    )
    created_at: str = Field(alias="createdAt", min_length=20, max_length=64)

    @model_validator(mode="after")
    def require_version(self) -> "_OwnerMarker":
        if self.schema_version != _STATE_VERSION:
            raise ValueError("owned marker version is unsupported")
        _parse_timestamp(self.created_at)
        return self


def _fail(code: str, message: str) -> NoReturn:
    raise WorkflowMarketplaceError(code, message)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError("invalid transaction timestamp") from error
    canonical = _timestamp(parsed)
    if canonical != value:
        raise ValueError("invalid transaction timestamp")
    return parsed


def _clean_identity_text(value: object, *, label: str, limit: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or value != value.strip()
        or "\x00" in value
    ):
        _fail("transaction_candidate_invalid", f"{label} is invalid")
    return value


def _marketplace_trust_origin(identity: InstalledPackageIdentity) -> str:
    return f"marketplace:{identity.source_key}/{identity.package_id}"


def _validated_trust_origin(
    identity: InstalledPackageIdentity,
    trust_origin: str | None,
) -> str | None:
    if trust_origin is None:
        return None
    expected = _marketplace_trust_origin(identity)
    if _TRUST_ORIGIN.fullmatch(trust_origin) is None or trust_origin != expected:
        _fail(
            "transaction_trust_origin_invalid",
            "trust origin does not match the installed package identity",
        )
    return trust_origin


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _entry(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _require_directory(path: Path, *, private: bool = False) -> None:
    created = False
    try:
        path.mkdir(mode=0o700 if private else 0o755)
        created = True
    except FileExistsError:
        pass
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or _is_reparse_point(metadata)
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        _fail("transaction_destination_invalid", "transaction path must be a directory")
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o222 == 0:
        _fail("transaction_destination_invalid", "transaction path is read-only")
    if created and private and os.name != "nt":
        os.chmod(path, 0o700)
    elif private and os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o700:
        _fail(
            "transaction_destination_invalid",
            "transaction state directory permissions are not private",
        )


def _require_existing_directory(path: Path, *, code: str) -> None:
    metadata = _entry(path)
    if (
        metadata is None
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse_point(metadata)
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        _fail(code, "transaction package path is invalid")


def _require_private_directory_at(parent_descriptor: int, name: str) -> None:
    created = False
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
        created = True
    except FileExistsError:
        pass
    before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if (
        stat.S_ISLNK(before.st_mode)
        or _is_reparse_point(before)
        or not stat.S_ISDIR(before.st_mode)
    ):
        _fail("transaction_destination_invalid", "transaction path must be a directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            _fail("transaction_destination_invalid", "transaction directory changed")
        if created:
            os.fchmod(descriptor, 0o700)
        elif stat.S_IMODE(opened.st_mode) != 0o700:
            _fail(
                "transaction_destination_invalid",
                "transaction state directory permissions are not private",
            )
        after = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            stat.S_ISLNK(after.st_mode)
            or _is_reparse_point(after)
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            _fail("transaction_destination_invalid", "transaction directory changed")
    finally:
        os.close(descriptor)


class _PreparationWorkspace:
    """Keep every staging mutation beneath one retained private authority."""

    def __init__(self, store: MarketplaceTransactionStore, identity: tuple[int, int]):
        self.store = store
        self.identity = identity
        self.guards = ExitStack()
        self.directory_guards: dict[Path, ExitStack] = {}
        self.directories: dict[Path, tuple[int | None, tuple[int, int]]] = {}
        self.envelope: Path | None = None
        self.marker_durable = False
        self.marker_content: bytes | None = None

    def open(self) -> None:
        try:
            self.store._require_root_identity(self.identity)
            if _DIRECTORY_FD_SUPPORTED:
                descriptor, identity = _open_no_follow_directory(self.store.root)
                self.guards.callback(os.close, descriptor)
                if identity != self.identity:
                    _fail("transaction_state_invalid", "marketplace authority changed")
                self.directories[self.store.root] = (descriptor, identity)
            elif os.name == "nt":
                # Pin the whole path before using Windows path APIs. No-delete
                # sharing prevents both ordinary-directory and reparse swaps.
                for parent in (*reversed(self.store.root.parents), self.store.root):
                    kernel32, handle = _open_windows_directory_guard(parent)
                    self.guards.callback(kernel32.CloseHandle, handle)
                self.directories[self.store.root] = (None, self.identity)
            else:
                _fail(
                    "transaction_state_invalid", "secure workspace access unavailable"
                )
            self.directory(self.store.staging_root)
            self.validate()
        except BaseException:
            self.guards.close()
            raise

    def validate(self) -> None:
        self.store._require_root_identity(self.identity)
        for path, (descriptor, identity) in self.directories.items():
            if path == self.store.root:
                continue
            parent_descriptor = self.directories[path.parent][0]
            metadata = os.stat(
                path if parent_descriptor is None else path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                stat.S_ISLNK(metadata.st_mode)
                or _is_reparse_point(metadata)
                or not stat.S_ISDIR(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino) != identity
                or (os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o700)
            ):
                _fail("transaction_state_invalid", "staging authority changed")
            if descriptor is not None:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != identity:
                    _fail("transaction_state_invalid", "staging authority changed")

    def directory(self, path: Path, *, create: bool = False) -> None:
        parent_descriptor = self.directories[path.parent][0]
        name = path if parent_descriptor is None else path.name
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=parent_descriptor)
            except FileExistsError:
                _fail("transaction_path_occupied", "transaction path already exists")
            if path.parent == self.store.staging_root:
                self.envelope = path
        before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            stat.S_ISLNK(before.st_mode)
            or _is_reparse_point(before)
            or not stat.S_ISDIR(before.st_mode)
        ):
            _fail("transaction_state_invalid", "staging directory is invalid")
        identity = (before.st_dev, before.st_ino)
        descriptor = None
        guards = ExitStack()
        self.guards.callback(guards.close)
        if parent_descriptor is not None:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            flags |= getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(name, flags, dir_fd=parent_descriptor)
            guards.callback(os.close, descriptor)
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != identity:
                _fail("transaction_state_invalid", "staging directory changed")
            if create:
                os.fchmod(descriptor, 0o700)
        else:
            kernel32, handle = _open_windows_directory_guard(path)
            guards.callback(kernel32.CloseHandle, handle)
        self.directories[path] = (descriptor, identity)
        self.directory_guards[path] = guards
        self.validate()
        if create:
            self.fsync(path.parent)

    def close_directory(self, path: Path) -> None:
        self.fsync(path)
        self.directory_guards.pop(path).close()
        del self.directories[path]

    def fsync(self, path: Path) -> None:
        descriptor = self.directories[path][0]
        if descriptor is not None:
            os.fsync(descriptor)
        self.validate()

    def write_file(self, path: Path, content: bytes) -> None:
        parent_descriptor = self.directories[path.parent][0]
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        for option in ("O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW", "O_BINARY"):
            flags |= getattr(os, option, 0)
        descriptor = os.open(
            path if parent_descriptor is None else path.name,
            flags,
            0o600,
            dir_fd=parent_descriptor,
        )
        try:
            if parent_descriptor is not None:
                os.fchmod(descriptor, 0o600)
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("could not write staging bytes")
                view = view[written:]
            os.fsync(descriptor)
            self.validate()
        finally:
            os.close(descriptor)

    def write_marker(self, envelope: Path, text: str) -> None:
        self.directory(envelope, create=True)
        self.marker_content = text.encode("utf-8")
        descriptor, identity = self.directories[envelope]
        if descriptor is None:
            atomic_write_text(
                envelope / "owner.json",
                text,
                tmp_prefix="owner.json.tmp-",
                create_mode=0o600,
                no_follow=True,
                expected_parent_identity=identity,
            )
        else:
            temporary = f"owner.json.tmp-{secrets.token_hex(16)}"
            created = False
            try:
                # Register only a temporary name this call successfully created.
                # Publication stays relative to the retained envelope descriptor.
                self.write_file(envelope / temporary, text.encode("utf-8"))
                created = True
                os.replace(
                    temporary,
                    "owner.json",
                    src_dir_fd=descriptor,
                    dst_dir_fd=descriptor,
                )
                created = False
            finally:
                if created:
                    os.unlink(temporary, dir_fd=descriptor)
        self.fsync(envelope)
        self.marker_durable = True

    def _read_marker_content(self) -> bytes | None:
        if self.envelope is None or self.envelope not in self.directories:
            raise OSError("ownership envelope could not be identified")
        descriptor = self.directories[self.envelope][0]
        if descriptor is None:
            return _read_bounded(
                self.envelope / "owner.json",
                limit=_MAX_MARKER_BYTES,
                size_code="transaction_marker_size_limit",
            )
        try:
            marker = os.open(
                "owner.json",
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=descriptor,
            )
        except FileNotFoundError:
            return None
        try:
            metadata = os.fstat(marker)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise OSError("ownership marker is invalid")
            chunks = bytearray()
            while len(chunks) <= _MAX_MARKER_BYTES:
                chunk = os.read(marker, _MAX_MARKER_BYTES + 1 - len(chunks))
                if not chunk:
                    break
                chunks.extend(chunk)
            return bytes(chunks)
        finally:
            os.close(marker)

    def has_durable_marker(self) -> bool:
        try:
            return (
                self.marker_durable
                and self._read_marker_content() == self.marker_content
            )
        except (OSError, WorkflowMarketplaceError):
            return False

    def cleanup(self) -> bool:
        envelope = self.envelope
        if envelope is None:
            return True
        if envelope not in self.directories:
            return False
        _, identity = self.directories[envelope]
        parent_descriptor = self.directories[envelope.parent][0]
        try:
            current = os.stat(
                envelope if parent_descriptor is None else envelope.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (current.st_dev, current.st_ino) != identity or _is_reparse_point(
                current
            ):
                return False
            marker = self._read_marker_content()
            if marker is not None and marker != self.marker_content:
                return False
            if parent_descriptor is None:
                # Windows cannot remove directories while their no-delete-share
                # guards are held. Keep uncertain artifacts for recovery instead
                # of releasing authority and deleting through an unpinned path.
                return False
            else:
                shutil.rmtree(envelope.name, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
                return True
        except (OSError, WorkflowMarketplaceError):
            # Uncertain artifacts remain under their original authority; never
            # retry cleanup via a replacement absolute path.
            return False


def _confirmation_digest(record: _PreparedRecord) -> str:
    value = record.model_dump(mode="json", by_alias=True)
    value.pop("tokenDigest")
    value.pop("confirmationDigest")
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(_CONFIRMATION_DOMAIN + encoded).hexdigest()


def _record_to_prepared(
    record: _PreparedRecord,
    *,
    token: str,
    consumed: bool,
    consumed_at: str | None = None,
    consumed_expires_at: str | None = None,
) -> PreparedTransaction:
    return PreparedTransaction(
        transaction_id=record.transaction_id,
        token=token,
        consumed=consumed,
        operation=record.operation,
        identity=record.identity,
        source_name=record.source_name,
        repository_url=record.repository_url,
        configured_ref=record.configured_ref,
        resolved_commit=record.resolved_commit,
        package_path=record.package_path,
        package_version=record.package_version,
        contract_version=record.contract_version,
        distribution_digest=record.distribution_digest,
        workflow_paths=tuple(record.workflow_paths),
        destination=Path(record.destination),
        staging_path=Path(record.staging_path),
        review_digest=record.review_digest,
        confirmation_digest=record.confirmation_digest,
        actor=record.actor,
        profile=record.profile,
        created_at=record.created_at,
        expires_at=record.expires_at,
        consumed_at=consumed_at,
        consumed_expires_at=consumed_expires_at,
        installed_provenance=record.installed_provenance,
    )


class MarketplaceTransactionStore:
    """Persist confirmations and own install/remove recovery for one profile."""

    max_state_bytes = _MAX_STATE_BYTES

    def __init__(
        self,
        hermes_home: Path | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        lock_timeout_seconds: float = 5.0,
        trust_store: WorkflowTrustStore | None = None,
    ):
        self.home = _absolute(
            Path(hermes_home) if hermes_home is not None else get_hermes_home()
        )
        shared = WorkflowSourceStore(self.home)
        self.root = shared.root
        self.lock_path = shared.lock_path
        self.path = self.root / "transactions.json"
        self.journal_path = self.root / "transaction-journals.json"
        self.staging_root = self.root / ".staging"
        self.quarantine_root = self.root / ".quarantine"
        self.installed_store = InstalledPackageStore(
            self.home,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        self.trust_store = trust_store or WorkflowTrustStore(self.home)
        self._shared_store = shared
        self.clock = clock
        self.token_factory = token_factory
        self.lock_timeout_seconds = lock_timeout_seconds

    @contextmanager
    def _locked(
        self, expected_parent_identity: tuple[int, int] | None = None
    ) -> Iterator[tuple[int, int]]:
        try:
            if expected_parent_identity is not None:
                self._require_root_identity(expected_parent_identity)
            root_identity = self._shared_store._ensure_private_root()
            if (
                expected_parent_identity is not None
                and root_identity != expected_parent_identity
            ):
                _fail(
                    "transaction_state_invalid", "marketplace state directory changed"
                )
            self._require_root_identity(root_identity)
            with workflow_lock(
                self.lock_path,
                timeout_seconds=self.lock_timeout_seconds,
            ):
                try:
                    metadata = self.root.lstat()
                except OSError:
                    _fail(
                        "transaction_state_invalid",
                        "marketplace transaction state directory changed",
                    )
                if (
                    stat.S_ISLNK(metadata.st_mode)
                    or _is_reparse_point(metadata)
                    or not stat.S_ISDIR(metadata.st_mode)
                    or (metadata.st_dev, metadata.st_ino) != root_identity
                ):
                    _fail(
                        "transaction_state_invalid",
                        "marketplace transaction state directory changed",
                    )
                yield root_identity
        except WorkflowLockTimeout as error:
            _fail("transaction_lock_timeout", str(error))
        except WorkflowMarketplaceError as error:
            if error.code.startswith("source_state"):
                _fail(
                    "transaction_state_invalid",
                    "marketplace transaction state directory is invalid",
                )
            raise

    def _require_root_identity(self, expected: tuple[int, int]) -> None:
        try:
            actual = _validate_no_follow_parent_path(self.root)
            metadata = self.root.lstat()
            parent_metadata = self.root.parent.lstat()
        except OSError:
            _fail("transaction_state_invalid", "marketplace state directory changed")
        if (
            actual != expected
            or (metadata.st_dev, metadata.st_ino) != expected
            or (
                os.name != "nt"
                and any(
                    stat.S_IMODE(item.st_mode) != 0o700
                    for item in (metadata, parent_metadata)
                )
            )
        ):
            _fail("transaction_state_invalid", "marketplace state directory changed")

    def _ensure_workflow_roots(self) -> tuple[int, int]:
        if not _DIRECTORY_FD_SUPPORTED and os.name != "nt":
            _fail("transaction_state_invalid", "secure workspace access unavailable")
        _require_directory(self.home)
        workflows = self.home / "workflows"
        _require_directory(workflows)
        try:
            root_identity = self._shared_store._ensure_private_root()
        except WorkflowMarketplaceError as error:
            if error.code.startswith("source_state"):
                _fail(
                    "transaction_state_invalid",
                    "marketplace transaction state directory is invalid",
                )
            raise
        self._require_root_identity(root_identity)
        with ExitStack() as guards:
            parent_descriptor = None
            try:
                if _DIRECTORY_FD_SUPPORTED:
                    parent_descriptor, opened_identity = _open_no_follow_directory(
                        self.root
                    )
                    guards.callback(os.close, parent_descriptor)
                    if opened_identity != root_identity:
                        _fail(
                            "transaction_state_invalid",
                            "marketplace state directory changed",
                        )
                elif os.name == "nt":
                    for parent in (*reversed(self.root.parents), self.root):
                        kernel32, handle = _open_windows_directory_guard(parent)
                        guards.callback(kernel32.CloseHandle, handle)
            except OSError:
                _fail(
                    "transaction_state_invalid", "marketplace state directory changed"
                )
            for root in (self.staging_root, self.quarantine_root):
                self._require_root_identity(root_identity)
                if parent_descriptor is None:
                    _require_directory(root, private=True)
                else:
                    _require_private_directory_at(parent_descriptor, root.name)
                self._require_root_identity(root_identity)
        return root_identity

    def _empty_prepared(self) -> _PreparedState:
        return _PreparedState.model_validate({
            "schemaVersion": _STATE_VERSION,
            "transactions": [],
        })

    def _empty_journals(self) -> _JournalState:
        return _JournalState.model_validate({
            "schemaVersion": _STATE_VERSION,
            "journals": [],
        })

    def _read_model(self, path: Path, model: type[_StateModel], empty: _StateModel):
        if not _path_entry_exists(path):
            return empty
        raw = _read_bounded(
            path,
            limit=self.max_state_bytes,
            size_code="transaction_state_size_limit",
        )
        value = _strict_json(raw, code="transaction_state_invalid")
        try:
            return model.model_validate(value)
        except ValidationError:
            _fail("transaction_state_invalid", "persisted transaction state is invalid")

    def _read_prepared(self) -> _PreparedState:
        state = self._read_model(
            self.path,
            _PreparedState,
            self._empty_prepared(),
        )
        assert isinstance(state, _PreparedState)
        for record in state.transactions:
            self._validate_record_paths(record)
            try:
                _parse_timestamp(record.created_at)
                _parse_timestamp(record.expires_at)
            except ValueError:
                _fail("transaction_state_invalid", "transaction timestamp is invalid")
            if _confirmation_digest(record) != record.confirmation_digest:
                _fail(
                    "transaction_state_invalid", "transaction confirmation is invalid"
                )
        return state

    def _read_journals(self) -> _JournalState:
        state = self._read_model(
            self.journal_path,
            _JournalState,
            self._empty_journals(),
        )
        assert isinstance(state, _JournalState)
        for record in state.journals:
            self._validate_journal_paths(record)
        return state

    def _render(self, value: BaseModel) -> str:
        rendered = (
            json.dumps(
                value.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        if len(rendered.encode("utf-8")) > self.max_state_bytes:
            _fail(
                "transaction_state_size_limit",
                "transaction state exceeds its byte limit",
            )
        return rendered

    def _write_model(
        self,
        path: Path,
        value: BaseModel,
        *,
        parent_identity: tuple[int, int],
    ) -> None:
        try:
            atomic_write_text(
                path,
                self._render(value),
                tmp_prefix=f"{path.name}.tmp-",
                create_mode=0o600,
                no_follow=True,
                expected_parent_identity=parent_identity,
            )
        except OSError as error:
            raise WorkflowMarketplaceError(
                "transaction_state_write_failed",
                "could not atomically replace transaction state",
            ) from error

    def _write_prepared(
        self,
        records: list[_PreparedRecord],
        *,
        parent_identity: tuple[int, int],
    ) -> None:
        records.sort(key=lambda item: item.transaction_id)
        self._write_model(
            self.path,
            _PreparedState.model_validate({
                "schemaVersion": _STATE_VERSION,
                "transactions": records,
            }),
            parent_identity=parent_identity,
        )

    def _write_journals(
        self,
        records: list[_JournalRecord],
        *,
        parent_identity: tuple[int, int],
    ) -> None:
        records.sort(key=lambda item: item.transaction_id)
        self._write_model(
            self.journal_path,
            _JournalState.model_validate({
                "schemaVersion": _STATE_VERSION,
                "journals": records,
            }),
            parent_identity=parent_identity,
        )

    def _validate_record_paths(self, record: _PreparedRecord) -> None:
        if (
            Path(record.destination)
            != self.installed_store.package_root(record.identity)
            or Path(record.staging_path)
            != self.staging_root / record.transaction_id / "package"
        ):
            _fail("transaction_state_invalid", "transaction paths are inconsistent")

    def _validate_journal_paths(self, record: _JournalRecord) -> None:
        if (
            Path(record.destination)
            != self.installed_store.package_root(record.identity)
            or Path(record.staging_path)
            != self.staging_root / record.transaction_id / "package"
            or Path(record.quarantine_path)
            != self.quarantine_root / record.transaction_id / "package"
        ):
            _fail("transaction_state_invalid", "journal paths are inconsistent")

    def _write_marker(
        self,
        envelope: Path,
        marker: _OwnerMarker,
        *,
        workspace: _PreparationWorkspace | None = None,
    ) -> None:
        if workspace is not None:
            workspace.write_marker(
                envelope,
                json.dumps(
                    marker.model_dump(mode="json", by_alias=True),
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
            )
            return
        try:
            envelope.mkdir(mode=0o700)
        except FileExistsError:
            _fail(
                "transaction_path_occupied",
                "transaction ownership envelope already exists",
            )
        except OSError as error:
            raise WorkflowMarketplaceError(
                "transaction_state_write_failed",
                "could not create transaction ownership envelope",
            ) from error
        metadata = envelope.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or _is_reparse_point(metadata)
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            _fail(
                "transaction_state_write_failed",
                "transaction ownership envelope is invalid",
            )
        parent_identity = (metadata.st_dev, metadata.st_ino)
        _fsync_directory(envelope.parent)
        try:
            atomic_write_text(
                envelope / "owner.json",
                json.dumps(
                    marker.model_dump(mode="json", by_alias=True),
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                tmp_prefix="owner.json.tmp-",
                create_mode=0o600,
                no_follow=True,
                expected_parent_identity=parent_identity,
            )
        except OSError as error:
            try:
                current = envelope.lstat()
                if (current.st_dev, current.st_ino) == parent_identity and not any(
                    envelope.iterdir()
                ):
                    envelope.rmdir()
                    _fsync_directory(envelope.parent)
            except OSError:
                pass
            raise WorkflowMarketplaceError(
                "transaction_state_write_failed",
                "could not write transaction ownership marker",
            ) from error

    def _read_marker(
        self,
        envelope: Path,
        *,
        transaction_id: str,
        kind: Literal["staging", "quarantine"],
        identity: InstalledPackageIdentity,
        destination: Path,
        package_digest: str | None,
    ) -> _OwnerMarker | None:
        metadata = _entry(envelope)
        if (
            metadata is None
            or stat.S_ISLNK(metadata.st_mode)
            or _is_reparse_point(metadata)
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            return None
        path = envelope / "owner.json"
        if not _path_entry_exists(path):
            return None
        try:
            raw = _read_bounded(
                path,
                limit=_MAX_MARKER_BYTES,
                size_code="transaction_marker_size_limit",
            )
            marker = _OwnerMarker.model_validate(
                _strict_json(raw, code="transaction_marker_invalid")
            )
        except (ValidationError, WorkflowMarketplaceError):
            return None
        expected = (
            transaction_id,
            kind,
            identity,
            str(destination),
            package_digest,
        )
        actual = (
            marker.transaction_id,
            marker.kind,
            marker.identity,
            marker.destination,
            marker.package_digest,
        )
        return marker if actual == expected else None

    def _new_marker(
        self,
        transaction_id: str,
        kind: Literal["staging", "quarantine"],
        identity: InstalledPackageIdentity,
        destination: Path,
        package_digest: str | None,
        created_at: str,
    ) -> _OwnerMarker:
        return _OwnerMarker.model_validate({
            "schemaVersion": _STATE_VERSION,
            "owner": _OWNER,
            "transactionId": transaction_id,
            "kind": kind,
            "identity": identity.model_dump(mode="json", by_alias=True),
            "destination": str(destination),
            "packageDigest": package_digest,
            "createdAt": created_at,
        })

    def _copy_verified_distribution(
        self,
        distribution: WorkflowDistribution,
        destination: Path,
        *,
        workspace: _PreparationWorkspace,
    ) -> None:
        workspace.directory(destination, create=True)
        active = [destination]
        # Component ordering finishes each sibling subtree before closing its
        # descriptor/handle. No completed branch is opened for writing again.
        for item in sorted(
            distribution.files, key=lambda item: tuple(item.relative_path.split("/"))
        ):
            target = destination.joinpath(*item.relative_path.split("/"))
            while active[-1] not in target.parents:
                workspace.close_directory(active.pop())
            components = target.parent.relative_to(active[-1]).parts
            for component in components:
                parent = active[-1] / component
                workspace.directory(parent, create=True)
                active.append(parent)
            workspace.write_file(target, item.content)
        while len(active) > 1:
            workspace.close_directory(active.pop())
        for directory in reversed(workspace.directories):
            workspace.fsync(directory)

    def _remove_owned_envelope(
        self,
        envelope: Path,
        *,
        transaction_id: str,
        kind: Literal["staging", "quarantine"],
        identity: InstalledPackageIdentity,
        destination: Path,
        package_digest: str | None,
    ) -> bool:
        if (
            self._read_marker(
                envelope,
                transaction_id=transaction_id,
                kind=kind,
                identity=identity,
                destination=destination,
                package_digest=package_digest,
            )
            is None
        ):
            return False
        shutil.rmtree(envelope)
        _fsync_directory(envelope.parent)
        return True

    def _candidate_provenance(
        self, prepared: PreparedTransaction
    ) -> InstalledPackageProvenance:
        try:
            return InstalledPackageProvenance.model_validate({
                "schemaVersion": 1,
                "identity": prepared.identity.model_dump(mode="json", by_alias=True),
                "sourceName": prepared.source_name,
                "repositoryUrl": prepared.repository_url,
                "configuredRef": prepared.configured_ref,
                "resolvedCommit": prepared.resolved_commit,
                "packagePath": prepared.package_path,
                "packageVersion": prepared.package_version,
                "contractVersion": prepared.contract_version,
                "distributionDigest": prepared.distribution_digest,
                "installedAt": prepared.created_at,
                "actor": prepared.actor,
                "workflowPaths": list(prepared.workflow_paths),
            })
        except (AttributeError, TypeError, ValidationError, ValueError) as error:
            raise WorkflowMarketplaceError(
                "confirmation_token_invalid",
                "confirmation transaction is invalid",
            ) from error

    def _validate_remove_candidate(
        self,
        candidate: TransactionCandidate,
        distribution: WorkflowDistribution,
        destination: Path,
    ) -> InstalledPackageProvenance:
        installed = candidate.installed_provenance
        workflows = [member.definition for member in distribution.manifest.workflows]
        if (
            installed is None
            or _absolute(distribution.root) != destination
            or installed.identity != candidate.identity
            or installed.source_name != candidate.source_name
            or installed.repository_url != candidate.repository_url
            or installed.configured_ref != candidate.configured_ref
            or installed.resolved_commit != candidate.resolved_commit
            or installed.package_path != candidate.package_path
            or installed.package_version != distribution.manifest.version
            or installed.contract_version != 1
            or installed.distribution_digest != distribution.digest
            or installed.workflow_paths != workflows
        ):
            _fail(
                "transaction_candidate_invalid",
                "removal candidate does not match installed provenance",
            )
        return installed

    def prepare(
        self,
        candidate: TransactionCandidate,
        *,
        review_digest: str,
        actor: str,
        profile: str,
        ttl_seconds: int = 300,
    ) -> PreparedTransaction:
        """Revalidate a candidate and issue one persisted confirmation token."""

        if not isinstance(candidate, TransactionCandidate):
            _fail("transaction_candidate_invalid", "transaction candidate is invalid")
        if candidate.operation not in {"install", "remove"}:
            _fail("transaction_candidate_invalid", "transaction operation is invalid")
        actor = _clean_identity_text(actor, label="actor", limit=256)
        profile = _clean_identity_text(profile, label="profile", limit=256)
        if (
            not isinstance(review_digest, str)
            or _SHA256.fullmatch(review_digest) is None
        ):
            _fail("transaction_review_invalid", "review digest is invalid")
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or not 1 <= ttl_seconds <= 3600
        ):
            _fail("transaction_expiry_invalid", "confirmation expiry is invalid")
        if not isinstance(candidate.distribution, WorkflowDistribution):
            _fail("transaction_candidate_invalid", "candidate distribution is invalid")
        distribution = load_distribution(
            candidate.distribution.root,
            expected_digest=candidate.distribution.digest,
        )
        destination = _absolute(candidate.destination)
        if destination != self.installed_store.package_root(candidate.identity):
            _fail("transaction_destination_invalid", "candidate destination is invalid")
        if (
            distribution.manifest.id != candidate.identity.package_id
            or distribution.manifest.version != candidate.distribution.manifest.version
        ):
            _fail("transaction_candidate_invalid", "candidate identity is inconsistent")
        if candidate.operation == "install":
            if candidate.installed_provenance is not None:
                _fail(
                    "transaction_candidate_invalid",
                    "install candidate includes removal provenance",
                )
            installed_provenance = None
        else:
            installed_provenance = self._validate_remove_candidate(
                candidate,
                distribution,
                destination,
            )
        now = self.clock()
        created_at = _timestamp(now)
        expires_at = _timestamp(now + timedelta(seconds=ttl_seconds))
        transaction_id = secrets.token_hex(16)
        token = self.token_factory()
        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            _fail("transaction_state_invalid", "token generator returned invalid data")
        staging_envelope = self.staging_root / transaction_id
        staging_path = staging_envelope / "package"
        root_identity = self._ensure_workflow_roots()
        self._validate_destination_parent(destination)
        self._require_root_identity(root_identity)
        workspace = _PreparationWorkspace(self, root_identity)
        try:
            workspace.open()
            staged = distribution
            if candidate.operation == "install":
                self._write_marker(
                    staging_envelope,
                    self._new_marker(
                        transaction_id,
                        "staging",
                        candidate.identity,
                        destination,
                        distribution.digest,
                        created_at,
                    ),
                    workspace=workspace,
                )
                self._copy_verified_distribution(
                    distribution,
                    staging_path,
                    workspace=workspace,
                )
                workspace.validate()
                staged = load_distribution(
                    staging_path,
                    expected_digest=distribution.digest,
                )
                workspace.validate()
            raw = {
                "transactionId": transaction_id,
                "tokenDigest": hashlib.sha256(token.encode()).hexdigest(),
                "operation": candidate.operation,
                "identity": candidate.identity.model_dump(mode="json", by_alias=True),
                "sourceName": candidate.source_name,
                "repositoryUrl": candidate.repository_url,
                "configuredRef": candidate.configured_ref,
                "resolvedCommit": candidate.resolved_commit,
                "packagePath": candidate.package_path,
                "packageVersion": staged.manifest.version,
                "contractVersion": 1,
                "distributionDigest": staged.digest,
                "workflowPaths": [
                    member.definition for member in staged.manifest.workflows
                ],
                "destination": str(destination),
                "stagingPath": str(staging_path),
                "reviewDigest": review_digest,
                "confirmationDigest": "0" * 64,
                "actor": actor,
                "profile": profile,
                "createdAt": created_at,
                "expiresAt": expires_at,
                "installedProvenance": (
                    installed_provenance.model_dump(mode="json", by_alias=True)
                    if installed_provenance is not None
                    else None
                ),
            }
            with self._locked(root_identity) as parent_identity:
                state = self._read_prepared()
                current = self._current_provenance(candidate.identity)
                destination_metadata = _entry(destination)
                if candidate.operation == "install":
                    if (current is None) != (destination_metadata is None):
                        _fail(
                            "transaction_destination_conflict",
                            "installed package and provenance are inconsistent",
                        )
                    if current is not None:
                        load_distribution(
                            destination,
                            expected_digest=current.distribution_digest,
                        )
                    installed_provenance = current
                else:
                    if current != installed_provenance:
                        _fail(
                            "installed_package_changed",
                            "installed package changed before removal review",
                        )
                    load_distribution(
                        destination,
                        expected_digest=distribution.digest,
                    )
                raw["installedProvenance"] = (
                    installed_provenance.model_dump(mode="json", by_alias=True)
                    if installed_provenance is not None
                    else None
                )
                try:
                    provisional = _PreparedRecord.model_validate(raw)
                except ValidationError as error:
                    raise WorkflowMarketplaceError(
                        "transaction_candidate_invalid",
                        "transaction candidate metadata is invalid",
                    ) from error
                record = provisional.model_copy(
                    update={"confirmation_digest": _confirmation_digest(provisional)}
                )
                if len(state.transactions) >= _MAX_PREPARED:
                    _fail(
                        "transaction_state_size_limit", "too many prepared transactions"
                    )
                self._write_prepared(
                    [*state.transactions, record],
                    parent_identity=parent_identity,
                )
            workspace.validate()
            return _record_to_prepared(record, token=token, consumed=False)
        except BaseException as error:
            cleaned = workspace.cleanup()
            # A package reader can discover a replacement before the next root
            # check. Keep authority failures inside the transaction boundary.
            self._require_root_identity(root_identity)
            if isinstance(error, (OSError, WorkflowMarketplaceError)):
                if not cleaned and not workspace.has_durable_marker():
                    _fail(
                        "transaction_recovery_inspection_incomplete",
                        "failed preparation ownership could not be verified",
                    )
                if isinstance(error, OSError):
                    raise WorkflowMarketplaceError(
                        "transaction_state_write_failed",
                        "could not write transaction preparation",
                    ) from error
            raise
        finally:
            workspace.guards.close()

    def consume(
        self,
        raw_token: str,
        *,
        actor: str,
        profile: str,
    ) -> PreparedTransaction:
        """Consume one actor/profile-bound confirmation token under the lock."""

        if not isinstance(raw_token, str) or _TOKEN.fullmatch(raw_token) is None:
            _fail("confirmation_token_invalid", "confirmation token is invalid")
        digest = hashlib.sha256(raw_token.encode()).hexdigest()
        try:
            with self._locked() as parent_identity:
                state = self._read_prepared()
                record = next(
                    (
                        item
                        for item in state.transactions
                        if item.token_digest == digest
                    ),
                    None,
                )
                now = self.clock()
                if (
                    record is None
                    or not isinstance(actor, str)
                    or not isinstance(profile, str)
                    or record.actor != actor
                    or record.profile != profile
                    or _parse_timestamp(record.expires_at) <= now
                ):
                    _fail("confirmation_token_invalid", "confirmation token is invalid")
                consumed_at = _timestamp(now)
                consumed_expires_at = _timestamp(
                    now + timedelta(seconds=_CONSUMED_LEASE_SECONDS)
                )
                consumed_prepared = _record_to_prepared(
                    record,
                    token=raw_token,
                    consumed=True,
                    consumed_at=consumed_at,
                    consumed_expires_at=consumed_expires_at,
                )
                candidate_provenance = (
                    self._candidate_provenance(consumed_prepared)
                    if record.operation == "install"
                    else None
                )
                previous_provenance = record.installed_provenance
                consumed_journal = _JournalRecord.model_validate({
                    "transactionId": record.transaction_id,
                    "operation": record.operation,
                    "phase": f"{record.operation}_consumed",
                    "identity": record.identity.model_dump(mode="json", by_alias=True),
                    "destination": record.destination,
                    "stagingPath": record.staging_path,
                    "quarantinePath": str(
                        self.quarantine_root / record.transaction_id / "package"
                    ),
                    "consumedAt": consumed_at,
                    "consumedExpiresAt": consumed_expires_at,
                    "candidateProvenance": (
                        candidate_provenance.model_dump(mode="json", by_alias=True)
                        if candidate_provenance is not None
                        else None
                    ),
                    "previousProvenance": (
                        previous_provenance.model_dump(mode="json", by_alias=True)
                        if previous_provenance is not None
                        else None
                    ),
                })
                journals = [
                    item
                    for item in self._read_journals().journals
                    if item.transaction_id != record.transaction_id
                ]
                if len(journals) >= _MAX_JOURNALS:
                    _fail(
                        "transaction_state_size_limit", "too many transaction journals"
                    )
                self._write_journals(
                    [*journals, consumed_journal],
                    parent_identity=parent_identity,
                )
                self._write_prepared(
                    [
                        item
                        for item in state.transactions
                        if item.transaction_id != record.transaction_id
                    ],
                    parent_identity=parent_identity,
                )
                return consumed_prepared
        except WorkflowMarketplaceError:
            raise
        except (TypeError, ValueError):
            _fail("confirmation_token_invalid", "confirmation token is invalid")

    def inspect_token(
        self,
        raw_token: str,
        *,
        actor: str,
        profile: str,
    ) -> PreparedTransactionMetadata:
        """Read live token metadata without consuming or extending the token."""

        if not isinstance(raw_token, str) or _TOKEN.fullmatch(raw_token) is None:
            _fail("confirmation_token_invalid", "confirmation token is invalid")
        digest = hashlib.sha256(raw_token.encode()).hexdigest()
        try:
            with self._locked():
                now = self.clock()
                record = next(
                    (
                        item
                        for item in self._read_prepared().transactions
                        if item.token_digest == digest
                        and item.actor == actor
                        and item.profile == profile
                        and _parse_timestamp(item.expires_at) > now
                    ),
                    None,
                )
                if record is None:
                    _fail(
                        "confirmation_token_invalid",
                        "confirmation token is invalid",
                    )
                return PreparedTransactionMetadata(
                    operation=(
                        "remove"
                        if record.operation == "remove"
                        else (
                            "update"
                            if record.installed_provenance is not None
                            else "install"
                        )
                    ),
                    identity=record.identity,
                    review_digest=record.review_digest,
                    expires_at=record.expires_at,
                )
        except WorkflowMarketplaceError:
            raise
        except (TypeError, ValueError):
            _fail("confirmation_token_invalid", "confirmation token is invalid")

    def _journal_public(self, record: _JournalRecord) -> TransactionJournal:
        return TransactionJournal(
            transaction_id=record.transaction_id,
            operation=record.operation,
            phase=record.phase,
            identity=record.identity,
            destination=Path(record.destination),
            staging_path=Path(record.staging_path),
            quarantine_path=Path(record.quarantine_path),
            consumed_at=record.consumed_at,
            consumed_expires_at=record.consumed_expires_at,
            candidate_provenance=record.candidate_provenance,
            previous_provenance=record.previous_provenance,
        )

    def list_journals(self) -> tuple[TransactionJournal, ...]:
        if not _path_entry_exists(self.journal_path):
            return ()
        with self._locked():
            return tuple(
                self._journal_public(item) for item in self._read_journals().journals
            )

    def _replace_journal(
        self,
        record: _JournalRecord,
        *,
        parent_identity: tuple[int, int],
    ) -> None:
        state = self._read_journals()
        records = [
            item
            for item in state.journals
            if item.transaction_id != record.transaction_id
        ]
        self._write_journals([*records, record], parent_identity=parent_identity)

    def _delete_journal(
        self,
        transaction_id: str,
        *,
        parent_identity: tuple[int, int],
    ) -> None:
        self._write_journals(
            [
                item
                for item in self._read_journals().journals
                if item.transaction_id != transaction_id
            ],
            parent_identity=parent_identity,
        )

    def _require_authorization(
        self,
        prepared: object,
        operation: Literal["install", "remove"],
    ) -> PreparedTransaction:
        if (
            not isinstance(prepared, PreparedTransaction)
            or not prepared.consumed
            or prepared.operation != operation
        ):
            _fail("confirmation_token_invalid", "confirmation transaction is invalid")
        return prepared

    def _require_consumed_journal(
        self,
        prepared: PreparedTransaction,
        operation: Literal["install", "remove"],
    ) -> _JournalRecord:
        journal = next(
            (
                item
                for item in self._read_journals().journals
                if item.transaction_id == prepared.transaction_id
            ),
            None,
        )
        candidate = (
            self._candidate_provenance(prepared) if operation == "install" else None
        )
        previous = prepared.installed_provenance
        if (
            journal is None
            or journal.operation != operation
            or journal.phase != f"{operation}_consumed"
            or journal.identity != prepared.identity
            or journal.destination != str(prepared.destination)
            or journal.staging_path != str(prepared.staging_path)
            or journal.consumed_at != prepared.consumed_at
            or journal.consumed_expires_at != prepared.consumed_expires_at
            or journal.candidate_provenance != candidate
            or journal.previous_provenance != previous
        ):
            _fail("confirmation_token_invalid", "confirmation transaction is invalid")
        now = self.clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or _parse_timestamp(journal.consumed_expires_at) <= now
        ):
            _fail("confirmation_token_invalid", "confirmation transaction is invalid")
        return journal

    def _prepared_record(
        self,
        prepared: PreparedTransaction,
        *,
        distribution_digest: str,
    ) -> _PreparedRecord:
        try:
            return _PreparedRecord.model_validate({
                "transactionId": prepared.transaction_id,
                "tokenDigest": hashlib.sha256(prepared.token.encode()).hexdigest(),
                "operation": prepared.operation,
                "identity": prepared.identity.model_dump(mode="json", by_alias=True),
                "sourceName": prepared.source_name,
                "repositoryUrl": prepared.repository_url,
                "configuredRef": prepared.configured_ref,
                "resolvedCommit": prepared.resolved_commit,
                "packagePath": prepared.package_path,
                "packageVersion": prepared.package_version,
                "contractVersion": prepared.contract_version,
                "distributionDigest": distribution_digest,
                "workflowPaths": list(prepared.workflow_paths),
                "destination": str(prepared.destination),
                "stagingPath": str(prepared.staging_path),
                "reviewDigest": prepared.review_digest,
                "confirmationDigest": prepared.confirmation_digest,
                "actor": prepared.actor,
                "profile": prepared.profile,
                "createdAt": prepared.created_at,
                "expiresAt": prepared.expires_at,
                "installedProvenance": (
                    prepared.installed_provenance.model_dump(
                        mode="json",
                        by_alias=True,
                    )
                    if prepared.installed_provenance is not None
                    else None
                ),
            })
        except (AttributeError, TypeError, ValidationError, ValueError) as error:
            raise WorkflowMarketplaceError(
                "transaction_review_changed",
                "confirmation binding changed",
            ) from error

    def _revalidate_prepared(
        self,
        prepared: PreparedTransaction,
        *,
        review_digest: str,
    ) -> WorkflowDistribution:
        self._require_authorization(prepared, "install")
        if (
            not isinstance(review_digest, str)
            or _SHA256.fullmatch(review_digest) is None
        ):
            _fail("transaction_review_invalid", "review digest is invalid")
        if review_digest != prepared.review_digest:
            _fail("transaction_review_changed", "review changed after confirmation")
        try:
            distribution = load_distribution(
                prepared.staging_path,
                expected_digest=prepared.distribution_digest,
            )
        except WorkflowMarketplaceError as error:
            raise WorkflowMarketplaceError(
                "transaction_candidate_changed",
                "staged package changed after confirmation",
            ) from error
        if (
            distribution.manifest.id != prepared.identity.package_id
            or distribution.manifest.version != prepared.package_version
            or tuple(member.definition for member in distribution.manifest.workflows)
            != prepared.workflow_paths
            or prepared.destination
            != self.installed_store.package_root(prepared.identity)
        ):
            _fail("transaction_candidate_changed", "candidate identity changed")
        record = self._prepared_record(
            prepared,
            distribution_digest=distribution.digest,
        )
        if _confirmation_digest(record) != prepared.confirmation_digest:
            _fail("transaction_review_changed", "confirmation binding changed")
        return distribution

    def _revalidate_remove_prepared(
        self,
        prepared: PreparedTransaction,
        *,
        review_digest: str,
    ) -> tuple[WorkflowDistribution, InstalledPackageProvenance]:
        self._require_authorization(prepared, "remove")
        if (
            not isinstance(review_digest, str)
            or _SHA256.fullmatch(review_digest) is None
        ):
            _fail("transaction_review_invalid", "review digest is invalid")
        if review_digest != prepared.review_digest:
            _fail("transaction_review_changed", "review changed after confirmation")
        previous = prepared.installed_provenance
        if previous is None:
            _fail("confirmation_token_invalid", "confirmation transaction is invalid")
        try:
            distribution = load_distribution(
                prepared.destination,
                expected_digest=prepared.distribution_digest,
            )
        except WorkflowMarketplaceError as error:
            raise WorkflowMarketplaceError(
                "transaction_candidate_changed",
                "installed package changed after confirmation",
            ) from error
        if (
            distribution.manifest.id != prepared.identity.package_id
            or distribution.manifest.version != prepared.package_version
            or tuple(member.definition for member in distribution.manifest.workflows)
            != prepared.workflow_paths
            or prepared.destination
            != self.installed_store.package_root(prepared.identity)
            or self._current_provenance(prepared.identity) != previous
        ):
            _fail(
                "installed_package_changed", "installed package changed before removal"
            )
        record = self._prepared_record(
            prepared,
            distribution_digest=distribution.digest,
        )
        if _confirmation_digest(record) != prepared.confirmation_digest:
            _fail("transaction_review_changed", "confirmation binding changed")
        return distribution, previous

    def _prepare_quarantine(
        self,
        *,
        transaction_id: str,
        identity: InstalledPackageIdentity,
        destination: Path,
        digest: str,
        created_at: str,
    ) -> Path:
        envelope = self.quarantine_root / transaction_id
        self._write_marker(
            envelope,
            self._new_marker(
                transaction_id,
                "quarantine",
                identity,
                destination,
                digest,
                created_at,
            ),
        )
        return envelope / "package"

    def _validate_destination_parent(self, destination: Path) -> None:
        expected = self.home / "workflows"
        for part in ("marketplace", destination.parent.name):
            expected = expected / part
            _require_directory(expected, private=True)
        if expected != destination.parent:
            _fail("transaction_destination_invalid", "package destination is invalid")
        metadata = _entry(destination)
        if metadata is not None and (
            stat.S_ISLNK(metadata.st_mode)
            or _is_reparse_point(metadata)
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            _fail("transaction_destination_invalid", "package destination is unsafe")

    def _current_provenance(
        self, identity: InstalledPackageIdentity
    ) -> InstalledPackageProvenance | None:
        try:
            return self.installed_store.get(identity)
        except WorkflowMarketplaceError as error:
            if error.code == "installed_package_not_found":
                return None
            raise

    def _rollback_install(
        self,
        journal: _JournalRecord,
        provenance_snapshot,
    ) -> None:
        destination = Path(journal.destination)
        staging = Path(journal.staging_path)
        quarantine = Path(journal.quarantine_path)
        candidate = journal.candidate_provenance
        assert candidate is not None
        destination_digest: str | None = None
        if _entry(destination) is not None:
            destination_digest = load_distribution(destination).digest
            if (
                destination_digest == candidate.distribution_digest
                and _entry(staging) is None
            ):
                os.replace(destination, staging)
                _fsync_directory(destination.parent)
                _fsync_directory(staging.parent)
                destination_digest = None
            elif (
                journal.previous_provenance is None
                or destination_digest != journal.previous_provenance.distribution_digest
            ):
                _fail(
                    "transaction_rollback_failed", "candidate rollback path is occupied"
                )
        previous = journal.previous_provenance
        if previous is not None:
            if destination_digest is None:
                load_distribution(
                    quarantine, expected_digest=previous.distribution_digest
                )
                os.replace(quarantine, destination)
                _fsync_directory(destination.parent)
                _fsync_directory(quarantine.parent)
        self.installed_store._restore_snapshot(provenance_snapshot)

    def _finish_install_cleanup(self, journal: _JournalRecord) -> bool:
        candidate = journal.candidate_provenance
        assert candidate is not None
        if journal.previous_provenance is not None:
            quarantine_envelope = Path(journal.quarantine_path).parent
            if _entry(
                quarantine_envelope
            ) is not None and not self._remove_owned_envelope(
                quarantine_envelope,
                transaction_id=journal.transaction_id,
                kind="quarantine",
                identity=journal.identity,
                destination=Path(journal.destination),
                package_digest=journal.previous_provenance.distribution_digest,
            ):
                return False
        staging_envelope = Path(journal.staging_path).parent
        return _entry(staging_envelope) is None or self._remove_owned_envelope(
            staging_envelope,
            transaction_id=journal.transaction_id,
            kind="staging",
            identity=journal.identity,
            destination=Path(journal.destination),
            package_digest=candidate.distribution_digest,
        )

    def atomic_install(
        self,
        prepared: PreparedTransaction,
        *,
        review_digest: str,
        trust_origin: str | None = None,
        provenance_writer: Callable[[InstalledPackageProvenance], object] | None = None,
        cancelled: Callable[[], bool] = lambda: False,
        enter_atomic: Callable[[], bool] = lambda: True,
        fault: Callable[[str], None] = lambda _point: None,
    ) -> InstalledPackageProvenance:
        """Install a consumed candidate with provenance inside one rollback boundary."""

        prepared = self._require_authorization(prepared, "install")
        trust_origin = _validated_trust_origin(prepared.identity, trust_origin)
        root_identity = self._ensure_workflow_roots()
        destination = prepared.destination
        writer = provenance_writer or self.installed_store.put
        with self._locked(root_identity) as parent_identity:
            consumed = self._require_consumed_journal(prepared, "install")
            distribution = self._revalidate_prepared(
                prepared,
                review_digest=review_digest,
            )
            candidate_provenance = self._candidate_provenance(prepared)
            self._validate_destination_parent(destination)
            previous_snapshot = self.installed_store._snapshot()
            previous = prepared.installed_provenance
            current = self._current_provenance(prepared.identity)
            if current != previous:
                _fail(
                    "installed_package_changed",
                    "installed package changed after confirmation",
                )
            destination_metadata = _entry(destination)
            if (previous is None) != (destination_metadata is None):
                _fail(
                    "transaction_destination_conflict",
                    "installed package and provenance are inconsistent",
                )
            if previous is not None:
                load_distribution(
                    destination, expected_digest=previous.distribution_digest
                )
                quarantine = Path(consumed.quarantine_path)
            else:
                quarantine = Path(consumed.quarantine_path)
            journal = consumed.model_copy(
                update={
                    "phase": "install_backup_move_pending",
                    "previous_provenance": previous,
                    "trust_origin": trust_origin,
                }
            )
            try:
                if cancelled() or not enter_atomic():
                    _fail(
                        "marketplace_operation_cancelled",
                        "marketplace operation was cancelled",
                    )
                fault("before_initial_journal")
                self._replace_journal(journal, parent_identity=parent_identity)
                if previous is not None:
                    quarantine = self._prepare_quarantine(
                        transaction_id=prepared.transaction_id,
                        identity=prepared.identity,
                        destination=destination,
                        digest=previous.distribution_digest,
                        created_at=prepared.created_at,
                    )
                fault("after_initial_journal")
                if previous is not None:
                    os.replace(destination, quarantine)
                    _fsync_directory(destination.parent)
                    _fsync_directory(quarantine.parent)
                fault("after_backup_move")
                journal = journal.model_copy(
                    update={"phase": "install_candidate_swap_pending"}
                )
                self._replace_journal(journal, parent_identity=parent_identity)
                os.replace(prepared.staging_path, destination)
                _fsync_directory(destination.parent)
                _fsync_directory(prepared.staging_path.parent)
                fault("after_candidate_swap")
                journal = journal.model_copy(
                    update={"phase": "install_provenance_write_pending"}
                )
                self._replace_journal(journal, parent_identity=parent_identity)
                writer(candidate_provenance)
                if self.installed_store.get(prepared.identity) != candidate_provenance:
                    _fail(
                        "provenance_state_write_failed",
                        "provenance writer did not persist candidate identity",
                    )
                fault("after_provenance_write")
                if trust_origin is not None:
                    journal = journal.model_copy(
                        update={"phase": "install_trust_revoke_pending"}
                    )
                    self._replace_journal(journal, parent_identity=parent_identity)
                    fault("after_trust_revoke_pending")
                    self.trust_store.revoke_origin(trust_origin)
                journal = journal.model_copy(update={"phase": "install_retire_pending"})
                if trust_origin is not None:
                    fault("after_trust_revoke")
                self._replace_journal(journal, parent_identity=parent_identity)
                if previous is not None:
                    quarantine_envelope = quarantine.parent
                    if _entry(quarantine_envelope) is not None and not (
                        self._remove_owned_envelope(
                            quarantine_envelope,
                            transaction_id=prepared.transaction_id,
                            kind="quarantine",
                            identity=prepared.identity,
                            destination=destination,
                            package_digest=previous.distribution_digest,
                        )
                    ):
                        _fail(
                            "transaction_recovery_ambiguous",
                            "quarantine ownership changed during installation",
                        )
                fault("after_backup_retired")
                staging_envelope = prepared.staging_path.parent
                if _entry(staging_envelope) is not None and not (
                    self._remove_owned_envelope(
                        staging_envelope,
                        transaction_id=prepared.transaction_id,
                        kind="staging",
                        identity=prepared.identity,
                        destination=destination,
                        package_digest=distribution.digest,
                    )
                ):
                    _fail(
                        "transaction_recovery_ambiguous",
                        "staging ownership changed during installation",
                    )
                fault("after_staging_retired")
                self._delete_journal(
                    prepared.transaction_id,
                    parent_identity=parent_identity,
                )
                return candidate_provenance
            except Exception as original:
                if journal.phase == "install_retire_pending":
                    raise WorkflowMarketplaceError(
                        "transaction_rollback_failed",
                        "package installation cleanup requires recovery",
                    ) from original
                rollback_journal = journal.model_copy(
                    update={"phase": "install_rollback_pending"}
                )
                try:
                    self._replace_journal(
                        rollback_journal,
                        parent_identity=parent_identity,
                    )
                    if not self._journal_markers_match(rollback_journal):
                        _fail(
                            "transaction_recovery_ambiguous",
                            "transaction ownership changed during rollback",
                        )
                    self._rollback_install(rollback_journal, previous_snapshot)
                    if not self._finish_install_cleanup(rollback_journal):
                        _fail(
                            "transaction_recovery_ambiguous",
                            "transaction ownership changed during rollback",
                        )
                    self._delete_journal(
                        prepared.transaction_id,
                        parent_identity=parent_identity,
                    )
                except Exception as rollback_error:
                    raise WorkflowMarketplaceError(
                        "transaction_rollback_failed",
                        "package installation rollback requires recovery",
                    ) from rollback_error
                from .lifecycle_state import _record_rollback

                _record_rollback(self, prepared.identity, prepared.installed_provenance)
                raise original

    def _rollback_remove(self, journal: _JournalRecord, provenance_snapshot) -> None:
        destination = Path(journal.destination)
        quarantine = Path(journal.quarantine_path)
        previous = journal.previous_provenance
        assert previous is not None
        if _entry(destination) is None:
            load_distribution(quarantine, expected_digest=previous.distribution_digest)
            os.replace(quarantine, destination)
            _fsync_directory(destination.parent)
            _fsync_directory(quarantine.parent)
        else:
            load_distribution(destination, expected_digest=previous.distribution_digest)
        self.installed_store._restore_snapshot(provenance_snapshot)

    def atomic_remove(
        self,
        prepared: PreparedTransaction,
        *,
        review_digest: str,
        trust_origin: str | None = None,
        provenance_remover: Callable[[InstalledPackageIdentity], object] | None = None,
        cancelled: Callable[[], bool] = lambda: False,
        enter_atomic: Callable[[], bool] = lambda: True,
        fault: Callable[[str], None] = lambda _point: None,
    ) -> InstalledPackageProvenance:
        """Remove one package authorized by an exact consumed confirmation."""

        prepared = self._require_authorization(prepared, "remove")
        trust_origin = _validated_trust_origin(prepared.identity, trust_origin)
        root_identity = self._ensure_workflow_roots()
        destination = prepared.destination
        remover = provenance_remover or self.installed_store.remove
        with self._locked(root_identity) as parent_identity:
            consumed = self._require_consumed_journal(prepared, "remove")
            _distribution, previous = self._revalidate_remove_prepared(
                prepared,
                review_digest=review_digest,
            )
            self._validate_destination_parent(destination)
            previous_snapshot = self.installed_store._snapshot()
            if self._current_provenance(prepared.identity) != previous:
                _fail(
                    "installed_package_changed",
                    "installed package changed before removal",
                )
            quarantine = Path(consumed.quarantine_path)
            journal = consumed.model_copy(
                update={
                    "phase": "remove_backup_move_pending",
                    "trust_origin": trust_origin,
                }
            )
            try:
                if cancelled() or not enter_atomic():
                    _fail(
                        "marketplace_operation_cancelled",
                        "marketplace operation was cancelled",
                    )
                fault("before_initial_journal")
                self._replace_journal(journal, parent_identity=parent_identity)
                quarantine = self._prepare_quarantine(
                    transaction_id=prepared.transaction_id,
                    identity=prepared.identity,
                    destination=destination,
                    digest=previous.distribution_digest,
                    created_at=prepared.created_at,
                )
                fault("after_initial_journal")
                os.replace(destination, quarantine)
                _fsync_directory(destination.parent)
                _fsync_directory(quarantine.parent)
                fault("after_backup_move")
                journal = journal.model_copy(
                    update={"phase": "remove_provenance_write_pending"}
                )
                self._replace_journal(journal, parent_identity=parent_identity)
                remover(prepared.identity)
                if self._current_provenance(prepared.identity) is not None:
                    _fail(
                        "provenance_state_write_failed",
                        "provenance remover did not remove installed identity",
                    )
                fault("after_provenance_remove")
                if trust_origin is not None:
                    journal = journal.model_copy(
                        update={"phase": "remove_trust_revoke_pending"}
                    )
                    self._replace_journal(journal, parent_identity=parent_identity)
                    fault("after_trust_revoke_pending")
                    self.trust_store.revoke_origin(trust_origin)
                journal = journal.model_copy(update={"phase": "remove_retire_pending"})
                if trust_origin is not None:
                    fault("after_trust_revoke")
                self._replace_journal(journal, parent_identity=parent_identity)
                quarantine_envelope = quarantine.parent
                if _entry(quarantine_envelope) is not None and not (
                    self._remove_owned_envelope(
                        quarantine_envelope,
                        transaction_id=prepared.transaction_id,
                        kind="quarantine",
                        identity=prepared.identity,
                        destination=destination,
                        package_digest=previous.distribution_digest,
                    )
                ):
                    _fail(
                        "transaction_recovery_ambiguous",
                        "quarantine ownership changed during removal",
                    )
                fault("after_backup_retired")
                self._delete_journal(
                    prepared.transaction_id,
                    parent_identity=parent_identity,
                )
                return previous
            except Exception as original:
                if journal.phase == "remove_retire_pending":
                    raise WorkflowMarketplaceError(
                        "transaction_rollback_failed",
                        "package removal cleanup requires recovery",
                    ) from original
                rollback_journal = journal.model_copy(
                    update={"phase": "remove_rollback_pending"}
                )
                try:
                    self._replace_journal(
                        rollback_journal,
                        parent_identity=parent_identity,
                    )
                    if not self._journal_markers_match(rollback_journal):
                        _fail(
                            "transaction_recovery_ambiguous",
                            "transaction ownership changed during rollback",
                        )
                    self._rollback_remove(rollback_journal, previous_snapshot)
                    if _entry(quarantine.parent) is not None and not (
                        self._remove_owned_envelope(
                            quarantine.parent,
                            transaction_id=prepared.transaction_id,
                            kind="quarantine",
                            identity=prepared.identity,
                            destination=destination,
                            package_digest=previous.distribution_digest,
                        )
                    ):
                        _fail(
                            "transaction_recovery_ambiguous",
                            "transaction ownership changed during rollback",
                        )
                    self._delete_journal(
                        prepared.transaction_id,
                        parent_identity=parent_identity,
                    )
                except Exception as rollback_error:
                    raise WorkflowMarketplaceError(
                        "transaction_rollback_failed",
                        "package removal rollback requires recovery",
                    ) from rollback_error
                from .lifecycle_state import _record_rollback

                _record_rollback(self, prepared.identity, prepared.installed_provenance)
                raise original

    def _journal_markers_match(self, journal: _JournalRecord) -> bool:
        candidate = journal.candidate_provenance
        if journal.operation == "install":
            assert candidate is not None
            staging_envelope = Path(journal.staging_path).parent
            if _entry(staging_envelope) is None:
                if journal.phase != "install_retire_pending":
                    return False
            elif (
                self._read_marker(
                    staging_envelope,
                    transaction_id=journal.transaction_id,
                    kind="staging",
                    identity=journal.identity,
                    destination=Path(journal.destination),
                    package_digest=candidate.distribution_digest,
                )
                is None
            ):
                return False
        previous = journal.previous_provenance
        if previous is not None:
            quarantine_envelope = Path(journal.quarantine_path).parent
            if (
                _entry(quarantine_envelope) is not None
                and self._read_marker(
                    quarantine_envelope,
                    transaction_id=journal.transaction_id,
                    kind="quarantine",
                    identity=journal.identity,
                    destination=Path(journal.destination),
                    package_digest=previous.distribution_digest,
                )
                is None
            ):
                return False
        return True

    def _recover_install(self, journal: _JournalRecord) -> bool:
        candidate = journal.candidate_provenance
        assert candidate is not None
        destination = Path(journal.destination)
        staging = Path(journal.staging_path)
        quarantine = Path(journal.quarantine_path)
        current = self._current_provenance(journal.identity)
        destination_digest: str | None = None
        if _entry(destination) is not None:
            try:
                destination_digest = load_distribution(destination).digest
            except WorkflowMarketplaceError:
                return False
        committed = (
            journal.phase != "install_rollback_pending"
            and current == candidate
            and destination_digest == candidate.distribution_digest
        )
        if committed:
            if journal.trust_origin is not None:
                self.trust_store.revoke_origin(journal.trust_origin)
            return self._finish_install_cleanup(journal)
        previous = journal.previous_provenance
        if previous is None:
            if destination_digest == candidate.distribution_digest:
                if _entry(staging) is not None:
                    return False
                os.replace(destination, staging)
                _fsync_directory(destination.parent)
                _fsync_directory(staging.parent)
            elif destination_digest is not None:
                return False
            if current is not None:
                self.installed_store.remove(journal.identity, expected=current)
        else:
            if destination_digest == candidate.distribution_digest:
                if _entry(staging) is not None:
                    return False
                os.replace(destination, staging)
                _fsync_directory(destination.parent)
                _fsync_directory(staging.parent)
                destination_digest = None
            if destination_digest is None:
                try:
                    load_distribution(
                        quarantine, expected_digest=previous.distribution_digest
                    )
                except WorkflowMarketplaceError:
                    return False
                os.replace(quarantine, destination)
                _fsync_directory(destination.parent)
                _fsync_directory(quarantine.parent)
            elif destination_digest != previous.distribution_digest:
                return False
            self.installed_store.put(previous)
        return self._finish_install_cleanup(journal)

    def _recover_remove(self, journal: _JournalRecord) -> bool:
        previous = journal.previous_provenance
        assert previous is not None
        destination = Path(journal.destination)
        quarantine = Path(journal.quarantine_path)
        current = self._current_provenance(journal.identity)
        if (
            journal.phase != "remove_rollback_pending"
            and current is None
            and _entry(destination) is None
        ):
            if journal.trust_origin is not None:
                self.trust_store.revoke_origin(journal.trust_origin)
            return _entry(quarantine.parent) is None or self._remove_owned_envelope(
                quarantine.parent,
                transaction_id=journal.transaction_id,
                kind="quarantine",
                identity=journal.identity,
                destination=destination,
                package_digest=previous.distribution_digest,
            )
        if _entry(destination) is None:
            try:
                load_distribution(
                    quarantine, expected_digest=previous.distribution_digest
                )
            except WorkflowMarketplaceError:
                return False
            os.replace(quarantine, destination)
            _fsync_directory(destination.parent)
            _fsync_directory(quarantine.parent)
        else:
            try:
                load_distribution(
                    destination, expected_digest=previous.distribution_digest
                )
            except WorkflowMarketplaceError:
                return False
        self.installed_store.put(previous)
        return _entry(quarantine.parent) is None or self._remove_owned_envelope(
            quarantine.parent,
            transaction_id=journal.transaction_id,
            kind="quarantine",
            identity=journal.identity,
            destination=destination,
            package_digest=previous.distribution_digest,
        )

    def _abandoned_staging_candidates(
        self,
        *,
        active_ids: set[str],
        journal_ids: set[str],
        require_complete: bool = False,
    ) -> Iterator[tuple[Path, _OwnerMarker]]:
        if require_complete and _entry(self.staging_root) is None:
            return
        try:
            with os.scandir(self.staging_root) as iterator:
                entries = list(islice(iterator, _MAX_PREPARED + _MAX_JOURNALS + 1))
        except OSError:
            if require_complete:
                _fail(
                    "transaction_recovery_inspection_incomplete",
                    "staging scope could not be inspected completely",
                )
            return
        if require_complete and len(entries) > _MAX_PREPARED + _MAX_JOURNALS:
            _fail(
                "transaction_recovery_inspection_incomplete",
                "staging scope exceeds the inspection limit",
            )
        for entry in entries:
            if (
                entry.name in active_ids
                or entry.name in journal_ids
                or _TRANSACTION_ID.fullmatch(entry.name) is None
            ):
                continue
            envelope = Path(entry.path)
            if require_complete:
                _require_existing_directory(
                    envelope, code="transaction_recovery_inspection_incomplete"
                )
            elif not entry.is_dir(follow_symlinks=False):
                continue
            path = envelope / "owner.json"
            try:
                value = _strict_json(
                    _read_bounded(
                        path,
                        limit=_MAX_MARKER_BYTES,
                        size_code="transaction_marker_size_limit",
                    ),
                    code="transaction_marker_invalid",
                )
                if (
                    require_complete
                    and isinstance(value, dict)
                    and value.get("owner") != _OWNER
                ):
                    owner = value.get("owner")
                    if (
                        not isinstance(owner, str)
                        or not 1 <= len(owner) <= 128
                        or owner != owner.strip()
                        or not owner.isprintable()
                    ):
                        _fail(
                            "transaction_recovery_inspection_incomplete",
                            "abandoned transaction ownership could not be verified",
                        )
                    # Validate every other field with the unchanged strict schema.
                    # This classifies explicit foreign data only; it never yields
                    # a cleanup candidate or grants the normalized value authority.
                    _OwnerMarker.model_validate({**value, "owner": _OWNER})
                    continue
                marker = _OwnerMarker.model_validate(value)
            except (ValidationError, WorkflowMarketplaceError):
                if require_complete:
                    _fail(
                        "transaction_recovery_inspection_incomplete",
                        "abandoned transaction ownership could not be verified",
                    )
                continue
            if (
                marker.owner == _OWNER
                and marker.kind == "staging"
                and marker.transaction_id == entry.name
                and marker.destination
                == str(self.installed_store.package_root(marker.identity))
                and self._read_marker(
                    envelope,
                    transaction_id=marker.transaction_id,
                    kind="staging",
                    identity=marker.identity,
                    destination=Path(marker.destination),
                    package_digest=marker.package_digest,
                )
                is not None
            ):
                yield envelope, marker
            elif require_complete:
                _fail(
                    "transaction_recovery_inspection_incomplete",
                    "abandoned transaction ownership could not be verified",
                )

    def _clean_abandoned_staging(
        self,
        *,
        active_ids: set[str],
        journal_ids: set[str],
    ) -> None:
        for envelope, _marker in self._abandoned_staging_candidates(
            active_ids=active_ids, journal_ids=journal_ids
        ):
            shutil.rmtree(envelope)
            _fsync_directory(self.staging_root)

    def inspect_recovery(self) -> _RecoveryInspection:
        """Read bounded recovery scope; incomplete inspection raises, never clears.

        Lock artifacts may be created by the existing lock primitive. No prepared
        records, journals, owned envelopes, packages or trust are changed here.
        Callers recheck this snapshot and recover under the same reentrant lock.
        """
        with self._locked():
            # Validate parents before any journal or abandoned-staging marker read.
            for root in (
                self.home,
                self.home / "workflows",
                self.staging_root,
                self.quarantine_root,
            ):
                if _entry(root) is not None:
                    _require_existing_directory(
                        root, code="transaction_recovery_inspection_incomplete"
                    )
            prepared = self._read_prepared()
            journals = self._read_journals()
            now = self.clock()
            if not isinstance(now, datetime) or now.tzinfo is None:
                _fail(
                    "transaction_state_invalid",
                    "transaction clock did not return an aware timestamp",
                )
            active_ids = {
                item.transaction_id
                for item in prepared.transactions
                if _parse_timestamp(item.expires_at) > now
            }
            abandoned = tuple(
                self._abandoned_staging_candidates(
                    active_ids=active_ids,
                    journal_ids={item.transaction_id for item in journals.journals},
                    require_complete=True,
                )
            )
            entries = [
                _RecoveryEntry(
                    item.transaction_id, item.identity, "expired_preparation", "owned"
                )
                for item in prepared.transactions
                if item.transaction_id not in active_ids
            ]
            active_writer = False
            for journal in journals.journals:
                writer = (
                    journal.phase in {"install_consumed", "remove_consumed"}
                    and _parse_timestamp(journal.consumed_expires_at) > now
                )
                active_writer |= writer
                if writer:
                    classification = "active_writer"
                elif journal.transaction_id in active_ids:
                    classification = "active_preparation"
                elif self._journal_markers_match(journal):
                    classification = "owned"
                else:
                    classification = "ownership_mismatch"
                entries.append(
                    _RecoveryEntry(
                        journal.transaction_id,
                        journal.identity,
                        "journal",
                        classification,
                        journal.phase,
                    )
                )
            entries.extend(
                _RecoveryEntry(
                    marker.transaction_id, marker.identity, "abandoned_staging", "owned"
                )
                for _envelope, marker in abandoned
            )
            return _RecoveryInspection(
                tuple(
                    sorted(entries, key=lambda item: (item.transaction_id, item.kind))
                ),
                active_writer,
                bool(active_ids),
            )

    def recover_transactions(self) -> tuple[str, ...]:
        """Complete or roll back only exact journals with matching owned markers."""

        root_identity = self._ensure_workflow_roots()
        recovered: list[str] = []
        with self._locked(root_identity) as parent_identity:
            prepared = self._read_prepared()
            journals = self._read_journals()
            now = self.clock()
            if not isinstance(now, datetime) or now.tzinfo is None:
                _fail(
                    "transaction_state_invalid",
                    "transaction clock did not return an aware timestamp",
                )
            active_transactions = [
                item
                for item in prepared.transactions
                if _parse_timestamp(item.expires_at) > now
            ]
            if active_transactions != prepared.transactions:
                self._write_prepared(
                    active_transactions,
                    parent_identity=parent_identity,
                )
            active_ids = {item.transaction_id for item in active_transactions}
            remaining: list[_JournalRecord] = []
            for journal in journals.journals:
                if journal.transaction_id in active_ids:
                    remaining.append(journal)
                    continue
                if (
                    journal.phase in {"install_consumed", "remove_consumed"}
                    and _parse_timestamp(journal.consumed_expires_at) > now
                ):
                    remaining.append(journal)
                    continue
                if not self._journal_markers_match(journal):
                    remaining.append(journal)
                    continue
                try:
                    if journal.phase == "install_consumed":
                        completed = self._finish_install_cleanup(journal)
                    elif journal.phase == "remove_consumed":
                        completed = True
                    elif journal.operation == "install":
                        completed = self._recover_install(journal)
                    else:
                        completed = self._recover_remove(journal)
                except (OSError, WorkflowMarketplaceError, WorkflowTrustError):
                    completed = False
                if completed:
                    recovered.append(journal.transaction_id)
                else:
                    remaining.append(journal)
            if remaining != journals.journals:
                self._write_journals(remaining, parent_identity=parent_identity)
            self._clean_abandoned_staging(
                active_ids=active_ids,
                journal_ids={item.transaction_id for item in remaining},
            )
        return tuple(recovered)


def prepare(candidate: TransactionCandidate, **kwargs) -> PreparedTransaction:
    return MarketplaceTransactionStore().prepare(candidate, **kwargs)


def consume(raw_token: str, **kwargs) -> PreparedTransaction:
    return MarketplaceTransactionStore().consume(raw_token, **kwargs)


def atomic_install(
    prepared: PreparedTransaction, **kwargs
) -> InstalledPackageProvenance:
    return MarketplaceTransactionStore().atomic_install(prepared, **kwargs)


def atomic_remove(
    prepared: PreparedTransaction, **kwargs
) -> InstalledPackageProvenance:
    return MarketplaceTransactionStore().atomic_remove(prepared, **kwargs)


def recover_transactions() -> tuple[str, ...]:
    return MarketplaceTransactionStore().recover_transactions()


__all__ = [
    "MarketplaceTransactionStore",
    "PreparedTransaction",
    "PreparedTransactionMetadata",
    "TransactionCandidate",
    "TransactionJournal",
    "atomic_install",
    "atomic_remove",
    "consume",
    "prepare",
    "recover_transactions",
]
