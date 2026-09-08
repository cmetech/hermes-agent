"""Strict installed-package provenance and discovery bindings."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Iterator, NoReturn
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hermes_cli.git_source import (
    GitSourceError,
    canonical_git_source,
    resolve_git_source,
    validate_credential_free_git_source,
)
from hermes_constants import get_hermes_home
from plugins.workflow.locks import WorkflowLockTimeout
from plugins.workflow.models import WorkflowMarketplaceBinding
from utils import _is_reparse_point, atomic_write_text

from .models import InstalledPackageIdentity, InstalledPackageProvenance
from .package import WorkflowMarketplaceError, load_distribution
from .source_store import (
    WorkflowSourceStore,
    _path_entry_exists,
    _read_bounded,
    _strict_json,
)


_STATE_VERSION = 1
_MAX_STATE_BYTES = 8 * 1024 * 1024
_MAX_INSTALLED_PACKAGES = 4096
_SOURCE_KEY = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,126}[a-z0-9])?$", re.ASCII)
_DIRECT_SOURCE_DOMAIN = b"hermes.workflow-marketplace.direct-source.v1\0"


class _StateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _InstalledRecord(_StateModel):
    destination: str = Field(min_length=1, max_length=4096)
    provenance: InstalledPackageProvenance


class _InstalledState(_StateModel):
    schema_version: int = Field(alias="schemaVersion")
    packages: list[_InstalledRecord] = Field(max_length=_MAX_INSTALLED_PACKAGES)

    @model_validator(mode="after")
    def require_version_and_order(self) -> "_InstalledState":
        if self.schema_version != _STATE_VERSION:
            raise ValueError("installed provenance schema version is unsupported")
        identities = [
            (item.provenance.identity.source_key, item.provenance.identity.package_id)
            for item in self.packages
        ]
        if identities != sorted(identities) or len(identities) != len(set(identities)):
            raise ValueError(
                "installed provenance identities must be unique and sorted"
            )
        if len({item.destination for item in self.packages}) != len(self.packages):
            raise ValueError("installed provenance destinations must be unique")
        return self


def _fail(code: str, message: str) -> NoReturn:
    raise WorkflowMarketplaceError(code, message)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


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


def _validate_source_key(value: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or unicodedata.normalize("NFC", value) != value
        or _SOURCE_KEY.fullmatch(value) is None
    ):
        _fail(
            "installed_identity_invalid",
            "installed marketplace source key is invalid",
        )
    return value


def _validate_identity(identity: InstalledPackageIdentity) -> InstalledPackageIdentity:
    if not isinstance(identity, InstalledPackageIdentity):
        _fail("installed_identity_invalid", "installed package identity is invalid")
    _validate_source_key(identity.source_key)
    return identity


def _canonical_timestamp(value: str) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        return False
    return (
        parsed.tzinfo is not None
        and parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") == value
    )


def direct_source_key(repository_url: str) -> str:
    """Return a stable credential-free key for one normalized direct source."""

    if not isinstance(repository_url, str):
        _fail("source_invalid", "direct marketplace source identity is invalid")
    try:
        validate_credential_free_git_source(repository_url)
        resolved = resolve_git_source(repository_url)
        identity = canonical_git_source(resolved.clone_url, resolved.subdirectory)
        validate_credential_free_git_source(identity)
    except GitSourceError as error:
        code = (
            "source_credentials_forbidden"
            if "credentials" in str(error).casefold()
            else "source_invalid"
        )
        _fail(code, "direct marketplace source identity is invalid")
    digest = hashlib.sha256(_DIRECT_SOURCE_DOMAIN + identity.encode("utf-8"))
    return f"direct-{digest.hexdigest()[:32]}"


class InstalledPackageStore:
    """Own exact installed-package provenance for one Hermes profile."""

    max_state_bytes = _MAX_STATE_BYTES

    def __init__(
        self,
        hermes_home: Path | None = None,
        *,
        lock_timeout_seconds: float = 5.0,
    ):
        self.home = _absolute(
            Path(hermes_home) if hermes_home is not None else get_hermes_home()
        )
        shared = WorkflowSourceStore(self.home)
        self.root = shared.root
        self.path = self.root / "installed.json"
        self.lock_path = shared.lock_path
        self._shared_store = shared
        self.lock_timeout_seconds = lock_timeout_seconds

    def package_root(self, identity: InstalledPackageIdentity) -> Path:
        checked = _validate_identity(identity)
        return (
            self.home
            / "workflows"
            / "marketplace"
            / checked.source_key
            / checked.package_id
        )

    @contextmanager
    def _locked(self) -> Iterator[tuple[int, int]]:
        try:
            root_identity = self._shared_store._ensure_private_root()
            from plugins.workflow.locks import workflow_lock

            with workflow_lock(
                self.lock_path,
                timeout_seconds=self.lock_timeout_seconds,
            ):
                try:
                    metadata = self.root.lstat()
                except OSError:
                    _fail(
                        "provenance_state_invalid",
                        "installed provenance state directory changed",
                    )
                if (
                    stat.S_ISLNK(metadata.st_mode)
                    or _is_reparse_point(metadata)
                    or not stat.S_ISDIR(metadata.st_mode)
                    or (metadata.st_dev, metadata.st_ino) != root_identity
                ):
                    _fail(
                        "provenance_state_invalid",
                        "installed provenance state directory changed",
                    )
                yield root_identity
        except WorkflowLockTimeout as error:
            _fail("provenance_lock_timeout", str(error))
        except WorkflowMarketplaceError as error:
            if error.code.startswith("source_state"):
                _fail(
                    "provenance_state_invalid",
                    "installed provenance state directory is invalid",
                )
            raise

    def _empty_state(self) -> _InstalledState:
        return _InstalledState.model_validate({
            "schemaVersion": _STATE_VERSION,
            "packages": [],
        })

    def _read_state(self) -> _InstalledState:
        if not _path_entry_exists(self.path):
            return self._empty_state()
        raw = _read_bounded(
            self.path,
            limit=self.max_state_bytes,
            size_code="provenance_state_size_limit",
        )
        value = _strict_json(raw, code="provenance_state_invalid")
        try:
            state = _InstalledState.model_validate(value)
        except ValidationError:
            _fail(
                "provenance_state_invalid",
                "persisted installed provenance is invalid",
            )
        for record in state.packages:
            provenance = record.provenance
            try:
                _validate_identity(provenance.identity)
            except WorkflowMarketplaceError:
                _fail(
                    "provenance_state_invalid",
                    "persisted installed provenance identity is invalid",
                )
            if record.destination != str(
                self.package_root(provenance.identity)
            ) or not _canonical_timestamp(provenance.installed_at):
                _fail(
                    "provenance_state_invalid",
                    "persisted installed provenance destination is invalid",
                )
        return state

    def _render_state(self, state: _InstalledState) -> str:
        rendered = (
            json.dumps(
                state.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        if len(rendered.encode("utf-8")) > self.max_state_bytes:
            _fail(
                "provenance_state_size_limit",
                "persisted installed provenance exceeds its byte limit",
            )
        return rendered

    def _write_state(
        self,
        state: _InstalledState,
        *,
        parent_identity: tuple[int, int],
    ) -> None:
        try:
            atomic_write_text(
                self.path,
                self._render_state(state),
                tmp_prefix="installed.json.tmp-",
                create_mode=0o600,
                no_follow=True,
                expected_parent_identity=parent_identity,
            )
        except OSError as error:
            raise WorkflowMarketplaceError(
                "provenance_state_write_failed",
                "could not atomically replace installed provenance",
            ) from error

    def list_installed(self) -> tuple[InstalledPackageProvenance, ...]:
        if not _path_entry_exists(self.path):
            return ()
        with self._locked():
            return tuple(item.provenance for item in self._read_state().packages)

    def get(self, identity: InstalledPackageIdentity) -> InstalledPackageProvenance:
        checked = _validate_identity(identity)
        for installed in self.list_installed():
            if installed.identity == checked:
                return installed
        _fail(
            "installed_package_not_found",
            "installed workflow marketplace package was not found",
        )

    def put(
        self,
        provenance: InstalledPackageProvenance,
    ) -> InstalledPackageProvenance:
        if not isinstance(provenance, InstalledPackageProvenance):
            _fail("provenance_invalid", "installed package provenance is invalid")
        _validate_identity(provenance.identity)
        if not _canonical_timestamp(provenance.installed_at):
            _fail("provenance_invalid", "installation timestamp is invalid")
        with self._locked() as parent_identity:
            state = self._read_state()
            record = _InstalledRecord(
                destination=str(self.package_root(provenance.identity)),
                provenance=provenance,
            )
            records = [
                item
                for item in state.packages
                if item.provenance.identity != provenance.identity
            ]
            records.append(record)
            records.sort(
                key=lambda item: (
                    item.provenance.identity.source_key,
                    item.provenance.identity.package_id,
                )
            )
            if len(records) > _MAX_INSTALLED_PACKAGES:
                _fail("provenance_state_size_limit", "too many installed packages")
            self._write_state(
                _InstalledState.model_validate({
                    "schemaVersion": _STATE_VERSION,
                    "packages": records,
                }),
                parent_identity=parent_identity,
            )
        return provenance

    def remove(
        self,
        identity: InstalledPackageIdentity,
        *,
        expected: InstalledPackageProvenance | None = None,
    ) -> InstalledPackageProvenance:
        checked = _validate_identity(identity)
        with self._locked() as parent_identity:
            state = self._read_state()
            installed = next(
                (
                    item.provenance
                    for item in state.packages
                    if item.provenance.identity == checked
                ),
                None,
            )
            if installed is None:
                _fail(
                    "installed_package_not_found",
                    "installed workflow marketplace package was not found",
                )
            if expected is not None and installed != expected:
                _fail(
                    "installed_package_changed",
                    "installed package provenance changed before mutation",
                )
            self._write_state(
                _InstalledState.model_validate({
                    "schemaVersion": _STATE_VERSION,
                    "packages": [
                        item
                        for item in state.packages
                        if item.provenance.identity != checked
                    ],
                }),
                parent_identity=parent_identity,
            )
            return installed

    def _snapshot(self) -> tuple[bool, _InstalledState]:
        return _path_entry_exists(self.path), self._read_state()

    def _restore_snapshot(self, snapshot: tuple[bool, _InstalledState]) -> None:
        existed, state = snapshot
        with self._locked() as parent_identity:
            if existed:
                self._write_state(state, parent_identity=parent_identity)
                return
            if not _path_entry_exists(self.path):
                return
            try:
                metadata = self.path.lstat()
                if (
                    stat.S_ISLNK(metadata.st_mode)
                    or _is_reparse_point(metadata)
                    or not stat.S_ISREG(metadata.st_mode)
                ):
                    _fail(
                        "provenance_state_invalid",
                        "installed provenance state changed during rollback",
                    )
                self.path.unlink()
                _fsync_directory(self.root)
            except WorkflowMarketplaceError:
                raise
            except OSError as error:
                raise WorkflowMarketplaceError(
                    "provenance_state_write_failed",
                    "could not restore absent installed provenance state",
                ) from error

    def binding_for_workflow(
        self,
        root: Path,
        relative_path: str,
    ) -> WorkflowMarketplaceBinding | None:
        """Return trust material only for exact, freshly verified installed bytes."""

        try:
            if not isinstance(relative_path, str):
                return None
            candidate_root = _absolute(Path(root))
            if not _path_entry_exists(self.path):
                return None
            with self._locked():
                state = self._read_state()
                record = next(
                    (
                        item
                        for item in state.packages
                        if item.destination == str(candidate_root)
                    ),
                    None,
                )
                if record is None:
                    return None
                provenance = record.provenance
                if (
                    candidate_root != self.package_root(provenance.identity)
                    or relative_path not in provenance.workflow_paths
                ):
                    return None
                distribution = load_distribution(
                    candidate_root,
                    expected_digest=provenance.distribution_digest,
                )
                declared = {
                    member.definition for member in distribution.manifest.workflows
                }
                if (
                    distribution.manifest.id != provenance.identity.package_id
                    or distribution.manifest.version != provenance.package_version
                    or relative_path not in declared
                ):
                    return None
                return WorkflowMarketplaceBinding(
                    installation_key=(
                        f"{provenance.identity.source_key}/"
                        f"{provenance.identity.package_id}"
                    ),
                    source_name=provenance.source_name,
                    package_id=provenance.identity.package_id,
                    package_version=provenance.package_version,
                    workflow_relative_path=relative_path,
                    distribution_digest=provenance.distribution_digest,
                )
        except (OSError, TypeError, WorkflowMarketplaceError, ValueError):
            return None


__all__ = ["InstalledPackageStore", "direct_source_key"]
