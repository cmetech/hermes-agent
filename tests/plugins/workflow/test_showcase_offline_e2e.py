from __future__ import annotations

import json

from plugins.workflow.showcase import (
    approve_showcase,
    build_showcase_report,
    reject_showcase,
    run_showcase,
)


def test_laptop_tour_snapshots_fictional_input_reworks_and_approves_offline(
    tmp_path,
) -> None:
    missing = run_showcase("laptop-diagnostic", hermes_home=tmp_path)
    assert missing["reason_code"] == "showcase_input_required"
    assert missing["run_id"] is None

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
