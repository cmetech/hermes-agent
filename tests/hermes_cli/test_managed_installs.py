from types import SimpleNamespace
from unittest.mock import patch

from hermes_cli.config import recommended_update_command
from hermes_cli.main import cmd_update
from tools.skills_hub import OptionalSkillSource


def test_recommended_update_command_defaults_to_hermes_update(monkeypatch):
    monkeypatch.delenv("HERMES_MANAGED", raising=False)

    # Also short-circuit the .managed marker path — CI runners may have an
    # ambient ~/.hermes/.managed if a prior test left HERMES_HOME pointing
    # somewhere with that marker, which would make get_managed_update_command()
    # return "Update your Nix flake input ..." instead of falling through to
    # detect_install_method().
    with patch("hermes_cli.config.get_managed_update_command", return_value=None), \
         patch("hermes_cli.config.detect_install_method", return_value="git"):
        # Derived, not hardcoded: this fork rebrands the source-install
        # update command (recommended_update_command_for_method returns
        # "<brand> update", not "hermes update"), so a literal here asserts
        # upstream's branding and fails on every brand branch. The behaviour
        # under test is that a git install falls through to the source-install
        # command at all -- not what that command is called.
        from hermes_cli.config import recommended_update_command_for_method

        assert recommended_update_command() == recommended_update_command_for_method("git")


def test_optional_skill_source_honors_env_override(monkeypatch, tmp_path):
    optional_dir = tmp_path / "optional-skills"
    optional_dir.mkdir()
    monkeypatch.setenv("HERMES_OPTIONAL_SKILLS", str(optional_dir))

    source = OptionalSkillSource()

    assert source._optional_dir == optional_dir
