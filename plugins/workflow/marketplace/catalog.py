"""Verified refresh, search, and inspection for workflow marketplace catalogs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
from typing import Callable

from hermes_cli.git_source import safe_git_error

from .git import WorkflowGitFetcher
from .models import WorkflowMarketplaceSource, WorkflowPackageIndexEntry
from .package import WorkflowMarketplaceError, load_repository_index
from .source_store import (
    RefreshState,
    VerifiedSourceCatalog,
    WorkflowSourceStore,
)


_INDEX_PATH = ".well-known/hermes-workflows/index.json"
_MAX_QUERY_LENGTH = 256
_MAX_SEARCH_RESULTS = 200


@dataclass(frozen=True, slots=True)
class SourceRefreshResult:
    """Bounded credential-free result of one source refresh attempt."""

    source_name: str
    repository_url: str
    state: RefreshState
    resolved_commit: str | None
    verified_at: str | None
    package_count: int
    diagnostic_code: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogPackage:
    """A package projection sourced only from a verified cached checkout."""

    identifier: str
    source_name: str
    repository_url: str
    configured_ref: str | None
    resolved_commit: str
    verified_at: str
    state: RefreshState
    id: str
    version: str
    display_name: str
    description: str
    license: str
    publisher: str
    tags: tuple[str, ...]
    package_path: str
    contract_version: int
    package_digest: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(clock: Callable[[], datetime]) -> str:
    value = clock()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _failure_state(error: WorkflowMarketplaceError) -> RefreshState:
    if error.code == "source_authentication_failed":
        return "authentication-failed"
    if error.code == "package_contract_unsupported":
        return "incompatible"
    if error.code.startswith("package_"):
        return "malformed"
    return "unavailable"


def _safe_failure(error: WorkflowMarketplaceError, source_url: str) -> str:
    result = subprocess.CompletedProcess(
        args=(), returncode=1, stdout="", stderr=str(error)
    )
    rendered = safe_git_error(result, source_url).strip()
    return rendered[:4096] or "marketplace source refresh failed"


class WorkflowMarketplaceCatalog:
    """Coordinate exact Git refreshes with the profile-local verified cache."""

    def __init__(
        self,
        source_store: WorkflowSourceStore | None = None,
        git_fetcher: WorkflowGitFetcher | None = None,
        *,
        clock: Callable[[], datetime] = _now,
    ):
        self.source_store = source_store or WorkflowSourceStore()
        self.git_fetcher = git_fetcher or WorkflowGitFetcher()
        self.clock = clock

    def add_source(
        self,
        name: str | WorkflowMarketplaceSource,
        repository_url: str | None = None,
        *,
        ref: str | None = None,
        enabled: bool = True,
    ) -> WorkflowMarketplaceSource:
        return self.source_store.add(
            name,
            repository_url,
            ref=ref,
            enabled=enabled,
        )

    def refresh_source(
        self,
        name: str,
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> SourceRefreshResult:
        source = self.source_store.get(name)
        if not source.enabled:
            try:
                cached = self.source_store.cached(source)
            except WorkflowMarketplaceError as error:
                return self._unpersisted_failure(source, error)
            return SourceRefreshResult(
                source_name=source.name,
                repository_url=source.repository_url,
                state="disabled",
                resolved_commit=(
                    cached.resolved_commit if cached is not None else None
                ),
                verified_at=cached.verified_at if cached is not None else None,
                package_count=len(cached.packages) if cached is not None else 0,
            )
        attempted_at = _timestamp(self.clock)
        try:
            with tempfile.TemporaryDirectory(
                prefix="hermes-workflow-marketplace-"
            ) as temporary:
                checkout = self.git_fetcher.fetch(
                    source,
                    Path(temporary) / "repository",
                    sparse_paths=(_INDEX_PATH,),
                    cancelled=cancelled,
                )
                index = load_repository_index(checkout.root)
                verified = VerifiedSourceCatalog.model_validate({
                    "source": source.model_dump(mode="json", by_alias=True),
                    "resolvedCommit": checkout.resolved_commit,
                    "verifiedAt": attempted_at,
                    "packages": [
                        entry.model_dump(mode="json", by_alias=True)
                        for entry in index.packages
                    ],
                })
                try:
                    cancellation_requested = cancelled()
                except Exception:
                    cancellation_requested = True
                if cancellation_requested:
                    raise WorkflowMarketplaceError(
                        "source_cancelled",
                        "workflow marketplace refresh was cancelled",
                    )
            self.source_store.replace_verified_cache(
                verified,
                attempted_at=attempted_at,
                cancelled=cancelled,
            )
        except WorkflowMarketplaceError as error:
            if error.code == "source_cancelled":
                try:
                    cached = self.source_store.cached(source)
                except WorkflowMarketplaceError as cache_error:
                    return self._unpersisted_failure(source, cache_error)
                return SourceRefreshResult(
                    source_name=source.name,
                    repository_url=source.repository_url,
                    state="cancelled",
                    resolved_commit=(
                        cached.resolved_commit if cached is not None else None
                    ),
                    verified_at=cached.verified_at if cached is not None else None,
                    package_count=len(cached.packages) if cached is not None else 0,
                    diagnostic_code=error.code,
                    message=_safe_failure(error, source.repository_url),
                )
            state = _failure_state(error)
            try:
                cached = self.source_store.record_failed_refresh(
                    source,
                    error,
                    attempted_at=attempted_at,
                    state=state,
                )
                status = self.source_store.status(source.name)
            except WorkflowMarketplaceError as cache_error:
                return self._unpersisted_failure(source, cache_error)
            return SourceRefreshResult(
                source_name=source.name,
                repository_url=source.repository_url,
                state="stale" if cached is not None else state,
                resolved_commit=cached.resolved_commit if cached is not None else None,
                verified_at=cached.verified_at if cached is not None else None,
                package_count=len(cached.packages) if cached is not None else 0,
                diagnostic_code=error.code,
                message=status.message if status is not None else None,
            )
        return SourceRefreshResult(
            source_name=source.name,
            repository_url=source.repository_url,
            state="fresh",
            resolved_commit=verified.resolved_commit,
            verified_at=verified.verified_at,
            package_count=len(verified.packages),
        )

    def _unpersisted_failure(
        self,
        source: WorkflowMarketplaceSource,
        error: WorkflowMarketplaceError,
    ) -> SourceRefreshResult:
        return SourceRefreshResult(
            source_name=source.name,
            repository_url=source.repository_url,
            state="unavailable",
            resolved_commit=None,
            verified_at=None,
            package_count=0,
            diagnostic_code=error.code,
            message=_safe_failure(error, source.repository_url),
        )

    def _projections(self) -> tuple[CatalogPackage, ...]:
        source_items, verified_catalogs, refresh_statuses = self.source_store.snapshot()
        sources = {source.name: source for source in source_items}
        statuses = {status.source_name: status for status in refresh_statuses}
        projected: list[CatalogPackage] = []
        for cached in verified_catalogs:
            source = sources.get(cached.source.name)
            if (
                source is None
                or not source.enabled
                or source.repository_url != cached.source.repository_url
                or source.ref != cached.source.ref
            ):
                continue
            status = statuses.get(source.name)
            if status is None:
                raise WorkflowMarketplaceError(
                    "catalog_state_invalid",
                    "verified marketplace catalog has no refresh status",
                )
            state: RefreshState = (
                status.state
                if status is not None and status.state == "stale"
                else "fresh"
            )
            projected.extend(
                self._package_projection(source, cached, entry, state=state)
                for entry in cached.packages
            )
        return tuple(sorted(projected, key=lambda item: item.identifier))

    @staticmethod
    def _package_projection(
        source: WorkflowMarketplaceSource,
        cached: VerifiedSourceCatalog,
        entry: WorkflowPackageIndexEntry,
        *,
        state: RefreshState,
    ) -> CatalogPackage:
        return CatalogPackage(
            identifier=f"{source.name}/{entry.id}",
            source_name=source.name,
            repository_url=source.repository_url,
            configured_ref=source.ref,
            resolved_commit=cached.resolved_commit,
            verified_at=cached.verified_at,
            state=state,
            id=entry.id,
            version=entry.version,
            display_name=entry.display_name,
            description=entry.description,
            license=entry.license,
            publisher=entry.publisher,
            tags=tuple(entry.tags),
            package_path=entry.package_path,
            contract_version=entry.contract_version,
            package_digest=entry.package_digest,
        )

    def search(
        self,
        query: str,
        *,
        source: str | None = None,
        limit: int = 100,
    ) -> tuple[CatalogPackage, ...]:
        if not isinstance(query, str) or len(query) > _MAX_QUERY_LENGTH:
            raise WorkflowMarketplaceError(
                "catalog_query_invalid",
                f"catalog query must contain at most {_MAX_QUERY_LENGTH} characters",
            )
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= _MAX_SEARCH_RESULTS
        ):
            raise WorkflowMarketplaceError(
                "catalog_limit_invalid",
                f"catalog result limit must be between 1 and {_MAX_SEARCH_RESULTS}",
            )
        source_name = None
        if source is not None:
            source_name = self.source_store.get(source).name
        folded = query.strip().casefold()
        results = []
        for item in self._projections():
            if source_name is not None and item.source_name != source_name:
                continue
            searchable = "\n".join((
                item.identifier,
                item.display_name,
                item.description,
                item.publisher,
                *item.tags,
            )).casefold()
            if folded and folded not in searchable:
                continue
            results.append(item)
            if len(results) == limit:
                break
        return tuple(results)

    def inspect(self, identifier: str) -> CatalogPackage:
        if not isinstance(identifier, str) or len(identifier) > 129:
            raise WorkflowMarketplaceError(
                "catalog_identifier_invalid",
                "catalog package identifier must be source/package",
            )
        source_name, separator, package_id = identifier.partition("/")
        if not separator or not source_name or not package_id or "/" in package_id:
            raise WorkflowMarketplaceError(
                "catalog_identifier_invalid",
                "catalog package identifier must be source/package",
            )
        source = self.source_store.get(source_name)
        expected = f"{source.name}/{package_id}"
        for item in self._projections():
            if item.identifier == expected:
                return item
        raise WorkflowMarketplaceError(
            "catalog_package_not_found",
            f"verified catalog package {expected!r} was not found",
        )


__all__ = [
    "CatalogPackage",
    "SourceRefreshResult",
    "WorkflowMarketplaceCatalog",
]
