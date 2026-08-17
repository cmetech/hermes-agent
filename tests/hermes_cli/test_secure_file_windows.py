"""Compatibility behavior for config's general best-effort permission helper."""

import sys
from unittest import mock

import pytest

from hermes_cli import config


class TestWindowsAcl:
    def test_windows_secure_file_delegates_to_shared_acl_boundary(self, tmp_path):
        from hermes_cli import windows_permissions

        target = tmp_path / "config.yaml"
        target.write_text("model: test", encoding="utf-8")
        with (
            mock.patch.object(config.sys, "platform", "win32"),
            mock.patch.object(config, "_is_container", return_value=False),
            mock.patch.object(
                windows_permissions, "restrict_file_to_current_user"
            ) as restrict,
        ):
            config._secure_file(target)

        restrict.assert_called_once_with(target)

    def test_general_config_acl_failure_remains_best_effort(self, tmp_path, capsys):
        from hermes_cli import windows_permissions

        config._WARNED_ACL_PATHS.clear()
        target = tmp_path / "config.yaml"
        target.write_text("model: test", encoding="utf-8")
        with (
            mock.patch.object(config.sys, "platform", "win32"),
            mock.patch.object(config, "_is_container", return_value=False),
            mock.patch.object(
                windows_permissions,
                "restrict_file_to_current_user",
                side_effect=windows_permissions.WindowsAclError("access denied"),
            ),
        ):
            config._secure_file(target)
            config._secure_file(target)

        assert capsys.readouterr().err.count("could not restrict") == 1

    def test_managed_mode_still_skips(self, tmp_path):
        from hermes_cli import windows_permissions

        target = tmp_path / "config.yaml"
        target.write_text("model: test", encoding="utf-8")
        with (
            mock.patch.object(config, "is_managed", return_value=True),
            mock.patch.object(config.sys, "platform", "win32"),
            mock.patch.object(
                windows_permissions, "restrict_file_to_current_user"
            ) as restrict,
        ):
            config._secure_file(target)
        restrict.assert_not_called()

    def test_container_still_skips(self, tmp_path):
        from hermes_cli import windows_permissions

        target = tmp_path / "config.yaml"
        target.write_text("model: test", encoding="utf-8")
        with (
            mock.patch.object(config, "_is_container", return_value=True),
            mock.patch.object(config.sys, "platform", "win32"),
            mock.patch.object(
                windows_permissions, "restrict_file_to_current_user"
            ) as restrict,
        ):
            config._secure_file(target)
        restrict.assert_not_called()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX behaviour")
    def test_posix_behaviour_is_unchanged(self, tmp_path):
        import os
        import stat

        target = tmp_path / ".env"
        target.write_text("SECRET=x", encoding="utf-8")
        config._secure_file(target)
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
