"""Profile-local workflow marketplace sources and verified catalog state."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Literal, NoReturn
import unicodedata
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hermes_cli.git_source import (
    GitSourceError,
    canonical_git_source,
    resolve_git_source,
    safe_git_error,
)
from hermes_constants import get_hermes_home
from plugins.workflow.locks import WorkflowLockTimeout, workflow_lock
from utils import atomic_write_text

from .models import (
    WorkflowMarketplaceSource,
    WorkflowPackageIndex,
    WorkflowPackageIndexEntry,
)
from .package import WorkflowMarketplaceError


_SOURCE_STATE_VERSION = 1
_CATALOG_STATE_VERSION = 1
_MAX_SOURCES = 128
_MAX_SOURCE_STATE_BYTES = 1024 * 1024
_MAX_CATALOG_STATE_BYTES = 64 * 1024 * 1024
_MAX_ERROR_BYTES = 4096
_SOURCE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")

RefreshState = Literal[
    "fresh",
    "stale",
    "disabled",
    "authentication-failed",
    "malformed",
    "incompatible",
    "unavailable",
    "cancelled",
]


def _has_forbidden_repository_credentials(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return True
    return parsed.password is not None or (
        parsed.scheme.casefold() not in {"http", "https"} and bool(parsed.query)
    )


class _StateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _SourceState(_StateModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    sources: list[WorkflowMarketplaceSource] = Field(max_length=_MAX_SOURCES)

    @model_validator(mode="after")
    def require_unique_sorted_sources(self) -> "_SourceState":
        names = [source.name for source in self.sources]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("sources must be unique and sorted by name")
        if any(
            _has_forbidden_repository_credentials(source.repository_url)
            for source in self.sources
        ):
            raise ValueError(
                "source repository identities must not contain credentials"
            )
        return self


class VerifiedSourceCatalog(_StateModel):
    """One independently verified repository index at an exact commit."""

    source: WorkflowMarketplaceSource
    resolved_commit: str = Field(alias="resolvedCommit", pattern=r"^[0-9a-f]{40}$")
    verified_at: str = Field(alias="verifiedAt", min_length=20, max_length=64)
    packages: list[WorkflowPackageIndexEntry] = Field(max_length=4096)

    @model_validator(mode="after")
    def require_valid_index_projection(self) -> "VerifiedSourceCatalog":
        if _has_forbidden_repository_credentials(self.source.repository_url):
            raise ValueError("source repository identity must not contain credentials")
        WorkflowPackageIndex.model_validate({
            "schemaVersion": 1,
            "packages": [
                package.model_dump(mode="json", by_alias=True)
                for package in self.packages
            ],
        })
        return self


class SourceRefreshStatus(_StateModel):
    """Credential-free refresh status retained separately from verified bytes."""

    source_name: str = Field(alias="sourceName", min_length=1, max_length=64)
    state: RefreshState
    attempted_at: str = Field(alias="attemptedAt", min_length=20, max_length=64)
    diagnostic_code: str | None = Field(
        default=None, alias="diagnosticCode", max_length=128
    )
    message: str | None = Field(default=None, max_length=_MAX_ERROR_BYTES)


class _CatalogState(_StateModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    verified: list[VerifiedSourceCatalog] = Field(max_length=_MAX_SOURCES)
    statuses: list[SourceRefreshStatus] = Field(max_length=_MAX_SOURCES)

    @model_validator(mode="after")
    def require_unique_sorted_entries(self) -> "_CatalogState":
        verified_names = [item.source.name for item in self.verified]
        status_names = [item.source_name for item in self.statuses]
        if (
            verified_names != sorted(verified_names)
            or len(verified_names) != len(set(verified_names))
            or status_names != sorted(status_names)
            or len(status_names) != len(set(status_names))
        ):
            raise ValueError("catalog entries must be unique and sorted by source")
        return self


def _fail(code: str, message: str) -> NoReturn:
    raise WorkflowMarketplaceError(code, message)


def _normalize_name(name: str) -> str:
    if not isinstance(name, str):
        _fail("source_name_invalid", "source name must be text")
    normalized = unicodedata.normalize("NFKC", name.strip()).casefold()
    if _SOURCE_NAME.fullmatch(normalized) is None:
        _fail(
            "source_name_invalid",
            "source name must use 1-64 lowercase letters, digits, '_' or '-'",
        )
    return normalized


def _reject_non_http_url_credentials(value: str) -> None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return
    if parsed.scheme.casefold() in {"http", "https"}:
        return
    if parsed.password is not None or parsed.query:
        _fail(
            "source_credentials_forbidden",
            "marketplace source URLs must not contain credentials",
        )


def _canonical_source(
    name: str,
    repository_url: str,
    *,
    ref: str | None,
    enabled: bool,
) -> WorkflowMarketplaceSource:
    normalized_name = _normalize_name(name)
    raw = {
        "name": normalized_name,
        "repositoryUrl": repository_url,
        "ref": ref,
        "enabled": enabled,
    }
    try:
        WorkflowMarketplaceSource.model_validate(raw)
    except ValidationError as error:
        if "credentials" in str(error).casefold():
            _fail(
                "source_credentials_forbidden",
                "marketplace source URLs must not contain credentials",
            )
        _fail("source_invalid", "marketplace source configuration is invalid")
    _reject_non_http_url_credentials(repository_url)
    try:
        resolved = resolve_git_source(repository_url)
    except GitSourceError as error:
        _fail("source_invalid", str(error))
    identity = canonical_git_source(resolved.clone_url, resolved.subdirectory)
    try:
        return WorkflowMarketplaceSource.model_validate({
            **raw,
            "repositoryUrl": identity,
        })
    except (
        ValidationError
    ) as error:  # defensive: the shared canonical form is untrusted
        if "credentials" in str(error).casefold():
            _fail(
                "source_credentials_forbidden",
                "marketplace source URLs must not contain credentials",
            )
        _fail("source_invalid", "marketplace source configuration is invalid")


def _strict_json(raw: bytes, *, code: str) -> object:
    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    try:
        return json.loads(
            raw,
            object_pairs_hook=reject_duplicate,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail(code, "persisted marketplace state is malformed")


def _read_bounded(path: Path, *, limit: int, size_code: str) -> bytes:
    invalid_code = size_code.replace("size_limit", "invalid")
    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            _fail(invalid_code, "state file must not be a symbolic link")
    except FileNotFoundError:
        _fail(invalid_code, "state file disappeared while being read")
    except OSError:
        _fail(invalid_code, "state file is unreadable")
    flags = os.O_RDONLY
    for option in ("O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW", "O_BINARY"):
        flags |= getattr(os, option, 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail(invalid_code, "state file is unreadable")
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            _fail(invalid_code, "state is not a file")
        if metadata.st_size > limit:
            _fail(size_code, "persisted marketplace state exceeds its byte limit")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            _fail(size_code, "persisted marketplace state exceeds its byte limit")
        return raw
    finally:
        os.close(descriptor)


def _render_state(value: BaseModel) -> str:
    return (
        json.dumps(
            value.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _redacted_error(error: WorkflowMarketplaceError, repository_url: str) -> str:
    completed = subprocess.CompletedProcess(
        args=(), returncode=1, stdout="", stderr=str(error)
    )
    rendered = safe_git_error(completed, repository_url).strip()
    if not rendered:
        rendered = "marketplace source refresh failed"
    encoded = rendered.encode("utf-8")[:_MAX_ERROR_BYTES]
    return encoded.decode("utf-8", errors="ignore")


class WorkflowSourceStore:
    """Own profile-local source definitions and verified catalog cache."""

    max_source_state_bytes = _MAX_SOURCE_STATE_BYTES
    max_catalog_state_bytes = _MAX_CATALOG_STATE_BYTES

    def __init__(self, hermes_home: Path | None = None):
        home = Path(hermes_home) if hermes_home is not None else get_hermes_home()
        self.root = home / "marketplace" / "workflows"
        self.path = self.root / "sources.json"
        self.catalog_path = self.root / "catalog.json"
        self.lock_path = self.root / "marketplace.lock"

    def _ensure_private_root(self) -> None:
        self.root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        for directory in (self.root.parent,):
            try:
                metadata = directory.lstat()
            except OSError:
                _fail("source_state_invalid", "marketplace state directory is invalid")
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                _fail(
                    "source_state_invalid",
                    "marketplace state directory must not be a symbolic link",
                )
        self.root.mkdir(parents=False, exist_ok=True, mode=0o700)
        try:
            metadata = self.root.lstat()
        except OSError:
            _fail("source_state_invalid", "marketplace state directory is invalid")
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            _fail(
                "source_state_invalid",
                "marketplace state directory must not be a symbolic link",
            )
        if os.name != "nt":
            os.chmod(self.root.parent, 0o700)
            os.chmod(self.root, 0o700)
        flags = os.O_CREAT | os.O_APPEND
        for option in ("O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW", "O_BINARY"):
            flags |= getattr(os, option, 0)
        try:
            descriptor = os.open(self.lock_path, flags, 0o600)
        except OSError:
            _fail("source_state_invalid", "marketplace lock file is invalid")
        metadata = os.fstat(descriptor)
        os.close(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            _fail("source_state_invalid", "marketplace lock is not a regular file")
        if os.name != "nt":
            os.chmod(self.lock_path, 0o600)

    def _locked(self):
        self._ensure_private_root()
        return workflow_lock(self.lock_path)

    def _read_sources(self) -> _SourceState:
        if not _path_entry_exists(self.path):
            return _SourceState.model_validate({"schemaVersion": 1, "sources": []})
        raw = _read_bounded(
            self.path,
            limit=_MAX_SOURCE_STATE_BYTES,
            size_code="source_state_size_limit",
        )
        value = _strict_json(raw, code="source_state_invalid")
        try:
            return _SourceState.model_validate(value)
        except ValidationError:
            _fail("source_state_invalid", "persisted source state is invalid")

    def _read_catalog(self) -> _CatalogState:
        if not _path_entry_exists(self.catalog_path):
            return _CatalogState.model_validate({
                "schemaVersion": 1,
                "verified": [],
                "statuses": [],
            })
        raw = _read_bounded(
            self.catalog_path,
            limit=_MAX_CATALOG_STATE_BYTES,
            size_code="catalog_state_size_limit",
        )
        value = _strict_json(raw, code="catalog_state_invalid")
        try:
            return _CatalogState.model_validate(value)
        except ValidationError:
            _fail("catalog_state_invalid", "persisted catalog state is invalid")

    def _write(self, path: Path, value: BaseModel) -> None:
        try:
            atomic_write_text(
                path,
                _render_state(value),
                tmp_prefix=f"{path.name}.tmp-",
                create_mode=0o600,
            )
            if os.name != "nt":
                os.chmod(path, 0o600)
        except OSError:
            code = (
                "source_state_write_failed"
                if path == self.path
                else "catalog_state_write_failed"
            )
            _fail(code, "could not atomically replace marketplace state")

    def _require_current_source(self, expected: WorkflowMarketplaceSource) -> None:
        current = next(
            (
                source
                for source in self._read_sources().sources
                if source.name == expected.name
            ),
            None,
        )
        if current is None or current != expected or not current.enabled:
            _fail(
                "source_changed",
                "marketplace source changed while its refresh was in progress",
            )

    def add(
        self,
        name: str | WorkflowMarketplaceSource,
        repository_url: str | None = None,
        *,
        ref: str | None = None,
        enabled: bool = True,
    ) -> WorkflowMarketplaceSource:
        if isinstance(name, WorkflowMarketplaceSource):
            if repository_url is not None or ref is not None or enabled is not True:
                _fail("source_invalid", "source object cannot be combined with fields")
            source = _canonical_source(
                name.name,
                name.repository_url,
                ref=name.ref,
                enabled=name.enabled,
            )
        else:
            if repository_url is None:
                _fail("source_invalid", "source repository URL is required")
            source = _canonical_source(
                name,
                repository_url,
                ref=ref,
                enabled=enabled,
            )
        try:
            with self._locked():
                state = self._read_sources()
                if any(item.name == source.name for item in state.sources):
                    _fail(
                        "source_already_exists",
                        f"marketplace source {source.name!r} already exists",
                    )
                if len(state.sources) >= _MAX_SOURCES:
                    _fail("source_limit", "marketplace source count limit reached")
                sources = sorted([*state.sources, source], key=lambda item: item.name)
                self._write(
                    self.path,
                    _SourceState.model_validate({
                        "schemaVersion": _SOURCE_STATE_VERSION,
                        "sources": sources,
                    }),
                )
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))
        return source

    def list_sources(self) -> tuple[WorkflowMarketplaceSource, ...]:
        if not _path_entry_exists(self.path):
            return ()
        try:
            with self._locked():
                return tuple(self._read_sources().sources)
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))

    def get(self, name: str) -> WorkflowMarketplaceSource:
        normalized = _normalize_name(name)
        for source in self.list_sources():
            if source.name == normalized:
                return source
        _fail("source_not_found", f"marketplace source {normalized!r} was not found")

    def set_enabled(self, name: str, enabled: bool) -> WorkflowMarketplaceSource:
        normalized = _normalize_name(name)
        if not isinstance(enabled, bool):
            _fail("source_invalid", "source enabled state must be boolean")
        try:
            with self._locked():
                state = self._read_sources()
                existing = next(
                    (source for source in state.sources if source.name == normalized),
                    None,
                )
                if existing is None:
                    _fail(
                        "source_not_found",
                        f"marketplace source {normalized!r} was not found",
                    )
                updated = existing.model_copy(update={"enabled": enabled})
                sources = [
                    updated if source.name == normalized else source
                    for source in state.sources
                ]
                self._write(
                    self.path,
                    _SourceState.model_validate({
                        "schemaVersion": _SOURCE_STATE_VERSION,
                        "sources": sources,
                    }),
                )
                return updated
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))

    def remove(self, name: str) -> WorkflowMarketplaceSource:
        normalized = _normalize_name(name)
        try:
            with self._locked():
                state = self._read_sources()
                catalog = self._read_catalog()
                removed = next(
                    (source for source in state.sources if source.name == normalized),
                    None,
                )
                if removed is None:
                    _fail(
                        "source_not_found",
                        f"marketplace source {normalized!r} was not found",
                    )
                self._write(
                    self.path,
                    _SourceState.model_validate({
                        "schemaVersion": _SOURCE_STATE_VERSION,
                        "sources": [
                            source
                            for source in state.sources
                            if source.name != normalized
                        ],
                    }),
                )
                self._write(
                    self.catalog_path,
                    _CatalogState.model_validate({
                        "schemaVersion": _CATALOG_STATE_VERSION,
                        "verified": [
                            item
                            for item in catalog.verified
                            if item.source.name != normalized
                        ],
                        "statuses": [
                            item
                            for item in catalog.statuses
                            if item.source_name != normalized
                        ],
                    }),
                )
                return removed
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))

    def cached(self, source: WorkflowMarketplaceSource) -> VerifiedSourceCatalog | None:
        if not _path_entry_exists(self.catalog_path):
            return None
        try:
            with self._locked():
                for cached in self._read_catalog().verified:
                    if (
                        cached.source.name == source.name
                        and cached.source.repository_url == source.repository_url
                        and cached.source.ref == source.ref
                    ):
                        return cached
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))
        return None

    def status(self, name: str) -> SourceRefreshStatus | None:
        normalized = _normalize_name(name)
        if not _path_entry_exists(self.catalog_path):
            return None
        try:
            with self._locked():
                return next(
                    (
                        status
                        for status in self._read_catalog().statuses
                        if status.source_name == normalized
                    ),
                    None,
                )
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))

    def verified_catalogs(self) -> tuple[VerifiedSourceCatalog, ...]:
        if not _path_entry_exists(self.catalog_path):
            return ()
        try:
            with self._locked():
                return tuple(self._read_catalog().verified)
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))

    def snapshot(
        self,
    ) -> tuple[
        tuple[WorkflowMarketplaceSource, ...],
        tuple[VerifiedSourceCatalog, ...],
        tuple[SourceRefreshStatus, ...],
    ]:
        """Read source definitions and cache under one consistent lock."""

        if not _path_entry_exists(self.path) and not _path_entry_exists(
            self.catalog_path
        ):
            return (), (), ()
        try:
            with self._locked():
                sources = tuple(self._read_sources().sources)
                catalog = self._read_catalog()
                return sources, tuple(catalog.verified), tuple(catalog.statuses)
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))

    def replace_verified_cache(
        self,
        verified: VerifiedSourceCatalog,
        *,
        attempted_at: str,
    ) -> None:
        try:
            with self._locked():
                self._require_current_source(verified.source)
                catalog = self._read_catalog()
                verified_entries = [
                    item
                    for item in catalog.verified
                    if item.source.name != verified.source.name
                ]
                verified_entries.append(verified)
                statuses = [
                    item
                    for item in catalog.statuses
                    if item.source_name != verified.source.name
                ]
                statuses.append(
                    SourceRefreshStatus.model_validate({
                        "sourceName": verified.source.name,
                        "state": "fresh",
                        "attemptedAt": attempted_at,
                    })
                )
                self._write(
                    self.catalog_path,
                    _CatalogState.model_validate({
                        "schemaVersion": _CATALOG_STATE_VERSION,
                        "verified": sorted(
                            verified_entries, key=lambda item: item.source.name
                        ),
                        "statuses": sorted(statuses, key=lambda item: item.source_name),
                    }),
                )
        except WorkflowLockTimeout as error:
            _fail("source_lock_timeout", str(error))

    def record_failed_refresh(
        self,
        source: WorkflowMarketplaceSource,
        error: WorkflowMarketplaceError,
        *,
        attempted_at: str,
        state: RefreshState,
    ) -> VerifiedSourceCatalog | None:
        message = _redacted_error(error, source.repository_url)
        try:
            with self._locked():
                self._require_current_source(source)
                catalog = self._read_catalog()
                cached = next(
                    (
                        item
                        for item in catalog.verified
                        if item.source.name == source.name
                        and item.source.repository_url == source.repository_url
                        and item.source.ref == source.ref
                    ),
                    None,
                )
                statuses = [
                    item for item in catalog.statuses if item.source_name != source.name
                ]
                statuses.append(
                    SourceRefreshStatus.model_validate({
                        "sourceName": source.name,
                        "state": "stale" if cached is not None else state,
                        "attemptedAt": attempted_at,
                        "diagnosticCode": error.code,
                        "message": message,
                    })
                )
                self._write(
                    self.catalog_path,
                    _CatalogState.model_validate({
                        "schemaVersion": _CATALOG_STATE_VERSION,
                        "verified": catalog.verified,
                        "statuses": sorted(statuses, key=lambda item: item.source_name),
                    }),
                )
                return cached
        except WorkflowLockTimeout as lock_error:
            _fail("source_lock_timeout", str(lock_error))


__all__ = [
    "RefreshState",
    "SourceRefreshStatus",
    "VerifiedSourceCatalog",
    "WorkflowSourceStore",
]
