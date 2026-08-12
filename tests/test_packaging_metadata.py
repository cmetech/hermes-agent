import ast
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def packaging_setup(tmp_path, monkeypatch):
    """Load the real setup helpers without invoking setuptools commands."""
    import setuptools

    monkeypatch.setattr(setuptools, "setup", lambda **_kwargs: None)
    monkeypatch.setenv("HERMES_NIX_BUILD", "1")
    spec = importlib.util.spec_from_file_location(
        f"task12_setup_{id(tmp_path)}", REPO_ROOT / "setup.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._ROOT = tmp_path
    (tmp_path / "pyproject.toml").write_text(
        "[tool.setuptools]\ndata-files = {}\n", encoding="utf-8"
    )
    return module


def _skill_file(root: Path, relative: str, contents: bytes = b"x") -> Path:
    path = root / "skills" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    return path


def _distribution_name(requirement: str) -> str:
    """Extract the PEP 508 distribution name from a requirement string.

    Robust to markers (``; python_version < '3.12'``), direct references
    (``name @ https://...``), extras (``name[extra]``) and every version
    operator (``==``, ``>=``, ``<=``, ``~=``, ``!=``, ``<``, ``>``), so a
    future dep declared with any valid specifier shape doesn't silently
    mis-parse here.
    """
    spec = requirement.split(";", 1)[0]  # drop environment markers
    spec = spec.split("@", 1)[0]  # drop direct-reference URLs
    spec = spec.split("[", 1)[0]  # drop extras
    spec = re.split(r"[=<>!~]", spec, maxsplit=1)[0]  # drop any version operator
    return spec.strip().lower()


def test_packaging_declared_as_core_dependency():
    """Regression for #40503.

    ``packaging`` is imported directly on three production paths
    (plugins/memory/hindsight/__init__.py, tools/lazy_deps.py,
    hermes_cli/main.py) yet was undeclared, so it only reached users
    transitively. The slim Docker image shipped without it, silently
    disabling Hindsight append-mode and version-constraint checks. It must
    be a declared core dependency so it installs everywhere and the
    update-repair step (``_verify_core_dependencies_installed``) guards it.
    """
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    core = data["project"]["dependencies"]
    names = {_distribution_name(dep) for dep in core}
    assert "packaging" in names, (
        "packaging is imported on production paths (hindsight version compare, "
        "lazy_deps version constraints, requirement parsing) and must be a "
        "declared core dependency, not a transitive — see #40503"
    )


def test_faster_whisper_is_not_a_base_dependency():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]

    assert not any(dep.startswith("faster-whisper") for dep in deps)

    voice_extra = data["project"]["optional-dependencies"]["voice"]
    assert any(dep.startswith("faster-whisper") for dep in voice_extra)


def test_manifest_includes_bundled_skills():
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "graft skills" in manifest
    assert "graft optional-skills" in manifest


def test_workflow_showcase_has_narrow_wheel_and_sdist_package_data():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    plugin_data = data["tool"]["setuptools"]["package-data"]["plugins"]
    data_files = data["tool"]["setuptools"]["data-files"]
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "workflow/showcases/**/*" in plugin_data
    assert "skills/productivity/workflow-showcase" in data_files
    assert "skills/productivity/workflow-showcase/workflows" in data_files
    assert "skills/productivity/workflow-showcase/references" in data_files
    assert "recursive-include plugins/workflow/showcases *" in manifest


def test_baked_capabilities_ship_in_wheel_and_sdist():
    """Clean packaged installs must be able to seed plugins, MCPs, and workflows."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data_files = data["tool"]["setuptools"]["data-files"]
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "capabilities" in data_files
    assert "capabilities/workflow-packages/ericsson/commands" in data_files
    assert "capabilities/workflow-packages/ericsson/workflows" in data_files
    assert "graft capabilities" in manifest


def test_bundled_plugin_manifests_ship_in_both_wheel_and_sdist():
    """Regression test for #34034 / #28149.

    Plugin discovery (hermes_cli/plugins.py) registers each bundled plugin by
    reading its ``plugin.yaml`` / ``plugin.yml`` manifest. Those manifests are
    data files, not Python modules, so they only reach installed packages when
    declared explicitly:

    - wheel  -> ``[tool.setuptools.package-data]`` ``plugins`` glob
    - sdist  -> ``MANIFEST.in`` (Homebrew and other downstream packagers build
                from the sdist)

    v0.15.0 declared neither, so the wheel shipped every adapter's Python code
    but none of its manifests, and *every* gateway platform failed with
    "No adapter available for <platform>". Both channels must cover manifests.
    """
    # There must actually be manifests on disk for the globs to match.
    on_disk = list((REPO_ROOT / "plugins").rglob("plugin.yaml")) + list(
        (REPO_ROOT / "plugins").rglob("plugin.yml")
    )
    assert on_disk, "expected bundled plugin manifests under plugins/"

    # Wheel channel: package-data must declare a glob that matches plugin
    # manifests anywhere under the plugins package.
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    plugins_pkg_data = data["tool"]["setuptools"]["package-data"].get("plugins", [])
    assert any(
        g.endswith("plugin.yaml") or g.endswith("plugin.yml")
        for g in plugins_pkg_data
    ), "pyproject package-data 'plugins' must ship plugin.yaml/plugin.yml (wheel)"

    # Sdist channel: MANIFEST.in must recursively include the manifests so
    # downstream packagers building from the sdist also get them.
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "recursive-include plugins" in manifest and "plugin.yaml" in manifest, (
        "MANIFEST.in must recursive-include plugins plugin.yaml/plugin.yml (sdist)"
    )


def test_generic_plugin_descriptors_and_skills_are_declared_for_both_artifacts():
    """Static metadata must cover arbitrary bundled plugins, not named vendors."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    plugin_data = data["tool"]["setuptools"]["package-data"]["plugins"]
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "**/config.schema.json" in plugin_data
    assert "**/skills/**/*" in plugin_data
    excluded = data["tool"]["setuptools"]["exclude-package-data"]["plugins"]
    for cache_name in ("__pycache__", ".pytest_cache", ".pytest-cache", ".ruff_cache"):
        assert any(cache_name in pattern for pattern in excluded)
    assert any(pattern.endswith("*.pyc") for pattern in excluded)
    assert any(pattern.endswith("*.pyo") for pattern in excluded)
    assert "graft plugins" in manifest
    for cache_name in ("__pycache__", ".pytest_cache", ".pytest-cache", ".ruff_cache"):
        assert cache_name in manifest


def test_skill_inventory_rejects_symlink_or_non_directory_root(
    packaging_setup, tmp_path
):
    real = tmp_path / "real-skills"
    real.mkdir()
    (tmp_path / "skills").symlink_to(real, target_is_directory=True)
    with pytest.raises(RuntimeError, match="root.*symlink"):
        packaging_setup._recursive_skill_data_files()

    (tmp_path / "skills").unlink()
    (tmp_path / "skills").write_text("not a directory", encoding="utf-8")
    with pytest.raises(RuntimeError, match="root.*directory"):
        packaging_setup._recursive_skill_data_files()


def test_skill_inventory_counts_every_entry_before_collection(
    packaging_setup, tmp_path, monkeypatch
):
    _skill_file(tmp_path, "one.md")
    _skill_file(tmp_path, "two.md")
    monkeypatch.setattr(packaging_setup, "_MAX_SKILL_ASSET_ENTRIES", 2)
    exact = packaging_setup._recursive_skill_data_files()
    assert exact == [("skills", ["skills/one.md", "skills/two.md"])]

    ignored = tmp_path / "skills" / ".pytest-cache"
    ignored.mkdir()
    with pytest.raises(RuntimeError, match="entry count"):
        packaging_setup._recursive_skill_data_files()


def test_skill_inventory_stops_streaming_at_adjacent_entry(
    packaging_setup, tmp_path, monkeypatch
):
    for index in range(20):
        _skill_file(tmp_path, f"asset-{index:02d}.md")
    monkeypatch.setattr(packaging_setup, "_MAX_SKILL_ASSET_ENTRIES", 2)
    original_scandir = os.scandir
    advances = 0
    closed = False

    class TrackingIterator:
        def __init__(self, path):
            self._inner = original_scandir(path)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal advances
            advances += 1
            return next(self._inner)

        def close(self):
            nonlocal closed
            closed = True
            self._inner.close()

    monkeypatch.setattr(packaging_setup.os, "scandir", TrackingIterator)
    with pytest.raises(RuntimeError, match="entry count"):
        packaging_setup._recursive_skill_data_files()
    assert advances == 3
    assert closed is True


def test_skill_inventory_counts_nonfiles_before_kind_checks(
    packaging_setup, tmp_path, monkeypatch
):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO fixture requires POSIX")
    _skill_file(tmp_path, "one.md")
    os.mkfifo(tmp_path / "skills" / "pipe")
    monkeypatch.setattr(packaging_setup, "_MAX_SKILL_ASSET_ENTRIES", 1)
    with pytest.raises(RuntimeError, match="entry count"):
        packaging_setup._recursive_skill_data_files()


def test_skill_inventory_file_count_exact_and_adjacent(
    packaging_setup, tmp_path, monkeypatch
):
    _skill_file(tmp_path, "a.md")
    _skill_file(tmp_path, "b.md")
    monkeypatch.setattr(packaging_setup, "_MAX_SKILL_ASSET_FILES", 2)
    assert packaging_setup._recursive_skill_data_files() == [
        ("skills", ["skills/a.md", "skills/b.md"])
    ]
    _skill_file(tmp_path, "c.md")
    with pytest.raises(RuntimeError, match="file count"):
        packaging_setup._recursive_skill_data_files()


def test_skill_inventory_byte_caps_are_exact_and_atomic(
    packaging_setup, tmp_path, monkeypatch
):
    first = _skill_file(tmp_path, "a.md", b"1234")
    _skill_file(tmp_path, "b.md", b"5678")
    monkeypatch.setattr(packaging_setup, "_MAX_SKILL_ASSET_FILE_BYTES", 4)
    monkeypatch.setattr(packaging_setup, "_MAX_SKILL_ASSET_BYTES", 8)
    expected = [("skills", ["skills/a.md", "skills/b.md"])]
    assert packaging_setup._recursive_skill_data_files() == expected

    first.write_bytes(b"12345")
    with pytest.raises(RuntimeError, match="per-file"):
        packaging_setup._recursive_skill_data_files()
    first.write_bytes(b"1234")
    _skill_file(tmp_path, "c.md", b"9")
    with pytest.raises(RuntimeError, match="total"):
        packaging_setup._recursive_skill_data_files()
    (tmp_path / "skills" / "c.md").unlink()
    assert packaging_setup._recursive_skill_data_files() == expected


def test_skill_inventory_depth_exact_and_adjacent(
    packaging_setup, tmp_path, monkeypatch
):
    _skill_file(tmp_path, "one/leaf.md")
    monkeypatch.setattr(packaging_setup, "_MAX_SKILL_ASSET_DEPTH", 1)
    assert packaging_setup._recursive_skill_data_files() == [
        ("skills/one", ["skills/one/leaf.md"])
    ]
    _skill_file(tmp_path, "one/two/overflow.md")
    with pytest.raises(RuntimeError, match="depth"):
        packaging_setup._recursive_skill_data_files()


def test_skill_inventory_closes_root_and_child_descriptors_on_error(
    packaging_setup, tmp_path, monkeypatch
):
    _skill_file(tmp_path, "one/two/overflow.md")
    monkeypatch.setattr(packaging_setup, "_MAX_SKILL_ASSET_DEPTH", 1)
    original_close = os.close
    closed: list[int] = []

    def tracking_close(fd: int) -> None:
        closed.append(fd)
        original_close(fd)

    monkeypatch.setattr(packaging_setup.os, "close", tracking_close)
    with pytest.raises(RuntimeError, match="depth"):
        packaging_setup._recursive_skill_data_files()
    assert len(closed) == 2
    assert len(set(closed)) == 2


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_skill_inventory_skips_nested_symlinks(
    packaging_setup, tmp_path, kind
):
    _skill_file(tmp_path, "kept.md")
    target = tmp_path / f"target-{kind}"
    if kind == "directory":
        target.mkdir()
        link = tmp_path / "skills" / "linked-directory"
        link.parent.mkdir(exist_ok=True)
        link.symlink_to(target, target_is_directory=True)
    else:
        target.write_text("outside\n", encoding="utf-8")
        link = tmp_path / "skills" / "linked-file.md"
        link.parent.mkdir(exist_ok=True)
        link.symlink_to(target)
    assert packaging_setup._recursive_skill_data_files() == [
        ("skills", ["skills/kept.md"])
    ]


def test_configured_data_files_reject_symlink_and_nonregular_sources(
    packaging_setup, tmp_path
):
    assets = tmp_path / "assets"
    assets.mkdir()
    target = tmp_path / "target.txt"
    target.write_text("target\n", encoding="utf-8")
    (assets / "linked.txt").symlink_to(target)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.setuptools.data-files]\nassets = ["assets/*"]\n', encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="regular file"):
        packaging_setup._configured_data_files()

    (assets / "linked.txt").unlink()
    if hasattr(os, "mkfifo"):
        os.mkfifo(assets / "pipe")
        with pytest.raises(RuntimeError, match="regular file"):
            packaging_setup._configured_data_files()


def test_configured_data_files_reject_symlinked_parent(
    packaging_setup, tmp_path
):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "asset.txt").write_text("outside\n", encoding="utf-8")
    (tmp_path / "linked-assets").symlink_to(outside, target_is_directory=True)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.setuptools.data-files]\nassets = ["linked-assets/*.txt"]\n',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="configured data-file.*symlink"):
        packaging_setup._configured_data_files()


def test_skill_inventory_merges_in_stable_target_and_file_order(
    packaging_setup, tmp_path
):
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in ("z.txt", "a.txt"):
        (assets / name).write_text(name, encoding="utf-8")
    _skill_file(tmp_path, "z-last/SKILL.md")
    _skill_file(tmp_path, "a-first/z.md")
    _skill_file(tmp_path, "a-first/a.md")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.setuptools.data-files]\nstatic = ["assets/*"]\n', encoding="utf-8"
    )
    assert packaging_setup._recursive_skill_data_files() == [
        ("skills/a-first", ["skills/a-first/a.md", "skills/a-first/z.md"]),
        ("skills/z-last", ["skills/z-last/SKILL.md"]),
        ("static", ["assets/a.txt", "assets/z.txt"]),
    ]


def test_generic_recursive_build_preserves_plugin_and_top_level_skill_paths(
    tmp_path,
):
    """A real wheel and sdist retain arbitrary source-relative skill assets."""
    source = tmp_path / "source"
    shutil.copytree(
        REPO_ROOT,
        source,
        ignore=shutil.ignore_patterns(
            ".git",
            ".worktrees",
            ".venv",
            "venv",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            ".pytest-cache",
            ".ruff_cache",
            "build",
            "dist",
            "hermes_agent.egg-info",
        ),
    )
    plugin = source / "plugins" / "generic_fixture"
    plugin_skill = plugin / "skills" / "investigate"
    plugin_skill.mkdir(parents=True)
    (plugin / "__init__.py").write_text("def register(ctx):\n    pass\n")
    (plugin / "plugin.yaml").write_text("name: generic-fixture\n")
    (plugin / "config.schema.json").write_text('{"version":1,"fields":[]}\n')
    (plugin_skill / "SKILL.md").write_text("---\nname: investigate\n---\nGeneric.\n")
    router = source / "skills" / "vendor_fixture" / "router"
    router.mkdir(parents=True)
    (router / "SKILL.md").write_text("---\nname: router\n---\nRoute.\n")
    symlink_target = source / "artifact-link-target.md"
    symlink_target.write_text("must not be copied through a link\n", encoding="utf-8")
    symlink_directory = source / "artifact-link-directory"
    symlink_directory.mkdir()
    (symlink_directory / "nested.md").write_text(
        "must not be copied through a directory link\n", encoding="utf-8"
    )
    plugin_symlinks = {
        plugin_skill / "linked-file.md": symlink_target,
        plugin_skill / "linked-directory": symlink_directory,
    }
    for link, target in plugin_symlinks.items():
        link.symlink_to(target, target_is_directory=target.is_dir())
    top_level_symlinks = {
        router / "linked-file.md": symlink_target,
        router / "linked-directory": symlink_directory,
    }
    for link, target in top_level_symlinks.items():
        link.symlink_to(target, target_is_directory=target.is_dir())
    cache_names = ("__pycache__", ".pytest_cache", ".pytest-cache", ".ruff_cache")
    ignored_relatives = set()
    for base in (
        plugin_skill,
        source / "skills" / "vendor_fixture" / "router",
    ):
        for cache_name in cache_names:
            ignored = base / cache_name
            ignored.mkdir()
            cached = ignored / "cached.bin"
            cached.write_bytes(b"not-package-data")
            ignored_relatives.add(cached.relative_to(source).as_posix())
        for suffix in ("pyc", "pyo"):
            cached = base / f"cached.{suffix}"
            cached.write_bytes(b"not-package-data")
            ignored_relatives.add(cached.relative_to(source).as_posix())

    artifacts = tmp_path / "artifacts"
    env = os.environ.copy()
    env["HERMES_NIX_BUILD"] = "1"
    env["UV_PYTHON"] = sys.executable
    built_wheel = subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--no-build-logs",
            "--out-dir",
            str(artifacts),
            ".",
        ],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert built_wheel.returncode == 0, built_wheel.stderr
    built_sdist = subprocess.run(
        [
            "uv",
            "build",
            "--sdist",
            "--no-build-logs",
            "--out-dir",
            str(artifacts),
            ".",
        ],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert built_sdist.returncode == 0, built_sdist.stderr
    wheel = next(artifacts.glob("*.whl"))
    sdist = next(artifacts.glob("*.tar.gz"))
    installed = tmp_path / "site"
    extracted = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--target",
            str(installed),
            "--no-deps",
            str(wheel),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert extracted.returncode == 0, extracted.stderr

    required = {
        "plugins/generic_fixture/config.schema.json",
        "plugins/generic_fixture/skills/investigate/SKILL.md",
        "skills/vendor_fixture/router/SKILL.md",
    }
    assert all((installed / relative).is_file() for relative in required)
    assert all(not (installed / relative).exists() for relative in ignored_relatives)
    linked_relatives = {
        link.relative_to(source).as_posix()
        for link in (*plugin_symlinks, *top_level_symlinks)
    }
    assert all(not (installed / relative).exists() for relative in linked_relatives)
    with tarfile.open(sdist, "r:gz") as archive:
        members = {
            "/".join(name.split("/")[1:])
            for name in archive.getnames()
            if "/" in name
        }
    assert required <= members
    assert not (members & ignored_relatives)
    assert not (members & linked_relatives)


# Minimum non-vulnerable Starlette: CVE-2026-48710 ("BadHost") was fixed in
# 1.0.1. Anything below that lets a malformed Host header desync
# ``request.url.path`` from the dispatched ASGI path, bypassing path-based
# authz in middleware/endpoints that gate on ``request.url``. Starlette is a
# transitive dep (fastapi in [web]; sse-starlette/mcp in [mcp]/[computer-use]/
# [dev]) so we pin it directly in every extra that exposes a server surface and
# enforce the floor in both pyproject and the committed lockfile.
_STARLETTE_CVE_FLOOR = (1, 0, 1)
_UPDATE_DOWNGRADE_GUARD_FLOORS = {
    # `hermes update` reinstalls exact pins from pyproject/lazy_deps. These
    # reviewed CVE pins must not slide back to stale versions that downgrade
    # already-patched user environments.
    "cryptography": (48, 0, 1),
    "starlette": (1, 3, 1),
    "python-multipart": (0, 0, 32),
}


def _version_tuple(spec: str) -> tuple[int, ...]:
    # "1.0.1" -> (1, 0, 1); tolerant of pre/post suffixes by truncating.
    head = spec.split("+", 1)[0]
    parts = []
    for chunk in head.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def test_starlette_pinned_above_cve_2026_48710_floor_in_pyproject():
    """Every extra that declares Starlette must pin a patched (>=1.0.1) version.

    Regression guard for #35067 / CVE-2026-48710. A future edit that drops the
    pin (re-exposing the unbounded transitive ``starlette>=0.27`` from mcp /
    ``>=0.40.0`` from fastapi) or pins a pre-1.0.1 version fails here instead of
    shipping a Host-header auth-bypass to dashboard / MCP-HTTP users.
    """
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]

    found = {}
    for extra, specs in extras.items():
        for spec in specs:
            name = spec.split("==", 1)[0].split(">", 1)[0].split("<", 1)[0].split("[", 1)[0].strip()
            if name.lower() == "starlette":
                assert "==" in spec, f"[{extra}] must exact-pin starlette, got {spec!r}"
                ver = spec.split("==", 1)[1].split(";", 1)[0].strip()
                found[extra] = ver

    # The four server-surface extras must each carry the direct pin.
    for extra in ("web", "mcp", "computer-use", "dev"):
        assert extra in found, (
            f"[{extra}] no longer pins starlette directly — CVE-2026-48710 "
            f"regression risk (mcp/fastapi pull it transitively with no upper bound)"
        )

    for extra, ver in found.items():
        assert _version_tuple(ver) >= _STARLETTE_CVE_FLOOR, (
            f"[{extra}] pins starlette=={ver}, below the CVE-2026-48710 fix "
            f"floor {'.'.join(map(str, _STARLETTE_CVE_FLOOR))}"
        )


def test_locked_starlette_is_not_vulnerable_to_cve_2026_48710():
    """The committed uv.lock must resolve starlette to a patched version.

    pyproject pins protect the declared extras, but the lockfile is what
    hash-verified installs (``uv sync --locked``) actually pull. Assert the
    resolved version is >= the CVE-2026-48710 fix floor so a stale-lock
    regression can't ship a vulnerable Starlette to users.
    """
    lock = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
    versions = []
    in_starlette = False
    for line in lock.splitlines():
        if line.startswith("[[package]]"):
            in_starlette = False
        elif line.strip() == 'name = "starlette"':
            in_starlette = True
        elif in_starlette and line.startswith("version = "):
            versions.append(line.split("=", 1)[1].strip().strip('"'))
            in_starlette = False

    assert versions, "starlette not found in uv.lock"
    for ver in versions:
        assert _version_tuple(ver) >= _STARLETTE_CVE_FLOOR, (
            f"uv.lock resolves starlette=={ver}, below the CVE-2026-48710 fix "
            f"floor {'.'.join(map(str, _STARLETTE_CVE_FLOOR))} — regenerate the "
            f"lockfile after bumping the pin"
        )




# ---------------------------------------------------------------------------
# Dependency-pin consistency: pyproject extras <-> tools/lazy_deps.py
#
# The same package is exact-pinned in two hand-maintained places: the
# [project.optional-dependencies] extras in pyproject.toml and the LAZY_DEPS
# allowlist in tools/lazy_deps.py (the lazy-install path deliberately mirrors
# the extras — see the comments on LAZY_DEPS: "match the corresponding extra
# in pyproject.toml ... update both this map AND the corresponding extra").
#
# They have silently drifted more than once: the aiohttp Slack pin (3.13.3 in
# the extras vs 3.13.4 in lazy_deps) and the anthropic pin (0.86.0 vs 0.87.0).
# The version a user ends up with then depends on whether the backend was
# installed eagerly (extra) or lazily (lazy_deps) — and for a CVE bump applied
# to only one side, that divergence is a latent security regression. These two
# tests assert the documented contract: the two sources agree, in lockstep.
# ---------------------------------------------------------------------------

# Matches "name==version" and "name[extra]==version", ignoring any trailing
# environment marker / comment. Only exact pins are collected; ranged specs
# (">=", "<") can't be compared for equality and are skipped.
_PIN_RE = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*([^\s;,#]+)"
)


def _canonical(name: str) -> str:
    # PEP 503 normalization so e.g. discord.py / discord-py compare equal.
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins_from_specs(specs):
    """Map canonical package name -> set of exact-pinned versions seen."""
    pins: dict[str, set[str]] = {}
    for spec in specs:
        m = _PIN_RE.match(spec)
        if not m:
            continue
        pins.setdefault(_canonical(m.group(1)), set()).add(m.group(2))
    return pins


def _locked_versions(package: str) -> set[str]:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {
        pkg["version"]
        for pkg in lock.get("package", [])
        if _canonical(pkg["name"]) == _canonical(package)
    }


def _pyproject_pinned_specs():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    specs = list(data["project"].get("dependencies", []))
    for extra in data["project"].get("optional-dependencies", {}).values():
        specs.extend(extra)
    return specs


def _lazy_deps_pinned_specs():
    """Extract every string literal inside the LAZY_DEPS dict via AST.

    Parsing rather than importing keeps this test free of
    tools/lazy_deps.py's runtime imports and side effects.
    """
    src = (REPO_ROOT / "tools" / "lazy_deps.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    specs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == "LAZY_DEPS" for t in targets):
            continue
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                specs.append(sub.value)
    assert specs, "could not extract specs from LAZY_DEPS — the AST parser drifted"
    return specs


def test_pyproject_pins_are_internally_consistent():
    """No package may be exact-pinned to two different versions in pyproject.

    A package legitimately appearing in several extras (e.g. aiohttp in
    messaging/slack/homeassistant/sms) must use the SAME version everywhere.
    """
    pins = _pins_from_specs(_pyproject_pinned_specs())
    conflicts = {name: sorted(v) for name, v in pins.items() if len(v) > 1}
    assert not conflicts, (
        "pyproject.toml exact-pins the same package to different versions "
        "across [project.dependencies] / extras: " + str(conflicts)
    )




def _lazy_deps_by_feature():
    """Parse LAZY_DEPS into {feature_name: [spec, ...]} via AST.

    Same parse-don't-import rationale as _lazy_deps_pinned_specs, but keeps the
    feature -> specs grouping so per-feature coverage can be asserted.
    """
    src = (REPO_ROOT / "tools" / "lazy_deps.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign)
            else []
        )
        if not any(isinstance(t, ast.Name) and t.id == "LAZY_DEPS" for t in targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        by_feature: dict[str, list[str]] = {}
        for key, value in zip(node.value.keys, node.value.values):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            by_feature[key.value] = [
                sub.value
                for sub in ast.walk(value)
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
            ]
        assert by_feature, "could not extract features from LAZY_DEPS — AST parser drifted"
        return by_feature
    raise AssertionError("LAZY_DEPS dict literal not found in tools/lazy_deps.py")


# Security-critical packages whose patched floor must be enforced on EVERY
# install path, eager and lazy. test_pyproject_and_lazy_deps_pins_agree only
# fires when a package is pinned in BOTH sources, so it cannot catch a lazy
# feature that omits the pin entirely — the exact gap that left platform.slack
# carrying aiohttp==3.14.0 while platform.discord (whose discord.py dep pulls
# aiohttp transitively as its HTTP backbone) shipped without it, so the lazy
# Discord path could keep an already-installed vulnerable aiohttp. A fully
# general "no mirrored feature drops a pin" check is impossible statically
# (it can't see transitive deps), so this is the explicit coverage contract:
# each security package -> the lazy features that bundle an SDK pulling it and
# must therefore carry the same pin as the pyproject extra.
_REQUIRED_SECURITY_PINS = {
    # Every lazy messaging feature whose SDK pulls aiohttp transitively must
    # carry the patched floor directly: discord.py (aiohttp<4), slack-bolt,
    # mautrix/aiohttp-socks (aiohttp<4 / >=3.10), and microsoft-teams-apps —
    # none of those upper/lower bounds excludes a vulnerable already-installed
    # aiohttp, so the lazy path would not upgrade it without an explicit pin.
    "aiohttp": {
        "platform.discord",
        "platform.slack",
        "platform.matrix",
        "platform.teams",
    },
}


def test_security_pins_present_in_mirrored_lazy_features():
    """Curated security pins must be present (not just version-consistent) in
    every lazy feature that bundles an SDK pulling that package transitively.
    """
    py = _pins_from_specs(_pyproject_pinned_specs())
    by_feature = _lazy_deps_by_feature()

    problems = []
    for pkg, features in _REQUIRED_SECURITY_PINS.items():
        canon = _canonical(pkg)
        expected = py.get(canon)
        assert expected, (
            f"{pkg} is listed in _REQUIRED_SECURITY_PINS but is not exact-pinned "
            f"in pyproject.toml — update the map or the pin."
        )
        for feature in sorted(features):
            specs = by_feature.get(feature)
            assert specs is not None, (
                f"lazy feature {feature!r} named in _REQUIRED_SECURITY_PINS no "
                f"longer exists in LAZY_DEPS — update the map."
            )
            got = _pins_from_specs(specs).get(canon)
            if got != expected:
                problems.append(
                    f"{feature}: {pkg}="
                    f"{sorted(got) if got else 'MISSING'}, expected {sorted(expected)}"
                )
    assert not problems, (
        "a lazy feature is missing a security pin it must mirror from the "
        "pyproject extras — the lazy install path would not enforce the "
        "CVE-patched floor:\n  " + "\n  ".join(problems)
    )
