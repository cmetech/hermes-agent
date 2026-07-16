# Design: managed-skill force-update (reliable delivery of skills we ship)

- **Date:** 2026-07-16
- **Status:** Approved design — implementing
- **Follows:** the `__pycache__` skill-hash fix (`d6114d3b2`) — that stops *future* poisoning; this closes the gap for *already-poisoned* installs of skills we own.

## Problem

`skills_sync` refuses to overwrite a skill whose on-disk hash differs from the recorded
`origin_hash` (treats it as "user-modified"). That's correct for user skills, but wrong for
**skills we deliver and manage** (our conformance tool + vendored Ericsson skills): once an
install's manifest entry is poisoned (e.g. bytecode recorded into `origin_hash` under the old
hash logic, or any drift), the sync can no longer tell "user edited it" from "old + poisoned",
so it safely refuses — and the skill freezes at its installed version forever. Observed live:
`gateway-toolcall-parity` and `workflow-builder` both stuck `user-modified` on a loop24 box
even after the fix shipped. The `_dir_hash` fix cannot un-poison an existing `origin_hash`.

## Solution

Mark the skills we own as **managed** and let the sync **force them to the bundled version
when the bundled copy changed, bypassing the user-modified skip** — using the existing
crash-safe backup/restore path. Managed status is **descriptor-driven** (mirrors the existing
`curation.skills.exclude`/`rename`/`disabledByDefault` model), read at runtime, fail-OPEN.

### Semantics (precise)

- A managed skill that is flagged user-modified **and** whose `bundled_hash != user_hash`
  (there genuinely is a different bundled version) → **force-update** to bundled, re-record a
  clean `origin_hash`. Crash-safe (backup → copy → restore-on-failure), same as normal update.
- Managed still **respects an explicit user *deletion*** (in manifest, absent from disk → not
  re-added). We fix the poison; we don't resurrect deliberately-removed skills.
- Force-update **overwrites local edits to managed skills by design** — they're ours, not
  user-authored (accepted trade-off). Non-managed skills are completely unaffected.
- Fail-OPEN: no descriptor / no `managed` list / any error → empty set → **current behavior**.

### Scope — the 5 skills we deliver

`gateway-toolcall-parity`, `onboard-ericsson-capabilities`, `opportunity-visuals`,
`workflow-builder`, `workflow-orchestrator`. Same list on both brands (both ship the same
delivered content).

## Files

- `hermes_cli/brand_config.py` — add `get_managed_skills(slug, root)` + `active_managed_skills(root)`,
  byte-for-byte mirroring `get_hidden_skills`/`active_hidden_skills` (reads
  `curation.skills.managed`, fail-OPEN).
- `tools/skills_sync.py`:
  - `_managed_skill_names()` — lazy `active_managed_skills()`, fail-OPEN to `set()` (the
    patch point for tests; keeps skills_sync decoupled from brand_config import-time).
  - Extract the existing update-with-backup block (the `bundled_hash != origin_hash` body) into
    `_update_skill(skill_src, dest, skill_name, bundled_hash, manifest, quiet) -> bool` and call
    it from BOTH the normal update path and the new managed path (DRY — no duplicated
    backup/restore logic).
  - In the user-modified branch: if `skill_name in managed and bundled_hash != user_hash` →
    `_update_skill(...)`; on success append to `updated` **and** a new `managed_forced` list;
    print `⤓ {name} (managed → forced to bundled)`. Else the existing user-modified skip.
  - Add `managed_forced: []` to the returned result dict and the two early-return shapes.
- `brands/otto.json` + `brands/loop24.json` — add `curation.skills.managed: [...5 names...]`.

## Tests (TDD)

- `tests/hermes_cli/test_brand_curation.py`:
  - `get_managed_skills` returns the descriptor list; fail-OPEN on missing descriptor/section.
- `tests/tools/test_skills_sync.py` (mirror the existing `test_user_modified_skill_not_overwritten`,
  patching `tools.skills_sync._managed_skill_names`):
  - `test_managed_skill_force_updated_despite_user_modified` — a managed, user-modified skill
    with a newer bundled version is in `updated` + `managed_forced`, NOT `user_modified`, and its
    content is overwritten to bundled.
  - `test_managed_skill_respects_user_deletion` — a managed skill in manifest but absent from
    disk is NOT re-added.
  - Regression: a NON-managed user-modified skill still lands in `user_modified` (existing test).

## Gates / release

- `./venv/bin/python -m pytest tests/tools/test_skills_sync.py tests/hermes_cli/test_brand_curation.py`
  green; full skills_sync suite no regressions.
- `node scripts/brand/generate.mjs <brand> --check` → 9/9 (no emitter touched).
- Merge `base` → `otto` + `loop24`; push; cut paired **v1.1.4** prerelease pinned to the new SHAs.
- On update, already-poisoned installs of the 5 skills **auto-heal** on the next backend sync —
  no per-box reset. New installs stay clean via the earlier `_dir_hash` fix.

## Non-goals

- No durable preservation of user edits to managed skills (backup is crash-safety only).
- No resurrection of user-deleted skills.
- No change to non-managed skill behavior. No new brand emitter (descriptor value only →
  `generate --check` stays 9/9).
