from __future__ import annotations

from plugins.workflow.showcase import approve_showcase, build_showcase_report, run_showcase
from plugins.workflow.store import RunStore


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


def test_operator_approval_claim_uses_durable_approval_evidence(tmp_path) -> None:
    started = run_showcase("approval-gate", hermes_home=tmp_path)
    assert started["status"] == "paused"

    paused = build_showcase_report(started["run_id"], hermes_home=tmp_path)
    paused_claim = next(
        claim for claim in paused.claims if claim.capability == "operator-approval"
    )
    assert paused_claim.outcome == "skipped"
    assert paused_claim.reason_code == "awaiting_operator_decision"

    completed = approve_showcase(started["run_id"], hermes_home=tmp_path)
    assert completed["status"] == "succeeded"
    report = build_showcase_report(started["run_id"], hermes_home=tmp_path)
    claim = next(
        item for item in report.claims if item.capability == "operator-approval"
    )
    assert claim.outcome == "passed"
    assert claim.reason_code == "operator_approval_observed"
    assert claim.evidence_refs


def test_cleanup_summary_fails_closed_on_unreaped_identity_and_owned_staging(
    tmp_path,
) -> None:
    started = run_showcase(
        "resilience",
        hermes_home=tmp_path,
        symptom="timeout",
        no_wait=False,
    )
    store = RunStore(tmp_path)
    reaped = next(
        event
        for event in reversed(store.tail_events(started["run_id"], limit=200))
        if event["event_type"] == "process_reaped"
    )
    store.append_event(
        started["run_id"],
        "cleanup_failed",
        {"pid": reaped["payload"]["pid"], "cleanup_complete": False},
        node_id=reaped["node_id"],
        attempt_id=reaped["attempt_id"],
    )
    owned_staging = tmp_path / "workflow/showcase/staging/incomplete"
    owned_staging.mkdir(parents=True)
    (owned_staging / "owner.json").write_text('{"owner":"workflow-showcase"}')

    report = build_showcase_report(started["run_id"], hermes_home=tmp_path)
    cleanup_claim = next(
        claim for claim in report.claims if claim.capability == "process-cleanup"
    )

    assert cleanup_claim.outcome == "failed"
    assert cleanup_claim.reason_code == "owned_process_cleanup_unproven"
    assert report.cleanup["owned_processes_live"] == 1
    assert report.cleanup["staging_present"] is True
