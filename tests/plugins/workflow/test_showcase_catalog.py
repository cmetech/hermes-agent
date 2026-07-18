from __future__ import annotations

import json
import argparse
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess

import pytest

from hermes_cli import capability_staging
from plugins.workflow.showcase import (
    ShowcaseCatalogError,
    load_showcase_catalog,
    preflight_showcase,
)
from plugins.workflow.cli import register_cli
import plugins.workflow.showcase as showcase_module


REPO_ROOT = Path(__file__).resolve().parents[3]


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


def test_default_catalog_repairs_crlf_only_managed_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    bundle = checkout / "plugins/workflow/showcases"
    bundle.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / ".gitattributes", checkout / ".gitattributes")
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", bundle)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.name", "Showcase Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "-qm", "fixture"], check=True
    )
    for path in bundle.rglob("*"):
        if path.is_file():
            data = path.read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(data.replace(b"\n", b"\r\n"))

    @contextmanager
    def installed_bundle(_explicit=None):
        yield bundle

    monkeypatch.setattr(showcase_module, "_bundle_path", installed_bundle)

    catalog = showcase_module.load_showcase_catalog()

    assert "laptop-diagnostic" in catalog
    assert b"\r\n" not in (bundle / "catalog.yaml").read_bytes()


def test_repair_forces_tracked_bytes_when_checkout_is_a_successful_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    bundle = checkout / "plugins/workflow/showcases"
    bundle.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / ".gitattributes", checkout / ".gitattributes")
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", bundle)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.name", "Showcase Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "-qm", "fixture"], check=True
    )
    for path in bundle.rglob("*"):
        if path.is_file():
            data = path.read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(data.replace(b"\n", b"\r\n"))

    original_run = capability_staging.subprocess.run

    def successful_noop_checkout(command, *args, **kwargs):
        if command[-4:] == ["checkout", "HEAD", "--", "plugins/workflow/showcases"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(capability_staging.subprocess, "run", successful_noop_checkout)

    assert capability_staging.repair_authenticated_resource_checkout(bundle) is True
    assert b"\r\n" not in (bundle / "catalog.yaml").read_bytes()


def test_default_catalog_does_not_repair_semantic_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    bundle = checkout / "plugins/workflow/showcases"
    bundle.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / ".gitattributes", checkout / ".gitattributes")
    shutil.copytree(REPO_ROOT / "plugins/workflow/showcases", bundle)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.name", "Showcase Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "-qm", "fixture"], check=True
    )
    catalog_path = bundle / "catalog.yaml"
    data = catalog_path.read_bytes().replace(b"\r\n", b"\n")
    catalog_path.write_bytes(data.replace(b"\n", b"\r\n") + b"tampered: true\r\n")

    @contextmanager
    def installed_bundle(_explicit=None):
        yield bundle

    monkeypatch.setattr(showcase_module, "_bundle_path", installed_bundle)

    with pytest.raises(ShowcaseCatalogError, match="catalog digest mismatch"):
        showcase_module.load_showcase_catalog()

    assert catalog_path.read_bytes().endswith(b"tampered: true\r\n")


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
