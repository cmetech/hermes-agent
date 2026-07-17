from __future__ import annotations

import json
import argparse
from pathlib import Path

import pytest

from plugins.workflow.showcase import (
    ShowcaseCatalogError,
    load_showcase_catalog,
    preflight_showcase,
)
from plugins.workflow.cli import register_cli


def test_catalog_has_four_safe_digest_verified_scenarios() -> None:
    catalog = load_showcase_catalog()

    assert tuple(catalog) == (
        "ai-extensions",
        "laptop-diagnostic",
        "resilience",
        "scheduling",
    )
    assert catalog["laptop-diagnostic"].offline is True
    assert catalog["laptop-diagnostic"].requires_ai is False
    assert catalog["ai-extensions"].requires_ai is True
    assert catalog["scheduling"].interaction_mode == "schedule"
    assert all(item.package_digest for item in catalog.values())
    assert all("destructive" not in item.safety_class for item in catalog.values())


def test_catalog_validation_rejects_traversal_and_destructive_claims(
    tmp_path: Path,
) -> None:
    root = tmp_path / "showcases"
    root.mkdir()
    (root / "catalog.yaml").write_text(
        "schema_version: 1\nscenarios:\n"
        "  - id: bad\n"
        "    display_name: Bad\n"
        "    purpose: bad\n"
        "    bundle_version: '1'\n"
        "    package_version: '1'\n"
        "    workflow_path: ../escape.yaml\n"
        "    interaction_mode: guided\n"
        "    offline: true\n"
        "    requires_ai: false\n"
        "    requires_network: false\n"
        "    safety_class: destructive\n"
        "    supported_platforms: [linux]\n"
        "    expected_checkpoints: []\n"
        "    expected_terminal_outcomes: [succeeded]\n"
        "    expected_artifacts: []\n"
        "    capability_claims: [corruption]\n"
        "    limits: {wall_seconds: 1}\n"
        "    cleanup_ownership: bad\n",
        encoding="utf-8",
    )
    (root / "digests.json").write_text(
        json.dumps({"schema_version": 1, "packages": {"bad": "0" * 64}}),
        encoding="utf-8",
    )

    with pytest.raises(ShowcaseCatalogError):
        load_showcase_catalog(root)


def test_preflight_is_side_effect_free_and_reports_explicit_opt_ins(
    tmp_path: Path, monkeypatch
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("preflight initialized execution or network state")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    laptop = preflight_showcase("laptop-diagnostic", hermes_home=tmp_path)
    ai = preflight_showcase("ai-extensions", hermes_home=tmp_path)

    assert laptop["runnable"] is True
    assert laptop["offline"] is True
    assert laptop["requires_confirmation"] is False
    assert ai["requires_confirmation"] is True
    assert ai["confirmation_kind"] == "ai"
    assert ai["confirmation_token"]


def test_showcase_cli_list_and_missing_input_have_stable_exit_categories(
    tmp_path: Path, capsys
) -> None:
    parser = argparse.ArgumentParser()
    command = parser.add_subparsers().add_parser("workflow")
    register_cli(command)

    listed = parser.parse_args(
        ["workflow", "--hermes-home", str(tmp_path), "showcase", "list", "--json"]
    )
    assert listed.func(listed) == 0
    assert len(json.loads(capsys.readouterr().out)) == 4

    missing = parser.parse_args(
        [
            "workflow", "--hermes-home", str(tmp_path), "showcase", "run",
            "laptop-diagnostic", "--json",
        ]
    )
    assert missing.func(missing) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["reason_code"] == "showcase_input_required"
