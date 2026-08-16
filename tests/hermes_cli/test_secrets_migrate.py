"""Tests for hermes_cli.secrets_migrate."""

from unittest import mock

import pytest

import hermes_cli.secret_keystore as sk
from hermes_cli.secrets_migrate import (
    MigrationReport,
    _handle_secrets_migrate,
    find_legacy_secrets,
    migrate_secrets,
)

# A valid storage key: HERMES_PLUGIN_<32 uppercase hex>_<SLUG>, matching
# _secret_storage_key()'s sha256(...).hexdigest()[:32].upper() in
# plugin_configuration.py. A shorter digest would not match
# _PLUGIN_SECRET_KEY and every test using it would silently find nothing.
KEY = "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT"


@pytest.fixture(autouse=True)
def _keystore(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
    sk.reset_backend_cache()
    yield
    sk.reset_backend_cache()


class TestFindLegacySecrets:
    def test_selects_only_plugin_secret_keys(self):
        env = {
            "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT": "tok1",
            "HERMES_PLUGIN_E5F60718E5F60718E5F60718E5F60718_API_TOKEN": "tok2",
            "HERMES_PLUGIN_PAYLOAD_MAX_CHARS": "50000",
            "ANTHROPIC_API_KEY": "sk-x",
            "HERMES_HOME": "/somewhere",
        }
        with mock.patch("hermes_cli.secrets_migrate.load_env", return_value=env):
            found = find_legacy_secrets()
        assert set(found) == {
            "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT",
            "HERMES_PLUGIN_E5F60718E5F60718E5F60718E5F60718_API_TOKEN",
        }

    def test_ignores_empty_values(self):
        with mock.patch(
            "hermes_cli.secrets_migrate.load_env",
            return_value={
                "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT": ""
            },
        ):
            assert find_legacy_secrets() == {}


class TestMigrate:
    def test_dry_run_writes_nothing(self):
        env = {
            "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT": "tok"
        }
        removed = []
        with mock.patch("hermes_cli.secrets_migrate.load_env", return_value=env):
            with mock.patch(
                "hermes_cli.secrets_migrate.remove_env_value",
                side_effect=lambda k, **kwargs: (removed.append(k), True)[1],
            ):
                report = migrate_secrets(dry_run=True)
        assert report.migrated == [
            "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT"
        ]
        assert report.dry_run is True
        assert removed == []
        assert (
            sk.get_secret(
                "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT"
            )
            is None
        )

    def test_migrates_then_removes_from_env(self):
        env = {
            "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT": "tok"
        }
        removed = []
        with mock.patch("hermes_cli.secrets_migrate.load_env", return_value=env):
            with mock.patch(
                "hermes_cli.secrets_migrate.remove_env_value",
                side_effect=lambda k, **kwargs: (removed.append(k), True)[1],
            ):
                report = migrate_secrets()
        assert report.migrated == [
            "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT"
        ]
        assert (
            sk.get_secret(
                "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT"
            )
            == "tok"
        )
        assert removed == [
            "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT"
        ]

    def test_env_entry_kept_when_keystore_write_fails(self):
        env = {
            "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT": "tok"
        }
        removed = []
        with mock.patch("hermes_cli.secrets_migrate.load_env", return_value=env):
            with mock.patch(
                "hermes_cli.secrets_migrate.remove_env_value",
                side_effect=lambda k, **kwargs: (removed.append(k), True)[1],
            ):
                with mock.patch.object(
                    sk, "set_secret", side_effect=sk.KeystoreError("boom")
                ):
                    report = migrate_secrets()
        assert report.failed == [
            "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT"
        ]
        assert report.migrated == []
        assert removed == []

    def test_env_entry_kept_when_readback_mismatches(self):
        env = {
            "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT": "tok"
        }
        removed = []
        with mock.patch("hermes_cli.secrets_migrate.load_env", return_value=env):
            with mock.patch(
                "hermes_cli.secrets_migrate.remove_env_value",
                side_effect=lambda k, **kwargs: (removed.append(k), True)[1],
            ):
                with mock.patch.object(sk, "get_secret", return_value="different"):
                    report = migrate_secrets()
        assert report.failed == [
            "HERMES_PLUGIN_A1B2C3D4A1B2C3D4A1B2C3D4A1B2C3D4_PAT"
        ]
        assert removed == []

    def test_no_legacy_secrets_is_a_clean_noop(self):
        with mock.patch("hermes_cli.secrets_migrate.load_env", return_value={}):
            report = migrate_secrets()
        assert report == MigrationReport(migrated=[], failed=[], dry_run=False)

    def test_a_failed_env_removal_is_reported_as_failed_not_migrated(self):
        """A surviving plaintext entry means the migration did not finish."""
        env = {KEY: "tok"}
        with mock.patch("hermes_cli.secrets_migrate.load_env", return_value=env):
            with mock.patch(
                "hermes_cli.secrets_migrate.remove_env_value",
                side_effect=lambda k, **kwargs: False,
            ):
                report = migrate_secrets()
        assert report.migrated == []
        assert report.failed == [KEY]

    def test_a_persistence_error_during_removal_is_also_failed(self):
        from hermes_cli.config import ConfigurationPersistenceError

        env = {KEY: "tok"}
        with mock.patch("hermes_cli.secrets_migrate.load_env", return_value=env):
            with mock.patch(
                "hermes_cli.secrets_migrate.remove_env_value",
                side_effect=ConfigurationPersistenceError("disk full"),
            ):
                report = migrate_secrets()
        assert report.migrated == []
        assert report.failed == [KEY]

    def test_removal_is_strict_and_does_not_touch_process_env(self):
        """Removal must fail loudly and never mirror a plugin PAT to env."""
        env = {KEY: "tok"}
        with mock.patch("hermes_cli.secrets_migrate.load_env", return_value=env):
            with mock.patch(
                "hermes_cli.secrets_migrate.remove_env_value", return_value=True
            ) as remove:
                migrate_secrets()
        assert remove.call_args.kwargs["strict"] is True
        assert remove.call_args.kwargs["mirror_process_env"] is False


class TestCommandHandler:
    def test_dry_run_reports_backend_and_returns_success(self, capsys):
        args = mock.Mock(dry_run=True)
        report = MigrationReport(migrated=[KEY], failed=[], dry_run=True)
        backend = mock.Mock()
        backend.name = "file"
        with mock.patch(
            "hermes_cli.secrets_migrate.migrate_secrets", return_value=report
        ):
            with mock.patch.object(sk, "get_backend", return_value=backend):
                exit_code = _handle_secrets_migrate(args)

        output = capsys.readouterr().out
        assert exit_code == 0
        assert "Would migrate 1 secret(s) to the file keystore." in output
        assert f"would migrate: {KEY}" in output

    def test_failure_reports_plaintext_is_retained_and_returns_error(self, capsys):
        args = mock.Mock(dry_run=False)
        report = MigrationReport(migrated=[], failed=[KEY], dry_run=False)
        backend = mock.Mock()
        backend.name = "file"
        with mock.patch(
            "hermes_cli.secrets_migrate.migrate_secrets", return_value=report
        ):
            with mock.patch.object(sk, "get_backend", return_value=backend):
                exit_code = _handle_secrets_migrate(args)

        output = capsys.readouterr().out
        assert exit_code == 1
        assert "could NOT be migrated and remain in .env" in output
        assert f"failed: {KEY}" in output
