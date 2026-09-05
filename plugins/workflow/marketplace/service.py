"""Presentation-independent workflow package marketplace orchestration."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import threading
from typing import Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
import yaml

from hermes_cli.git_source import (
    GitSourceError,
    canonical_git_source,
    resolve_git_source,
    safe_git_error,
    validate_credential_free_git_source,
)
from hermes_constants import get_hermes_home
from plugins.workflow.admission_service import (
    WorkflowAdmissionAssessment,
    assess_production_workflow_admission,
)
from plugins.workflow.compilation import (
    WorkflowCatalogSnapshot,
    WorkflowCompilation,
    compile_workflow,
)
from plugins.workflow.locks import WorkflowLockTimeout, workflow_lock
from plugins.workflow.models import WorkflowMarketplaceBinding, WorkflowValidationError
from plugins.workflow.schema import parse_workflow_source_bytes
from plugins.workflow.resources import normalize_mcp_server_document
from plugins.workflow.trust import WorkflowTrustStore
from utils import atomic_write_text

from .catalog import CatalogPackage, SourceRefreshResult, WorkflowMarketplaceCatalog
from .git import WorkflowGitFetcher
from .models import (
    ExternalRequirements,
    FileDigestChange,
    InstallRequest,
    InstalledPackage,
    InstalledPackageIdentity,
    InstalledPackageProvenance,
    InstallReview,
    PackageDiagnostic,
    PackageInspection,
    PackageInspectionResource,
    PackageInspectionResourceType,
    PackageReviewAssessment,
    RemoveReview,
    RequirementChanges,
    StringSetChange,
    TrustReview,
    UpdateCheck,
    UpdateReview,
    WorkflowMarketplaceSource,
    WorkflowCompatibilityChanges,
    WorkflowCompatibilityIdentity,
    WorkflowRiskChanges,
    WorkflowRiskIdentity,
    WorkflowTrustReviewItem,
)
from .package import (
    WorkflowDistribution,
    WorkflowMarketplaceError,
    load_distribution,
    load_repository_index,
    load_repository_index_document,
    verify_indexed_distribution,
)
from .provenance import InstalledPackageStore, direct_source_key
from .source_store import (
    RefreshState,
    WorkflowSourceStore,
    _path_entry_exists,
    _read_bounded,
    _strict_json,
)
from .transactions import MarketplaceTransactionStore, TransactionCandidate
from .lifecycle_state import domain_mutation, _capture_trust


_INDEX_PATH = ".well-known/hermes-workflows/index.json"
_REVIEW_DOMAIN = b"hermes.workflow-marketplace.lifecycle-review.v1\0"
_TRUST_REVIEW_DOMAIN = b"hermes.workflow-marketplace.trust-review.v1\0"
_ISSUED_REVIEW_DOMAIN = b"hermes.workflow-marketplace.issued-review.v1\0"
_ISSUED_REVIEW_LIMIT = 512  # Existing 256 transaction + 256 trust token maxima.
_TRUST_TOKEN_STATE_VERSION = 1
_TRUST_TOKEN_STATE_BYTES = 1024 * 1024
_TRUST_TOKEN_LIMIT = 256
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,4096}$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_DIRECT_PREFIX = "direct-"
_CREDENTIAL_VALUE = re.compile(
    r"(?i)\b(token|password|secret|authorization|api[_-]?key)\b"
    r"(\s*(?::|=|\bis\b)?\s+)([^\s,;]+)"
)
_DESTINATION_ADVISORY_CODES = frozenset({
    "execution_environment_unavailable",
    "mcp_isolation",
    "mcp_unavailable",
    "provider_authority_missing",
    "provider_field_unsupported",
    "required_service",
    "requested_tool",
    "tool_unavailable",
    "worktree_requirement",
})


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
        raise ValueError("invalid review timestamp") from error
    if _timestamp(parsed) != value:
        raise ValueError("invalid review timestamp")
    return parsed


def _safe_message(message: object, repository_url: str = "") -> str:
    result = subprocess.CompletedProcess(
        args=(), returncode=1, stdout="", stderr=str(message)
    )
    rendered = safe_git_error(result, repository_url).strip()
    rendered = _CREDENTIAL_VALUE.sub(r"\1 [REDACTED]", rendered)
    if not rendered:
        rendered = "workflow marketplace operation failed"
    return rendered.encode("utf-8")[:4000].decode("utf-8", errors="ignore")


def _canonical_digest(domain: bytes, value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(domain + encoded).hexdigest()


def _issued_review_fingerprint(
    review: InstallReview | UpdateReview | RemoveReview | TrustReview,
) -> str:
    """Fingerprint the complete issued review without retaining its raw token."""

    value = review.model_dump(mode="json", by_alias=True)
    value.pop("confirmationToken", None)
    return _canonical_digest(_ISSUED_REVIEW_DOMAIN, value)


def _set_change(old: Iterable[str], candidate: Iterable[str]) -> StringSetChange:
    before = set(old)
    after = set(candidate)
    return StringSetChange(
        added=sorted(after - before),
        removed=sorted(before - after),
    )


def _requirement_changes(
    old: ExternalRequirements,
    candidate: ExternalRequirements,
) -> RequirementChanges:
    return RequirementChanges(**{
        field: _set_change(getattr(old, field), getattr(candidate, field))
        for field in ("runtimes", "tools", "providers", "services", "secrets")
    })


def _risk_changes(
    old: Iterable[WorkflowTrustReviewItem],
    candidate: Iterable[WorkflowTrustReviewItem],
) -> WorkflowRiskChanges:
    def values(
        items: Iterable[WorkflowTrustReviewItem],
    ) -> dict[tuple[str, str, str], WorkflowRiskIdentity]:
        return {
            (item.workflow_name, item.package_digest, item.risk_digest): (
                WorkflowRiskIdentity(
                    workflowName=item.workflow_name,
                    packageDigest=item.package_digest,
                    riskDigest=item.risk_digest,
                )
            )
            for item in items
        }

    before = values(old)
    after = values(candidate)
    return WorkflowRiskChanges(
        added=[after[key] for key in sorted(set(after) - set(before))],
        removed=[before[key] for key in sorted(set(before) - set(after))],
    )


def _compatibility_changes(
    old: Iterable[WorkflowTrustReviewItem],
    candidate: Iterable[WorkflowTrustReviewItem],
) -> WorkflowCompatibilityChanges:
    def values(
        items: Iterable[WorkflowTrustReviewItem],
    ) -> dict[tuple[str, str, str], WorkflowCompatibilityIdentity]:
        return {
            (item.workflow_name, finding.code, finding.severity): (
                WorkflowCompatibilityIdentity(
                    workflowName=item.workflow_name,
                    code=finding.code,
                    severity=finding.severity,
                )
            )
            for item in items
            for finding in item.compatibility
        }

    before = values(old)
    after = values(candidate)
    return WorkflowCompatibilityChanges(
        added=[after[key] for key in sorted(set(after) - set(before))],
        removed=[before[key] for key in sorted(set(before) - set(after))],
    )


def _file_changes(
    old: WorkflowDistribution | None,
    candidate: WorkflowDistribution,
) -> list[FileDigestChange]:
    before = (
        {item.relative_path: item for item in old.files if item.included}
        if old is not None
        else {}
    )
    after = {item.relative_path: item for item in candidate.files if item.included}
    removed = set(before) - set(after)
    added = set(after) - set(before)
    renamed: dict[str, str] = {}
    by_identity: dict[tuple[int, str], list[str]] = {}
    for path in removed:
        item = before[path]
        by_identity.setdefault((item.size, item.sha256), []).append(path)
    for new_path in sorted(added):
        item = after[new_path]
        matches = by_identity.get((item.size, item.sha256), [])
        if len(matches) == 1:
            old_path = matches[0]
            if (
                sum(
                    1
                    for path in added
                    if (after[path].size, after[path].sha256)
                    == (item.size, item.sha256)
                )
                == 1
            ):
                renamed[new_path] = old_path
                removed.remove(old_path)
    added.difference_update(renamed)
    changes: list[FileDigestChange] = []
    for path in sorted(set(before).intersection(after)):
        if before[path].sha256 != after[path].sha256:
            changes.append(
                FileDigestChange(
                    path=path,
                    kind="modified",
                    oldDigest=before[path].sha256,
                    candidateDigest=after[path].sha256,
                )
            )
    changes.extend(
        FileDigestChange(
            path=path,
            kind="renamed",
            oldPath=old_path,
            oldDigest=before[old_path].sha256,
            candidateDigest=after[path].sha256,
        )
        for path, old_path in sorted(renamed.items())
    )
    changes.extend(
        FileDigestChange(
            path=path,
            kind="added",
            candidateDigest=after[path].sha256,
        )
        for path in sorted(added)
    )
    changes.extend(
        FileDigestChange(
            path=path,
            kind="removed",
            oldDigest=before[path].sha256,
        )
        for path in sorted(removed)
    )
    return sorted(changes, key=lambda item: item.path)


def _semver_key(value: str) -> tuple[int, int, int, tuple[tuple[int, int, str], ...]]:
    core, _, _build = value.partition("+")
    release, separator, prerelease = core.partition("-")
    major, minor, patch = (int(part) for part in release.split("."))
    if not separator:
        pre_key: tuple[tuple[int, int, str], ...] = ((2, 0, ""),)
    else:
        pre_key = tuple(
            (0, int(part), "") if part.isdigit() else (1, 0, part)
            for part in prerelease.split(".")
        )
    return major, minor, patch, pre_key


class _TokenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _TrustTokenRecord(_TokenModel):
    token_digest: str = Field(alias="tokenDigest", pattern=_SHA256.pattern)
    actor: str = Field(min_length=1, max_length=256)
    profile: str = Field(min_length=1, max_length=256)
    identity: InstalledPackageIdentity
    distribution_digest: str = Field(
        alias="distributionDigest", pattern=_SHA256.pattern
    )
    review_digest: str = Field(alias="reviewDigest", pattern=_SHA256.pattern)
    workflow_paths: list[str] = Field(
        alias="workflowPaths", min_length=1, max_length=512
    )
    expires_at: str = Field(alias="expiresAt", min_length=20, max_length=64)

    @model_validator(mode="after")
    def validate_record(self) -> "_TrustTokenRecord":
        if (
            self.actor != self.actor.strip()
            or self.profile != self.profile.strip()
            or "\0" in self.actor
            or "\0" in self.profile
            or self.workflow_paths != sorted(self.workflow_paths)
            or len(self.workflow_paths) != len(set(self.workflow_paths))
        ):
            raise ValueError("trust confirmation identity is invalid")
        _parse_timestamp(self.expires_at)
        return self


class _TrustTokenState(_TokenModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    tokens: list[_TrustTokenRecord] = Field(max_length=_TRUST_TOKEN_LIMIT)

    @model_validator(mode="after")
    def validate_records(self) -> "_TrustTokenState":
        keys = [record.token_digest for record in self.tokens]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("trust confirmations must be unique and sorted")
        return self


@dataclass(frozen=True, slots=True)
class _TrustAuthorization:
    identity: InstalledPackageIdentity
    distribution_digest: str
    review_digest: str
    workflow_paths: tuple[str, ...]
    expires_at: str


@dataclass(frozen=True, slots=True)
class ConfirmationTargetMetadata:
    operation: Literal["install", "update", "remove", "trust"]
    identity: InstalledPackageIdentity


class _TrustConfirmationStore:
    def __init__(
        self,
        source_store: WorkflowSourceStore,
        *,
        clock: Callable[[], datetime],
    ):
        self.source_store = source_store
        self.path = source_store.root / "trust-confirmations.json"
        self.clock = clock
        self._selections = {}

    def _read(self) -> _TrustTokenState:
        if not _path_entry_exists(self.path):
            return _TrustTokenState.model_validate({"schemaVersion": 1, "tokens": []})
        try:
            return _TrustTokenState.model_validate(
                _strict_json(
                    _read_bounded(
                        self.path,
                        limit=_TRUST_TOKEN_STATE_BYTES,
                        size_code="trust_confirmation_size_limit",
                    ),
                    code="trust_confirmation_state_invalid",
                )
            )
        except ValidationError:
            _fail(
                "trust_confirmation_state_invalid",
                "persisted trust confirmations are invalid",
            )

    def _write(
        self,
        tokens: Iterable[_TrustTokenRecord],
        *,
        parent_identity: tuple[int, int],
    ) -> None:
        state = _TrustTokenState(
            schemaVersion=1,
            tokens=sorted(tokens, key=lambda item: item.token_digest),
        )
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
        if len(rendered.encode()) > _TRUST_TOKEN_STATE_BYTES:
            _fail(
                "trust_confirmation_size_limit",
                "persisted trust confirmations exceed their byte limit",
            )
        try:
            atomic_write_text(
                self.path,
                rendered,
                tmp_prefix="trust-confirmations.json.tmp-",
                create_mode=0o600,
                no_follow=True,
                expected_parent_identity=parent_identity,
            )
        except OSError as error:
            raise WorkflowMarketplaceError(
                "trust_confirmation_write_failed",
                "could not persist trust confirmation",
            ) from error

    def issue(
        self,
        *,
        actor: str,
        profile: str,
        identity: InstalledPackageIdentity,
        distribution_digest: str,
        review_digest: str,
        workflow_paths: tuple[str, ...],
        ttl_seconds: int = 300,
        workflow_name: str | None = None,
    ) -> str:
        token = secrets.token_urlsafe(32)
        record = _TrustTokenRecord(
            tokenDigest=hashlib.sha256(token.encode()).hexdigest(),
            actor=actor,
            profile=profile,
            identity=identity,
            distributionDigest=distribution_digest,
            reviewDigest=review_digest,
            workflowPaths=sorted(workflow_paths),
            expiresAt=_timestamp(self.clock() + timedelta(seconds=ttl_seconds)),
        )
        try:
            parent_identity = self.source_store._ensure_private_root()
            with workflow_lock(self.source_store.lock_path):
                now = self.clock()
                current = [
                    item
                    for item in self._read().tokens
                    if _parse_timestamp(item.expires_at) > now
                ]
                if len(current) >= _TRUST_TOKEN_LIMIT:
                    _fail(
                        "trust_confirmation_size_limit",
                        "too many prepared trust confirmations",
                    )
                self._write([*current, record], parent_identity=parent_identity)
                live = {item.token_digest for item in current}
                self._selections = {
                    key: value for key, value in self._selections.items() if key in live
                }
                self._selections[record.token_digest] = workflow_name
        except WorkflowLockTimeout as error:
            _fail("trust_confirmation_lock_timeout", str(error))
        return token

    def selection(self, token: str):
        from .lifecycle_models import AllTrustSelection, OneTrustSelection

        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.source_store._locked():
            if digest not in self._selections:
                _fail(
                    "confirmation_token_invalid",
                    "confirmation selection is unavailable",
                )
            name = self._selections[digest]
            return (
                OneTrustSelection(type="one", workflow_name=name)
                if name is not None
                else AllTrustSelection(type="all")
            )

    def consume(self, token: str, *, actor: str, profile: str) -> _TrustAuthorization:
        try:
            with self.source_store._locked() as parent_identity:
                return self.consume_locked(
                    token,
                    actor=actor,
                    profile=profile,
                    parent_identity=parent_identity,
                )
        except WorkflowLockTimeout as error:
            _fail("trust_confirmation_lock_timeout", str(error))

    def inspect(self, token: str, *, actor: str, profile: str) -> _TrustAuthorization:
        """Read a live token binding without consuming or extending it."""

        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            _fail("confirmation_token_invalid", "confirmation token is invalid")
        token_digest = hashlib.sha256(token.encode()).hexdigest()
        try:
            with self.source_store._locked():
                now = self.clock()
                record = next(
                    (
                        item
                        for item in self._read().tokens
                        if item.token_digest == token_digest
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
                return _TrustAuthorization(
                    identity=record.identity,
                    distribution_digest=record.distribution_digest,
                    review_digest=record.review_digest,
                    workflow_paths=tuple(record.workflow_paths),
                    expires_at=record.expires_at,
                )
        except WorkflowLockTimeout as error:
            _fail("trust_confirmation_lock_timeout", str(error))

    def consume_locked(
        self,
        token: str,
        *,
        actor: str,
        profile: str,
        parent_identity: tuple[int, int],
    ) -> _TrustAuthorization:
        """Consume while the caller holds the shared marketplace lifecycle lock."""

        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            _fail("confirmation_token_invalid", "confirmation token is invalid")
        token_digest = hashlib.sha256(token.encode()).hexdigest()
        state = self._read()
        now = self.clock()
        record = next(
            (
                item
                for item in state.tokens
                if item.token_digest == token_digest
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
        self._write(
            [
                item
                for item in state.tokens
                if item.token_digest != token_digest
                and _parse_timestamp(item.expires_at) > now
            ],
            parent_identity=parent_identity,
        )
        return _TrustAuthorization(
            identity=record.identity,
            distribution_digest=record.distribution_digest,
            review_digest=record.review_digest,
            workflow_paths=tuple(record.workflow_paths),
            expires_at=record.expires_at,
        )


@dataclass(frozen=True, slots=True)
class _FetchedCandidate:
    distribution: WorkflowDistribution
    identity: InstalledPackageIdentity
    source_name: str
    repository_url: str
    configured_ref: str | None
    resolved_commit: str
    package_path: str


@dataclass(frozen=True, slots=True)
class WorkflowMarketplaceSourceListing:
    """One configured source joined to its last persisted refresh authority."""

    name: str
    repository_url: str
    ref: str | None
    enabled: bool
    refresh_state: RefreshState | None
    attempted_at: str | None
    resolved_commit: str | None
    verified_at: str | None
    verified_package_count: int
    diagnostic_code: str | None
    message: str | None


class WorkflowMarketplaceService:
    """Own source, package lifecycle, review, and origin-aware trust rules."""

    def __init__(
        self,
        hermes_home: Path | None = None,
        *,
        profile: str = "default",
        catalog: WorkflowMarketplaceCatalog | None = None,
        transactions: MarketplaceTransactionStore | None = None,
        installed_store: InstalledPackageStore | None = None,
        trust_store: WorkflowTrustStore | None = None,
        assessment_builder: Callable[
            [WorkflowCompilation], WorkflowAdmissionAssessment
        ] = assess_production_workflow_admission,
        available_runtimes: frozenset[str] = frozenset(),
        available_tools: frozenset[str] = frozenset(),
        available_providers: frozenset[str] = frozenset(),
        available_services: frozenset[str] = frozenset(),
        available_secrets: frozenset[str] = frozenset(),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        if (
            not isinstance(profile, str)
            or not profile
            or len(profile) > 256
            or profile != profile.strip()
            or "\0" in profile
        ):
            _fail("profile_invalid", "marketplace profile identity is invalid")
        self.home = _absolute(
            Path(hermes_home) if hermes_home is not None else get_hermes_home()
        )
        source_store = WorkflowSourceStore(self.home)
        self.catalog = catalog or WorkflowMarketplaceCatalog(
            source_store,
            WorkflowGitFetcher(),
            clock=clock,
        )
        self.installed_store = installed_store or InstalledPackageStore(self.home)
        self.trust_store = trust_store or WorkflowTrustStore(self.home)
        self.transactions = transactions or MarketplaceTransactionStore(
            self.home,
            clock=clock,
            trust_store=self.trust_store,
        )
        self.transactions.trust_store = self.trust_store
        self.profile = profile
        self.assessment_builder = assessment_builder
        self.available_capabilities = {
            "runtimes": frozenset(available_runtimes),
            "tools": frozenset(available_tools),
            "providers": frozenset(available_providers),
            "services": frozenset(available_services),
            "secrets": frozenset(available_secrets),
        }
        self.clock = clock
        self._trust_confirmations = _TrustConfirmationStore(
            self.catalog.source_store,
            clock=clock,
        )
        self._issued_review_lock = threading.Lock()
        self._issued_reviews: dict[str, str] = {}

    def add_source(
        self, source: WorkflowMarketplaceSource
    ) -> WorkflowMarketplaceSource:
        return self.catalog.source_store.add(source)

    def list_sources(self) -> tuple[WorkflowMarketplaceSource, ...]:
        return self.catalog.source_store.list_sources()

    def list_source_records(self) -> tuple[WorkflowMarketplaceSourceListing, ...]:
        """Return source configuration and cache status from one locked snapshot."""

        sources, verified_catalogs, refresh_statuses = (
            self.catalog.source_store.snapshot()
        )
        verified_by_name = {
            catalog.source.name: catalog for catalog in verified_catalogs
        }
        status_by_name = {status.source_name: status for status in refresh_statuses}
        records: list[WorkflowMarketplaceSourceListing] = []
        for source in sorted(sources, key=lambda item: item.name):
            verified = verified_by_name.get(source.name)
            status = status_by_name.get(source.name)
            records.append(
                WorkflowMarketplaceSourceListing(
                    name=source.name,
                    repository_url=source.repository_url,
                    ref=source.ref,
                    enabled=source.enabled,
                    refresh_state=status.state if status is not None else None,
                    attempted_at=status.attempted_at if status is not None else None,
                    resolved_commit=(
                        verified.resolved_commit if verified is not None else None
                    ),
                    verified_at=verified.verified_at if verified is not None else None,
                    verified_package_count=(
                        len(verified.packages) if verified is not None else 0
                    ),
                    diagnostic_code=(
                        status.diagnostic_code if status is not None else None
                    ),
                    message=status.message if status is not None else None,
                )
            )
        return tuple(records)

    def set_source_enabled(self, name: str, enabled: bool) -> WorkflowMarketplaceSource:
        return self.catalog.source_store.set_enabled(name, enabled)

    def update_source(
        self,
        name: str,
        repository_url: str,
        *,
        ref: str | None,
        enabled: bool,
    ) -> WorkflowMarketplaceSource:
        return self.catalog.source_store.update(
            name,
            repository_url,
            ref=ref,
            enabled=enabled,
        )

    def remove_source(self, name: str) -> WorkflowMarketplaceSource:
        return self.catalog.source_store.remove(name)

    def canonical_install_target(self, request: InstallRequest) -> str:
        """Return a credential-free logical single-flight target for a request."""

        if not isinstance(request, InstallRequest):
            _fail("install_request_invalid", "install request is invalid")
        source_name, separator, package_id = request.identifier.partition("/")
        if separator and package_id and "/" not in package_id:
            try:
                self.catalog.source_store.get(source_name)
            except WorkflowMarketplaceError as error:
                if error.code != "source_not_found":
                    raise
            else:
                if request.ref is not None or request.package_path is not None:
                    _fail(
                        "install_request_conflict",
                        "registered installs use source package identity",
                    )
                identity = InstalledPackageIdentity(
                    sourceKey=source_name,
                    packageId=package_id,
                )
                return f"package:{identity.source_key}/{identity.package_id}"
        try:
            resolved = resolve_git_source(request.identifier)
            repository_url = canonical_git_source(resolved.clone_url, None)
            validate_credential_free_git_source(repository_url)
        except GitSourceError:
            _fail("source_invalid", "direct marketplace Git source is invalid")
        embedded_path = resolved.subdirectory
        if (
            request.package_path is not None
            and embedded_path is not None
            and request.package_path != embedded_path
        ):
            _fail(
                "direct_package_path_conflict",
                "direct Git URL and request specify different package paths",
            )
        scope = json.dumps(
            {
                "repository": repository_url,
                "ref": request.ref,
                "path": request.package_path or embedded_path,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return (
            f"package-direct:{direct_source_key(repository_url)}:"
            f"{hashlib.sha256(scope.encode()).hexdigest()}"
        )

    def confirmation_metadata(
        self,
        token: str,
        *,
        actor: str,
        operation: Literal["install", "update", "remove", "trust"],
    ) -> ConfirmationTargetMetadata:
        """Resolve live actor/profile-bound token metadata without consuming it."""

        if operation == "trust":
            authorization = self._trust_confirmations.inspect(
                token, actor=actor, profile=self.profile
            )
            return ConfirmationTargetMetadata(
                operation="trust", identity=authorization.identity
            )
        prepared = self.transactions.inspect_token(
            token,
            actor=actor,
            profile=self.profile,
        )
        if prepared.operation != operation:
            _fail("confirmation_token_invalid", "confirmation token is invalid")
        return ConfirmationTargetMetadata(
            operation=prepared.operation,
            identity=prepared.identity,
        )

    def _remember_issued_review(
        self, review: InstallReview | UpdateReview | RemoveReview | TrustReview
    ) -> None:
        token = review.confirmation_token
        if not isinstance(token, str):
            _fail(
                "confirmation_token_invalid",
                "review confirmation authority is unavailable",
            )
        token_digest = hashlib.sha256(token.encode()).hexdigest()
        fingerprint = _issued_review_fingerprint(review)
        with self.transactions._locked():
            transaction_now = self.transactions.clock()
            trust_now = self._trust_confirmations.clock()
            live = {
                record.token_digest
                for record in self.transactions._read_prepared().transactions
                if _parse_timestamp(record.expires_at) > transaction_now
            }
            live.update(
                record.token_digest
                for record in self._trust_confirmations._read().tokens
                if _parse_timestamp(record.expires_at) > trust_now
            )
        if token_digest not in live:
            _fail(
                "confirmation_token_invalid",
                "review confirmation authority is unavailable",
            )
        with self._issued_review_lock:
            retained = {
                key: value for key, value in self._issued_reviews.items() if key in live
            }
            if token_digest not in retained and len(retained) >= _ISSUED_REVIEW_LIMIT:
                _fail(
                    "transaction_state_size_limit",
                    "review confirmation authority capacity is exhausted",
                )
            retained[token_digest] = fingerprint
            self._issued_reviews = retained

    def _matches_issued_review(
        self, review: InstallReview | UpdateReview | RemoveReview | TrustReview
    ) -> bool:
        token = review.confirmation_token
        if not isinstance(token, str):
            return False
        token_digest = hashlib.sha256(token.encode()).hexdigest()
        fingerprint = _issued_review_fingerprint(review)
        with self._issued_review_lock:
            return self._issued_reviews.get(token_digest) == fingerprint

    def refresh_source(
        self,
        name: str,
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> SourceRefreshResult:
        return self.catalog.refresh_source(name, cancelled=cancelled)

    def search(
        self,
        query: str,
        *,
        source: str | None = None,
        limit: int = 100,
    ) -> tuple[CatalogPackage, ...]:
        return self.catalog.search(query, source=source, limit=limit)

    def inspect(
        self,
        identifier: str,
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> PackageInspection:
        if not isinstance(identifier, str) or len(identifier) > 129:
            _fail(
                "catalog_identifier_invalid",
                "catalog package identifier must be source/package",
            )
        source_name, separator, package_id = identifier.partition("/")
        if not separator or not source_name or not package_id or "/" in package_id:
            _fail(
                "catalog_identifier_invalid",
                "catalog package identifier must be source/package",
            )
        with self._fetch_registered(
            source_name,
            package_id,
            cancelled=cancelled,
        ) as fetched:
            workflows, blockers = self._assess_distribution(
                fetched.distribution,
                fetched.identity,
                fetched.source_name,
            )
            assessment = self._assessment(
                fetched.distribution,
                workflows,
                blockers,
                review_digest=fetched.distribution.digest,
            )
            installed = self._optional_installed(fetched.identity)
            installed_projection = None
            update_status: Literal["not_applicable", "current", "update_available"] = (
                "not_applicable"
            )
            if installed is not None:
                installed_distribution = load_distribution(
                    self.installed_store.package_root(installed.identity),
                    expected_digest=installed.distribution_digest,
                )
                candidate_version = fetched.distribution.manifest.version
                installed_version = installed_distribution.manifest.version
                candidate_key = _semver_key(candidate_version)
                installed_key = _semver_key(installed_version)
                if candidate_key < installed_key:
                    _fail(
                        "package_version_regression",
                        "candidate package version is older than the installed version",
                    )
                if (
                    candidate_key == installed_key
                    and fetched.distribution.digest != installed_distribution.digest
                ):
                    _fail(
                        "package_version_conflict",
                        "candidate changed bytes without advancing package version",
                    )
                update_status = (
                    "current"
                    if fetched.distribution.digest == installed_distribution.digest
                    else "update_available"
                )
                projected = self._installed_projection(installed)
                installed_projection = projected.model_copy(
                    update={
                        "configured_ref": (
                            _safe_message(
                                projected.configured_ref,
                                projected.repository_url,
                            )
                            if projected.configured_ref is not None
                            else None
                        ),
                        "actor": _safe_message(
                            projected.actor,
                            projected.repository_url,
                        ),
                    }
                )
            manifest = fetched.distribution.manifest
            return PackageInspection.model_validate({
                "identifier": f"{fetched.source_name}/{manifest.id}",
                "identity": fetched.identity,
                "sourceName": fetched.source_name,
                "repositoryUrl": fetched.repository_url,
                "configuredRef": (
                    _safe_message(fetched.configured_ref, fetched.repository_url)
                    if fetched.configured_ref is not None
                    else None
                ),
                "resolvedCommit": fetched.resolved_commit,
                "verifiedAt": _timestamp(self.clock()),
                "verified": True,
                "sourceState": "fresh",
                "id": manifest.id,
                "version": manifest.version,
                "displayName": _safe_message(
                    manifest.display_name, fetched.repository_url
                ),
                "description": _safe_message(
                    manifest.description, fetched.repository_url
                ),
                "license": _safe_message(manifest.license, fetched.repository_url),
                "publisher": _safe_message(manifest.publisher, fetched.repository_url),
                "tags": sorted(manifest.tags),
                "packagePath": fetched.package_path,
                "contractVersion": 1,
                "packageDigest": fetched.distribution.digest,
                "workflows": workflows,
                "resources": self._inspection_resources(
                    fetched.distribution,
                    workflows,
                ),
                "externalRequirements": assessment.external_requirements,
                "blockers": assessment.blockers,
                "advisories": assessment.advisories,
                "installStatus": (
                    "installed" if installed is not None else "not_installed"
                ),
                "updateStatus": update_status,
                "installed": installed_projection,
            })

    @staticmethod
    def _inspection_resources(
        distribution: WorkflowDistribution,
        workflows: list[WorkflowTrustReviewItem],
    ) -> list[PackageInspectionResource]:
        roles: dict[str, set[PackageInspectionResourceType]] = {}

        def add(paths: Iterable[str], role: PackageInspectionResourceType) -> None:
            for path in paths:
                roles.setdefault(path, set()).add(role)

        add((item.definition_path for item in workflows), "workflow_definition")
        add(
            (
                item.companion_path
                for item in workflows
                if item.companion_path is not None
            ),
            "workflow_companion",
        )
        for item in workflows:
            add(item.command_resources, "command")
            add(item.script_resources, "script")
            add(item.mcp_resources, "mcp")
            add(item.mcp_resource_files, "mcp_resource")
        return [
            PackageInspectionResource(
                path=path,
                types=sorted(roles.get(path, {"other"})),
            )
            for path in sorted(distribution.covered_paths)
        ]

    def installed_packages(self) -> tuple[InstalledPackage, ...]:
        return tuple(
            self._installed_projection(item)
            for item in self.installed_store.list_installed()
        )

    def _installed_projection(
        self, provenance: InstalledPackageProvenance
    ) -> InstalledPackage:
        orphaned = False
        if not provenance.identity.source_key.startswith(_DIRECT_PREFIX):
            try:
                source = self.catalog.source_store.get(provenance.source_name)
                orphaned = source.repository_url != provenance.repository_url
            except WorkflowMarketplaceError as error:
                if error.code != "source_not_found":
                    raise
                orphaned = True
        return InstalledPackage(
            identity=provenance.identity,
            sourceName=provenance.source_name,
            repositoryUrl=provenance.repository_url,
            configuredRef=provenance.configured_ref,
            resolvedCommit=provenance.resolved_commit,
            packagePath=provenance.package_path,
            version=provenance.package_version,
            contractVersion=provenance.contract_version,
            distributionDigest=provenance.distribution_digest,
            installedAt=provenance.installed_at,
            actor=provenance.actor,
            workflowPaths=provenance.workflow_paths,
            orphanedSource=orphaned,
        )

    def _optional_installed(
        self, identity: InstalledPackageIdentity
    ) -> InstalledPackageProvenance | None:
        try:
            return self.installed_store.get(identity)
        except WorkflowMarketplaceError as error:
            if error.code == "installed_package_not_found":
                return None
            raise

    @contextmanager
    def _fetch_registered(
        self,
        source_name: str,
        package_id: str,
        *,
        cancelled: Callable[[], bool],
    ):
        source = self.catalog.source_store.get(source_name)
        if not source.enabled:
            _fail("source_disabled", "registered marketplace source is disabled")
        with tempfile.TemporaryDirectory(
            prefix="hermes-workflow-marketplace-service-"
        ) as temporary:
            checkout = self.catalog.git_fetcher.fetch(
                source,
                Path(temporary) / "repository",
                sparse_paths=(_INDEX_PATH,),
                selected_package_id=package_id,
                cancelled=cancelled,
            )
            index = load_repository_index_document(checkout.root)
            entry = next(
                (item for item in index.packages if item.id == package_id), None
            )
            if entry is None:
                _fail(
                    "catalog_package_not_found",
                    "registered marketplace package was not found in fetched bytes",
                )
            distribution = load_distribution(
                checkout.root.joinpath(*entry.package_path.split("/")),
                expected_digest=entry.package_digest,
            )
            verify_indexed_distribution(entry, distribution)
            yield _FetchedCandidate(
                distribution=distribution,
                identity=InstalledPackageIdentity(
                    sourceKey=source.name,
                    packageId=distribution.manifest.id,
                ),
                source_name=source.name,
                repository_url=checkout.repository_url,
                configured_ref=source.ref,
                resolved_commit=checkout.resolved_commit,
                package_path=entry.package_path,
            )

    @contextmanager
    def _fetch_direct(
        self,
        request: InstallRequest,
        *,
        cancelled: Callable[[], bool],
    ):
        try:
            validate_credential_free_git_source(request.identifier)
            resolved = resolve_git_source(request.identifier)
            repository_url = canonical_git_source(resolved.clone_url, None)
            validate_credential_free_git_source(repository_url)
        except GitSourceError as error:
            code = (
                "source_credentials_forbidden"
                if "credentials" in str(error).casefold()
                else "source_invalid"
            )
            _fail(code, "direct marketplace Git source is invalid")
        embedded_path = resolved.subdirectory
        if (
            request.package_path is not None
            and embedded_path is not None
            and request.package_path != embedded_path
        ):
            _fail(
                "direct_package_path_conflict",
                "direct Git URL and request specify different package paths",
            )
        requested_path = request.package_path or embedded_path
        source_key = direct_source_key(repository_url)
        source = WorkflowMarketplaceSource(
            name=source_key,
            repositoryUrl=repository_url,
            ref=request.ref,
        )
        sparse_paths = (
            (_INDEX_PATH, requested_path)
            if requested_path is not None
            else (_INDEX_PATH,)
        )
        with tempfile.TemporaryDirectory(
            prefix="hermes-workflow-marketplace-service-"
        ) as temporary:
            checkout = self.catalog.git_fetcher.fetch(
                source,
                Path(temporary) / "repository",
                sparse_paths=sparse_paths,
                cancelled=cancelled,
            )
            index_path = checkout.root.joinpath(*_INDEX_PATH.split("/"))
            index = (
                load_repository_index(checkout.root) if index_path.exists() else None
            )
            if requested_path is None:
                if index is None or len(index.packages) != 1:
                    _fail(
                        "direct_package_ambiguous",
                        "direct repository must resolve to exactly one package",
                    )
                entry = index.packages[0]
                package_path = entry.package_path
                expected_digest = entry.package_digest
            else:
                package_path = requested_path
                entry = (
                    next(
                        (
                            item
                            for item in index.packages
                            if item.package_path == package_path
                        ),
                        None,
                    )
                    if index is not None
                    else None
                )
                expected_digest = entry.package_digest if entry is not None else None
            distribution = load_distribution(
                checkout.root.joinpath(*package_path.split("/")),
                expected_digest=expected_digest,
            )
            if entry is not None and entry.id != distribution.manifest.id:
                _fail(
                    "package_index_invalid",
                    "direct package identity differs from repository index",
                )
            yield _FetchedCandidate(
                distribution=distribution,
                identity=InstalledPackageIdentity(
                    sourceKey=source_key,
                    packageId=distribution.manifest.id,
                ),
                source_name=source_key,
                repository_url=checkout.repository_url,
                configured_ref=request.ref,
                resolved_commit=checkout.resolved_commit,
                package_path=package_path,
            )

    @contextmanager
    def _fetch_request(
        self,
        request: InstallRequest,
        *,
        cancelled: Callable[[], bool],
    ):
        source_name, separator, package_id = request.identifier.partition("/")
        registered = False
        if separator and package_id and "/" not in package_id:
            try:
                self.catalog.source_store.get(source_name)
                registered = True
            except WorkflowMarketplaceError as error:
                if error.code != "source_not_found":
                    raise
        if registered:
            if request.ref is not None or request.package_path is not None:
                _fail(
                    "install_request_conflict",
                    "registered installs use the source ref and indexed package path",
                )
            with self._fetch_registered(
                source_name, package_id, cancelled=cancelled
            ) as fetched:
                yield fetched
            return
        with self._fetch_direct(request, cancelled=cancelled) as fetched:
            yield fetched

    def _transaction_candidate(
        self, fetched: _FetchedCandidate
    ) -> TransactionCandidate:
        return TransactionCandidate(
            distribution=fetched.distribution,
            identity=fetched.identity,
            source_name=fetched.source_name,
            repository_url=fetched.repository_url,
            configured_ref=fetched.configured_ref,
            resolved_commit=fetched.resolved_commit,
            package_path=fetched.package_path,
            destination=self.installed_store.package_root(fetched.identity),
        )

    def _assess_distribution(
        self,
        distribution: WorkflowDistribution,
        identity: InstalledPackageIdentity,
        source_name: str,
    ) -> tuple[list[WorkflowTrustReviewItem], list[PackageDiagnostic]]:
        files = {item.relative_path: item for item in distribution.files}
        sources = []
        members_by_path = {}
        try:
            for member in distribution.manifest.workflows:
                definition = files[member.definition]
                companion = files[member.companion] if member.companion else None
                binding = WorkflowMarketplaceBinding(
                    installation_key=f"{identity.source_key}/{identity.package_id}",
                    source_name=source_name,
                    package_id=identity.package_id,
                    package_version=distribution.manifest.version,
                    workflow_relative_path=member.definition,
                    distribution_digest=distribution.digest,
                )
                source = parse_workflow_source_bytes(
                    distribution.root.joinpath(*member.definition.split("/")),
                    workflow_bytes=definition.content,
                    sidecar_bytes=companion.content if companion else None,
                    sidecar_path=(
                        distribution.root.joinpath(*member.companion.split("/"))
                        if companion is not None and member.companion is not None
                        else None
                    ),
                    source="profile",
                    precedence=2,
                    package_root=distribution.root,
                    marketplace_binding=binding,
                )
                sources.append(source)
                members_by_path[member.definition] = (member, source)
            snapshot = WorkflowCatalogSnapshot.capture(sources)
            if snapshot.ambiguous_names:
                _fail(
                    "package_workflow_invalid",
                    "package contains ambiguous workflow names",
                )
            assessments = []
            for member in distribution.manifest.workflows:
                source = members_by_path[member.definition][1]
                compilation = compile_workflow(source, snapshot)
                assessments.append((
                    member,
                    compilation,
                    self.assessment_builder(compilation),
                ))
        except WorkflowMarketplaceError:
            raise
        except (
            KeyError,
            OSError,
            TypeError,
            UnicodeError,
            ValueError,
            ValidationError,
            WorkflowValidationError,
            yaml.YAMLError,
        ) as error:
            _fail(
                "package_workflow_invalid",
                (
                    "package workflow assessment is invalid"
                    if isinstance(error, OSError)
                    else _safe_message(error)
                ),
            )
        package_paths = sorted(distribution.covered_paths)
        mcp_disclosures: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}

        def inspect_mcp(reference: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
            candidates = (
                reference,
                f"mcp/{reference}",
                f"mcp/{reference.removesuffix('.yaml')}.yaml",
            )
            matched = next(
                (candidate for candidate in candidates if candidate in files), None
            )
            if matched is None:
                raise ValueError("declared MCP definition is unavailable")
            cached = mcp_disclosures.get(matched)
            if cached is None:
                encoded = files[matched].content
                if len(encoded) > 256_000:
                    raise ValueError("MCP definition exceeds 256000 bytes")
                document = yaml.safe_load(encoded.decode("utf-8")) or {}
                servers = normalize_mcp_server_document(
                    document,
                    default_name=Path(matched).stem,
                )
                cached = (
                    tuple(
                        sorted(
                            name
                            for name, server in servers.items()
                            if "command" in server
                        )
                    ),
                    tuple(
                        sorted(
                            name for name, server in servers.items() if "url" in server
                        )
                    ),
                )
                mcp_disclosures[matched] = cached
            return matched, cached[0], cached[1]

        rendered: list[WorkflowTrustReviewItem] = []
        blockers: list[PackageDiagnostic] = []
        for member, compilation, assessment in assessments:
            risk = assessment.risk
            compatibility: list[PackageDiagnostic] = []
            for finding in assessment.compatibility.findings:
                destination = finding.code in _DESTINATION_ADVISORY_CODES or any(
                    marker in finding.path.casefold()
                    for marker in ("provider", "requires", "worktree")
                )
                severity: Literal["blocker", "advisory"] = (
                    "advisory" if destination or not finding.blocking else "blocker"
                )
                diagnostic = PackageDiagnostic(
                    code=finding.code,
                    message=_safe_message(finding.message),
                    severity=severity,
                )
                compatibility.append(diagnostic)
                if severity == "blocker":
                    blockers.append(diagnostic)
            nodes = compilation.package.definition.nodes
            bindings = compilation.dependency_manifest.resources
            command_resources = sorted({
                binding.source_relative_path
                for binding in bindings
                if binding.resource_kind in {"command", "loop_command"}
            })
            script_resources = sorted({
                binding.source_relative_path
                for binding in bindings
                if binding.resource_kind == "named_script"
            })
            mcp_bindings = tuple(
                binding for binding in bindings if binding.resource_kind == "mcp"
            )
            mcp_resource_files = sorted({
                binding.source_relative_path
                for binding in bindings
                if binding.resource_kind == "mcp_resource"
            })
            mcp_resource_paths = {
                binding.source_relative_path for binding in mcp_bindings
            }
            local_mcp: set[str] = set()
            remote_mcp: set[str] = set()
            try:
                for reference in risk.local_mcp_servers:
                    matched, local_names, remote_names = inspect_mcp(reference)
                    mcp_resource_paths.add(matched)
                    local_mcp.update(local_names)
                    remote_mcp.update(remote_names)
                mcp_resources = sorted(mcp_resource_paths)
                item = WorkflowTrustReviewItem(
                    workflowName=compilation.package.definition.name,
                    definitionPath=member.definition,
                    companionPath=member.companion,
                    packageDigest=assessment.package_digest.sha256,
                    riskDigest=risk.risk_digest,
                    trustState=self.trust_store.check_read_only(
                        assessment.package_digest.sha256,
                        risk_digest=risk.risk_digest,
                    ),
                    shellOrScriptNodes=sorted(risk.shell_or_script_nodes),
                    commandNodes=sorted(
                        node.id for node in nodes if node.node_type == "command"
                    ),
                    approvalNodes=sorted(
                        node.id for node in nodes if node.node_type == "approval"
                    ),
                    commandResources=command_resources,
                    scriptResources=script_resources,
                    mcpResources=mcp_resources,
                    mcpResourceFiles=mcp_resource_files,
                    requestedTools=sorted(risk.requested_tools),
                    requestedSkills=sorted(risk.requested_skills),
                    localMcpServers=sorted(local_mcp),
                    remoteMcpServers=sorted(remote_mcp),
                    providers=sorted(risk.providers),
                    outwardActionNodes=sorted(risk.outward_action_nodes),
                    requiredSecrets=sorted(risk.required_secret_names),
                    externalRequirements=distribution.manifest.external_requirements,
                    packageResourceSet="package",
                    compatibility=compatibility,
                )
            except (
                KeyError,
                OSError,
                TypeError,
                UnicodeError,
                ValueError,
                ValidationError,
                yaml.YAMLError,
            ) as error:
                _fail(
                    "package_workflow_invalid",
                    (
                        "package workflow assessment is invalid"
                        if isinstance(error, OSError)
                        else _safe_message(error)
                    ),
                )
            rendered.append(item)
        return sorted(rendered, key=lambda item: item.workflow_name), sorted(
            blockers, key=lambda item: (item.code, item.message)
        )

    def _destination_advisories(
        self, requirements: ExternalRequirements
    ) -> list[PackageDiagnostic]:
        singular = {
            "runtimes": "runtime",
            "tools": "tool",
            "providers": "provider",
            "services": "service",
            "secrets": "secret",
        }
        diagnostics = []
        for field, label in singular.items():
            available = self.available_capabilities[field]
            for value in sorted(set(getattr(requirements, field)) - available):
                diagnostics.append(
                    PackageDiagnostic(
                        code=f"missing_{label}",
                        message=f"destination does not currently provide {label} {value!r}",
                        severity="advisory",
                    )
                )
        return diagnostics

    def _assessment(
        self,
        distribution: WorkflowDistribution,
        workflows: list[WorkflowTrustReviewItem],
        blockers: list[PackageDiagnostic],
        *,
        review_digest: str,
    ) -> PackageReviewAssessment:
        compatibility_advisories = [
            finding
            for workflow in workflows
            for finding in workflow.compatibility
            if finding.severity == "advisory"
        ]
        advisories = [
            *self._destination_advisories(distribution.manifest.external_requirements),
            *compatibility_advisories,
        ]
        unique_blockers = {
            (item.code, item.message, item.severity): item for item in blockers
        }
        unique_advisories = {
            (item.code, item.message, item.severity): item for item in advisories
        }
        return PackageReviewAssessment(
            packageDigest=distribution.digest,
            reviewDigest=review_digest,
            workflowNames=sorted(item.workflow_name for item in workflows),
            blockers=sorted(
                unique_blockers.values(), key=lambda item: (item.code, item.message)
            ),
            advisories=sorted(
                unique_advisories.values(), key=lambda item: (item.code, item.message)
            ),
            externalRequirements=distribution.manifest.external_requirements,
            packageResources=sorted(distribution.covered_paths),
        )

    def prepare_install(
        self,
        request: InstallRequest,
        *,
        actor: str,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> InstallReview:
        if not isinstance(request, InstallRequest):
            _fail("install_request_invalid", "install request is invalid")
        with self._fetch_request(request, cancelled=cancelled) as fetched:
            if self._optional_installed(fetched.identity) is not None:
                _fail(
                    "installed_package_conflict",
                    "package identity is already installed; use update",
                )
            workflows, blockers = self._assess_distribution(
                fetched.distribution, fetched.identity, fetched.source_name
            )
            if blockers:
                _fail(
                    "package_review_blocked",
                    "package has structural compatibility blockers",
                )
            changes = _file_changes(None, fetched.distribution)
            review_digest = _canonical_digest(
                _REVIEW_DOMAIN,
                {
                    "operation": "install",
                    "identity": fetched.identity.model_dump(mode="json", by_alias=True),
                    "source": fetched.source_name,
                    "repository": fetched.repository_url,
                    "ref": fetched.configured_ref,
                    "commit": fetched.resolved_commit,
                    "path": fetched.package_path,
                    "version": fetched.distribution.manifest.version,
                    "digest": fetched.distribution.digest,
                    "files": [
                        item.model_dump(mode="json", by_alias=True) for item in changes
                    ],
                    "workflows": [
                        self._trust_digest_projection(item) for item in workflows
                    ],
                },
            )
            assessment = self._assessment(
                fetched.distribution,
                workflows,
                blockers,
                review_digest=review_digest,
            )
            prepared = self.transactions.prepare(
                self._transaction_candidate(fetched),
                review_digest=review_digest,
                actor=actor,
                profile=self.profile,
            )
            review = InstallReview(
                operation="install",
                confirmationToken=prepared.token,
                reviewDigest=review_digest,
                identity=fetched.identity,
                sourceName=fetched.source_name,
                repositoryUrl=fetched.repository_url,
                configuredRef=fetched.configured_ref,
                resolvedCommit=fetched.resolved_commit,
                packagePath=fetched.package_path,
                candidateVersion=fetched.distribution.manifest.version,
                candidateDigest=fetched.distribution.digest,
                assessment=assessment,
                fileChanges=changes,
                workflowReviews=workflows,
            )
            self._remember_issued_review(review)
            return review

    @domain_mutation("install_confirm")
    def confirm_install(
        self,
        token: str,
        *,
        actor: str,
        cancelled: Callable[[], bool] = lambda: False,
        enter_atomic: Callable[[], bool] = lambda: True,
    ) -> InstalledPackage:
        prepared = self.transactions.consume(token, actor=actor, profile=self.profile)
        if prepared.operation != "install" or prepared.installed_provenance is not None:
            _fail("confirmation_token_invalid", "confirmation token is invalid")
        installed = self.transactions.atomic_install(
            prepared,
            review_digest=prepared.review_digest,
            trust_origin=self._trust_origin(prepared.identity),
            cancelled=cancelled,
            enter_atomic=enter_atomic,
        )
        return self._installed_projection(installed)

    @contextmanager
    def _fetch_update(
        self,
        installed: InstalledPackageProvenance,
        *,
        cancelled: Callable[[], bool],
    ):
        if installed.identity.source_key.startswith(_DIRECT_PREFIX):
            request = InstallRequest(
                identifier=installed.repository_url,
                ref=installed.configured_ref,
                packagePath=installed.package_path,
            )
            with self._fetch_direct(request, cancelled=cancelled) as fetched:
                if fetched.identity != installed.identity:
                    _fail(
                        "installed_identity_conflict",
                        "direct update resolves to a different package identity",
                    )
                yield fetched
            return
        with self._fetch_registered(
            installed.source_name,
            installed.identity.package_id,
            cancelled=cancelled,
        ) as fetched:
            if (
                fetched.identity != installed.identity
                or fetched.repository_url != installed.repository_url
            ):
                _fail(
                    "installed_identity_conflict",
                    "registered update resolves to a conflicting source identity",
                )
            yield fetched

    def prepare_update(
        self,
        identity: InstalledPackageIdentity,
        *,
        actor: str,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> UpdateReview:
        installed = self.installed_store.get(identity)
        destination = self.installed_store.package_root(identity)
        old_distribution = load_distribution(
            destination, expected_digest=installed.distribution_digest
        )
        old_workflows, old_blockers = self._assess_distribution(
            old_distribution, identity, installed.source_name
        )
        if old_blockers:
            _fail(
                "installed_package_invalid",
                "installed package has structural compatibility blockers",
            )
        with self._fetch_update(installed, cancelled=cancelled) as fetched:
            candidate_version = fetched.distribution.manifest.version
            comparison = (
                _semver_key(candidate_version),
                _semver_key(installed.package_version),
            )
            if comparison[0] < comparison[1]:
                _fail(
                    "package_version_regression",
                    "candidate package version is older than the installed version",
                )
            candidate_workflows, blockers = self._assess_distribution(
                fetched.distribution, identity, installed.source_name
            )
            if blockers:
                _fail(
                    "package_review_blocked",
                    "candidate package has structural compatibility blockers",
                )
            if (
                comparison[0] == comparison[1]
                and fetched.distribution.digest != installed.distribution_digest
            ):
                _fail(
                    "package_version_conflict",
                    "candidate changed bytes without advancing package version",
                )
            changes = _file_changes(old_distribution, fetched.distribution)
            workflow_changes = _set_change(
                (item.workflow_name for item in old_workflows),
                (item.workflow_name for item in candidate_workflows),
            )
            requirement_changes = _requirement_changes(
                old_distribution.manifest.external_requirements,
                fetched.distribution.manifest.external_requirements,
            )
            risk_changes = _risk_changes(old_workflows, candidate_workflows)
            compatibility_changes = _compatibility_changes(
                old_workflows, candidate_workflows
            )
            review_digest = _canonical_digest(
                _REVIEW_DOMAIN,
                {
                    "operation": "update",
                    "identity": identity.model_dump(mode="json", by_alias=True),
                    "old": installed.model_dump(mode="json", by_alias=True),
                    "candidateCommit": fetched.resolved_commit,
                    "candidateVersion": candidate_version,
                    "candidateDigest": fetched.distribution.digest,
                    "files": [
                        item.model_dump(mode="json", by_alias=True) for item in changes
                    ],
                    "workflowChanges": workflow_changes.model_dump(mode="json"),
                    "requirementChanges": requirement_changes.model_dump(mode="json"),
                    "riskChanges": risk_changes.model_dump(mode="json"),
                    "compatibilityChanges": compatibility_changes.model_dump(
                        mode="json"
                    ),
                },
            )
            assessment = self._assessment(
                fetched.distribution,
                candidate_workflows,
                blockers,
                review_digest=review_digest,
            )
            unchanged = fetched.distribution.digest == installed.distribution_digest
            token = None
            if not unchanged:
                token = self.transactions.prepare(
                    self._transaction_candidate(fetched),
                    review_digest=review_digest,
                    actor=actor,
                    profile=self.profile,
                ).token
            review = UpdateReview(
                operation="update",
                result="unchanged" if unchanged else "update_available",
                confirmationToken=token,
                reviewDigest=review_digest,
                identity=identity,
                sourceName=installed.source_name,
                repositoryUrl=installed.repository_url,
                configuredRef=installed.configured_ref,
                oldVersion=installed.package_version,
                candidateVersion=candidate_version,
                oldCommit=installed.resolved_commit,
                candidateCommit=fetched.resolved_commit,
                oldDigest=installed.distribution_digest,
                candidateDigest=fetched.distribution.digest,
                fileChanges=changes,
                workflowChanges=workflow_changes,
                requirementChanges=requirement_changes,
                riskChanges=risk_changes,
                compatibilityChanges=compatibility_changes,
                assessment=assessment,
                workflowReviews=candidate_workflows,
            )
            if token is not None:
                self._remember_issued_review(review)
            return review

    @domain_mutation("update_confirm")
    def confirm_update(
        self,
        token: str,
        *,
        actor: str,
        cancelled: Callable[[], bool] = lambda: False,
        enter_atomic: Callable[[], bool] = lambda: True,
    ) -> InstalledPackage:
        prepared = self.transactions.consume(token, actor=actor, profile=self.profile)
        previous = prepared.installed_provenance
        if prepared.operation != "install" or previous is None:
            _fail("confirmation_token_invalid", "confirmation token is invalid")
        origin = self._trust_origin(prepared.identity)

        def persist_provenance(provenance: InstalledPackageProvenance) -> None:
            self.installed_store.put(provenance)

        installed = self.transactions.atomic_install(
            prepared,
            review_digest=prepared.review_digest,
            trust_origin=origin,
            provenance_writer=persist_provenance,
            cancelled=cancelled,
            enter_atomic=enter_atomic,
        )
        return self._installed_projection(installed)

    def check_updates(
        self,
        identity: InstalledPackageIdentity | None = None,
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> tuple[UpdateCheck, ...]:
        installed_items = (
            (self.installed_store.get(identity),)
            if identity is not None
            else self.installed_store.list_installed()
        )
        checks = []
        for installed in installed_items:
            if installed.identity.source_key.startswith(_DIRECT_PREFIX):
                try:
                    with self._fetch_update(installed, cancelled=cancelled) as fetched:
                        _workflows, blockers = self._assess_distribution(
                            fetched.distribution,
                            installed.identity,
                            installed.source_name,
                        )
                        if blockers:
                            _fail(
                                "package_review_blocked",
                                "candidate package has structural compatibility blockers",
                            )
                        candidate_version = fetched.distribution.manifest.version
                        candidate_key = _semver_key(candidate_version)
                        installed_key = _semver_key(installed.package_version)
                        if candidate_key < installed_key:
                            _fail(
                                "package_version_regression",
                                "candidate package version is older than the installed version",
                            )
                        if (
                            candidate_key == installed_key
                            and fetched.distribution.digest
                            != installed.distribution_digest
                        ):
                            _fail(
                                "package_version_conflict",
                                "candidate changed bytes without advancing package version",
                            )
                        checks.append(
                            UpdateCheck.model_validate({
                                "identity": installed.identity,
                                "status": (
                                    "update_available"
                                    if candidate_key > installed_key
                                    else "current"
                                ),
                                "installedVersion": installed.package_version,
                                "candidateVersion": candidate_version,
                            })
                        )
                except WorkflowMarketplaceError as error:
                    checks.append(
                        UpdateCheck.model_validate({
                            "identity": installed.identity,
                            "status": "error",
                            "installedVersion": installed.package_version,
                            "candidateVersion": None,
                            "diagnosticCode": error.code,
                            "message": _safe_message(error, installed.repository_url),
                        })
                    )
                continue
            try:
                item = self.catalog.inspect(
                    f"{installed.source_name}/{installed.identity.package_id}"
                )
            except WorkflowMarketplaceError as error:
                if error.code in {"source_not_found", "catalog_package_not_found"}:
                    checks.append(
                        UpdateCheck(
                            identity=installed.identity,
                            status="orphaned",
                            installedVersion=installed.package_version,
                            candidateVersion=None,
                        )
                    )
                    continue
                raise
            checks.append(
                UpdateCheck(
                    identity=installed.identity,
                    status=(
                        "update_available"
                        if _semver_key(item.version)
                        > _semver_key(installed.package_version)
                        else "current"
                    ),
                    installedVersion=installed.package_version,
                    candidateVersion=item.version,
                )
            )
        return tuple(
            sorted(
                checks,
                key=lambda item: (
                    item.identity.source_key,
                    item.identity.package_id,
                ),
            )
        )

    def prepare_remove(
        self,
        identity: InstalledPackageIdentity,
        *,
        actor: str,
    ) -> RemoveReview:
        installed = self.installed_store.get(identity)
        destination = self.installed_store.package_root(identity)
        distribution = load_distribution(
            destination, expected_digest=installed.distribution_digest
        )
        workflow_names = sorted(
            item.workflow_name
            for item in self._assess_distribution(
                distribution, identity, installed.source_name
            )[0]
        )
        review_digest = _canonical_digest(
            _REVIEW_DOMAIN,
            {
                "operation": "remove",
                "provenance": installed.model_dump(mode="json", by_alias=True),
                "workflowNames": workflow_names,
            },
        )
        candidate = TransactionCandidate(
            distribution=distribution,
            identity=identity,
            source_name=installed.source_name,
            repository_url=installed.repository_url,
            configured_ref=installed.configured_ref,
            resolved_commit=installed.resolved_commit,
            package_path=installed.package_path,
            destination=destination,
            operation="remove",
            installed_provenance=installed,
        )
        prepared = self.transactions.prepare(
            candidate,
            review_digest=review_digest,
            actor=actor,
            profile=self.profile,
        )
        review = RemoveReview(
            operation="remove",
            confirmationToken=prepared.token,
            reviewDigest=review_digest,
            identity=identity,
            currentVersion=installed.package_version,
            currentCommit=installed.resolved_commit,
            distributionDigest=installed.distribution_digest,
            workflowNames=workflow_names,
        )
        self._remember_issued_review(review)
        return review

    @domain_mutation("remove_confirm")
    def confirm_remove(
        self,
        token: str,
        *,
        actor: str,
        cancelled: Callable[[], bool] = lambda: False,
        enter_atomic: Callable[[], bool] = lambda: True,
    ) -> InstalledPackage:
        prepared = self.transactions.consume(token, actor=actor, profile=self.profile)
        if prepared.operation != "remove" or prepared.installed_provenance is None:
            _fail("confirmation_token_invalid", "confirmation token is invalid")
        origin = self._trust_origin(prepared.identity)

        def remove_provenance(identity: InstalledPackageIdentity) -> None:
            self.installed_store.remove(
                identity, expected=prepared.installed_provenance
            )

        removed = self.transactions.atomic_remove(
            prepared,
            review_digest=prepared.review_digest,
            trust_origin=origin,
            provenance_remover=remove_provenance,
            cancelled=cancelled,
            enter_atomic=enter_atomic,
        )
        return self._installed_projection(removed)

    @staticmethod
    def _trust_origin(identity: InstalledPackageIdentity) -> str:
        return f"marketplace:{identity.source_key}/{identity.package_id}"

    @staticmethod
    def _trust_digest_projection(item: WorkflowTrustReviewItem) -> dict[str, object]:
        value = item.model_dump(mode="json", by_alias=True)
        value.pop("trustState")
        return value

    def _trust_review_parts(
        self,
        identity: InstalledPackageIdentity,
        *,
        workflow_name: str | None = None,
        workflow_paths: tuple[str, ...] | None = None,
        installed: InstalledPackageProvenance | None = None,
    ) -> tuple[
        InstalledPackageProvenance,
        list[WorkflowTrustReviewItem],
        str,
        list[str],
    ]:
        installed = installed or self.installed_store.get(identity)
        if installed.identity != identity:
            _fail(
                "trust_review_changed", "installed package changed after trust review"
            )
        distribution = load_distribution(
            self.installed_store.package_root(identity),
            expected_digest=installed.distribution_digest,
        )
        workflows, blockers = self._assess_distribution(
            distribution, identity, installed.source_name
        )
        if blockers:
            _fail("package_review_blocked", "installed package review is blocked")
        if workflow_name is not None:
            workflows = [
                item for item in workflows if item.workflow_name == workflow_name
            ]
        if workflow_paths is not None:
            selected = set(workflow_paths)
            workflows = [item for item in workflows if item.definition_path in selected]
            if {item.definition_path for item in workflows} != selected:
                _fail("trust_review_changed", "reviewed workflows changed")
        if not workflows:
            _fail("installed_workflow_not_found", "installed workflow was not found")
        review_digest = _canonical_digest(
            _TRUST_REVIEW_DOMAIN,
            {
                "identity": identity.model_dump(mode="json", by_alias=True),
                "source": installed.source_name,
                "version": installed.package_version,
                "commit": installed.resolved_commit,
                "distributionDigest": installed.distribution_digest,
                "packageResources": sorted(distribution.covered_paths),
                "workflows": [
                    self._trust_digest_projection(item) for item in workflows
                ],
            },
        )
        return installed, workflows, review_digest, sorted(distribution.covered_paths)

    def review_trust(
        self,
        identity: InstalledPackageIdentity,
        *,
        actor: str,
        workflow_name: str | None = None,
    ) -> TrustReview:
        installed, workflows, review_digest, package_resources = (
            self._trust_review_parts(identity, workflow_name=workflow_name)
        )
        token = self._trust_confirmations.issue(
            actor=actor,
            profile=self.profile,
            identity=identity,
            distribution_digest=installed.distribution_digest,
            review_digest=review_digest,
            workflow_paths=tuple(item.definition_path for item in workflows),
            workflow_name=workflow_name,
        )
        review = TrustReview(
            confirmationToken=token,
            reviewDigest=review_digest,
            identity=identity,
            sourceName=installed.source_name,
            version=installed.package_version,
            resolvedCommit=installed.resolved_commit,
            distributionDigest=installed.distribution_digest,
            packageResources=package_resources,
            workflows=workflows,
        )
        self._remember_issued_review(review)
        return review

    @domain_mutation("trust_confirm")
    def grant_trust(
        self,
        review_or_token: TrustReview | str,
        *,
        actor: str,
        cancelled: Callable[[], bool] = lambda: False,
        enter_atomic: Callable[[], bool] = lambda: True,
    ) -> dict[str, Literal["trusted", "untrusted"]]:
        token = (
            review_or_token.confirmation_token
            if isinstance(review_or_token, TrustReview)
            else review_or_token
        )
        try:
            with self.catalog.source_store._locked() as parent_identity:
                authorization = self._trust_confirmations.consume_locked(
                    token,
                    actor=actor,
                    profile=self.profile,
                    parent_identity=parent_identity,
                )
                _existed, installed_state = self.installed_store._snapshot()
                installed = next(
                    (
                        record.provenance
                        for record in installed_state.packages
                        if record.provenance.identity == authorization.identity
                    ),
                    None,
                )
                if installed is None:
                    _fail(
                        "trust_review_changed",
                        "installed package changed after trust review",
                    )
                installed, workflows, current_digest, _package_resources = (
                    self._trust_review_parts(
                        authorization.identity,
                        workflow_paths=authorization.workflow_paths,
                        installed=installed,
                    )
                )
                if (
                    installed.distribution_digest != authorization.distribution_digest
                    or current_digest != authorization.review_digest
                ):
                    _fail(
                        "trust_review_changed",
                        "installed package changed after trust review",
                    )
                origin = self._trust_origin(authorization.identity)
                if cancelled() or not enter_atomic():
                    _fail(
                        "marketplace_operation_cancelled",
                        "marketplace operation was cancelled",
                    )
                states = {}

                def capture(payload):
                    state = _capture_trust(self, authorization.identity, payload)
                    if state.trust is None:
                        _fail(
                            "trust_state_unverified",
                            "package trust state is unconfirmed",
                        )
                    states.update({
                        item.workflow_name: item.state for item in state.trust.workflows
                    })

                self.trust_store.trust_origin_many(
                    tuple(
                        (item.package_digest, item.risk_digest) for item in workflows
                    ),
                    actor=actor,
                    origin=origin,
                    observe=capture,
                )
        except WorkflowLockTimeout as error:
            _fail("trust_confirmation_lock_timeout", str(error))
        return states

    @domain_mutation("trust_revoke")
    def revoke_trust(
        self,
        identity: InstalledPackageIdentity,
        *,
        workflow_name: str | None = None,
        cancelled: Callable[[], bool] = lambda: False,
        enter_atomic: Callable[[], bool] = lambda: True,
    ) -> int:
        try:
            with self.catalog.source_store._locked():
                _existed, state = self.installed_store._snapshot()
                installed = next(
                    (
                        record.provenance
                        for record in state.packages
                        if record.provenance.identity == identity
                    ),
                    None,
                )
                if installed is None:
                    _fail("installed_package_not_found", "package is not installed")
                _installed, workflows, _review_digest, _package_resources = (
                    self._trust_review_parts(
                        identity,
                        workflow_name=workflow_name,
                        installed=installed,
                    )
                )
                origin = self._trust_origin(identity)
                if cancelled() or not enter_atomic():
                    _fail(
                        "marketplace_operation_cancelled",
                        "marketplace operation was cancelled",
                    )
                return self.trust_store.revoke_origin_many(
                    (item.package_digest for item in workflows),
                    origin,
                    observe=lambda payload: _capture_trust(self, identity, payload),
                )
        except WorkflowLockTimeout as error:
            _fail("trust_confirmation_lock_timeout", str(error))

    def workflow_trust(
        self, identity: InstalledPackageIdentity
    ) -> dict[str, Literal["trusted", "untrusted"]]:
        _installed, workflows, _review_digest, _package_resources = (
            self._trust_review_parts(identity)
        )
        return {
            item.workflow_name: self.trust_store.check_read_only(
                item.package_digest,
                risk_digest=item.risk_digest,
            )
            for item in workflows
        }


__all__ = [
    "ConfirmationTargetMetadata",
    "WorkflowMarketplaceService",
    "WorkflowMarketplaceSourceListing",
]
