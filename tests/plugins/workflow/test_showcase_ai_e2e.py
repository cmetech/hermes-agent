from __future__ import annotations

from plugins.workflow.showcase import preflight_showcase, run_showcase


def test_ai_tour_skips_cleanly_without_explicit_digest_bound_consent(tmp_path) -> None:
    preflight = preflight_showcase("ai-extensions", hermes_home=tmp_path)

    result = run_showcase("ai-extensions", hermes_home=tmp_path)

    assert result["status"] == "skipped"
    assert result["reason_code"] == "ai_confirmation_required"
    assert result["confirmation_token"] == preflight["confirmation_token"]


def test_ai_preflight_lists_bounded_extensions_without_connecting(tmp_path) -> None:
    result = preflight_showcase("ai-extensions", hermes_home=tmp_path)

    assert result["requires_ai"] is True
    assert result["requested_skills"] == ["ascii-art"]
    assert result["local_mcp_servers"] == ["mcp/echo.yaml"]
    assert result["inline_agent_limit"] == 1
    assert result["wall_seconds"] <= 600
