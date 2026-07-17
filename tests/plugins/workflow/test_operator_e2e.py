from __future__ import annotations

from plugins.workflow.showcase import build_showcase_report, run_showcase
from plugins.workflow.store import RunStore


def test_operator_views_agree_on_wait_attention_identity_and_artifacts(tmp_path) -> None:
    started = run_showcase(
        "laptop-diagnostic", hermes_home=tmp_path, symptom="fictional operator check"
    )
    run_id = started["run_id"]
    store = RunStore(tmp_path)
    listed = next(item for item in store.list_runs() if item["run_id"] == run_id)
    status = store.get_run_status(run_id)
    report = build_showcase_report(run_id, hermes_home=tmp_path)

    assert listed["status"] == status["status"] == report.terminal_outcome == "paused"
    assert status["health"] == "user_wait"
    assert status["progress"]["kind"] == "graph"
    assert "eta" not in status and "completion_eta" not in status
    assert status["definition_digest"] == report.definition_digest
    assert status["run_metadata"]["showcase_id"] == report.showcase_id
    assert report.interactions[0]["type"] == "workflow_approval"
    assert all(item["verified"] for item in report.artifacts)
    assert status["next_actions"] == [
        "status",
        "events",
        "approve",
        "reject",
        "cancel",
    ]
