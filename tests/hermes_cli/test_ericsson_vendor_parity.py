from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
IGNORED_TREE_NAMES = {"__pycache__", ".git", ".pytest_cache", ".venv"}
HERMES_COMPATIBILITY_SKILLS = {"skills/ericsson/confluence-research"}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"},
    ).stdout.strip()


def _managed_files(root: Path) -> dict[str, bytes]:
    if root.is_file():
        return {"": root.read_bytes()}
    assert root.is_dir(), f"managed path is missing: {root}"
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in IGNORED_TREE_NAMES for part in relative.parts):
            continue
        assert not path.is_symlink(), f"managed path is a symlink: {path}"
        if path.is_file():
            result[relative.as_posix()] = path.read_bytes()
    return result


def _source_destination_pairs(source: Path, manifest: dict) -> dict[str, str]:
    pairs = {relative: relative for relative in manifest.get("skills", [])}
    for entry in manifest.get("plugins", []):
        relative = entry if isinstance(entry, str) else entry["path"]
        if relative != "plugins/workflow":
            pairs[relative] = relative
    for relative in manifest.get("mcpLocal", []):
        pairs[f"plugins/{Path(relative).name}"] = relative
    for relative in manifest.get("workflows", []):
        destination = f"capabilities/workflows/{Path(relative).name}"
        pairs[destination] = relative
        sidecar = str(Path(relative).with_suffix(".hermes.yaml"))
        if (source / sidecar).exists():
            pairs[f"capabilities/workflows/{Path(sidecar).name}"] = sidecar
    for entry in manifest.get("workflowPackages", []):
        pairs[entry["path"]] = entry["path"]
    if relative := manifest.get("mcpServers"):
        pairs[f"capabilities/{Path(relative).name}"] = relative
    return pairs


def test_vendored_ericsson_snapshot_matches_exact_source_authority() -> None:
    source_value = os.environ.get("ERICSSON_CAPABILITIES_DIR")
    expected_sha = os.environ.get("ERICSSON_CAPABILITIES_EXPECTED_SHA")
    assert source_value, "ERICSSON_CAPABILITIES_DIR is required"
    assert expected_sha, "ERICSSON_CAPABILITIES_EXPECTED_SHA is required"
    runner = (REPO / "scripts/run_tests.sh").read_text(encoding="utf-8")
    for variable in (
        "ERICSSON_CAPABILITIES_DIR",
        "ERICSSON_CAPABILITIES_EXPECTED_SHA",
    ):
        assert variable in runner, f"scripts/run_tests.sh must allow {variable}"

    source = Path(source_value).resolve()
    assert source.is_dir(), f"Ericsson source directory is missing: {source}"
    assert len(expected_sha) == 40 and all(
        c in "0123456789abcdef" for c in expected_sha
    )
    assert Path(_git(source, "rev-parse", "--show-toplevel")).resolve() == source
    assert _git(source, "rev-parse", "HEAD^{commit}") == expected_sha
    assert _git(source, "status", "--porcelain=v1", "--untracked-files=all") == ""

    source_manifest = json.loads((source / "sets/ericsson.json").read_text())
    vendored_manifest = json.loads((REPO / "capabilities/ericsson.json").read_text())
    ledger = json.loads(
        (REPO / "capabilities/ericsson-vendored-paths.json").read_text()
    )
    pairs = _source_destination_pairs(source, source_manifest)
    assert ledger == sorted(pairs), "vendored managed-path inventory differs from source"

    for destination, source_relative in sorted(pairs.items()):
        source_files = _managed_files(source / source_relative)
        vendored_files = _managed_files(REPO / destination)
        assert vendored_files.keys() == source_files.keys(), (
            f"managed file inventory differs: {destination}"
        )
        for relative, expected in source_files.items():
            assert vendored_files[relative] == expected, (
                f"managed bytes differ: {destination}/{relative}".rstrip("/")
            )

    expected_manifest = dict(source_manifest)
    source_skills = set(source_manifest.get("skills", []))
    compatibility_skills = set(vendored_manifest.get("skills", [])) - source_skills
    assert compatibility_skills == HERMES_COMPATIBILITY_SKILLS
    assert all(
        relative not in ledger and (REPO / relative).is_dir()
        for relative in compatibility_skills
    )
    expected_manifest["skills"] = [
        *source_manifest.get("skills", []),
        *sorted(compatibility_skills),
    ]
    if "configDefaults" not in source_manifest and "configDefaults" in vendored_manifest:
        expected_manifest["configDefaults"] = vendored_manifest["configDefaults"]
    expected_manifest["vendoredFrom"] = expected_sha
    expected_manifest["workflowPackages"] = source_manifest.get("workflowPackages", [])
    if mcp_servers := source_manifest.get("mcpServers"):
        expected_manifest["mcpServersFile"] = Path(mcp_servers).name
    assert vendored_manifest == expected_manifest

    package = source / "capabilities/workflow-packages/ericsson"
    source_digests = json.loads((package / "digests.json").read_text())
    vendored_digests = json.loads(
        (REPO / "capabilities/workflow-packages/ericsson/digests.json").read_text()
    )
    workflow_names = {
        path.stem
        for path in (package / "workflows").glob("*.yaml")
        if not path.name.endswith(".hermes.yaml")
    }
    assert source_digests == vendored_digests
    assert set(source_digests["packages"]) == workflow_names
    assert all(
        len(value) == 64 and all(c in "0123456789abcdef" for c in value)
        for value in source_digests["packages"].values()
    )
