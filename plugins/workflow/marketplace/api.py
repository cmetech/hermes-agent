"""Authenticated, profile-scoped REST adapter for the workflow marketplace."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from typing import Literal, Protocol, TypeVar
import urllib.parse

from fastapi import APIRouter, HTTPException, Request
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from hermes_cli.git_source import (
    GitSourceError,
    validate_credential_free_git_source,
)
from hermes_constants import get_hermes_home, hermes_home_key
from .lifecycle_models import (
    AllPackagesSubject,
    LifecycleSubject,
    LifecycleResult,
    LifecyclePublicError,
    OutcomeUnknown,
    PackageIdentity,
    PackageSubject,
    SourceSubject,
)

from .models import (
    InstalledPackage,
    InstalledPackageIdentity,
    SOURCE_NAME_PATTERN,
    PACKAGE_ID_PATTERN,
    SEMANTIC_VERSION_PATTERN,
    SHA256_PATTERN,
    UpdateCheck,
    WorkflowMarketplaceSource,
)
from .operations import (
    CancellationToken,
    LegacyLifecycleCompletion,
    LifecycleCompletion,
    MarketplaceOperation,
    MarketplaceOperationRegistryError,
    MarketplaceOperationResult,
    WorkflowMarketplaceOperationRegistry,
    validate_marketplace_operation_result,
)
from .package import WorkflowMarketplaceError
from .lifecycle_state import complete_read, complete_source_refresh
from .service import WorkflowMarketplaceService
from .source_store import (
    redact_source_refresh_message,
    source_refresh_message_is_safe,
)


_BODY_BYTES_MAX = 64 * 1024
_JSON_NESTING_MAX = 64
_UINT = re.compile(r"^(0|[1-9][0-9]*)$", re.ASCII)
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,4096}$", re.ASCII)
_OPERATION_ID = re.compile(r"^wmop_[0-9a-f]{12}_[0-9a-f]{32}$", re.ASCII)
_COMMIT = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$", re.ASCII)
_REPOSITORY_PATH_DECODE_MAX = 8
_SENSITIVE_REPOSITORY_SEGMENTS = frozenset({
    ".cache",
    ".quarantine",
    ".staging",
    "cache",
    "caches",
    "quarantine",
    "staging",
    "temp",
    "tmp",
})
_INTERNAL_REPOSITORY_PATH_SEQUENCES = (
    ("marketplace", "workflows"),
    ("var", "folders"),
    ("workflows", "marketplace"),
)
_SECRET_KEY = re.compile(
    r"^(?:access|refresh)?token$|^api(?:key)?$|^auth(?:orization)?$|^password$|"
    r"^secret$|^clientsecret$|^credentials?$|^confirmationtoken$",
    re.ASCII,
)
_REDACTED_REPOSITORY_URL = "file:///REDACTED"


class _Authority(Protocol):
    authority_binding: str

    def require(self, capability: str) -> None: ...


VerifiedOperator = Callable[[Request, str | None], _Authority]


class _StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MarketplaceCapabilitiesResponse(_StrictApiModel):
    schema_version: Literal[1]
    profile: str = Field(min_length=1, max_length=256)
    capabilities: list[
        Literal[
            "sources",
            "search",
            "installed",
            "updates",
            "transactions",
            "trust",
            "operations",
        ]
    ] = Field(min_length=7, max_length=7)


class MarketplaceSourceListItem(_StrictApiModel):
    name: str = Field(min_length=1, max_length=64, pattern=SOURCE_NAME_PATTERN)
    repository_url: str = Field(min_length=1, max_length=4096)
    ref: str | None = Field(default=None, min_length=1, max_length=1024)
    enabled: StrictBool
    refresh_state: (
        Literal[
            "fresh",
            "stale",
            "authentication-failed",
            "malformed",
            "incompatible",
            "unavailable",
        ]
        | None
    ) = None
    attempted_at: str | None = Field(default=None, min_length=20, max_length=64)
    resolved_commit: str | None = Field(default=None, pattern=_COMMIT.pattern)
    verified_at: str | None = Field(default=None, min_length=20, max_length=64)
    verified_package_count: StrictInt = Field(ge=0, le=4096)
    diagnostic_code: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=_ERROR_CODE.pattern
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

    @field_validator("attempted_at", "verified_at")
    @classmethod
    def validate_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.endswith("Z"):
            raise ValueError("timestamp is invalid")
        try:
            from datetime import datetime, timezone

            parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
        except ValueError as error:
            raise ValueError("timestamp is invalid") from error
        if parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") != value:
            raise ValueError("timestamp is invalid")
        return value

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str | None) -> str | None:
        if value is not None and not source_refresh_message_is_safe(value):
            raise ValueError("source diagnostic is not canonically redacted")
        return value

    @model_validator(mode="after")
    def require_refresh_cache_relationship(self) -> "MarketplaceSourceListItem":
        has_verified = self.resolved_commit is not None or self.verified_at is not None
        if (self.resolved_commit is None) != (self.verified_at is None):
            raise ValueError("verified source cache identity is incomplete")
        if not has_verified and self.verified_package_count != 0:
            raise ValueError("package count requires a verified source cache")
        if self.refresh_state is None:
            if (
                self.attempted_at is not None
                or self.diagnostic_code is not None
                or self.message is not None
                or has_verified
            ):
                raise ValueError("never-refreshed source contains refresh state")
        elif self.attempted_at is None:
            raise ValueError("refresh state requires an attempted timestamp")
        elif self.refresh_state == "fresh":
            if (
                not has_verified
                or self.diagnostic_code is not None
                or self.message is not None
            ):
                raise ValueError("fresh source cache relationship is invalid")
        elif self.refresh_state == "stale":
            if not has_verified or self.diagnostic_code is None or self.message is None:
                raise ValueError("stale source cache relationship is invalid")
        elif (
            has_verified
            or self.verified_package_count != 0
            or self.diagnostic_code is None
            or self.message is None
        ):
            raise ValueError("failed source refresh relationship is invalid")
        return self


class MarketplaceSourceListResponse(_StrictApiModel):
    profile: str = Field(min_length=1, max_length=256)
    sources: list[MarketplaceSourceListItem] = Field(max_length=128)


class MarketplaceSourceResponse(_StrictApiModel):
    status: Literal["created", "updated", "enabled", "disabled", "removed"]
    profile: str = Field(min_length=1, max_length=256)
    source: WorkflowMarketplaceSource


class MarketplaceSourceUpdateRequest(_StrictApiModel):
    repository_url: str = Field(alias="repositoryUrl", min_length=1, max_length=4096)
    ref: str | None = Field(default=None, min_length=1, max_length=1024)
    enabled: StrictBool

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str) -> str:
        try:
            validate_credential_free_git_source(value)
        except GitSourceError as error:
            raise ValueError("repository URL is invalid") from error
        return value

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str | None) -> str | None:
        if value is not None and (value != value.strip() or "\0" in value):
            raise ValueError("source ref is invalid")
        return value


class MarketplaceSourceEnabledRequest(_StrictApiModel):
    enabled: StrictBool


class MarketplaceCatalogPackage(_StrictApiModel):
    identifier: str = Field(min_length=3, max_length=129)
    source_name: str = Field(min_length=1, max_length=64, pattern=SOURCE_NAME_PATTERN)
    repository_url: str = Field(min_length=1, max_length=4096)
    configured_ref: str | None = Field(default=None, min_length=1, max_length=1024)
    resolved_commit: str = Field(pattern=_COMMIT.pattern)
    verified_at: str = Field(min_length=20, max_length=64)
    state: Literal["fresh", "stale"]
    id: str = Field(min_length=1, max_length=64, pattern=PACKAGE_ID_PATTERN)
    version: str = Field(min_length=1, max_length=128, pattern=SEMANTIC_VERSION_PATTERN)
    display_name: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=4096)
    license: str = Field(min_length=1, max_length=256)
    publisher: str = Field(min_length=1, max_length=256)
    tags: list[str] = Field(min_length=1, max_length=64)
    package_path: str = Field(min_length=1, max_length=1024)
    contract_version: Literal[1]
    package_digest: str = Field(pattern=SHA256_PATTERN)

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str) -> str:
        try:
            validate_credential_free_git_source(value)
        except GitSourceError as error:
            raise ValueError("repository URL is invalid") from error
        return value


class MarketplacePackagePage(_StrictApiModel):
    profile: str = Field(min_length=1, max_length=256)
    query: str = Field(max_length=256)
    source: str | None = Field(default=None, max_length=64)
    offset: StrictInt = Field(ge=0, le=199)
    limit: StrictInt = Field(ge=1, le=100)
    items: list[MarketplaceCatalogPackage] = Field(max_length=100)
    next_offset: StrictInt | None = Field(default=None, ge=1, le=199)


class MarketplaceInstalledPage(_StrictApiModel):
    profile: str = Field(min_length=1, max_length=256)
    packages: list[InstalledPackage] = Field(max_length=512)


class MarketplaceIdentityRequest(InstalledPackageIdentity):
    pass


class MarketplaceTrustReviewRequest(_StrictApiModel):
    identity: InstalledPackageIdentity
    workflow_name: str | None = Field(
        default=None, alias="workflowName", min_length=1, max_length=256
    )


class MarketplaceTrustRevokeRequest(_StrictApiModel):
    identity: InstalledPackageIdentity
    workflow_name: str | None = Field(
        default=None, alias="workflowName", min_length=1, max_length=256
    )


class MarketplaceConfirmationRequest(_StrictApiModel):
    confirmation_token: str = Field(
        alias="confirmationToken",
        min_length=32,
        max_length=4096,
        pattern=_TOKEN.pattern,
    )


class MarketplaceUpdateCheckRequest(_StrictApiModel):
    identity: InstalledPackageIdentity | None = None


class MarketplaceSourceRefreshProjection(_StrictApiModel):
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
    resolved_commit: str | None = Field(default=None, pattern=_COMMIT.pattern)
    verified_at: str | None = Field(default=None, min_length=20, max_length=64)
    package_count: StrictInt = Field(ge=0, le=4096)
    diagnostic_code: str | None = Field(default=None, min_length=1, max_length=128)
    message: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str) -> str:
        try:
            validate_credential_free_git_source(value)
        except GitSourceError as error:
            raise ValueError("repository URL is invalid") from error
        return value


class MarketplaceUpdateChecksProjection(_StrictApiModel):
    checks: list[UpdateCheck] = Field(max_length=512)


class MarketplaceTrustState(_StrictApiModel):
    workflow_name: str = Field(min_length=1, max_length=256)
    state: Literal["trusted", "untrusted"]


class MarketplaceTrustStatesProjection(_StrictApiModel):
    workflows: list[MarketplaceTrustState] = Field(max_length=512)


class MarketplaceTrustRevocationProjection(_StrictApiModel):
    revoked: StrictInt = Field(ge=0, le=512)


class MarketplaceOperationPage(_StrictApiModel):
    profile: str = Field(min_length=1, max_length=256)
    offset: StrictInt = Field(ge=0, le=10_000)
    limit: StrictInt = Field(ge=1, le=100)
    operations: list[MarketplaceOperation] = Field(max_length=100)


@dataclass(slots=True)
class _ProfileServices:
    service: WorkflowMarketplaceService
    registry: WorkflowMarketplaceOperationRegistry
    last_used: int


ServiceFactory = Callable[[Path, str], WorkflowMarketplaceService]
ProfileResolver = Callable[[Path], str]


def _default_profile_resolver(_home: Path) -> str:
    from hermes_cli.profiles import get_active_profile_name

    return get_active_profile_name()


class WorkflowMarketplaceApiContext:
    """Cache service and operation ownership by the authoritative profile home."""

    def __init__(
        self,
        *,
        service_factory: ServiceFactory = lambda home, profile: (
            WorkflowMarketplaceService(home, profile=profile)
        ),
        home_resolver: Callable[[], Path] = get_hermes_home,
        profile_resolver: ProfileResolver = _default_profile_resolver,
        operation_limits: Mapping[str, int] | None = None,
        max_profiles: int = 4,
        shutdown_timeout: float = 5.0,
    ):
        self._service_factory = service_factory
        self._home_resolver = home_resolver
        self._profile_resolver = profile_resolver
        self._operation_limits = dict(operation_limits or {})
        if set(self._operation_limits) - {
            "max_workers",
            "max_in_flight",
            "max_terminal",
        }:
            raise ValueError("unknown marketplace operation limit")
        if (
            isinstance(max_profiles, bool)
            or not isinstance(max_profiles, int)
            or not 1 <= max_profiles <= 16
            or isinstance(shutdown_timeout, bool)
            or not isinstance(shutdown_timeout, int | float)
            or not 0 <= shutdown_timeout <= 30
        ):
            raise ValueError("marketplace API context limits are invalid")
        self._max_profiles = max_profiles
        self._shutdown_timeout = float(shutdown_timeout)
        self._lock = threading.Lock()
        self._profiles: dict[str, _ProfileServices] = {}
        self._sequence = 0
        self._closed = False

    def _current_identity(self) -> tuple[Path, str, str]:
        home = Path(self._home_resolver()).expanduser().resolve(strict=False)
        key = hermes_home_key(home)
        raw_profile = self._profile_resolver(home)
        if (
            not isinstance(raw_profile, str)
            or not raw_profile
            or len(raw_profile) > 256
            or raw_profile != raw_profile.strip()
            or "\0" in raw_profile
        ):
            raw_profile = f"custom-{hashlib.sha256(key.encode()).hexdigest()[:12]}"
        return home, key, raw_profile

    def _current(self) -> tuple[str, str, _ProfileServices]:
        home, key, profile = self._current_identity()
        retired: WorkflowMarketplaceOperationRegistry | None = None
        try:
            with self._lock:
                if self._closed:
                    raise HTTPException(
                        status_code=503,
                        detail={"code": "marketplace_operation_unavailable"},
                    )
                services = self._profiles.get(key)
                if services is None:
                    if len(self._profiles) >= self._max_profiles:
                        for candidate_key, candidate in sorted(
                            self._profiles.items(),
                            key=lambda item: (item[1].last_used, item[0]),
                        ):
                            if candidate.registry.retire_if_idle():
                                retired = candidate.registry
                                self._profiles.pop(candidate_key)
                                break
                        else:
                            raise HTTPException(
                                status_code=429,
                                detail={"code": "marketplace_operation_capacity"},
                            )
                    self._sequence += 1
                    services = _ProfileServices(
                        service=self._service_factory(home, profile),
                        registry=WorkflowMarketplaceOperationRegistry(
                            profile_key=key,
                            profile=profile,
                            **self._operation_limits,
                        ),
                        last_used=self._sequence,
                    )
                    self._profiles[key] = services
                else:
                    self._sequence += 1
                    services.last_used = self._sequence
        except BaseException:
            if retired is not None:
                retired.close_retired()
            raise
        if retired is not None:
            retired.close_retired()
        return key, profile, services

    def service_for_current_profile(self) -> WorkflowMarketplaceService:
        return self._current()[2].service

    def registry_for_current_profile(self) -> WorkflowMarketplaceOperationRegistry:
        return self._current()[2].registry

    def current(
        self,
    ) -> tuple[
        str, str, WorkflowMarketplaceService, WorkflowMarketplaceOperationRegistry
    ]:
        key, profile, services = self._current()
        return key, profile, services.service, services.registry

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            services = tuple(self._profiles.values())
            self._profiles.clear()
        deadline = time.monotonic() + self._shutdown_timeout
        for item in services:
            remaining = max(0.0, deadline - time.monotonic())
            item.registry.close(wait_timeout=remaining)


_ModelT = TypeVar("_ModelT", bound=BaseModel)
_ValueT = TypeVar("_ValueT")


def _request_error() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "marketplace_request_invalid"},
    )


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON value")


def _check_json_nesting(raw: bytes | bytearray) -> None:
    depth = 0
    quoted = False
    escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                quoted = False
            continue
        if byte == 0x22:
            quoted = True
        elif byte in (0x7B, 0x5B):
            depth += 1
            if depth > _JSON_NESTING_MAX:
                raise ValueError("JSON nesting exceeds its limit")
        elif byte in (0x7D, 0x5D):
            depth -= 1


async def _body(request: Request, model: type[_ModelT]) -> _ModelT:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            parsed_length = int(content_length)
        except ValueError:
            raise _request_error()
        if parsed_length < 0 or parsed_length > _BODY_BYTES_MAX:
            raise _request_error()
    raw = bytearray()
    try:
        async for chunk in request.stream():
            if len(chunk) > _BODY_BYTES_MAX - len(raw):
                raise _request_error()
            raw.extend(chunk)
    except HTTPException:
        raise
    except Exception:
        raise _request_error()
    if not raw:
        raise _request_error()
    try:
        _check_json_nesting(raw)
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
        return model.model_validate(value)
    except (RecursionError, UnicodeDecodeError, ValueError, ValidationError):
        raise _request_error()


def _query(request: Request, allowed: set[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if key not in allowed or key in values:
            raise _request_error()
        values[key] = value
    return values


def _uint(value: str | None, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    if _UINT.fullmatch(value) is None:
        raise _request_error()
    parsed = int(value)
    if parsed > maximum:
        raise _request_error()
    return parsed


def _source_name(value: str) -> str:
    try:
        source = WorkflowMarketplaceSource(
            name=value,
            repositoryUrl="https://example.invalid/workflow-marketplace.git",
        )
    except ValidationError:
        raise _request_error()
    return source.name


def _package_identity(source_name: str, package_id: str) -> InstalledPackageIdentity:
    try:
        return InstalledPackageIdentity(
            sourceKey=_source_name(source_name),
            packageId=package_id,
        )
    except ValidationError:
        raise _request_error()


def _actor(authority: _Authority, profile_key: str) -> str:
    binding = authority.authority_binding
    if not isinstance(binding, str) or not binding or len(binding) > 16_384:
        raise HTTPException(
            status_code=403,
            detail={"code": "marketplace_actor_invalid"},
        )
    digest = hashlib.sha256(
        json.dumps(
            {"authority": binding, "profile": profile_key},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"marketplace:{digest}"


def _authorize(
    verified_operator: VerifiedOperator,
    request: Request,
    capability: Literal["read", "admin"],
) -> _Authority:
    authority = verified_operator(request, None)
    authority.require(capability)
    return authority


def _public(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if is_dataclass(value) and not isinstance(value, type):
        return _public(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _public(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_public(item) for item in value]
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise TypeError("marketplace projection contains an unsupported value")


def _safe_public_text(value: str) -> str:
    sanitized = redact_source_refresh_message(value)
    if sanitized == "workflow marketplace source refresh failed" and value != sanitized:
        return "workflow marketplace operation failed"
    return sanitized


def _safe_repository_url(value: str) -> str:
    """Preserve a typed Git identity or replace sensitive local identity paths."""

    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return _REDACTED_REPOSITORY_URL
    try:
        validate_credential_free_git_source(value)
        parsed = urllib.parse.urlsplit(value)
    except GitSourceError:
        return _REDACTED_REPOSITORY_URL
    if parsed.scheme.casefold() != "file":
        return value
    components = [parsed.netloc, parsed.path, parsed.query, parsed.fragment]
    try:
        for _ in range(_REPOSITORY_PATH_DECODE_MAX):
            decoded = [
                urllib.parse.unquote(component, errors="strict")
                for component in components
            ]
            if decoded == components:
                break
            components = decoded
        else:
            if any("%" in component for component in components):
                return _REDACTED_REPOSITORY_URL
    except UnicodeDecodeError:
        return _REDACTED_REPOSITORY_URL
    authority, classification_path, query, fragment = components
    authority = authority.casefold()
    classification_path = classification_path.replace("\\", "/")
    segments = tuple(
        segment.casefold() for segment in classification_path.split("/") if segment
    )
    if (
        authority not in {"", "localhost"}
        or not classification_path.startswith("/")
        or classification_path.startswith("//")
        or not segments
        or query
        or fragment
        or any("%" in component for component in components)
        or "?" in classification_path
        or "#" in classification_path
        or any(
            ord(character) < 32 or ord(character) == 127
            for component in components
            for character in component
        )
        or any(segment in {".", ".."} for segment in segments)
        or re.match(r"^[A-Za-z]:", classification_path.lstrip("/")) is not None
        or any(segment in _SENSITIVE_REPOSITORY_SEGMENTS for segment in segments)
        or any(
            segments[index : index + len(sequence)] == sequence
            for sequence in _INTERNAL_REPOSITORY_PATH_SEQUENCES
            for index in range(len(segments) - len(sequence) + 1)
        )
    ):
        return _REDACTED_REPOSITORY_URL
    return value


def _sanitize_result(
    value: object,
    *,
    allow_confirmation_token: bool,
    key: str = "",
) -> object:
    normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
    if normalized_key == "repositoryurl" and isinstance(value, str):
        return _safe_repository_url(value)
    if _SECRET_KEY.fullmatch(normalized_key) is not None:
        if (
            normalized_key == "confirmationtoken"
            and allow_confirmation_token
            and isinstance(value, str)
            and _TOKEN.fullmatch(value) is not None
        ):
            return value
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(child): _sanitize_result(
                item,
                allow_confirmation_token=allow_confirmation_token,
                key=str(child),
            )
            for child, item in value.items()
        }
    if isinstance(value, list):
        return [
            _sanitize_result(
                item,
                allow_confirmation_token=allow_confirmation_token,
                key=key,
            )
            for item in value
        ]
    if isinstance(value, str):
        return _safe_public_text(value)
    return value


def _operation_result(kind: str, value: object) -> MarketplaceOperationResult:
    validated = validate_marketplace_operation_result({
        "type": kind,
        "value": _public(value),
    })
    public = validated.model_dump(mode="json", by_alias=True)
    sanitized = _sanitize_result(
        public,
        allow_confirmation_token=kind
        in {"install_review", "update_review", "remove_review", "trust_review"},
    )
    return validate_marketplace_operation_result(sanitized)


def _legacy_completion(completion: LifecycleCompletion) -> LegacyLifecycleCompletion:
    """Sanitize and validate both read projections without changing domain truth."""
    if completion.state != "succeeded":
        return LegacyLifecycleCompletion(completion=completion, result=None)
    try:
        public = _sanitize_result(
            completion.result.model_dump(mode="json"), allow_confirmation_token=False
        )
        lifecycle_result = TypeAdapter(LifecycleResult).validate_python(public)
        legacy_result = TypeAdapter(MarketplaceOperationResult).validate_python(
            public, by_name=True
        )
        return LegacyLifecycleCompletion(
            completion=replace(completion, result=lifecycle_result),
            result=validate_marketplace_operation_result(
                legacy_result.model_dump(mode="json", by_alias=True)
            ),
        )
    except Exception:
        return LegacyLifecycleCompletion(
            completion=LifecycleCompletion(
                state="failed",
                result=None,
                error=LifecyclePublicError(code="marketplace_operation_failed"),
                outcome=OutcomeUnknown(
                    type="outcome_unknown", reason="terminal_invalid"
                ),
            ),
            result=None,
        )


def _service_error(error: WorkflowMarketplaceError) -> HTTPException:
    code = error.code
    if not isinstance(code, str) or _ERROR_CODE.fullmatch(code) is None:
        code = "marketplace_request_failed"
    if code == "source_authentication_failed":
        status = 401
    elif code.endswith("_not_found"):
        status = 404
    elif code.endswith("_invalid") or code in {
        "source_name_invalid",
        "source_invalid",
        "catalog_query_invalid",
        "catalog_limit_invalid",
    }:
        status = 422
    else:
        status = 409
    return HTTPException(
        status_code=status,
        detail={
            "code": code,
            "message": "Workflow marketplace request failed.",
        },
    )


def _registry_error(error: MarketplaceOperationRegistryError) -> HTTPException:
    status = {
        "marketplace_operation_not_found": 404,
        "marketplace_operation_capacity": 429,
        "marketplace_operation_unavailable": 503,
        "marketplace_operation_conflict": 409,
    }.get(error.code, 409)
    return HTTPException(status_code=status, detail={"code": error.code})


def _service_call(call: Callable[[], _ValueT]) -> _ValueT:
    try:
        return call()
    except WorkflowMarketplaceError as error:
        raise _service_error(error)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "marketplace_internal_error",
                "message": "Workflow marketplace request failed.",
            },
        ) from error


def _start(
    registry: WorkflowMarketplaceOperationRegistry,
    kind: str,
    call: Callable[
        [CancellationToken], MarketplaceOperationResult | LegacyLifecycleCompletion
    ],
    *,
    actor: str,
    target: str | None = None,
    subject: LifecycleSubject | None = None,
    canonical_body: object = None,
) -> MarketplaceOperation:
    try:
        return registry.start(
            kind,
            call,
            actor=actor,
            target=target,
            subject=subject,
            canonical_body=canonical_body,
        )
    except MarketplaceOperationRegistryError as error:
        if error.code == "marketplace_admission_capacity":
            raise HTTPException(status_code=503, detail={"code": error.code}) from None
        raise _registry_error(error)


def _identity_target(identity: InstalledPackageIdentity) -> str:
    return f"package:{identity.source_key}/{identity.package_id}"


def create_marketplace_router(
    verified_operator: VerifiedOperator,
    *,
    context: WorkflowMarketplaceApiContext | None = None,
) -> APIRouter:
    """Build routes around the dashboard's already-verified operator seam."""

    api = context or WorkflowMarketplaceApiContext()
    router = APIRouter(prefix="/marketplace")

    @router.get(
        "/capabilities",
        response_model=MarketplaceCapabilitiesResponse,
        response_model_by_alias=False,
    )
    def capabilities(request: Request):
        _authorize(verified_operator, request, "read")
        _key, profile, _service, _registry = api.current()
        return MarketplaceCapabilitiesResponse(
            schema_version=1,
            profile=profile,
            capabilities=[
                "sources",
                "search",
                "installed",
                "updates",
                "transactions",
                "trust",
                "operations",
            ],
        )

    @router.get(
        "/sources",
        response_model=MarketplaceSourceListResponse,
        response_model_by_alias=False,
    )
    def list_sources(request: Request):
        _authorize(verified_operator, request, "read")
        _key, profile, service, _registry = api.current()
        source_records = _service_call(service.list_source_records)
        sources = []
        for source in source_records:
            public = _public(source)
            if not isinstance(public, dict):
                raise TypeError("marketplace source projection is invalid")
            message = public.get("message")
            if isinstance(message, str):
                public["message"] = redact_source_refresh_message(message)
            sources.append(
                MarketplaceSourceListItem.model_validate(
                    _sanitize_result(
                        public,
                        allow_confirmation_token=False,
                    )
                )
            )
        return MarketplaceSourceListResponse(
            profile=profile,
            sources=sorted(sources, key=lambda item: item.name),
        )

    @router.post(
        "/sources",
        status_code=201,
        response_model=MarketplaceSourceResponse,
        response_model_by_alias=False,
    )
    async def add_source(request: Request):
        _authorize(verified_operator, request, "admin")
        source = await _body(request, WorkflowMarketplaceSource)
        _key, profile, service, _registry = api.current()
        created = _service_call(lambda: service.add_source(source))
        return MarketplaceSourceResponse(
            status="created", profile=profile, source=created
        )

    @router.put(
        "/sources/{name}",
        response_model=MarketplaceSourceResponse,
        response_model_by_alias=False,
    )
    async def update_source(name: str, request: Request):
        _authorize(verified_operator, request, "admin")
        name = _source_name(name)
        body = await _body(request, MarketplaceSourceUpdateRequest)
        _key, profile, service, _registry = api.current()
        updated = _service_call(
            lambda: service.update_source(
                name,
                body.repository_url,
                ref=body.ref,
                enabled=body.enabled,
            )
        )
        return MarketplaceSourceResponse(
            status="updated", profile=profile, source=updated
        )

    @router.post(
        "/sources/{name}/enabled",
        response_model=MarketplaceSourceResponse,
        response_model_by_alias=False,
    )
    async def set_source_enabled(name: str, request: Request):
        _authorize(verified_operator, request, "admin")
        name = _source_name(name)
        body = await _body(request, MarketplaceSourceEnabledRequest)
        _key, profile, service, _registry = api.current()
        source = _service_call(lambda: service.set_source_enabled(name, body.enabled))
        return MarketplaceSourceResponse(
            status="enabled" if body.enabled else "disabled",
            profile=profile,
            source=source,
        )

    @router.delete(
        "/sources/{name}",
        response_model=MarketplaceSourceResponse,
        response_model_by_alias=False,
    )
    def remove_source(name: str, request: Request):
        _authorize(verified_operator, request, "admin")
        name = _source_name(name)
        _key, profile, service, _registry = api.current()
        source = _service_call(lambda: service.remove_source(name))
        return MarketplaceSourceResponse(
            status="removed", profile=profile, source=source
        )

    @router.post(
        "/sources/{name}/refresh",
        status_code=202,
        response_model=MarketplaceOperation,
        response_model_by_alias=False,
    )
    def refresh_source(name: str, request: Request):
        authority = _authorize(verified_operator, request, "admin")
        name = _source_name(name)
        key, _profile, service, registry = api.current()
        actor = _actor(authority, key)

        def run(cancellation: CancellationToken) -> LegacyLifecycleCompletion:
            cancellation.set_progress("fetching", 10)
            return _legacy_completion(
                complete_source_refresh(
                    service,
                    source_name=name,
                    call=lambda: service.refresh_source(
                        name, cancelled=cancellation.is_cancelled
                    ),
                )
            )

        return _start(
            registry,
            "refresh",
            run,
            actor=actor,
            target=f"source:{name}",
            subject=SourceSubject(type="source", source_name=name),
            canonical_body={"source_name": name},
        )

    @router.get(
        "/packages",
        response_model=MarketplacePackagePage,
        response_model_by_alias=False,
    )
    def search_packages(request: Request):
        _authorize(verified_operator, request, "read")
        query = _query(request, {"q", "source", "offset", "limit"})
        text = query.get("q", "")
        source = query.get("source")
        if len(text) > 256 or (source is not None and len(source) > 64):
            raise _request_error()
        if source is not None:
            source = _source_name(source)
        offset = _uint(query.get("offset"), default=0, maximum=199)
        limit = _uint(query.get("limit"), default=50, maximum=100)
        if limit < 1 or offset + limit > 200:
            raise _request_error()
        _key, profile, service, _registry = api.current()
        fetch_limit = min(200, offset + limit + 1)
        values = _service_call(
            lambda: service.search(text, source=source, limit=fetch_limit)
        )
        items = [
            MarketplaceCatalogPackage.model_validate(_public(asdict(item)))
            for item in values[offset : offset + limit]
        ]
        return MarketplacePackagePage(
            profile=profile,
            query=text,
            source=source,
            offset=offset,
            limit=limit,
            items=items,
            next_offset=(
                offset + len(items) if len(values) > offset + len(items) else None
            ),
        )

    @router.get(
        "/packages/{source_name}/{package_id}",
        status_code=202,
        response_model=MarketplaceOperation,
        response_model_by_alias=False,
    )
    def inspect_package(source_name: str, package_id: str, request: Request):
        authority = _authorize(verified_operator, request, "read")
        identity = _package_identity(source_name, package_id)
        identifier = f"{identity.source_key}/{identity.package_id}"
        key, _profile, service, registry = api.current()
        actor = _actor(authority, key)

        subject = PackageSubject(
            type="package",
            identity=PackageIdentity(
                source_key=identity.source_key, package_id=identity.package_id
            ),
        )

        def run(cancellation: CancellationToken) -> LegacyLifecycleCompletion:
            cancellation.set_progress("fetching", 10)
            return _legacy_completion(
                complete_read(
                    service,
                    kind="inspect",
                    subject=subject,
                    selection=None,
                    actor=actor,
                    call=lambda: service.inspect(
                        identifier, cancelled=cancellation.is_cancelled
                    ),
                )
            )

        return _start(
            registry,
            "package_detail",
            run,
            actor=actor,
            subject=subject,
            canonical_body=identity.model_dump(mode="json"),
        )

    @router.get(
        "/installed",
        response_model=MarketplaceInstalledPage,
        response_model_by_alias=False,
    )
    def installed_packages(request: Request):
        _authorize(verified_operator, request, "read")
        _key, profile, service, _registry = api.current()
        packages = _service_call(service.installed_packages)
        return MarketplaceInstalledPage(
            profile=profile,
            packages=sorted(
                packages,
                key=lambda item: (
                    item.identity.source_key,
                    item.identity.package_id,
                ),
            ),
        )

    @router.post(
        "/updates/check",
        status_code=202,
        response_model=MarketplaceOperation,
        response_model_by_alias=False,
    )
    async def check_updates(request: Request):
        authority = _authorize(verified_operator, request, "admin")
        body = await _body(request, MarketplaceUpdateCheckRequest)
        key, _profile, service, registry = api.current()
        actor = _actor(authority, key)

        subject = (
            PackageSubject(
                type="package",
                identity=PackageIdentity(
                    source_key=body.identity.source_key,
                    package_id=body.identity.package_id,
                ),
            )
            if body.identity
            else AllPackagesSubject(type="all_packages")
        )

        def run(cancellation: CancellationToken) -> LegacyLifecycleCompletion:
            cancellation.set_progress("fetching", 10)
            return _legacy_completion(
                complete_read(
                    service,
                    kind="update_check",
                    subject=subject,
                    selection=None,
                    actor=actor,
                    call=lambda: service.check_updates(
                        body.identity, cancelled=cancellation.is_cancelled
                    ),
                )
            )

        target = (
            _identity_target(body.identity)
            if body.identity is not None
            else "package:all"
        )
        return _start(
            registry,
            "update_check",
            run,
            actor=actor,
            target=target,
            subject=subject,
            canonical_body=body.model_dump(mode="json"),
        )

    @router.post("/install/prepare")
    @router.post("/install/confirm")
    @router.post("/update/prepare")
    @router.post("/update/confirm")
    @router.post("/remove/prepare")
    @router.post("/remove/confirm")
    @router.post("/trust/review")
    @router.post("/trust/grant")
    @router.post("/trust/revoke")
    def retired_preview_mutation(request: Request):
        _authorize(verified_operator, request, "admin")
        raise HTTPException(
            status_code=409,
            detail={"code": "marketplace_lifecycle_upgrade_required"},
        )

    @router.get(
        "/operations",
        response_model=MarketplaceOperationPage,
        response_model_by_alias=False,
    )
    def list_operations(request: Request):
        authority = _authorize(verified_operator, request, "read")
        query = _query(request, {"offset", "limit"})
        offset = _uint(query.get("offset"), default=0, maximum=10_000)
        limit = _uint(query.get("limit"), default=50, maximum=100)
        if limit < 1:
            raise _request_error()
        key, profile, _service, registry = api.current()
        actor = _actor(authority, key)
        return MarketplaceOperationPage(
            profile=profile,
            offset=offset,
            limit=limit,
            operations=list(registry.list(actor=actor, offset=offset, limit=limit)),
        )

    @router.get(
        "/operations/{operation_id}",
        response_model=MarketplaceOperation,
        response_model_by_alias=False,
    )
    def get_operation(operation_id: str, request: Request):
        authority = _authorize(verified_operator, request, "read")
        if _OPERATION_ID.fullmatch(operation_id) is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "marketplace_operation_not_found"},
            )
        key, _profile, _service, registry = api.current()
        actor = _actor(authority, key)
        try:
            return registry.get(operation_id, actor=actor)
        except MarketplaceOperationRegistryError as error:
            raise _registry_error(error)

    @router.post(
        "/operations/{operation_id}/cancel",
        response_model=MarketplaceOperation,
        response_model_by_alias=False,
    )
    def cancel_operation(operation_id: str, request: Request):
        authority = _authorize(verified_operator, request, "admin")
        if _OPERATION_ID.fullmatch(operation_id) is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "marketplace_operation_not_found"},
            )
        key, _profile, _service, registry = api.current()
        actor = _actor(authority, key)
        try:
            return registry.cancel(operation_id, actor=actor)
        except MarketplaceOperationRegistryError as error:
            raise _registry_error(error)

    return router


__all__ = [
    "MarketplaceCapabilitiesResponse",
    "MarketplaceCatalogPackage",
    "MarketplaceInstalledPage",
    "MarketplaceOperationPage",
    "MarketplacePackagePage",
    "MarketplaceSourceListResponse",
    "MarketplaceSourceListItem",
    "MarketplaceSourceResponse",
    "WorkflowMarketplaceApiContext",
    "create_marketplace_router",
]
