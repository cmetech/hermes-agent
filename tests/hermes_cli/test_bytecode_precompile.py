"""Install-time caches must be usable, safe to refresh, and optional."""

import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import shutil
import py_compile
import venv

import pytest


def _helper():
    spec = importlib.util.find_spec("hermes_cli.bytecode_cache")
    assert spec is not None, "install/update bytecode preparation is not implemented"
    return importlib.import_module("hermes_cli.bytecode_cache")


def _runtime(tmp_path):
    root = tmp_path / "runtime with spaces"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.setuptools]\npy-modules = ["entry"]\n'
        '[tool.setuptools.packages.find]\ninclude = ["runtime", "runtime.*", "plugins.*"]\n',
        encoding="utf-8",
    )
    (root / "entry.py").write_text("VALUE = 'before'\n", encoding="utf-8")
    (root / "runtime").mkdir()
    (root / "runtime" / "__init__.py").write_text(
        "raise RuntimeError('compiling must not execute application code')\n", encoding="utf-8"
    )
    return root


def test_preparation_creates_importable_cache_without_executing_modules(tmp_path, monkeypatch):
    module = _helper()
    root = _runtime(tmp_path)
    monkeypatch.setattr(sys, "pycache_prefix", None)
    assert module.precompile_runtime(root, ())
    cache = Path(importlib.util.cache_from_source(str(root / "entry.py")))
    assert cache.is_file()
    assert Path(importlib.util.cache_from_source(str(root / "runtime" / "__init__.py"))).is_file()
    code = "import importlib.machinery; importlib.machinery.SourceFileLoader.source_to_code = lambda *a, **k: (_ for _ in ()).throw(AssertionError('cache miss')); import entry; print(entry.VALUE)"
    env = {**os.environ, "PYTHONPATH": str(root)}
    env.pop("PYTHONPYCACHEPREFIX", None)
    result = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "before"


def test_refresh_replaces_same_size_same_timestamp_source_cache(tmp_path, monkeypatch):
    module = _helper()
    root = _runtime(tmp_path)
    monkeypatch.setattr(sys, "pycache_prefix", None)
    assert module.precompile_runtime(root, ())
    source = root / "entry.py"
    stamp = source.stat()
    source.write_text("VALUE = 'after!'\n", encoding="utf-8")
    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert module.precompile_runtime(root, ())
    env = {**os.environ, "PYTHONPATH": str(root)}
    env.pop("PYTHONPYCACHEPREFIX", None)
    result = subprocess.run([sys.executable, "-c", "import entry; print(entry.VALUE)"], cwd=root, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "after!"


def test_runtime_and_dependency_caches_exclude_data_and_development_trees(tmp_path, monkeypatch):
    module = _helper()
    root = _runtime(tmp_path)
    monkeypatch.setattr(sys, "pycache_prefix", None)
    ignored = ["tests", "skills", "plugins/workflow/showcases/packages/demo/scripts", "runtime/node_modules", "runtime/.venv", "runtime/tests"]
    for rel in ignored:
        folder = root / rel
        folder.mkdir(parents=True)
        (folder / "fixture.py").write_text("invalid Python fixture !", encoding="utf-8")
    dependency = tmp_path / "site-packages"
    dependency.mkdir()
    (dependency / "dep.py").write_text("VALUE = 42\n", encoding="utf-8")
    assert module.precompile_runtime(root, (dependency,))
    assert Path(importlib.util.cache_from_source(str(dependency / "dep.py"))).is_file()
    for rel in ignored:
        assert not (root / rel / "__pycache__").exists()


def test_invalid_optional_source_warns_and_other_files_still_compile(tmp_path, monkeypatch, capsys):
    module = _helper()
    root = _runtime(tmp_path)
    monkeypatch.setattr(sys, "pycache_prefix", None)
    (root / "runtime" / "optional.py").write_text("invalid Python !", encoding="utf-8")
    assert not module.precompile_runtime(root, ())
    assert Path(importlib.util.cache_from_source(str(root / "entry.py"))).is_file()
    assert "warning" in capsys.readouterr().err.lower()


def test_unwritable_cache_is_nonfatal(tmp_path, monkeypatch, capsys):
    module = _helper()
    root = _runtime(tmp_path)
    # Portable real filesystem failure: the cache directory is occupied by a file.
    monkeypatch.setattr(sys, "pycache_prefix", None)
    (root / "__pycache__").write_text("not a directory", encoding="utf-8")
    assert not module.precompile_runtime(root, ())
    assert "warning" in capsys.readouterr().err.lower()


def test_unmanaged_python_does_not_compile_global_dependencies(monkeypatch):
    module = _helper()
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    assert module.dependency_cache_roots() == ()


def test_install_preparation_preserves_fresh_cache_on_next_launch(tmp_path, monkeypatch):
    module = _helper()
    prepare = getattr(module, "prepare_installation", None)
    assert callable(prepare), "installer completion must prepare caches after stale-cache cleanup"
    from hermes_cli import main
    root = _runtime(tmp_path)
    (root / ".git" / "refs" / "heads").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/base\n", encoding="utf-8")
    (root / ".git" / "refs" / "heads" / "base").write_text("b" * 40, encoding="utf-8")
    (root / ".bytecode-fingerprint").write_text("old checkout", encoding="utf-8")
    monkeypatch.setattr(main, "PROJECT_ROOT", root)
    monkeypatch.setattr(module, "dependency_cache_roots", lambda: ())
    monkeypatch.setattr(sys, "pycache_prefix", None)
    assert prepare(root)
    cache = Path(importlib.util.cache_from_source(str(root / "entry.py")))
    before = cache.read_bytes()
    main._sweep_stale_bytecode_if_checkout_changed()
    assert cache.read_bytes() == before
    assert (root / ".bytecode-fingerprint").read_text().endswith("b" * 40)


def test_install_preparation_never_imports_stale_application_code(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "pycache_prefix", None)
    root = _runtime(tmp_path)
    package = root / "hermes_cli"
    package.mkdir()
    (package / "__init__.py").write_text("raise RuntimeError('application imported before cleanup')\n")
    main = package / "main.py"
    main.write_text("raise RuntimeError('stale application code')\n")
    py_compile.compile(str(main), doraise=True)
    stale = Path(importlib.util.cache_from_source(str(main)))
    helper = package / "bytecode_cache.py"
    shutil.copyfile(Path(_helper().__file__), helper)
    venv.EnvBuilder(with_pip=False).create(root / "venv")
    python = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = subprocess.run([str(python), str(helper)], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not stale.exists()


def test_updater_runs_new_checkout_helper_in_fresh_interpreter(tmp_path, monkeypatch):
    from hermes_cli import main, update_cmd
    prepare = getattr(update_cmd, "_precompile_updated_runtime", None)
    assert callable(prepare), "updater must prepare the newly installed checkout"
    root = tmp_path / "new runtime"
    (root / "hermes_cli").mkdir(parents=True)
    venv.EnvBuilder(with_pip=False).create(root / "venv")
    # Stand-in for a newly downloaded helper; proves the old updater doesn't
    # import a cached copy in-process and passes the actual installation root.
    (root / "hermes_cli" / "bytecode_cache.py").write_text(
        "import os, pathlib, sys\n"
        "assert 'PYTHONHOME' not in os.environ\n"
        "assert 'PYTHONPATH' not in os.environ\n"
        "pathlib.Path('prepared').write_text(sys.prefix)\n", encoding="utf-8"
    )
    monkeypatch.setattr(main, "PROJECT_ROOT", root)
    monkeypatch.setenv("PYTHONHOME", "broken inherited Python")
    monkeypatch.setenv("PYTHONPATH", "foreign runtime")
    prepare()
    assert Path((root / "prepared").read_text()).resolve() == (root / "venv").resolve()


def test_updater_cache_failure_does_not_fail_update(tmp_path, monkeypatch, capsys):
    from hermes_cli import main, update_cmd
    prepare = getattr(update_cmd, "_precompile_updated_runtime", None)
    assert callable(prepare), "updater must handle optional bytecode preparation"
    root = tmp_path / "runtime"
    (root / "hermes_cli").mkdir(parents=True)
    (root / "hermes_cli" / "bytecode_cache.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    monkeypatch.setattr(main, "PROJECT_ROOT", root)
    prepare()
    assert "warning" in capsys.readouterr().out.lower()


def test_current_checkout_backfill_is_revision_keyed(tmp_path, monkeypatch):
    module = _helper()
    root = _runtime(tmp_path)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("a" * 40)
    monkeypatch.setattr(module, "dependency_cache_roots", lambda: ())
    monkeypatch.setattr(sys, "pycache_prefix", None)
    assert module.prepare_installation(root, only_if_needed=True)
    cache = Path(importlib.util.cache_from_source(str(root / "entry.py")))
    cache.unlink()
    assert module.prepare_installation(root, only_if_needed=True)
    assert not cache.exists(), "unchanged update must not repeat preparation"
    (root / ".git" / "HEAD").write_text("b" * 40)
    assert module.prepare_installation(root, only_if_needed=True)
    assert cache.exists(), "new checkout must be prepared"


def test_current_checkout_update_invokes_backfill_before_node_work(tmp_path, monkeypatch):
    from hermes_cli import main, update_cmd
    events = []
    monkeypatch.setattr(main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(update_cmd, "_precompile_updated_runtime", lambda **kw: events.append(("prepare", kw)))
    monkeypatch.setattr(update_cmd, "_update_node_dependencies", lambda: events.append(("node", {})) or [])
    monkeypatch.setattr(main, "_build_web_ui", lambda *a: None)
    monkeypatch.setattr(update_cmd, "_check_and_apply_config_migration", lambda **kw: None)
    update_cmd._repair_node_deps_on_current_checkout(lambda message: None)
    assert events == [("prepare", {"only_if_needed": True}), ("node", {})]
