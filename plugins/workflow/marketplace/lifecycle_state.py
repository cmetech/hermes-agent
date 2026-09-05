"""Locked domain evidence for lifecycle adapters; no registry or route mutations."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import threading
from typing import TYPE_CHECKING, Literal

from pydantic import TypeAdapter

from . import lifecycle_models as wire
from .models import (
    InstallReview,
    InstalledPackageIdentity,
    RemoveReview,
    TrustReview,
    UpdateReview,
)
from .operations import (
    LifecycleCompletion,
    ReviewTokenMetadata,
    MarketplaceOperationCancelled,
)
from .package import WorkflowMarketplaceError, load_distribution

if TYPE_CHECKING:
    from .service import WorkflowMarketplaceService


_RESULT = TypeAdapter(wire.LifecycleResult)
_RECOVERY = {
    "transaction_rollback_failed": "rollback_failed",
    "transaction_recovery_ambiguous": "recovery_ambiguous",
}


def _identity(identity):
    return wire.PackageIdentity.model_validate(
        identity.model_dump(mode="json", by_alias=False)
    )


def _utc(service):
    from datetime import timezone

    return service.clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _unconfirmed(service, identity, *, recovery="unconfirmed", busy=False):
    return wire.PackageState(
        profile=service.profile,
        identity=_identity(identity),
        observed_at=_utc(service),
        state="unconfirmed",
        installed=None,
        trust=None,
        recovery=recovery,
        busy=busy,
    )


def _state_locked(service, identity, *, trust_payload=None):
    """Caller owns the marketplace lock; supplied trust payload owns its lock too."""
    from .transactions import _parse_timestamp

    journals = service.transactions._read_journals().journals
    relevant = [j for j in journals if j.identity == identity]
    if relevant:
        busy = any(
            j.phase in {"install_consumed", "remove_consumed"}
            and _parse_timestamp(j.consumed_expires_at) > service.clock()
            for j in relevant
        )
        owned = all(service.transactions._journal_markers_match(j) for j in relevant)
        return _unconfirmed(
            service,
            identity,
            recovery="required" if owned else "unconfirmed",
            busy=busy,
        )
    records = service.installed_store._read_state().packages
    provenance = next(
        (r.provenance for r in records if r.provenance.identity == identity), None
    )
    root = service.installed_store.package_root(identity)
    try:
        root.lstat()
        exists = True
    except FileNotFoundError:
        exists = False
    if provenance is None:
        if exists:
            return _unconfirmed(service, identity)
        return wire.PackageState(
            profile=service.profile,
            identity=_identity(identity),
            observed_at=_utc(service),
            state="absent",
            installed=None,
            trust=None,
            recovery="clear",
            busy=False,
        )
    distribution = load_distribution(
        root, expected_digest=provenance.distribution_digest
    )
    if (
        distribution.manifest.id != identity.package_id
        or distribution.manifest.version != provenance.package_version
        or distribution.manifest.schema_version != provenance.contract_version
        or sorted(m.definition for m in distribution.manifest.workflows)
        != sorted(provenance.workflow_paths)
    ):
        return _unconfirmed(service, identity)
    _, workflows, _, _ = service._trust_review_parts(identity, installed=provenance)

    def project(payload):
        trust = wire.TrustSnapshot(
            identity=_identity(identity),
            distribution_digest=distribution.digest,
            workflows=[
                wire.TrustWorkflowState(
                    workflow_name=w.workflow_name,
                    definition_path=w.definition_path,
                    state=service.trust_store.check_snapshot(
                        payload, w.package_digest, risk_digest=w.risk_digest
                    ),
                )
                for w in workflows
            ],
        )
        return wire.PackageState(
            profile=service.profile,
            identity=_identity(identity),
            observed_at=_utc(service),
            state="installed",
            installed=service._installed_projection(provenance).model_dump(
                mode="json", by_alias=False
            ),
            trust=trust,
            recovery="clear",
            busy=False,
        )

    if trust_payload is not None:
        return project(trust_payload)
    return service.trust_store.observe_snapshot(project)


def read_package_state(
    service: WorkflowMarketplaceService, identity: InstalledPackageIdentity
) -> wire.PackageState:
    identity = InstalledPackageIdentity.model_validate(
        identity.model_dump(mode="json", by_alias=True)
    )
    try:
        with service.transactions._locked():
            return _state_locked(service, identity)
    except Exception as error:
        return _unconfirmed(
            service,
            identity,
            busy=getattr(error, "code", None) == "transaction_lock_timeout",
        )


def _execution():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


@dataclass
class _Evidence:
    service: object
    kind: str
    subject: wire.LifecycleSubject
    selection: wire.TrustSelection | None
    actor: str
    execution: tuple
    completion: LifecycleCompletion | None = None
    used: bool = False
    reused: bool = False
    invalid_scope: threading.Event = field(default_factory=threading.Event, repr=False)
    closed: threading.Event = field(default_factory=threading.Event, repr=False)
    entered: bool = False
    committed: bool = False
    cancelled: bool = False


@dataclass
class _Boundary:
    service: object
    identity: InstalledPackageIdentity
    execution: tuple
    entered: bool = False
    rollback: bool = False
    previous: object = None
    trust_state: wire.PackageState | None = None


_evidence: ContextVar[_Evidence | None] = ContextVar(
    "marketplace_lifecycle_evidence", default=None
)
_boundary: ContextVar[_Boundary | None] = ContextVar(
    "marketplace_lifecycle_boundary", default=None
)


def _current_evidence(service):
    value = _evidence.get()
    if value is None:
        return None
    if value.closed.is_set() or value.execution != _execution():
        value.invalid_scope.set()
        raise WorkflowMarketplaceError(
            "marketplace_operation_failed",
            "lifecycle evidence scope is invalid",
        )
    return value if value.service is service else None


def _failure(error, outcome):
    try:
        public = wire.LifecyclePublicError(
            code=getattr(error, "code", "marketplace_operation_failed")
        )
    except Exception:
        public = wire.LifecyclePublicError(code="marketplace_operation_failed")
    return LifecycleCompletion(
        state="failed", result=None, error=public, outcome=outcome
    )


def _unknown(error=None):
    if getattr(error, "code", None) in _RECOVERY:
        return _failure(
            error,
            wire.RecoveryRequiredOutcome(
                type="recovery_required", reason=_RECOVERY[error.code]
            ),
        )
    return _failure(
        error, wire.OutcomeUnknown(type="outcome_unknown", reason="terminal_invalid")
    )


def _record_rollback(store, identity, previous):
    """Transaction's restored/cleaned boundary, still requiring a full state proof."""
    boundary = _boundary.get()
    if (
        boundary
        and boundary.execution == _execution()
        and boundary.service.transactions is store
        and boundary.identity == identity
    ):
        boundary.rollback = True
        boundary.previous = previous


def _capture_trust(service, identity, payload):
    state = _state_locked(service, identity, trust_payload=payload)
    boundary = _boundary.get()
    if (
        boundary
        and boundary.execution == _execution()
        and boundary.service is service
        and boundary.identity == identity
    ):
        boundary.trust_state = state
    return state


def domain_mutation(kind):
    """Preserve legacy return values while recording at the service's locked boundary."""

    def decorate(method):
        @wraps(method)
        def wrapped(service, *args, **kwargs):
            evidence = _current_evidence(service)
            if evidence:
                if evidence.used:
                    evidence.reused = True
                    raise WorkflowMarketplaceError(
                        "marketplace_operation_failed",
                        "lifecycle evidence collector is already used",
                    )
                evidence.used = True
            boundary = None
            handle = None
            try:
                with service.transactions._locked():
                    if kind == "trust_revoke":
                        identity = args[0] if args else kwargs["identity"]
                        actual_selection = (
                            wire.OneTrustSelection(
                                type="one", workflow_name=kwargs["workflow_name"]
                            )
                            if kwargs.get("workflow_name")
                            else wire.AllTrustSelection(type="all")
                        )
                    else:
                        value = (
                            args[0]
                            if args
                            else kwargs.get("token", kwargs.get("review_or_token"))
                        )
                        token = (
                            value.confirmation_token
                            if isinstance(value, TrustReview)
                            else value
                        )
                        operation = (
                            "trust"
                            if kind == "trust_confirm"
                            else kind.removesuffix("_confirm")
                        )
                        identity = service.confirmation_metadata(
                            token, actor=kwargs["actor"], operation=operation
                        ).identity
                        actual_selection = None
                        if operation == "trust" and evidence:
                            actual_selection = service._trust_confirmations.selection(
                                token
                            )
                    if evidence and (
                        evidence.kind != kind
                        or evidence.subject.identity != _identity(identity)
                        or evidence.actor != kwargs.get("actor", evidence.actor)
                        or evidence.selection != actual_selection
                    ):
                        raise WorkflowMarketplaceError(
                            "confirmation_token_invalid",
                            "confirmation binding is invalid",
                        )
                    boundary = _Boundary(service, identity, _execution())
                    handle = _boundary.set(boundary)
                    enter_atomic = kwargs.get("enter_atomic", lambda: True)

                    def enter():
                        accepted = enter_atomic()
                        boundary.entered = bool(accepted)
                        if evidence and accepted:
                            evidence.entered = True
                        return accepted

                    kwargs["enter_atomic"] = enter
                    try:
                        value = method(service, *args, **kwargs)
                    except Exception as error:
                        code = getattr(error, "code", None)
                        if (
                            code == "marketplace_operation_cancelled"
                            and not boundary.entered
                        ):
                            if evidence:
                                evidence.cancelled = True
                            raise
                        if code in _RECOVERY:
                            outcome = wire.RecoveryRequiredOutcome(
                                type="recovery_required", reason=_RECOVERY[code]
                            )
                        elif boundary.rollback:
                            try:
                                state = _state_locked(service, identity)
                            except Exception:
                                state = _unconfirmed(service, identity)
                            expected = boundary.previous
                            matches = (
                                state.state == "absent"
                                if expected is None
                                else state.state == "installed"
                                and state.installed.distribution_digest
                                == expected.distribution_digest
                                and service.installed_store.get(identity) == expected
                            )
                            if matches and state.recovery == "clear":
                                error = WorkflowMarketplaceError(
                                    "transaction_rollback_completed",
                                    "package rollback was verified",
                                )
                                outcome = wire.KnownUnchangedOutcome(
                                    type="known_unchanged",
                                    evidence="rollback_verified",
                                    package_state=state,
                                )
                            else:
                                outcome = wire.RecoveryRequiredOutcome(
                                    type="recovery_required", reason="state_unverified"
                                )
                        elif not boundary.entered:
                            outcome = wire.KnownUnchangedOutcome(
                                type="known_unchanged",
                                evidence="before_mutation",
                                package_state=None,
                            )
                        else:
                            outcome = wire.RecoveryRequiredOutcome(
                                type="recovery_required", reason="state_unverified"
                            )
                        if evidence:
                            evidence.completion = _failure(error, outcome)
                        raise error
                    if evidence:
                        evidence.committed = True
                        state = (
                            boundary.trust_state
                            if kind.startswith("trust_")
                            else _state_locked(service, identity)
                        )
                        if state is None or state.state == "unconfirmed":
                            evidence.completion = _failure(
                                None,
                                wire.RecoveryRequiredOutcome(
                                    type="recovery_required", reason="state_unverified"
                                ),
                            )
                        else:
                            if kind.startswith("trust_"):
                                result_value = {
                                    **state.trust.model_dump(mode="json"),
                                    "selection": actual_selection.model_dump(
                                        mode="json"
                                    ),
                                }
                                if kind == "trust_revoke":
                                    result_value["revoked"] = value
                            else:
                                result_value = value.model_dump(
                                    mode="json", by_alias=False
                                )
                            result = _RESULT.validate_python({
                                "type": wire.RESULT_TYPE_BY_KIND[kind],
                                "value": result_value,
                            })
                            evidence.completion = LifecycleCompletion(
                                state="succeeded",
                                result=result,
                                error=None,
                                outcome=wire.CommittedOutcome(
                                    type="committed", package_state=state
                                ),
                            )
                    return value
            except Exception as error:
                if evidence and evidence.completion is None:
                    # Only this pre-entry service path can positively certify no write.
                    if boundary is None or not boundary.entered:
                        outcome = (
                            wire.RecoveryRequiredOutcome(
                                type="recovery_required", reason=_RECOVERY[error.code]
                            )
                            if getattr(error, "code", None) in _RECOVERY
                            else wire.KnownUnchangedOutcome(
                                type="known_unchanged",
                                evidence="before_mutation",
                                package_state=None,
                            )
                        )
                        evidence.completion = _failure(error, outcome)
                    else:
                        evidence.completion = _unknown(error)
                raise
            finally:
                if handle is not None:
                    _boundary.reset(handle)

        return wrapped

    return decorate


def complete_mutation(
    service: WorkflowMarketplaceService,
    *,
    kind: Literal[
        "install_confirm",
        "update_confirm",
        "remove_confirm",
        "trust_confirm",
        "trust_revoke",
    ],
    subject: wire.PackageSubject,
    selection: wire.TrustSelection | None,
    actor: str,
    call: Callable[[], object],
) -> LifecycleCompletion:
    evidence = _Evidence(service, kind, subject, selection, actor, _execution())
    handle = _evidence.set(evidence)
    try:
        try:
            call()
        except Exception as error:
            if evidence.invalid_scope.is_set():
                return _unknown(error)
            if evidence.cancelled and not evidence.entered and not evidence.committed:
                raise MarketplaceOperationCancelled(
                    "marketplace operation was cancelled"
                ) from None
            return (
                evidence.completion
                if evidence.completion and evidence.completion.state == "failed"
                else _unknown(error)
            )
        return (
            _unknown()
            if evidence.reused or evidence.invalid_scope.is_set()
            else evidence.completion or _unknown()
        )
    finally:
        evidence.closed.set()
        _evidence.reset(handle)


def review_token_metadata(
    service: WorkflowMarketplaceService,
    review: InstallReview | UpdateReview | RemoveReview | TrustReview,
    *,
    actor: str,
    subject: wire.LifecycleSubject,
    selection: wire.TrustSelection | None,
) -> ReviewTokenMetadata:
    token = review.confirmation_token
    operation = "trust" if isinstance(review, TrustReview) else review.operation
    with service.transactions._locked():
        if operation == "trust":
            authority = service._trust_confirmations.inspect(
                token, actor=actor, profile=service.profile
            )
            actual_selection = service._trust_confirmations.selection(token)
            _, inventory, _, _ = service._trust_review_parts(review.identity)
            expected = (
                inventory
                if isinstance(selection, wire.AllTrustSelection)
                else [
                    w
                    for w in inventory
                    if isinstance(selection, wire.OneTrustSelection)
                    and w.workflow_name == selection.workflow_name
                ]
            )
            if (
                selection != actual_selection
                or not expected
                or sorted(w.definition_path for w in expected)
                != sorted(authority.workflow_paths)
                or [(w.workflow_name, w.definition_path) for w in review.workflows]
                != [(w.workflow_name, w.definition_path) for w in expected]
                or review.distribution_digest != authority.distribution_digest
                or not service._matches_issued_review(review)
            ):
                raise WorkflowMarketplaceError(
                    "confirmation_token_invalid", "confirmation binding is invalid"
                )
        else:
            authority = service.transactions.inspect_token(
                token, actor=actor, profile=service.profile
            )
            if (
                selection is not None
                or authority.operation != operation
                or not service._matches_issued_review(review)
            ):
                raise WorkflowMarketplaceError(
                    "confirmation_token_invalid", "confirmation binding is invalid"
                )
        if (
            authority.identity != review.identity
            or authority.review_digest != review.review_digest
            or (
                isinstance(subject, wire.PackageSubject)
                and subject.identity != _identity(review.identity)
            )
            or (
                isinstance(subject, wire.DirectInstallSubject)
                and (
                    operation != "install"
                    or subject.source_key != review.identity.source_key
                )
            )
            or not isinstance(subject, (wire.PackageSubject, wire.DirectInstallSubject))
        ):
            raise WorkflowMarketplaceError(
                "confirmation_token_invalid", "confirmation binding is invalid"
            )
        expires_at = authority.expires_at

    def validate_unused(candidate):
        if candidate != token:
            return False
        try:
            current = (
                service._trust_confirmations.inspect(
                    candidate, actor=actor, profile=service.profile
                )
                if operation == "trust"
                else service.transactions.inspect_token(
                    candidate, actor=actor, profile=service.profile
                )
            )
            return current == authority and (
                operation != "trust"
                or service._trust_confirmations.selection(candidate) == selection
            )
        except Exception:
            return False

    return ReviewTokenMetadata(
        confirmation_token=token,
        expires_at=expires_at,
        review_digest=authority.review_digest,
        subject=subject.model_copy(deep=True),
        selection=selection.model_copy(deep=True) if selection else None,
        validate_unused=validate_unused,
    )


def complete_read(
    service: WorkflowMarketplaceService,
    *,
    kind: Literal[
        "inspect",
        "update_check",
        "install_prepare",
        "update_prepare",
        "remove_prepare",
        "trust_prepare",
    ],
    subject: wire.LifecycleSubject,
    selection: wire.TrustSelection | None,
    actor: str,
    call: Callable[[], object],
) -> LifecycleCompletion:
    if kind not in {
        "inspect",
        "update_check",
        "install_prepare",
        "update_prepare",
        "remove_prepare",
        "trust_prepare",
    }:
        return _unknown()
    evidence = _Evidence(service, kind, subject, selection, actor, _execution())
    handle = _evidence.set(evidence)
    try:
        value = call()
        if evidence.invalid_scope.is_set():
            return _unknown()
        metadata = None
        if kind == "update_check":
            projection = {
                "checks": [v.model_dump(mode="json", by_alias=False) for v in value]
            }
        else:
            projection = value.model_dump(mode="json", by_alias=False)
        if kind.endswith("_prepare"):
            if projection.pop("confirmation_token", None) is not None:
                metadata = review_token_metadata(
                    service, value, actor=actor, subject=subject, selection=selection
                )
            projection.update(
                confirmation_available=metadata is not None,
                expires_at=metadata.expires_at if metadata else None,
            )
            if kind == "trust_prepare":
                with service.transactions._locked():
                    installed, inventory, _, _ = service._trust_review_parts(
                        value.identity
                    )
                    if installed.distribution_digest != value.distribution_digest:
                        raise WorkflowMarketplaceError(
                            "trust_review_changed", "review changed"
                        )
                    projection["package_workflows"] = [
                        {
                            "workflow_name": w.workflow_name,
                            "definition_path": w.definition_path,
                        }
                        for w in inventory
                    ]
        result = _RESULT.validate_python({
            "type": wire.RESULT_TYPE_BY_KIND[kind],
            "value": projection,
        })
        if isinstance(subject, wire.PackageSubject):
            values = result.value.checks if kind == "update_check" else [result.value]
            if (kind == "update_check" and len(values) != 1) or any(
                _identity(v.identity) != subject.identity for v in values
            ):
                return _unknown()
        return LifecycleCompletion(
            state="succeeded",
            result=result,
            error=None,
            outcome=wire.KnownUnchangedOutcome(
                type="known_unchanged", evidence="read_only", package_state=None
            ),
            review_token=metadata,
        )
    except Exception as error:
        if evidence.invalid_scope.is_set():
            return _unknown(error)
        if isinstance(error, MarketplaceOperationCancelled) or getattr(
            error, "code", None
        ) in {"source_cancelled", "marketplace_operation_cancelled"}:
            raise MarketplaceOperationCancelled(
                "marketplace operation was cancelled"
            ) from None
        outcome = (
            wire.RecoveryRequiredOutcome(
                type="recovery_required", reason=_RECOVERY[error.code]
            )
            if getattr(error, "code", None) in _RECOVERY
            else wire.KnownUnchangedOutcome(
                type="known_unchanged", evidence="read_only", package_state=None
            )
        )
        return _failure(error, outcome)
    finally:
        evidence.closed.set()
        _evidence.reset(handle)
