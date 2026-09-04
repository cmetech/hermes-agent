"""Bounded, profile-scoped background operations for the marketplace API."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import secrets
import threading
from collections.abc import Callable
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

from .models import (
    InstallReview,
    InstalledPackage,
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


class MarketplaceOperationRegistryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class MarketplaceOperationCapacityError(MarketplaceOperationRegistryError):
    pass


class MarketplaceOperationConflictError(MarketplaceOperationRegistryError):
    pass


class MarketplaceOperationNotFoundError(MarketplaceOperationRegistryError):
    pass


class MarketplaceOperationCancelled(RuntimeError):
    pass


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
    result: MarketplaceOperationResult | None
    error: MarketplaceOperationPublicError | None
    cancellation: CancellationToken
    future: Future[None] | None = None


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
    ):
        self.profile_key = _clean_identity(
            profile_key, label="operation profile key", maximum=4096
        )
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
        self._profile_digest = hashlib.sha256(profile_key.encode()).hexdigest()[:12]
        self._lock = threading.RLock()
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

    def _project_locked(self, record: _OperationRecord) -> MarketplaceOperation:
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
                record.result.model_copy(deep=True)
                if record.result is not None
                else None
            ),
            "error": (
                record.error.model_copy(deep=True) if record.error is not None else None
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
        result: MarketplaceOperationResult | None = None,
        error: MarketplaceOperationPublicError | None = None,
    ) -> None:
        if record.state in _TERMINAL_STATES:
            return
        now = self._now()
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
            if progress < record.progress:
                raise ValueError("operation progress cannot move backwards")
            record.phase = phase
            record.progress = progress
            record.updated_at = self._now()

    def start(
        self,
        kind: str,
        call: Callable[[CancellationToken], MarketplaceOperationResult],
        *,
        actor: str = "operator",
        target: str | None = None,
    ) -> MarketplaceOperation:
        kind = _clean_identifier(kind, label="operation kind")
        actor = _clean_identity(actor, label="operation actor", maximum=256)
        if not callable(call):
            raise TypeError("operation call must be callable")
        if target is not None:
            target = _clean_identity(target, label="operation target", maximum=512)
        _refresh_source_name(kind, target)
        with self._lock:
            if self._closed:
                raise MarketplaceOperationCapacityError(
                    "marketplace_operation_unavailable",
                    "marketplace operation registry is closed",
                )
            now = self._now()
            self._prune_locked(now)
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
            self._sequence += 1
            operation_id = self._operation_id()
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
            )
            self._records[operation_id] = record
            if target is not None:
                self._active_targets[target] = operation_id
            projection = self._project_locked(record)
        try:
            future = self._executor.submit(self._run, operation_id, call)
        except Exception:
            with self._lock:
                current = self._records.pop(operation_id, None)
                if current is not None:
                    self._release_target_locked(current)
            raise
        with self._lock:
            current = self._records.get(operation_id)
            if current is not None:
                current.future = future
                if current.cancellation.cancellation_requested and future.cancel():
                    self._terminal_locked(current, state="cancelled")
        return projection

    def _run(
        self,
        operation_id: str,
        call: Callable[[CancellationToken], MarketplaceOperationResult],
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
        try:
            cancellation.checkpoint()
            result = call(cancellation)
            result = validate_marketplace_operation_result(
                result.model_dump(mode="json", by_alias=True)
                if isinstance(result, _OperationResultBase)
                else result
            )
            with self._lock:
                record = self._records.get(operation_id)
                if record is None or record.state in _TERMINAL_STATES:
                    return
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
            if code in {"source_cancelled", "marketplace_operation_cancelled"}:
                with self._lock:
                    record = self._records.get(operation_id)
                    if record is not None:
                        self._terminal_locked(record, state="cancelled")
                return
            with self._lock:
                record = self._records.get(operation_id)
                if record is not None:
                    self._terminal_locked(
                        record,
                        state="failed",
                        error=MarketplaceOperationPublicError(code=code),
                    )

    def get(
        self, operation_id: str, *, actor: str = "operator"
    ) -> MarketplaceOperation:
        actor = _clean_identity(actor, label="operation actor", maximum=256)
        with self._lock:
            self._prune_locked(self._now())
            return self._project_locked(self._require_locked(operation_id, actor))

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
                self._project_locked(record)
                for record in records[offset : offset + limit]
            )

    def cancel(
        self, operation_id: str, *, actor: str = "operator"
    ) -> MarketplaceOperation:
        actor = _clean_identity(actor, label="operation actor", maximum=256)
        with self._lock:
            self._prune_locked(self._now())
            record = self._require_locked(operation_id, actor)
            if record.state in _TERMINAL_STATES:
                return self._project_locked(record)
            record.cancellation.cancel()
            future = record.future
            if record.state == "pending" and future is not None and future.cancel():
                self._terminal_locked(record, state="cancelled")
            return self._project_locked(record)

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
