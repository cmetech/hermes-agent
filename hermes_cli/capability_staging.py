"""Brand capability/persona staging seam + startup orchestration.

Dependency-light (stdlib + brand_config only) so it is safe on the CLI/backend startup
path. Today this is a NO-OP: every brand descriptor ships empty capabilitySets/personaSets.
The seam is wired now so P2 (Ericsson capabilities) and P3 (out-of-box personas) bolt on
without changing startup control flow — they fill in resolve_capability_bundle + the
staging targets. Everything here is fail-safe: capability staging must NEVER block startup.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from hermes_cli import brand_config

log = logging.getLogger(__name__)

STAGING_MANIFEST = ".brand-staging.json"
STAGING_SCHEMA_VERSION = 1


def resolve_capability_bundle(set_name: str, root: Path | None = None) -> Path | None:
    """Resolve a named capability/persona set to a directory of stageable content.

    P2/P3 wire the real source (a common capabilities repo or a released artifact). Today
    there is no source, so this always returns None — even a NON-empty set no-ops gracefully.
    """
    log.debug("capability set %r requested but no resolver configured yet (P2/P3)", set_name)
    return None


def stage_brand_capabilities(home: Path | str, root: Path | None = None) -> None:
    """Stage the active brand's capabilitySets/personaSets into `home`.

    No-op today (empty sets). The dormant branch below is the contract P2/P3 implement:
    resolve each named set to a bundle and copy it into $HERMES_HOME/{skills,plugins,...},
    recording a manifest so re-staging is idempotent.
    """
    slug = brand_config.resolve_active_brand(root)
    try:
        descriptor = brand_config.load_brand(slug, root)
    except Exception:
        log.debug("stage_brand_capabilities: could not load descriptor for %r", slug, exc_info=True)
        return

    cap_sets = descriptor.get("capabilitySets") or []
    persona_sets = descriptor.get("personaSets") or []
    if not cap_sets and not persona_sets:
        return  # today's only path — a true no-op

    # --- dormant until P2/P3 provide a bundle source ---
    staged: list[str] = []
    for name in [*cap_sets, *persona_sets]:
        bundle = resolve_capability_bundle(name, root)
        if bundle is None:
            continue  # no source yet → skip gracefully
        # P2/P3: copy bundle contents into home/{skills,plugins,mcp,agents,personas}
        staged.append(name)

    if staged:
        manifest = Path(home) / STAGING_MANIFEST
        manifest.write_text(
            json.dumps({"schemaVersion": STAGING_SCHEMA_VERSION, "stagedSets": staged}, indent=2) + "\n",
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
