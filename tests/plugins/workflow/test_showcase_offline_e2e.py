from __future__ import annotations

import json

import pytest

from plugins.workflow.showcase import (
    ShowcaseCatalogError,
    approve_showcase,
    build_showcase_report,
    reject_showcase,
    run_showcase,
)
from plugins.workflow.trust import WorkflowTrustStore


def test_direct_no_wait_showcase_start_requires_idempotency_key(tmp_path) -> None:
    with pytest.raises(ShowcaseCatalogError, match="idempotency key"):
        run_showcase(
            "laptop-diagnostic",
            hermes_home=tmp_path,
            symptom="fictional slow startup",
            no_wait=True,
        )


def test_laptop_tour_snapshots_fictional_input_reworks_and_approves_offline(
    tmp_path,
) -> None:
    missing = run_showcase("laptop-diagnostic", hermes_home=tmp_path)
    assert missing["reason_code"] == "showcase_input_required"
    assert missing["run_id"] is None

    trust_store = WorkflowTrustStore(tmp_path)
    trust_store.trust("a" * 64, actor="existing-operator", risk_digest="b" * 64)
    trust_bytes_before = trust_store.path.read_bytes()

    started = run_showcase(
        "laptop-diagnostic",
        hermes_home=tmp_path,
        symptom="fictional slow startup",
    )
    assert started["status"] == "paused"
    run_id = started["run_id"]
    manifest = json.loads(
        (tmp_path / "workflows/runs/laptop-diagnostic" / run_id / "inputs.json").read_text()
    )
    assert {"arguments", "evidence"} <= manifest.keys()
    assert "laptop-snapshot.json" not in manifest["evidence"]["relative_path"]

    reworked = reject_showcase(
        run_id,
        "make the fictional remediation more cautious",
        hermes_home=tmp_path,
    )
    assert reworked["status"] == "paused"
    assert reworked["nodes"]["review-plan"]["approval_rework_attempts"] == 1

    completed = approve_showcase(run_id, hermes_home=tmp_path)
    assert completed["status"] == "succeeded"
    report = build_showcase_report(run_id, hermes_home=tmp_path)
    assert report.terminal_outcome == "succeeded"
    names = {artifact["name"] for artifact in report.artifacts}
    assert {
        "diagnostic-report.json",
        "diagnostic-report.md",
        "remediation-plan.md",
    } <= names
    report_json = next(
        item for item in report.artifacts if item["name"] == "diagnostic-report.json"
    )
    assert report_json["verified"] is True
    assert trust_store.path.read_bytes() == trust_bytes_before
