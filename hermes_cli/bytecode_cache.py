"""Best-effort install/update preparation, never part of ordinary startup.

Compile only runtime code, not bundled data or user profiles. In particular,
workflow showcases have an exact-byte contract and must not gain .pyc files.
"""

from itertools import chain
import os
from pathlib import Path
import shutil
import sys
import sysconfig

_PREPARATION_STAMP = ".bytecode-prepared"


_SKIP_DIRECTORIES = frozenset({
    "__pycache__", "node_modules", "venv", "tests", "test", "fixtures",
    "assets", "showcases", "skills", "optional-skills", "examples",
})


def _read_packed_ref(common_dir: Path, ref: str) -> str | None:
    """Look up a ref in .git/packed-refs without spawning git.

    packed-refs lines look like ``<sha> <ref>`` with optional ``^<sha>``
    peel lines and ``#``-prefixed comments / ``# pack-refs with:`` header.
    """
    try:
        text = (common_dir / "packed-refs").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if not line or line.startswith("#") or line.startswith("^"):
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1].strip() == ref:
            return parts[0].strip()
    return None


def _read_git_revision_fingerprint(repo_root: Path) -> str | None:
    """Return a cheap checkout fingerprint without spawning git."""
    git_dir = repo_root / ".git"
    try:
        if git_dir.is_file():
            for line in git_dir.read_text(encoding="utf-8", errors="replace").splitlines():
                key, _, value = line.partition(":")
                if key.strip() == "gitdir" and value.strip():
                    git_dir = (repo_root / value.strip()).resolve()
                    break
        # Worktrees point HEAD at a per-worktree gitdir but pack their refs
        # in the main repo's gitdir (referenced via ``commondir``). Resolve
        # that up front so packed-refs lookups hit the right file.
        common_dir = git_dir
        commondir_file = git_dir / "commondir"
        if commondir_file.exists():
            try:
                rel = commondir_file.read_text(encoding="utf-8", errors="replace").strip()
                if rel:
                    common_dir = (git_dir / rel).resolve()
            except OSError:
                pass
        head_file = git_dir / "HEAD"
        head = head_file.read_text(encoding="utf-8", errors="replace").strip()
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            # Loose refs may live in the worktree gitdir OR the common dir
            # (branches created via `git worktree add` typically live in the
            # common dir's refs/heads/).
            for candidate in (git_dir, common_dir):
                ref_file = candidate / ref
                if ref_file.exists():
                    return f"git:{ref}:{ref_file.read_text(encoding='utf-8', errors='replace').strip()}"
            packed_sha = _read_packed_ref(common_dir, ref)
            if packed_sha:
                return f"git:{ref}:{packed_sha}"
            # Ref name is known but unresolved — still stable across launches,
            # and the version/release fallback in the caller will invalidate
            # after `hermes update`.
            return f"git:{ref}:unresolved"
        return f"git:HEAD:{head}"
    except OSError:
        return None


def _clear_bytecode_cache(root: Path) -> int:
    """Remove all __pycache__ directories under *root*.

    Stale .pyc files can cause ImportError after code updates when Python
    loads a cached bytecode file that references names that no longer exist
    (or don't yet exist) in the updated source.  Clearing them forces Python
    to recompile from the .py source on next import.

    Returns the number of directories removed.
    """
    removed = 0
    for dirpath, dirnames, _ in os.walk(root):
        # Skip venv / node_modules / .git entirely
        dirnames[:] = [
            d
            for d in dirnames
            if d not in {"venv", ".venv", "node_modules", ".git", ".worktrees"}
        ]
        if os.path.basename(dirpath) == "__pycache__":
            try:
                shutil.rmtree(dirpath)
                removed += 1
            except OSError:
                pass
            dirnames.clear()  # nothing left to recurse into
    return removed


def _python_files(root: Path):
    """Walk one runtime root without following links or entering data trees."""
    root = root.resolve()
    for directory, children, files in os.walk(root):
        parent = Path(directory)
        children[:] = [
            name for name in children
            if not name.startswith(".") and name not in _SKIP_DIRECTORIES
            and not (parent / name).is_symlink()
            and (parent / name).resolve().is_relative_to(root)
        ]
        for name in files:
            source = parent / name
            if name.endswith(".py") and not source.is_symlink():
                yield source


def dependency_cache_roots() -> tuple[Path, ...]:
    """Only this interpreter's venv, never user-site or a shared system Python."""
    if sys.prefix == sys.base_prefix:
        return ()
    prefix = Path(sys.prefix).resolve()
    paths = sysconfig.get_paths()
    return tuple(dict.fromkeys(
        path for key in ("purelib", "platlib")
        if (path := Path(paths[key]).resolve()).is_relative_to(prefix)
        and path.is_dir()
    ))


def precompile_runtime(root: Path, dependencies: tuple[Path, ...]) -> bool:
    """Prepare caches with this Python; false means slower startup, not failure.

    Source targets come from existing packaging metadata. Force-refresh them
    after updates, including the same-size/same-timestamp case. Dependencies
    use Python's usual incremental compilation. No target module is imported.
    """
    import compileall
    import py_compile
    import tomllib

    failures = 0
    attempted = 0
    try:
        root = root.resolve()
        metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        packaging = metadata["tool"]["setuptools"]
        modules = packaging.get("py-modules", ())
        packages = packaging.get("packages", {}).get("find", {}).get("include", ())
        sources = [root / f"{name}.py" for name in modules if name.isidentifier()]
        for name in dict.fromkeys(pattern.split(".")[0] for pattern in packages):
            folder = root / name
            if name.isidentifier() and folder.is_dir() and not folder.is_symlink() and folder.resolve().is_relative_to(root):
                sources.extend(_python_files(folder))
        for source, force in chain(
            ((path, True) for path in sources),
            ((path, False) for folder in dependencies for path in _python_files(folder)),
        ):
            if not source.is_file() or source.is_symlink():
                continue
            attempted += 1
            if not compileall.compile_file(
                str(source), quiet=2, force=force, optimize=0,
                invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
            ):
                failures += 1
    except Exception as exc:
        print(f"Warning: Python bytecode preparation incomplete: {exc}", file=sys.stderr)
        return False
    if failures:
        print(f"Warning: could not prepare {failures} Python bytecode files; normal source loading remains available.", file=sys.stderr)
    print(f"Python bytecode preparation: {attempted} files checked, {failures} unavailable.")
    return failures == 0


def prepare_installation(root: Path | None = None, *, only_if_needed: bool = False) -> bool:
    """Clean before ANY application import, including for non-Git installs."""
    try:
        root = root or Path(__file__).resolve().parent.parent
        fingerprint = _read_git_revision_fingerprint(root)
        prepared_stamp = root / _PREPARATION_STAMP
        preparation_key = f"{fingerprint}\n{sys.implementation.cache_tag}\n{sys.executable}\n{sys.pycache_prefix}"
        if only_if_needed and fingerprint and prepared_stamp.is_file():
            if prepared_stamp.read_text(encoding="utf-8") == preparation_key:
                return True
        # Failed/interrupted attempts must remain retryable.
        prepared_stamp.unlink(missing_ok=True)
        _clear_bytecode_cache(root)
        if fingerprint:
            stamp = root / ".bytecode-fingerprint"
            temporary = stamp.with_name(stamp.name + ".tmp")
            temporary.write_text(fingerprint, encoding="utf-8")
            temporary.replace(stamp)
        success = precompile_runtime(root, dependency_cache_roots())
        if success:
            prepared_stamp.write_text(preparation_key, encoding="utf-8")
        return success
    except Exception as exc:
        print(f"Warning: Python bytecode preparation unavailable: {exc}", file=sys.stderr)
        return False


if __name__ == "__main__":
    # Run the NEW checkout directly, including during updates by an old process.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(0 if prepare_installation(only_if_needed="--if-needed" in sys.argv[1:]) else 1)
