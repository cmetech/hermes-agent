from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cron.jobs import create_job, list_jobs
from plugins.workflow.showcase import (
    preflight_showcase,
    reset_showcase,
    run_showcase,
)


def test_scheduling_requires_consent_and_preserves_unrelated_jobs(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    unrelated = create_job(
        prompt="unrelated user job",
        schedule=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        skills=["workflow"],
    )
    preflight = preflight_showcase("scheduling", hermes_home=tmp_path)
    run_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    refused = run_showcase(
        "scheduling", hermes_home=tmp_path, schedule_at=run_at
    )
    assert refused["reason_code"] == "schedule_confirmation_required"
    scheduled = run_showcase(
        "scheduling",
        hermes_home=tmp_path,
        schedule_at=run_at,
        confirmation_token=preflight["confirmation_token"],
    )
    assert scheduled["repeat"] == {"times": 1, "completed": 0}
    reset = reset_showcase("scheduling", hermes_home=tmp_path)
    assert reset["owned_schedule"]["id"] == scheduled["schedule_id"]
    assert reset["removed"] is False
    assert any(job["id"] == unrelated["id"] for job in list_jobs())
