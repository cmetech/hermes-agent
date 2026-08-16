"""Tests for hermes_cli.secret_keystore — two-tier plugin secret storage."""

import multiprocessing
import os
import stat
import sys
from pathlib import Path
from unittest import mock

import pytest

from hermes_cli.secret_keystore import FileKeystore, KeystoreError


def _concurrent_file_writer(root, prefix, start, count):
    """Spawn-safe worker used by the real cross-process transaction test."""
    store = FileKeystore(Path(root))
    start.wait(timeout=10)
    for index in range(count):
        store.set(f"{prefix}-{index}", f"value-{prefix}-{index}")


class TestFileKeystore:
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
