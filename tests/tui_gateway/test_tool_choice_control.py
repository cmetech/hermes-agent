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


def test_tui_prompt_handler_installs_tool_choice_consumer_in_server_namespace():
    from tui_gateway import server

    assert server._consume_session_tool_choice({}) is None
