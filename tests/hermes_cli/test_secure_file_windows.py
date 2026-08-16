"""_secure_file must restrict access on Windows, not silently no-op."""

import sys
from unittest import mock

import pytest

from hermes_cli import config


class TestWindowsAcl:
    def test_windows_path_rebuilds_the_dacl_via_powershell(self, tmp_path):
        target = tmp_path / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        with mock.patch.object(config.sys, "platform", "win32"):
            with mock.patch.object(
                config,
                "_current_windows_sid",
                return_value="S-1-5-21-1-2-3-1001",
            ):
                with mock.patch.object(config.subprocess, "run") as run:
                    run.return_value = mock.Mock(returncode=0)
                    config._secure_file(target)
        assert run.called
        argv = run.call_args[0][0]
        assert argv[0] == "powershell"
        # Read the script from -Command's operand, not argv[-1]: what trails
        # the command has changed twice already and positional indexing hid it.
        script = argv[argv.index("-Command") + 1]
        # Inheritance detached AND inherited copies discarded.
        assert "SetAccessRuleProtection($true,$false)" in script
        # Every explicit ACE purged. icacls could not do this: /inheritance:r
        # drops only inherited ACEs and /remove:g only the SIDs it is given.
        assert "PurgeAccessRules" in script
        # Exactly one rule added back.
        assert script.count("AddAccessRule") == 1
        # Identity arrives as data, not baked into the source.
        assert "S-1-5-21-1-2-3-1001" not in script
        assert "$env:HERMES_ACL_SID" in script
        assert "Administrators" not in script
        child_env = run.call_args.kwargs["env"]
        assert child_env["HERMES_ACL_SID"] == "S-1-5-21-1-2-3-1001"

    def test_the_path_is_passed_as_data_not_interpolated_source(self, tmp_path):
        """A path is user-controlled. Interpolating it into PowerShell source
        lets a directory named  '; rm -r x; $y='  run as code -- and Windows
        permits single quotes in file names."""
        hostile = tmp_path / "it's ok'; Write-Output PWNED; $x='"
        hostile.mkdir()
        target = hostile / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        with mock.patch.object(config.sys, "platform", "win32"):
            with mock.patch.object(
                config,
                "_current_windows_sid",
                return_value="S-1-5-21-1-2-3-1001",
            ):
                with mock.patch.object(config.subprocess, "run") as run:
                    run.return_value = mock.Mock(returncode=0)
                    config._secure_file(target)
        argv = run.call_args[0][0]
        script = argv[argv.index("-Command") + 1]
        assert "PWNED" not in script, "path was interpolated into the script body"
        assert str(target) not in script, "path reached the script source at all"
        # It travels through the child environment instead, where it is data.
        assert run.call_args.kwargs["env"]["HERMES_ACL_PATH"] == str(target)

    def test_no_sid_means_no_call_and_no_silent_success(self, tmp_path):
        """If the SID cannot be resolved the DACL cannot be built correctly.
        Doing nothing and reporting success would be the worst outcome."""
        target = tmp_path / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        config._WARNED_ACL_PATHS.clear()
        with mock.patch.object(config.sys, "platform", "win32"):
            with mock.patch.object(config, "_current_windows_sid", return_value=""):
                with mock.patch.object(config.subprocess, "run") as run:
                    config._secure_file(target)
        assert not run.called

    def test_acl_failure_does_not_raise(self, tmp_path):
        """A failed ACL must not break setup -- but see the next test."""
        target = tmp_path / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        with mock.patch.object(config.sys, "platform", "win32"):
            with mock.patch.object(
                config.subprocess, "run", side_effect=OSError("no powershell")
            ):
                config._secure_file(target)

    def test_acl_failure_warns_once(self, tmp_path, capsys):
        """Silently passing is exactly what produced this gap. A failure the
        operator never sees is indistinguishable from no protection."""
        config._WARNED_ACL_PATHS.clear()
        target = tmp_path / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        with mock.patch.object(config.sys, "platform", "win32"):
            with mock.patch.object(
                config.subprocess, "run", side_effect=OSError("no powershell")
            ):
                config._secure_file(target)
                config._secure_file(target)
        warnings = capsys.readouterr().err
        assert warnings.count("could not restrict") == 1

    def test_managed_mode_still_skips(self, tmp_path):
        target = tmp_path / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        with mock.patch.object(config, "is_managed", return_value=True):
            with mock.patch.object(config.sys, "platform", "win32"):
                with mock.patch.object(config.subprocess, "run") as run:
                    config._secure_file(target)
        assert not run.called

    def test_container_still_skips(self, tmp_path):
        target = tmp_path / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        with mock.patch.object(config, "_is_container", return_value=True):
            with mock.patch.object(config.sys, "platform", "win32"):
                with mock.patch.object(config.subprocess, "run") as run:
                    config._secure_file(target)
        assert not run.called

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX behaviour")
    def test_posix_behaviour_is_unchanged(self, tmp_path):
        import os
        import stat

        target = tmp_path / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        config._secure_file(target)
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
