"""
setup.py — wheel/sdist build guard and deterministic skill asset inventory.

pip/PyPI and Homebrew are no longer supported distribution methods for
Hermes Agent (see website/docs/getting-started/platform-support.md). The
wheel build still has to preserve repository-relative runtime assets because
Nix installs and downstream sealed-install tests execute from the extracted
wheel. Static ``data-files`` entries cover narrow catalogs, while the generic
``skills/`` tree needs a recursive inventory that retains every subdirectory.

This file overrides the ``bdist_wheel`` and ``sdist`` setuptools commands
to raise an error when run outside a Nix build. The PEP 517
``build_wheel`` / ``build_sdist`` hooks in
``setuptools.build_meta`` call these commands internally, so the guard
fires for ``uv build``, ``pip wheel``, ``python -m build``, and direct
``setup.py`` invocations alike.

The one legitimate consumer of ``build_wheel`` is uv2nix, which calls
``setuptools.build_meta.build_wheel`` (→ ``bdist_wheel``) inside a Nix
build sandbox. ``nix/python.nix`` sets ``HERMES_NIX_BUILD=1`` on the
Hermes package derivation, so only that build may create an artifact.

Editable installs (``uv sync``, ``pip install -e .``, ``nix develop``)
use ``build_editable``, which does NOT call ``bdist_wheel`` — it calls
``build_ext`` in editable mode. So the guard does not affect development.
"""

import os
from pathlib import Path
import stat
import tomllib

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

_IN_NIX_BUILD = os.environ.get("HERMES_NIX_BUILD") == "1"

_ROOT = Path(__file__).resolve().parent
_MAX_SKILL_ASSET_ENTRIES = 16_384
_MAX_SKILL_ASSET_FILES = 8_192
_MAX_SKILL_ASSET_BYTES = 64 * 1024 * 1024
_MAX_SKILL_ASSET_FILE_BYTES = 4 * 1024 * 1024
_MAX_SKILL_ASSET_DEPTH = 32
_IGNORED_SKILL_PARTS = frozenset({
    "__pycache__",
    ".pytest_cache",
    ".pytest-cache",
    ".ruff_cache",
})
_IGNORED_SKILL_SUFFIXES = frozenset({".pyc", ".pyo"})

_BLOCK_MESSAGE = (
    "Building wheels or sdists for hermes-agent is not supported.\n"
    "Hermes is distributed via the shell installer, Docker image, or Nix.\n"
    "See: https://hermes-agent.nousresearch.com/docs/getting-started/installation\n"
    "\n"
    "If you are developing, use an editable install instead:\n"
    "  uv sync          # or: uv pip install -e .\n"
    "\n"
    "If you are building with Nix (uv2nix), this error should not fire —\n"
    "the Hermes Nix derivation sets HERMES_NIX_BUILD=1. If it does, file a bug."
)


def _regular_source_status(source: str | os.PathLike[str]) -> str:
    """Classify one lexical repository path without following any component."""
    candidate = Path(source)
    try:
        relative = (
            candidate.relative_to(_ROOT) if candidate.is_absolute() else candidate
        )
    except ValueError:
        return "outside"
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        return "outside"
    current = _ROOT
    for index, part in enumerate(relative.parts):
        if part in _IGNORED_SKILL_PARTS:
            return "ignored"
        current /= part
        try:
            metadata = current.lstat()
        except OSError:
            return "unreadable"
        if stat.S_ISLNK(metadata.st_mode):
            return "symlink"
        if index < len(relative.parts) - 1:
            if not stat.S_ISDIR(metadata.st_mode):
                return "nonregular"
            continue
        if not stat.S_ISREG(metadata.st_mode):
            return "nonregular"
        if current.suffix.lower() in _IGNORED_SKILL_SUFFIXES:
            return "ignored"
    return "regular"


def _is_safe_artifact_source(source: str | os.PathLike[str]) -> bool:
    """Return whether a package/sdist source is a regular, non-cache file."""
    return _regular_source_status(source) == "regular"


def _configured_data_files() -> dict[str, set[str]]:
    """Expand the reviewed static data-file table without changing targets."""
    document = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    configured = document["tool"]["setuptools"].get("data-files", {})
    grouped: dict[str, set[str]] = {}
    for target in sorted(configured):
        patterns = configured[target]
        if not isinstance(patterns, list):
            raise RuntimeError(f"invalid setuptools data-files entry: {target}")
        files = grouped.setdefault(str(target), set())
        for pattern in patterns:
            if not isinstance(pattern, str):
                raise RuntimeError(f"invalid setuptools data-files pattern: {target}")
            for candidate in sorted(
                _ROOT.glob(pattern), key=lambda path: path.as_posix()
            ):
                status = _regular_source_status(candidate)
                if status == "symlink":
                    raise RuntimeError(
                        "configured data-file symlink source must be a regular file"
                    )
                if status != "regular":
                    raise RuntimeError(
                        "configured data-file source must be a regular file"
                    )
                files.add(candidate.relative_to(_ROOT).as_posix())
    return grouped


def _directory_open_flags() -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    return flags


def _recursive_skill_data_files() -> list[tuple[str, list[str]]]:
    """Return a bounded, stable, repository-relative inventory for ``skills/``.

    Setuptools' static ``data-files`` globs flatten recursive matches into one
    target directory. Grouping each source file by its actual parent preserves
    linked references, scripts, and router paths exactly as a checkout does.
    """
    grouped = _configured_data_files()
    skills_root = _ROOT / "skills"
    try:
        root_metadata = skills_root.lstat()
    except FileNotFoundError:
        return [
            (target, sorted(paths))
            for target, paths in sorted(grouped.items())
            if paths
        ]
    except OSError as exc:
        raise RuntimeError("bundled skills root could not be inspected") from exc
    if stat.S_ISLNK(root_metadata.st_mode):
        raise RuntimeError("bundled skills root must not be a symlink")
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise RuntimeError("bundled skills root must be a directory")

    entry_count = 0
    file_count = 0
    total_bytes = 0

    def inventory(directory_fd: int, relative_parts: tuple[str, ...]) -> None:
        nonlocal entry_count, file_count, total_bytes
        bounded_entries: list[tuple[str, os.stat_result]] = []
        try:
            with os.scandir(directory_fd) as iterator:
                for entry in iterator:
                    entry_count += 1
                    if entry_count > _MAX_SKILL_ASSET_ENTRIES:
                        raise RuntimeError(
                            "bundled skills entry count exceeds build capacity"
                        )
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise RuntimeError(
                            "bundled skill asset could not be inspected"
                        ) from exc
                    bounded_entries.append((entry.name, metadata))
        except RuntimeError:
            raise
        except OSError as exc:
            raise RuntimeError("bundled skills directory could not be read") from exc

        for name, metadata in sorted(bounded_entries, key=lambda item: item[0]):
            mode = metadata.st_mode
            if stat.S_ISLNK(mode):
                continue
            if stat.S_ISDIR(mode):
                if name in _IGNORED_SKILL_PARTS:
                    continue
                child_parts = (*relative_parts, name)
                if len(child_parts) > _MAX_SKILL_ASSET_DEPTH:
                    raise RuntimeError(
                        "bundled skills asset depth exceeds build capacity"
                    )
                try:
                    child_fd = os.open(
                        name, _directory_open_flags(), dir_fd=directory_fd
                    )
                except OSError as exc:
                    raise RuntimeError(
                        "bundled skill directory could not be opened"
                    ) from exc
                try:
                    opened = os.fstat(child_fd)
                    if not stat.S_ISDIR(opened.st_mode) or (
                        opened.st_dev,
                        opened.st_ino,
                    ) != (metadata.st_dev, metadata.st_ino):
                        raise RuntimeError(
                            "bundled skill directory changed during inventory"
                        )
                    inventory(child_fd, child_parts)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(mode):
                raise RuntimeError("bundled skill asset must be a regular file")
            if (
                name in _IGNORED_SKILL_PARTS
                or Path(name).suffix.lower() in _IGNORED_SKILL_SUFFIXES
            ):
                continue
            file_count += 1
            if file_count > _MAX_SKILL_ASSET_FILES:
                raise RuntimeError("bundled skills file count exceeds build capacity")
            if metadata.st_size > _MAX_SKILL_ASSET_FILE_BYTES:
                raise RuntimeError(
                    "bundled skill asset exceeds per-file build capacity"
                )
            total_bytes += metadata.st_size
            if total_bytes > _MAX_SKILL_ASSET_BYTES:
                raise RuntimeError("bundled skills assets exceed total build capacity")
            relative_path = Path("skills", *relative_parts, name).as_posix()
            target = Path("skills", *relative_parts).as_posix()
            grouped.setdefault(target, set()).add(relative_path)

    try:
        root_fd = os.open(skills_root, _directory_open_flags())
    except OSError as exc:
        raise RuntimeError("bundled skills root could not be opened") from exc
    try:
        opened_root = os.fstat(root_fd)
        if not stat.S_ISDIR(opened_root.st_mode) or (
            opened_root.st_dev,
            opened_root.st_ino,
        ) != (root_metadata.st_dev, root_metadata.st_ino):
            raise RuntimeError("bundled skills root changed during inventory")
        inventory(root_fd, ())
    finally:
        os.close(root_fd)
    return [
        (target, sorted(paths)) for target, paths in sorted(grouped.items()) if paths
    ]


class _FilteredBuildPy(build_py):
    """Exclude linked/nonregular package data before wheel staging copies it."""

    def find_data_files(self, package, src_dir):
        return [
            source
            for source in super().find_data_files(package, src_dir)
            if _is_safe_artifact_source(source)
        ]


class _GuardedSdist(sdist):
    def get_file_list(self) -> None:
        super().get_file_list()
        self.filelist.files[:] = [
            source for source in self.filelist.files if _is_safe_artifact_source(source)
        ]
        self.filelist.sort()
        self.filelist.remove_duplicates()
        self.write_manifest()

    def make_release_tree(self, base_dir, files) -> None:
        super().make_release_tree(
            base_dir,
            [source for source in files if _is_safe_artifact_source(source)],
        )

    def run(self, *args, **kwargs):
        if not _IN_NIX_BUILD:
            raise RuntimeError(_BLOCK_MESSAGE)
        return super().run(*args, **kwargs)


cmdclass = {"build_py": _FilteredBuildPy, "sdist": _GuardedSdist}

# bdist_wheel is only available when the `wheel` package is installed.
# setuptools.build_meta.build_wheel() calls it internally, so the guard
# fires for all PEP 517 wheel build paths. Define the subclass only when
# the import succeeds — otherwise a None base class raises TypeError at
# class-definition time, before the cmdclass guard can run.
try:
    from setuptools.command.bdist_wheel import bdist_wheel

    class _GuardedBdistWheel(bdist_wheel):
        def run(self, *args, **kwargs):
            if not _IN_NIX_BUILD:
                raise RuntimeError(_BLOCK_MESSAGE)
            # pyproject configuration is applied after setup.py keyword
            # arguments, so bind the final merged recursive inventory at the
            # wheel command boundary where setuptools can no longer replace it.
            self.distribution.data_files = _recursive_skill_data_files()
            return super().run(*args, **kwargs)

    cmdclass["bdist_wheel"] = _GuardedBdistWheel
except ImportError:
    pass

setup(cmdclass=cmdclass)
