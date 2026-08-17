"""Owner-bound plugin application command port contracts."""

from __future__ import annotations

import math

import pytest

from hermes_cli.plugin_application_commands import (
    PluginApplicationCommandExecutionError,
    PluginApplicationCommandInvalid,
    PluginApplicationCommandInvocation,
    PluginApplicationCommandMode,
    PluginApplicationCommandRegistrationError,
    _build_registration,
    _mint_invocation_for_test,
    _validate_result_mapping,
)


class TestCommandModel:
    def test_modes_are_exactly_read_dry_run_and_confirm(self):
        assert tuple(mode.value for mode in PluginApplicationCommandMode) == (
            "read",
            "dry_run",
            "confirm",
        )

    def test_direct_construction_is_host_guarded(self):
        with pytest.raises(TypeError, match="minted by Hermes"):
            PluginApplicationCommandInvocation(
                provider_id="records-provider",
                caller_id="records-cli",
                operation="records_get",
                arguments={"record_id": "123"},
                mode=PluginApplicationCommandMode.READ,
                invocation_id="invocation-1",
                profile_fingerprint="profile-fingerprint",
            )

    def test_minted_invocation_attributes_and_arguments_are_immutable(self):
        invocation = _mint_invocation_for_test(
            provider_id="records-provider",
            caller_id="records-cli",
            operation="records_get",
            arguments={"record_id": "123"},
            mode="read",
            invocation_id="invocation-1",
            profile_fingerprint="profile-fingerprint",
        )

        with pytest.raises(AttributeError, match="immutable"):
            invocation.operation = "records_update"
        with pytest.raises(TypeError):
            invocation.arguments["record_id"] = "456"

    def test_repr_contains_authority_metadata_but_not_argument_values(self):
        invocation = _mint_invocation_for_test(
            provider_id="records-provider",
            caller_id="records-cli",
            operation="records_get",
            arguments={"value": "distinctive-secret-marker"},
            mode="read",
            invocation_id="invocation-1",
            profile_fingerprint="profile-fingerprint",
        )

        representation = repr(invocation)

        assert "records-provider" in representation
        assert "records-cli" in representation
        assert "records_get" in representation
        assert "read" in representation
        assert "invocation-1" in representation
        assert invocation.arguments_sha256 in representation
        assert "profile-fingerprint" in representation
        assert "distinctive-secret-marker" not in representation

    def test_canonical_dictionary_order_produces_the_same_digest(self):
        first = _mint_invocation_for_test(
            provider_id="records-provider",
            caller_id="records-cli",
            operation="records_get",
            arguments={"alpha": 1, "beta": {"gamma": 2}},
            mode="read",
            invocation_id="invocation-1",
            profile_fingerprint="profile-fingerprint",
        )
        second = _mint_invocation_for_test(
            provider_id="records-provider",
            caller_id="records-cli",
            operation="records_get",
            arguments={"beta": {"gamma": 2}, "alpha": 1},
            mode="read",
            invocation_id="invocation-2",
            profile_fingerprint="profile-fingerprint",
        )

        assert first.arguments_sha256 == second.arguments_sha256

    def test_canonicalization_detaches_nested_caller_mutation(self):
        arguments = {"record": {"status": "open"}}
        invocation = _mint_invocation_for_test(
            provider_id="records-provider",
            caller_id="records-cli",
            operation="records_get",
            arguments=arguments,
            mode="read",
            invocation_id="invocation-1",
            profile_fingerprint="profile-fingerprint",
        )

        arguments["record"]["status"] = "closed"

        assert invocation.arguments["record"]["status"] == "open"

    @pytest.mark.parametrize(
        ("arguments", "mode", "invocation_id"),
        [
            ({"value": object()}, "read", "invocation-1"),
            ({1: "value"}, "read", "invocation-1"),
            ({"value": math.nan}, "read", "invocation-1"),
            ({"value": math.inf}, "read", "invocation-1"),
            ({"value": -math.inf}, "read", "invocation-1"),
            ({"value": "ok"}, True, "invocation-1"),
            ({"value": "ok"}, "read", ""),
            ({"value": "ok"}, "read", "x" * 129),
        ],
    )
    def test_invalid_arguments_modes_and_invocation_ids_are_rejected(
        self, arguments, mode, invocation_id
    ):
        with pytest.raises(PluginApplicationCommandInvalid):
            _mint_invocation_for_test(
                provider_id="records-provider",
                caller_id="records-cli",
                operation="records_get",
                arguments=arguments,
                mode=mode,
                invocation_id=invocation_id,
                profile_fingerprint="profile-fingerprint",
            )

    def test_result_requires_a_mapping_and_enforces_exact_utf8_bound(self):
        maximum = 1_048_576
        at_limit = {"value": "x" * (maximum - len(b'{"value":""}'))}
        over_limit = {"value": "x" * (maximum - len(b'{"value":""}') + 1)}

        validated = _validate_result_mapping(at_limit)

        assert validated == at_limit
        with pytest.raises(PluginApplicationCommandExecutionError):
            _validate_result_mapping(over_limit)
        with pytest.raises(PluginApplicationCommandExecutionError):
            _validate_result_mapping(["not", "a", "mapping"])

    def test_registration_metadata_is_validated_and_immutable(self):
        registration = _build_registration(
            provider_id="records-provider",
            operations={"records_get": "read", "records_update": "write"},
            allowed_callers={"records-cli"},
            handler=lambda invocation: {},
        )

        assert registration.provider_id == "records-provider"
        assert dict(registration.operations) == {
            "records_get": "read",
            "records_update": "write",
        }
        assert registration.allowed_callers == frozenset({"records-cli"})
        with pytest.raises(TypeError):
            registration.operations["records_delete"] = "write"
        with pytest.raises((AttributeError, TypeError)):
            registration.provider_id = "other-provider"

    @pytest.mark.parametrize(
        ("provider_id", "operations", "allowed_callers", "handler"),
        [
            ("Records", {"records_get": "read"}, {"records-cli"}, lambda _: {}),
            ("records", {"Records_Get": "read"}, {"records-cli"}, lambda _: {}),
            ("records", {"records_get": []}, {"records-cli"}, lambda _: {}),
            ("records", {"records_get": "delete"}, {"records-cli"}, lambda _: {}),
            ("records", {"records_get": "read"}, set(), lambda _: {}),
            ("records", {"records_get": "read"}, {"Records CLI"}, lambda _: {}),
            ("records", {"records_get": "read"}, {"records-cli"}, None),
        ],
    )
    def test_invalid_registration_metadata_uses_stable_registration_error(
        self, provider_id, operations, allowed_callers, handler
    ):
        with pytest.raises(PluginApplicationCommandRegistrationError):
            _build_registration(
                provider_id=provider_id,
                operations=operations,
                allowed_callers=allowed_callers,
                handler=handler,
            )
