from types import SimpleNamespace

import pytest


class _Runner:
    def __init__(self):
        self._tool_choice_controls = {}

    def _session_key_for_source(self, source):
        return source.session_key


@pytest.mark.asyncio
async def test_gateway_command_sets_session_local_one_shot_control():
    from gateway.slash_commands import GatewaySlashCommandsMixin
    from gateway.run import _consume_gateway_tool_choice

    runner = _Runner()
    event = SimpleNamespace(
        source=SimpleNamespace(session_key="session-fixture"),
        get_command_args=lambda: "required --otto-v1",
    )

    output = await GatewaySlashCommandsMixin._handle_tool_choice_command(
        runner, event
    )
    first = _consume_gateway_tool_choice(runner, "session-fixture")
    second = _consume_gateway_tool_choice(runner, "session-fixture")

    assert "required" in output
    assert first.policy.mode == "required"
    assert first.otto_contract_version == "v1"
    assert second is None


def test_gateway_conversation_boundary_can_drop_pending_control():
    from agent.tool_choice_control import OneShotToolChoice

    runner = _Runner()
    control = OneShotToolChoice()
    control.set_required(otto_v1=True)
    runner._tool_choice_controls["session-fixture"] = control

    runner._tool_choice_controls.pop("session-fixture", None)

    assert "session-fixture" not in runner._tool_choice_controls
