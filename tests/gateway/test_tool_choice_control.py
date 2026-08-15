import pytest


def _runner_and_source():
    from gateway.config import GatewayConfig, Platform
    from gateway.run import GatewayRunner
    from gateway.session import SessionSource, SessionStore

    config = GatewayConfig(multiplex_profiles=True)
    store = object.__new__(SessionStore)
    store.config = config
    runner = object.__new__(GatewayRunner)
    runner.config = config
    runner.session_store = store
    runner._tool_choice_controls = {}
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="chat-fixture",
        user_id="user-fixture",
        profile="profile-fixture",
    )
    return runner, source


@pytest.mark.asyncio
async def test_gateway_command_sets_session_local_one_shot_control():
    from gateway.run import TurnRunner, _consume_gateway_tool_choice
    from gateway.turn_context import TurnContext
    from gateway.slash_commands import GatewaySlashCommandsMixin

    runner, source = _runner_and_source()
    event = type(
        "EventFixture",
        (),
        {
            "source": source,
            "get_command_args": lambda self: "required --otto-v1",
        },
    )()

    output = await GatewaySlashCommandsMixin._handle_tool_choice_command(
        runner, event
    )
    session_key = runner.session_store._generate_session_key(source)
    turn = TurnRunner(
        runner,
        TurnContext(source=source, session_key=session_key),
    )
    first = _consume_gateway_tool_choice(
        turn._runner,
        turn._ctx.session_key,
    )
    second = _consume_gateway_tool_choice(
        turn._runner,
        turn._ctx.session_key,
    )

    assert "required" in output
    assert session_key.startswith("agent:profile-fixture:")
    assert first.policy.mode == "required"
    assert first.otto_contract_version == "v1"
    assert second is None


def test_gateway_conversation_boundary_drops_pending_control():
    from agent.tool_choice_control import OneShotToolChoice

    runner, source = _runner_and_source()
    session_key = runner._session_key_for_source(source)
    control = OneShotToolChoice()
    control.set_required(otto_v1=True)
    runner._tool_choice_controls[session_key] = control

    runner._clear_conversation_scope(session_key, reason="test")

    assert session_key not in runner._tool_choice_controls
