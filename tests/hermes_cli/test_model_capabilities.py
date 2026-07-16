"""Tests for verified model capability fetching, validation, joining, and caching."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError

import pytest

from providers.base import ProviderProfile
from hermes_cli.model_capabilities import (
    CAPABILITY_KEYS,
    ModelCapabilityCatalog,
    VerifiedModelCapability,
    clear_model_capabilities_cache,
    fetch_model_capability_catalog,
    join_live_model_capabilities,
)


def _entry(
    model_id: str = "model-a",
    *,
    name: str = "Model A",
    available: bool = True,
    selection_mode: str = "explicit",
    capabilities: dict[str, str] | None = None,
    evidence: dict | None = None,
    **extra,
) -> dict:
    return {
        "id": model_id,
        "name": name,
        "available": available,
        "selection_mode": selection_mode,
        "capabilities": capabilities
        if capabilities is not None
        else {
            "completion": "supported",
            "tools": "unsupported",
            "vision": "unknown",
            "reasoning": "supported",
        },
        "evidence": evidence if evidence is not None else {},
        **extra,
    }


def _payload(*entries: dict, **extra) -> dict:
    return {
        "object": "list",
        "registry_revision": "revision-1",
        "generated_at": "2026-07-16T12:00:00Z",
        "data": list(entries),
        **extra,
    }


class _Response:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.status = status
        self._body = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode("utf-8")
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


@pytest.fixture
def capability_profile(monkeypatch):
    profile = ProviderProfile(
        name="gateway",
        base_url="http://127.0.0.1:18080/v1",
        model_capabilities_path="model-capabilities",
        default_headers={
            "User-Agent": "gateway-test",
            "X-Provider-Secret": "header-secret",
        },
    )

    def lookup(provider: str):
        return profile if provider == "gateway" else None

    monkeypatch.setattr(
        "hermes_cli.model_capabilities.get_provider_profile", lookup
    )
    return profile


def _install_fake_opener(monkeypatch, results: list[object]):
    calls = []
    pending = list(results)

    def fake_open(request, *, timeout):
        calls.append((request, timeout))
        result = pending.pop(0)
        if isinstance(result, BaseException):
            raise result
        return _Response(result)

    monkeypatch.setattr(
        "hermes_cli.model_capabilities.open_credentialed_url", fake_open
    )
    return calls


def _fetch(**kwargs) -> ModelCapabilityCatalog:
    return fetch_model_capability_catalog(
        "gateway",
        api_key="test-key",
        base_url="http://127.0.0.1:18080/v1",
        **kwargs,
    )


def test_valid_payload_parses_all_states_and_normalizes_missing_keys(
    monkeypatch, capability_profile
):
    calls = _install_fake_opener(
        monkeypatch,
        [
            _payload(
                _entry(
                    capabilities={
                        "completion": "supported",
                        "tools": "unsupported",
                        "vision": "unknown",
                    },
                    evidence={
                        "vision": {
                            "source": "vendor_documentation",
                            "reference": "public-reference",
                        }
                    },
                    future_entry_field="ignored",
                ),
                future_top_level_field="ignored",
            )
        ],
    )

    catalog = _fetch()

    assert catalog.status == "ready"
    assert catalog.registry_revision == "revision-1"
    assert catalog.generated_at == "2026-07-16T12:00:00Z"
    assert catalog.models["model-a"].capabilities == {
        "completion": "supported",
        "tools": "unsupported",
        "vision": "unknown",
        "reasoning": "unknown",
    }
    assert catalog.models["model-a"].evidence["vision"]["reference"] == (
        "public-reference"
    )
    request, timeout = calls[0]
    assert request.full_url == (
        "http://127.0.0.1:18080/v1/model-capabilities"
    )
    assert timeout == 8.0
    assert request.get_header("Authorization") == "Bearer test-key"
    assert request.get_header("X-provider-secret") == "header-secret"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["data"][0].pop("id"),
        lambda payload: payload["data"][0].update(id=""),
        lambda payload: payload["data"].append(dict(payload["data"][0])),
        lambda payload: payload["data"][0]["capabilities"].update(audio="supported"),
        lambda payload: payload["data"][0]["capabilities"].update(tools="maybe"),
        lambda payload: payload["data"][0].update(selection_mode="manual"),
        lambda payload: payload["data"][0].update(available=False),
        lambda payload: payload.update(object="catalog"),
        lambda payload: payload.update(registry_revision=3),
        lambda payload: payload.update(generated_at=None),
        lambda payload: payload.update(data={}),
    ],
    ids=[
        "missing-id",
        "empty-id",
        "duplicate-id",
        "invalid-capability-key",
        "invalid-capability-state",
        "invalid-selection-mode",
        "unavailable-explicit-entry",
        "invalid-object",
        "invalid-registry-revision",
        "invalid-generated-at",
        "invalid-data",
    ],
)
def test_invalid_schema_rejects_the_whole_response(
    monkeypatch, capability_profile, mutate
):
    payload = _payload(_entry())
    mutate(payload)
    _install_fake_opener(monkeypatch, [payload])

    catalog = _fetch()

    assert catalog.status == "capability-response-invalid"
    assert catalog.models == {}


def test_malformed_json_rejects_the_whole_response(
    monkeypatch, capability_profile
):
    _install_fake_opener(monkeypatch, [b'{"data":'])

    catalog = _fetch()

    assert catalog.status == "capability-response-invalid"
    assert catalog.models == {}


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (
            HTTPError(
                "http://gateway.test",
                401,
                "body-must-not-leak",
                {},
                None,
            ),
            "authentication-required",
        ),
        (
            HTTPError(
                "http://gateway.test",
                403,
                "body-must-not-leak",
                {},
                None,
            ),
            "authentication-required",
        ),
        (
            HTTPError(
                "http://gateway.test",
                404,
                "body-must-not-leak",
                {},
                None,
            ),
            "gateway-upgrade-required",
        ),
        (
            HTTPError(
                "http://gateway.test",
                503,
                "body-must-not-leak",
                {},
                None,
            ),
            "gateway-unreachable",
        ),
        (TimeoutError("timed out with test-key"), "gateway-unreachable"),
        (URLError("connection failed with test-key"), "gateway-unreachable"),
        (ConnectionError("connection failed with test-key"), "gateway-unreachable"),
    ],
)
def test_fetch_maps_failures_without_leaking_sensitive_diagnostics(
    monkeypatch, capability_profile, error, expected_status
):
    _install_fake_opener(monkeypatch, [error])

    catalog = _fetch()

    assert catalog.status == expected_status
    assert catalog.models == {}
    assert "test-key" not in catalog.detail
    assert "body-must-not-leak" not in catalog.detail


def test_valid_auto_only_payload_is_catalog_empty(
    monkeypatch, capability_profile
):
    auto = _entry(
        "auto",
        name="Automatic",
        selection_mode="automatic",
        capabilities={},
    )
    _install_fake_opener(monkeypatch, [_payload(auto)])

    catalog = _fetch()

    assert catalog.status == "catalog-empty"
    assert catalog.models["auto"].selection_mode == "automatic"
    assert catalog.models["auto"].capabilities == {
        key: "unknown" for key in CAPABILITY_KEYS
    }


def test_fetch_omits_bearer_header_for_empty_key(
    monkeypatch, capability_profile
):
    calls = _install_fake_opener(monkeypatch, [_payload(_entry())])

    fetch_model_capability_catalog(
        "gateway",
        api_key="",
        base_url="http://127.0.0.1:18080/v1",
    )

    request, _timeout = calls[0]
    assert request.get_header("Authorization") is None


def test_join_uses_exact_ids_and_omits_stale_capability_models():
    catalog = ModelCapabilityCatalog(
        status="ready",
        models={
            "auto": VerifiedModelCapability(
                id="auto",
                name="Automatic",
                selection_mode="automatic",
                capabilities={key: "unknown" for key in CAPABILITY_KEYS},
                evidence={},
            ),
            "model-a": VerifiedModelCapability(
                id="model-a",
                name="Model A",
                selection_mode="explicit",
                capabilities={
                    "completion": "supported",
                    "tools": "supported",
                    "vision": "unknown",
                    "reasoning": "unsupported",
                },
                evidence={},
            ),
            "stale-model": VerifiedModelCapability(
                id="stale-model",
                name="Stale",
                selection_mode="explicit",
                capabilities={key: "supported" for key in CAPABILITY_KEYS},
                evidence={},
            ),
        },
    )

    live, metadata, mismatch_count = join_live_model_capabilities(
        ["MODEL-A", "model-a", "live-without-metadata", "auto", "auto"],
        catalog,
    )

    assert live == ["auto", "MODEL-A", "model-a", "live-without-metadata"]
    assert list(metadata) == live
    assert metadata["model-a"].capabilities["tools"] == "supported"
    assert metadata["MODEL-A"].capabilities == {
        key: "unknown" for key in CAPABILITY_KEYS
    }
    assert metadata["live-without-metadata"].capabilities == {
        key: "unknown" for key in CAPABILITY_KEYS
    }
    assert "stale-model" not in metadata
    assert mismatch_count == 3


def test_cache_isolated_by_provider_base_url_and_credential(
    monkeypatch, capability_profile
):
    second_profile = ProviderProfile(
        name="other-gateway",
        model_capabilities_path="model-capabilities",
    )

    def lookup(provider: str):
        return {
            "gateway": capability_profile,
            "other-gateway": second_profile,
        }.get(provider)

    monkeypatch.setattr(
        "hermes_cli.model_capabilities.get_provider_profile", lookup
    )
    calls = _install_fake_opener(
        monkeypatch,
        [
            _payload(_entry("first")),
            _payload(_entry("other-provider")),
            _payload(_entry("other-base")),
            _payload(_entry("other-key")),
        ],
    )

    first = fetch_model_capability_catalog(
        "gateway",
        api_key="key-one",
        base_url="https://Gateway.Example/v1/",
    )
    normalized_url_hit = fetch_model_capability_catalog(
        "gateway",
        api_key="key-one",
        base_url="https://gateway.example/v1",
    )
    other_provider = fetch_model_capability_catalog(
        "other-gateway",
        api_key="key-one",
        base_url="https://gateway.example/v1",
    )
    other_base = fetch_model_capability_catalog(
        "gateway",
        api_key="key-one",
        base_url="https://gateway.example/alternate/v1",
    )
    other_key = fetch_model_capability_catalog(
        "gateway",
        api_key="key-two",
        base_url="https://gateway.example/v1",
    )

    assert list(first.models) == ["first"]
    assert list(normalized_url_hit.models) == ["first"]
    assert list(other_provider.models) == ["other-provider"]
    assert list(other_base.models) == ["other-base"]
    assert list(other_key.models) == ["other-key"]
    assert len(calls) == 4


def test_valid_success_caches_for_one_hour_and_force_refresh_bypasses(
    monkeypatch, capability_profile
):
    now = [1000.0]
    monkeypatch.setattr(
        "hermes_cli.model_capabilities.time.time", lambda: now[0]
    )
    calls = _install_fake_opener(
        monkeypatch,
        [
            _payload(_entry("first")),
            _payload(_entry("forced")),
            _payload(_entry("expired")),
        ],
    )

    first = _fetch()
    now[0] = 4599.0
    cached = _fetch()
    forced = _fetch(force_refresh=True)
    now[0] = 8200.0
    expired = _fetch()

    assert list(first.models) == ["first"]
    assert list(cached.models) == ["first"]
    assert list(forced.models) == ["forced"]
    assert list(expired.models) == ["expired"]
    assert len(calls) == 3


def test_auto_only_success_is_cached(monkeypatch, capability_profile):
    auto_payload = _payload(
        _entry(
            "auto",
            name="Automatic",
            selection_mode="automatic",
            capabilities={},
        )
    )
    calls = _install_fake_opener(monkeypatch, [auto_payload])

    first = _fetch()
    second = _fetch()

    assert first.status == second.status == "catalog-empty"
    assert len(calls) == 1


def test_failures_are_not_persisted(monkeypatch, capability_profile):
    calls = _install_fake_opener(
        monkeypatch,
        [
            HTTPError("http://gateway.test", 503, "unavailable", {}, None),
            _payload(_entry("recovered")),
        ],
    )

    failed = _fetch()
    recovered = _fetch()

    assert failed.status == "gateway-unreachable"
    assert list(recovered.models) == ["recovered"]
    assert len(calls) == 2


def test_cache_contains_no_literal_credentials_or_headers(
    monkeypatch, capability_profile
):
    _install_fake_opener(monkeypatch, [_payload(_entry())])

    _fetch()

    from hermes_constants import get_hermes_home

    cache_text = (
        get_hermes_home() / "model_capabilities_cache.json"
    ).read_text(encoding="utf-8")
    assert "test-key" not in cache_text
    assert "Bearer test-key" not in cache_text
    assert "Authorization" not in cache_text
    assert "header-secret" not in cache_text
    assert "X-Provider-Secret" not in cache_text


def test_clear_cache_removes_one_provider_or_all(
    monkeypatch, capability_profile
):
    second_profile = ProviderProfile(
        name="other-gateway",
        model_capabilities_path="model-capabilities",
    )
    monkeypatch.setattr(
        "hermes_cli.model_capabilities.get_provider_profile",
        lambda provider: {
            "gateway": capability_profile,
            "other-gateway": second_profile,
        }.get(provider),
    )
    calls = _install_fake_opener(
        monkeypatch,
        [
            _payload(_entry("gateway-first")),
            _payload(_entry("other-first")),
            _payload(_entry("gateway-second")),
            _payload(_entry("gateway-third")),
            _payload(_entry("other-second")),
        ],
    )

    _fetch()
    fetch_model_capability_catalog(
        "other-gateway",
        api_key="test-key",
        base_url="http://127.0.0.1:18080/v1",
    )
    clear_model_capabilities_cache("gateway")
    gateway_second = _fetch()
    other_cached = fetch_model_capability_catalog(
        "other-gateway",
        api_key="test-key",
        base_url="http://127.0.0.1:18080/v1",
    )
    clear_model_capabilities_cache()
    gateway_third = _fetch()
    other_second = fetch_model_capability_catalog(
        "other-gateway",
        api_key="test-key",
        base_url="http://127.0.0.1:18080/v1",
    )

    assert list(gateway_second.models) == ["gateway-second"]
    assert list(other_cached.models) == ["other-first"]
    assert list(gateway_third.models) == ["gateway-third"]
    assert list(other_second.models) == ["other-second"]
    assert len(calls) == 5
