"""Strict schema tests for durable per-key secret authority metadata."""

import json

import pytest


def _authority_api():
    from hermes_cli.secret_authority import (
        AuthorityRegistry,
        AuthorityRegistryError,
        SecretAuthority,
        encode_authority_registry,
        load_authority_registry,
    )

    return (
        AuthorityRegistry,
        AuthorityRegistryError,
        SecretAuthority,
        encode_authority_registry,
        load_authority_registry,
    )


def test_authority_registry_round_trip_is_canonical_and_read_only(tmp_path):
    (
        AuthorityRegistry,
        _AuthorityRegistryError,
        SecretAuthority,
        encode_authority_registry,
        load_authority_registry,
    ) = _authority_api()
    root = tmp_path / "secrets"

    assert load_authority_registry(root) is None
    assert not root.exists()

    root.mkdir()
    registry = AuthorityRegistry(
        version=1,
        entries={"OLD": SecretAuthority.CLEARED, "K": SecretAuthority.OS},
    )
    encoded = encode_authority_registry(registry)
    (root / "authority.json").write_bytes(encoded)

    assert encoded == b'{"version":1,"authorities":{"K":"os","OLD":"cleared"}}\n'
    assert load_authority_registry(root) == registry


@pytest.mark.parametrize(
    "payload",
    [
        '{"version":1,"entries":{"K":"os"}}',
        '{"version":2,"authorities":{"K":"os"}}',
        '{"version":1,"authorities":{"K":"bogus"}}',
        '{"version":1,"authorities":{"":"os"}}',
        '{"version":1,"authorities":{"K":"os"},"extra":true}',
        '{"version":1,"version":1,"authorities":{}}',
        '{"version":1,"authorities":{"K":"os","K":"file"}}',
        '{"version":true,"authorities":{}}',
        "[]",
    ],
)
def test_corrupt_authority_registry_fails_closed(tmp_path, payload):
    (
        _AuthorityRegistry,
        AuthorityRegistryError,
        _SecretAuthority,
        _encode_authority_registry,
        load_authority_registry,
    ) = _authority_api()
    root = tmp_path / "secrets"
    root.mkdir()
    (root / "authority.json").write_text(payload, encoding="utf-8")

    with pytest.raises(AuthorityRegistryError):
        load_authority_registry(root)


def test_encoder_rejects_invalid_in_memory_registry():
    (
        AuthorityRegistry,
        AuthorityRegistryError,
        SecretAuthority,
        encode_authority_registry,
        _load_authority_registry,
    ) = _authority_api()

    with pytest.raises(AuthorityRegistryError):
        encode_authority_registry(
            AuthorityRegistry(version=2, entries={"K": SecretAuthority.FILE})
        )
    with pytest.raises(AuthorityRegistryError):
        encode_authority_registry(
            AuthorityRegistry(version=1, entries={"": SecretAuthority.OS})
        )
    with pytest.raises(AuthorityRegistryError):
        encode_authority_registry(
            AuthorityRegistry(version=1, entries={"K": "os"})  # type: ignore[arg-type]
        )


def test_authority_document_contains_states_but_never_secret_values(tmp_path):
    (
        AuthorityRegistry,
        _AuthorityRegistryError,
        SecretAuthority,
        encode_authority_registry,
        _load_authority_registry,
    ) = _authority_api()
    secret = "distinctive-secret-value"

    encoded = encode_authority_registry(
        AuthorityRegistry(version=1, entries={"K": SecretAuthority.FILE})
    )
    decoded = json.loads(encoded)

    assert decoded == {"version": 1, "authorities": {"K": "file"}}
    assert secret.encode() not in encoded
