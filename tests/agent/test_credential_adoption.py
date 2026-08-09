"""Immutable snapshots for sealed credential adoption."""

from __future__ import annotations

from collections.abc import Mapping

import httpx
import pytest
from openai import OpenAI

from agent.credential_adoption import (
    _PendingSealedCredentialAdoption,
    _materialize_candidate_client_kwargs,
    _snapshot_candidate_client_kwargs,
)
from hermes_cli import runtime_provider as rp


def _sealed_route_constraint():
    endpoint = "https://route.test/v1"
    identity = rp.execution_runtime_identity(
        rp.classify_execution_runtime(
            provider="openai-api",
            model_config={"provider": "openai-api", "default": "test-model"},
            provider_config={
                "api_mode": "chat_completions",
                "base_url": endpoint,
            },
        )
    )
    return rp.CredentialFreeExecutionRouteConstraint(
        route_fingerprint="f" * 64,
        requested_provider="openai-api",
        model="test-model",
        api_mode="chat_completions",
        base_url=endpoint,
        provider_config={},
        identity=identity,
    )


def test_pending_candidate_snapshot_is_private_and_transport_free():
    original = {
        "api_key": "TOKEN_CANARY",
        "default_headers": {"Authorization": "HEADER_CANARY"},
        "default_query": {"api-version": ["one", "two"]},
        "http_client": object(),
    }
    candidate = _PendingSealedCredentialAdoption(
        generation=1,
        source="pool",
        route_constraint=_sealed_route_constraint(),
        api_key="TOKEN_CANARY",
        base_url="https://route.test/v1",
        client_kwargs=_snapshot_candidate_client_kwargs(original),
    )

    original["default_headers"]["Authorization"] = "mutated"
    original["default_query"]["api-version"].append("three")
    assert "TOKEN_CANARY" not in repr(candidate)
    assert "HEADER_CANARY" not in repr(candidate)
    assert "http_client" not in candidate.client_kwargs
    assert isinstance(candidate.client_kwargs, Mapping)
    with pytest.raises(TypeError):
        candidate.client_kwargs["api_key"] = "mutated"

    materialized = _materialize_candidate_client_kwargs(candidate.client_kwargs)
    assert materialized["default_headers"]["Authorization"] == "HEADER_CANARY"
    assert materialized["default_query"]["api-version"] == ["one", "two"]


def _cycle():
    value = []
    value.append(value)
    return value


class _MutableValue:
    pass


@pytest.mark.parametrize(
    "value_factory",
    [
        lambda: (lambda: None),
        _cycle,
        _MutableValue,
        lambda: OpenAI(api_key="sdk-client"),
        httpx.Client,
    ],
    ids=["callable", "cycle", "mutable-object", "sdk-client", "transport"],
)
def test_snapshot_rejects_unsupported_secret_bearing_values(value_factory):
    value = value_factory()
    key = "transport" if isinstance(value, httpx.Client) else "unsupported"
    try:
        with pytest.raises((TypeError, ValueError)):
            _snapshot_candidate_client_kwargs({key: value})
    finally:
        close = getattr(value, "close", None)
        if callable(close):
            close()


def test_snapshot_rebuilds_supported_nested_timeout_and_limits_values():
    original = {
        "default_headers": {"X-Test": ["one", {"two": (3, 4)}]},
        "timeout": httpx.Timeout(connect=1.0, read=2.0, write=3.0, pool=4.0),
        "limits": httpx.Limits(
            max_connections=11,
            max_keepalive_connections=7,
            keepalive_expiry=13.0,
        ),
    }
    snapshot = _snapshot_candidate_client_kwargs(original)
    first = _materialize_candidate_client_kwargs(snapshot)
    second = _materialize_candidate_client_kwargs(snapshot)

    assert first is not second
    assert first["default_headers"] is not second["default_headers"]
    assert first["default_headers"]["X-Test"] is not second["default_headers"]["X-Test"]
    assert first["timeout"] is not second["timeout"]
    assert first["limits"] is not second["limits"]
    assert first["timeout"].connect == 1.0
    assert first["timeout"].read == 2.0
    assert first["limits"].max_connections == 11
    assert first["limits"].max_keepalive_connections == 7
