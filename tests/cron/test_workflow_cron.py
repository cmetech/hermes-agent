from datetime import datetime, timezone

from cron.jobs import create_job
from plugins.workflow.cli import workflow_trigger_idempotency_key


def test_one_shot_workflow_cron_uses_skill_and_finite_repeat(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    job = create_job(
        prompt="Run the demo with immutable input manifest abc123",
        schedule="2030-01-15T14:00:00+00:00",
        skills=["workflow"],
    )
    assert job["skills"] == ["workflow"]
    assert job["repeat"] == {"times": 1, "completed": 0}


def test_schedule_fire_idempotency_is_stable_and_instant_bound():
    fire = datetime(2030, 1, 15, 14, tzinfo=timezone.utc)
    key = workflow_trigger_idempotency_key("schedule-1", fire)
    assert key == workflow_trigger_idempotency_key("schedule-1", fire)
    assert key != workflow_trigger_idempotency_key("schedule-2", fire)
    assert key != workflow_trigger_idempotency_key("schedule-1", fire.replace(hour=15))
