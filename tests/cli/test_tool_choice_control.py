from unittest.mock import patch


def test_required_control_is_visible_and_consumed_once():
    from agent.tool_choice_control import OneShotToolChoice, configure_tool_choice

    control = OneShotToolChoice()

    message = configure_tool_choice(control, "required --otto-v1")
    first = control.consume_context(operation_id="operation-fixture")
    second = control.consume_context(operation_id="operation-next")

    assert "required" in message
    assert "OTTO v1" in message
    assert first.policy.mode == "required"
    assert first.otto_contract_version == "v1"
    assert second is None


def test_named_control_selects_the_explicit_outer_tool():
    from agent.tool_choice_control import OneShotToolChoice, configure_tool_choice

    control = OneShotToolChoice()
    configure_tool_choice(control, "named tool_call --otto-v1")

    context = control.consume_context(operation_id="operation-fixture")

    assert context.policy.mode == "named"
    assert context.policy.name == "tool_call"


def test_auto_and_off_clear_pending_control():
    from agent.tool_choice_control import OneShotToolChoice, configure_tool_choice

    control = OneShotToolChoice()
    configure_tool_choice(control, "required --otto-v1")

    auto_message = configure_tool_choice(control, "auto")
    assert "automatic" in auto_message
    assert control.consume_context() is None

    configure_tool_choice(control, "required --otto-v1")
    off_message = configure_tool_choice(control, "off")
    assert "disabled" in off_message
    assert control.consume_context() is None


def test_cli_command_updates_frontend_holder_without_transcript_text():
    from agent.tool_choice_control import OneShotToolChoice
    from cli import HermesCLI

    cli = object.__new__(HermesCLI)
    cli._tool_choice_control = OneShotToolChoice()
    cli._pending_resume_sessions = None

    with patch("cli._cprint") as output:
        assert cli.process_command("/tool-choice required --otto-v1") is True

    context = cli._tool_choice_control.consume_context(
        operation_id="operation-fixture"
    )
    assert context.policy.mode == "required"
    assert context.otto_contract_version == "v1"
    assert "required" in output.call_args.args[0]
