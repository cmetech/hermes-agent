from pathlib import Path

import yaml

from agent.skill_commands import build_skill_invocation_message


ROOT = Path(__file__).parents[2]
SKILL = ROOT / "skills/productivity/workflow/SKILL.md"


def _frontmatter() -> dict[str, object]:
    text = SKILL.read_text(encoding="utf-8")
    _, raw, _ = text.split("---", 2)
    return yaml.safe_load(raw)


def test_workflow_skill_description_covers_operator_intents_concisely():
    metadata = _frontmatter()
    description = str(metadata["description"])
    assert description.startswith("Use when")
    assert metadata["platforms"] == ["darwin", "linux", "windows"]
    assert len(description) <= 700


def test_workflow_slash_command_loads_as_one_user_message(monkeypatch):
    commands = {
        "/workflow": {
            "name": "workflow",
            "description": _frontmatter()["description"],
            "skill_dir": str(SKILL.parent),
        }
    }
    monkeypatch.setattr("agent.skill_commands.get_skill_commands", lambda: commands)
    monkeypatch.setattr(
        "agent.skill_commands._load_skill_payload",
        lambda *_args, **_kwargs: ({"content": SKILL.read_text(encoding="utf-8")}, SKILL.parent, "workflow"),
    )

    message = build_skill_invocation_message("/workflow", "run demo")

    assert message is not None
    assert "run demo" in message
    assert "PRODUCT_CLI workflow" in message
    assert message.count("run demo") == 1


def test_workflow_is_edge_capability_not_a_core_model_tool():
    from toolsets import _HERMES_CORE_TOOLS

    assert "workflow" not in _HERMES_CORE_TOOLS
    assert "workflow_run" not in _HERMES_CORE_TOOLS
