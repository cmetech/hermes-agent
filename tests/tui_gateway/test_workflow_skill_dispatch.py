from pathlib import Path
from unittest.mock import MagicMock, patch


def test_tui_command_dispatch_returns_workflow_skill_payload():
    skill = Path(__file__).parents[2] / "skills/productivity/workflow/SKILL.md"
    with patch.dict("sys.modules", {
        "hermes_constants": MagicMock(get_hermes_home=MagicMock(return_value="/tmp/hermes_test")),
        "hermes_cli.env_loader": MagicMock(),
        "hermes_cli.banner": MagicMock(),
        "hermes_state": MagicMock(),
    }):
        import tui_gateway.server as server

        server._sessions["workflow-session"] = {"session_key": "workflow-session"}
        commands = {"/workflow": {"name": "workflow", "skill_dir": str(skill.parent)}}
        with patch("agent.skill_commands.scan_skill_commands", return_value=commands), patch(
            "agent.skill_commands.get_skill_commands", return_value=commands
        ), patch(
            "agent.skill_commands.build_skill_invocation_message",
            return_value="Loaded workflow skill\nshow demo",
        ):
            response = server.handle_request({
                "id": "workflow",
                "method": "command.dispatch",
                "params": {"name": "workflow", "arg": "show demo", "session_id": "workflow-session"},
            })
        server._sessions.clear()

    assert response["result"]["type"] == "skill"
    assert "show demo" in response["result"]["message"]
