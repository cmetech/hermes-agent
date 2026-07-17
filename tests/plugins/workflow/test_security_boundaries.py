from __future__ import annotations

import json
import hashlib
from pathlib import Path
import shutil

import pytest

from plugins.workflow.showcase import ShowcaseCatalogError, load_showcase_catalog
from plugins.workflow.trust import WorkflowTrustStore, compute_package_digest
from plugins.workflow.schema import load_workflow


SHOWCASES = Path(__file__).parents[3] / "plugins/workflow/showcases"


@pytest.mark.parametrize(
    "relative",
    [
        "catalog.yaml",
        "digests.json",
        "packages/laptop-diagnostic/fixtures/laptop-snapshot.json",
        "packages/laptop-diagnostic/scripts/analyze-snapshot.py",
        "packages/laptop-diagnostic/commands/interpret-report.md",
        "packages/laptop-diagnostic/workflows/laptop-diagnostic.yaml",
        "packages/laptop-diagnostic/workflows/laptop-diagnostic.hermes.yaml",
        "packages/ai-extensions/mcp/echo.yaml",
        "packages/ai-extensions/mcp/echo-server.py",
    ],
)
def test_every_showcase_resource_class_fails_closed_when_tampered(
    tmp_path: Path, relative: str
) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(SHOWCASES, copied)
    target = copied / relative
    target.write_bytes(target.read_bytes() + b"\n# tampered\n")

    with pytest.raises(ShowcaseCatalogError):
        load_showcase_catalog(copied)


def test_distribution_identity_does_not_trust_a_changed_executable(tmp_path: Path) -> None:
    workflow = SHOWCASES / "packages/resilience/workflows/resilience.yaml"
    package = load_workflow(workflow)
    before = compute_package_digest(package).sha256
    store = WorkflowTrustStore(tmp_path)
    store.trust(before, actor="trusted_distribution", risk_digest=before)

    copied = tmp_path / "package"
    shutil.copytree(package.root, copied)
    changed = copied / "scripts/fail-once.py"
    changed.write_text(changed.read_text() + "\n# changed\n")
    after = compute_package_digest(load_workflow(copied / "workflows/resilience.yaml")).sha256

    assert after != before
    assert store.check(after, risk_digest=after) == "untrusted"
    records = json.loads(store.path.read_text())["records"]
    assert records[before]["actor"] == "trusted_distribution"


def test_even_digest_consistent_bundle_rejects_live_inventory_commands(
    tmp_path: Path,
) -> None:
    copied = tmp_path / "showcases"
    shutil.copytree(SHOWCASES, copied)
    script = copied / "packages/laptop-diagnostic/scripts/analyze-snapshot.py"
    script.write_text(script.read_text() + "\n# powershell Get-ComputerInfo\n")

    package = copied / "packages/laptop-diagnostic"
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            relative = path.relative_to(package).as_posix()
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(str(len(data)).encode())
            digest.update(b"\0")
            digest.update(data)
            digest.update(b"\0")
    manifest_path = copied / "digests.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["packages"]["laptop-diagnostic"] = digest.hexdigest()
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ShowcaseCatalogError, match="safety"):
        load_showcase_catalog(copied)
