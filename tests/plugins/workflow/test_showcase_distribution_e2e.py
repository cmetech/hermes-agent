from __future__ import annotations

import zipfile
from pathlib import Path


def test_packaging_metadata_includes_every_showcase_resource() -> None:
    root = Path(__file__).resolve().parents[3]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    manifest = (root / "MANIFEST.in").read_text(encoding="utf-8")

    assert "workflow/showcases/**/*" in pyproject
    assert "recursive-include plugins/workflow/showcases *" in manifest


def test_built_wheel_contains_catalog_packages_and_fixture(tmp_path) -> None:
    root = Path(__file__).resolve().parents[3]
    wheels = sorted((root / "dist").glob("*.whl"))
    if not wheels:
        return
    with zipfile.ZipFile(wheels[-1]) as archive:
        names = set(archive.namelist())
    assert any(name.endswith("workflow/showcases/catalog.yaml") for name in names)
    assert any(name.endswith("fixtures/laptop-snapshot.json") for name in names)
    assert any(name.endswith("mcp/echo-server.py") for name in names)
    assert any(name.endswith("skills/productivity/workflow-showcase/SKILL.md") for name in names)
    assert any(name.endswith("workflow-showcase/workflows/run-showcase.md") for name in names)
