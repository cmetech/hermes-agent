from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import shutil

import yaml

from cron.jobs import create_job, list_jobs
import plugins.workflow.showcase as showcase_module
from plugins.workflow.compat import assess_compatibility
from plugins.workflow.schema import load_workflow
from plugins.workflow.showcase import (
    preflight_showcase,
    reset_showcase,
    run_showcase,
)
from plugins.workflow.store import RunStore
from plugins.workflow.trust import build_risk_summary


def test_run_showcase_prepares_admission_while_materialized_bundle_is_alive(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    """Real admission must finish every live package read before as_file cleanup."""
    template = tmp_path / "template"
    workflow = workflow_writer(
        template / "packages/materialized/workflows",
        name="materialized-admission",
        filename="workflow.yaml",
        nodes=[{"id": "execute", "bash": "true"}],
    )
    base = showcase_module.load_showcase_catalog()["resilience"]
    scenario = replace(
        base,
        id="materialized-admission",
        workflow_path="packages/materialized/workflows/workflow.yaml",
        package_digest=showcase_module._tree_digest(workflow.parent.parent),
        capability_claims=(),
    )
    materializations = []

    @contextmanager
    def materialized_bundle(_explicit=None):
        materialized = tmp_path / f"materialized-{len(materializations)}"
        shutil.copytree(template, materialized)
        materializations.append(materialized)
        try:
            yield materialized
        finally:
            shutil.rmtree(materialized)

    monkeypatch.setattr(
        showcase_module,
        "load_showcase_catalog",
        lambda: {scenario.id: scenario},
    )
    monkeypatch.setattr(showcase_module, "_bundle_path", materialized_bundle)
    monkeypatch.setattr(
        showcase_module,
        "preflight_showcase",
        lambda *_args, **_kwargs: {"bundle_digest": "b" * 64},
    )

    started = run_showcase(
        scenario.id,
        hermes_home=tmp_path / "home",
        no_wait=True,
        idempotency_key="materialized-admission",
    )

    assert started["run_id"]
    assert materializations
    assert all(not materialized.exists() for materialized in materializations)
    assert (
        RunStore(tmp_path / "home")
        .run_directory(str(started["run_id"]))
        .joinpath("definition.yaml")
        .is_file()
    )


def test_scheduling_requires_confirmation_and_preserves_unrelated_jobs(
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

    expected_token = hashlib.sha256(
        (
            f"schedule\0{preflight['package_digest']}\0"
            f"{preflight['bundle_digest']}"
        ).encode()
    ).hexdigest()
    assert preflight["requires_confirmation"] is True
    assert preflight["confirmation_kind"] == "schedule"
    assert preflight["confirmation_token"] == expected_token

    refused = run_showcase(
        "scheduling", hermes_home=tmp_path, schedule_at=run_at
    )
    assert refused == {
        "status": "skipped",
        "reason_code": "schedule_confirmation_required",
        "run_id": None,
        "confirmation_token": expected_token,
    }
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


def test_showcase_admission_seals_resolved_profile_execution_authority(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "profile"
    path = workflow_writer(
        tmp_path / "package/workflows",
        name="archon-sealed-showcase-limits",
        nodes=[{"id": "start", "bash": "true"}],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump({
            "plugins": {
                "entries": {
                    "workflow": {
                        "runtime": {
                            "ai_idle_timeout_seconds": 120,
                            "ai_wall_timeout_seconds": 240,
                            "provider_request_timeout_seconds": 90,
                            "subprocess_timeout_seconds": 30,
                            "combined_retries": 2,
                        }
                    }
                }
            }
        }),
        encoding="utf-8",
    )
    package = load_workflow(path)
    risk = build_risk_summary(package, assess_compatibility(package))
    base_scenario = showcase_module.load_showcase_catalog()["resilience"]
    scenario = replace(
        base_scenario,
        id="archon-sealed-showcase-limits",
        display_name="Archon sealed showcase limits",
        package_digest=risk.package_digest,
        capability_claims=(),
    )
    monkeypatch.setattr(
        showcase_module,
        "load_showcase_catalog",
        lambda: {scenario.id: scenario},
    )
    monkeypatch.setattr(
        showcase_module,
        "_scenario_package",
        lambda _scenario, **_kwargs: package,
    )
    monkeypatch.setattr(
        showcase_module,
        "preflight_showcase",
        lambda *_args, **_kwargs: {"bundle_digest": "b" * 64},
    )
    monkeypatch.setattr(
        showcase_module,
        "_verified_distribution_risk",
        lambda *_args, **_kwargs: risk,
    )

    started = run_showcase(scenario.id, hermes_home=home)
    resources = json.loads(
        (
            RunStore(home).run_directory(str(started["run_id"])) / "resources.json"
        ).read_bytes()
    )

    assert resources["phase3_execution_semantics"]["limits"] == {
        "ai_idle_timeout_seconds": 120.0,
        "ai_wall_timeout_seconds": 240.0,
        "provider_request_timeout_seconds": 90.0,
        "subprocess_timeout_seconds": 30.0,
        "combined_total_attempts": 2,
    }
