from __future__ import annotations

from plugins.workflow.showcase import (
    approve_showcase,
    build_showcase_report,
    reject_showcase,
    run_showcase,
)
from plugins.workflow.scheduler import RunScheduler
from plugins.workflow.store import RunStore


def test_resilience_retry_fails_once_persists_backoff_then_succeeds(tmp_path) -> None:
    result = run_showcase(
        "resilience", hermes_home=tmp_path, symptom="retry", no_wait=False
    )

    assert result["status"] == "succeeded"
    assert len(result["nodes"]["retry"]["attempts"]) == 2
    run_directory = tmp_path / "workflows/runs/resilience" / result["run_id"]
    assert not (run_directory / ".showcase-failed-once").exists()
    assert (run_directory / "artifacts/.showcase-failed-once").read_text(
        encoding="utf-8"
    ) == "owned\n"
    legacy_state = run_directory / "legacy-showcase-state.txt"
    legacy_state.write_text("retained\n", encoding="utf-8")
    store = RunStore(tmp_path)
    scheduler = RunScheduler(store)
    try:
        loaded = scheduler._load_verified_run_package(result["run_id"])[0]
    finally:
        scheduler.shutdown(deadline_seconds=2)
    assert loaded.language.effective_profile.value == "hermes-legacy"
    assert legacy_state.read_text(encoding="utf-8") == "retained\n"
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


def test_showcase_decisions_submit_each_current_interaction_identity(tmp_path) -> None:
    started = run_showcase(
        "laptop-diagnostic", hermes_home=tmp_path, symptom="identity exercise"
    )
    first_id = started["pending_interaction"]["interaction_id"]

    reworked = reject_showcase(
        started["run_id"], "revise", hermes_home=tmp_path
    )
    second_id = reworked["pending_interaction"]["interaction_id"]
    assert second_id != first_id

    completed = approve_showcase(started["run_id"], hermes_home=tmp_path)

    assert completed["status"] == "succeeded"
    decisions = [
        event
        for event in RunStore(tmp_path).tail_events(started["run_id"])
        if event["event_type"] in {"interaction_rejected", "interaction_approved"}
    ]
    assert [event["payload"]["interaction_id"] for event in decisions] == [
        first_id,
        second_id,
    ]
