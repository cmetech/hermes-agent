from __future__ import annotations

import json

import pytest

import plugins.workflow.showcase as showcase
from plugins.workflow.showcase import (
    ShowcaseCatalogError,
    preflight_showcase,
    run_showcase,
)


def test_ai_preflight_has_no_confirmation_gate(tmp_path) -> None:
    preflight = preflight_showcase("ai-extensions", hermes_home=tmp_path)

    assert preflight["requires_confirmation"] is False
    assert preflight["confirmation_kind"] is None
    assert preflight["confirmation_token"] is None
    serialized = json.dumps(preflight, sort_keys=True)
    assert "consent_required" not in serialized
    assert "ai_confirmation_required" not in serialized


def test_ai_preflight_lists_bounded_extensions_without_connecting(tmp_path) -> None:
    result = preflight_showcase("ai-extensions", hermes_home=tmp_path)

    assert result["requires_ai"] is True
    assert result["requested_skills"] == ["ascii-art"]
    assert result["local_mcp_servers"] == ["mcp/echo.yaml"]
    assert result["inline_agent_limit"] == 1
    assert result["wall_seconds"] <= 600


@pytest.mark.parametrize("legacy_confirmation_token", [None, "legacy-ai-token"])
def test_cli_run_rejects_ai_showcase_before_execution_side_effects(
    tmp_path, monkeypatch, legacy_confirmation_token
) -> None:
    side_effects: list[str] = []

    def forbidden(name):
        def record(*_args, **_kwargs):
            side_effects.append(name)
            raise AssertionError(f"unexpected {name} side effect")

        return record

    monkeypatch.setattr(showcase, "_store", forbidden("durable store"))
    monkeypatch.setattr(showcase, "_scheduler", forbidden("scheduler"))
    monkeypatch.setattr(showcase, "_advance_until_wait", forbidden("runner"))
    monkeypatch.setattr(showcase, "_stage_fixture", forbidden("staging"))
    monkeypatch.setattr(showcase, "create_job", forbidden("cron"))
    monkeypatch.setattr("subprocess.Popen", forbidden("MCP/provider process"))
    existing_home_entries = set(tmp_path.iterdir())

    with pytest.raises(
        ShowcaseCatalogError,
        match="not runnable under ordinary compatibility policy",
    ):
        run_showcase(
            "ai-extensions",
            hermes_home=tmp_path,
            confirmation_token=legacy_confirmation_token,
        )

    assert side_effects == []
    assert set(tmp_path.iterdir()) == existing_home_entries
