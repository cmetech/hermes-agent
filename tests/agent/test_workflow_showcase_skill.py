from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills/productivity/workflow-showcase"


def test_showcase_skill_is_compact_safe_router() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")

    for intent in (
        "showcase",
        "tour",
        "Laptop Diagnostic",
        "resilience",
        "retry",
        "timeout",
        "cancel",
        "AI",
        "scheduling",
        "status",
        "report",
        "resume",
        "cleanup",
    ):
        assert intent.lower() in text.lower()
    assert "--json" in text
    assert "never approve" in text.lower()
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
