"""Real Docker volume persistence for the encrypted secret file store."""

from __future__ import annotations

import subprocess
import uuid


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


def test_new_key_survives_restart_on_named_volume(built_image: str) -> None:
    volume = f"hermes-secret-persistence-{uuid.uuid4().hex}"
    subprocess.run(
        ["docker", "volume", "create", volume],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
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
    finally:
        subprocess.run(
            ["docker", "volume", "rm", "-f", volume],
            capture_output=True,
            text=True,
            timeout=30,
        )
