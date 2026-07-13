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
