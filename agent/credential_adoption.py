"""Private immutable handoff types for sealed credential adoption."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal

import httpx

from hermes_cli.runtime_provider import CredentialFreeExecutionRouteConstraint


@dataclass(frozen=True, slots=True, repr=False)
class _PendingSealedCredentialAdoption:
    generation: int
    source: Literal["vertex", "pool", "anthropic_direct"]
    route_constraint: CredentialFreeExecutionRouteConstraint = field(repr=False)
    api_key: str = field(repr=False)
    base_url: str = field(repr=False)
    client_kwargs: Mapping[str, Any] = field(repr=False, compare=False)
    pool_entry_id: str | None = None
    is_anthropic_oauth: bool | None = None
    adoption_attempts: int = 0


class _CredentialRefreshStatus(StrEnum):
    ADOPTED = "adopted"
    ACQUISITION_FAILED = "acquisition_failed"
    ADOPTION_FAILED = "adoption_failed"
    INVALIDATED = "invalidated"
    NOT_APPLICABLE = "not_applicable"


class _CandidateAttemptStatus(StrEnum):
    ADOPTED = "adopted"
    RETRYABLE_BUILD_FAILURE = "retryable_build_failure"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True, repr=False)
class _CandidateAttemptResult:
    status: _CandidateAttemptStatus
    prior_client: Any = field(default=None, repr=False, compare=False)
    retirement_kind: Literal["openai", "anthropic"] | None = None


@dataclass(frozen=True, slots=True)
class _FrozenSequence:
    kind: Literal["list", "tuple", "set", "frozenset"]
    values: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class _FrozenTimeout:
    connect: float | None
    read: float | None
    write: float | None
    pool: float | None


@dataclass(frozen=True, slots=True)
class _FrozenLimits:
    max_connections: int | None
    max_keepalive_connections: int | None
    keepalive_expiry: float | None


def _freeze_candidate_value(value: Any, active_ids: set[int]) -> Any:
    if value is None or isinstance(value, (bool, int, float, complex, str, bytes)):
        return value
    if callable(value):
        raise TypeError("unsupported candidate client value")
    if isinstance(value, httpx.Timeout):
        return _FrozenTimeout(
            connect=value.connect,
            read=value.read,
            write=value.write,
            pool=value.pool,
        )
    if isinstance(value, httpx.Limits):
        return _FrozenLimits(
            max_connections=value.max_connections,
            max_keepalive_connections=value.max_keepalive_connections,
            keepalive_expiry=value.keepalive_expiry,
        )

    identity = id(value)
    if identity in active_ids:
        raise ValueError("cyclic candidate client value")
    if isinstance(value, Mapping):
        active_ids.add(identity)
        try:
            frozen: dict[str, Any] = {}
            for key, nested in value.items():
                if not isinstance(key, str):
                    raise TypeError("candidate client mapping keys must be strings")
                frozen[key] = _freeze_candidate_value(nested, active_ids)
            return MappingProxyType(frozen)
        finally:
            active_ids.remove(identity)
    if isinstance(value, (list, tuple, set, frozenset)):
        active_ids.add(identity)
        try:
            kind = type(value).__name__
            return _FrozenSequence(
                kind=kind,
                values=tuple(
                    _freeze_candidate_value(nested, active_ids) for nested in value
                ),
            )
        finally:
            active_ids.remove(identity)
    raise TypeError("unsupported candidate client value")


def _snapshot_candidate_client_kwargs(
    client_kwargs: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Return a transport-free, deeply immutable client-kwargs snapshot."""
    if not isinstance(client_kwargs, Mapping):
        raise TypeError("candidate client kwargs must be a mapping")
    snapshot: dict[str, Any] = {}
    active_ids = {id(client_kwargs)}
    for key, value in client_kwargs.items():
        if not isinstance(key, str):
            raise TypeError("candidate client mapping keys must be strings")
        if key == "http_client":
            continue
        snapshot[key] = _freeze_candidate_value(value, active_ids)
    return MappingProxyType(snapshot)


def _materialize_candidate_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _materialize_candidate_value(nested)
            for key, nested in value.items()
        }
    if isinstance(value, _FrozenSequence):
        values = tuple(_materialize_candidate_value(item) for item in value.values)
        if value.kind == "list":
            return list(values)
        if value.kind == "tuple":
            return values
        if value.kind == "set":
            return set(values)
        return frozenset(values)
    if isinstance(value, _FrozenTimeout):
        return httpx.Timeout(
            connect=value.connect,
            read=value.read,
            write=value.write,
            pool=value.pool,
        )
    if isinstance(value, _FrozenLimits):
        return httpx.Limits(
            max_connections=value.max_connections,
            max_keepalive_connections=value.max_keepalive_connections,
            keepalive_expiry=value.keepalive_expiry,
        )
    return value


def _materialize_candidate_client_kwargs(
    client_kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    """Materialize a fresh mutable kwargs tree for one SDK build attempt."""
    return {
        key: _materialize_candidate_value(value)
        for key, value in client_kwargs.items()
    }


__all__ = [
    "_CandidateAttemptResult",
    "_CandidateAttemptStatus",
    "_CredentialRefreshStatus",
    "_PendingSealedCredentialAdoption",
    "_materialize_candidate_client_kwargs",
    "_snapshot_candidate_client_kwargs",
]
