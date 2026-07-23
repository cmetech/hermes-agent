"""Behavior contracts for the upstream-customization ledger checker."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
import yaml

from scripts.check_upstream_customizations import (
    classify_upstream_overlap,
    load_and_validate_manifest,
    validate_diff_coverage,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "core.py").write_text("class Owned:\n    pass\n")
    (repo / "test_core.py").write_text("def test_owned():\n    pass\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo


def _manifest(repo: Path, baseline: str) -> Path:
    path = repo / "ledger.yaml"
    path.write_text(yaml.safe_dump({
        "schema_version": 1,
        "feature": "test-feature",
        "upstream_changes": [{
            "id": "owned",
            "change_class": "agent-core-generic",
            "owner": "test-feature",
            "files": ["core.py"],
            "owned_symbols": ["Owned"],
            "tests": ["test_core.py"],
            "expected_commit_subject": "feat: owned",
            "upstream_candidate": True,
            "merge_guidance": "Reconcile behavior.",
            "removal_condition": "Remove after equivalent upstream support.",
            "last_verified_upstream": baseline,
        }],
    }, sort_keys=False))
    return path


def test_manifest_rejects_non_hex_and_paths_outside_repository(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    manifest = _manifest(repo, "not-a-sha")
    with pytest.raises(ValueError, match="40-hex"):
        load_and_validate_manifest(manifest, repo)

    data = yaml.safe_load(manifest.read_text())
    data["upstream_changes"][0]["last_verified_upstream"] = "a" * 40
    data["upstream_changes"][0]["files"] = ["../escape.py"]
    manifest.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="contained"):
        load_and_validate_manifest(manifest, repo, check_git=False)


def test_diff_coverage_detects_add_delete_and_rename(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    data = load_and_validate_manifest(manifest, repo)

    (repo / "unledgered.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feat: unledgered")
    with pytest.raises(ValueError, match="unledgered.py"):
        validate_diff_coverage(data, repo, f"{baseline}..HEAD")

    _git(repo, "mv", "core.py", "renamed.py")
    _git(repo, "commit", "-m", "rename", "-a")
    with pytest.raises(ValueError, match="renamed.py"):
        validate_diff_coverage(data, repo, "HEAD~1..HEAD")


def test_diff_coverage_requires_ledger_for_existing_plugin_files(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    plugin = repo / "plugins/kanban/dashboard/plugin_api.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("value = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "add upstream plugin")
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    data = load_and_validate_manifest(manifest, repo)

    plugin.write_text("value = 2\n")
    _git(repo, "commit", "-am", "change existing upstream plugin")

    with pytest.raises(ValueError, match="plugins/kanban/dashboard/plugin_api.py"):
        validate_diff_coverage(data, repo, f"{baseline}..HEAD")


def test_diff_coverage_ignores_new_additive_plugin_directory(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    data = load_and_validate_manifest(manifest, repo)
    plugin = repo / "plugins/new-feature/plugin.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("value = 1\n")
    _git(repo, "add", str(plugin.relative_to(repo)))
    _git(repo, "commit", "-m", "add feature plugin")

    validate_diff_coverage(data, repo, f"{baseline}..HEAD")


def test_overlap_classification_distinguishes_file_symbol_and_equivalent(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    entry = load_and_validate_manifest(manifest, repo)["upstream_changes"][0]

    (repo / "core.py").write_text("class Owned:\n    value = 1\n")
    _git(repo, "commit", "-am", "upstream owned symbol")
    assert classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")["classification"] == "owned_symbol"

    second = _git(repo, "rev-parse", "HEAD")
    (repo / "core.py").write_text("class Owned:\n    value = 1\n# unrelated\n")
    _git(repo, "commit", "-am", "same file only")
    assert classify_upstream_overlap(entry, repo, f"{second}..HEAD")["classification"] == "same_file"

    third = _git(repo, "rev-parse", "HEAD")
    (repo / "replacement.py").write_text("class Owned:\n    pass\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "equivalent public contract")
    assert classify_upstream_overlap(entry, repo, f"{third}..HEAD")["classification"] == "possible_upstream_equivalent"


def test_overlap_reporting_is_read_only_for_git_and_baseline(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    before_branch = _git(repo, "branch", "--show-current")
    before_text = manifest.read_text()
    before_head = _git(repo, "rev-parse", "HEAD")

    entry = load_and_validate_manifest(manifest, repo)["upstream_changes"][0]
    report = classify_upstream_overlap(entry, repo, f"{baseline}..HEAD")
    json.dumps(report)

    assert manifest.read_text() == before_text
    assert _git(repo, "branch", "--show-current") == before_branch
    assert _git(repo, "rev-parse", "HEAD") == before_head


def test_diff_coverage_enforces_expected_commit_boundary(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    data = load_and_validate_manifest(manifest, repo)
    (repo / "core.py").write_text("class Owned:\n    value = 1\n")
    _git(repo, "commit", "-am", "wrong subject")

    with pytest.raises(ValueError, match="expected commit subject"):
        validate_diff_coverage(data, repo, f"{baseline}..HEAD")

    (repo / "core.py").write_text("class Owned:\n    value = 2\n")
    _git(repo, "commit", "-am", "feat: owned")
    validate_diff_coverage(data, repo, f"{baseline}..HEAD")


def test_manifest_coverage_scope_excludes_pre_feature_and_named_release_commits(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    root = _git(repo, "rev-parse", "HEAD")
    (repo / "preexisting_fork.py").write_text("fork = True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "pre-existing fork customization")
    feature_base = _git(repo, "rev-parse", "HEAD")
    (repo / "release_only.py").write_text("version = 'alpha'\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "separate alpha release")
    excluded = _git(repo, "rev-parse", "HEAD")
    (repo / "core.py").write_text("class Owned:\n    value = 1\n")
    _git(repo, "commit", "-am", "feat: owned")

    manifest = _manifest(repo, root)
    raw = yaml.safe_load(manifest.read_text())
    raw["coverage"] = {
        "base_commit": feature_base,
        "excluded_commits": [
            {"commit": excluded, "reason": "separate user-requested alpha release"}
        ],
    }
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))
    data = load_and_validate_manifest(manifest, repo)

    validate_diff_coverage(data, repo, f"{root}..HEAD")

    (repo / "unledgered_feature.py").write_text("value = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feature scope leak")
    with pytest.raises(ValueError, match="unledgered_feature.py"):
        validate_diff_coverage(data, repo, f"{root}..HEAD")


def test_manifest_coverage_ignores_only_local_sdd_progress_ledger(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    baseline = _git(repo, "rev-parse", "HEAD")
    manifest = _manifest(repo, baseline)
    raw = yaml.safe_load(manifest.read_text())
    raw["coverage"] = {"base_commit": baseline, "excluded_commits": []}
    manifest.write_text(yaml.safe_dump(raw, sort_keys=False))

    progress = repo / ".superpowers/sdd/progress.md"
    progress.parent.mkdir(parents=True)
    progress.write_text("local progress\n")
    _git(repo, "add", str(progress.relative_to(repo)))
    _git(repo, "commit", "-m", "accidentally track local progress")
    progress.unlink()
    _git(repo, "commit", "-am", "untrack local progress")

    data = load_and_validate_manifest(manifest, repo)
    validate_diff_coverage(data, repo, f"{baseline}..HEAD")

    adjacent = repo / ".superpowers/sdd/unregistered.md"
    adjacent.write_text("must remain covered\n")
    _git(repo, "add", str(adjacent.relative_to(repo)))
    _git(repo, "commit", "-m", "add unregistered sdd artifact")
    with pytest.raises(ValueError, match=r"\.superpowers/sdd/unregistered\.md"):
        validate_diff_coverage(data, repo, f"{baseline}..HEAD")
