"""Brand capability/persona staging seam + startup orchestration.

Dependency-light (stdlib + brand_config only) so it is safe on the CLI/backend startup
path. As of P2, the resolver + staging are live for brands that ship capabilitySets.
Remains fail-safe and no-op when sets are empty or ungated. Every step is individually
fail-safe: capability staging must NEVER block startup.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from hermes_cli import brand_config

log = logging.getLogger(__name__)

STAGING_MANIFEST = ".brand-staging.json"
STAGING_SCHEMA_VERSION = 1

CACHE_SUBDIR = Path("capabilities") / "src"
GIT_CLONE_TIMEOUT = 60
GIT_PULL_TIMEOUT = 30


def _valid_bundle(path: Path, set_name: str) -> bool:
    return (path / "sets" / f"{set_name}.json").is_file()


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
                               check=True, capture_output=True, timeout=GIT_PULL_TIMEOUT)
            except Exception:
                log.debug("capability cache pull failed for %s (using cached copy)", set_name)
        else:
            cache.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--depth", "1", "-q", source_url, str(cache)],
                           check=True, capture_output=True, timeout=GIT_CLONE_TIMEOUT)
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


def stage_bundle(bundle: Path, set_name: str, home: Path | str) -> bool:
    """Stage one resolved bundle into `home` per its sets/<set_name>.json manifest.

    Copies skills/plugins/mcpLocal/workflows, merges missing mcp_servers entries
    (resolving ${CAPABILITY_DIR} to <home>/plugins), and seeds disabled-by-default
    + plugins.enabled via ONE config round-trip. Returns True when staged.
    """
    import yaml

    home = Path(home)
    manifest = json.loads((bundle / "sets" / f"{set_name}.json").read_text(encoding="utf-8"))

    # skills: preserve the repo-relative layout under skills/ (keeps the category dir)
    for rel in manifest.get("skills") or []:
        src = bundle / rel
        if not src.is_dir():
            continue
        rel_under = Path(rel).relative_to("skills") if rel.startswith("skills/") else Path(src.name)
        _copy_tree(src, home / "skills" / rel_under)

    # plugins + local MCP servers both land in $HERMES_HOME/plugins/<basename>
    plugin_names: list[str] = []
    for key in ("plugins", "mcpLocal"):
        for rel in manifest.get(key) or []:
            src = bundle / rel
            if not src.is_dir():
                continue
            _copy_tree(src, home / "plugins" / src.name)
            if key == "plugins":
                plugin_names.append(src.name)

    for rel in manifest.get("workflows") or []:
        src = bundle / rel
        if src.is_file():
            dst = home / "workflows" / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # config: mcp merge + disabled-by-default seeding + plugin enable (one round-trip)
    from hermes_cli import config as config_mod
    from hermes_cli.brand_config import _union

    cfg = config_mod.load_config() or {}
    changed = False

    mcp_rel = manifest.get("mcpServers")
    if mcp_rel and (bundle / mcp_rel).is_file():
        fragment = yaml.safe_load((bundle / mcp_rel).read_text(encoding="utf-8")) or {}
        entries = fragment.get("mcp_servers") or {}
        text_sub = str(home / "plugins")
        existing = cfg.setdefault("mcp_servers", {})
        for name, entry in entries.items():
            if name in existing:
                continue  # NEVER clobber a user-configured server
            resolved = json.loads(json.dumps(entry).replace("${CAPABILITY_DIR}", text_sub))
            existing[name] = resolved
            changed = True

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
    if plugin_names:
        plugins_cfg = cfg.setdefault("plugins", {})
        merged = _union(plugins_cfg.get("enabled"), plugin_names)
        if merged != plugins_cfg.get("enabled"):
            plugins_cfg["enabled"] = merged
            changed = True

    if changed:
        config_mod.save_config(cfg)
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
            if stage_bundle(bundle, name, home):
                staged_sets[name] = {"version": version}
                dirty = True
        except Exception:
            log.debug("staging capability set %r failed (skipped)", name, exc_info=True)

    if dirty:
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        stamp_path.write_text(
            json.dumps({"schemaVersion": STAGING_SCHEMA_VERSION, "sets": staged_sets}, indent=2) + "\n",
            encoding="utf-8",
        )


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
