from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills/productivity/workflow-showcase"


def test_showcase_skill_is_compact_safe_router() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    _, frontmatter, content = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)

    assert metadata["name"] == "workflow-showcase"
    assert str(metadata["description"]).startswith("Use when")
    assert "result.command_contract" in content
    assert "<success_criteria>" in content
    assert len(text.splitlines()) < 120


def test_showcase_skill_routes_to_branch_procedures() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for name in (
        "explain-showcase.md",
        "run-showcase.md",
        "resume-and-report.md",
        "reset-and-cleanup.md",
    ):
        assert name in text
        assert (SKILL / "workflows" / name).is_file()
    assert (SKILL / "references/showcase-contract.md").is_file()
    assert (SKILL / "references/safety-and-interpretation.md").is_file()


def test_showcase_skill_resolves_the_active_product_cli_before_execution() -> None:
    files = sorted(SKILL.rglob("*.md"))
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    router = (SKILL / "SKILL.md").read_text(encoding="utf-8")

    assert "brand.json" in router
    assert "PRODUCT_CLI" in router
    assert "execute `PRODUCT_CLI` literally" in router
    assert "if slug ==" not in router
    assert "`hermes workflow" not in text
    assert "PRODUCT_CLI workflow showcase" in text
