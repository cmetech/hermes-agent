"""Read a brand descriptor (brands/<slug>.json) and apply its curation to config.

Deliberately dependency-light (reads JSON directly, no CLI-config import) so it
can run in the build/bootstrap path — mirrors agent/skill_utils.get_disabled_skill_names.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path


def load_brand(slug: str, root: Path | None = None) -> dict:
    root = root or Path(__file__).resolve().parent.parent
    return json.loads((root / "brands" / f"{slug}.json").read_text(encoding="utf-8"))


def _union(existing, extra):
    seen = list(existing or [])
    for item in extra or []:
        if item not in seen:
            seen.append(item)
    return seen


def seed_disabled(config: dict, brand: dict) -> dict:
    out = copy.deepcopy(config) if config else {}
    curation = (brand or {}).get("curation", {})
    skills_off = curation.get("skills", {}).get("disabledByDefault", [])
    tools_off = curation.get("tools", {}).get("disabledByDefault", [])

    skills_cfg = out.setdefault("skills", {})
    skills_cfg["disabled"] = _union(skills_cfg.get("disabled"), skills_off)
    out["disabled_toolsets"] = _union(out.get("disabled_toolsets"), tools_off)
    return out


def resolve_active_brand(root: Path | None = None) -> str:
    """Active brand slug: env OTTO_BRAND > brand/active marker > 'otto'."""
    env = os.environ.get("OTTO_BRAND")
    if env and env.strip():
        return env.strip()
    root = root or Path(__file__).resolve().parent.parent
    marker = root / "brand" / "active"
    try:
        if marker.exists():
            val = marker.read_text(encoding="utf-8").strip()
            if val:
                return val
    except Exception:
        pass
    return "otto"


def get_channel_allowlist(slug: str, root: Path | None = None) -> set[str] | None:
    """The brand's messaging allowlist, or None (show all) if empty/absent/error."""
    try:
        brand = load_brand(slug, root)
        curation = brand.get("curation") if isinstance(brand, dict) else None
        channels = curation.get("channels") if isinstance(curation, dict) else None
        allow = channels.get("allow") if isinstance(channels, dict) else None
        if isinstance(allow, list) and allow:
            return set(allow)
        return None
    except Exception:
        return None


def visible_platform_ids(config: dict | None = None, root: Path | None = None) -> set[str] | None:
    """Effective visible platform ids: config override > brand allowlist > None (all).

    config=None → lazily load the CLI config (guarded) so both the web layer and
    the gateway get consistent results. Any failure → fall through to the brand
    allowlist / None (fail OPEN — never hide everything).
    """
    if config is None:
        try:
            from hermes_cli.config import load_config
            config = load_config() or {}
        except Exception:
            config = {}
    try:
        override = ((config or {}).get("messaging") or {}).get("allowed_platforms")
        if isinstance(override, list) and override:
            return set(override)
    except Exception:
        pass
    return get_channel_allowlist(resolve_active_brand(root), root)


# ── Skill / toolset curation (runtime hide + rename) ─────────────────────────
# Reads the active brand descriptor's `curation` block and applies it at the
# enumeration sites (skills scan + toolsets/skills API). Mirrors the channel
# allowlist above: dependency-light, fail-OPEN (any error → no curation, never
# hide everything). Unlike `seed_disabled` (which only DISABLES → still shown as
# a toggle), `exclude`/`excludeToolsets` HIDE the item entirely (dropped from the
# list and never loaded); `rename` overrides only the display name.


def get_hidden_skills(slug: str, root: Path | None = None) -> set[str]:
    """Skill identifiers (frontmatter name and/or dir name) to hide entirely."""
    try:
        brand = load_brand(slug, root)
        excl = brand.get("curation", {}).get("skills", {}).get("exclude", [])
        return {str(x) for x in excl} if isinstance(excl, list) else set()
    except Exception:
        return set()


def get_skill_rename_map(slug: str, root: Path | None = None) -> dict[str, str]:
    """Map of skill name/dir → display name override (UI title only)."""
    try:
        brand = load_brand(slug, root)
        rn = brand.get("curation", {}).get("skills", {}).get("rename", {})
        return {str(k): str(v) for k, v in rn.items()} if isinstance(rn, dict) else {}
    except Exception:
        return {}


def get_excluded_toolsets(slug: str, root: Path | None = None) -> set[str]:
    """Toolset keys to hide from the configurable-toolsets list entirely."""
    try:
        brand = load_brand(slug, root)
        excl = brand.get("curation", {}).get("tools", {}).get("excludeToolsets", [])
        return {str(x) for x in excl} if isinstance(excl, list) else set()
    except Exception:
        return set()


def get_managed_skills(slug: str, root: Path | None = None) -> set[str]:
    """Skill identifiers (frontmatter name and/or dir name) that are brand-MANAGED.

    Managed skills are force-updated from the bundled copy when it changes, bypassing
    the skills-sync "user-modified" skip — so skills we deliver always track what we
    ship, even on installs whose manifest hash drifted. Fail-OPEN (empty on any error).
    """
    try:
        brand = load_brand(slug, root)
        mgd = brand.get("curation", {}).get("skills", {}).get("managed", [])
        return {str(x) for x in mgd} if isinstance(mgd, list) else set()
    except Exception:
        return set()


def active_hidden_skills(root: Path | None = None) -> set[str]:
    return get_hidden_skills(resolve_active_brand(root), root)


def active_managed_skills(root: Path | None = None) -> set[str]:
    return get_managed_skills(resolve_active_brand(root), root)


def active_skill_rename_map(root: Path | None = None) -> dict[str, str]:
    return get_skill_rename_map(resolve_active_brand(root), root)


def active_excluded_toolsets(root: Path | None = None) -> set[str]:
    return get_excluded_toolsets(resolve_active_brand(root), root)


BRAND_JSON_SCHEMA_VERSION = 1

# Canonical brand.json shape. MUST stay in sync with the JS builder in
# scripts/brand/brand-json.mjs (brandJsonPayload). Both are asserted against the
# same key list in tests (test_brand_runtime.py / brand-json.test.mjs).
def brand_json_payload(slug: str, root: Path | None = None) -> dict:
    """Resolved brand identity for `slug` — the discoverable descriptor the P4 tray reads.

    Defaults mirror scripts/brand/descriptor.mjs withDefaults so the Python and JS
    writers produce byte-compatible output for the same slug.
    """
    b = load_brand(slug, root)
    s = b["slug"]
    display = b.get("displayName") or s.upper()
    scheme = b.get("scheme") or s
    return {
        "schemaVersion": BRAND_JSON_SCHEMA_VERSION,
        "slug": s,
        "displayName": display,
        "appId": b.get("appId") or f"io.cmetech.{s}",
        "scheme": scheme,
        "schemes": [scheme, "hermes"],
        "homeDir": b.get("homeDir") or f".{s}",
        "releasesRepo": b.get("releasesRepo") or f"cmetech/{s}",
        "updateCommand": b.get("updateCommand") or f"{s} update",
        "gateway": b.get("gateway") or "otto",
    }


def write_brand_json(home: Path | str, root: Path | None = None) -> None:
    """Write <home>/brand.json for the active brand (write-if-changed).

    Best-effort caller (run_brand_startup) wraps this; a redundant identical write is
    skipped to avoid disk churn on every startup.
    """
    slug = resolve_active_brand(root)
    payload = brand_json_payload(slug, root)
    text = json.dumps(payload, indent=2) + "\n"
    target = Path(home) / "brand.json"
    try:
        if target.exists() and target.read_text(encoding="utf-8") == text:
            return
    except Exception:
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
