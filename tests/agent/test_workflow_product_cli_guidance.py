from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_SKILL_ROOTS = (
    ROOT / "skills/productivity/workflow",
    ROOT / "skills/productivity/workflow-showcase",
    ROOT / "skills/software-development/workflow-builder",
    ROOT / "skills/ericsson/onboard-ericsson-capabilities",
)


def test_workflow_skills_resolve_the_active_product_cli() -> None:
    for skill_root in WORKFLOW_SKILL_ROOTS:
        router = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        assert "PRODUCT_CLI" in router, skill_root
        assert "brand.json" in router, skill_root
        assert "loop24" in router, skill_root
        assert "otto" in router, skill_root


def test_workflow_skill_commands_never_hardcode_the_neutral_executable() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for skill_root in WORKFLOW_SKILL_ROOTS
        for path in sorted(skill_root.rglob("*.md"))
    )

    assert "`hermes workflow" not in text
    assert "\nhermes workflow" not in text
    assert "`hermes plugins" not in text
    assert "PRODUCT_CLI workflow" in text
