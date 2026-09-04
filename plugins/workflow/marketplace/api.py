"""Authenticated, profile-scoped REST adapter for the workflow marketplace."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Literal, Protocol, TypeVar

from fastapi import APIRouter, HTTPException, Request
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
)

from hermes_cli.git_source import (
    GitSourceError,
    safe_git_error,
    validate_credential_free_git_source,
)
from hermes_constants import get_hermes_home, hermes_home_key

from .models import (
    InstallRequest,
    InstalledPackage,
    InstalledPackageIdentity,
    PackageInspection,
    SOURCE_NAME_PATTERN,
    PACKAGE_ID_PATTERN,
    SEMANTIC_VERSION_PATTERN,
    SHA256_PATTERN,
    UpdateCheck,
    WorkflowMarketplaceSource,
)
from .operations import (
    CancellationToken,
    MarketplaceOperation,
    MarketplaceOperationRegistryError,
    MarketplaceOperationResult,
    WorkflowMarketplaceOperationRegistry,
    validate_marketplace_operation_result,
)
from .package import WorkflowMarketplaceError
from .service import WorkflowMarketplaceService


_BODY_BYTES_MAX = 64 * 1024
_JSON_NESTING_MAX = 64
_UINT = re.compile(r"^(0|[1-9][0-9]*)$", re.ASCII)
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,4096}$", re.ASCII)
_OPERATION_ID = re.compile(r"^wmop_[0-9a-f]{12}_[0-9a-f]{32}$", re.ASCII)
_COMMIT = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$", re.ASCII)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:access[_-]?token|refresh[_-]?token|api[_-]?key|auth(?:orization)?|"
    r"password|credentials?|client[_-]?secret|confirmation[_-]?token)\b\s*[=:]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_LOCAL_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:"
    r"/(?:private|tmp|users|home|var/folders|var/tmp|var/cache)/[^\s\"']*|"
    r"[A-Z]:\\(?:[^\s\"']+\\)*(?:[^\s\"']*)|"
    r"\\\\[^\\\s]+\\[^\s\"']+|"
    r"(?:/[^\s\"']+)*(?:\.staging|\.quarantine|/cache|/staging|/quarantine)"
    r"(?:/[^\s\"']*)?)"
)
_SECRET_KEY = re.compile(
    r"^(?:access|refresh)?token$|^api(?:key)?$|^auth(?:orization)?$|^password$|"
    r"^secret$|^clientsecret$|^credentials?$|^confirmationtoken$",
    re.ASCII,
)


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


class MarketplaceSourceListResponse(_StrictApiModel):
    profile: str = Field(min_length=1, max_length=256)
    sources: list[WorkflowMarketplaceSource] = Field(max_length=128)


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
    completed = subprocess.CompletedProcess(
        args=(), returncode=1, stdout="", stderr=value
    )
    sanitized = safe_git_error(completed).strip()
    sanitized = _CREDENTIAL_ASSIGNMENT.sub(r"\1[REDACTED]", sanitized)
    sanitized = _LOCAL_PATH.sub("[REDACTED_PATH]", sanitized)
    return sanitized[:4096]


def _sanitize_result(
    value: object,
    *,
    allow_confirmation_token: bool,
    key: str = "",
) -> object:
    normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
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
    call: Callable[[CancellationToken], MarketplaceOperationResult],
    *,
    actor: str,
    target: str | None = None,
) -> MarketplaceOperation:
    try:
        return registry.start(kind, call, actor=actor, target=target)
    except MarketplaceOperationRegistryError as error:
        raise _registry_error(error)


def _confirmation_target(
    service: WorkflowMarketplaceService,
    token: str,
    *,
    actor: str,
    operation: Literal["install", "update", "remove", "trust"],
) -> str:
    try:
        metadata = service.confirmation_metadata(
            token,
            actor=actor,
            operation=operation,
        )
    except WorkflowMarketplaceError:
        # Invalid, expired, or foreign tokens share one non-oracular reservation.
        return "package:unresolved-confirmation"
    return _identity_target(metadata.identity)


def _request_target(
    service: WorkflowMarketplaceService, request: InstallRequest
) -> str:
    return _service_call(lambda: service.canonical_install_target(request))


def _identity_target(identity: InstalledPackageIdentity) -> str:
    return f"package:{identity.source_key}/{identity.package_id}"


def _confirmed_call(
    cancellation: CancellationToken,
    call: Callable[[Callable[[], bool], Callable[[], bool]], object],
    *,
    result_type: str,
) -> MarketplaceOperationResult:
    cancellation.checkpoint()
    result = call(cancellation.is_cancelled, cancellation.enter_atomic)
    cancellation.mark_committed()
    return _operation_result(result_type, result)


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
        sources = _service_call(service.list_sources)
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

        def run(cancellation: CancellationToken) -> MarketplaceOperationResult:
            cancellation.set_progress("fetching", 10)
            value = service.refresh_source(name, cancelled=cancellation.is_cancelled)
            cancellation.checkpoint()
            cancellation.set_progress("verifying", 90)
            projection = MarketplaceSourceRefreshProjection.model_validate(
                asdict(value)
            )
            return _operation_result("source_refresh", projection)

        return _start(
            registry,
            "refresh",
            run,
            actor=actor,
            target=f"source:{name}",
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

        def run(cancellation: CancellationToken) -> MarketplaceOperationResult:
            cancellation.set_progress("fetching", 10)
            value = service.inspect(identifier, cancelled=cancellation.is_cancelled)
            cancellation.checkpoint()
            return _operation_result(
                "package_detail", PackageInspection.model_validate(value)
            )

        return _start(registry, "package_detail", run, actor=actor)

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

        def run(cancellation: CancellationToken) -> MarketplaceOperationResult:
            cancellation.set_progress("fetching", 10)
            checks = service.check_updates(
                body.identity, cancelled=cancellation.is_cancelled
            )
            cancellation.checkpoint()
            projection = MarketplaceUpdateChecksProjection(
                checks=[UpdateCheck.model_validate(item) for item in checks]
            )
            return _operation_result("update_checks", projection)

        target = (
            _identity_target(body.identity)
            if body.identity is not None
            else "package:all"
        )
        return _start(registry, "update_check", run, actor=actor, target=target)

    @router.post(
        "/install/prepare",
        status_code=202,
        response_model=MarketplaceOperation,
        response_model_by_alias=False,
    )
    async def prepare_install(request: Request):
        authority = _authorize(verified_operator, request, "admin")
        body = await _body(request, InstallRequest)
        key, _profile, service, registry = api.current()
        actor = _actor(authority, key)

        def run(cancellation: CancellationToken) -> MarketplaceOperationResult:
            cancellation.set_progress("fetching", 10)
            value = service.prepare_install(
                body,
                actor=actor,
                cancelled=cancellation.is_cancelled,
            )
            cancellation.checkpoint()
            cancellation.set_progress("reviewing", 90)
            return _operation_result("install_review", value)

        return _start(
            registry,
            "install_prepare",
            run,
            actor=actor,
            target=_request_target(service, body),
        )

    @router.post(
        "/install/confirm",
        status_code=202,
        response_model=MarketplaceOperation,
        response_model_by_alias=False,
    )
    async def confirm_install(request: Request):
        authority = _authorize(verified_operator, request, "admin")
        body = await _body(request, MarketplaceConfirmationRequest)
        key, _profile, service, registry = api.current()
        actor = _actor(authority, key)
        return _start(
            registry,
            "install_confirm",
            lambda cancellation: _confirmed_call(
                cancellation,
                lambda cancelled, enter_atomic: service.confirm_install(
                    body.confirmation_token,
                    actor=actor,
                    cancelled=cancelled,
                    enter_atomic=enter_atomic,
                ),
                result_type="installed_package",
            ),
            actor=actor,
            target=_confirmation_target(
                service,
                body.confirmation_token,
                actor=actor,
                operation="install",
            ),
        )

    @router.post(
        "/update/prepare",
        status_code=202,
        response_model=MarketplaceOperation,
        response_model_by_alias=False,
    )
    async def prepare_update(request: Request):
        authority = _authorize(verified_operator, request, "admin")
        identity = await _body(request, MarketplaceIdentityRequest)
        key, _profile, service, registry = api.current()
        actor = _actor(authority, key)

        def run(cancellation: CancellationToken) -> MarketplaceOperationResult:
            cancellation.set_progress("fetching", 10)
            value = service.prepare_update(
                identity,
                actor=actor,
                cancelled=cancellation.is_cancelled,
            )
            cancellation.checkpoint()
            cancellation.set_progress("reviewing", 90)
            return _operation_result("update_review", value)

        return _start(
            registry,
            "update_prepare",
            run,
            actor=actor,
            target=_identity_target(identity),
        )

    @router.post(
        "/update/confirm",
        status_code=202,
        response_model=MarketplaceOperation,
        response_model_by_alias=False,
    )
    async def confirm_update(request: Request):
        authority = _authorize(verified_operator, request, "admin")
        body = await _body(request, MarketplaceConfirmationRequest)
        key, _profile, service, registry = api.current()
        actor = _actor(authority, key)
        return _start(
            registry,
            "update_confirm",
            lambda cancellation: _confirmed_call(
                cancellation,
                lambda cancelled, enter_atomic: service.confirm_update(
                    body.confirmation_token,
                    actor=actor,
                    cancelled=cancelled,
                    enter_atomic=enter_atomic,
                ),
                result_type="updated_package",
            ),
            actor=actor,
            target=_confirmation_target(
                service,
                body.confirmation_token,
                actor=actor,
                operation="update",
            ),
        )

    @router.post(
        "/remove/prepare",
        status_code=202,
        response_model=MarketplaceOperation,
        response_model_by_alias=False,
    )
    async def prepare_remove(request: Request):
        authority = _authorize(verified_operator, request, "admin")
        identity = await _body(request, MarketplaceIdentityRequest)
        key, _profile, service, registry = api.current()
        actor = _actor(authority, key)

        def run(cancellation: CancellationToken) -> MarketplaceOperationResult:
            cancellation.set_progress("reviewing", 25)
            value = service.prepare_remove(identity, actor=actor)
            cancellation.checkpoint()
            return _operation_result("remove_review", value)

        return _start(
            registry,
            "remove_prepare",
            run,
            actor=actor,
            target=_identity_target(identity),
        )

    @router.post(
        "/remove/confirm",
        status_code=202,
        response_model=MarketplaceOperation,
        response_model_by_alias=False,
    )
    async def confirm_remove(request: Request):
        authority = _authorize(verified_operator, request, "admin")
        body = await _body(request, MarketplaceConfirmationRequest)
        key, _profile, service, registry = api.current()
        actor = _actor(authority, key)
        return _start(
            registry,
            "remove_confirm",
            lambda cancellation: _confirmed_call(
                cancellation,
                lambda cancelled, enter_atomic: service.confirm_remove(
                    body.confirmation_token,
                    actor=actor,
                    cancelled=cancelled,
                    enter_atomic=enter_atomic,
                ),
                result_type="removed_package",
            ),
            actor=actor,
            target=_confirmation_target(
                service,
                body.confirmation_token,
                actor=actor,
                operation="remove",
            ),
        )

    @router.post(
        "/trust/review",
        status_code=202,
        response_model=MarketplaceOperation,
        response_model_by_alias=False,
    )
    async def review_trust(request: Request):
        authority = _authorize(verified_operator, request, "admin")
        body = await _body(request, MarketplaceTrustReviewRequest)
        key, _profile, service, registry = api.current()
        actor = _actor(authority, key)

        def run(cancellation: CancellationToken) -> MarketplaceOperationResult:
            cancellation.set_progress("reviewing", 25)
            value = service.review_trust(
                body.identity,
                actor=actor,
                workflow_name=body.workflow_name,
            )
            cancellation.checkpoint()
            return _operation_result("trust_review", value)

        return _start(
            registry,
            "trust_prepare",
            run,
            actor=actor,
            target=_identity_target(body.identity),
        )

    @router.post(
        "/trust/grant",
        status_code=202,
        response_model=MarketplaceOperation,
        response_model_by_alias=False,
    )
    async def grant_trust(request: Request):
        authority = _authorize(verified_operator, request, "admin")
        body = await _body(request, MarketplaceConfirmationRequest)
        key, _profile, service, registry = api.current()
        actor = _actor(authority, key)

        def call(cancelled, enter_atomic) -> MarketplaceTrustStatesProjection:
            states = service.grant_trust(
                body.confirmation_token,
                actor=actor,
                cancelled=cancelled,
                enter_atomic=enter_atomic,
            )
            return MarketplaceTrustStatesProjection(
                workflows=[
                    MarketplaceTrustState(workflow_name=name, state=states[name])
                    for name in sorted(states)
                ]
            )

        return _start(
            registry,
            "trust_confirm",
            lambda cancellation: _confirmed_call(
                cancellation, call, result_type="trust_grant"
            ),
            actor=actor,
            target=_confirmation_target(
                service,
                body.confirmation_token,
                actor=actor,
                operation="trust",
            ),
        )

    @router.post(
        "/trust/revoke",
        status_code=202,
        response_model=MarketplaceOperation,
        response_model_by_alias=False,
    )
    async def revoke_trust(request: Request):
        authority = _authorize(verified_operator, request, "admin")
        body = await _body(request, MarketplaceTrustRevokeRequest)
        key, _profile, service, registry = api.current()
        actor = _actor(authority, key)

        def call(cancelled, enter_atomic) -> MarketplaceTrustRevocationProjection:
            revoked = service.revoke_trust(
                body.identity,
                workflow_name=body.workflow_name,
                cancelled=cancelled,
                enter_atomic=enter_atomic,
            )
            return MarketplaceTrustRevocationProjection(revoked=revoked)

        return _start(
            registry,
            "trust_revoke",
            lambda cancellation: _confirmed_call(
                cancellation, call, result_type="trust_revoke"
            ),
            actor=actor,
            target=_identity_target(body.identity),
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
    "MarketplaceSourceResponse",
    "WorkflowMarketplaceApiContext",
    "create_marketplace_router",
]
