"""Diagnosis and transactional recovery for plugin secret storage.

Every test installs an in-memory keyring before calling recovery code.  The
bounded read workers finish while that patch is alive, so this module can
never fall through to the developer's real keychain.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from hermes_cli import secret_keystore as sk
from hermes_cli.plugin_configuration import (
    FieldStorage,
    PluginConfigurationDescriptor,
    PluginConfigurationField,
    PluginConfigurationService,
    _secret_storage_key,
)
from hermes_cli.secret_authority import (
    AUTHORITY_VERSION,
    AuthorityRegistry,
    SecretAuthority,
    encode_authority_registry,
)
from hermes_cli.secrets_repair import (
    RepairRefusedError,
    apply_secret_repair,
    diagnose_secrets,
    plan_secret_repair,
    register_cli,
)


KEY = "HERMES_PLUGIN_0123456789ABCDEF0123456789ABCDEF_TOKEN"
SECRET = "secret-value-must-never-be-rendered"


class FakeKeyring:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.values: dict[tuple[str, str], str] = {}
        self.get_calls: list[tuple[str, str]] = []
        self.set_calls: list[tuple[str, str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []

    def get_password(self, service: str, account: str) -> str | None:
        self.get_calls.append((service, account))
        if not self.available:
            raise RuntimeError("unavailable")
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.set_calls.append((service, account, value))
        if not self.available:
            raise RuntimeError("unavailable")
        self.values[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        self.delete_calls.append((service, account))
        if not self.available:
            raise RuntimeError("unavailable")
        self.values.pop((service, account), None)

    def put(self, key: str, value: str, profile: Path) -> None:
        account = sk._os_account_name(key, str(profile.resolve()))
        self.values[(sk.SERVICE_NAME, account)] = value

    def value(self, key: str, profile: Path) -> str | None:
        account = sk._os_account_name(key, str(profile.resolve()))
        return self.values.get((sk.SERVICE_NAME, account))


@pytest.fixture
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "profiles" / "repair-test"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "auto")
    monkeypatch.setattr(
        PluginConfigurationService,
        "secret_storage_keys",
        lambda self: [KEY],
    )
    sk.reset_backend_cache()
    yield home
    sk.reset_backend_cache()


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    fake = FakeKeyring()
    monkeypatch.setattr(sk, "keyring", fake)
    return fake


def snapshot_tree(root: Path) -> dict[str, tuple[str, int, bytes]]:
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


def write_registry(profile: Path, entries: dict[str, SecretAuthority]) -> None:
    root = profile / "secrets"
    root.mkdir(parents=True, exist_ok=True)
    (root / "keystore.lock").touch(mode=0o600)
    (root / "authority.json").write_bytes(
        encode_authority_registry(
            AuthorityRegistry(version=AUTHORITY_VERSION, entries=entries)
        )
    )
    os.chmod(root / "authority.json", 0o600)


def write_file_secret(profile: Path, key: str = KEY, value: str = SECRET) -> None:
    sk.FileKeystore(profile / "secrets").set(key, value)


def finding_codes() -> set[str]:
    return {finding.code for finding in diagnose_secrets().findings}


def test_container_storage_evidence_is_fresh_and_doctor_is_read_only(
    profile, fake_keyring, monkeypatch
):
    from hermes_cli import container_storage
    from hermes_cli.container_storage import MountPersistence, PersistenceState

    root = profile / "secrets"
    evidence = iter(
        (
            MountPersistence(
                PersistenceState.UNKNOWN, None, None, None, "missing mountinfo"
            ),
            MountPersistence(
                PersistenceState.PERSISTENT,
                profile,
                "ext4",
                "/dev/x",
                "volume mount",
            ),
        )
    )
    inspected_paths: list[Path] = []
    monkeypatch.setattr(container_storage, "is_container", lambda: True)

    def inspect(path: Path) -> MountPersistence:
        inspected_paths.append(path)
        return next(evidence)

    monkeypatch.setattr(container_storage, "inspect_mount_persistence", inspect)
    before = snapshot_tree(profile)

    first = diagnose_secrets()
    second = diagnose_secrets()

    assert "CONTAINER_STORAGE_UNPROVEN" in {
        finding.code for finding in first.findings
    }
    assert "CONTAINER_STORAGE_UNPROVEN" not in {
        finding.code for finding in second.findings
    }
    assert inspected_paths == [root, root]
    assert snapshot_tree(profile) == before
    assert fake_keyring.set_calls == []
    assert fake_keyring.delete_calls == []


@pytest.mark.parametrize("state", ["ephemeral", "unknown"])
def test_unrecoverable_reset_refuses_unproven_container_storage_before_quarantine(
    profile, fake_keyring, monkeypatch, state
):
    from hermes_cli import container_storage
    from hermes_cli.container_storage import MountPersistence, PersistenceState

    write_file_secret(profile)
    write_registry(profile, {KEY: SecretAuthority.FILE})
    (profile / "secrets" / "keystore.key").unlink()
    persistence_state = PersistenceState(state)
    monkeypatch.setattr(container_storage, "is_container", lambda: True)
    monkeypatch.setattr(
        container_storage,
        "inspect_mount_persistence",
        lambda path: MountPersistence(
            persistence_state,
            profile if state == "ephemeral" else None,
            "tmpfs" if state == "ephemeral" else None,
            "tmpfs" if state == "ephemeral" else None,
            f"{state} test evidence",
        ),
    )
    before = snapshot_tree(profile)

    plan = plan_secret_repair(reset_unrecoverable=True)

    assert "CONTAINER_STORAGE_UNPROVEN" in {
        finding.code for finding in plan.blocked_findings
    }
    assert all(action.code != "RESET_UNRECOVERABLE" for action in plan.actions)
    with pytest.raises(RepairRefusedError, match="blocked"):
        apply_secret_repair(plan, confirm_reset=True)
    assert snapshot_tree(profile) == before


def test_reset_rechecks_persistence_after_all_reads_before_quarantine(
    profile, fake_keyring, monkeypatch
):
    from hermes_cli import container_storage
    from hermes_cli import secrets_repair as repair
    from hermes_cli.container_storage import MountPersistence, PersistenceState

    write_file_secret(profile)
    write_registry(profile, {KEY: SecretAuthority.FILE})
    (profile / "secrets" / "keystore.key").unlink()
    current_state = PersistenceState.PERSISTENT
    monkeypatch.setattr(container_storage, "is_container", lambda: True)

    def inspect(path):
        return MountPersistence(
            current_state,
            profile if current_state is PersistenceState.PERSISTENT else None,
            "ext4" if current_state is PersistenceState.PERSISTENT else None,
            "/dev/x" if current_state is PersistenceState.PERSISTENT else None,
            f"{current_state.value} evidence",
        )

    monkeypatch.setattr(container_storage, "inspect_mount_persistence", inspect)
    plan = plan_secret_repair(reset_unrecoverable=True)
    assert any(action.code == "RESET_UNRECOVERABLE" for action in plan.actions)
    original_assert = repair._assert_tier_unchanged

    def change_storage_after_read(*args, **kwargs):
        nonlocal current_state
        original_assert(*args, **kwargs)
        current_state = PersistenceState.UNKNOWN

    monkeypatch.setattr(repair, "_assert_tier_unchanged", change_storage_after_read)
    before = snapshot_tree(profile)

    with pytest.raises(RepairRefusedError, match="storage.*unknown"):
        apply_secret_repair(plan, confirm_reset=True)

    assert snapshot_tree(profile) == before


def test_windows_doctor_reports_acl_drift_without_mutating(
    profile, fake_keyring, monkeypatch
):
    from hermes_cli import secrets_repair as repair
    from hermes_cli import windows_permissions

    write_file_secret(profile)
    write_registry(profile, {KEY: SecretAuthority.FILE})
    before = snapshot_tree(profile)
    monkeypatch.setattr(sk, "_is_windows", lambda: True)
    inspect_directory = mock.Mock(
        return_value=windows_permissions.WindowsAclInspection(
            secure=False, detail="unexpected directory ACE"
        )
    )
    inspect_file = mock.Mock(
        return_value=windows_permissions.WindowsAclInspection(
            secure=True, detail=None
        )
    )
    monkeypatch.setattr(
        windows_permissions, "inspect_directory_acl", inspect_directory
    )
    monkeypatch.setattr(windows_permissions, "inspect_file_acl", inspect_file)

    report = diagnose_secrets()

    assert snapshot_tree(profile) == before
    drift = [finding for finding in report.findings if finding.code == "PERMISSION_DRIFT"]
    assert len(drift) == 1
    assert "unexpected directory ACE" in drift[0].message
    inspect_directory.assert_called_once_with(profile / "secrets")
    assert inspect_file.call_count >= 4


def test_windows_quarantine_acls_created_directories_manifest_and_renamed_file(
    profile, monkeypatch
):
    from hermes_cli import secrets_repair as repair
    from hermes_cli import windows_permissions

    root = profile / "secrets"
    root.mkdir(parents=True)
    artifact = root / "authority.json"
    artifact.write_text("corrupt", encoding="utf-8")
    restrict_file = mock.Mock()
    restrict_directory = mock.Mock()
    monkeypatch.setattr(sk, "_is_windows", lambda: True)
    monkeypatch.setattr(
        windows_permissions, "restrict_file_to_current_user", restrict_file
    )
    monkeypatch.setattr(
        windows_permissions,
        "restrict_directory_to_current_user",
        restrict_directory,
    )

    destination = repair._quarantine_artifacts(
        root, [artifact], finding_codes=("AUTHORITY_CORRUPT",)
    )

    assert destination is not None
    assert not artifact.exists()
    assert (destination / "authority.json").read_text(encoding="utf-8") == "corrupt"
    secured_directories = [call.args[0] for call in restrict_directory.call_args_list]
    assert root / "quarantine" in secured_directories
    assert destination in secured_directories
    secured_files = [call.args[0] for call in restrict_file.call_args_list]
    assert destination / "manifest.json" in secured_files
    assert destination / "authority.json" in secured_files


def test_windows_quarantine_acl_failure_raises_keystore_error(profile, monkeypatch):
    from hermes_cli import secrets_repair as repair
    from hermes_cli import windows_permissions

    root = profile / "secrets"
    root.mkdir(parents=True)
    artifact = root / "authority.json"
    artifact.write_text("corrupt", encoding="utf-8")
    monkeypatch.setattr(sk, "_is_windows", lambda: True)
    monkeypatch.setattr(
        windows_permissions,
        "restrict_directory_to_current_user",
        lambda _path: (_ for _ in ()).throw(
            windows_permissions.WindowsAclError("access denied")
        ),
    )

    with pytest.raises(sk.KeystoreError, match="ACL"):
        repair._quarantine_artifacts(
            root, [artifact], finding_codes=("AUTHORITY_CORRUPT",)
        )


def test_secret_storage_keys_is_static_manifest_inventory(monkeypatch):
    descriptor = PluginConfigurationDescriptor(
        version=1,
        fields=(
            PluginConfigurationField(
                id="token",
                label="Token",
                type="string",
                storage=FieldStorage.SECRET,
            ),
            PluginConfigurationField(
                id="endpoint",
                label="Endpoint",
                type="string",
                storage=FieldStorage.SETTING,
            ),
        ),
    )
    manifest = SimpleNamespace(
        key="sample-plugin", name="sample-plugin", configuration=descriptor
    )
    manager = SimpleNamespace(
        loaded_plugins=lambda: [SimpleNamespace(manifest=manifest)]
    )
    monkeypatch.setattr(
        sk, "resolve_secret", lambda *args, **kwargs: pytest.fail("read a value")
    )

    keys = PluginConfigurationService(manager).secret_storage_keys()

    assert keys == [_secret_storage_key("sample-plugin", "token")]


def test_doctor_does_not_create_an_absent_profile(profile, fake_keyring):
    assert not profile.exists()

    report = diagnose_secrets()

    assert report.authorities == {}
    assert not profile.exists()
    assert fake_keyring.set_calls == []
    assert fake_keyring.delete_calls == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission drift")
def test_doctor_is_byte_for_byte_read_only(profile, fake_keyring):
    write_file_secret(profile)
    write_registry(profile, {KEY: SecretAuthority.FILE})
    fake_keyring.put(KEY, SECRET, profile)
    abandoned = profile / "secrets" / ".keystore.enc.abandoned.tmp"
    abandoned.write_bytes(b"not-a-secret")
    os.chmod(abandoned, 0o600)
    os.chmod(profile / "secrets" / "keystore.enc", 0o644)
    before = snapshot_tree(profile)

    report = diagnose_secrets()

    assert snapshot_tree(profile) == before
    assert fake_keyring.set_calls == []
    assert fake_keyring.delete_calls == []
    codes = {finding.code for finding in report.findings}
    assert {"STALE_DUPLICATE", "PERMISSION_DRIFT", "ABANDONED_TEMP"} <= codes
    assert all(SECRET not in finding.message for finding in report.findings)


def test_doctor_reports_authority_corruption(profile, fake_keyring):
    root = profile / "secrets"
    root.mkdir(parents=True)
    (root / "authority.json").write_text("{not-json", encoding="utf-8")
    assert "AUTHORITY_CORRUPT" in finding_codes()


def test_doctor_reports_forced_mode_mismatch(profile, fake_keyring, monkeypatch):
    monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
    write_registry(profile, {KEY: SecretAuthority.OS})
    fake_keyring.put(KEY, SECRET, profile)
    assert "AUTHORITY_MODE_MISMATCH" in finding_codes()


def test_doctor_reports_competing_pre_registry_values(profile, fake_keyring):
    write_file_secret(profile, value="first-private-value")
    fake_keyring.put(KEY, "second-private-value", profile)
    report = diagnose_secrets()
    assert "COMPETING_VALUES" in {finding.code for finding in report.findings}
    assert all("first-private-value" not in item.message for item in report.findings)
    assert all("second-private-value" not in item.message for item in report.findings)


def test_doctor_reports_corrupt_file_store(profile, fake_keyring):
    write_file_secret(profile)
    write_registry(profile, {KEY: SecretAuthority.FILE})
    (profile / "secrets" / "keystore.key").write_bytes(b"short")
    assert "FILE_STORE_CORRUPT" in finding_codes()


def test_doctor_reports_os_unavailable_without_mutating(profile, monkeypatch):
    fake = FakeKeyring(available=False)
    monkeypatch.setattr(sk, "keyring", fake)
    before = snapshot_tree(profile)

    assert "OS_UNAVAILABLE" in finding_codes()

    assert snapshot_tree(profile) == before
    assert fake.set_calls == []
    assert fake.delete_calls == []


def test_repair_plan_is_sorted_and_read_only(profile, fake_keyring):
    write_file_secret(profile)
    write_registry(profile, {KEY: SecretAuthority.FILE})
    fake_keyring.put(KEY, SECRET, profile)
    abandoned = profile / "secrets" / ".authority.json.old.tmp"
    abandoned.write_bytes(b"temporary-metadata")
    before = snapshot_tree(profile)

    plan = plan_secret_repair()

    assert snapshot_tree(profile) == before
    assert list(plan.actions) == sorted(
        plan.actions, key=lambda action: (action.code, action.key or "")
    )
    assert {action.code for action in plan.actions} >= {
        "CLEAN_ABANDONED_TEMP",
        "DELETE_STALE_COPY",
    }
    assert fake_keyring.set_calls == []
    assert fake_keyring.delete_calls == []


def test_repair_reconstructs_unambiguous_registry(profile, fake_keyring):
    write_file_secret(profile)
    plan = plan_secret_repair()
    assert any(action.code == "REBUILD_AUTHORITY" for action in plan.actions)

    report = apply_secret_repair(plan)

    assert report.failed == ()
    assert sk.get_authority(KEY) == "file"
    assert sk.FileKeystore(profile / "secrets").get(KEY) == SECRET


def test_repair_quarantines_and_reconstructs_corrupt_authority(
    profile, fake_keyring
):
    write_file_secret(profile)
    (profile / "secrets" / "authority.json").write_text(
        "{broken-authority", encoding="utf-8"
    )
    os.chmod(profile / "secrets" / "authority.json", 0o600)
    plan = plan_secret_repair()
    assert len(plan.actions) == 1
    assert plan.actions[0].code == "REBUILD_AUTHORITY"
    assert plan.actions[0].key is None

    report = apply_secret_repair(plan)

    assert report.failed == ()
    assert sk.get_authority(KEY) == "file"
    assert len(report.quarantine_paths) == 1
    assert (report.quarantine_paths[0] / "authority.json").is_file()
    assert stat.S_IMODE(report.quarantine_paths[0].stat().st_mode) == 0o700
    assert stat.S_IMODE(
        (report.quarantine_paths[0] / "manifest.json").stat().st_mode
    ) == 0o600


def test_repair_rolls_back_interrupted_move_by_deleting_stale_copy(
    profile, fake_keyring
):
    write_file_secret(profile)
    write_registry(profile, {KEY: SecretAuthority.FILE})
    fake_keyring.put(KEY, "uncommitted-destination-value", profile)

    plan = plan_secret_repair()
    action = next(item for item in plan.actions if item.code == "DELETE_STALE_COPY")
    assert (action.source, action.destination) == ("os", None)
    report = apply_secret_repair(plan)

    assert report.failed == ()
    assert fake_keyring.value(KEY, profile) is None
    assert sk.FileKeystore(profile / "secrets").get(KEY) == SECRET
    assert sk.get_authority(KEY) == "file"


def test_repair_resumes_interrupted_move_when_only_destination_survives(
    profile, fake_keyring
):
    write_file_secret(profile, key="OTHER", value="other-private-value")
    write_registry(profile, {KEY: SecretAuthority.FILE})
    fake_keyring.put(KEY, SECRET, profile)

    plan = plan_secret_repair()
    action = next(item for item in plan.actions if item.code == "RESUME_MOVE")
    assert (action.source, action.destination) == ("file", "os")
    report = apply_secret_repair(plan)

    assert report.failed == ()
    assert sk.get_authority(KEY) == "os"
    assert fake_keyring.value(KEY, profile) == SECRET


def test_explicit_move_uses_transactional_tier_move(profile, fake_keyring):
    write_file_secret(profile)
    write_registry(profile, {KEY: SecretAuthority.FILE})

    plan = plan_secret_repair(move_to="os")
    assert any(action.code == "MOVE_SECRET" for action in plan.actions)
    report = apply_secret_repair(plan)

    assert report.failed == ()
    assert sk.get_authority(KEY) == "os"
    assert fake_keyring.value(KEY, profile) == SECRET
    assert sk.FileKeystore(profile / "secrets").get(KEY) is None


def test_explicit_file_policy_survives_revalidation_for_equal_legacy_copies(
    profile, fake_keyring
):
    write_file_secret(profile)
    fake_keyring.put(KEY, SECRET, profile)
    plan = plan_secret_repair(move_to="file")
    assert any(
        action.code == "REBUILD_AUTHORITY" and action.destination == "file"
        for action in plan.actions
    )

    report = apply_secret_repair(plan)

    assert report.failed == ()
    assert sk.get_authority(KEY) == "file"
    assert fake_keyring.value(KEY, profile) is None
    assert sk.FileKeystore(profile / "secrets").get(KEY) == SECRET


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission repair")
def test_repair_restores_permissions_and_cleans_temporary_files(
    profile, fake_keyring
):
    write_file_secret(profile)
    temp = profile / "secrets" / ".keystore.key.crash.tmp"
    temp.write_bytes(b"temporary")
    os.chmod(profile / "secrets", 0o755)
    os.chmod(profile / "secrets" / "keystore.key", 0o644)

    report = apply_secret_repair(plan_secret_repair())

    assert report.failed == ()
    assert not temp.exists()
    assert stat.S_IMODE((profile / "secrets").stat().st_mode) == 0o700
    assert stat.S_IMODE((profile / "secrets" / "keystore.key").stat().st_mode) == 0o600


def test_corrupt_file_store_rebuilds_from_healthy_os_copy(
    profile, fake_keyring
):
    write_file_secret(profile)
    write_registry(profile, {KEY: SecretAuthority.FILE})
    fake_keyring.put(KEY, SECRET, profile)
    (profile / "secrets" / "keystore.key").write_bytes(b"lost-master-key")

    plan = plan_secret_repair()
    assert [action.code for action in plan.actions] == ["REBUILD_FILE_STORE"]
    report = apply_secret_repair(plan)

    assert report.failed == ()
    assert len(report.quarantine_paths) == 1
    assert sk.FileKeystore(profile / "secrets").get(KEY) == SECRET
    assert fake_keyring.value(KEY, profile) is None
    manifest = (report.quarantine_paths[0] / "manifest.json").read_text()
    assert SECRET not in manifest
    assert "FILE_STORE_CORRUPT" in manifest


def test_unrecoverable_reset_requires_confirmation(profile, fake_keyring):
    write_file_secret(profile)
    write_registry(profile, {KEY: SecretAuthority.FILE})
    (profile / "secrets" / "keystore.key").unlink()
    plan = plan_secret_repair(reset_unrecoverable=True)
    assert any(action.code == "RESET_UNRECOVERABLE" for action in plan.actions)

    with pytest.raises(RepairRefusedError):
        apply_secret_repair(plan, confirm_reset=False)

    report = apply_secret_repair(plan, confirm_reset=True)
    assert len(report.quarantine_paths) == 1
    assert report.quarantine_paths[0].is_dir()
    assert sk.get_authority(KEY) == "cleared"
    assert sk.FileKeystore(profile / "secrets").keys() == []


def test_reset_failure_after_quarantine_is_retryable_and_preserves_evidence(
    profile, fake_keyring, monkeypatch
):
    import hermes_cli.secrets_repair as repair

    write_file_secret(profile)
    write_registry(profile, {KEY: SecretAuthority.FILE})
    (profile / "secrets" / "keystore.key").unlink()
    plan = plan_secret_repair(reset_unrecoverable=True)

    def fail_after_quarantine(_root: Path) -> None:
        raise sk.KeystoreError("injected initialization failure")

    monkeypatch.setattr(repair, "_initialize_clean_file_store", fail_after_quarantine)
    failed = apply_secret_repair(plan, confirm_reset=True)
    assert failed.failed
    quarantine = profile / "secrets" / "quarantine"
    first_snapshot = snapshot_tree(quarantine)
    assert first_snapshot

    monkeypatch.undo()
    # Restore the mandatory fake after undoing only to make the worker lifetime
    # explicit; all bounded reads finish before the test exits.
    monkeypatch.setenv("HERMES_HOME", str(profile))
    monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "auto")
    monkeypatch.setattr(sk, "keyring", fake_keyring)
    monkeypatch.setattr(
        PluginConfigurationService, "secret_storage_keys", lambda self: [KEY]
    )
    retry = apply_secret_repair(
        plan_secret_repair(reset_unrecoverable=True), confirm_reset=True
    )

    assert retry.failed == ()
    assert sk.get_authority(KEY) == "cleared"
    assert all(path.exists() for path in quarantine.iterdir())
    for relative, entry in first_snapshot.items():
        if relative == ".":
            continue
        assert relative in snapshot_tree(quarantine)


def test_reset_retries_after_clean_store_init_but_before_tombstone_commit(
    profile, fake_keyring, monkeypatch
):
    write_file_secret(profile)
    write_registry(profile, {KEY: SecretAuthority.FILE})
    (profile / "secrets" / "keystore.key").unlink()
    plan = plan_secret_repair(reset_unrecoverable=True)
    original_write = sk._write_authority_registry

    def fail_tombstone(_root, _registry):
        raise sk.KeystoreError("injected authority failure")

    monkeypatch.setattr(sk, "_write_authority_registry", fail_tombstone)
    failed = apply_secret_repair(plan, confirm_reset=True)
    assert failed.failed
    assert sk._read_file_store_readonly(profile / "secrets") == {}
    monkeypatch.setattr(sk, "_write_authority_registry", original_write)

    retry_plan = plan_secret_repair(reset_unrecoverable=True)
    assert any(
        action.code == "RESET_UNRECOVERABLE" for action in retry_plan.actions
    )
    retry = apply_secret_repair(retry_plan, confirm_reset=True)

    assert retry.failed == ()
    assert sk.get_authority(KEY) == "cleared"


def test_apply_rejects_a_stale_plan(profile, fake_keyring):
    temp = profile / "secrets" / ".authority.json.stale.tmp"
    temp.parent.mkdir(parents=True)
    temp.write_bytes(b"temporary")
    plan = plan_secret_repair()
    temp.unlink()

    with pytest.raises(RepairRefusedError, match="state changed"):
        apply_secret_repair(plan)


def test_apply_rejects_changed_source_with_same_action_shape(
    profile, fake_keyring, capsys
):
    import hermes_cli.secrets_repair as repair

    original = "original-private-value"
    replacement = "replacement-private-value"
    write_file_secret(profile, value=original)
    write_registry(profile, {KEY: SecretAuthority.FILE})
    plan = plan_secret_repair(move_to="os")
    assert [action.code for action in plan.actions] == ["MOVE_SECRET"]

    write_file_secret(profile, value=replacement)
    before = snapshot_tree(profile)
    with pytest.raises(RepairRefusedError, match="state changed"):
        apply_secret_repair(plan)

    assert snapshot_tree(profile) == before
    assert sk.get_authority(KEY) == "file"
    assert sk.FileKeystore(profile / "secrets").get(KEY) == replacement
    assert fake_keyring.value(KEY, profile) is None
    args = build_secrets_parser().parse_args(
        ["secrets", "repair", "--move-to", "os"]
    )
    assert args.func(args) == 0
    public_text = (
        repr(plan)
        + repr(diagnose_secrets())
        + repr(repair._PLAN_BINDINGS)
        + capsys.readouterr().out
    )
    for sensitive in (original, replacement):
        assert sensitive not in public_text
        assert hashlib.sha256(sensitive.encode()).hexdigest() not in public_text


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_absolute_authority_symlink_is_quarantined_without_following_target(
    profile, fake_keyring, tmp_path
):
    root = profile / "secrets"
    root.mkdir(parents=True)
    outside = tmp_path / "outside-authority.json"
    outside.write_bytes(b"outside-bytes-must-stay")
    os.chmod(outside, 0o644)
    (root / "authority.json").symlink_to(outside)
    before_mode = stat.S_IMODE(outside.stat().st_mode)
    before_bytes = outside.read_bytes()

    report = diagnose_secrets()
    assert "AUTHORITY_CORRUPT" in {finding.code for finding in report.findings}
    plan = plan_secret_repair()
    applied = apply_secret_repair(plan)

    assert applied.failed == ()
    assert outside.read_bytes() == before_bytes
    assert stat.S_IMODE(outside.stat().st_mode) == before_mode
    quarantined = applied.quarantine_paths[0] / "authority.json"
    assert quarantined.is_symlink()
    assert os.readlink(quarantined) == str(outside)
    assert not (root / "authority.json").is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_dangling_authority_symlink_is_detected_and_quarantined(
    profile, fake_keyring, tmp_path
):
    root = profile / "secrets"
    root.mkdir(parents=True)
    missing_target = tmp_path / "missing-outside-authority.json"
    (root / "authority.json").symlink_to(missing_target)

    assert "AUTHORITY_CORRUPT" in finding_codes()
    applied = apply_secret_repair(plan_secret_repair())

    assert applied.failed == ()
    quarantined = applied.quarantine_paths[0] / "authority.json"
    assert quarantined.is_symlink()
    assert os.readlink(quarantined) == str(missing_target)
    assert not missing_target.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
@pytest.mark.parametrize("dangling", [False, True])
def test_file_key_symlink_is_quarantined_without_following_target(
    profile, fake_keyring, tmp_path, dangling
):
    write_registry(profile, {KEY: SecretAuthority.FILE})
    root = profile / "secrets"
    outside = tmp_path / "outside-file-key"
    if not dangling:
        outside.write_bytes(b"x" * 32)
        os.chmod(outside, 0o644)
    (root / "keystore.key").symlink_to(outside)
    (root / "keystore.enc").write_bytes(b"x" * 13)
    before_bytes = None if dangling else outside.read_bytes()
    before_mode = None if dangling else stat.S_IMODE(outside.stat().st_mode)

    assert "FILE_STORE_CORRUPT" in finding_codes()
    plan = plan_secret_repair(reset_unrecoverable=True)
    applied = apply_secret_repair(plan, confirm_reset=True)

    assert applied.failed == ()
    quarantined = applied.quarantine_paths[0] / "keystore.key"
    assert quarantined.is_symlink()
    assert os.readlink(quarantined) == str(outside)
    if dangling:
        assert not outside.exists()
    else:
        assert outside.read_bytes() == before_bytes
        assert stat.S_IMODE(outside.stat().st_mode) == before_mode


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_permission_repair_refuses_a_replaced_symlink(
    profile, fake_keyring, tmp_path
):
    write_registry(profile, {})
    authority = profile / "secrets" / "authority.json"
    os.chmod(profile / "secrets", 0o700)
    os.chmod(profile / "secrets" / "keystore.lock", 0o600)
    os.chmod(authority, 0o644)
    plan = plan_secret_repair()
    assert [action.code for action in plan.actions] == ["REPAIR_PERMISSIONS"]
    valid_authority_bytes = authority.read_bytes()
    authority.unlink()
    outside = tmp_path / "outside-permission-target"
    outside.write_bytes(valid_authority_bytes)
    os.chmod(outside, 0o644)
    authority.symlink_to(outside)
    before_mode = stat.S_IMODE(outside.stat().st_mode)

    with pytest.raises(RepairRefusedError, match="state changed"):
        apply_secret_repair(plan)

    assert outside.read_bytes() == valid_authority_bytes
    assert stat.S_IMODE(outside.stat().st_mode) == before_mode


def test_quarantine_refuses_paths_outside_direct_child(profile, fake_keyring, tmp_path):
    import hermes_cli.secrets_repair as repair

    root = profile / "secrets"
    root.mkdir(parents=True)
    outside = tmp_path / "outside-artifact"
    outside.write_bytes(b"outside-bytes-must-stay")

    with pytest.raises(RepairRefusedError, match="direct child"):
        repair._quarantine_artifacts(
            root,
            [outside],
            finding_codes=("AUTHORITY_CORRUPT",),
        )

    assert outside.read_bytes() == b"outside-bytes-must-stay"


def build_secrets_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes")
    commands = parser.add_subparsers(dest="command")
    secrets = commands.add_parser("secrets")
    register_cli(secrets.add_subparsers(dest="secrets_command"))
    return parser


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["secrets", "doctor"], "doctor"),
        (["secrets", "repair"], "repair"),
        (["secrets", "repair", "--apply"], "repair"),
        (["secrets", "repair", "--move-to", "os", "--apply"], "repair"),
        (
            [
                "secrets",
                "repair",
                "--reset-unrecoverable",
                "--apply",
                "--yes",
            ],
            "repair",
        ),
    ],
)
def test_required_cli_forms_parse(argv, expected):
    args = build_secrets_parser().parse_args(argv)
    assert args.secrets_command == expected
    assert callable(args.func)


@pytest.mark.parametrize("prefix", ["--a", "--ap", "--app", "--appl"])
def test_repair_parser_rejects_apply_abbreviations(prefix):
    with pytest.raises(SystemExit) as exc_info:
        build_secrets_parser().parse_args(["secrets", "repair", prefix])
    assert exc_info.value.code == 2


def test_default_repair_cli_is_nonmutating_and_redacts_values(
    profile, fake_keyring, capsys
):
    write_file_secret(profile, value="first-private-value")
    fake_keyring.put(KEY, "second-private-value", profile)
    before = snapshot_tree(profile)
    parser = build_secrets_parser()
    args = parser.parse_args(["secrets", "repair"])

    rc = args.func(args)
    output = capsys.readouterr().out

    assert rc != 0
    assert snapshot_tree(profile) == before
    assert "COMPETING_VALUES" in output
    assert KEY in output
    assert "first-private-value" not in output
    assert "second-private-value" not in output
    assert fake_keyring.set_calls == []
    assert fake_keyring.delete_calls == []


def test_yes_is_refused_without_reset_and_apply(profile, fake_keyring, capsys):
    args = build_secrets_parser().parse_args(["secrets", "repair", "--yes"])
    assert args.func(args) != 0
    assert "--yes requires --reset-unrecoverable --apply" in capsys.readouterr().err


def test_noninteractive_reset_requires_yes(
    profile, fake_keyring, monkeypatch, capsys
):
    import io

    write_file_secret(profile)
    write_registry(profile, {KEY: SecretAuthority.FILE})
    (profile / "secrets" / "keystore.key").unlink()
    before = snapshot_tree(profile)
    args = build_secrets_parser().parse_args(
        ["secrets", "repair", "--reset-unrecoverable", "--apply"]
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    assert args.func(args) != 0
    assert "noninteractive reset requires --yes" in capsys.readouterr().err
    assert snapshot_tree(profile) == before


def test_real_console_doctor_and_default_repair_are_byte_read_only(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    hermes = repo / ".venv" / "bin" / "hermes"
    assert hermes.is_file(), "the real .venv/bin/hermes entrypoint is required"
    profile = tmp_path / "profiles" / "subprocess"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("secret_keystore: file\n", encoding="utf-8")
    marker = profile / "profile-marker.bin"
    marker.write_bytes(b"unchanged-profile-bytes")
    fake_modules = tmp_path / "fake-modules"
    package = fake_modules / "keyring"
    package.mkdir(parents=True)
    mutation_marker = tmp_path / "keyring-mutated"
    (package / "__init__.py").write_text(
        "from . import errors\n"
        "def get_password(service, account): return None\n"
        "def set_password(service, account, value):\n"
        f"    open({str(mutation_marker)!r}, 'wb').write(b'set')\n"
        "    raise AssertionError('mutating OS probe invoked')\n"
        "def delete_password(service, account):\n"
        f"    open({str(mutation_marker)!r}, 'wb').write(b'delete')\n"
        "    raise AssertionError('mutating OS probe invoked')\n",
        encoding="utf-8",
    )
    (package / "errors.py").write_text(
        "class PasswordDeleteError(Exception): pass\n", encoding="utf-8"
    )
    before = snapshot_tree(profile)
    env = os.environ.copy()
    env["HERMES_HOME"] = str(profile)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(fake_modules), str(repo), env.get("PYTHONPATH", "")]
    )

    for argv in (("secrets", "doctor"), ("secrets", "repair")):
        result = subprocess.run(
            [str(hermes), *argv],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode in {0, 1}, result.stderr
        assert "unchanged-profile-bytes" not in result.stdout
        assert snapshot_tree(profile) == before
        assert not mutation_marker.exists()


@pytest.mark.parametrize("prefix", ["--app", "--appl"])
def test_real_console_apply_prefix_cannot_mutate_from_readonly_path(tmp_path, prefix):
    repo = Path(__file__).resolve().parents[2]
    hermes = repo / ".venv" / "bin" / "hermes"
    profile = tmp_path / "profiles" / "prefix"
    temp = profile / "secrets" / ".authority.json.prefix.tmp"
    temp.parent.mkdir(parents=True)
    temp.write_bytes(b"must-not-be-removed")
    fake_modules = tmp_path / "fake-modules"
    package = fake_modules / "keyring"
    package.mkdir(parents=True)
    mutation_marker = tmp_path / "keyring-mutated"
    (package / "__init__.py").write_text(
        "from . import errors\n"
        "def get_password(service, account): return None\n"
        "def set_password(service, account, value):\n"
        f"    open({str(mutation_marker)!r}, 'wb').write(b'set')\n"
        "def delete_password(service, account):\n"
        f"    open({str(mutation_marker)!r}, 'wb').write(b'delete')\n",
        encoding="utf-8",
    )
    (package / "errors.py").write_text(
        "class PasswordDeleteError(Exception): pass\n", encoding="utf-8"
    )
    before = snapshot_tree(profile)
    env = os.environ.copy()
    env["HERMES_HOME"] = str(profile)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(fake_modules), str(repo), env.get("PYTHONPATH", "")]
    )

    result = subprocess.run(
        [str(hermes), "secrets", "repair", prefix],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 2
    assert snapshot_tree(profile) == before
    assert not mutation_marker.exists()
