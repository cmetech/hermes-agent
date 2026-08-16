"""Real Docker volume persistence for the encrypted secret file store."""

from __future__ import annotations

import subprocess
import uuid

import pytest


def _run_probe(image: str, volume: str, source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/opt/hermes/.venv/bin/python",
            "-e",
            "HERMES_CONTAINER=1",
            "-v",
            f"{volume}:/persist",
            image,
            "-c",
            source,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _remove_volume(volume: str, *, active_error: BaseException | None = None) -> None:
    try:
        result = subprocess.run(
            ["docker", "volume", "rm", "-f", volume],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        cleanup_error: BaseException = AssertionError(
            f"failed to remove Docker volume {volume}: {detail}"
        )
    except BaseException as exc:
        cleanup_error = exc
    if active_error is not None:
        active_error.add_note(f"Docker volume cleanup also failed: {cleanup_error}")
        return
    raise cleanup_error


def test_volume_cleanup_failure_is_reported_without_masking_primary(monkeypatch):
    failed = subprocess.CompletedProcess(
        ["docker", "volume", "rm", "-f", "test-volume"],
        1,
        stdout="",
        stderr="volume is busy",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: failed)

    with pytest.raises(AssertionError, match="volume is busy"):
        _remove_volume("test-volume")

    primary = RuntimeError("probe failed")
    _remove_volume("test-volume", active_error=primary)
    assert any("volume is busy" in note for note in primary.__notes__)


def test_new_key_survives_restart_on_named_volume(built_image: str) -> None:
    volume = f"hermes-secret-persistence-{uuid.uuid4().hex}"
    subprocess.run(
        ["docker", "volume", "create", volume],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    active_error: BaseException | None = None
    try:
        write = _run_probe(
            built_image,
            volume,
            "from pathlib import Path; "
            "from hermes_cli.secret_keystore import FileKeystore; "
            "FileKeystore(Path('/persist/secrets')).set('K', 'v')",
        )
        assert write.returncode == 0, write.stderr[-2000:]

        read = _run_probe(
            built_image,
            volume,
            "from pathlib import Path; "
            "from hermes_cli.secret_keystore import FileKeystore; "
            "print(FileKeystore(Path('/persist/secrets')).get('K'))",
        )
        assert read.returncode == 0, read.stderr[-2000:]
        assert read.stdout.strip() == "v"
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        _remove_volume(volume, active_error=active_error)
