from __future__ import annotations

from plugins.workflow.showcase import build_showcase_report, run_showcase


def test_report_claims_are_derived_from_durable_evidence(tmp_path) -> None:
    started = run_showcase(
        "laptop-diagnostic",
        hermes_home=tmp_path,
        symptom="fictional fan noise",
        no_wait=False,
    )
    assert started["status"] == "paused"

    report = build_showcase_report(started["run_id"], hermes_home=tmp_path)
    claims = {claim.capability: claim for claim in report.claims}

    assert claims["immutable-inputs"].outcome == "passed"
    assert claims["parallel-fan-in"].outcome == "passed"
    assert claims["approval-rework"].outcome == "skipped"
    assert all(claim.evidence_refs for claim in claims.values())
    assert report.interactions[0]["type"] == "workflow_approval"
    assert all("source_path" not in str(item) for item in report.artifacts)


def test_missing_catalog_evidence_fails_instead_of_declaring_pass(tmp_path) -> None:
    started = run_showcase(
        "laptop-diagnostic",
        hermes_home=tmp_path,
        symptom="fictional symptom",
        no_wait=False,
    )
    report = build_showcase_report(started["run_id"], hermes_home=tmp_path)

    assert any(
        claim.outcome != "passed" and claim.reason_code
        for claim in report.claims
    )
