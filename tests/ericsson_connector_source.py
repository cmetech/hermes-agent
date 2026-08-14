"""Shared, explicit source authority for pre-vendor connector tests."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess

import pytest


SOURCE_DIR_ENV = "ERICSSON_CAPABILITIES_DIR"
SOURCE_SHA_ENV = "ERICSSON_CAPABILITIES_TEST_EXPECTED_SHA"
_FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True, slots=True)
class EricssonConnectorSource:
    root: Path
    revision: str
    plugin: Path
    router_skill: Path
    workflow: Path
    workflow_sidecar: Path
    capability_manifest: Path
    vendored: bool


def _required_source(
    root: Path,
    *,
    revision: str,
    manifest: Path,
    vendored: bool,
) -> EricssonConnectorSource | None:
    workflow_root = (
        root / "capabilities" / "workflow-packages" / "ericsson" / "workflows"
        if vendored
        else root / "workflows"
    )
    workflow = next(
        (
            workflow_root / f"jira-to-gitlab{suffix}"
            for suffix in (".yml", ".yaml")
            if (workflow_root / f"jira-to-gitlab{suffix}").is_file()
        ),
        workflow_root / "jira-to-gitlab.yml",
    )
    source = EricssonConnectorSource(
        root=root,
        revision=revision,
        plugin=root / "plugins" / "ericsson-gitlab",
        router_skill=root / "skills" / "ericsson" / "gitlab" / "SKILL.md",
        workflow=workflow,
        workflow_sidecar=workflow.with_name("jira-to-gitlab.hermes.yaml"),
        capability_manifest=manifest,
        vendored=vendored,
    )
    required = (
        source.plugin / "plugin.yaml",
        source.plugin / "config.schema.json",
        source.router_skill,
        source.workflow,
        source.workflow_sidecar,
        source.capability_manifest,
    )
    return source if all(path.is_file() for path in required) else None


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise pytest.UsageError("explicit Ericsson test source is not a Git repository")
    return completed.stdout.strip()


def resolve_ericsson_connector_source(
    *,
    repo_root: Path | None = None,
) -> EricssonConnectorSource:
    """Return the committed vendor or an explicitly pinned clean source checkout."""
    hermes_root = (
        repo_root.resolve()
        if repo_root is not None
        else Path(__file__).resolve().parents[1]
    )
    vendored_manifest = hermes_root / "capabilities" / "ericsson.json"
    if vendored_manifest.is_file():
        try:
            manifest = json.loads(vendored_manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise pytest.UsageError(
                "vendored Ericsson capability manifest is invalid"
            ) from exc
        revision = manifest.get("vendoredFrom") if isinstance(manifest, dict) else None
        vendored = _required_source(
            hermes_root,
            revision=revision if isinstance(revision, str) else "",
            manifest=vendored_manifest,
            vendored=True,
        )
        if vendored is not None:
            if not isinstance(revision, str) or _FULL_SHA.fullmatch(revision) is None:
                raise pytest.UsageError("vendoredFrom must be a full SHA")
            return vendored

    configured_root = os.environ.get(SOURCE_DIR_ENV)
    configured_sha = os.environ.get(SOURCE_SHA_ENV)
    if not configured_root and not configured_sha:
        pytest.skip(
            "pre-vendor Task 12 requires ERICSSON_CAPABILITIES_DIR and "
            "ERICSSON_CAPABILITIES_TEST_EXPECTED_SHA"
        )
    if not configured_root or not configured_sha:
        raise pytest.UsageError(
            "explicit Ericsson test source requires both directory and expected SHA"
        )
    if _FULL_SHA.fullmatch(configured_sha) is None:
        raise pytest.UsageError("expected Ericsson test revision must be a full SHA")

    source_root = Path(configured_root).expanduser().resolve()
    top_level = Path(_git(source_root, "rev-parse", "--show-toplevel")).resolve()
    if top_level != source_root:
        raise pytest.UsageError(
            "explicit Ericsson test source must be its repository root"
        )
    actual_sha = _git(source_root, "rev-parse", "HEAD")
    if actual_sha != configured_sha:
        raise pytest.UsageError("explicit Ericsson test source revision does not match")
    # The preserved worktree-local virtualenv is not source. Every other tracked
    # or untracked path must be clean so the asserted revision owns all bytes.
    dirty = _git(
        source_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        ".",
        ":(exclude).venv",
        ":(exclude).venv/**",
    )
    if dirty:
        raise pytest.UsageError("explicit Ericsson test source must be clean")
    source = _required_source(
        source_root,
        revision=actual_sha,
        manifest=source_root / "sets" / "ericsson.json",
        vendored=False,
    )
    if source is None:
        raise pytest.UsageError("explicit Ericsson test source is incomplete")
    return source
