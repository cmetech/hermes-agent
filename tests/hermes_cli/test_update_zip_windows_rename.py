"""Native Windows lock coverage for transactional ZIP directory swaps."""

import ctypes
import errno
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from hermes_cli import update_cmd


@contextmanager
def _held_windows_path(path, *, directory=False):
    """Hold a native read handle without delete sharing; always close it."""
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(
        str(path), 0x80000000, 3, None, 3,
        0x02000000 if directory else 0, None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())

    def release():
        nonlocal handle
        if handle is not None:
            if not kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            handle = None

    try:
        yield release
    finally:
        release()


def _tree(root, contents):
    child = root / "desktop" / "release" / "payload.txt"
    child.parent.mkdir(parents=True)
    child.write_text(contents, encoding="utf-8")
    return child


@pytest.fixture
def rename_attempts(monkeypatch):
    """Observe actual filesystem renames, including their native error codes."""
    real_rename = os.rename
    attempts = []

    def record(src, dst, *args, **kwargs):
        attempt = {"src": Path(src), "dst": Path(dst), "winerror": None}
        attempts.append(attempt)
        try:
            return real_rename(src, dst, *args, **kwargs)
        except OSError as exc:
            attempt["winerror"] = getattr(exc, "winerror", None)
            raise

    monkeypatch.setattr(update_cmd.os, "rename", record)
    return attempts


def _observe_target_backoff(monkeypatch, attempts, target, release=None):
    """Control our held handle; retain real backoff for unrelated host locks."""
    real_sleep = update_cmd._time.sleep
    delays = []

    def sleep(delay):
        if attempts[-1]["src"] == target:
            delays.append(delay)
            if release is not None:
                release()
        else:
            real_sleep(delay)

    monkeypatch.setattr(update_cmd._time, "sleep", sleep)
    return delays


@pytest.mark.windows_only
@pytest.mark.parametrize("phase", ["staging", "destination"])
@pytest.mark.parametrize("lock_kind,expected_error", [("child", 5), ("directory", 32)])
def test_temporary_native_lock_completes_swap(
    tmp_path, monkeypatch, rename_attempts, phase, lock_kind, expected_error,
):
    destination = tmp_path / "apps"
    staging = tmp_path / "apps.hermes-update-staging"
    old_child = _tree(destination, "old desktop")
    new_child = _tree(staging, "new desktop")
    locked_root = staging if phase == "staging" else destination
    locked_path = (
        locked_root if lock_kind == "directory"
        else new_child if phase == "staging" else old_child
    )
    with _held_windows_path(locked_path, directory=lock_kind == "directory") as release:
        delays = _observe_target_backoff(monkeypatch, rename_attempts, locked_root, release)
        update_cmd._commit_staged_replacements([(str(staging), str(destination))])

    assert old_child.read_text(encoding="utf-8") == "new desktop"
    assert not staging.exists()
    assert not Path(f"{destination}.hermes-update-old").exists()
    assert delays == [0.1]
    assert [a["winerror"] for a in rename_attempts if a["src"] == locked_root] == [
        expected_error, None,
    ]


@pytest.mark.windows_only
@pytest.mark.parametrize("phase", ["staging", "destination"])
def test_persistent_native_lock_is_bounded_and_rolls_back_prior_entries(
    tmp_path, monkeypatch, rename_attempts, phase,
):
    first = tmp_path / "first"
    first_staging = tmp_path / "first.hermes-update-staging"
    first_child = _tree(first, "original first")
    _tree(first_staging, "replacement first")
    destination = tmp_path / "apps"
    staging = tmp_path / "apps.hermes-update-staging"
    old_child = _tree(destination, "original desktop")
    new_child = _tree(staging, "replacement desktop")
    locked_root = staging if phase == "staging" else destination
    locked_child = new_child if phase == "staging" else old_child
    delays = _observe_target_backoff(monkeypatch, rename_attempts, locked_root)

    with _held_windows_path(locked_child):
        with pytest.raises(PermissionError) as caught:
            update_cmd._commit_staged_replacements([
                (str(first_staging), str(first)),
                (str(staging), str(destination)),
            ])

    assert caught.value.winerror == 5
    assert delays == [0.1, 0.25, 0.5, 1.0]
    assert [a["winerror"] for a in rename_attempts if a["src"] == locked_root] == [5] * 5
    assert first_child.read_text(encoding="utf-8") == "original first"
    assert old_child.read_text(encoding="utf-8") == "original desktop"
    assert new_child.read_text(encoding="utf-8") == "replacement desktop"
    assert not Path(f"{first}.hermes-update-old").exists()
    assert not Path(f"{destination}.hermes-update-old").exists()


@pytest.mark.windows_only
@pytest.mark.parametrize("release_during_retry", [True, False])
def test_rollback_backup_lock_recovers_or_preserves_backup(
    tmp_path, monkeypatch, rename_attempts, caplog, release_during_retry,
):
    destination = tmp_path / "apps"
    staging = tmp_path / "apps.hermes-update-staging"
    old_child = _tree(destination, "original desktop")
    _tree(staging, "replacement desktop")
    backup = Path(f"{destination}.hermes-update-old")
    missing_staging = tmp_path / "missing-staging"
    other_destination = tmp_path / "other"
    observed_rename = update_cmd.os.rename
    held = None

    def acquire_after_swap(src, dst, *args, **kwargs):
        nonlocal held
        result = observed_rename(src, dst, *args, **kwargs)
        if Path(src) == staging:
            held = _held_windows_path(backup / old_child.relative_to(destination))
            held.__enter__()
        return result

    def release_during_backoff():
        nonlocal held
        if release_during_retry:
            held.__exit__(None, None, None)
            held = None

    monkeypatch.setattr(update_cmd.os, "rename", acquire_after_swap)
    delays = _observe_target_backoff(monkeypatch, rename_attempts, backup, release_during_backoff)
    try:
        with pytest.raises(FileNotFoundError):
            update_cmd._commit_staged_replacements([
                (str(staging), str(destination)),
                (str(missing_staging), str(other_destination)),
            ])
    finally:
        if held is not None:
            held.__exit__(None, None, None)

    backup_errors = [a["winerror"] for a in rename_attempts if a["src"] == backup]
    if release_during_retry:
        assert delays == [0.1]
        assert backup_errors == [5, None]
        assert old_child.read_text(encoding="utf-8") == "original desktop"
        assert not backup.exists()
        assert "rollback failed" not in caplog.text
    else:
        assert delays == [0.1, 0.25, 0.5, 1.0]
        assert backup_errors == [5] * 5
        assert not destination.exists()
        assert (backup / old_child.relative_to(destination)).read_text(encoding="utf-8") == "original desktop"
        assert "rollback failed" in caplog.text
    assert not other_destination.exists()


def test_unrelated_rename_error_is_immediate(tmp_path, monkeypatch, rename_attempts):
    staging = tmp_path / "missing-staging"
    destination = tmp_path / "apps"
    delays = []
    monkeypatch.setattr(update_cmd._time, "sleep", delays.append)

    with pytest.raises(FileNotFoundError):
        update_cmd._commit_staged_replacements([(str(staging), str(destination))])

    assert len(rename_attempts) == 1
    assert delays == []


def _assert_posix_permission_error_is_immediate(tmp_path, monkeypatch):
    attempts = []
    delays = []
    error = PermissionError(errno.EACCES, "permission denied")

    def denied(src, dst):
        attempts.append((src, dst))
        raise error

    monkeypatch.setattr(update_cmd.os, "rename", denied)
    monkeypatch.setattr(update_cmd._time, "sleep", delays.append)
    with pytest.raises(PermissionError) as caught:
        update_cmd._commit_staged_replacements([
            (str(tmp_path / "staging"), str(tmp_path / "apps")),
        ])
    assert caught.value is error
    assert len(attempts) == 1
    assert delays == []


@pytest.mark.linux_only
def test_linux_permission_error_is_not_retried(tmp_path, monkeypatch):
    _assert_posix_permission_error_is_immediate(tmp_path, monkeypatch)


@pytest.mark.macos_only
def test_macos_permission_error_is_not_retried(tmp_path, monkeypatch):
    _assert_posix_permission_error_is_immediate(tmp_path, monkeypatch)
