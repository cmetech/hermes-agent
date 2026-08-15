def test_tui_session_control_is_consumed_by_exactly_one_prompt():
    from tui_gateway.methods_prompt import _consume_session_tool_choice
    from tui_gateway.methods_tools import _handle_tool_choice_control

    session = {}

    output = _handle_tool_choice_control(session, "required --otto-v1")
    first = _consume_session_tool_choice(session)
    second = _consume_session_tool_choice(session)

    assert "required" in output
    assert first.policy.mode == "required"
    assert first.otto_contract_version == "v1"
    assert second is None


def test_tui_auto_clears_a_pending_named_choice():
    from tui_gateway.methods_prompt import _consume_session_tool_choice
    from tui_gateway.methods_tools import _handle_tool_choice_control

    session = {}
    _handle_tool_choice_control(session, "named tool_call --otto-v1")

    output = _handle_tool_choice_control(session, "auto")

    assert "automatic" in output
    assert _consume_session_tool_choice(session) is None


def test_tool_choice_configure_rpc_scopes_and_consumes_one_shot():
    from tui_gateway.methods_prompt import _consume_session_tool_choice
    from tui_gateway import server

    server._sessions["desktop-a"] = {"history": []}
    server._sessions["desktop-b"] = {"history": []}
    try:
        response = server._methods["tool_choice.configure"](
            "rpc-1",
            {
                "session_id": "desktop-a",
                "arguments": "required --otto-v1",
            },
        )

        assert response["result"] == {
            "output": "Next turn tool choice: required with OTTO v1."
        }
        first = _consume_session_tool_choice(server._sessions["desktop-a"])
        assert first.policy.mode == "required"
        assert first.otto_contract_version == "v1"
        assert _consume_session_tool_choice(server._sessions["desktop-a"]) is None
        assert _consume_session_tool_choice(server._sessions["desktop-b"]) is None
    finally:
        server._sessions.pop("desktop-a", None)
        server._sessions.pop("desktop-b", None)


def test_tool_choice_configure_rpc_preserves_parser_validation():
    from tui_gateway.methods_prompt import _consume_session_tool_choice
    from tui_gateway import server

    server._sessions["desktop-invalid"] = {"history": []}
    try:
        response = server._methods["tool_choice.configure"](
            "rpc-2",
            {"session_id": "desktop-invalid", "arguments": "named"},
        )

        assert response["error"] == {
            "code": 4004,
            "message": "Usage: /tool-choice named <tool> [--otto-v1]",
        }
        assert _consume_session_tool_choice(server._sessions["desktop-invalid"]) is None
    finally:
        server._sessions.pop("desktop-invalid", None)


def test_slash_exec_tool_choice_compatibility_path_uses_same_control():
    from tui_gateway.methods_prompt import _consume_session_tool_choice
    from tui_gateway import server

    server._sessions["desktop-compat"] = {"history": []}
    try:
        response = server._methods["slash.exec"](
            "rpc-3",
            {
                "session_id": "desktop-compat",
                "command": "/tool-choice required --otto-v1",
            },
        )

        assert response["result"] == {
            "output": "Next turn tool choice: required with OTTO v1."
        }
        context = _consume_session_tool_choice(server._sessions["desktop-compat"])
        assert context.policy.mode == "required"
        assert context.otto_contract_version == "v1"
    finally:
        server._sessions.pop("desktop-compat", None)
