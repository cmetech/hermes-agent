"""Owner-bound plugin application command port contracts."""

from __future__ import annotations

import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hermes_cli.plugin_application_commands import (
    PluginApplicationCommandDenied,
    PluginApplicationCommandExecutionError,
    PluginApplicationCommandInvalid,
    PluginApplicationCommandInvocation,
    PluginApplicationCommandMode,
    PluginApplicationCommandRegistrationError,
    PluginApplicationCommandUnavailable,
    _build_registration,
    _mint_invocation,
    _mint_invocation_for_test,
    _validate_result_mapping,
)
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest


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


def _port_contexts():
    manager = PluginManager()
    provider = PluginContext(
        PluginManifest(name="Records Provider", key="records-provider"), manager
    )
    caller = PluginContext(
        PluginManifest(name="Records CLI", key="records-cli"), manager
    )
    outsider = PluginContext(
        PluginManifest(name="Other Caller", key="other-caller"), manager
    )
    return manager, provider, caller, outsider


class _FingerprintSnapshot:
    def __init__(self, fingerprint="profile-fingerprint"):
        self.fingerprint = fingerprint
        self.calls = []

    def scoped_fingerprint(self, required_services, required_tools):
        self.calls.append((required_services, required_tools))
        return self.fingerprint


def _patch_snapshot(monkeypatch, snapshot=None):
    snapshot = snapshot or _FingerprintSnapshot()
    monkeypatch.setattr(
        "hermes_cli.plugin_configuration.connector_capability_snapshot",
        lambda: snapshot,
    )
    return snapshot


class TestPluginContextApplicationCommands:
    def test_provider_registration_uses_canonical_owner_and_is_immutable(self):
        manager, provider, _caller, _outsider = _port_contexts()
        handler = lambda invocation: {"ok": True}

        provider.register_application_commands(
            operations={"records_get": "read", "records_update": "write"},
            allowed_callers={"records-cli"},
            handler=handler,
        )

        registration = manager._application_command_providers["records-provider"]
        assert registration.provider_id == "records-provider"
        assert registration.handler is handler
        assert dict(registration.operations) == {
            "records_get": "read",
            "records_update": "write",
        }
        assert registration.allowed_callers == frozenset({"records-cli"})
        with pytest.raises(TypeError):
            registration.operations["records_delete"] = "write"

    def test_provider_cannot_register_for_a_different_identity(self):
        _manager, provider, _caller, _outsider = _port_contexts()

        with pytest.raises(TypeError):
            provider.register_application_commands(
                provider_id="other-provider",
                operations={"records_get": "read"},
                allowed_callers={"records-cli"},
                handler=lambda invocation: {},
            )

    def test_duplicate_registration_preserves_the_first_provider(self):
        manager, provider, _caller, _outsider = _port_contexts()
        first = lambda invocation: {"first": True}
        provider.register_application_commands(
            operations={"records_get": "read"},
            allowed_callers={"records-cli"},
            handler=first,
        )

        with pytest.raises(PluginApplicationCommandRegistrationError):
            provider.register_application_commands(
                operations={"records_get": "read"},
                allowed_callers={"records-cli"},
                handler=lambda invocation: {"second": True},
            )

        assert manager._application_command_providers["records-provider"].handler is first

    @pytest.mark.parametrize(
        ("operation", "mode", "expected_mode"),
        [
            ("records_get", "read", PluginApplicationCommandMode.READ),
            ("records_update", "dry_run", PluginApplicationCommandMode.DRY_RUN),
            ("records_update", "confirm", PluginApplicationCommandMode.CONFIRM),
        ],
    )
    def test_allowed_caller_invokes_with_exact_mode_and_scoped_fingerprint(
        self, monkeypatch, operation, mode, expected_mode
    ):
        _manager, provider, caller, _outsider = _port_contexts()
        seen = []
        provider.register_application_commands(
            operations={"records_get": "read", "records_update": "write"},
            allowed_callers={"records-cli"},
            handler=lambda invocation: seen.append(invocation) or {"ok": True},
        )
        snapshot = _patch_snapshot(monkeypatch)

        result = caller.invoke_application_command(
            "records-provider",
            operation,
            {"record_id": "123"},
            mode=mode,
            invocation_id="invocation-1",
        )

        assert result == {"ok": True}
        assert len(seen) == 1
        invocation = seen[0]
        assert invocation.provider_id == "records-provider"
        assert invocation.caller_id == "records-cli"
        assert invocation.operation == operation
        assert invocation.mode is expected_mode
        assert invocation.profile_fingerprint == "profile-fingerprint"
        assert snapshot.calls == [
            (frozenset({"records-provider"}), frozenset({operation}))
        ]

    @pytest.mark.parametrize("mode", ["dry_run", "confirm"])
    def test_read_rejects_write_modes_before_handler_or_snapshot(
        self, monkeypatch, mode
    ):
        _manager, provider, caller, _outsider = _port_contexts()
        called = []
        provider.register_application_commands(
            operations={"records_get": "read"},
            allowed_callers={"records-cli"},
            handler=lambda invocation: called.append(True) or {},
        )
        snapshot = _patch_snapshot(monkeypatch)

        with pytest.raises(PluginApplicationCommandInvalid):
            caller.invoke_application_command(
                "records-provider",
                "records_get",
                {},
                mode=mode,
                invocation_id="invocation-1",
            )

        assert called == []
        assert snapshot.calls == []

    def test_write_rejects_read_mode_before_handler_or_snapshot(self, monkeypatch):
        _manager, provider, caller, _outsider = _port_contexts()
        called = []
        provider.register_application_commands(
            operations={"records_update": "write"},
            allowed_callers={"records-cli"},
            handler=lambda invocation: called.append(True) or {},
        )
        snapshot = _patch_snapshot(monkeypatch)

        with pytest.raises(PluginApplicationCommandDenied):
            caller.invoke_application_command(
                "records-provider",
                "records_update",
                {},
                mode="read",
                invocation_id="invocation-1",
            )

        assert called == []
        assert snapshot.calls == []

    def test_unlisted_operation_is_invalid_before_handler_or_snapshot(self, monkeypatch):
        _manager, provider, caller, _outsider = _port_contexts()
        called = []
        provider.register_application_commands(
            operations={"records_get": "read"},
            allowed_callers={"records-cli"},
            handler=lambda invocation: called.append(True) or {},
        )
        snapshot = _patch_snapshot(monkeypatch)

        with pytest.raises(PluginApplicationCommandInvalid):
            caller.invoke_application_command(
                "records-provider",
                "records_delete",
                {},
                mode="read",
                invocation_id="invocation-1",
            )

        assert called == []
        assert snapshot.calls == []

    def test_unlisted_caller_is_denied_before_handler_or_snapshot(self, monkeypatch):
        _manager, provider, _caller, outsider = _port_contexts()
        called = []
        provider.register_application_commands(
            operations={"records_get": "read"},
            allowed_callers={"records-cli"},
            handler=lambda invocation: called.append(True) or {},
        )
        snapshot = _patch_snapshot(monkeypatch)

        with pytest.raises(PluginApplicationCommandDenied):
            outsider.invoke_application_command(
                "records-provider",
                "records_get",
                {},
                mode="read",
                invocation_id="invocation-1",
            )

        assert called == []
        assert snapshot.calls == []

    def test_absent_provider_fails_before_configuration_snapshot(self, monkeypatch):
        _manager, _provider, caller, _outsider = _port_contexts()
        monkeypatch.setattr(
            "hermes_cli.plugin_configuration.connector_capability_snapshot",
            lambda: pytest.fail("snapshot must not run for an absent provider"),
        )

        with pytest.raises(PluginApplicationCommandUnavailable):
            caller.invoke_application_command(
                "missing-provider",
                "records_get",
                {},
                mode="read",
                invocation_id="invocation-1",
            )

    def test_arguments_are_copied_before_concurrent_caller_mutation(self, monkeypatch):
        _manager, provider, caller, _outsider = _port_contexts()
        reached_snapshot = threading.Event()
        allow_snapshot = threading.Event()
        seen = []

        class BlockingSnapshot(_FingerprintSnapshot):
            def scoped_fingerprint(self, required_services, required_tools):
                reached_snapshot.set()
                assert allow_snapshot.wait(timeout=5)
                return super().scoped_fingerprint(required_services, required_tools)

        provider.register_application_commands(
            operations={"records_get": "read"},
            allowed_callers={"records-cli"},
            handler=lambda invocation: seen.append(invocation.arguments["record"]["status"])
            or {"ok": True},
        )
        _patch_snapshot(monkeypatch, BlockingSnapshot())
        arguments = {"record": {"status": "open"}}

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                caller.invoke_application_command,
                "records-provider",
                "records_get",
                arguments,
                mode="read",
                invocation_id="invocation-1",
            )
            assert reached_snapshot.wait(timeout=5)
            arguments["record"]["status"] = "closed"
            allow_snapshot.set()
            assert future.result(timeout=5) == {"ok": True}

        assert seen == ["open"]

    def test_handler_exception_is_stable_and_closes_invocation(self, monkeypatch):
        _manager, provider, caller, _outsider = _port_contexts()
        seen = []

        def handler(invocation):
            seen.append(invocation)
            assert invocation.active is True
            raise RuntimeError("distinctive-secret-marker")

        provider.register_application_commands(
            operations={"records_get": "read"},
            allowed_callers={"records-cli"},
            handler=handler,
        )
        _patch_snapshot(monkeypatch)

        with pytest.raises(PluginApplicationCommandExecutionError) as raised:
            caller.invoke_application_command(
                "records-provider",
                "records_get",
                {},
                mode="read",
                invocation_id="invocation-1",
            )

        assert "distinctive-secret-marker" not in str(raised.value)
        assert len(seen) == 1
        assert seen[0].active is False

    @pytest.mark.parametrize(
        "invalid_result",
        [
            ["not", "a", "mapping"],
            {"value": object()},
            {"value": math.nan},
            {"value": "x" * 1_048_576},
        ],
    )
    def test_invalid_handler_result_uses_stable_execution_error(
        self, monkeypatch, invalid_result
    ):
        _manager, provider, caller, _outsider = _port_contexts()
        provider.register_application_commands(
            operations={"records_get": "read"},
            allowed_callers={"records-cli"},
            handler=lambda invocation: invalid_result,
        )
        _patch_snapshot(monkeypatch)

        with pytest.raises(PluginApplicationCommandExecutionError):
            caller.invoke_application_command(
                "records-provider",
                "records_get",
                {},
                mode="read",
                invocation_id="invocation-1",
            )

    def test_successful_handler_invocation_is_closed_after_return(self, monkeypatch):
        _manager, provider, caller, _outsider = _port_contexts()
        seen = []
        provider.register_application_commands(
            operations={"records_get": "read"},
            allowed_callers={"records-cli"},
            handler=lambda invocation: seen.append((invocation, invocation.active))
            or {"ok": True},
        )
        _patch_snapshot(monkeypatch)

        assert caller.invoke_application_command(
            "records-provider",
            "records_get",
            {},
            mode="read",
            invocation_id="invocation-1",
        ) == {"ok": True}

        assert seen[0][1] is True
        assert seen[0][0].active is False

    def test_same_internally_minted_invocation_enters_handler_only_once(
        self, monkeypatch
    ):
        import hermes_cli.plugin_application_commands as application_commands

        manager, provider, caller, _outsider = _port_contexts()
        entered = []
        provider.register_application_commands(
            operations={"records_get": "read"},
            allowed_callers={"records-cli"},
            handler=lambda invocation: entered.append(invocation) or {"ok": True},
        )
        registration = manager._application_command_providers["records-provider"]
        shared = _mint_invocation(
            provider_id="records-provider",
            caller_id="records-cli",
            operation="records_get",
            arguments={},
            mode="read",
            invocation_id="shared-invocation",
            profile_fingerprint="profile-fingerprint",
            registration_token=registration._registration_token,
        )
        monkeypatch.setattr(application_commands, "_mint_invocation", lambda **_: shared)
        _patch_snapshot(monkeypatch)

        def invoke():
            try:
                return caller.invoke_application_command(
                    "records-provider",
                    "records_get",
                    {},
                    mode="read",
                    invocation_id="shared-invocation",
                )
            except PluginApplicationCommandExecutionError:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _index: invoke(), range(2)))

        assert results.count({"ok": True}) == 1
        assert results.count("rejected") == 1
        assert entered == [shared]


def _mint_real_tool_admission(monkeypatch):
    from hermes_cli.plugins import resolve_pre_tool_admission
    from tools.registry import registry

    tool_name = "_application_command_cross_authority_tool"
    registry.deregister(tool_name)
    context = PluginContext(
        PluginManifest(name="Tool Provider", key="tool-provider", source="user"),
        PluginManager(),
    )
    context.register_tool(
        tool_name,
        "cross-authority",
        {
            "name": tool_name,
            "description": "cross-authority test probe",
            "parameters": {"type": "object", "properties": {}},
        },
        lambda arguments, **kwargs: {},
    )
    monkeypatch.setattr(
        "hermes_cli.plugins.invoke_hook",
        lambda hook_name, **kwargs: [
            {"action": "approve", "message": "confirm mutation"}
        ],
    )
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda *args, **kwargs: {"approved": True, "message": None},
    )
    decision = resolve_pre_tool_admission(
        tool_name,
        {"value": 1},
        tool_call_id="tool-call-1",
        turn_id="turn-1",
    )
    assert decision.admission is not None
    return tool_name, decision.admission


class TestAuthoritySeparation:
    @pytest.mark.parametrize("placement", ["arguments", "mode"])
    def test_model_tool_admission_cannot_enter_application_command(
        self, monkeypatch, placement
    ):
        from tools.registry import registry

        tool_name, admission = _mint_real_tool_admission(monkeypatch)
        _manager, provider, caller, _outsider = _port_contexts()
        called = []
        provider.register_application_commands(
            operations={"records_get": "read"},
            allowed_callers={"records-cli"},
            handler=lambda invocation: called.append(invocation) or {},
        )
        _patch_snapshot(monkeypatch)
        arguments = {"value": admission} if placement == "arguments" else {}
        mode = admission if placement == "mode" else "read"
        try:
            with pytest.raises(PluginApplicationCommandInvalid):
                caller.invoke_application_command(
                    "records-provider",
                    "records_get",
                    arguments,
                    mode=mode,
                    invocation_id="invocation-1",
                )
        finally:
            registry.deregister(tool_name)

        assert called == []

    def test_application_dispatch_skips_every_model_tool_path(self, monkeypatch):
        _manager, provider, caller, _outsider = _port_contexts()
        provider.register_application_commands(
            operations={"records_get": "read"},
            allowed_callers={"records-cli"},
            handler=lambda invocation: {
                "has_tool_admission": hasattr(invocation, "tool_admission")
            },
        )
        _patch_snapshot(monkeypatch)

        def unexpected(*args, **kwargs):
            pytest.fail("application commands must not enter model-tool machinery")

        monkeypatch.setattr("hermes_cli.plugins.invoke_hook", unexpected)
        monkeypatch.setattr("tools.approval.request_tool_approval", unexpected)
        monkeypatch.setattr(
            "hermes_cli.middleware.run_tool_execution_middleware", unexpected
        )
        monkeypatch.setattr("tools.registry.registry.dispatch", unexpected)

        result = caller.invoke_application_command(
            "records-provider",
            "records_get",
            {},
            mode="read",
            invocation_id="invocation-1",
        )

        assert result == {"has_tool_admission": False}

    def test_application_invocation_cannot_escape_through_result(self, monkeypatch):
        _manager, provider, caller, _outsider = _port_contexts()
        provider.register_application_commands(
            operations={"records_get": "read"},
            allowed_callers={"records-cli"},
            handler=lambda invocation: {"invocation": invocation},
        )
        _patch_snapshot(monkeypatch)

        with pytest.raises(PluginApplicationCommandExecutionError):
            caller.invoke_application_command(
                "records-provider",
                "records_get",
                {},
                mode="read",
                invocation_id="invocation-1",
            )


def test_application_command_port_is_documented():
    repository = Path(__file__).resolve().parents[2]
    plugin_guide = (
        repository / "website/docs/developer-guide/plugins/index.md"
    ).read_text(encoding="utf-8")
    cli_guide = (
        repository / "website/docs/developer-guide/extending-the-cli.md"
    ).read_text(encoding="utf-8")
    combined = f"{plugin_guide}\n{cli_guide}"

    for required_term in (
        "register_application_commands",
        "invoke_application_command",
        "register_cli_command",
        "dry_run",
        "confirm",
    ):
        assert required_term in combined
    assert "does not mint or replace model-tool approval" in combined.lower()
    assert "must not import another plugin's implementation" in combined.lower()
