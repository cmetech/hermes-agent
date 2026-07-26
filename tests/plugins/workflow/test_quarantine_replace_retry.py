"""Damaged-index quarantine must survive a Windows file lock.

`_preserve_damaged_index` renames a corrupt admission index aside so a healthy
one can be recreated. POSIX renames open files happily; Windows refuses with
PermissionError (WinError 32) while any handle is open, so on Windows this
turned a recoverable corrupt index into an unhandled PermissionError -- and it
only ever fires on the unhealthy path, which is the worst time to fail.

These tests simulate the Windows behaviour by making os.replace raise, so they
are meaningful on every platform. Without that, POSIX would pass vacuously,
which is exactly why the bug reached CI unnoticed.
"""
from __future__ import annotations

import os

import pytest

import plugins.workflow.store as store_module
from plugins.workflow.store import _replace_with_retry


def test_replace_retries_until_the_handle_is_released(tmp_path, monkeypatch) -> None:
    source = tmp_path / "admission.sqlite3"
    target = tmp_path / "quarantined.sqlite3"
    source.write_bytes(b"payload")
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 4:
            raise PermissionError(
                32, "The process cannot access the file because it is being "
                "used by another process"
            )
        return real_replace(src, dst)

    monkeypatch.setattr(store_module.os, "replace", flaky)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    _replace_with_retry(source, target)

    assert calls["n"] == 4
    assert target.read_bytes() == b"payload"
    assert not source.exists()


def test_replace_collects_garbage_before_backing_off(tmp_path, monkeypatch) -> None:
    """A connection reachable only from a retained traceback must be reaped."""
    source = tmp_path / "admission.sqlite3"
    target = tmp_path / "quarantined.sqlite3"
    source.write_bytes(b"payload")
    real_replace = os.replace
    order: list[str] = []
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        order.append("replace")
        if calls["n"] == 1:
            raise PermissionError(32, "locked")
        return real_replace(src, dst)

    monkeypatch.setattr(store_module.os, "replace", flaky)
    monkeypatch.setattr(store_module.gc, "collect", lambda: order.append("gc"))
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    _replace_with_retry(source, target)

    # gc must run before the first backoff, not after every failure.
    assert order == ["replace", "gc", "replace"]


def test_replace_still_raises_when_the_lock_never_clears(tmp_path, monkeypatch) -> None:
    source = tmp_path / "admission.sqlite3"
    source.write_bytes(b"payload")

    def always_locked(_src, _dst):
        raise PermissionError(32, "locked forever")

    monkeypatch.setattr(store_module.os, "replace", always_locked)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    # A permanently held handle must still surface, not hang or pass silently.
    with pytest.raises(PermissionError):
        _replace_with_retry(source, tmp_path / "quarantined.sqlite3")
