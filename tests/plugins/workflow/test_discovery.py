from __future__ import annotations

import os

import pytest

from plugins.workflow.discovery import clear_discovery_cache, discover_workflows
from plugins.workflow.models import WorkflowValidationError


def test_project_overrides_profile_and_results_are_sorted(workflow_writer, tmp_path):
    workdir = tmp_path / "repo"
    hermes_home = tmp_path / "profile"
    user_home = tmp_path / "home"
    workflow_writer(
        hermes_home / "workflows" / "nested", name="shared", description="profile"
    )
    workflow_writer(hermes_home / "workflows", name="zulu", filename="zulu.yaml")
    workflow_writer(
        workdir / ".hermes" / "workflows", name="shared", description="project"
    )
    workflow_writer(
        workdir / ".hermes" / "workflows" / "nested", name="alpha", filename="alpha.yml"
    )

    packages = discover_workflows(workdir, hermes_home, user_home)

    assert [package.definition.name for package in packages] == [
        "alpha",
        "shared",
        "zulu",
    ]
    shared = next(
        package for package in packages if package.definition.name == "shared"
    )
    assert shared.definition.description == "project"
    assert shared.source == "project"
    assert shared.precedence == 1
    assert not (user_home / ".archon").exists()


def test_explicit_package_has_highest_precedence(workflow_writer, tmp_path):
    workdir = tmp_path / "repo"
    hermes_home = tmp_path / "profile"
    explicit = workflow_writer(
        tmp_path / "external", name="shared", description="explicit"
    )
    workflow_writer(
        workdir / ".hermes" / "workflows", name="shared", description="project"
    )

    packages = discover_workflows(
        workdir, hermes_home, tmp_path / "home", explicit_path=explicit
    )

    assert len(packages) == 1
    assert packages[0].definition.description == "explicit"
    assert packages[0].source == "explicit"
    assert packages[0].precedence == 0


def test_explicit_package_root_scans_only_portable_workflows(workflow_writer, tmp_path):
    root = tmp_path / "external-package"
    workflow_writer(root / "workflows", name="portable", filename="portable.yaml")
    (root / "mcp").mkdir()
    (root / "mcp" / "echo.yaml").write_text("command: python\n", encoding="utf-8")

    packages = discover_workflows(
        tmp_path / "repo",
        tmp_path / "profile",
        tmp_path / "home",
        explicit_path=root,
    )

    assert [package.definition.name for package in packages] == ["portable"]


def test_recursive_workflow_keeps_neutral_package_root(workflow_writer, tmp_path):
    root = tmp_path / "repo" / ".hermes"
    workflow_writer(root / "workflows" / "nested", name="portable")

    package = discover_workflows(
        tmp_path / "repo", tmp_path / "profile", tmp_path / "home"
    )[0]

    assert package.root == root.resolve()


def test_same_level_duplicate_names_are_errors_with_logical_provenance(
    workflow_writer, tmp_path
):
    root = tmp_path / "repo" / ".hermes" / "workflows"
    workflow_writer(root / "one", name="duplicate")
    workflow_writer(root / "two", name="duplicate")

    with pytest.raises(WorkflowValidationError, match="duplicate workflow name") as exc:
        discover_workflows(tmp_path / "repo", tmp_path / "profile", tmp_path / "home")

    assert exc.value.issues[0].path == "workflows/two/example.yaml"


def test_lower_precedence_duplicate_names_are_errors_even_when_shadowed(
    workflow_writer, tmp_path
):
    workdir = tmp_path / "repo"
    hermes_home = tmp_path / "profile"
    workflow_writer(
        workdir / ".hermes" / "workflows", name="duplicate", description="winner"
    )
    workflow_writer(
        hermes_home / "workflows" / "one", name="duplicate", description="first"
    )
    workflow_writer(
        hermes_home / "workflows" / "two", name="duplicate", description="second"
    )

    with pytest.raises(WorkflowValidationError, match="duplicate workflow name") as exc:
        discover_workflows(workdir, hermes_home, tmp_path / "home")

    assert exc.value.issues[0].path == "workflows/two/example.yaml"
    assert "profile precedence" in exc.value.issues[0].message


def test_explicit_path_overlapping_project_catalog_keeps_explicit_provenance(
    workflow_writer, tmp_path
):
    workdir = tmp_path / "repo"
    workflow_path = workflow_writer(
        workdir / ".hermes" / "workflows",
        name="shared",
        description="same physical definition",
    )

    package = discover_workflows(
        workdir,
        tmp_path / "profile",
        tmp_path / "home",
        explicit_path=workflow_path,
    )[0]

    assert package.source == "explicit"
    assert package.precedence == 0


def test_profile_catalog_ignores_workflow_owned_runtime_directories(
    workflow_writer, tmp_path
) -> None:
    profile = tmp_path / "profile"
    catalog = profile / "workflows"
    workflow_writer(catalog, name="durable", filename="durable.yaml")
    workflow_writer(catalog / "nested-package", name="nested", filename="nested.yaml")
    for owned in ("runs", ".staging", ".quarantine", ".locks"):
        workflow_writer(
            catalog / owned / "durable" / "snapshot",
            name="durable",
            filename="definition.yaml",
        )

    packages = discover_workflows(
        tmp_path / "repo",
        profile,
        tmp_path / "home",
    )

    assert [package.definition.name for package in packages] == ["durable", "nested"]
    assert all("snapshot" not in str(package.workflow_path) for package in packages)


def test_successful_parse_cache_invalidates_on_content_change(
    workflow_writer, tmp_path
):
    clear_discovery_cache()
    workdir = tmp_path / "repo"
    path = workflow_writer(
        workdir / ".hermes" / "workflows", name="cached", description="first"
    )
    first = discover_workflows(workdir, tmp_path / "profile", tmp_path / "home")[0]
    path.write_text(
        path.read_text(encoding="utf-8").replace("first", "other"), encoding="utf-8"
    )
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns))

    second = discover_workflows(workdir, tmp_path / "profile", tmp_path / "home")[0]

    assert first.definition.description == "first"
    assert second.definition.description == "other"


def test_parse_cache_invalidates_when_companion_is_created_edited_and_deleted(
    workflow_writer, tmp_path
):
    clear_discovery_cache()
    workdir = tmp_path / "repo"
    path = workflow_writer(workdir / ".hermes" / "workflows", name="cached")
    companion = path.with_name(f"{path.stem}.hermes.yaml")

    absent = discover_workflows(
        workdir, tmp_path / "profile", tmp_path / "home"
    )[0]
    companion.write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    created = discover_workflows(
        workdir, tmp_path / "profile", tmp_path / "home"
    )[0]
    created_stat = companion.stat()
    companion.write_text(
        "language_compatibility: hermes-legacy \n", encoding="utf-8"
    )
    os.utime(
        companion,
        ns=(created_stat.st_atime_ns, created_stat.st_mtime_ns),
    )
    edited = discover_workflows(
        workdir, tmp_path / "profile", tmp_path / "home"
    )[0]
    companion.unlink()
    deleted = discover_workflows(
        workdir, tmp_path / "profile", tmp_path / "home"
    )[0]

    assert absent.language.declared_profile is None
    assert created.language.effective_profile.value == "archon-2026-07"
    assert edited.language.declared_profile.value == "hermes-legacy"
    assert deleted.language.declared_profile is None
