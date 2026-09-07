"""Build/browser proof from an exact local commit without generating into its source tree."""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile


def dependency_view(source: Path, target: Path) -> None:
    """Reuse already validated packages, but never reuse mutable tool caches."""
    target.mkdir()
    caches = {".vite", ".vite-temp", ".cache"}
    for entry in source.resolve(strict=True).iterdir():
        if entry.name not in caches and not entry.name.startswith(
            ".workflow-merge-gate-owner."
        ):
            (target / entry.name).symlink_to(entry)
    for name in caches:
        (target / name).mkdir()


def main() -> int:
    source = Path(sys.argv[1]).resolve(strict=True)
    source_sha = sys.argv[2]
    root = Path(tempfile.mkdtemp(prefix="hermes-workflow-gate-build-")).resolve()
    identity = root.stat()
    marker = root / ".owner"
    token = secrets.token_hex(32)
    marker.write_text(token, encoding="ascii")
    child: subprocess.Popen | None = None
    interrupted = 0

    def on_signal(number, _frame):
        nonlocal interrupted
        interrupted = number
        if child is not None:
            try:
                os.killpg(child.pid, number)
            except ProcessLookupError:
                pass

    for number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(number, on_signal)

    def run(*args: str, cwd: Path = source) -> None:
        nonlocal child
        if interrupted:
            raise InterruptedError
        child = subprocess.Popen(args, cwd=cwd, start_new_session=True)
        try:
            while True:
                try:
                    code = child.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    if interrupted:
                        try:
                            child.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL)
                            child.wait()
                        raise InterruptedError
        finally:
            child = None
        if interrupted:
            raise InterruptedError
        if code:
            raise subprocess.CalledProcessError(code, args)

    status = 0
    try:
        checkout = root / "source"
        # Local objects only; never fetch, create commits, or change source refs/index.
        run(
            "git",
            "clone",
            "--local",
            "--shared",
            "--no-checkout",
            "--no-hardlinks",
            "--quiet",
            "--",
            str(source),
            str(checkout),
        )
        cloned_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
        ).strip()
        if cloned_sha != source_sha:
            raise RuntimeError("source HEAD changed before isolated build")
        # Archive every committed byte, including export-ignored files, with no
        # substitutions. Only the private clone's attributes/index are written.
        (checkout / ".git/info/attributes").write_text(
            "* -export-ignore -export-subst\n"
        )
        archive = root / "source.tar"
        run(
            "git",
            "archive",
            "--format=tar",
            "-o",
            str(archive),
            source_sha,
            cwd=checkout,
        )
        with tarfile.open(archive) as contents:
            contents.extractall(checkout, filter="data")
        run("git", "read-tree", source_sha, cwd=checkout)
        run("git", "diff", "--exit-code", "HEAD", "--", cwd=checkout)
        desktop = checkout / "apps/desktop"
        desktop.mkdir(parents=True, exist_ok=True)
        dependency_view(source / "node_modules", checkout / "node_modules")
        dependency_view(source / "apps/desktop/node_modules", desktop / "node_modules")
        if (source / ".venv").is_dir():
            (checkout / ".venv").symlink_to((source / ".venv").resolve())
        print(
            f"Isolated generated build/browser proof from source commit {source_sha}",
            flush=True,
        )
        run("npm", "run", "build", cwd=desktop)
        run(
            "npx",
            "playwright",
            "test",
            "e2e/workflow-marketplace-lifecycle.spec.ts",
            "e2e/workflow-marketplace-layout.spec.ts",
            cwd=desktop,
        )
    except InterruptedError:
        status = 128 + interrupted
    except (
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        tarfile.TarError,
    ) as error:
        print(f"isolated build failed: {error}", file=sys.stderr)
        status = 1
    finally:
        # A private root and exact marker delimit our cleanup authority. A
        # replacement or changed marker is preserved, never guessed/restored.
        try:
            current = root.lstat()
            marker_stat = marker.lstat()
            if not (
                stat.S_ISDIR(current.st_mode)
                and (current.st_dev, current.st_ino)
                == (identity.st_dev, identity.st_ino)
                and stat.S_ISREG(marker_stat.st_mode)
                and marker.read_text(encoding="ascii") == token
            ):
                raise RuntimeError("ownership changed")
            shutil.rmtree(root)
        except (OSError, RuntimeError, UnicodeError) as error:
            print(
                f"isolated build cleanup refused; preserved {root}: {error}",
                file=sys.stderr,
            )
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
