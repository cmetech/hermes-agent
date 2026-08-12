"""Cross-surface contracts for recurring Ericsson GitLab activity digests."""

from __future__ import annotations

import json
import shutil

import yaml

from tests.ericsson_connector_source import resolve_ericsson_connector_source


def test_qualified_digest_skill_builds_a_safe_self_contained_future_prompt(
    tmp_path, monkeypatch
):
    import cron.scheduler as scheduler
    from hermes_cli import plugins as plugins_module
    from hermes_cli.plugins import PluginManager

    source = resolve_ericsson_connector_source()
    home = tmp_path / "profile"
    home.mkdir()
    shutil.copytree(source.plugin, home / "plugins" / "ericsson-gitlab")
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {"plugins": {"enabled": ["ericsson-gitlab"], "disabled": []}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(PluginManager, "_scan_entry_points", lambda self: [])

    manager = PluginManager()
    manager.discover_and_load()
    monkeypatch.setattr(plugins_module, "_plugin_manager", manager)

    job = {
        "id": "daily-gitlab",
        "name": "Daily GitLab activity",
        "prompt": (
            "For sd-macs-att-rnam-hosting/oscar_app/eventmesh, summarize commits "
            "and merge requests created in the rolling last 24 hours. Include "
            "canonical links. If both collections are empty, return [SILENT]."
        ),
        "skills": ["ericsson-gitlab:gitlab-activity-digest"],
        "enabled_toolsets": ["skills", "ericsson-gitlab"],
    }

    effective_prompt = scheduler._build_job_prompt(job)

    assert "ericsson-gitlab:gitlab-activity-digest" in effective_prompt
    assert "gitlab_list_commits" in effective_prompt
    assert "gitlab_list_merge_requests" in effective_prompt
    assert "lookback_hours=24" in effective_prompt
    assert "Do not call `cronjob`" in effective_prompt
    assert "return exactly `[SILENT]`" in effective_prompt
    assert "failed run, not an empty digest" in effective_prompt
    assert scheduler._resolve_cron_enabled_toolsets(job, {}) == [
        "skills",
        "ericsson-gitlab",
    ]

    serialized = json.dumps({"prompt": effective_prompt, "job": job})
    assert "PRIVATE-TOKEN" not in serialized
    assert "BEGIN PRIVATE KEY" not in serialized
    assert "client-key.pem" not in serialized
