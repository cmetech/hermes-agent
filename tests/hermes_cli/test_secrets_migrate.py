"""Tests for hermes_cli.secrets_migrate."""

import os
import stat
import subprocess
from pathlib import Path
from unittest import mock

import pytest

import hermes_cli.secret_keystore as sk
from hermes_cli.secret_authority import (
    AUTHORITY_VERSION,
    AuthorityRegistry,
    SecretAuthority,
    encode_authority_registry,
)
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
SECOND_KEY = "HERMES_PLUGIN_E5F60718E5F60718E5F60718E5F60718_API_TOKEN"


def snapshot_tree(root: Path) -> dict[str, tuple[str, int, bytes]]:
    """Capture profile contents and modes for exact read-only assertions."""
    if not root.exists():
        return {}
    snapshot: dict[str, tuple[str, int, bytes]] = {}
    for path in sorted((root, *root.rglob("*"))):
        relative = "." if path == root else path.relative_to(root).as_posix()
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            snapshot[relative] = ("dir", mode, b"")
        elif path.is_file():
            snapshot[relative] = ("file", mode, path.read_bytes())
    return snapshot


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


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("os", "os"), (" FILE ", "file"), ("bogus", "auto")],
)
def test_dependency_light_mode_reader_matches_public_contract(
    monkeypatch, configured, expected
):
    from hermes_cli.secrets_migrate import _get_configured_mode_readonly

    monkeypatch.setenv("HERMES_SECRET_KEYSTORE", configured)
    assert _get_configured_mode_readonly() == expected
    assert sk.get_configured_mode() == expected


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

    @pytest.mark.parametrize(
        ("target", "error"),
        [
            ("builtins.open", PermissionError("open denied")),
            (
                "hermes_cli.config.tempfile.mkstemp",
                PermissionError("mkstemp denied"),
            ),
            ("hermes_cli.config.os.fsync", OSError("fsync failed")),
            ("hermes_cli.config.atomic_replace", OSError("replace failed")),
        ],
    )
    def test_ordinary_removal_error_keeps_plaintext_and_continues(
        self, tmp_path, target, error
    ):
        """A real file-operation failure must not abort later migrations."""
        import builtins

        from hermes_cli import config

        env_path = tmp_path / ".env"
        env_path.write_text(
            f"{KEY}=legacy-one\n{SECOND_KEY}=legacy-two\n",
            encoding="utf-8",
        )
        config.invalidate_env_cache()
        assert config.load_env() == {
            KEY: "legacy-one",
            SECOND_KEY: "legacy-two",
        }

        originals = {
            "builtins.open": builtins.open,
            "hermes_cli.config.tempfile.mkstemp": config.tempfile.mkstemp,
            "hermes_cli.config.os.fsync": config.os.fsync,
            "hermes_cli.config.atomic_replace": config.atomic_replace,
        }
        original = originals[target]
        calls = 0

        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise error
            return original(*args, **kwargs)

        values = {KEY: "legacy-one", SECOND_KEY: "legacy-two"}
        with mock.patch.object(sk, "set_secret"):
            with mock.patch.object(
                sk, "get_secret", side_effect=lambda key: values[key]
            ):
                with mock.patch(target, side_effect=fail_once):
                    report = migrate_secrets()

        assert report.failed == [KEY]
        assert report.migrated == [SECOND_KEY]
        remaining = env_path.read_text(encoding="utf-8")
        assert f"{KEY}=legacy-one" in remaining
        assert SECOND_KEY not in remaining

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
    def test_clean_noop_does_not_resolve_backend(self, capsys):
        args = mock.Mock(dry_run=False)
        report = MigrationReport(migrated=[], failed=[], dry_run=False)
        with mock.patch(
            "hermes_cli.secrets_migrate.migrate_secrets", return_value=report
        ):
            with mock.patch.object(sk, "get_backend") as get_backend:
                exit_code = _handle_secrets_migrate(args)

        assert exit_code == 0
        assert "No plugin secrets found in .env" in capsys.readouterr().out
        get_backend.assert_not_called()

    def test_dry_run_never_resolves_or_probes_backend(
        self, monkeypatch, capsys
    ):
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "auto")
        args = mock.Mock(dry_run=True)
        report = MigrationReport(migrated=[KEY], failed=[], dry_run=True)
        with mock.patch(
            "hermes_cli.secrets_migrate.migrate_secrets", return_value=report
        ), mock.patch.object(
            sk, "get_backend", side_effect=AssertionError("resolved")
        ), mock.patch.object(
            sk, "probe_os_keystore", side_effect=AssertionError("probed")
        ):
            exit_code = _handle_secrets_migrate(args)

        output = capsys.readouterr().out
        assert exit_code == 0
        assert output == (
            "Would migrate 1 secret(s) "
            "(configured auto mode; backend not probed).\n"
            f"  would migrate: {KEY}\n"
        )

    def test_candidate_free_dry_run_still_reports_non_probing_mode(
        self, monkeypatch, capsys
    ):
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "auto")
        report = MigrationReport(migrated=[], failed=[], dry_run=True)
        with mock.patch(
            "hermes_cli.secrets_migrate.migrate_secrets", return_value=report
        ), mock.patch.object(
            sk, "get_backend", side_effect=AssertionError("resolved")
        ), mock.patch.object(
            sk, "probe_os_keystore", side_effect=AssertionError("probed")
        ):
            exit_code = _handle_secrets_migrate(mock.Mock(dry_run=True))

        assert exit_code == 0
        assert capsys.readouterr().out == (
            "Would migrate 0 secret(s) "
            "(configured auto mode; backend not probed).\n"
        )

    def test_dry_run_handler_does_not_create_an_absent_profile(
        self, tmp_path, monkeypatch, capsys
    ):
        profile = tmp_path / "profiles" / "absent"
        monkeypatch.setenv("HERMES_HOME", str(profile))
        monkeypatch.delenv("HERMES_SECRET_KEYSTORE", raising=False)
        report = MigrationReport(migrated=[KEY], failed=[], dry_run=True)
        before = snapshot_tree(profile)

        with mock.patch(
            "hermes_cli.secrets_migrate.migrate_secrets", return_value=report
        ), mock.patch.object(
            sk, "get_backend", side_effect=AssertionError("resolved")
        ), mock.patch.object(
            sk, "probe_os_keystore", side_effect=AssertionError("probed")
        ):
            exit_code = _handle_secrets_migrate(mock.Mock(dry_run=True))

        assert exit_code == 0
        assert snapshot_tree(profile) == before == {}
        assert "configured auto mode; backend not probed" in capsys.readouterr().out

    def test_real_file_migration_reports_backend_neutral_success(
        self, tmp_path, capsys
    ):
        from hermes_cli import config

        env_path = tmp_path / ".env"
        env_path.write_text(f"{KEY}=legacy-value\n", encoding="utf-8")
        config.invalidate_env_cache()

        with mock.patch.object(
            sk, "get_backend", side_effect=AssertionError("resolved after writes")
        ):
            exit_code = _handle_secrets_migrate(mock.Mock(dry_run=False))

        assert exit_code == 0
        assert sk.get_secret(KEY) == "legacy-value"
        assert sk.get_authority(KEY) is SecretAuthority.FILE
        assert KEY not in config.load_env()
        assert capsys.readouterr().out == (
            "Migrated 1 secret(s) to protected credential storage.\n"
            f"  migrated: {KEY}\n"
        )

    @pytest.mark.parametrize(
        "authorities",
        [
            {KEY: SecretAuthority.OS},
            {KEY: SecretAuthority.FILE},
            {KEY: SecretAuthority.OS, SECOND_KEY: SecretAuthority.FILE},
        ],
        ids=("os", "file", "mixed"),
    )
    def test_success_reporting_is_neutral_for_durable_authority_layouts(
        self, tmp_path, authorities, capsys
    ):
        root = tmp_path / "secrets"
        root.mkdir(mode=0o700)
        (root / "keystore.lock").touch(mode=0o600)
        authority_path = root / "authority.json"
        authority_path.write_bytes(
            encode_authority_registry(
                AuthorityRegistry(version=AUTHORITY_VERSION, entries=authorities)
            )
        )
        os.chmod(authority_path, 0o600)
        assert {key: sk.get_authority(key) for key in authorities} == authorities

        migrated = list(authorities)
        report = MigrationReport(migrated=migrated, failed=[], dry_run=False)
        with mock.patch(
            "hermes_cli.secrets_migrate.migrate_secrets", return_value=report
        ), mock.patch.object(
            sk, "get_backend", side_effect=AssertionError("resolved after writes")
        ):
            exit_code = _handle_secrets_migrate(mock.Mock(dry_run=False))

        expected_lines = [
            f"Migrated {len(migrated)} secret(s) to protected credential storage."
        ]
        expected_lines.extend(f"  migrated: {key}" for key in migrated)
        assert exit_code == 0
        assert capsys.readouterr().out == "\n".join(expected_lines) + "\n"

    def test_failure_reports_plaintext_is_retained_and_returns_error(self, capsys):
        args = mock.Mock(dry_run=False)
        report = MigrationReport(migrated=[], failed=[KEY], dry_run=False)
        with mock.patch(
            "hermes_cli.secrets_migrate.migrate_secrets", return_value=report
        ), mock.patch.object(
            sk, "get_backend", side_effect=AssertionError("resolved after writes")
        ):
            exit_code = _handle_secrets_migrate(args)

        output = capsys.readouterr().out
        assert exit_code == 1
        assert output.startswith(
            "Migrated 0 secret(s) to protected credential storage.\n"
        )
        assert "could NOT be migrated and remain in .env" in output
        assert f"failed: {KEY}" in output


def test_real_console_dry_run_is_byte_read_only_and_never_touches_keyring(
    tmp_path,
):
    repo = Path(__file__).resolve().parents[2]
    hermes = repo / ".venv" / "bin" / "hermes"
    assert hermes.is_file(), "the real .venv/bin/hermes entrypoint is required"

    hermes_root = tmp_path / "hermes-root"
    profile = hermes_root / "profiles" / "dry-run"
    profile.mkdir(parents=True)
    (profile / ".env").write_text(f"{KEY}=legacy-secret\n", encoding="utf-8")
    marker = profile / "profile-marker.bin"
    marker.write_bytes(b"unchanged-profile-bytes")

    fake_modules = tmp_path / "fake-modules"
    package = fake_modules / "keyring"
    package.mkdir(parents=True)
    keyring_marker = tmp_path / "keyring-touched"
    (package / "__init__.py").write_text(
        f"open({str(keyring_marker)!r}, 'wb').write(b'import')\n"
        "raise AssertionError('keyring imported during dry-run')\n",
        encoding="utf-8",
    )
    (package / "errors.py").write_text(
        "class PasswordDeleteError(Exception): pass\n", encoding="utf-8"
    )

    before = snapshot_tree(profile)
    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_root)
    env.pop("HERMES_SECRET_KEYSTORE", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(fake_modules), str(repo), env.get("PYTHONPATH", "")]
    )
    result = subprocess.run(
        [
            str(hermes),
            "--profile",
            "dry-run",
            "secrets",
            "migrate",
            "--dry-run",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "configured auto mode; backend not probed" in result.stdout
    assert KEY in result.stdout
    assert "legacy-secret" not in result.stdout
    assert snapshot_tree(profile) == before
    assert not keyring_marker.exists()
