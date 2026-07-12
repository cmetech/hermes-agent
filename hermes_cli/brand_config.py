"""Read a brand descriptor (brands/<slug>.json) and apply its curation to config.

Deliberately dependency-light (reads JSON directly, no CLI-config import) so it
can run in the build/bootstrap path — mirrors agent/skill_utils.get_disabled_skill_names.
"""
from __future__ import annotations

import copy
import json
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
