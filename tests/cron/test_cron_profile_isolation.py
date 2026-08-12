"""Regression tests for #4707 — cron must be per-profile.

Design intent (Teknium, June 2026): a profile's cron jobs both LIVE in that
profile's HERMES_HOME and EXECUTE under it.

- Storage: a job created under profile ``coder`` writes to
  ``~/.hermes/profiles/coder/cron/jobs.json`` — NOT the shared default root.
- Execution: the profile-scoped gateway's in-process ticker resolves the
  active HERMES_HOME (profile home) at call time, so jobs run with that
  profile's ``.env`` / ``config.yaml`` / scripts / skills.

This is the opposite direction from the (reverted) #50112/#32091 "anchor at the
shared root" approach. Anchoring at the root funnels every profile's jobs into
one store and runs them under whatever HERMES_HOME the ticker happens to have —
leaking config/credentials/skills across profiles, the security boundary #4707
was filed for. These tests pin per-profile isolation so a stale-branch merge or
a re-anchor "fix" can't silently flip it back.
"""
import importlib
import shutil
from pathlib import Path

from tests.ericsson_connector_source import resolve_ericsson_connector_source


def _set_profile_env(monkeypatch, root: Path, profile_home: Path) -> None:
    """Pretend the platform default root is ``root`` and the active
    HERMES_HOME is a profile under it (``<root>/profiles/<name>``)."""
    import hermes_constants

    monkeypatch.setattr(
        hermes_constants, "_get_platform_default_hermes_home", lambda: root
    )
    monkeypatch.setenv("HERMES_HOME", str(profile_home))


def test_cron_storage_anchors_at_profile_home(tmp_path, monkeypatch):
    """Under a profile HERMES_HOME (<root>/profiles/<name>), the cron store
    resolves to <profile>/cron, NOT the shared <root>/cron."""
    root = tmp_path / "hermes_home"
    profile_home = root / "profiles" / "coder"
    profile_home.mkdir(parents=True)

    _set_profile_env(monkeypatch, root, profile_home)

    import hermes_constants

    # Sanity: the override is wired the way the gateway sees it.
    assert hermes_constants.get_hermes_home().resolve() == profile_home.resolve()
    assert hermes_constants.get_default_hermes_root().resolve() == root.resolve()

    # cron/jobs.py computes HERMES_DIR from get_hermes_home() at import, so a
    # fresh import under this env anchors the store at <profile>/cron.
    import cron.jobs as jobs

    importlib.reload(jobs)
    try:
        assert jobs.HERMES_DIR.resolve() == profile_home.resolve()
        assert (
            jobs.JOBS_FILE.resolve()
            == (profile_home / "cron" / "jobs.json").resolve()
        )
        # The shared-root path must NOT be the store — that would re-break
        # per-profile isolation (#4707).
        assert (
            jobs.JOBS_FILE.resolve() != (root / "cron" / "jobs.json").resolve()
        )
    finally:
        monkeypatch.undo()
        importlib.reload(jobs)


def test_cron_connector_uses_ambient_profile_and_denies_interactive_approval(
    tmp_path, monkeypatch
):
    """Cron has no per-job profile escape and no waiting interactive gate."""
    import cron.scheduler as scheduler
    from gateway.session_context import get_session_env
    from hermes_cli import plugins as plugins_module
    from tools import approval
    from tools.registry import registry

    root = tmp_path / "hermes"
    profile = root / "profiles" / "scheduler-selected"
    plugin_root = profile / "plugins" / "ericsson-gitlab"
    plugin_root.parent.mkdir(parents=True)
    source = resolve_ericsson_connector_source()
    shutil.copytree(source.plugin, plugin_root)
    profile.joinpath("config.yaml").write_text(
        "plugins:\n  enabled: [ericsson-gitlab]\n  disabled: []\n"
        "platform_toolsets:\n  cron: [skills]\n"
        "approvals:\n  cron_mode: deny\n",
        encoding="utf-8",
    )
    root.mkdir(exist_ok=True)
    root.joinpath("config.yaml").write_text(
        "plugins:\n  enabled: []\n  disabled: [ericsson-gitlab]\n",
        encoding="utf-8",
    )
    _set_profile_env(monkeypatch, root, profile)
    monkeypatch.setenv("HERMES_MODEL", "test-model")
    captured = {}

    class DummyDB:
        def set_session_title(self, *args, **kwargs):
            pass

        def end_session(self, *args, **kwargs):
            pass

        def close(self):
            pass

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def run_conversation(self, prompt):
            captured["home_during_run"] = str(scheduler._get_hermes_home())
            captured["cron_context"] = get_session_env("HERMES_CRON_SESSION")
            captured["approval"] = approval.check_execute_code_guard(
                "print('cron')", "local"
            )
            return {
                "completed": True,
                "failed": False,
                "final_response": "done",
                "turn_exit_reason": "",
            }

        def close(self):
            pass

    monkeypatch.setattr("hermes_state.SessionDB", DummyDB)
    monkeypatch.setattr("run_agent.AIAgent", FakeAgent)
    monkeypatch.setattr(
        "hermes_constants.resolve_reasoning_config", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **_kwargs: {
            "api_key": "test-key",
            "base_url": None,
            "provider": "test-provider",
            "requested_provider": "test-provider",
            "api_mode": None,
            "command": None,
            "args": None,
        },
    )
    monkeypatch.setattr("tools.mcp_tool.discover_mcp_tools", lambda: [])
    monkeypatch.setattr(scheduler, "get_fallback_chain", lambda _cfg: [])
    monkeypatch.setattr(scheduler, "_guard_job_credential_exfil", lambda _job: None)
    monkeypatch.setattr(
        plugins_module.PluginManager, "_scan_entry_points", lambda self: []
    )

    success, _output, response, error = scheduler.run_job(
        {
            "id": "profile-connector",
            "name": "Profile Connector",
            "prompt": "Inspect GitLab",
            "schedule_display": "manual",
            # There is deliberately no supported per-job profile field.
            "profile": "wrong-profile-must-not-switch",
        }
    )

    assert success is True
    assert response == "done"
    assert error is None
    assert captured["platform"] == "cron"
    assert captured["home_during_run"] == str(profile)
    assert captured["cron_context"] == "1"
    assert "ericsson-gitlab" in captured["enabled_toolsets"]
    assert captured["approval"]["approved"] is False
    assert captured["approval"]["outcome"] == "blocked"

    for name in tuple(registry.get_all_tool_names()):
        if name.startswith("gitlab_"):
            registry.deregister(name)
