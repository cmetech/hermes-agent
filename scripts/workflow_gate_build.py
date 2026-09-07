"""Build/browser proof from an exact local commit without generating into its source tree."""

from __future__ import annotations

import os
import json
from pathlib import Path
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile

# Standalone scripts do not otherwise have the repository on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.managed_process import ManagedProcessTree


class ProcessRunner:
    """Own every launched tree through the repository's job/group boundary."""

    def __init__(
        self,
        *,
        windows,
        subprocess_api=subprocess,
        signal_api=signal,
        which=shutil.which,
        spawn=ManagedProcessTree.spawn,
    ):
        self.windows = windows
        self.api = subprocess_api
        self.signals = signal_api
        self.which = which
        self.spawn = spawn
        self.tree = None
        self.quiescent = True
        self.interrupted = 0

    def interrupt(self, number, _frame):
        self.interrupted = number

    def register_signals(self):
        for name in ("SIGHUP", "SIGINT", "SIGTERM"):
            number = getattr(self.signals, name, None)
            if number is not None:
                self.signals.signal(number, self.interrupt)

    def finish(self):
        if self.tree is not None:
            try:
                self.tree.close()
                self.quiescent = self.tree.reaped is True
            except (OSError, RuntimeError):
                self.quiescent = False
            self.tree = None
        return self.quiescent

    def run(self, *args, cwd, capture=False):
        if self.interrupted:
            raise InterruptedError
        taskkill = self.which("taskkill") if self.windows else None
        if self.windows and not taskkill:
            raise RuntimeError(
                "taskkill is required for owned Windows tree termination"
            )
        options = (
            {
                "creationflags": getattr(self.api, "CREATE_NEW_PROCESS_GROUP", 0x200)
                | getattr(self.api, "CREATE_NO_WINDOW", 0x8000000)
            }
            if self.windows
            else {"start_new_session": True}
        )
        self.tree = self.spawn(
            args,
            cwd=cwd,
            stdin=None,
            stdout=subprocess.PIPE if capture else None,
            stderr=None,
            **options,
        )
        self.quiescent = False
        child = self.tree.process
        output = None
        try:
            while not self.interrupted:
                try:
                    if capture:
                        output, _stderr = child.communicate(timeout=1)
                        code = child.returncode
                    else:
                        code = child.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if self.windows and (self.interrupted or code):
                try:
                    result = self.api.run(
                        [taskkill, "/PID", str(child.pid), "/T", "/F"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                        creationflags=options["creationflags"],
                    )
                    if result.returncode != 0:
                        raise RuntimeError("taskkill did not confirm tree termination")
                    child.wait(timeout=10)
                except (OSError, subprocess.SubprocessError, RuntimeError) as error:
                    # Killing only our direct handle is best effort, never tree proof.
                    try:
                        child.kill()
                    except OSError:
                        pass
                    raise RuntimeError("Windows tree termination failed") from error
            if self.interrupted:
                raise InterruptedError
            if code:
                raise subprocess.CalledProcessError(code, args)
            return output.decode("utf-8") if output is not None else None
        finally:
            if not self.finish():
                raise RuntimeError("owned process tree is not proven quiescent")
            if self.interrupted and sys.exc_info()[0] is None:
                raise InterruptedError


def local_playwright(source):
    """No npm/npx discovery: only the named local package's contained CLI."""
    for modules in (source / "apps/desktop/node_modules", source / "node_modules"):
        package = modules / "@playwright/test"
        if not package.exists():
            continue
        package_root = package.resolve(strict=True)
        if not package_root.is_relative_to(modules.resolve(strict=True)):
            raise RuntimeError("local Playwright package escapes its dependency root")
        manifest_path = package_root / "package.json"
        cli_path = package_root / "cli.js"
        for path in (manifest_path, cli_path):
            if not stat.S_ISREG(path.lstat().st_mode):
                raise RuntimeError(
                    "local Playwright identity must be a regular non-symlink file"
                )
            if not path.resolve(strict=True).is_relative_to(package_root):
                raise RuntimeError("local Playwright identity escapes its package")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("name") != "@playwright/test":
            raise RuntimeError("invalid local Playwright executable")
        return cli_path.resolve(strict=True)
    raise RuntimeError(
        "local Playwright executable is unavailable; no package-runner fallback"
    )


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
    runner = ProcessRunner(windows=os.name == "nt")
    root = identity = marker = token = None
    status = 0
    raw_root = tempfile.mkdtemp(prefix="hermes-workflow-gate-build-")
    try:
        root = Path(raw_root).resolve()
        identity = root.lstat()
        token = secrets.token_hex(32)
        marker = root / ".owner"
        marker.write_text(token, encoding="ascii")
        runner.register_signals()

        def run(*args, cwd=source, capture=False):
            return runner.run(*args, cwd=cwd, capture=capture)

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
        cloned_sha = run("git", "rev-parse", "HEAD", cwd=checkout, capture=True).strip()
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
        playwright = local_playwright(source)
        node = shutil.which("node")
        npm = shutil.which("npm")
        if not node or not npm:
            raise RuntimeError("local node/npm executables are required")
        print(
            f"Isolated generated build/browser proof from source commit {source_sha}",
            flush=True,
        )
        run(npm, "run", "build", cwd=desktop)
        run(
            node,
            str(playwright),
            "test",
            "e2e/workflow-marketplace-lifecycle.spec.ts",
            "e2e/workflow-marketplace-layout.spec.ts",
            cwd=desktop,
        )
    except InterruptedError:
        status = 128 + runner.interrupted
    except (
        OSError,
        RuntimeError,
        ValueError,
        subprocess.SubprocessError,
        tarfile.TarError,
    ) as error:
        print(f"isolated build failed: {error}", file=sys.stderr)
        status = 1
    finally:
        # A private root and exact marker delimit our cleanup authority. A
        # replacement or changed marker is preserved, never guessed/restored.
        try:
            if not runner.finish():
                raise RuntimeError("tree quiescence unconfirmed; preserving workspace")
            if root is None or identity is None:
                raise RuntimeError("root identity unavailable")
            current = root.lstat()
            if not (
                stat.S_ISDIR(current.st_mode)
                and (current.st_dev, current.st_ino)
                == (identity.st_dev, identity.st_ino)
            ):
                raise RuntimeError("ownership changed")
            if marker is None or (not marker.exists() and not marker.is_symlink()):
                root.rmdir()  # setup failed before any marker: remove only if empty
            elif (
                stat.S_ISREG(marker.lstat().st_mode)
                and marker.read_text(encoding="ascii") == token
            ):
                shutil.rmtree(root)
            else:
                raise RuntimeError("ownership marker changed")
        except (OSError, RuntimeError, UnicodeError) as error:
            print(
                f"isolated build cleanup refused; preserved {raw_root}: {error}",
                file=sys.stderr,
            )
            status = 1
    if runner.interrupted and status == 0:
        status = 128 + runner.interrupted
    return status


if __name__ == "__main__":
    raise SystemExit(main())
