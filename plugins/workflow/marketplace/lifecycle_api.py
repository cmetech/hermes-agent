"""Authenticated V2 HTTP lifecycle adapter; domain and registry own authority."""

from dataclasses import replace
from datetime import datetime, timezone
import hmac
from typing import Generic, Literal, TypeVar

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, TypeAdapter, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from hermes_cli.git_source import (
    GitSourceError,
    canonical_git_source,
    resolve_git_source,
    validate_credential_free_git_source,
)
from . import lifecycle_models as wire
from .api import (
    VerifiedOperator,
    WorkflowMarketplaceApiContext,
    _OPERATION_ID,
    _TOKEN,
    _actor,
    _authorize,
    _body,
    _identity_target,
    _query,
    _registry_error,
    _request_error,
    _sanitize_result,
    _service_error,
    _source_name,
    _uint,
)
from .lifecycle_state import (
    complete_mutation,
    complete_read,
    complete_source_refresh,
    read_package_state,
)
from .models import InstallRequest, InstalledPackageIdentity
from .operations import (
    AdmissionEvicted,
    AdmissionFound,
    LifecycleCompletion,
    LifecycleOperationPage,
    MarketplaceOperationRegistryError,
    ReviewTokenResponse,
)
from .package import WorkflowMarketplaceError
from .service import direct_source_key


_REQUEST_PATTERN = r"^wmreq_[0-9a-f]{32}_[0-9]{13}_[0-9a-f]{32}$"
_BodyT = TypeVar("_BodyT", bound=wire.StrictLifecycleModel)
_RESULT = TypeAdapter(wire.LifecycleResult)
_OUTCOME = TypeAdapter(wire.LifecycleOutcome)


class _StartRequest(wire.StrictLifecycleModel, Generic[_BodyT]):
    request_id: str = Field(pattern=_REQUEST_PATTERN)
    body: _BodyT


class _EmptyBody(wire.StrictLifecycleModel):
    pass


class _InstallBody(wire.StrictLifecycleModel):
    identifier: str
    ref: str | None = None
    package_path: str | None = None

    def domain(self) -> InstallRequest:
        return InstallRequest(
            identifier=self.identifier, ref=self.ref, packagePath=self.package_path
        )

    @model_validator(mode="after")
    def validate_request(self):
        self.domain()
        return self


class _IdentityBody(wire.StrictLifecycleModel):
    identity: wire.PackageIdentity


class _CheckBody(wire.StrictLifecycleModel):
    identity: wire.PackageIdentity | None = None


class _TrustBody(_IdentityBody):
    workflow_name: str | None = None

    def selection(self) -> wire.TrustSelection:
        return (
            wire.AllTrustSelection(type="all")
            if self.workflow_name is None
            else wire.OneTrustSelection(type="one", workflow_name=self.workflow_name)
        )

    @model_validator(mode="after")
    def validate_selection(self):
        self.selection()
        return self


class _ConfirmBody(wire.StrictLifecycleModel):
    confirmation_token: str = Field(pattern=_TOKEN.pattern, repr=False)
    prepare_operation_id: str = Field(pattern=_OPERATION_ID.pattern)
    subject: wire.PackageSubject
    selection: wire.TrustSelection | None
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class _ReviewTokenBody(wire.StrictLifecycleModel):
    review_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    subject: wire.LifecycleSubject
    selection: wire.TrustSelection | None


class _Capabilities(wire.StrictLifecycleModel):
    schema_version: Literal[2] = 2
    profile: str = Field(min_length=1, max_length=256)
    registry_epoch: str = Field(pattern=r"^[0-9a-f]{32}$")
    server_time: str
    capabilities: list[
        Literal[
            "operations",
            "admission_replay",
            "package_state",
            "transactions",
            "updates",
            "trust",
            "sources",
            "inspect",
        ]
    ] = Field(max_length=8)


def _domain_identity(identity: wire.PackageIdentity) -> InstalledPackageIdentity:
    return InstalledPackageIdentity(
        sourceKey=identity.source_key, packageId=identity.package_id
    )


def _unavailable():
    raise HTTPException(
        status_code=410, detail={"code": "marketplace_review_unavailable"}
    )


def _call(call):
    """Bound errors at HTTP without reflecting bodies, tokens or private paths."""
    try:
        return call()
    except HTTPException:
        raise
    except MarketplaceOperationRegistryError as error:
        status = {
            "marketplace_admission_not_found": 404,
            "marketplace_review_unavailable": 410,
            "marketplace_list_expired": 410,
            "marketplace_admission_capacity": 503,
            "marketplace_list_capacity": 503,
        }.get(error.code)
        if status is not None:
            raise HTTPException(
                status_code=status, detail={"code": error.code}
            ) from None
        raise _registry_error(error) from None
    except WorkflowMarketplaceError as error:
        raise _service_error(error) from None
    except (ValueError, ValidationError, GitSourceError):
        raise _request_error() from None
    except Exception:
        raise HTTPException(
            status_code=500, detail={"code": "marketplace_internal_error"}
        ) from None


def _public_completion(completion: LifecycleCompletion) -> LifecycleCompletion:
    """Sanitize both correlated projections, preserving the domain outcome."""
    try:
        result = (
            _RESULT.validate_python(
                _sanitize_result(
                    completion.result.model_dump(mode="json"),
                    allow_confirmation_token=False,
                )
            )
            if completion.result is not None
            else None
        )
        outcome = _OUTCOME.validate_python(
            _sanitize_result(
                completion.outcome.model_dump(mode="json"),
                allow_confirmation_token=False,
            )
        )
        return replace(completion, result=result, outcome=outcome)
    except Exception:
        return LifecycleCompletion(
            state="failed",
            result=None,
            error=wire.LifecyclePublicError(code="marketplace_operation_failed"),
            outcome=wire.OutcomeUnknown(
                type="outcome_unknown", reason="terminal_invalid"
            ),
        )


def _install_subject(service, registry, body):
    request = body.domain()
    # This is the validated route identifier, never a registry target string.
    source, separator, package = request.identifier.partition("/")
    if separator and package and "/" not in package:
        try:
            service.catalog.source_store.get(source)
        except WorkflowMarketplaceError as error:
            if error.code != "source_not_found":
                raise
        else:
            if request.ref is not None or request.package_path is not None:
                raise _request_error()
            return wire.PackageSubject(
                type="package",
                identity=wire.PackageIdentity(
                    source_key=source,
                    package_id=package,
                ),
            ), None
    validate_credential_free_git_source(request.identifier)
    resolved = resolve_git_source(request.identifier)
    repository = canonical_git_source(resolved.clone_url, None)
    validate_credential_free_git_source(repository)
    if (
        request.package_path is not None
        and resolved.subdirectory is not None
        and request.package_path != resolved.subdirectory
    ):
        raise _request_error()
    path = request.package_path or resolved.subdirectory
    # Reuse the package-path validator for embedded URL paths, too.
    InstallRequest(identifier=repository, ref=request.ref, packagePath=path)
    selector = registry.admissions.direct_selector_id(repository, request.ref, path)
    return wire.DirectInstallSubject(
        type="direct_install",
        source_key=direct_source_key(repository),
        selector_id=selector,
    ), (repository, request.ref, path)


def _authorize_confirm(service, registry, actor, kind, body):
    try:
        prepared = registry.get_lifecycle(body.prepare_operation_id, actor=actor)
        expected_kind = kind.removesuffix("_confirm") + "_prepare"
        if (
            prepared.kind != expected_kind
            or prepared.state != "succeeded"
            or prepared.selection != body.selection
            or prepared.result.value.review_digest != body.review_digest
            or prepared.result.value.identity != body.subject.identity
        ):
            _unavailable()
        token = registry.review_token(
            prepared.id,
            actor=actor,
            registry_epoch=registry.admissions.epoch,
            review_digest=body.review_digest,
            subject=prepared.subject,
            selection=body.selection,
        )
        if not hmac.compare_digest(token.confirmation_token, body.confirmation_token):
            _unavailable()
        metadata = service.confirmation_metadata(
            body.confirmation_token,
            actor=actor,
            operation=kind.removesuffix("_confirm"),
        )
        if metadata.identity != _domain_identity(body.subject.identity):
            _unavailable()
    except (
        MarketplaceOperationRegistryError,
        WorkflowMarketplaceError,
        AttributeError,
    ):
        _unavailable()


def create_lifecycle_router(
    context: WorkflowMarketplaceApiContext, verified_operator: VerifiedOperator
) -> APIRouter:
    router = APIRouter(prefix="/lifecycle/v2")

    def scope(request, capability):
        authority = _authorize(verified_operator, request, capability)
        key, profile, service, registry = context.current()
        return profile, service, registry, _actor(authority, key)

    @router.get(
        "/capabilities", response_model=_Capabilities, response_model_by_alias=False
    )
    def capabilities(request: Request):
        def run():
            profile, _, registry, _ = scope(request, "read")
            return _Capabilities(
                profile=profile,
                registry_epoch=registry.admissions.epoch,
                server_time=datetime
                .now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                capabilities=[
                    "operations",
                    "admission_replay",
                    "package_state",
                    "transactions",
                    "updates",
                    "trust",
                    "sources",
                    "inspect",
                ],
            )

        return _call(run)

    @router.get(
        "/operations",
        response_model=LifecycleOperationPage,
        response_model_by_alias=False,
    )
    def list_operations(request: Request):
        def run():
            _, _, registry, actor = scope(request, "read")
            query = _query(request, {"cursor", "limit"})
            return registry.list_snapshot(
                actor=actor,
                limit=_uint(query.get("limit"), default=100, maximum=100),
                cursor=query.get("cursor"),
            )

        return _call(run)

    def exact(request, operation_id, capability, cancel=False):
        _, _, registry, actor = scope(request, capability)
        if _OPERATION_ID.fullmatch(operation_id) is None:
            raise HTTPException(
                status_code=404, detail={"code": "marketplace_operation_not_found"}
            )
        operation = (
            registry.cancel_lifecycle(operation_id, actor=actor)
            if cancel
            else registry.get_lifecycle(operation_id, actor=actor)
        )
        if operation.id != operation_id:
            raise ValueError("operation identity is inconsistent")
        return wire.LifecycleOperation.model_validate_json(operation.model_dump_json())

    @router.get(
        "/operations/{operation_id}",
        response_model=wire.LifecycleOperation,
        response_model_by_alias=False,
    )
    def get_operation(operation_id: str, request: Request):
        return _call(lambda: exact(request, operation_id, "read"))

    @router.post(
        "/operations/{operation_id}/cancel",
        response_model=wire.LifecycleOperation,
        response_model_by_alias=False,
    )
    def cancel_operation(operation_id: str, request: Request):
        return _call(lambda: exact(request, operation_id, "admin", True))

    @router.get(
        "/admissions/{request_id}",
        response_model=AdmissionFound | AdmissionEvicted,
        response_model_by_alias=False,
    )
    def admission(request_id: str, request: Request):
        def run():
            _, _, registry, actor = scope(request, "read")
            return registry.lookup_admission(request_id, actor=actor)

        return _call(run)

    @router.post(
        "/operations/{operation_id}/review-token",
        response_model=ReviewTokenResponse,
        response_model_by_alias=False,
    )
    async def review_token(operation_id: str, request: Request):
        _authorize(verified_operator, request, "admin")
        body = await _body(request, _ReviewTokenBody)

        def run():
            _, _, registry, actor = scope(request, "admin")
            if _OPERATION_ID.fullmatch(operation_id) is None:
                _unavailable()
            return registry.review_token(
                operation_id,
                actor=actor,
                registry_epoch=registry.admissions.epoch,
                review_digest=body.review_digest,
                subject=body.subject,
                selection=body.selection,
            )

        return await run_in_threadpool(_call, run)

    @router.get(
        "/packages/{source_key}/{package_id}/state",
        response_model=wire.PackageState,
        response_model_by_alias=False,
    )
    def package_state(source_key: str, package_id: str, request: Request):
        def run():
            profile, service, registry, _ = scope(request, "read")
            expected = wire.PackageIdentity(
                source_key=source_key, package_id=package_id
            )
            identity = _domain_identity(expected)
            state = read_package_state(service, identity)
            if state.identity != expected or state.profile != profile:
                raise HTTPException(
                    status_code=500, detail={"code": "marketplace_internal_error"}
                )
            active = registry.intersects_active_mutation(identity)
            public = _sanitize_result(
                state.model_dump(mode="json"), allow_confirmation_token=False
            )
            public["busy"] = state.busy or active
            return wire.PackageState.model_validate(public)

        return _call(run)

    def start_operation(request, envelope, kind, *, source_name=None, identity=None):
        capability = "read" if kind == "inspect" else "admin"
        _, service, registry, actor = scope(request, capability)
        body = envelope.body
        selection = None
        direct = None
        if kind == "refresh":
            subject = wire.SourceSubject(
                type="source", source_name=_source_name(source_name)
            )
        elif kind == "inspect":
            subject = wire.PackageSubject(type="package", identity=identity)
        elif kind == "install_prepare":
            # Source membership may have changed after admission. The original
            # receipt supplies its subject; start still checks the exact HMAC
            # body/kind/selection before returning that receipt. If it expires
            # between these calls, its admission window already forbids new work.
            try:
                prior = registry.lookup_admission(envelope.request_id, actor=actor)
            except MarketplaceOperationRegistryError as error:
                if error.code != "marketplace_admission_not_found":
                    raise
                subject, direct = _install_subject(service, registry, body)
            else:
                subject = (
                    prior.operation if isinstance(prior, AdmissionFound) else prior
                ).subject
        elif kind.endswith("_confirm"):
            subject, selection = body.subject, body.selection
        elif kind == "update_check" and body.identity is None:
            subject = wire.AllPackagesSubject(type="all_packages")
        else:
            subject = wire.PackageSubject(type="package", identity=body.identity)
            if kind.startswith("trust_"):
                selection = body.selection()
        target = (
            _identity_target(_domain_identity(subject.identity))
            if isinstance(subject, wire.PackageSubject)
            else f"source:{subject.source_name}"
            if isinstance(subject, wire.SourceSubject)
            else f"direct:{subject.selector_id}"
            if isinstance(subject, wire.DirectInstallSubject)
            else "package:all"
        )

        def worker(cancellation):
            def domain():
                cancelled = cancellation.is_cancelled
                if kind == "refresh":
                    return service.refresh_source(
                        subject.source_name, cancelled=cancelled
                    )
                if kind == "inspect":
                    return service.inspect(
                        f"{subject.identity.source_key}/{subject.identity.package_id}",
                        cancelled=cancelled,
                    )
                if kind == "update_check":
                    return service.check_updates(
                        _domain_identity(body.identity) if body.identity else None,
                        cancelled=cancelled,
                    )
                if kind == "install_prepare":
                    review = service.prepare_install(
                        body.domain(), actor=actor, cancelled=cancelled
                    )
                    if direct is not None:
                        repository, ref, path = direct
                        if (
                            review.repository_url != repository
                            or review.configured_ref != ref
                            or (path is not None and review.package_path != path)
                            or review.identity.source_key != subject.source_key
                        ):
                            raise WorkflowMarketplaceError(
                                "confirmation_token_invalid",
                                "Direct review binding is invalid.",
                            )
                    return review
                if kind == "update_prepare":
                    return service.prepare_update(
                        _domain_identity(subject.identity),
                        actor=actor,
                        cancelled=cancelled,
                    )
                if kind == "remove_prepare":
                    return service.prepare_remove(
                        _domain_identity(subject.identity), actor=actor
                    )
                if kind == "trust_prepare":
                    return service.review_trust(
                        _domain_identity(subject.identity),
                        actor=actor,
                        workflow_name=body.workflow_name,
                    )
                if kind == "trust_revoke":
                    return service.revoke_trust(
                        _domain_identity(subject.identity),
                        workflow_name=body.workflow_name,
                        cancelled=cancelled,
                        enter_atomic=cancellation.enter_atomic,
                    )
                method = (
                    service.grant_trust
                    if kind == "trust_confirm"
                    else getattr(service, "confirm_" + kind.removesuffix("_confirm"))
                )
                return method(
                    body.confirmation_token,
                    actor=actor,
                    cancelled=cancelled,
                    enter_atomic=cancellation.enter_atomic,
                )

            if kind == "refresh":
                completion = complete_source_refresh(
                    service, source_name=subject.source_name, call=domain
                )
            else:
                producer = (
                    complete_mutation
                    if kind.endswith("_confirm") or kind == "trust_revoke"
                    else complete_read
                )
                completion = producer(
                    service,
                    kind=kind,
                    subject=subject,
                    selection=selection,
                    actor=actor,
                    call=domain,
                )
            return _public_completion(completion)

        return registry.start(
            kind,
            worker,
            actor=actor,
            target=target,
            request_id=envelope.request_id,
            canonical_body=body.model_dump(mode="json"),
            subject=subject,
            selection=selection,
            authorize=(lambda: _authorize_confirm(service, registry, actor, kind, body))
            if kind.endswith("_confirm")
            else None,
        )

    def register_start(path, kind, body_model):
        async def endpoint(request: Request):
            _authorize(verified_operator, request, "admin")
            envelope = await _body(request, _StartRequest[body_model])
            return await run_in_threadpool(
                _call, lambda: start_operation(request, envelope, kind)
            )

        router.add_api_route(
            path,
            endpoint,
            methods=["POST"],
            status_code=202,
            response_model=wire.LifecycleOperation | AdmissionEvicted,
            response_model_by_alias=False,
            name=kind,
        )

    for path, kind, model in (
        ("/updates/check", "update_check", _CheckBody),
        ("/install/prepare", "install_prepare", _InstallBody),
        ("/install/confirm", "install_confirm", _ConfirmBody),
        ("/update/prepare", "update_prepare", _IdentityBody),
        ("/update/confirm", "update_confirm", _ConfirmBody),
        ("/remove/prepare", "remove_prepare", _IdentityBody),
        ("/remove/confirm", "remove_confirm", _ConfirmBody),
        ("/trust/review", "trust_prepare", _TrustBody),
        ("/trust/grant", "trust_confirm", _ConfirmBody),
        ("/trust/revoke", "trust_revoke", _TrustBody),
    ):
        register_start(path, kind, model)

    @router.post(
        "/sources/{source_name}/refresh",
        status_code=202,
        response_model=wire.LifecycleOperation | AdmissionEvicted,
        response_model_by_alias=False,
    )
    async def refresh(source_name: str, request: Request):
        _authorize(verified_operator, request, "admin")
        envelope = await _body(request, _StartRequest[_EmptyBody])
        return await run_in_threadpool(
            _call,
            lambda: start_operation(
                request, envelope, "refresh", source_name=source_name
            ),
        )

    @router.post(
        "/packages/{source_key}/{package_id}",
        status_code=202,
        response_model=wire.LifecycleOperation | AdmissionEvicted,
        response_model_by_alias=False,
    )
    async def inspect(source_key: str, package_id: str, request: Request):
        _authorize(verified_operator, request, "read")
        envelope = await _body(request, _StartRequest[_EmptyBody])
        return await run_in_threadpool(
            _call,
            lambda: start_operation(
                request,
                envelope,
                "inspect",
                identity=wire.PackageIdentity(
                    source_key=source_key, package_id=package_id
                ),
            ),
        )

    return router
