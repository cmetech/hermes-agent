"""Tests for hermes_cli.secret_keystore — two-tier plugin secret storage."""

import multiprocessing
import os
import stat
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

from hermes_cli import secret_keystore
from hermes_cli.secret_keystore import (
    OSKeystore,
    PROBE_TIMEOUT_SECONDS,
    SERVICE_NAME,
    _PROBE_VALUE,
    FileKeystore,
    KeystoreError,
    probe_os_keystore,
)


def _concurrent_file_writer(root, prefix, start, count):
    """Spawn-safe worker used by the real cross-process transaction test."""
    store = FileKeystore(Path(root))
    start.wait(timeout=10)
    for index in range(count):
        store.set(f"{prefix}-{index}", f"value-{prefix}-{index}")


class TestFileKeystore:
    def test_absent_store_get_does_not_create_root(self, tmp_path):
        root = tmp_path / "secrets"

        assert FileKeystore(root).get("missing") is None

        assert not root.exists()

    def test_absent_store_keys_does_not_create_root(self, tmp_path):
        root = tmp_path / "secrets"

        assert FileKeystore(root).keys() == []

        assert not root.exists()

    def test_absent_store_delete_does_not_create_root(self, tmp_path):
        root = tmp_path / "secrets"

        FileKeystore(root).delete("missing")

        assert not root.exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
    def test_set_securely_initializes_an_absent_store(self, tmp_path):
        root = tmp_path / "secrets"

        FileKeystore(root).set("K", "v")

        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE((root / "keystore.key").stat().st_mode) == 0o600
        assert stat.S_IMODE((root / "keystore.enc").stat().st_mode) == 0o600
        assert stat.S_IMODE((root / "keystore.lock").stat().st_mode) == 0o600

    def test_round_trip(self, tmp_path):
        store = FileKeystore(tmp_path)
        store.set("HERMES_PLUGIN_ABC_PAT", "s3cret-value")
        assert store.get("HERMES_PLUGIN_ABC_PAT") == "s3cret-value"

    def test_missing_key_returns_none(self, tmp_path):
        store = FileKeystore(tmp_path)
        assert store.get("HERMES_PLUGIN_NOPE_PAT") is None

    def test_delete_removes_value(self, tmp_path):
        store = FileKeystore(tmp_path)
        store.set("K", "v")
        store.delete("K")
        assert store.get("K") is None

    def test_delete_missing_key_is_silent(self, tmp_path):
        FileKeystore(tmp_path).delete("ABSENT")

    def test_ciphertext_does_not_contain_plaintext(self, tmp_path):
        store = FileKeystore(tmp_path)
        store.set("K", "distinctive-plaintext-marker")
        blob = (tmp_path / "keystore.enc").read_bytes()
        assert b"distinctive-plaintext-marker" not in blob

    def test_survives_new_instance(self, tmp_path):
        FileKeystore(tmp_path).set("K", "v")
        assert FileKeystore(tmp_path).get("K") == "v"

    def test_keys_lists_stored_names(self, tmp_path):
        store = FileKeystore(tmp_path)
        store.set("A", "1")
        store.set("B", "2")
        assert sorted(store.keys()) == ["A", "B"]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
    def test_file_modes_match_super_cli(self, tmp_path):
        store = FileKeystore(tmp_path)
        store.set("K", "v")
        assert stat.S_IMODE(os.stat(tmp_path).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(tmp_path / "keystore.key").st_mode) == 0o600
        assert stat.S_IMODE(os.stat(tmp_path / "keystore.enc").st_mode) == 0o600

    @pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
    def test_existing_store_files_are_resecured(self, tmp_path):
        store = FileKeystore(tmp_path)
        store.set("K", "v")
        with secret_keystore._store_lock(tmp_path):
            pass

        os.chmod(tmp_path, 0o755)
        os.chmod(tmp_path / "keystore.key", 0o644)
        os.chmod(tmp_path / "keystore.enc", 0o644)
        os.chmod(tmp_path / "keystore.lock", 0o644)

        reopened = FileKeystore(tmp_path)
        assert reopened.get("K") == "v"
        assert stat.S_IMODE(os.stat(tmp_path).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(tmp_path / "keystore.key").st_mode) == 0o600
        assert stat.S_IMODE(os.stat(tmp_path / "keystore.enc").st_mode) == 0o600
        assert stat.S_IMODE(os.stat(tmp_path / "keystore.lock").st_mode) == 0o600

    @pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
    def test_existing_key_is_resecured_without_ciphertext(self, tmp_path):
        key_path = tmp_path / "keystore.key"
        key_path.write_bytes(b"\0" * 32)
        os.chmod(key_path, 0o644)

        assert FileKeystore(tmp_path).get("missing") is None
        assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600

    @pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
    def test_raises_when_private_permissions_cannot_be_established(self, tmp_path):
        with mock.patch(
            "hermes_cli.secret_keystore.os.chmod", side_effect=OSError("read-only")
        ):
            with pytest.raises(KeystoreError, match="cannot secure permissions"):
                FileKeystore(tmp_path)

    def test_corrupt_key_file_raises(self, tmp_path):
        FileKeystore(tmp_path).set("K", "v")
        (tmp_path / "keystore.key").write_bytes(b"too-short")
        with pytest.raises(KeystoreError, match="expected 32 bytes"):
            FileKeystore(tmp_path).get("K")

    def test_wrong_key_raises_rather_than_returning_garbage(self, tmp_path):
        FileKeystore(tmp_path).set("K", "v")
        (tmp_path / "keystore.key").write_bytes(b"\x00" * 32)
        with pytest.raises(KeystoreError, match="cannot decrypt"):
            FileKeystore(tmp_path).get("K")

    def test_ciphertext_without_key_raises_without_creating_a_new_key(self, tmp_path):
        store = FileKeystore(tmp_path)
        store.set("K", "v")
        key_path = tmp_path / "keystore.key"
        key_path.unlink()

        with pytest.raises(KeystoreError, match="missing encryption key"):
            store.get("K")
        assert not key_path.exists()

    def test_interrupted_atomic_replace_preserves_previous_ciphertext(self, tmp_path):
        """Direct truncation destroys the last good store when a write fails."""
        store = FileKeystore(tmp_path)
        store.set("existing", "survives")

        with mock.patch(
            "hermes_cli.secret_keystore.os.replace", side_effect=OSError("disk full")
        ):
            with pytest.raises(OSError, match="disk full"):
                store.set("new", "not-committed")

        reopened = FileKeystore(tmp_path)
        assert reopened.get("existing") == "survives"
        assert reopened.get("new") is None

    def test_concurrent_processes_do_not_lose_updates_or_split_the_key(self, tmp_path):
        """Hermes CLI, gateway and desktop may write one profile concurrently."""
        ctx = multiprocessing.get_context("spawn")
        start = ctx.Event()
        workers = [
            ctx.Process(
                target=_concurrent_file_writer,
                args=(str(tmp_path), f"p{worker}", start, 12),
            )
            for worker in range(4)
        ]
        for worker in workers:
            worker.start()
        try:
            start.set()
            for worker in workers:
                worker.join(timeout=20)
                assert not worker.is_alive(), "keystore transaction deadlocked"
                assert worker.exitcode == 0
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=5)

        store = FileKeystore(tmp_path)
        assert len(store.keys()) == 48
        for worker in range(4):
            for index in range(12):
                assert store.get(f"p{worker}-{index}") == f"value-p{worker}-{index}"

    def test_windows_initializes_lock_sentinel_only_after_acquiring_lock(self, tmp_path):
        lock_path = tmp_path / "keystore.lock"
        fake_msvcrt = mock.Mock(LK_LOCK=1, LK_UNLCK=2)

        def assert_lock_precedes_initialization(_fd, mode, _count):
            if mode == fake_msvcrt.LK_LOCK:
                assert lock_path.read_bytes() == b""

        fake_msvcrt.locking.side_effect = assert_lock_precedes_initialization
        with (
            mock.patch("hermes_cli.secret_keystore.os.name", "nt"),
            mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}),
        ):
            with secret_keystore._store_lock(tmp_path):
                assert lock_path.read_bytes() == b"\0"


class TestContainerKeyPersistence:
    """Decision D4: an ephemeral key in a container is refused loudly.

    Generating one silently is the worst available outcome -- every secret
    written under it becomes unreadable at the next restart, and the symptom
    ("my credentials vanished") gives no hint of the cause.
    """

    def test_new_key_in_a_container_is_refused(self, tmp_path):
        with mock.patch("hermes_cli.secret_keystore._in_container", return_value=True):
            with pytest.raises(KeystoreError, match="persistent volume"):
                FileKeystore(tmp_path).set("K", "v")

    def test_refusal_leaves_no_key_file_behind(self, tmp_path):
        with mock.patch("hermes_cli.secret_keystore._in_container", return_value=True):
            with pytest.raises(KeystoreError):
                FileKeystore(tmp_path).set("K", "v")
        assert not (tmp_path / "keystore.key").exists()

    def test_an_existing_key_in_a_container_is_fine(self, tmp_path):
        """A mounted volume with a key already on it is the supported setup —
        the refusal is about *creating* a key, not about containers."""
        FileKeystore(tmp_path).set("K", "v")           # key created outside the container
        with mock.patch("hermes_cli.secret_keystore._in_container", return_value=True):
            assert FileKeystore(tmp_path).get("K") == "v"

    def test_outside_a_container_a_new_key_is_created(self, tmp_path):
        with mock.patch("hermes_cli.secret_keystore._in_container", return_value=False):
            FileKeystore(tmp_path).set("K", "v")
        assert (tmp_path / "keystore.key").exists()


class _FakeKeyring:
    """In-memory stand-in for the keyring module."""

    def __init__(self, fail=False):
        self.store = {}
        self.fail = fail

    def get_password(self, service, name):
        if self.fail:
            raise RuntimeError("no backend")
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        if self.fail:
            raise RuntimeError("no backend")
        self.store[(service, name)] = value

    def delete_password(self, service, name):
        if self.fail:
            raise RuntimeError("no backend")
        self.store.pop((service, name), None)


class TestOSKeystore:
    def test_round_trip(self):
        fake = _FakeKeyring()
        with mock.patch("hermes_cli.secret_keystore.keyring", fake):
            store = OSKeystore()
            store.set("K", "v")
            assert store.get("K") == "v"

    def test_uses_namespaced_service_name(self):
        fake = _FakeKeyring()
        with mock.patch("hermes_cli.secret_keystore.keyring", fake):
            OSKeystore().set("K", "v")
        assert len(fake.store) == 1
        service, account_name = next(iter(fake.store))
        assert service == SERVICE_NAME
        assert account_name != "K"

    def test_backend_failure_raises_keystore_error(self):
        with mock.patch("hermes_cli.secret_keystore.keyring", _FakeKeyring(fail=True)):
            with pytest.raises(KeystoreError):
                OSKeystore().set("K", "v")


class TestProbe:
    def test_probe_true_when_round_trip_works(self):
        with mock.patch("hermes_cli.secret_keystore.keyring", _FakeKeyring()):
            assert probe_os_keystore() is True

    def test_probe_false_when_backend_unavailable(self):
        with mock.patch("hermes_cli.secret_keystore.keyring", _FakeKeyring(fail=True)):
            assert probe_os_keystore() is False

    def test_probe_false_when_keyring_not_installed(self):
        with mock.patch("hermes_cli.secret_keystore.keyring", None):
            assert probe_os_keystore() is False

    def test_probe_leaves_no_residue(self):
        fake = _FakeKeyring()
        with mock.patch("hermes_cli.secret_keystore.keyring", fake):
            probe_os_keystore()
        assert fake.store == {}

    def test_probe_false_when_cleanup_is_refused(self):
        fake = _FakeKeyring()

        def refuse_delete(service, name):
            raise RuntimeError("delete refused")

        fake.delete_password = refuse_delete
        with mock.patch("hermes_cli.secret_keystore.keyring", fake):
            assert probe_os_keystore() is False

    def test_probe_stays_true_when_cleanup_reports_already_absent(self):
        fake = _FakeKeyring()

        def report_already_absent(service, name):
            fake.store.pop((service, name), None)
            raise secret_keystore._PasswordDeleteError("already absent")

        fake.delete_password = report_already_absent
        with mock.patch("hermes_cli.secret_keystore.keyring", fake):
            assert probe_os_keystore() is True
        assert fake.store == {}

    def test_probe_false_when_round_trip_value_mismatches(self):
        fake = _FakeKeyring()
        fake.get_password = lambda service, name: "wrong-value"
        with mock.patch("hermes_cli.secret_keystore.keyring", fake):
            assert probe_os_keystore() is False

    def test_probe_gives_up_on_a_hanging_backend(self):
        """The failure mode that a try/except cannot catch.

        A locked Linux keyring or a D-Bus Secret Service that never answers
        blocks inside keyring rather than raising. The Global Constraints
        require probing to "fail fast to the fallback rather than hang", and
        an exception handler does not deliver that.

        The worker is released and joined INSIDE the patch. An earlier draft
        exited the `with` block first, which restored the real keyring module
        while the abandoned worker was still mid-round-trip -- so its next
        call resolved the real backend and touched the developer's actual
        keychain. Releasing first keeps the fake in place for the whole life
        of the thread.
        """
        import threading

        released = threading.Event()
        finished = threading.Event()

        class _HangingKeyring:
            def set_password(self, service, name, value):
                released.wait(timeout=10)   # unblocked by the test, not the probe

            def get_password(self, service, name):
                released.wait(timeout=10)
                return _PROBE_VALUE

            def delete_password(self, service, name):
                finished.set()

        with mock.patch("hermes_cli.secret_keystore.keyring", _HangingKeyring()):
            start = time.monotonic()
            result = probe_os_keystore(timeout_seconds=0.25)
            elapsed = time.monotonic() - start

            assert result is False
            assert elapsed < 5.0, (
                f"probe blocked for {elapsed:.1f}s instead of failing fast"
            )

            # Let the abandoned worker finish against the fake, then wait for
            # it, so no thread outlives the patch.
            released.set()
            assert finished.wait(timeout=5.0), "abandoned worker never completed"

    def test_probe_timeout_is_configurable_and_defaulted(self):
        import inspect

        signature = inspect.signature(probe_os_keystore)
        assert signature.parameters["timeout_seconds"].default == PROBE_TIMEOUT_SECONDS


import hermes_cli.secret_keystore as sk


@pytest.fixture(autouse=True)
def _reset_backend_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    sk.reset_backend_cache()
    yield
    sk.reset_backend_cache()


class TestBackendSelection:
    def test_prefers_os_when_probe_succeeds(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        with mock.patch.object(sk, "keyring", _FakeKeyring()):
            assert sk.get_backend().name == "os"

    def test_falls_back_to_file_when_probe_fails(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        with mock.patch.object(sk, "keyring", _FakeKeyring(fail=True)):
            assert sk.get_backend().name == "file"

    def test_env_override_forces_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
        with mock.patch.object(sk, "keyring", _FakeKeyring()):
            assert sk.get_backend().name == "file"

    def test_env_override_off_disables_keystore(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "off")
        assert sk.get_backend() is None
        assert sk.get_secret("K") is None

    def test_probe_runs_once_per_process(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        calls = []
        with mock.patch.object(sk, "keyring", _FakeKeyring()):
            with mock.patch.object(
                sk,
                "probe_os_keystore",
                side_effect=lambda **_kwargs: calls.append(1) or True,
            ):
                sk.get_backend()
                sk.get_backend()
        assert len(calls) == 1


class TestModuleLevelAPI:
    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
        sk.set_secret("HERMES_PLUGIN_X_PAT", "tok")
        assert sk.get_secret("HERMES_PLUGIN_X_PAT") == "tok"

    def test_get_secret_never_raises_on_backend_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        broken = mock.Mock()
        broken.name = "os"
        broken.get.side_effect = KeystoreError("boom")
        with mock.patch.object(sk, "get_backend", return_value=broken):
            assert sk.get_secret("K") is None

    def test_get_secret_never_raises_on_unexpected_backend_failure(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        broken = mock.Mock()
        broken.name = "os"
        broken.get.side_effect = RuntimeError("unexpected backend failure")
        with mock.patch.object(sk, "get_backend", return_value=broken):
            assert sk.get_secret("K") is None

    def test_set_secret_raises_on_backend_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        broken = mock.Mock()
        broken.name = "os"
        broken.set.side_effect = KeystoreError("boom")
        with mock.patch.object(sk, "get_backend", return_value=broken):
            with pytest.raises(KeystoreError):
                sk.set_secret("K", "v")

    def test_set_secret_raises_when_keystore_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "off")
        with pytest.raises(KeystoreError):
            sk.set_secret("K", "v")

    def test_set_secret_normalizes_an_ordinary_backend_exception(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        broken = mock.Mock()
        broken.set.side_effect = PermissionError("refused")
        with mock.patch.object(sk, "get_backend", return_value=broken):
            with pytest.raises(KeystoreError, match="write failed"):
                sk.set_secret("K", "v")

    def test_delete_secret_normalizes_an_ordinary_backend_exception(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        broken = mock.Mock()
        broken.delete.side_effect = OSError("unavailable")
        with mock.patch.object(sk, "get_backend", return_value=broken):
            with pytest.raises(KeystoreError, match="delete failed"):
                sk.delete_secret("K")


class TestProfileIsolation:
    def test_os_accounts_are_isolated_by_canonical_profile(self, tmp_path, monkeypatch):
        fake = _FakeKeyring()
        first = tmp_path / "profiles" / "one" / ".." / "one"
        second = tmp_path / "profiles" / "two"
        with mock.patch.object(sk, "keyring", fake):
            monkeypatch.setenv("HERMES_HOME", str(first))
            OSKeystore().set("HERMES_PLUGIN_SHARED_TOKEN", "one")

            monkeypatch.setenv("HERMES_HOME", str(second))
            assert OSKeystore().get("HERMES_PLUGIN_SHARED_TOKEN") is None
            OSKeystore().set("HERMES_PLUGIN_SHARED_TOKEN", "two")

            monkeypatch.setenv("HERMES_HOME", str(first))
            assert OSKeystore().get("HERMES_PLUGIN_SHARED_TOKEN") == "one"
            monkeypatch.setenv("HERMES_HOME", str(second))
            OSKeystore().delete("HERMES_PLUGIN_SHARED_TOKEN")
            monkeypatch.setenv("HERMES_HOME", str(first))
            assert OSKeystore().get("HERMES_PLUGIN_SHARED_TOKEN") == "one"

        assert len(fake.store) == 1
        assert all(
            name != "HERMES_PLUGIN_SHARED_TOKEN" for _service, name in fake.store
        )

    def test_file_backend_rebinds_when_profile_changes_without_manual_reset(
        self, tmp_path, monkeypatch
    ):
        first = tmp_path / "one"
        second = tmp_path / "two"
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "file")
        monkeypatch.setenv("HERMES_HOME", str(first))
        sk.set_secret("K", "one")
        first_backend = sk.get_backend()
        assert sk.get_backend() is first_backend

        monkeypatch.setenv("HERMES_HOME", str(second))
        assert sk.get_secret("K") is None
        assert sk.get_backend() is not first_backend
        sk.set_secret("K", "two")

        monkeypatch.setenv("HERMES_HOME", str(first))
        assert sk.get_secret("K") == "one"

    def test_os_backend_rebinds_when_profile_changes_without_manual_reset(
        self, tmp_path, monkeypatch
    ):
        first = tmp_path / "one"
        second = tmp_path / "two"
        fake = _FakeKeyring()
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "os")
        with mock.patch.object(sk, "keyring", fake):
            monkeypatch.setenv("HERMES_HOME", str(first))
            sk.set_secret("K", "one")
            first_backend = sk.get_backend()
            assert sk.get_backend() is first_backend

            monkeypatch.setenv("HERMES_HOME", str(second))
            assert sk.get_secret("K") is None
            assert sk.get_backend() is not first_backend
            sk.set_secret("K", "two")

            monkeypatch.setenv("HERMES_HOME", str(first))
            assert sk.get_secret("K") == "one"


class TestBatchWrites:
    def test_file_batch_persists_all_values(self, tmp_path):
        store = FileKeystore(tmp_path)

        store.set_many({"first": "one", "second": "two"})

        assert store.get("first") == "one"
        assert store.get("second") == "two"

    def test_os_batch_failure_rolls_back_earlier_successes(self):
        fake = _FakeKeyring()
        with mock.patch.object(sk, "keyring", fake):
            store = OSKeystore()
            store.set("first", "old-first")
            store.set("second", "old-second")
            original_set = fake.set_password
            calls = 0

            def fail_second_set(service, name, value):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("write refused")
                original_set(service, name, value)

            fake.set_password = fail_second_set
            with pytest.raises(KeystoreError, match="state restored"):
                store.set_many({"first": "new-first", "second": "new-second"})

            assert store.get("first") == "old-first"
            assert store.get("second") == "old-second"

    def test_os_batch_rollback_failure_reports_outcome_uncertainty(self):
        fake = _FakeKeyring()
        with mock.patch.object(sk, "keyring", fake):
            store = OSKeystore()
            store.set("first", "old-first")
            store.set("second", "old-second")
            original_set = fake.set_password
            calls = 0

            def fail_write_and_rollback(service, name, value):
                nonlocal calls
                calls += 1
                if calls in {2, 3}:
                    raise OSError("write refused")
                original_set(service, name, value)

            fake.set_password = fail_write_and_rollback
            with pytest.raises(KeystoreError, match="rollback failed.*uncertain"):
                store.set_many({"first": "new-first", "second": "new-second"})

    def test_os_batch_restores_the_current_key_when_its_write_commits_then_raises(self):
        fake = _FakeKeyring()
        with mock.patch.object(sk, "keyring", fake):
            store = OSKeystore()
            store.set("first", "old-first")
            store.set("second", "old-second")
            original_set = fake.set_password
            calls = 0

            def commit_then_raise(service, name, value):
                nonlocal calls
                calls += 1
                original_set(service, name, value)
                if calls == 2:
                    raise OSError("reply lost after commit")

            fake.set_password = commit_then_raise
            with pytest.raises(KeystoreError, match="state restored"):
                store.set_many({"first": "new-first", "second": "new-second"})

            assert store.get("first") == "old-first"
            assert store.get("second") == "old-second"

    def test_os_batch_rollback_cannot_overwrite_a_concurrent_successful_batch(self):
        import threading

        fake = _FakeKeyring()
        entered_failure = threading.Event()
        release_failure = threading.Event()
        second_started = threading.Event()
        second_done = threading.Event()
        results = []
        with mock.patch.object(sk, "keyring", fake):
            store = OSKeystore()
            store.set("shared", "old")
            original_set = fake.set_password
            calls = 0

            def fail_after_first_mutation(service, name, value):
                nonlocal calls
                calls += 1
                if calls == 2:
                    entered_failure.set()
                    assert release_failure.wait(timeout=5)
                    raise OSError("second field refused")
                original_set(service, name, value)

            fake.set_password = fail_after_first_mutation

            def failing_batch():
                with pytest.raises(KeystoreError, match="batch write failed"):
                    store.set_many({"shared": "first", "reject": "value"})

            def successful_batch():
                second_started.set()
                store.set_many({"shared": "second"})
                results.append(store.get("shared"))
                second_done.set()

            first = threading.Thread(target=failing_batch)
            second = threading.Thread(target=successful_batch)
            first.start()
            assert entered_failure.wait(timeout=5)
            second.start()
            assert second_started.wait(timeout=5)
            release_failure.set()
            first.join(timeout=5)
            second.join(timeout=5)
            assert not first.is_alive()
            assert not second.is_alive()
            assert store.get("shared") == "second"

        assert second_done.is_set()
        assert results == ["second"]


class TestProbeConcurrency:
    def test_concurrent_probes_use_distinct_accounts_and_clean_up_exactly(self):
        import threading

        class ProbeKeyring(_FakeKeyring):
            def __init__(self):
                super().__init__()
                self.accounts = []
                self.deleted = []
                self.barrier = threading.Barrier(2)
                self.finished = threading.Event()
                self._completed = 0
                self._lock = threading.Lock()

            def set_password(self, service, name, value):
                self.accounts.append(name)
                super().set_password(service, name, value)
                self.barrier.wait(timeout=5)

            def delete_password(self, service, name):
                self.deleted.append(name)
                super().delete_password(service, name)
                with self._lock:
                    self._completed += 1
                    if self._completed == 2:
                        self.finished.set()

        fake = ProbeKeyring()
        results = []
        with mock.patch.object(sk, "keyring", fake):
            try:
                threads = [
                    threading.Thread(
                        target=lambda: results.append(probe_os_keystore())
                    )
                    for _ in range(2)
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=5)
                    assert not thread.is_alive()
            finally:
                fake.barrier.abort()
                assert fake.finished.wait(timeout=5), (
                    "probe workers outlived the fake-keyring patch"
                )

        assert results == [True, True]
        assert len(set(fake.accounts)) == 2
        assert sorted(fake.deleted) == sorted(fake.accounts)
        assert fake.store == {}


class TestBoundedReads:
    def test_late_timeout_from_detached_state_cannot_poison_new_lifecycle(
        self, tmp_path
    ):
        import threading

        from hermes_constants import (
            reset_hermes_home_override,
            set_hermes_home_override,
        )

        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        profile_token = set_hermes_home_override(tmp_path / "profile")
        try:
            old_store = OSKeystore()
        finally:
            reset_hermes_home_override(profile_token)
        profile_identity = old_store._profile_identity
        old_account = old_store._account_name("K")
        reads = 0

        class LateKeyring:
            def get_password(self, _service, name):
                nonlocal reads
                if name == old_account:
                    reads += 1
                    if reads == 1:
                        entered.set()
                        release.wait(timeout=5)
                        finished.set()
                        return "late"
                return "fresh"

        reader_errors = []
        with mock.patch.object(sk, "keyring", LateKeyring()):
            try:
                with mock.patch.object(sk, "PROBE_TIMEOUT_SECONDS", 0.2):
                    def read_old():
                        try:
                            old_store.get("K")
                        except KeystoreError as exc:
                            reader_errors.append(exc)

                    reader = threading.Thread(target=read_old)
                    reader.start()
                    assert entered.wait(timeout=5)

                    resetter = threading.Thread(target=sk.reset_backend_cache)
                    resetter.start()
                    for _ in range(100):
                        if profile_identity not in sk._PROFILE_STATES:
                            break
                        threading.Event().wait(0.01)
                    assert profile_identity not in sk._PROFILE_STATES
                    new_store = OSKeystore(profile_identity)

                    reader.join(timeout=5)
                    resetter.join(timeout=5)
                    assert not reader.is_alive()
                    assert not resetter.is_alive()
                    assert len(reader_errors) == 1
                    assert "timed out" in str(reader_errors[0])
                    assert new_store._state.healthy is True
                    assert new_store.get("K") == "fresh"
            finally:
                release.set()
                assert finished.wait(timeout=5)

    def test_profile_b_selection_does_not_reset_profile_a_timeout_health(
        self, tmp_path
    ):
        import threading

        from hermes_constants import (
            reset_hermes_home_override,
            set_hermes_home_override,
        )

        release_a = threading.Event()
        finished_a = threading.Event()
        token_a = set_hermes_home_override(tmp_path / "a")
        try:
            store_a = OSKeystore()
        finally:
            reset_hermes_home_override(token_a)
        token_b = set_hermes_home_override(tmp_path / "b")
        try:
            store_b = OSKeystore()
        finally:
            reset_hermes_home_override(token_b)

        account_a = store_a._account_name("K")

        class ProfileKeyring:
            def get_password(self, _service, name):
                if name == account_a:
                    release_a.wait(timeout=5)
                    finished_a.set()
                    return "late"
                return "profile-b"

        with mock.patch.object(sk, "keyring", ProfileKeyring()):
            with mock.patch.object(sk, "PROBE_TIMEOUT_SECONDS", 0.2):
                with pytest.raises(KeystoreError, match="timed out"):
                    store_a.get("K")
                assert store_b.get("K") == "profile-b"
                with pytest.raises(KeystoreError, match="stopped responding"):
                    store_a.get("K")
            release_a.set()
            assert finished_a.wait(timeout=5)

    def test_a_blocking_read_times_out_rather_than_hanging(self):
        """The probe passing does not make later reads safe: a keychain can
        be unlocked at startup and re-locked by screen-lock mid-process."""
        import threading
        import time as _time

        import hermes_cli.secret_keystore as sk

        released = threading.Event()
        finished = threading.Event()

        class _HangingKeyring:
            def get_password(self, service, name):
                released.wait(timeout=10)
                finished.set()
                return "late"

        sk.reset_backend_cache()
        with mock.patch.object(sk, "keyring", _HangingKeyring()):
            store = sk.OSKeystore()
            start = _time.monotonic()
            with pytest.raises(sk.KeystoreError, match="timed out"):
                store.get("K")
            elapsed = _time.monotonic() - start
            assert elapsed < 5.0, f"read blocked for {elapsed:.1f}s"
            released.set()
            assert finished.wait(timeout=5.0), "abandoned worker never completed"

    def test_concurrent_reads_spawn_at_most_one_worker(self, tmp_path, monkeypatch):
        """The latch must cover concurrent callers, not only a serial loop."""
        import threading

        import hermes_cli.secret_keystore as sk

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "os")
        released = threading.Event()
        finished = threading.Event()
        callers_ready = threading.Barrier(6)
        calls = []

        class _HangingKeyring:
            def get_password(self, service, name):
                calls.append(name)
                released.wait(timeout=10)
                finished.set()

        results = []

        def _resolve(index):
            callers_ready.wait(timeout=5)
            results.append(sk.get_secret(f"K{index}"))

        sk.reset_backend_cache()
        with mock.patch.object(sk, "keyring", _HangingKeyring()):
            with mock.patch.object(sk, "PROBE_TIMEOUT_SECONDS", 0.2):
                assert sk.get_backend().name == "os"
                callers = [
                    threading.Thread(target=_resolve, args=(index,))
                    for index in range(5)
                ]
                for caller in callers:
                    caller.start()
                callers_ready.wait(timeout=5)
                for caller in callers:
                    caller.join(timeout=5)
                    assert not caller.is_alive()

            # Exactly one call reached keyring. The other four waited for the
            # atomic check/spawn transition, then saw the false latch.
            assert len(calls) == 1
            assert results == [None] * 5
            released.set()
            assert finished.wait(timeout=5), "abandoned worker never completed"

    def test_a_forced_os_mode_is_not_demoted(self, tmp_path, monkeypatch):
        """"os" pins the tier. Demoting would move reads to a store that does
        not hold the operator's secrets, which looks like data loss."""
        import threading

        import hermes_cli.secret_keystore as sk

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "os")
        released = threading.Event()
        finished = threading.Event()

        class _HangingKeyring:
            def get_password(self, service, name):
                released.wait(timeout=10)
                finished.set()

        sk.reset_backend_cache()
        with mock.patch.object(sk, "keyring", _HangingKeyring()):
            assert sk.get_backend().name == "os"
            assert sk.get_secret("K") is None       # timed out, swallowed
            assert sk.get_backend().name == "os"    # tier NOT swapped
            assert sk._profile_state(sk._active_profile_identity()).healthy is False
            released.set()
            assert finished.wait(timeout=5), "abandoned worker never completed"

    def test_a_timed_out_read_demotes_the_backend(self, tmp_path, monkeypatch):
        """One hang is enough evidence. Without demotion, resolving N secrets
        against a wedged keychain abandons N threads and burns N timeouts."""
        import threading

        import hermes_cli.secret_keystore as sk

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "auto")
        released = threading.Event()
        finished = threading.Event()

        class _HangingKeyring:
            def get_password(self, service, name):
                released.wait(timeout=10)
                finished.set()

        sk.reset_backend_cache()
        with mock.patch.object(sk, "keyring", _HangingKeyring()):
            try:
                with mock.patch.object(sk, "probe_os_keystore", return_value=True):
                    assert sk.get_backend().name == "os"
                    assert sk.get_secret("K") is None       # timed out, swallowed
                    assert sk.get_backend().name == "file"  # demoted
            finally:
                released.set()
                assert finished.wait(timeout=5), (
                    "abandoned worker never completed"
                )

    def test_auto_selection_demotes_after_current_mode_changes_to_os(
        self, tmp_path, monkeypatch
    ):
        """The process-cached selection mode, not mutable current config,
        controls whether a timed-out OS read demotes the backend."""
        import threading

        import hermes_cli.secret_keystore as sk

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "auto")
        released = threading.Event()
        finished = threading.Event()

        class _HangingKeyring:
            def get_password(self, service, name):
                released.wait(timeout=10)
                finished.set()

        sk.reset_backend_cache()
        with mock.patch.object(sk, "keyring", _HangingKeyring()):
            try:
                with (
                    mock.patch.object(sk, "probe_os_keystore", return_value=True),
                    mock.patch.object(sk, "PROBE_TIMEOUT_SECONDS", 0.2),
                ):
                    assert sk.get_backend().name == "os"
                    monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "os")
                    assert sk.get_secret("K") is None
                    assert sk.get_backend().name == "file"
            finally:
                released.set()
                assert finished.wait(timeout=5), (
                    "abandoned worker never completed"
                )

    def test_forced_os_selection_stays_pinned_after_current_mode_changes_to_auto(
        self, tmp_path, monkeypatch
    ):
        """A forced OS selection stays pinned for the process even if the
        config source changes before a later read times out."""
        import threading

        import hermes_cli.secret_keystore as sk

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "os")
        released = threading.Event()
        finished = threading.Event()

        class _HangingKeyring:
            def get_password(self, service, name):
                released.wait(timeout=10)
                finished.set()

        sk.reset_backend_cache()
        with mock.patch.object(sk, "keyring", _HangingKeyring()):
            try:
                with mock.patch.object(sk, "PROBE_TIMEOUT_SECONDS", 0.2):
                    assert sk.get_backend().name == "os"
                    monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "auto")
                    assert sk.get_secret("K") is None
                    assert sk.get_backend().name == "os"
                    assert (
                        sk._profile_state(sk._active_profile_identity()).healthy
                        is False
                    )
            finally:
                released.set()
                assert finished.wait(timeout=5), (
                    "abandoned worker never completed"
                )


class TestRevocationFailuresPropagate:
    """Already repaired once. Untested, it regresses silently -- and its
    symptom is a credential the dashboard says is gone that still works."""

    def test_os_delete_raises_when_the_backend_refuses(self):
        import hermes_cli.secret_keystore as sk

        sk.reset_backend_cache()
        broken = mock.Mock()
        broken.delete_password.side_effect = RuntimeError("dbus refused")
        with mock.patch.object(sk, "keyring", broken):
            with pytest.raises(sk.KeystoreError):
                sk.OSKeystore().delete("K")

    def test_os_delete_treats_absent_as_success(self):
        import hermes_cli.secret_keystore as sk

        sk.reset_backend_cache()
        broken = mock.Mock()
        broken.delete_password.side_effect = sk._PasswordDeleteError("no such item")
        with mock.patch.object(sk, "keyring", broken):
            sk.OSKeystore().delete("K")   # must not raise

    def test_module_delete_secret_propagates(self, tmp_path, monkeypatch):
        import hermes_cli.secret_keystore as sk

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        sk.reset_backend_cache()
        broken = mock.Mock()
        broken.name = "os"
        broken.delete.side_effect = sk.KeystoreError("refused")
        with mock.patch.object(sk, "get_backend", return_value=broken):
            with pytest.raises(sk.KeystoreError):
                sk.delete_secret("K")


class TestConfigSetting:
    def test_mode_is_a_settable_config_key(self):
        from hermes_cli.config_defaults import DEFAULT_CONFIG

        assert DEFAULT_CONFIG.get("secret_keystore") == "auto"

    def test_config_key_passes_validation(self):
        from hermes_cli.config import _validate_config_key

        assert _validate_config_key("secret_keystore")[0] is True

    def test_config_yaml_value_is_honoured(self, tmp_path, monkeypatch):
        """The documented path. Earlier tests only exercised the env bridge,
        so a mode read that ignored config.yaml would have passed them all."""
        import hermes_cli.secret_keystore as sk

        monkeypatch.delenv("HERMES_SECRET_KEYSTORE", raising=False)
        monkeypatch.setattr(
            "hermes_cli.config.load_config_readonly",
            lambda *a, **k: {"secret_keystore": "file"},
        )
        sk.reset_backend_cache()
        assert sk.get_backend().name == "file"

    def test_env_bridge_overrides_config(self, tmp_path, monkeypatch):
        import hermes_cli.secret_keystore as sk

        monkeypatch.setenv("HERMES_SECRET_KEYSTORE", "off")
        monkeypatch.setattr(
            "hermes_cli.config.load_config_readonly",
            lambda *a, **k: {"secret_keystore": "file"},
        )
        sk.reset_backend_cache()
        assert sk.get_backend() is None

    def test_a_typo_in_config_falls_back_to_auto(self, monkeypatch):
        """This runs on the credential-resolution path; a typo must not make
        every plugin unreadable."""
        import hermes_cli.secret_keystore as sk

        monkeypatch.delenv("HERMES_SECRET_KEYSTORE", raising=False)
        monkeypatch.setattr(
            "hermes_cli.config.load_config_readonly",
            lambda *a, **k: {"secret_keystore": "fille"},
        )
        sk.reset_backend_cache()
        # A typo resolves to "auto", and "auto" probes the OS keystore -- so
        # without this patch the test would write a probe entry into the
        # developer's real Keychain, or raise an authorisation prompt in CI.
        with mock.patch.object(sk, "probe_os_keystore", return_value=False):
            assert sk.get_backend() is not None
            assert sk.get_backend().name == "file"
