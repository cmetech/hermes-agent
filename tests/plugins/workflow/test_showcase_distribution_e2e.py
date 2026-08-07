from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path


def test_packaging_metadata_includes_every_showcase_resource() -> None:
    root = Path(__file__).resolve().parents[3]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    manifest = (root / "MANIFEST.in").read_text(encoding="utf-8")

    assert "workflow/showcases/**/*" in pyproject
    assert "recursive-include plugins/workflow/showcases *" in manifest


def test_built_wheel_contains_phase4_modules_contract_references_and_showcases(
    tmp_path,
) -> None:
    root = Path(__file__).resolve().parents[3]
    artifacts = tmp_path / "artifacts"
    generated_paths = (root / "build", root / "hermes_agent.egg-info")
    preexisting = {path for path in generated_paths if path.exists()}
    build_env = os.environ.copy()
    build_env["HERMES_NIX_BUILD"] = "1"
    try:
        built = subprocess.run(
            [
                "uv",
                "build",
                "--wheel",
                "--no-build-logs",
                "--out-dir",
                str(artifacts),
                ".",
            ],
            cwd=root,
            env=build_env,
            capture_output=True,
            text=True,
            timeout=600,
        )
    finally:
        for path in generated_paths:
            if path not in preexisting:
                shutil.rmtree(path, ignore_errors=True)
    assert built.returncode == 0, built.stderr
    wheels = list(artifacts.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
    for module in (
        "plugins/workflow/includes.py",
        "plugins/workflow/dependency_manifest.py",
        "plugins/workflow/executors/loop.py",
    ):
        assert any(name.endswith(module) for name in names)
    for reference in (
        "workflow-builder/references/portable-schema.md",
        "workflow-builder/references/authoring-checklist.md",
    ):
        assert any(name.endswith(reference) for name in names)
    assert any(name.endswith("workflow/showcases/catalog.yaml") for name in names)
    assert any(name.endswith("fixtures/laptop-snapshot.json") for name in names)
    assert any(name.endswith("mcp/echo-server.py") for name in names)
    assert any(name.endswith("skills/productivity/workflow-showcase/SKILL.md") for name in names)
    assert any(name.endswith("workflow-showcase/workflows/run-showcase.md") for name in names)
    assert any(name.endswith("capabilities/ericsson.json") for name in names)
    assert any(name.endswith("capabilities/mcp-servers.yaml") for name in names)
    assert any(name.endswith("capabilities/workflow-packages/ericsson/digests.json") for name in names)
