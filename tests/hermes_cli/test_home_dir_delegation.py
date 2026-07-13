import os
from pathlib import Path

import hermes_constants


def test_home_dir_basename_matches_default_home_name():
    # Derives from the single owned literal — same basename the resolver uses.
    expected = hermes_constants._get_platform_default_hermes_home().name
    assert hermes_constants.home_dir_basename() == expected


def test_env_loader_uses_hermes_home(tmp_path, monkeypatch):
    from hermes_cli import env_loader

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("FOO=bar\n")
    # load_hermes_dotenv() resolves HERMES_HOME/.env, not ~/.hermes/.env.
    env_loader.load_hermes_dotenv()
    assert os.environ.get("FOO") == "bar"


def test_env_loader_no_env_var_falls_back_to_resolver(monkeypatch):
    # With HERMES_HOME unset AND no hermes_home arg, env_loader must resolve
    # via get_hermes_home() (the baked default), NOT an inlined ~/.hermes literal.
    from hermes_cli import env_loader

    monkeypatch.delenv("HERMES_HOME", raising=False)
    calls = {}
    import hermes_constants as hc

    real = hc.get_hermes_home

    monkeypatch.setattr(hc, "get_hermes_home", lambda: calls.setdefault("hit", real()))
    env_loader.load_hermes_dotenv()
    assert "hit" in calls  # the fallback path delegated to the resolver
