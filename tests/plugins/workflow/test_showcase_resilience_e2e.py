from __future__ import annotations

from plugins.workflow.showcase import build_showcase_report, run_showcase


def test_resilience_retry_fails_once_persists_backoff_then_succeeds(tmp_path) -> None:
    result = run_showcase(
        "resilience", hermes_home=tmp_path, symptom="retry", no_wait=False
    )

    assert result["status"] == "succeeded"
    assert len(result["nodes"]["retry"]["attempts"]) == 2
    report = build_showcase_report(result["run_id"], hermes_home=tmp_path)
    assert next(
        claim for claim in report.claims if claim.capability == "persisted-retry"
    ).outcome == "passed"


def test_resilience_timeout_is_truthfully_failed_and_reaped(tmp_path) -> None:
    result = run_showcase(
        "resilience", hermes_home=tmp_path, symptom="timeout", no_wait=False
    )

    assert result["status"] == "failed"
    assert result["last_error"]["code"] == "timeout"
    events = (tmp_path / "workflows/runs/resilience" / result["run_id"] / "events.jsonl").read_text()
    assert "process_reaped" in events
    report = build_showcase_report(result["run_id"], hermes_home=tmp_path)
    assert next(
        claim for claim in report.claims if claim.capability == "typed-timeout"
    ).outcome == "passed"
