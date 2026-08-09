from __future__ import annotations

from agent.plugin_agent_worker import _install_node_hooks
from hermes_cli.plugins import PluginManager


def _deny_hook():
    return ({
        "event": "PreToolUse",
        "matcher": "^read_file$",
        "response": {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "blocked",
            }
        },
    },)


def test_scoped_hook_cleanup_removes_only_token_owned_callbacks(monkeypatch):
    manager = PluginManager()
    foreign = lambda **_kwargs: {"foreign": True}
    manager._hooks["pre_tool_call"] = [foreign]
    monkeypatch.setattr("hermes_cli.plugins.get_plugin_manager", lambda: manager)

    installed = _install_node_hooks(_deny_hook())
    owned = manager._hooks["pre_tool_call"][-1]
    installed.close()

    assert manager._hooks["pre_tool_call"] == [foreign]
    assert owned not in manager._hooks["pre_tool_call"]


def test_node_hook_installer_uses_only_public_scoped_lifecycle_api(monkeypatch):
    calls = []

    class Registration:
        def close(self):
            calls.append("closed")

    class Manager:
        def register_scoped_lifecycle(self, *, hooks, middleware):
            calls.append((hooks, middleware))
            return Registration()

    monkeypatch.setattr("hermes_cli.plugins.get_plugin_manager", lambda: Manager())

    installed = _install_node_hooks(_deny_hook())
    installed.close()

    assert len(calls) == 2
    hooks, middleware = calls[0]
    assert set(hooks) == {"pre_tool_call"}
    assert set(middleware) == set()
    assert calls[1] == "closed"


def test_scoped_registration_snapshots_invocation_and_is_idempotent():
    manager = PluginManager()
    observed = []
    registration = manager.register_scoped_lifecycle(
        hooks={"pre_tool_call": (lambda **_kwargs: observed.append("owned"),)},
        middleware={},
    )

    manager.invoke_hook("pre_tool_call")
    registration.close()
    registration.close()
    manager.invoke_hook("pre_tool_call")

    assert observed == ["owned"]
