"""Bounded, profile-scoped background operations for the marketplace API."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import secrets
import threading
from collections.abc import Callable
from types import MappingProxyType
from typing import Annotated, Literal, NoReturn, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    TypeAdapter,
    field_validator,
    model_validator,
)

from hermes_cli.git_source import GitSourceError, validate_credential_free_git_source

from .lifecycle_models import (
    CancelledBeforeCommitOutcome,
    KnownUnchangedOutcome,
    LifecycleOperation,
    LifecycleOutcome,
    LifecyclePublicError,
    LifecycleResult,
    LifecycleSubject,
    OutcomeUnknown,
    PackageSubject,
    StrictLifecycleModel,
    TrustSelection,
    RESULT_TYPE_BY_KIND,
    RUNNING_PHASES_BY_KIND,
    require_result_kind,
)
from .admissions import (
    AdmissionReceipt,
    LifecycleAdmissionStore,
    MarketplaceOperationRegistryError,
    canonical_profile_key,
)
from .models import (
    InstallReview,
    InstalledPackage,
    InstalledPackageIdentity,
    PackageInspection,
    RemoveReview,
    SOURCE_NAME_PATTERN,
    TrustReview,
    UpdateCheck,
    UpdateReview,
)

OperationState = Literal["pending", "running", "succeeded", "failed", "cancelled"]
_TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})
_OPERATION_ID = re.compile(r"^wmop_[0-9a-f]{12}_[0-9a-f]{32}$", re.ASCII)
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,127}$", re.ASCII)
_RESULT_BYTES_MAX = 2 * 1024 * 1024
_V1_KIND_ALIASES = MappingProxyType({"package_detail": "inspect"})
_V1_PHASE_ADDITIONS = MappingProxyType({"refresh": frozenset({"verifying"})})


def _lifecycle_kind(kind: str) -> str:
    return _V1_KIND_ALIASES.get(kind, kind)


def _registry_running_phases(kind: str) -> frozenset[str]:
    lifecycle_kind = _lifecycle_kind(kind)
    return RUNNING_PHASES_BY_KIND[lifecycle_kind] | _V1_PHASE_ADDITIONS.get(
        kind, frozenset()
    )


class _StrictOperationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _OperationResultBase(_StrictOperationModel):
    """Shared byte bound for one closed operation-result variant."""

    @model_validator(mode="after")
    def require_bounded_json(self) -> "_OperationResultBase":
        try:
            rendered = json.dumps(
                self.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("operation result must be finite JSON") from error
        if len(rendered) > _RESULT_BYTES_MAX:
            raise ValueError("operation result exceeds its byte limit")
        return self


class MarketplaceSourceRefreshValue(_StrictOperationModel):
    source_name: str = Field(min_length=1, max_length=64, pattern=SOURCE_NAME_PATTERN)
    repository_url: str = Field(min_length=1, max_length=4096)
    state: Literal[
        "fresh",
        "stale",
        "disabled",
        "authentication-failed",
        "malformed",
        "incompatible",
        "unavailable",
        "cancelled",
    ]
    resolved_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    verified_at: str | None = Field(default=None, min_length=20, max_length=64)
    package_count: StrictInt = Field(ge=0, le=4096)
    diagnostic_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_IDENTIFIER.pattern,
    )
    message: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str) -> str:
        try:
            validate_credential_free_git_source(value)
        except GitSourceError as error:
            raise ValueError("repository URL is invalid") from error
        return value


class MarketplaceUpdateChecksValue(_StrictOperationModel):
    checks: list[UpdateCheck] = Field(max_length=512)


class MarketplaceTrustState(_StrictOperationModel):
    workflow_name: str = Field(min_length=1, max_length=256)
    state: Literal["trusted", "untrusted"]


class MarketplaceTrustStatesValue(_StrictOperationModel):
    workflows: list[MarketplaceTrustState] = Field(max_length=512)


class MarketplaceTrustRevocationValue(_StrictOperationModel):
    revoked: StrictInt = Field(ge=0, le=512)


class MarketplaceSourceRefreshOperationResult(_OperationResultBase):
    type: Literal["source_refresh"]
    value: MarketplaceSourceRefreshValue


class MarketplacePackageDetailOperationResult(_OperationResultBase):
    type: Literal["package_detail"]
    value: PackageInspection


class MarketplaceUpdateChecksOperationResult(_OperationResultBase):
    type: Literal["update_checks"]
    value: MarketplaceUpdateChecksValue


class MarketplaceInstallReviewOperationResult(_OperationResultBase):
    type: Literal["install_review"]
    value: InstallReview


class MarketplaceInstalledPackageOperationResult(_OperationResultBase):
    type: Literal["installed_package"]
    value: InstalledPackage


class MarketplaceUpdateReviewOperationResult(_OperationResultBase):
    type: Literal["update_review"]
    value: UpdateReview


class MarketplaceUpdatedPackageOperationResult(_OperationResultBase):
    type: Literal["updated_package"]
    value: InstalledPackage


class MarketplaceRemoveReviewOperationResult(_OperationResultBase):
    type: Literal["remove_review"]
    value: RemoveReview


class MarketplaceRemovedPackageOperationResult(_OperationResultBase):
    type: Literal["removed_package"]
    value: InstalledPackage


class MarketplaceTrustReviewOperationResult(_OperationResultBase):
    type: Literal["trust_review"]
    value: TrustReview


class MarketplaceTrustGrantOperationResult(_OperationResultBase):
    type: Literal["trust_grant"]
    value: MarketplaceTrustStatesValue


class MarketplaceTrustRevokeOperationResult(_OperationResultBase):
    type: Literal["trust_revoke"]
    value: MarketplaceTrustRevocationValue


MarketplaceOperationResult: TypeAlias = Annotated[
    MarketplaceSourceRefreshOperationResult
    | MarketplacePackageDetailOperationResult
    | MarketplaceUpdateChecksOperationResult
    | MarketplaceInstallReviewOperationResult
    | MarketplaceInstalledPackageOperationResult
    | MarketplaceUpdateReviewOperationResult
    | MarketplaceUpdatedPackageOperationResult
    | MarketplaceRemoveReviewOperationResult
    | MarketplaceRemovedPackageOperationResult
    | MarketplaceTrustReviewOperationResult
    | MarketplaceTrustGrantOperationResult
    | MarketplaceTrustRevokeOperationResult,
    Field(discriminator="type"),
]
_OPERATION_RESULT_ADAPTER = TypeAdapter(MarketplaceOperationResult)


def validate_marketplace_operation_result(value: object) -> MarketplaceOperationResult:
    """Strictly validate and detach one closed result projection."""

    validated = _OPERATION_RESULT_ADAPTER.validate_python(value)
    return _OPERATION_RESULT_ADAPTER.validate_python(
        validated.model_dump(mode="json", by_alias=True)
    )


class MarketplaceOperationPublicError(_StrictOperationModel):
    code: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER.pattern)
    message: Literal["Workflow marketplace operation failed."] = (
        "Workflow marketplace operation failed."
    )


class MarketplaceOperation(_StrictOperationModel):
    """Immutable public projection of one actor-owned operation."""

    schema_version: Literal[1] = Field(alias="schemaVersion")
    id: str = Field(pattern=_OPERATION_ID.pattern)
    kind: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER.pattern)
    source_name: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=SOURCE_NAME_PATTERN
    )
    profile: str = Field(min_length=1, max_length=256)
    state: OperationState
    phase: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER.pattern)
    progress: StrictInt = Field(ge=0, le=100)
    created_at: str = Field(alias="createdAt", min_length=20, max_length=64)
    started_at: str | None = Field(
        default=None, alias="startedAt", min_length=20, max_length=64
    )
    updated_at: str = Field(alias="updatedAt", min_length=20, max_length=64)
    finished_at: str | None = Field(
        default=None, alias="finishedAt", min_length=20, max_length=64
    )
    result: MarketplaceOperationResult | None = None
    error: MarketplaceOperationPublicError | None = None

    @field_validator("created_at", "started_at", "updated_at", "finished_at")
    @classmethod
    def validate_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("operation timestamp must be canonical UTC") from error
        canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        if not value.endswith("Z") or canonical != value:
            raise ValueError("operation timestamp must be canonical UTC")
        return value

    @model_validator(mode="after")
    def validate_state_shape(self) -> "MarketplaceOperation":
        if (self.kind == "refresh") != (self.source_name is not None):
            raise ValueError("operation source identity is inconsistent")
        if (
            self.state == "succeeded"
            and self.kind == "refresh"
            and isinstance(self.result, MarketplaceSourceRefreshOperationResult)
            and self.result.value.source_name != self.source_name
        ):
            raise ValueError("refresh operation result source is inconsistent")
        if self.state == "pending":
            if (
                self.started_at is not None
                or self.finished_at is not None
                or self.result is not None
                or self.error is not None
                or self.progress != 0
            ):
                raise ValueError("pending operation projection is inconsistent")
        elif self.state == "running":
            if (
                self.started_at is None
                or self.finished_at is not None
                or self.result is not None
                or self.error is not None
            ):
                raise ValueError("running operation projection is inconsistent")
        elif self.state == "succeeded":
            if (
                self.started_at is None
                or self.finished_at is None
                or self.result is None
                or self.error is not None
                or self.progress != 100
            ):
                raise ValueError("successful operation projection is inconsistent")
        elif self.state == "failed":
            if (
                self.started_at is None
                or self.finished_at is None
                or self.result is not None
                or self.error is None
            ):
                raise ValueError("failed operation projection is inconsistent")
        elif (
            self.finished_at is None
            or self.result is not None
            or self.error is not None
        ):
            raise ValueError("cancelled operation projection is inconsistent")
        return self


class MarketplaceOperationCapacityError(MarketplaceOperationRegistryError):
    pass


class MarketplaceOperationConflictError(MarketplaceOperationRegistryError):
    pass


class MarketplaceOperationNotFoundError(MarketplaceOperationRegistryError):
    pass


class MarketplaceOperationCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReviewTokenMetadata:
    """Private authority supplied by a preparation's service boundary."""

    confirmation_token: str = field(repr=False)
    expires_at: str
    review_digest: str
    subject: LifecycleSubject
    selection: TrustSelection | None
    validate_unused: Callable[[str], bool] = field(repr=False)


@dataclass(frozen=True, slots=True)
class LifecycleCompletion:
    """Explicit worker evidence; validated against the reserved operation."""

    state: Literal["succeeded", "failed"]
    result: LifecycleResult | None
    error: LifecyclePublicError | None
    outcome: LifecycleOutcome
    review_token: ReviewTokenMetadata | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class LegacyLifecycleCompletion:
    """V1 read wire result paired with its authoritative domain completion."""

    completion: LifecycleCompletion
    result: MarketplaceOperationResult | None


class AdmissionFound(StrictLifecycleModel):
    state: Literal["found"] = "found"
    operation: LifecycleOperation


class AdmissionEvicted(StrictLifecycleModel):
    state: Literal["evicted"] = "evicted"
    operation_id: str
    request_id: str
    registry_epoch: str
    profile: str
    kind: str
    subject: LifecycleSubject
    selection: TrustSelection | None


class LifecycleOperationPage(StrictLifecycleModel):
    items: tuple[LifecycleOperation, ...]
    next_cursor: str | None
    complete: bool


class ReviewTokenResponse(StrictLifecycleModel):
    operation_id: str
    request_id: str
    subject: LifecycleSubject
    selection: TrustSelection | None
    review_digest: str
    confirmation_token: str = Field(repr=False)
    expires_at: str


@dataclass(frozen=True, slots=True)
class _ListSnapshot:
    actor: str
    expires_at: datetime
    entries: tuple[bytes, ...]
    cursors: dict[str, int]


_SNAPSHOT_RECORDS_MAX = 1088
_SNAPSHOTS_MAX = 4
_SNAPSHOT_BYTES_MAX = 16 * 1024 * 1024


def _cancelled() -> NoReturn:
    raise MarketplaceOperationCancelled("marketplace operation was cancelled")


class CancellationToken:
    """Cooperative token with an indivisible cancellation/atomic boundary."""

    def __init__(self, progress: Callable[[str, int], None]):
        self._lock = threading.Lock()
        self._cancel_requested = False
        self._atomic_started = False
        self._committed = False
        self._progress = progress

    def cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancel_requested and not self._atomic_started

    def checkpoint(self) -> None:
        if self.is_cancelled():
            _cancelled()

    def set_progress(self, phase: str, progress: int) -> None:
        self.checkpoint()
        self._progress(phase, progress)

    def begin_atomic(self) -> None:
        """Enter an atomic mutation only if cancellation has not already won."""

        if not self.enter_atomic():
            _cancelled()

    def enter_atomic(self) -> bool:
        """Atomically arbitrate cancellation against the durable mutation."""

        with self._lock:
            if self._cancel_requested:
                return False
            if self._atomic_started:
                raise RuntimeError("atomic marketplace mutation already started")
            self._atomic_started = True
        self._progress("committing", 90)
        return True

    def mark_committed(self) -> None:
        with self._lock:
            if not self._atomic_started:
                raise RuntimeError("marketplace mutation was not started")
            self._committed = True

    @property
    def cancellation_requested(self) -> bool:
        with self._lock:
            return self._cancel_requested

    @property
    def atomic_started(self) -> bool:
        with self._lock:
            return self._atomic_started

    @property
    def committed(self) -> bool:
        with self._lock:
            return self._committed


@dataclass(slots=True)
class _OperationRecord:
    sequence: int
    id: str
    kind: str
    actor: str
    target: str | None
    state: OperationState
    phase: str
    progress: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result: MarketplaceOperationResult | LifecycleResult | None
    error: MarketplaceOperationPublicError | LifecyclePublicError | None
    cancellation: CancellationToken
    future: Future[None] | None = None
    receipt: AdmissionReceipt | None = None
    lifecycle: bool = False
    outcome: LifecycleOutcome | None = None
    legacy_result: MarketplaceOperationResult | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _clean_identifier(value: object, *, label: str, maximum: int = 64) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or _IDENTIFIER.fullmatch(value) is None
    ):
        raise ValueError(f"{label} is invalid")
    return value


def _refresh_source_name(kind: str, target: str | None) -> str | None:
    """Project only the canonical public source identity for refresh operations."""

    if kind != "refresh":
        return None
    prefix = "source:"
    if target is None or not target.startswith(prefix):
        raise ValueError("refresh operation target is invalid")
    source_name = target[len(prefix) :]
    if re.fullmatch(SOURCE_NAME_PATTERN, source_name, flags=re.ASCII) is None:
        raise ValueError("refresh operation target is invalid")
    return source_name


def _clean_identity(value: object, *, label: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value != value.strip()
        or "\0" in value
    ):
        raise ValueError(f"{label} is invalid")
    return value


class WorkflowMarketplaceOperationRegistry:
    """Thread-safe bounded operation registry for exactly one backend profile."""

    def __init__(
        self,
        *,
        profile_key: str,
        profile: str,
        max_workers: int = 3,
        max_in_flight: int = 12,
        max_terminal: int = 128,
        terminal_ttl: timedelta = timedelta(hours=1),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        admissions: LifecycleAdmissionStore | None = None,
    ):
        self.profile_key = canonical_profile_key(profile_key)
        self.profile = _clean_identity(profile, label="operation profile", maximum=256)
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or max_workers < 1
            or max_workers > 16
            or isinstance(max_in_flight, bool)
            or not isinstance(max_in_flight, int)
            or max_in_flight < max_workers
            or max_in_flight > 64
            or isinstance(max_terminal, bool)
            or not isinstance(max_terminal, int)
            or max_terminal < 1
            or max_terminal > 1024
            or not isinstance(terminal_ttl, timedelta)
            or not 0 < terminal_ttl.total_seconds() <= 7 * 24 * 60 * 60
        ):
            raise ValueError("operation registry limits are invalid")
        self.max_in_flight = max_in_flight
        self.max_terminal = max_terminal
        self.terminal_ttl = terminal_ttl
        self.clock = clock
        self._profile_digest = hashlib.sha256(self.profile_key.encode()).hexdigest()[
            :12
        ]
        self.admissions = admissions or LifecycleAdmissionStore(
            profile_key=self.profile_key, clock=clock
        )
        if self.admissions.profile_key != self.profile_key:
            raise ValueError("admission profile is inconsistent")
        self._lock = self.admissions.lock
        # Workers never acquire this gate. Authorization may acquire a domain
        # lock whose progress callback needs _lock, so it must run outside _lock.
        self._authorization_gate = threading.Lock()
        self._snapshots: list[_ListSnapshot] = []
        self._review_tokens: dict[str, ReviewTokenMetadata] = {}
        self._records: dict[str, _OperationRecord] = {}
        self._active_targets: dict[str, str] = {}
        self._sequence = 0
        self._closed = False
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=f"workflow-marketplace-{self._profile_digest}",
        )

    def _now(self) -> datetime:
        return _utc(self.clock())

    def _operation_id(self) -> str:
        return f"wmop_{self._profile_digest}_{secrets.token_hex(16)}"

    def _prune_locked(self, now: datetime) -> None:
        terminal = [
            record
            for record in self._records.values()
            if record.state in _TERMINAL_STATES
        ]
        expired = {
            record.id
            for record in terminal
            if record.finished_at is not None
            and now - record.finished_at > self.terminal_ttl
        }
        survivors = [record for record in terminal if record.id not in expired]
        over_limit = max(0, len(survivors) - self.max_terminal)
        evicted = {
            record.id
            for record in sorted(
                survivors,
                key=lambda item: (item.finished_at or item.created_at, item.sequence),
            )[:over_limit]
        }
        for operation_id in expired | evicted:
            self._records.pop(operation_id, None)
            self._review_tokens.pop(operation_id, None)
        self.admissions.prune()
        self._snapshots = [
            snapshot for snapshot in self._snapshots if snapshot.expires_at > now
        ]
        for operation_id, token in tuple(self._review_tokens.items()):
            if datetime.fromisoformat(token.expires_at.replace("Z", "+00:00")) <= now:
                self._review_tokens.pop(operation_id, None)

    def _project_lifecycle_locked(self, record: _OperationRecord) -> LifecycleOperation:
        receipt = record.receipt
        if receipt is None:
            raise MarketplaceOperationRegistryError(
                "marketplace_operation_unavailable",
                "Lifecycle projection is unavailable.",
            )
        try:
            return LifecycleOperation.model_validate({
                "schema_version": 2,
                "id": record.id,
                "registry_epoch": self.admissions.epoch,
                "request_id": receipt.request_id,
                "kind": receipt.kind,
                "subject": receipt.subject.model_dump(mode="json"),
                "selection": receipt.selection.model_dump(mode="json")
                if receipt.selection
                else None,
                "profile": self.profile,
                "state": record.state,
                "phase": (
                    "fetching"
                    if not record.lifecycle
                    and record.kind == "refresh"
                    and record.phase == "verifying"
                    else record.phase
                ),
                "progress": record.progress,
                "created_at": _timestamp(record.created_at),
                "started_at": _timestamp(record.started_at)
                if record.started_at
                else None,
                "updated_at": _timestamp(record.updated_at),
                "finished_at": _timestamp(record.finished_at)
                if record.finished_at
                else None,
                "result": record.result.model_dump(mode="json")
                if record.result
                else None,
                "error": record.error.model_dump(mode="json") if record.error else None,
                "outcome": record.outcome.model_dump(mode="json")
                if record.outcome
                else None,
            })
        except Exception:
            raise MarketplaceOperationRegistryError(
                "marketplace_operation_unavailable",
                "Lifecycle projection is unavailable.",
            ) from None

    def _project_receipt_locked(self, receipt):
        record = self._records.get(receipt.operation_id)
        if record is not None:
            return self._project_lifecycle_locked(record)
        return AdmissionEvicted(
            operation_id=receipt.operation_id,
            request_id=receipt.request_id,
            registry_epoch=self.admissions.epoch,
            profile=self.profile,
            kind=receipt.kind,
            subject=receipt.subject.model_copy(deep=True),
            selection=receipt.selection.model_copy(deep=True)
            if receipt.selection
            else None,
        )

    def _project_locked(self, record: _OperationRecord) -> MarketplaceOperation:
        result = record.legacy_result if record.receipt is not None else record.result
        return MarketplaceOperation.model_validate({
            "schemaVersion": 1,
            "id": record.id,
            "kind": record.kind,
            "source_name": _refresh_source_name(record.kind, record.target),
            "profile": self.profile,
            "state": record.state,
            "phase": record.phase,
            "progress": record.progress,
            "createdAt": _timestamp(record.created_at),
            "startedAt": (
                _timestamp(record.started_at) if record.started_at is not None else None
            ),
            "updatedAt": _timestamp(record.updated_at),
            "finishedAt": (
                _timestamp(record.finished_at)
                if record.finished_at is not None
                else None
            ),
            "result": (
                result.model_dump(mode="json", by_alias=True)
                if result is not None
                else None
            ),
            "error": (
                record.error.model_dump(mode="json")
                if record.error is not None
                else None
            ),
        })

    def _require_locked(self, operation_id: str, actor: str) -> _OperationRecord:
        record = self._records.get(operation_id)
        if record is None or record.actor != actor:
            raise MarketplaceOperationNotFoundError(
                "marketplace_operation_not_found",
                "marketplace operation was not found",
            )
        return record

    def _release_target_locked(self, record: _OperationRecord) -> None:
        if (
            record.target is not None
            and self._active_targets.get(record.target) == record.id
        ):
            self._active_targets.pop(record.target, None)

    def _terminal_locked(
        self,
        record: _OperationRecord,
        *,
        state: Literal["succeeded", "failed", "cancelled"],
        result: MarketplaceOperationResult | LifecycleResult | None = None,
        error: MarketplaceOperationPublicError | LifecyclePublicError | None = None,
        outcome: LifecycleOutcome | None = None,
        legacy_result: MarketplaceOperationResult | None = None,
    ) -> None:
        if record.state in _TERMINAL_STATES:
            return
        now = self._now()
        previous_progress = record.progress
        record.state = state
        record.phase = {
            "succeeded": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }[state]
        record.progress = 100 if state == "succeeded" else record.progress
        record.updated_at = now
        record.finished_at = now
        record.result = result
        record.error = error
        record.outcome = outcome
        record.legacy_result = legacy_result
        if record.receipt is not None:
            if state == "cancelled":
                if record.cancellation.atomic_started:
                    record.state = "failed"
                    record.phase = "failed"
                    record.error = LifecyclePublicError(
                        code="marketplace_operation_failed"
                    )
                    record.outcome = OutcomeUnknown(
                        type="outcome_unknown", reason="terminal_invalid"
                    )
                else:
                    record.outcome = CancelledBeforeCommitOutcome(
                        type="cancelled_before_commit", package_state=None
                    )
            if record.outcome is None:
                record.outcome = OutcomeUnknown(
                    type="outcome_unknown", reason="terminal_invalid"
                )
            try:
                validated = self._project_lifecycle_locked(record)
                record.result = validated.result
                record.error = validated.error
                record.outcome = validated.outcome
                if not record.lifecycle:
                    legacy = self._project_locked(record)
                    if legacy.result is not None and (
                        legacy.result.model_dump(mode="json", by_alias=False)
                        != validated.result.model_dump(mode="json")
                    ):
                        raise ValueError("legacy completion result is inconsistent")
                    record.legacy_result = legacy.result
                # Only a fully validated no-write completion can still yield to
                # cancellation. Decide under the publication lock, after both
                # wire forms agree; commit, rollback and uncertainty stay final.
                if (
                    isinstance(validated.outcome, KnownUnchangedOutcome)
                    and validated.outcome.evidence in {"read_only", "before_mutation"}
                    and not record.cancellation.atomic_started
                    and record.cancellation.cancellation_requested
                ):
                    record.state = "cancelled"
                    record.phase = "cancelled"
                    record.progress = previous_progress
                    record.result = None
                    record.legacy_result = None
                    record.error = None
                    record.outcome = CancelledBeforeCommitOutcome(
                        type="cancelled_before_commit", package_state=None
                    )
                    self._project_lifecycle_locked(record)
                    if not record.lifecycle:
                        self._project_locked(record)
            except Exception:
                record.state = "failed"
                record.phase = "failed"
                record.progress = min(record.progress, 99)
                record.result = None
                record.legacy_result = None
                record.error = LifecyclePublicError(code="marketplace_operation_failed")
                record.outcome = OutcomeUnknown(
                    type="outcome_unknown", reason="terminal_invalid"
                )
        if record.receipt is not None:
            self.admissions.finish(record.receipt)
        self._release_target_locked(record)
        self._prune_locked(now)

    def _progress(self, operation_id: str, phase: str, progress: int) -> None:
        phase = _clean_identifier(phase, label="operation phase")
        if isinstance(progress, bool) or not isinstance(progress, int):
            raise ValueError("operation progress is invalid")
        if not 0 <= progress < 100:
            raise ValueError("operation progress is invalid")
        with self._lock:
            record = self._records.get(operation_id)
            if record is None or record.state != "running":
                return
            allowed_phases = (
                RUNNING_PHASES_BY_KIND[record.receipt.kind]
                if record.lifecycle
                else _registry_running_phases(record.kind)
            )
            if phase not in allowed_phases:
                raise ValueError("operation phase is invalid for its kind")
            if progress < record.progress:
                raise ValueError("operation progress cannot move backwards")
            record.phase = phase
            record.progress = progress
            record.updated_at = self._now()

    def start(
        self,
        kind: str,
        call: Callable[
            [CancellationToken],
            MarketplaceOperationResult
            | LifecycleCompletion
            | LegacyLifecycleCompletion,
        ],
        *,
        actor: str = "operator",
        target: str | None = None,
        request_id: str | None = None,
        canonical_body: object = None,
        subject: LifecycleSubject | None = None,
        selection: TrustSelection | None = None,
        authorize: Callable[[], None] | None = None,
    ) -> MarketplaceOperation | LifecycleOperation | AdmissionEvicted:
        with self._authorization_gate:
            if authorize is not None:
                with self._lock:
                    self._prune_locked(self._now())
                    if request_id is not None:
                        receipt = self.admissions.lookup(
                            actor, self.profile_key, request_id
                        )
                        if receipt is not None:
                            receipt.require_same_request(
                                _lifecycle_kind(kind),
                                canonical_body,
                                subject,
                                selection,
                            )
                            return self._project_receipt_locked(receipt)
                        self.admissions.require_new_request_window(request_id)
                authorize()
            return self._start(
                kind,
                call,
                actor=actor,
                target=target,
                request_id=request_id,
                canonical_body=canonical_body,
                subject=subject,
                selection=selection,
            )

    def _start(
        self,
        kind: str,
        call: Callable[
            [CancellationToken],
            MarketplaceOperationResult
            | LifecycleCompletion
            | LegacyLifecycleCompletion,
        ],
        *,
        actor: str = "operator",
        target: str | None = None,
        request_id: str | None = None,
        canonical_body: object = None,
        subject: LifecycleSubject | None = None,
        selection: TrustSelection | None = None,
    ) -> MarketplaceOperation | LifecycleOperation | AdmissionEvicted:
        kind = _clean_identifier(kind, label="operation kind")
        if _lifecycle_kind(kind) not in RESULT_TYPE_BY_KIND:
            raise ValueError("operation kind is invalid")
        actor = _clean_identity(actor, label="operation actor", maximum=256)
        if not callable(call):
            raise TypeError("operation call must be callable")
        if target is not None:
            target = _clean_identity(target, label="operation target", maximum=512)
        lifecycle = request_id is not None
        if not lifecycle:
            _refresh_source_name(kind, target)
            if (
                kind == "refresh"
                and subject is not None
                and (
                    subject.type != "source"
                    or subject.source_name != _refresh_source_name(kind, target)
                )
            ):
                raise ValueError("legacy refresh subject is inconsistent")
        with self._lock:
            now = self._now()
            self._prune_locked(now)
            receipt = None
            if lifecycle or subject is not None:
                if request_id is None:
                    request_id = self.admissions.new_request_id()
                receipt = self.admissions.lookup(actor, self.profile_key, request_id)
                if receipt is not None:
                    receipt.require_same_request(
                        _lifecycle_kind(kind), canonical_body, subject, selection
                    )
                    return self._project_receipt_locked(receipt)
                self.admissions.require_new_request_window(request_id)
            if self._closed:
                raise MarketplaceOperationCapacityError(
                    "marketplace_operation_unavailable",
                    "marketplace operation registry is closed",
                )
            in_flight = sum(
                record.state not in _TERMINAL_STATES
                for record in self._records.values()
            )
            if in_flight >= self.max_in_flight:
                raise MarketplaceOperationCapacityError(
                    "marketplace_operation_capacity",
                    "marketplace operation capacity is full",
                )
            if target is not None and target in self._active_targets:
                raise MarketplaceOperationConflictError(
                    "marketplace_operation_conflict",
                    "a conflicting marketplace operation is already active",
                )
            if request_id is not None:
                receipt = self.admissions.reserve(
                    request_id,
                    actor,
                    self.profile_key,
                    _lifecycle_kind(kind),
                    canonical_body,
                    subject,
                    selection,
                ).receipt
            self._sequence += 1
            operation_id = receipt.operation_id if receipt else self._operation_id()
            cancellation = CancellationToken(
                lambda phase, progress: self._progress(operation_id, phase, progress)
            )
            record = _OperationRecord(
                sequence=self._sequence,
                id=operation_id,
                kind=kind,
                actor=actor,
                target=target,
                state="pending",
                phase="queued",
                progress=0,
                created_at=now,
                updated_at=now,
                started_at=None,
                finished_at=None,
                result=None,
                error=None,
                cancellation=cancellation,
                receipt=receipt,
                lifecycle=lifecycle,
            )
            self._records[operation_id] = record
            if target is not None:
                self._active_targets[target] = operation_id
            projection = (
                self._project_lifecycle_locked(record)
                if lifecycle
                else self._project_locked(record)
            )
            try:
                future = self._executor.submit(self._run, operation_id, call)
            except Exception:
                record.started_at = now
                self._terminal_locked(
                    record,
                    state="failed",
                    error=MarketplaceOperationPublicError(
                        code="marketplace_operation_unavailable"
                    ),
                    outcome=KnownUnchangedOutcome(
                        type="known_unchanged",
                        evidence="before_mutation",
                        package_state=None,
                    ),
                )
                return (
                    self._project_lifecycle_locked(record)
                    if lifecycle
                    else self._project_locked(record)
                )
            current = self._records.get(operation_id)
            if current is not None:
                current.future = future
                if current.cancellation.cancellation_requested and future.cancel():
                    self._terminal_locked(current, state="cancelled")
        return projection

    def _run(
        self,
        operation_id: str,
        call: Callable[
            [CancellationToken],
            MarketplaceOperationResult
            | LifecycleCompletion
            | LegacyLifecycleCompletion,
        ],
    ) -> None:
        with self._lock:
            record = self._records.get(operation_id)
            if record is None or record.state in _TERMINAL_STATES:
                return
            now = self._now()
            record.state = "running"
            record.phase = "running"
            record.started_at = now
            record.updated_at = now
            cancellation = record.cancellation
            kind = record.kind
        try:
            cancellation.checkpoint()
            result = call(cancellation)
            if record.receipt is not None:
                legacy_result = None
                if not record.lifecycle:
                    if not isinstance(result, LegacyLifecycleCompletion):
                        raise ValueError(
                            "legacy lifecycle completion evidence is required"
                        )
                    legacy_result = result.result
                    result = result.completion
                with self._lock:
                    if (
                        self._records.get(operation_id) is not record
                        or record.state != "running"
                    ):
                        return
                    if not isinstance(
                        result, LifecycleCompletion
                    ) or result.state not in {"succeeded", "failed"}:
                        raise ValueError("lifecycle completion evidence is required")
                    # The domain producer has already arbitrated cancellation.
                    # A later request cannot erase publication or uncertain truth.
                    if result.review_token is not None:
                        self._validate_review_metadata(record, result)
                # As at retrieval, authoritative inspection can wait on a
                # transaction lock whose owner needs this registry for progress.
                authority_valid = True
                if result.review_token is not None:
                    metadata = result.review_token
                    try:
                        authority_valid = (
                            metadata.validate_unused(metadata.confirmation_token)
                            is True
                        )
                    except Exception:
                        authority_valid = False
                with self._lock:
                    if (
                        self._records.get(operation_id) is not record
                        or record.state != "running"
                    ):
                        return
                    if result.review_token is not None:
                        self._validate_review_metadata(record, result)
                        if not authority_valid:
                            raise ValueError("review authority is unavailable")
                    self._terminal_locked(
                        record,
                        state=result.state,
                        result=result.result,
                        error=result.error,
                        outcome=result.outcome,
                        legacy_result=legacy_result,
                    )
                    if record.state == "succeeded" and result.review_token is not None:
                        if record.id in self._records:
                            self._review_tokens[record.id] = result.review_token
                return
            result = validate_marketplace_operation_result(
                result.model_dump(mode="json", by_alias=True)
                if isinstance(result, _OperationResultBase)
                else result
            )
            require_result_kind(kind=_lifecycle_kind(kind), result=result)
            with self._lock:
                record = self._records.get(operation_id)
                if record is None or record.state in _TERMINAL_STATES:
                    return
                if (
                    record.kind == "refresh"
                    and isinstance(result, MarketplaceSourceRefreshOperationResult)
                    and result.value.source_name
                    != _refresh_source_name(record.kind, record.target)
                ):
                    raise ValueError("refresh operation result source is inconsistent")
                if cancellation.cancellation_requested and not cancellation.committed:
                    self._terminal_locked(record, state="cancelled")
                elif cancellation.atomic_started and not cancellation.committed:
                    self._terminal_locked(
                        record,
                        state="failed",
                        error=MarketplaceOperationPublicError(
                            code="marketplace_operation_failed"
                        ),
                    )
                else:
                    self._terminal_locked(record, state="succeeded", result=result)
        except MarketplaceOperationCancelled:
            with self._lock:
                record = self._records.get(operation_id)
                if record is not None:
                    self._terminal_locked(record, state="cancelled")
        except Exception as exception:
            code = getattr(exception, "code", "marketplace_operation_failed")
            if (
                not isinstance(code, str)
                or _IDENTIFIER.fullmatch(code) is None
                or len(code) > 128
            ):
                code = "marketplace_operation_failed"
            with self._lock:
                record = self._records.get(operation_id)
                if record is not None:
                    self._terminal_locked(
                        record,
                        state="failed",
                        error=MarketplaceOperationPublicError(code=code),
                    )

    def intersects_active_mutation(self, identity: InstalledPackageIdentity) -> bool:
        """Observe exact profile-wide writer activity without projecting actors."""
        identity = InstalledPackageIdentity.model_validate(
            identity.model_dump(mode="json", by_alias=True)
        )
        with self._lock:
            return any(
                record.state in {"pending", "running"}
                and record.receipt is not None
                and record.receipt.kind
                in {
                    "install_confirm",
                    "update_confirm",
                    "remove_confirm",
                    "trust_confirm",
                    "trust_revoke",
                }
                and isinstance(record.receipt.subject, PackageSubject)
                and record.receipt.subject.identity.source_key == identity.source_key
                and record.receipt.subject.identity.package_id == identity.package_id
                for record in self._records.values()
            )

    def get(
        self, operation_id: str, *, actor: str = "operator"
    ) -> MarketplaceOperation:
        actor = _clean_identity(actor, label="operation actor", maximum=256)
        with self._lock:
            self._prune_locked(self._now())
            record = self._require_locked(operation_id, actor)
            return (
                self._project_lifecycle_locked(record)
                if record.lifecycle
                else self._project_locked(record)
            )

    def get_lifecycle(self, operation_id, *, actor="operator"):
        actor = _clean_identity(actor, label="operation actor", maximum=256)
        with self._lock:
            self._prune_locked(self._now())
            return self._project_lifecycle_locked(
                self._require_locked(operation_id, actor)
            )

    def _require_legacy_locked(self, operation_id, actor):
        record = self._require_locked(operation_id, actor)
        if record.lifecycle:
            raise MarketplaceOperationRegistryError(
                "marketplace_lifecycle_upgrade_required",
                "Marketplace lifecycle upgrade required.",
            )
        return record

    def get_legacy(self, operation_id: str, *, actor: str) -> MarketplaceOperation:
        actor = _clean_identity(actor, label="operation actor", maximum=256)
        with self._lock:
            self._prune_locked(self._now())
            return self._project_locked(
                self._require_legacy_locked(operation_id, actor)
            )

    def list_legacy(
        self, *, actor: str, offset: int, limit: int
    ) -> tuple[MarketplaceOperation, ...]:
        actor = _clean_identity(actor, label="operation actor", maximum=256)
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ValueError("operation pagination is invalid")
        with self._lock:
            self._prune_locked(self._now())
            records = sorted(
                (
                    record
                    for record in self._records.values()
                    if record.actor == actor and not record.lifecycle
                ),
                key=lambda record: record.sequence,
                reverse=True,
            )
            return tuple(
                self._project_locked(record)
                for record in records[offset : offset + limit]
            )

    def cancel_legacy(self, operation_id: str, *, actor: str) -> MarketplaceOperation:
        actor = _clean_identity(actor, label="operation actor", maximum=256)
        with self._lock:
            self._prune_locked(self._now())
            record = self._require_legacy_locked(operation_id, actor)
            self._cancel_locked(record)
            return self._project_locked(record)

    def cancel_lifecycle(self, operation_id: str, *, actor: str) -> LifecycleOperation:
        actor = _clean_identity(actor, label="operation actor", maximum=256)
        with self._lock:
            self._prune_locked(self._now())
            record = self._require_locked(operation_id, actor)
            # Establish that this record is eligible for a strict V2 observation
            # before changing it, including legacy internal records without receipts.
            self._project_lifecycle_locked(record)
            self._cancel_locked(record)
            return self._project_lifecycle_locked(record)

    def lookup_admission(self, request_id, *, actor="operator"):
        actor = _clean_identity(actor, label="operation actor", maximum=256)
        with self._lock:
            self._prune_locked(self._now())
            receipt = self.admissions.lookup(actor, self.profile_key, request_id)
            if receipt is None:
                self.admissions.require_new_request_window(request_id)
                raise MarketplaceOperationNotFoundError(
                    "marketplace_admission_not_found",
                    "Marketplace admission was not found.",
                )
            projection = self._project_receipt_locked(receipt)
            return (
                AdmissionFound(operation=projection)
                if isinstance(projection, LifecycleOperation)
                else projection
            )

    def list_snapshot(self, *, actor="operator", limit=100, cursor=None):
        actor = _clean_identity(actor, label="operation actor", maximum=256)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("operation pagination is invalid")
        with self._lock:
            now = self._now()
            self._prune_locked(now)
            if cursor is None:
                if len(self._snapshots) >= _SNAPSHOTS_MAX:
                    raise MarketplaceOperationCapacityError(
                        "marketplace_list_capacity", "Operation list capacity is full."
                    )
                records = sorted(
                    (
                        record
                        for record in self._records.values()
                        if record.actor == actor
                    ),
                    key=lambda record: record.sequence,
                    reverse=True,
                )
                if len(records) > _SNAPSHOT_RECORDS_MAX:
                    raise MarketplaceOperationCapacityError(
                        "marketplace_list_capacity", "Operation list capacity is full."
                    )
                entries = []
                size = sum(
                    len(entry)
                    for snapshot in self._snapshots
                    for entry in snapshot.entries
                )
                for record in records:
                    entry = (
                        self
                        ._project_lifecycle_locked(record)
                        .model_dump_json()
                        .encode()
                    )
                    size += len(entry)
                    if size > _SNAPSHOT_BYTES_MAX:
                        raise MarketplaceOperationCapacityError(
                            "marketplace_list_capacity",
                            "Operation list capacity is full.",
                        )
                    entries.append(entry)
                snapshot = _ListSnapshot(
                    actor, now + timedelta(seconds=30), tuple(entries), {}
                )
                self._snapshots.append(snapshot)
                offset = 0
            else:
                if not isinstance(cursor, str) or len(cursor) != 32:
                    raise MarketplaceOperationRegistryError(
                        "marketplace_list_expired", "Operation list expired."
                    )
                snapshot = next(
                    (
                        item
                        for item in self._snapshots
                        if item.actor == actor and cursor in item.cursors
                    ),
                    None,
                )
                if snapshot is None:
                    raise MarketplaceOperationRegistryError(
                        "marketplace_list_expired", "Operation list expired."
                    )
                offset = snapshot.cursors[cursor]
            end = min(offset + limit, len(snapshot.entries))
            next_cursor = None
            if end < len(snapshot.entries):
                next_cursor = next(
                    (
                        key
                        for key, position in snapshot.cursors.items()
                        if position == end
                    ),
                    None,
                )
                if next_cursor is None:
                    next_cursor = secrets.token_hex(16)
                    snapshot.cursors[next_cursor] = end
            return LifecycleOperationPage(
                items=tuple(
                    LifecycleOperation.model_validate_json(entry)
                    for entry in snapshot.entries[offset:end]
                ),
                next_cursor=next_cursor,
                complete=end == len(snapshot.entries),
            )

    def _validate_review_metadata(self, record, completion):
        metadata = completion.review_token
        receipt = record.receipt
        value = getattr(completion.result, "value", None)
        if (
            completion.state != "succeeded"
            or not record.kind.endswith("_prepare")
            or receipt is None
            or metadata.subject != receipt.subject
            or metadata.selection != receipt.selection
            or getattr(value, "review_digest", None) != metadata.review_digest
            or getattr(value, "confirmation_available", False) is not True
            or value.expires_at != metadata.expires_at
            or datetime.fromisoformat(metadata.expires_at.replace("Z", "+00:00"))
            <= self._now()
            or not isinstance(metadata.confirmation_token, str)
            or not 1 <= len(metadata.confirmation_token) <= 4096
            or not callable(metadata.validate_unused)
        ):
            raise ValueError("review authority is inconsistent")

    def review_token(
        self, operation_id, *, actor, registry_epoch, review_digest, subject, selection
    ):
        def unavailable():
            raise MarketplaceOperationRegistryError(
                "marketplace_review_unavailable", "Marketplace review is unavailable."
            )

        with self._lock:
            self._prune_locked(self._now())
            record = self._records.get(operation_id)
            metadata = self._review_tokens.get(operation_id)
            if (
                registry_epoch != self.admissions.epoch
                or record is None
                or record.actor != actor
                or record.state != "succeeded"
                or metadata is None
                or metadata.subject != subject
                or metadata.selection != selection
                or metadata.review_digest != review_digest
            ):
                unavailable()
        # Domain token inspection can acquire transaction/trust locks. Never
        # hold the registry lock here: a committing worker may report progress.
        try:
            valid = metadata.validate_unused(metadata.confirmation_token) is True
        except Exception:
            valid = False
        with self._lock:
            self._prune_locked(self._now())
            if not valid:
                if self._review_tokens.get(operation_id) is metadata:
                    self._review_tokens.pop(operation_id, None)
                unavailable()
            if self._review_tokens.get(operation_id) is not metadata:
                unavailable()
            return ReviewTokenResponse(
                operation_id=record.id,
                request_id=record.receipt.request_id,
                subject=metadata.subject.model_copy(deep=True),
                selection=metadata.selection.model_copy(deep=True)
                if metadata.selection
                else None,
                review_digest=metadata.review_digest,
                confirmation_token=metadata.confirmation_token,
                expires_at=metadata.expires_at,
            )

    def list(
        self,
        *,
        actor: str = "operator",
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[MarketplaceOperation, ...]:
        actor = _clean_identity(actor, label="operation actor", maximum=256)
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("operation pagination is invalid")
        with self._lock:
            self._prune_locked(self._now())
            records = sorted(
                (record for record in self._records.values() if record.actor == actor),
                key=lambda item: item.sequence,
                reverse=True,
            )
            return tuple(
                (
                    self._project_lifecycle_locked(record)
                    if record.lifecycle
                    else self._project_locked(record)
                )
                for record in records[offset : offset + limit]
            )

    def cancel(
        self, operation_id: str, *, actor: str = "operator"
    ) -> MarketplaceOperation:
        actor = _clean_identity(actor, label="operation actor", maximum=256)
        with self._lock:
            self._prune_locked(self._now())
            record = self._require_locked(operation_id, actor)
            self._cancel_locked(record)
            return (
                self._project_lifecycle_locked(record)
                if record.lifecycle
                else self._project_locked(record)
            )

    def _cancel_locked(self, record):
        if record.state in _TERMINAL_STATES:
            return
        record.cancellation.cancel()
        future = record.future
        if record.state == "pending" and future is not None and future.cancel():
            self._terminal_locked(record, state="cancelled")

    def close(self, *, wait_timeout: float = 5.0) -> None:
        if isinstance(wait_timeout, bool) or not isinstance(wait_timeout, int | float):
            raise ValueError("operation shutdown timeout is invalid")
        if wait_timeout < 0 or wait_timeout > 30:
            raise ValueError("operation shutdown timeout is invalid")
        with self._lock:
            self._closed = True
            futures = []
            for record in tuple(self._records.values()):
                if record.state in _TERMINAL_STATES:
                    continue
                record.cancellation.cancel()
                if record.future is not None:
                    if record.state == "pending" and record.future.cancel():
                        self._terminal_locked(record, state="cancelled")
                    else:
                        futures.append(record.future)
        if futures and wait_timeout:
            wait(futures, timeout=float(wait_timeout))
        self._executor.shutdown(wait=False, cancel_futures=True)

    def retire_if_idle(self) -> bool:
        """Prevent new work if this registry has no pending/running operations."""

        with self._lock:
            self._prune_locked(self._now())
            if (
                self.admissions.has_live_receipts()
                or self._snapshots
                or self._review_tokens
            ):
                return False
            if any(
                record.state not in _TERMINAL_STATES
                for record in self._records.values()
            ):
                return False
            if self._closed:
                return True
            self._closed = True
            return True

    def close_retired(self) -> None:
        """Release an executor after ``retire_if_idle`` won."""

        with self._lock:
            if not self._closed or any(
                record.state not in _TERMINAL_STATES
                for record in self._records.values()
            ):
                raise RuntimeError("active marketplace registry cannot be retired")
        self._executor.shutdown(wait=False, cancel_futures=True)

    def has_in_flight(self) -> bool:
        with self._lock:
            return any(
                record.state not in _TERMINAL_STATES
                for record in self._records.values()
            )


__all__ = [
    "AdmissionEvicted",
    "AdmissionFound",
    "LifecycleCompletion",
    "LegacyLifecycleCompletion",
    "LifecycleOperationPage",
    "ReviewTokenMetadata",
    "ReviewTokenResponse",
    "CancellationToken",
    "MarketplaceOperation",
    "MarketplaceOperationCancelled",
    "MarketplaceOperationCapacityError",
    "MarketplaceOperationConflictError",
    "MarketplaceOperationNotFoundError",
    "MarketplaceOperationPublicError",
    "MarketplaceOperationRegistryError",
    "MarketplaceOperationResult",
    "MarketplaceInstallReviewOperationResult",
    "MarketplaceInstalledPackageOperationResult",
    "MarketplacePackageDetailOperationResult",
    "MarketplaceRemoveReviewOperationResult",
    "MarketplaceRemovedPackageOperationResult",
    "MarketplaceSourceRefreshOperationResult",
    "MarketplaceSourceRefreshValue",
    "MarketplaceTrustGrantOperationResult",
    "MarketplaceTrustReviewOperationResult",
    "MarketplaceTrustRevokeOperationResult",
    "MarketplaceTrustRevocationValue",
    "MarketplaceTrustState",
    "MarketplaceTrustStatesValue",
    "MarketplaceUpdateChecksOperationResult",
    "MarketplaceUpdateChecksValue",
    "MarketplaceUpdateReviewOperationResult",
    "MarketplaceUpdatedPackageOperationResult",
    "WorkflowMarketplaceOperationRegistry",
    "validate_marketplace_operation_result",
]
