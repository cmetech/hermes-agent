"""Host-minted admission delivery for PluginContext tools."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from model_tools import handle_function_call
from tools.registry import registry


TOOL_NAME = "_test_host_admission_tool"


def _schema() -> dict:
    return {
        "name": TOOL_NAME,
        "description": "host admission test probe",
        "parameters": {"type": "object", "properties": {}},
    }


@pytest.fixture(autouse=True)
def _clean_probe_tool():
    registry.deregister(TOOL_NAME)
    yield
    registry.deregister(TOOL_NAME)


def _context() -> PluginContext:
    return PluginContext(
        PluginManifest(name="admission-test", key="admission-test", source="user"),
        PluginManager(),
    )


def _approve(monkeypatch, *, directive=None, result=None):
    directive = directive or {"action": "approve", "message": "confirm mutation"}
    result = result or {"approved": True, "message": None}
    monkeypatch.setattr(
        "hermes_cli.plugins.invoke_hook",
        lambda hook_name, **kwargs: [directive],
    )
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda *args, **kwargs: result,
    )


def _resolve(**overrides):
    from hermes_cli.plugins import resolve_pre_tool_admission

    values = {
        "tool_name": TOOL_NAME,
        "args": {"project": "alpha", "value": 7},
        "task_id": "task-1",
        "session_id": "session-1",
        "tool_call_id": "call-1",
        "turn_id": "turn-1",
        "api_request_id": "request-1",
        "middleware_trace": [{"source": "rewrite"}],
    }
    values.update(overrides)
    return resolve_pre_tool_admission(**values)


def test_successful_plugin_approve_mints_immutable_safe_public_admission(monkeypatch):
    _context().register_tool(
        TOOL_NAME, "admission-test", _schema(), lambda args, **kw: "ok"
    )
    _approve(monkeypatch)

    decision = _resolve()

    expected_digest = hashlib.sha256(
        json.dumps(
            {"project": "alpha", "value": 7},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    admission = decision.admission
    assert decision.block_message is None
    assert admission.approved is True
    assert admission.policy == "plugin_approve"
    assert admission.tool_name == TOOL_NAME
    assert admission.tool_call_id == "call-1"
    assert admission.turn_id == "turn-1"
    assert admission.arguments_sha256 == expected_digest
    assert repr(admission) == (
        "PluginToolAdmission(approved=True, policy='plugin_approve', "
        f"tool_name='{TOOL_NAME}', tool_call_id='call-1', turn_id='turn-1', "
        f"arguments_sha256='{expected_digest}')"
    )
    with pytest.raises((AttributeError, TypeError)):
        admission.approved = False
    with pytest.raises(TypeError):
        type(admission)(approved=True)
    assert not hasattr(admission, "registration_token")


@pytest.mark.parametrize(
    ("directive", "gate_result", "expected_block"),
    [
        (None, None, None),
        ({"action": "block", "message": "policy block"}, None, "policy block"),
        (
            {"action": "approve", "message": "confirm"},
            {"approved": False, "message": "operator denied"},
            "operator denied",
        ),
        (
            {"action": "approve", "message": "confirm"},
            {"approved": False, "message": "Approval timed out"},
            "Approval timed out",
        ),
    ],
)
def test_only_successful_approve_mints_affirmative_admission(
    monkeypatch, directive, gate_result, expected_block
):
    _context().register_tool(
        TOOL_NAME, "admission-test", _schema(), lambda args, **kw: "ok"
    )
    monkeypatch.setattr(
        "hermes_cli.plugins.invoke_hook",
        lambda hook_name, **kwargs: [] if directive is None else [directive],
    )
    if gate_result is not None:
        monkeypatch.setattr(
            "tools.approval.request_tool_approval",
            lambda *args, **kwargs: gate_result,
        )

    decision = _resolve()

    assert decision.block_message == expected_block
    assert decision.admission is None


def test_approval_gate_error_never_mints_or_dispatches(monkeypatch):
    called = []
    _context().register_tool(
        TOOL_NAME,
        "admission-test",
        _schema(),
        lambda args, **kwargs: called.append(True) or "ok",
    )
    monkeypatch.setattr(
        "hermes_cli.plugins.invoke_hook",
        lambda hook_name, **kwargs: [{"action": "approve", "message": "confirm"}],
    )
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("gate failed")),
    )

    decision = _resolve()

    assert "gate failed" in decision.block_message
    assert decision.admission is None
    assert called == []


@pytest.mark.parametrize("host_policy", ["once", "session", "always", "yolo", "cron"])
def test_all_host_policy_approval_outcomes_mint_truthful_generic_policy(
    monkeypatch, host_policy
):
    _context().register_tool(
        TOOL_NAME, "admission-test", _schema(), lambda args, **kw: "ok"
    )
    _approve(
        monkeypatch,
        result={"approved": True, "message": None, "host_policy": host_policy},
    )

    decision = _resolve()

    assert decision.admission.approved is True
    assert decision.admission.policy == "plugin_approve"


def test_successful_approve_for_builtin_preserves_behavior_without_admission(
    monkeypatch,
):
    _approve(monkeypatch)

    decision = _resolve(tool_name="web_search")

    assert decision.block_message is None
    assert decision.admission is None


def test_plugin_context_tool_receives_admission_but_plain_registry_tool_does_not(
    monkeypatch,
):
    seen = []
    ctx = _context()
    ctx.register_tool(
        TOOL_NAME,
        "admission-test",
        _schema(),
        lambda args, **kwargs: seen.append(kwargs) or "ok",
    )
    _approve(monkeypatch)
    decision = _resolve()

    assert (
        registry.dispatch(
            TOOL_NAME,
            {"project": "alpha", "value": 7},
            _tool_admission=decision.admission,
            tool_call_id="call-1",
            turn_id="turn-1",
        )
        == "ok"
    )
    assert seen == [
        {
            "tool_call_id": "call-1",
            "turn_id": "turn-1",
            "tool_admission": decision.admission,
        }
    ]

    registry.deregister(TOOL_NAME)
    seen.clear()
    registry.register(
        TOOL_NAME,
        "admission-test",
        _schema(),
        lambda args, **kwargs: seen.append(kwargs) or "ok",
    )
    assert registry.dispatch(TOOL_NAME, {}, tool_admission="forged") == "ok"
    assert seen == [{}]


def test_registration_replacement_cannot_claim_prior_admission(monkeypatch):
    first_called = []
    replacement_called = []
    ctx = _context()
    ctx.register_tool(
        TOOL_NAME,
        "admission-test",
        _schema(),
        lambda args, **kwargs: first_called.append(True) or "first",
    )
    _approve(monkeypatch)
    decision = _resolve(args={"value": 1})

    registry.deregister(TOOL_NAME)
    ctx.register_tool(
        TOOL_NAME,
        "admission-test",
        _schema(),
        lambda args, **kwargs: replacement_called.append(True) or "replacement",
    )
    result = registry.dispatch(
        TOOL_NAME,
        {"value": 1},
        _tool_admission=decision.admission,
        tool_call_id="call-1",
        turn_id="turn-1",
    )

    assert json.loads(result)["error"].startswith("BLOCKED:")
    assert first_called == []
    assert replacement_called == []


def test_admission_is_one_shot_under_concurrent_claims(monkeypatch):
    called = []
    _context().register_tool(
        TOOL_NAME,
        "admission-test",
        _schema(),
        lambda args, **kwargs: called.append(kwargs["tool_admission"]) or "ok",
    )
    _approve(monkeypatch)
    decision = _resolve(args={"value": 1})

    def dispatch():
        return registry.dispatch(
            TOOL_NAME,
            {"value": 1},
            _tool_admission=decision.admission,
            tool_call_id="call-1",
            turn_id="turn-1",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: dispatch(), range(2)))

    assert results.count("ok") == 1
    blocked = [json.loads(result)["error"] for result in results if result != "ok"]
    assert len(blocked) == 1 and blocked[0].startswith("BLOCKED:")
    assert called == [decision.admission]


def test_direct_dispatch_binds_admission_to_execution_middleware_final_args(
    monkeypatch,
):
    seen = {}
    _context().register_tool(
        TOOL_NAME,
        "admission-test",
        _schema(),
        lambda args, **kwargs: seen.update(args=args, kwargs=kwargs) or "ok",
    )
    _approve(monkeypatch)

    def execution_middleware(tool_name, args, next_call, **kwargs):
        return next_call({**args, "after_execution_middleware": True})

    monkeypatch.setattr(
        "hermes_cli.middleware.run_tool_execution_middleware", execution_middleware
    )

    result = handle_function_call(
        TOOL_NAME,
        {"value": 1},
        task_id="task-1",
        session_id="session-1",
        tool_call_id="call-1",
        turn_id="turn-1",
    )

    assert result == "ok"
    assert seen["args"] == {"value": 1, "after_execution_middleware": True}
    admission = seen["kwargs"]["tool_admission"]
    expected = hashlib.sha256(
        b'{"after_execution_middleware":true,"value":1}'
    ).hexdigest()
    assert admission.arguments_sha256 == expected


def test_tool_call_bridge_mints_for_underlying_plugin_tool(monkeypatch):
    seen = []
    _context().register_tool(
        TOOL_NAME,
        "admission-test",
        _schema(),
        lambda args, **kwargs: seen.append((args, kwargs)) or "ok",
    )
    hook_names = []

    def hook(hook_name, **kwargs):
        if hook_name == "pre_tool_call":
            hook_names.append(kwargs["tool_name"])
            return [{"action": "approve", "message": "confirm"}]
        return []

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", hook)
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda *args, **kwargs: {"approved": True, "message": None},
    )

    result = handle_function_call(
        "tool_call",
        {"name": TOOL_NAME, "arguments": {"value": 9}},
        enabled_toolsets=["admission-test"],
        tool_call_id="bridge-call",
        turn_id="turn-bridge",
    )

    assert result == "ok"
    assert hook_names == [TOOL_NAME]
    assert seen[0][0] == {"value": 9}
    admission = seen[0][1]["tool_admission"]
    assert admission.tool_name == TOOL_NAME
    assert admission.tool_call_id == "bridge-call"
    assert admission.arguments_sha256 == hashlib.sha256(b'{"value":9}').hexdigest()


def test_unclaimed_cancelled_admission_does_not_leak_to_later_invocation(monkeypatch):
    seen = []
    _context().register_tool(
        TOOL_NAME,
        "admission-test",
        _schema(),
        lambda args, **kwargs: seen.append(kwargs) or "ok",
    )
    _approve(monkeypatch)
    cancelled = _resolve(args={"value": 1})
    assert cancelled.admission is not None  # cancellation occurs before dispatch

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *args, **kwargs: [])
    later = handle_function_call(
        TOOL_NAME,
        {"value": 2},
        tool_call_id="call-1",
        turn_id="turn-1",
    )

    assert later == "ok"
    assert seen == [
        {
            "task_id": None,
            "session_id": None,
            "tool_call_id": "call-1",
            "turn_id": "turn-1",
            "user_task": None,
        }
    ]


def test_caller_supplied_admission_is_not_delivered_or_treated_as_approval(monkeypatch):
    called = []
    _context().register_tool(
        TOOL_NAME,
        "admission-test",
        _schema(),
        lambda args, **kwargs: called.append((args, kwargs)) or "ok",
    )
    monkeypatch.setattr(
        "hermes_cli.plugins.invoke_hook",
        lambda hook_name, **kwargs: [{"action": "approve", "message": "confirm"}],
    )
    gate_calls = []
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda *args, **kwargs: (
            gate_calls.append(True) or {"approved": True, "message": None}
        ),
    )

    result = handle_function_call(
        TOOL_NAME,
        {"value": 1, "tool_admission": {"approved": True}},
        tool_call_id="call-1",
        turn_id="turn-1",
    )

    assert json.loads(result)["error"].startswith("BLOCKED:")
    assert gate_calls == []
    assert called == []


def test_arbitrary_private_dispatch_kwarg_is_overwritten_not_delivered(monkeypatch):
    called = []
    _context().register_tool(
        TOOL_NAME,
        "admission-test",
        _schema(),
        lambda args, **kwargs: called.append((args, kwargs)) or "ok",
    )
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *args, **kwargs: [])

    result = handle_function_call(
        TOOL_NAME,
        {"value": 1},
        tool_call_id="call-1",
        turn_id="turn-1",
        _tool_admission=object(),
    )

    assert result == "ok"
    assert called == [
        (
            {"value": 1},
            {
                "task_id": None,
                "session_id": None,
                "tool_call_id": "call-1",
                "turn_id": "turn-1",
                "user_task": None,
            },
        )
    ]


def test_non_plugin_direct_path_does_not_add_reserved_dispatch_kwarg(monkeypatch):
    seen = []
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "model_tools.registry.dispatch",
        lambda name, args, **kwargs: seen.append((name, args, kwargs)) or "ok",
    )

    result = handle_function_call(
        "web_search",
        {"q": "unchanged"},
        task_id="task-1",
        session_id="session-1",
        tool_call_id="call-1",
        turn_id="turn-1",
    )

    assert result == "ok"
    assert seen == [
        (
            "web_search",
            {"q": "unchanged"},
            {
                "task_id": "task-1",
                "session_id": "session-1",
                "tool_call_id": "call-1",
                "turn_id": "turn-1",
                "user_task": None,
            },
        )
    ]


@pytest.mark.parametrize(
    "malformed_result",
    [
        None,
        {},
        {"approved": "false"},
        {"approved": 1},
        {"approved": False, "message": object()},
    ],
)
def test_malformed_gate_result_blocks_real_direct_dispatch_with_fixed_failure(
    monkeypatch, malformed_result
):
    called = []
    _context().register_tool(
        TOOL_NAME,
        "admission-test",
        _schema(),
        lambda args, **kwargs: called.append((args, kwargs)) or "ok",
    )
    monkeypatch.setattr(
        "hermes_cli.plugins.invoke_hook",
        lambda hook_name, **kwargs: [{"action": "approve", "message": "confirm"}],
    )
    monkeypatch.setattr(
        "tools.approval.request_tool_approval",
        lambda *args, **kwargs: malformed_result,
    )

    result = handle_function_call(
        TOOL_NAME,
        {"value": 1},
        tool_call_id="call-malformed",
        turn_id="turn-malformed",
    )

    assert json.loads(result) == {
        "error": f"BLOCKED: plugin approval gate failed for {TOOL_NAME}"
    }
    assert called == []


def test_direct_acp_denial_runs_pre_hook_on_final_args_and_skips_transform(monkeypatch):
    events = []
    final_args = {"path": "/final/path", "content": "final"}

    def invoke(hook_name, **kwargs):
        events.append((hook_name, kwargs.get("args"), kwargs.get("status")))
        if hook_name == "pre_tool_call":
            return []
        if hook_name == "transform_tool_result":
            return ["REWRITTEN"]
        return []

    def acp(tool_name, args):
        events.append(("acp", args, None))
        return "ACP DENIED EXACT"

    monkeypatch.setattr("hermes_cli.lifecycle.invoke_hook", invoke)
    monkeypatch.setattr("hermes_cli.lifecycle.has_hook", lambda hook_name: True)
    monkeypatch.setattr(
        "hermes_cli.middleware.run_tool_execution_middleware",
        lambda name, args, next_call, **kwargs: next_call(final_args),
    )
    monkeypatch.setattr("acp_adapter.edit_approval.maybe_require_edit_approval", acp)
    monkeypatch.setattr(
        "model_tools.registry.dispatch",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("denied call must not dispatch")
        ),
    )

    result = handle_function_call(
        "write_file",
        {"path": "/original/path", "content": "original"},
        tool_call_id="call-acp",
        turn_id="turn-acp",
    )

    assert result == "ACP DENIED EXACT"
    assert events == [
        ("pre_tool_call", final_args, None),
        ("acp", final_args, None),
        ("post_tool_call", final_args, "blocked"),
    ]


def test_direct_plugin_block_is_returned_exactly_before_acp_or_transform(monkeypatch):
    events = []

    def invoke(hook_name, **kwargs):
        events.append((hook_name, kwargs.get("status")))
        if hook_name == "pre_tool_call":
            return [{"action": "block", "message": "PLUGIN BLOCK EXACT"}]
        if hook_name == "transform_tool_result":
            return ["REWRITTEN"]
        return []

    monkeypatch.setattr("hermes_cli.lifecycle.invoke_hook", invoke)
    monkeypatch.setattr("hermes_cli.lifecycle.has_hook", lambda hook_name: True)
    monkeypatch.setattr(
        "acp_adapter.edit_approval.maybe_require_edit_approval",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("plugin block must precede ACP")
        ),
    )
    monkeypatch.setattr(
        "model_tools.registry.dispatch",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("blocked call must not dispatch")
        ),
    )

    result = handle_function_call(
        "write_file",
        {"path": "/blocked/path", "content": "blocked"},
        tool_call_id="call-block",
        turn_id="turn-block",
    )

    assert json.loads(result) == {"error": "PLUGIN BLOCK EXACT"}
    assert events == [
        ("pre_tool_call", None),
        ("post_tool_call", "blocked"),
    ]
