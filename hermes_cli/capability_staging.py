"""Brand capability/persona staging seam + startup orchestration.

Dependency-light (stdlib + brand_config only) so it is safe on the CLI/backend startup
path. As of P2, the resolver + staging are live for brands that ship capabilitySets.
Remains fail-safe and no-op when sets are empty or ungated. Every step is individually
fail-safe: capability staging must NEVER block startup.

Staging is gated at two levels: a descriptor-level `capabilityRequiresEnv` gate is
checked BEFORE any network resolution (clone/pull), so an unmet gate costs zero I/O;
the in-repo manifest's `requiresEnv` is then checked AFTER resolution as defense in
depth (it remains authoritative, since the descriptor gate is optional/best-effort).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Callable

from hermes_cli import brand_config

log = logging.getLogger(__name__)

STAGING_MANIFEST = ".brand-staging.json"
STAGING_SCHEMA_VERSION = 1

CACHE_SUBDIR = Path("capabilities") / "src"
GIT_CLONE_TIMEOUT = 60
GIT_PULL_TIMEOUT = 30
GIT_REPAIR_TIMEOUT = 15
_AUTHENTICATED_RESOURCE_ROOTS = (
    Path("capabilities/workflow-packages"),
    Path("plugins/workflow/showcases"),
)
_PLUGIN_PATH_RE = re.compile(r"^plugins/[a-z0-9][a-z0-9_-]*$")
_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_LIFECYCLE_MIGRATION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MAX_LIFECYCLE_MIGRATIONS = 256
_PLUGIN_OBJECT_KEYS = frozenset({"path", "id", "enabled", "lifecycleMigration"})
_PLUGIN_OBJECT_REQUIRED_KEYS = frozenset({"path", "id", "enabled"})


def _valid_bundle(path: Path, set_name: str) -> bool:
    return (path / "sets" / f"{set_name}.json").is_file()


def _no_prompt_env() -> dict:
    """Env for subprocess git calls that disables interactive credential prompts.

    Without this, an auth-requiring URL blocks on a terminal prompt (or hangs
    non-interactively) until the full subprocess timeout elapses instead of
    failing fast.
    """
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def repair_authenticated_resource_checkout(resource_root: Path | str) -> bool:
    """Restore legacy CRLF-only drift in a distribution-owned Git resource tree.

    Older managed Git-for-Windows checkouts can retain CRLF working-tree bytes
    after LF attributes are added because unchanged paths are not rewritten by
    a later pull. Repair is deliberately narrow: the tree must be tracked by
    the current repository, contain CRLF working-tree files, and have no
    semantic diff from ``HEAD`` when end-of-line whitespace is ignored. Any
    actual edit, untracked content, Git failure, or non-checkout install remains
    untouched so the caller's digest verification still fails closed.
    """
    try:
        resource = Path(resource_root).resolve(strict=True)
        discovered = subprocess.run(
            ["git", "-C", str(resource), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_REPAIR_TIMEOUT,
            env=_no_prompt_env(),
        )
        if discovered.returncode != 0 or not discovered.stdout.strip():
            return False
        repo = Path(discovered.stdout.strip()).resolve(strict=True)
        relative_path = resource.relative_to(repo)
        if (
            not resource.is_dir()
            or not (repo / ".git").exists()
            or not any(
                relative_path == allowed or allowed in relative_path.parents
                for allowed in _AUTHENTICATED_RESOURCE_ROOTS
            )
        ):
            return False
        relative = relative_path.as_posix()

        git = ["git", "-c", "core.autocrlf=false", "-C", str(repo)]
        eol = subprocess.run(
            [*git, "ls-files", "--eol", "--", relative],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_REPAIR_TIMEOUT,
            env=_no_prompt_env(),
        )
        if eol.returncode != 0 or not any(
            "w/crlf" in line for line in eol.stdout.splitlines()
        ):
            return False

        semantic_diff = subprocess.run(
            [*git, "diff", "--ignore-space-at-eol", "--quiet", "HEAD", "--", relative],
            check=False,
            capture_output=True,
            timeout=GIT_REPAIR_TIMEOUT,
            env=_no_prompt_env(),
        )
        if semantic_diff.returncode != 0:
            return False

        configured = subprocess.run(
            ["git", "-C", str(repo), "config", "core.autocrlf", "false"],
            check=False,
            capture_output=True,
            timeout=GIT_REPAIR_TIMEOUT,
            env=_no_prompt_env(),
        )
        if configured.returncode != 0:
            return False

        restored = subprocess.run(
            [*git, "checkout", "HEAD", "--", relative],
            check=False,
            capture_output=True,
            timeout=GIT_REPAIR_TIMEOUT,
            env=_no_prompt_env(),
        )
        if restored.returncode != 0:
            return False

        verified = subprocess.run(
            [*git, "ls-files", "--eol", "--", relative],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_REPAIR_TIMEOUT,
            env=_no_prompt_env(),
        )
        if verified.returncode != 0:
            return False

        if any("w/crlf" in line for line in verified.stdout.splitlines()):
            # Git for Windows can report a successful path checkout while
            # retaining pre-attribute CRLF bytes in a legacy worktree.  Force
            # materialization of only the already-verified tracked files from
            # the index, then retain the byte-level postcondition below.
            tracked = subprocess.run(
                [*git, "ls-files", "--", relative],
                check=False,
                capture_output=True,
                text=True,
                timeout=GIT_REPAIR_TIMEOUT,
                env=_no_prompt_env(),
            )
            paths = tracked.stdout.splitlines()
            if (
                tracked.returncode != 0
                or not paths
                or any(not path.startswith(f"{relative}/") for path in paths)
            ):
                return False
            # A legacy Git-for-Windows index can suppress even a forced
            # in-place checkout while reporting success. Materializing under a
            # new prefix bypasses that stat-cache optimization. Keep the
            # temporary tree on the repository volume, validate every output,
            # then atomically replace only the tracked paths admitted above.
            with tempfile.TemporaryDirectory(
                prefix=".authenticated-resource-", dir=repo
            ) as staging_dir:
                staging = Path(staging_dir).resolve(strict=True)
                prefix = f"{staging}{os.sep}"
                materialized = subprocess.run(
                    [
                        *git,
                        "checkout-index",
                        "--force",
                        "--ignore-skip-worktree-bits",
                        f"--prefix={prefix}",
                        "--",
                        *paths,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=GIT_REPAIR_TIMEOUT,
                    env=_no_prompt_env(),
                )
                if materialized.returncode != 0:
                    return False

                replacements: list[tuple[Path, Path]] = []
                for path in paths:
                    source = (staging / Path(path)).resolve(strict=True)
                    source.relative_to(staging)
                    if not source.is_file() or source.is_symlink():
                        return False
                    destination = repo / Path(path)
                    replacements.append((source, destination))
                for source, destination in replacements:
                    os.replace(source, destination)

            verified = subprocess.run(
                [*git, "ls-files", "--eol", "--", relative],
                check=False,
                capture_output=True,
                text=True,
                timeout=GIT_REPAIR_TIMEOUT,
                env=_no_prompt_env(),
            )
            if verified.returncode != 0:
                return False

        if any("w/crlf" in line for line in verified.stdout.splitlines()):
            return False

        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        log.debug(
            "authenticated resource CRLF repair failed for %s",
            resource_root,
            exc_info=True,
        )
        return False


def _resolve_placeholders(obj, replacement: str):
    """Recursively substitute ``${CAPABILITY_DIR}`` in string values of obj.

    Operates on parsed JSON values (not JSON text) so replacement strings
    containing backslashes (e.g. Windows paths) can never produce invalid
    JSON — unlike a naive json.dumps(...).replace(...) round-trip.
    """
    if isinstance(obj, str):
        return obj.replace("${CAPABILITY_DIR}", replacement)
    if isinstance(obj, list):
        return [_resolve_placeholders(x, replacement) for x in obj]
    if isinstance(obj, dict):
        return {k: _resolve_placeholders(v, replacement) for k, v in obj.items()}
    return obj


def _merge_mcp_server_defaults(
    existing: dict,
    entries: dict,
    capability_dir: str,
) -> bool:
    """Merge missing servers and fill only missing/blank managed URLs."""
    changed = False
    for name, entry in entries.items():
        resolved = _resolve_placeholders(entry, capability_dir)
        if name not in existing:
            existing[name] = resolved
            changed = True
            continue

        current = existing.get(name)
        if not isinstance(current, dict) or not isinstance(resolved, dict):
            continue
        current_url = current.get("url")
        default_url = resolved.get("url")
        current_url_is_blank = current_url is None or (
            isinstance(current_url, str) and not current_url.strip()
        )
        if (
            current_url_is_blank
            and isinstance(default_url, str)
            and default_url.strip()
        ):
            current["url"] = default_url
            changed = True
    return changed


def _merge_workflow_agent_defaults(config: dict, plugin_names: list[str]) -> bool:
    """Seed workflow-only agent overrides without replacing operator choices."""
    if "workflow" not in plugin_names:
        return False
    plugins = config.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        return False
    entries = plugins.setdefault("entries", {})
    if not isinstance(entries, dict):
        return False
    workflow = entries.setdefault("workflow", {})
    if not isinstance(workflow, dict):
        return False
    agent = workflow.setdefault("agent", {})
    if not isinstance(agent, dict):
        return False
    changed = False
    for key in ("allow_provider_override", "allow_model_override"):
        if key not in agent:
            agent[key] = True
            changed = True
    return changed


def _validated_manifest_plugins(
    raw_entries: object,
    bundle: Path,
) -> list[dict] | None:
    """Normalize capability plugin metadata without guessing through damage.

    String entries retain the original enabled-backend contract. Structured
    entries carry an explicit id and enabled state; their static descriptor is
    checked before staging so lifecycle metadata can never de-seed a different
    plugin. Any malformed structured metadata invalidates the whole plugin
    section, leaving unrelated plugin config untouched.
    """
    if not isinstance(raw_entries, list):
        return None

    import yaml

    normalized: list[dict] = []
    ids: set[str] = set()
    legacy_paths: set[str] = set()
    structured_paths: set[str] = set()
    migration_ids: set[str] = set()
    for raw in raw_entries:
        if isinstance(raw, str):
            if not _PLUGIN_PATH_RE.fullmatch(raw):
                return None
            plugin_id = Path(raw).name
            # Duplicate legacy strings historically collapse during the union.
            # A structured entry may not compete with an earlier legacy path/id.
            if raw in structured_paths:
                return None
            normalized.append({
                "enabled": True,
                "id": plugin_id,
                "legacy": True,
                "migration": None,
                "path": raw,
            })
            legacy_paths.add(raw)
            ids.add(plugin_id)
            continue

        if not isinstance(raw, dict):
            return None
        if (
            not _PLUGIN_OBJECT_REQUIRED_KEYS <= set(raw)
            or not set(raw) <= _PLUGIN_OBJECT_KEYS
        ):
            return None
        rel = raw.get("path")
        plugin_id = raw.get("id")
        enabled = raw.get("enabled")
        if (
            not isinstance(rel, str)
            or not _PLUGIN_PATH_RE.fullmatch(rel)
            or not isinstance(plugin_id, str)
            or not _PLUGIN_ID_RE.fullmatch(plugin_id)
            or type(enabled) is not bool
            or rel in legacy_paths
            or rel in structured_paths
            or plugin_id in ids
        ):
            return None

        source = bundle / rel
        descriptor = source / "plugin.yaml"
        if (
            source.is_symlink()
            or not source.is_dir()
            or descriptor.is_symlink()
            or not descriptor.is_file()
        ):
            return None
        try:
            descriptor_data = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError):
            return None
        expected_kind = "backend" if enabled else "standalone"
        if (
            not isinstance(descriptor_data, dict)
            or descriptor_data.get("name") != plugin_id
            or descriptor_data.get("kind") != expected_kind
        ):
            return None

        migration = None
        if "lifecycleMigration" in raw:
            migration_raw = raw.get("lifecycleMigration")
            if (
                enabled
                or not isinstance(migration_raw, dict)
                or set(migration_raw) != {"id", "from"}
                or migration_raw.get("from") != "auto_seeded_backend"
            ):
                return None
            migration_id = migration_raw.get("id")
            if (
                not isinstance(migration_id, str)
                or not _LIFECYCLE_MIGRATION_ID_RE.fullmatch(migration_id)
                or migration_id in migration_ids
            ):
                return None
            migration_ids.add(migration_id)
            migration = migration_id

        structured_paths.add(rel)
        ids.add(plugin_id)
        normalized.append({
            "enabled": enabled,
            "id": plugin_id,
            "legacy": False,
            "migration": migration,
            "path": rel,
        })
    return normalized


def _merge_plugin_manifest_defaults(config: dict, entries: list[dict]) -> bool:
    """Apply activation defaults and one-time lifecycle transitions atomically."""
    current = config.get("plugins")
    if current is None:
        plugins: dict = {}
    elif isinstance(current, dict):
        plugins = current
    else:
        return False

    enabled_raw = plugins.get("enabled", [])
    disabled_raw = plugins.get("disabled", [])
    applied_raw = plugins.get("lifecycle_migrations_applied", [])
    if (
        not isinstance(enabled_raw, list)
        or not all(isinstance(item, str) for item in enabled_raw)
        or not isinstance(disabled_raw, list)
        or not all(isinstance(item, str) for item in disabled_raw)
        or not isinstance(applied_raw, list)
        or len(applied_raw) > _MAX_LIFECYCLE_MIGRATIONS
        or not all(
            isinstance(item, str) and _LIFECYCLE_MIGRATION_ID_RE.fullmatch(item)
            for item in applied_raw
        )
        or len(set(applied_raw)) != len(applied_raw)
    ):
        return False

    disabled = set(disabled_raw)
    enabled = list(enabled_raw)
    enabled_defaults = [
        entry["id"]
        for entry in entries
        if entry["enabled"] and entry["id"] not in disabled
    ]
    for plugin_id in enabled_defaults:
        if plugin_id not in enabled:
            enabled.append(plugin_id)

    applied = list(applied_raw)
    unseen = [
        entry
        for entry in entries
        if entry["migration"] is not None and entry["migration"] not in applied
    ]
    if len(applied) + len(unseen) > _MAX_LIFECYCLE_MIGRATIONS:
        return False
    for entry in unseen:
        enabled = [item for item in enabled if item != entry["id"]]
        applied.append(entry["migration"])

    changed = False
    if enabled != enabled_raw:
        plugins["enabled"] = enabled
        changed = True
    if applied != applied_raw:
        plugins["lifecycle_migrations_applied"] = applied
        changed = True
    if changed and current is None:
        config["plugins"] = plugins
    changed |= _merge_workflow_agent_defaults(config, enabled_defaults)
    return changed


def resolve_capability_bundle(set_name: str, source_url: str | None,
                              home: Path | str, root: Path | None = None) -> Path | None:
    """Resolve a named capability set to a local checkout directory.

    Order: OTTO_CAPABILITY_SOURCE env override (dev) -> cached clone under
    $HERMES_HOME/capabilities/src/<set> (anonymous git clone/pull of the brand
    descriptor's public source URL). All failures degrade: stale cache is still
    used; no cache + no clone -> None. Never raises.
    """
    override = os.environ.get("OTTO_CAPABILITY_SOURCE")
    if override:
        p = Path(override)
        if _valid_bundle(p, set_name):
            return p
        log.debug("OTTO_CAPABILITY_SOURCE=%r lacks sets/%s.json — ignoring", override, set_name)
        return None

    if not source_url:
        return None
    cache = Path(home) / CACHE_SUBDIR / set_name
    try:
        if (cache / ".git").is_dir():
            try:
                subprocess.run(["git", "-C", str(cache), "pull", "--ff-only", "-q"],
                               check=True, capture_output=True, timeout=GIT_PULL_TIMEOUT,
                               env=_no_prompt_env())
            except Exception:
                log.debug("capability cache pull failed for %s (using cached copy)", set_name)
        else:
            cache.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--depth", "1", "-q", source_url, str(cache)],
                           check=True, capture_output=True, timeout=GIT_CLONE_TIMEOUT,
                           env=_no_prompt_env())
    except Exception:
        log.debug("capability clone failed for %s from %r", set_name, source_url, exc_info=True)
    return cache if _valid_bundle(cache, set_name) else None


def _requires_env_met(manifest: dict) -> bool:
    req = manifest.get("requiresEnv") or {}
    if not isinstance(req, dict):
        return False
    return all(os.environ.get(k) == v for k, v in req.items())


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True)


def _manifest_relative_path(value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"invalid {label} path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path == Path("."):
        raise ValueError(f"invalid {label} path")
    return path


def _assert_safe_tree(root: Path, relative: Path, *, label: str) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} contains a symlink: {relative.as_posix()}")
    if not current.is_dir():
        raise ValueError(f"{label} is not a directory: {relative.as_posix()}")
    for candidate in current.rglob("*"):
        if candidate.is_symlink():
            raise ValueError(
                f"{label} contains a symlink: {candidate.relative_to(root).as_posix()}"
            )
        if not candidate.is_file() and not candidate.is_dir():
            raise ValueError(f"{label} contains an unsupported filesystem entry")
    return current


def _verified_workflow_package(
    package_root: Path, digest_manifest: Path
) -> tuple[tuple[str, str, object, object | None], ...]:
    from plugins.workflow.compilation import WorkflowCatalogSnapshot, compile_workflow
    from plugins.workflow.language import supports_phase4_semantics
    from plugins.workflow.schema import parse_workflow_source_bytes
    from plugins.workflow.trust import compute_package_digest

    if digest_manifest.is_symlink() or not digest_manifest.is_file():
        raise ValueError("workflow package digest manifest is missing or unsafe")
    try:
        payload = json.loads(digest_manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("workflow package digest manifest is invalid") from exc
    packages = payload.get("packages") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schemaVersion") != 1
        or set(payload) != {"schemaVersion", "packages"}
        or not isinstance(packages, dict)
        or not packages
    ):
        raise ValueError("workflow package digest manifest has an unsupported shape")
    workflow_root = package_root / "workflows"
    if not workflow_root.is_dir():
        raise ValueError("workflow package does not contain workflows")
    workflow_paths = sorted(
        path
        for path in workflow_root.glob("*.yaml")
        if not path.name.endswith(".hermes.yaml")
    )
    if not workflow_paths:
        raise ValueError("workflow package does not contain portable YAML")
    sources = []
    for workflow_path in workflow_paths:
        sidecar_path = workflow_path.with_name(
            f"{workflow_path.stem}.hermes.yaml"
        )
        sources.append(
            parse_workflow_source_bytes(
                workflow_path,
                workflow_bytes=workflow_path.read_bytes(),
                sidecar_bytes=(
                    sidecar_path.read_bytes() if sidecar_path.is_file() else None
                ),
                source="distribution",
                precedence=1,
            )
        )
    catalog = WorkflowCatalogSnapshot.capture(sources)
    verified: list[tuple[str, str, object, object | None]] = []
    for source in sources:
        compilation = compile_workflow(source, catalog)
        package = compilation.package
        expected = packages.get(package.definition.name)
        phase4 = supports_phase4_semantics(
            package.language.effective_profile,
            package.language.normalizer_version,
        )
        actual = (
            compilation.composite_digest
            if phase4
            else compute_package_digest(package).sha256
        )
        if not isinstance(expected, str) or expected != actual:
            raise ValueError(
                f"workflow package digest mismatch: {package.definition.name}"
            )
        verified.append((
            package.definition.name,
            actual,
            package,
            compilation if phase4 else None,
        ))
    if set(packages) != {name for name, _digest, _package, _compilation in verified}:
        raise ValueError("workflow package digest manifest does not match package names")
    return tuple(verified)


def _stage_workflow_package(
    *,
    bundle: Path,
    entry: object,
    home: Path,
    trusted_distribution: bool,
    fault_injector: Callable[[str, Path], None],
) -> None:
    if not isinstance(entry, dict) or set(entry) != {"path", "digestManifest"}:
        raise ValueError("workflowPackages entries require path and digestManifest")
    source_relative = _manifest_relative_path(
        entry["path"], label="workflow package"
    )
    digest_relative = _manifest_relative_path(
        entry["digestManifest"], label="workflow digest manifest"
    )
    source = _assert_safe_tree(bundle, source_relative, label="workflow package")
    try:
        digest_inside = (bundle / digest_relative).relative_to(source)
    except ValueError as exc:
        raise ValueError("workflow digest manifest must be inside its package") from exc
    destination_parent = home / "workflows"
    destination_parent.mkdir(parents=True, exist_ok=True)
    destination = destination_parent / source.name
    transaction = uuid.uuid4().hex
    staged = destination_parent / f".{source.name}-stage-{transaction}"
    backup = destination_parent / f".{source.name}-backup-{transaction}"
    moved_previous = False
    published = False
    previously_untrusted: list[str] = []
    try:
        shutil.copytree(source, staged)
        _verified_workflow_package(staged, staged / digest_inside)
        fault_injector("before-workflow-package-swap", destination)
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                raise ValueError("workflow package destination is unsafe")
            os.replace(destination, backup)
            moved_previous = True
        os.replace(staged, destination)
        published = True
        if trusted_distribution:
            from plugins.workflow.compat import assess_compatibility
            from plugins.workflow.trust import WorkflowTrustStore
            from plugins.workflow.trust import build_risk_summary

            trust = WorkflowTrustStore(home)
            published = _verified_workflow_package(
                destination,
                destination / digest_inside,
            )
            for name, digest, package, compilation in published:
                if trust.check(digest) == "untrusted":
                    previously_untrusted.append(digest)
                risk = build_risk_summary(
                    package,
                    assess_compatibility(package),
                    compilation=compilation,
                )
                trust.trust(
                    digest,
                    actor="trusted_distribution",
                    risk_digest=risk.risk_digest,
                )
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        if published and destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        if moved_previous and backup.exists():
            os.replace(backup, destination)
        if previously_untrusted:
            from plugins.workflow.trust import WorkflowTrustStore

            trust = WorkflowTrustStore(home)
            for digest in previously_untrusted:
                trust.revoke(digest)
        raise
    finally:
        shutil.rmtree(staged, ignore_errors=True)
        if not moved_previous or published:
            shutil.rmtree(backup, ignore_errors=True)


def stage_bundle(
    bundle: Path,
    set_name: str,
    home: Path | str,
    *,
    trusted_distribution: bool = False,
    fault_injector: Callable[[str, Path], None] = lambda _phase, _path: None,
) -> bool:
    """Stage one resolved bundle into `home` per its sets/<set_name>.json manifest.

    Copies skills/plugins/mcpLocal/workflow packages, merges missing mcp_servers entries
    (resolving ${CAPABILITY_DIR} to <home>/plugins), and seeds disabled-by-default
    + plugins.enabled via ONE config round-trip. Returns True (reserved for
    future partial-failure reporting). Note: a version bump re-runs this staging,
    which re-applies disabledByDefault seeding — managed content, so a user's
    manual re-enable of a seeded skill/toolset is re-disabled on set upgrade by
    design.
    """
    import yaml

    home = Path(home)
    manifest = json.loads((bundle / "sets" / f"{set_name}.json").read_text(encoding="utf-8"))
    plugin_entries = _validated_manifest_plugins(manifest.get("plugins"), bundle)

    # skills: preserve the repo-relative layout under skills/ (keeps the category dir)
    for rel in manifest.get("skills") or []:
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts:
            log.debug("skipping skills entry %r: path traversal", rel)
            continue
        src = bundle / rel
        if not src.is_dir():
            continue
        rel_under = Path(rel).relative_to("skills") if rel.startswith("skills/") else Path(src.name)
        _copy_tree(src, home / "skills" / rel_under)

    # Manifest plugins and local MCP servers both land in
    # $HERMES_HOME/plugins/<basename>. Invalid structured plugin metadata
    # fails closed for the entire plugin section, while MCP staging remains
    # independent.
    staged_plugin_entries: list[dict] = []
    if plugin_entries is not None:
        for entry in plugin_entries:
            rel = entry["path"]
            p = Path(rel)
            if p.is_absolute() or ".." in p.parts:
                log.debug("skipping plugins entry %r: path traversal", rel)
                continue
            src = bundle / rel
            if p.as_posix() == "plugins/workflow":
                staged_plugin_entries.append(entry)
                continue
            if not src.is_dir():
                continue
            _copy_tree(src, home / "plugins" / src.name)
            staged_plugin_entries.append(entry)

    for rel in manifest.get("mcpLocal") or []:
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts:
            log.debug("skipping mcpLocal entry %r: path traversal", rel)
            continue
        src = bundle / rel
        if src.is_dir():
            _copy_tree(src, home / "plugins" / src.name)

    for entry in manifest.get("workflowPackages") or []:
        _stage_workflow_package(
            bundle=bundle,
            entry=entry,
            home=home,
            trusted_distribution=trusted_distribution,
            fault_injector=fault_injector,
        )

    # config: mcp merge + disabled-by-default seeding + plugin enable (one round-trip)
    from hermes_cli import config as config_mod
    from hermes_cli.brand_config import _union

    # config IO must target the SAME home being staged, never the ambient process
    # home: load_config()/save_config() otherwise resolve via get_hermes_home()
    # (context override -> HERMES_HOME env -> platform default), which can differ
    # from `home` for tests/tooling/future callers and silently pollute the wrong
    # config.yaml.
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(str(home))
    try:
        cfg = config_mod.load_config() or {}
        changed = False

        mcp_rel = manifest.get("mcpServers")
        if mcp_rel and (bundle / mcp_rel).is_file():
            fragment = yaml.safe_load((bundle / mcp_rel).read_text(encoding="utf-8")) or {}
            entries = fragment.get("mcp_servers") or {}
            text_sub = str(home / "plugins")
            existing = cfg.setdefault("mcp_servers", {})
            changed |= _merge_mcp_server_defaults(existing, entries, text_sub)

        dbd = manifest.get("disabledByDefault") or {}
        skills_off = dbd.get("skills") or []
        tools_off = dbd.get("toolsets") or []
        if skills_off:
            skills_cfg = cfg.setdefault("skills", {})
            merged = _union(skills_cfg.get("disabled"), skills_off)
            if merged != skills_cfg.get("disabled"):
                skills_cfg["disabled"] = merged
                changed = True
        if tools_off:
            merged = _union(cfg.get("disabled_toolsets"), tools_off)
            if merged != cfg.get("disabled_toolsets"):
                cfg["disabled_toolsets"] = merged
                changed = True
        if staged_plugin_entries:
            changed |= _merge_plugin_manifest_defaults(cfg, staged_plugin_entries)

        if changed:
            config_mod.save_config(cfg)
    finally:
        reset_hermes_home_override(token)
    return True


def stage_brand_capabilities(home: Path | str, root: Path | None = None) -> None:
    """Stage the active brand's capabilitySets into `home` (fail-safe, idempotent)."""
    slug = brand_config.resolve_active_brand(root)
    try:
        descriptor = brand_config.load_brand(slug, root)
    except Exception:
        log.debug("stage_brand_capabilities: could not load descriptor for %r", slug, exc_info=True)
        return

    cap_sets = descriptor.get("capabilitySets") or []
    persona_sets = descriptor.get("personaSets") or []
    if not cap_sets and not persona_sets:
        return  # empty sets — the original no-op path
    sources = descriptor.get("capabilitySources") or {}
    gates = descriptor.get("capabilityRequiresEnv") or {}

    home = Path(home)
    stamp_path = home / STAGING_MANIFEST
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except Exception:
        stamp = {}
    staged_sets = stamp.get("sets") or {}

    dirty = False
    for name in [*cap_sets, *persona_sets]:
        try:
            gate = gates.get(name)
            if isinstance(gate, dict) and gate and not all(
                    os.environ.get(k) == v for k, v in gate.items()):
                log.debug("capability set %r gated off pre-resolve (descriptor env gate unmet)", name)
                continue
            bundle = resolve_capability_bundle(name, sources.get(name), home, root)
            if bundle is None:
                continue
            manifest = json.loads((bundle / "sets" / f"{name}.json").read_text(encoding="utf-8"))
            if not _requires_env_met(manifest):
                log.debug("capability set %r gated off (requiresEnv unmet)", name)
                continue
            version = str(manifest.get("version") or "0")
            if staged_sets.get(name, {}).get("version") == version:
                continue  # already staged at this version — fast no-op
            if stage_bundle(
                bundle,
                name,
                home,
                trusted_distribution=not bool(os.environ.get("OTTO_CAPABILITY_SOURCE")),
            ):
                staged_sets[name] = {"version": version}
                dirty = True
        except Exception:
            log.debug("staging capability set %r failed (skipped)", name, exc_info=True)

    if dirty:
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = stamp_path.with_name(f"{stamp_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(
            json.dumps({"schemaVersion": STAGING_SCHEMA_VERSION, "sets": staged_sets}, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, stamp_path)


def _repo_root() -> Path:
    source_root = Path(__file__).resolve().parents[1]
    if (source_root / "capabilities").is_dir():
        return source_root

    # setuptools ``data-files`` are installed relative to sys.prefix, not
    # inside site-packages.  Prefer the source-tree layout during development,
    # then use the wheel/venv prefix so clean packaged installs can seed the
    # same authenticated capability bytes.
    packaged_root = Path(sys.prefix).resolve()
    if (packaged_root / "capabilities").is_dir():
        return packaged_root
    return source_root


def seed_baked_capabilities(home: Path | str, root: Path | None = None) -> None:
    """Seed baked-in (vendored) capabilities into `home` — LOCAL only, no network.

    Reads capabilities/*.json in the repo: merges each mcp-servers fragment into config.yaml
    (idempotent, never clobbering a user entry, ${CAPABILITY_DIR} -> the bundled plugins dir,
    e.g. <clone>/plugins) and copies workflows to $HERMES_HOME/workflows/. Fail-safe: never raises.
    """
    try:
        import yaml
        from hermes_cli.plugins import get_bundled_plugins_dir
        home = Path(home)
        cap_dir = _repo_root() / "capabilities"
        if not cap_dir.is_dir():
            return
        text_sub = str(get_bundled_plugins_dir())
        for manifest_path in sorted(cap_dir.glob("*.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(manifest, dict):
                continue
            for entry in manifest.get("workflowPackages") or []:
                try:
                    _stage_workflow_package(
                        bundle=_repo_root(),
                        entry=entry,
                        home=home,
                        trusted_distribution=True,
                        fault_injector=lambda _phase, _path: None,
                    )
                except Exception:
                    try:
                        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                            repaired = repair_authenticated_resource_checkout(
                                _repo_root() / entry["path"]
                            )
                        else:
                            repaired = False
                        if repaired:
                            _stage_workflow_package(
                                bundle=_repo_root(),
                                entry=entry,
                                home=home,
                                trusted_distribution=True,
                                fault_injector=lambda _phase, _path: None,
                            )
                            continue
                    except Exception:
                        pass
                    # Package authentication fails closed, but plugin activation
                    # and MCP defaults below are independent migrations. In
                    # particular, upgraded Git-for-Windows checkouts can retain
                    # pre-.gitattributes CRLF bytes for unchanged package files.
                    log.debug(
                        "baked workflow package staging failed for %s",
                        manifest_path,
                        exc_info=True,
                    )
            # mcp_servers fragment -> config.yaml (merge missing only)
            frag_name = manifest.get("mcpServersFile")
            frag = cap_dir / frag_name if frag_name else None
            entries = {}
            if frag and frag.is_file():
                try:
                    entries = (yaml.safe_load(frag.read_text(encoding="utf-8")) or {}).get("mcp_servers") or {}
                except Exception:
                    entries = {}
            plugin_entries = _validated_manifest_plugins(
                manifest.get("plugins"), _repo_root()
            )
            if entries or plugin_entries:
                from hermes_cli import config as config_mod
                from hermes_constants import reset_hermes_home_override, set_hermes_home_override
                token = set_hermes_home_override(str(home))
                try:
                    cfg = config_mod.load_config() or {}
                    changed = False
                    if entries:
                        existing = cfg.setdefault("mcp_servers", {})
                        changed |= _merge_mcp_server_defaults(existing, entries, text_sub)
                    if plugin_entries:
                        changed |= _merge_plugin_manifest_defaults(cfg, plugin_entries)
                    if changed:
                        config_mod.save_config(cfg)
                finally:
                    reset_hermes_home_override(token)
    except Exception:
        log.debug("seed_baked_capabilities failed", exc_info=True)


BRAND_DEFAULTS_MARKER = ".brand-defaults-seeded.json"
BRAND_DEFAULTS_SCHEMA_VERSION = 1


def _capability_config_defaults(root: Path | None = None) -> dict:
    """Merge every vendored capability manifest's ``configDefaults`` block.

    Manifest-driven for the same reason ``_inject_capability_env_vars`` is
    (config.py): adding a default later is a manifest edit, not a code edit.
    Located the same way — ``<repo root>/capabilities/*.json`` — and equally
    fail-safe, since a malformed manifest must never break startup.
    """
    base = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    cap_dir = base / "capabilities"
    merged: dict = {}
    if not cap_dir.is_dir():
        return merged

    for manifest_path in sorted(cap_dir.glob("*.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(manifest, dict):
            continue
        defaults = manifest.get("configDefaults")
        if isinstance(defaults, dict):
            _deep_merge_defaults(merged, defaults)
    return merged


def _deep_merge_defaults(target: dict, source: dict) -> None:
    """Merge ``source`` into ``target``; the first manifest to claim a leaf wins."""
    for key, value in source.items():
        if isinstance(value, dict):
            child = target.setdefault(key, {})
            if isinstance(child, dict):
                _deep_merge_defaults(child, value)
        elif key not in target:
            target[key] = value


def _flatten_defaults(defaults: dict, prefix: str = "") -> "list[tuple[list[str], object]]":
    """Flatten to (path, value) leaves. A list IS a leaf — ``trusted_origins``
    must be seeded whole, never merged element-wise."""
    leaves: list[tuple[list[str], object]] = []
    for key, value in defaults.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            leaves.extend(_flatten_defaults(value, path))
        else:
            leaves.append((path.split("."), value))
    return leaves


def _read_nested(cfg: dict, path: "list[str]") -> "tuple[bool, object]":
    """Return (present, value) for a dotted path already in the config."""
    cursor: object = cfg
    for part in path:
        if not isinstance(cursor, dict) or part not in cursor:
            return False, None
        cursor = cursor[part]
    return True, cursor


def _write_nested(cfg: dict, path: "list[str]", value: object) -> None:
    cursor = cfg
    for part in path[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[path[-1]] = value


def seed_brand_defaults(home: Path | str, root: Path | None = None) -> None:
    """Seed the active brand's curation.*.disabledByDefault into config.yaml ONCE.

    Activates the brand descriptor's `curation.skills.disabledByDefault` /
    `curation.tools.disabledByDefault`: on first run each listed name is unioned
    into config.yaml (`skills.disabled` / `disabled_toolsets`) so it ships toggled
    OFF but still visible on the Skills page. A marker file records the names
    already seeded, so this is a first-run seed only — a user who later re-enables
    a seeded skill/toolset is NOT re-disabled on the next startup. (Contrast with
    capability-set staging, which re-disables managed content on a version bump.)

    Fail-safe: any error is logged and swallowed — must never block startup.
    """
    try:
        slug = brand_config.resolve_active_brand(root)
        try:
            descriptor = brand_config.load_brand(slug, root)
        except Exception:
            log.debug("seed_brand_defaults: could not load descriptor for %r", slug, exc_info=True)
            return
        curation = descriptor.get("curation") or {}
        skills_dbd = [
            str(x) for x in ((curation.get("skills") or {}).get("disabledByDefault") or [])
            if isinstance(x, str)
        ]
        tools_dbd = [
            str(x) for x in ((curation.get("tools") or {}).get("disabledByDefault") or [])
            if isinstance(x, str)
        ]
        # Vendored capability manifests contribute config defaults alongside the
        # descriptor's curation lists — same seed-once semantics, because a
        # seeded value (browser trusted_origins, say) is one a user may
        # deliberately trim and must not have restored on the next launch.
        try:
            config_defaults = _capability_config_defaults(root)
        except Exception:
            log.debug("seed_brand_defaults: could not read capability configDefaults", exc_info=True)
            config_defaults = {}
        default_leaves = _flatten_defaults(config_defaults) if isinstance(config_defaults, dict) else []

        if not skills_dbd and not tools_dbd and not default_leaves:
            return  # nothing to seed — the no-op path

        home = Path(home)
        marker_path = home / BRAND_DEFAULTS_MARKER
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except Exception:
            marker = {}
        seeded_skills = set(marker.get("skills") or [])
        seeded_tools = set(marker.get("toolsets") or [])
        seeded_config = set(marker.get("config") or [])

        # Absent from the marker AND absent from the config: never seeded, and
        # not something the user has set. Anything else is left alone.
        pending_leaves = [
            (path, value) for path, value in default_leaves if ".".join(path) not in seeded_config
        ]

        new_skills = [s for s in skills_dbd if s not in seeded_skills]
        new_tools = [t for t in tools_dbd if t not in seeded_tools]
        if not new_skills and not new_tools and not pending_leaves:
            return  # already seeded once — respect the user's toggles

        # config IO must target the SAME home being seeded (see stage_bundle note).
        from hermes_cli import config as config_mod
        from hermes_cli.brand_config import _union
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override

        token = set_hermes_home_override(str(home))
        try:
            cfg = config_mod.load_config() or {}
            changed = False
            if new_skills:
                skills_cfg = cfg.setdefault("skills", {})
                merged = _union(skills_cfg.get("disabled"), new_skills)
                if merged != skills_cfg.get("disabled"):
                    skills_cfg["disabled"] = merged
                    changed = True
            if new_tools:
                merged = _union(cfg.get("disabled_toolsets"), new_tools)
                if merged != cfg.get("disabled_toolsets"):
                    cfg["disabled_toolsets"] = merged
                    changed = True
            for path, value in pending_leaves:
                # Present already means the user (or an earlier hand-edit) owns
                # it. Seeding is additive only — it never overwrites.
                present, _existing = _read_nested(cfg, path)
                if present:
                    continue
                _write_nested(cfg, path, value)
                changed = True
            if changed:
                config_mod.save_config(cfg)
        finally:
            reset_hermes_home_override(token)

        # Record ALL descriptor defaults as seeded (only reached once the config
        # round-trip above completed) so they are never reconsidered.
        marker_out = {
            "config": sorted(seeded_config | {".".join(path) for path, _ in default_leaves}),
            "schemaVersion": BRAND_DEFAULTS_SCHEMA_VERSION,
            "skills": sorted(seeded_skills | set(skills_dbd)),
            "toolsets": sorted(seeded_tools | set(tools_dbd)),
        }
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker_path.with_name(f"{marker_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(marker_out, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, marker_path)
    except Exception:
        log.debug("seed_brand_defaults failed", exc_info=True)


def run_brand_startup(home: Path | str, root: Path | None = None) -> None:
    """Single startup entry: write the discoverable brand.json + stage capabilities.

    Each step is individually fail-safe so a failure in one cannot block the other or startup.
    """
    try:
        brand_config.write_brand_json(home, root)
    except Exception:
        log.debug("write_brand_json failed", exc_info=True)
    try:
        stage_brand_capabilities(home, root)
    except Exception:
        log.debug("stage_brand_capabilities failed", exc_info=True)
    try:
        seed_baked_capabilities(home, root)
    except Exception:
        log.debug("seed_baked_capabilities failed", exc_info=True)
    try:
        seed_brand_defaults(home, root)
    except Exception:
        log.debug("seed_brand_defaults failed", exc_info=True)
