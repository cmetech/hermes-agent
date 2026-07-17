from pathlib import Path

from agent.skill_commands import build_skill_invocation_message


SKILL = Path(__file__).parents[2] / "skills/productivity/workflow/SKILL.md"


def test_gateway_workflow_invocations_preserve_arguments(monkeypatch):
    monkeypatch.setattr(
        "agent.skill_commands.get_skill_commands",
        lambda: {"/workflow": {"name": "workflow", "skill_dir": str(SKILL.parent)}},
    )
    monkeypatch.setattr(
        "agent.skill_commands._load_skill_payload",
        lambda *_args, **_kwargs: ({"content": SKILL.read_text(encoding="utf-8")}, SKILL.parent, "workflow"),
    )
    for instruction in ("run demo", "list", "show demo", "runs", "status RUN-1"):
        message = build_skill_invocation_message("/workflow", instruction)
        assert message is not None
        assert instruction in message
        assert "PRODUCT_CLI workflow" in message


def test_disabled_plugin_guidance_is_actionable():
    text = SKILL.read_text(encoding="utf-8")
    assert "PRODUCT_CLI plugins enable workflow" in text
