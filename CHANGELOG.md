# OTTO Desktop — Changelog

Notable changes to the OTTO desktop app. OTTO is a branded fork of Hermes;
releases are published at https://github.com/cmetech/otto/releases.

## Unreleased

- Docs: add this changelog (also serves as the first "See what's new" test entry —
  installing v0.1.1 and checking for updates should surface this commit).

## v0.1.1 — 2026-07-12

- Fix: desktop self-update now tracks the `otto` branch (seeded from the install
  stamp) instead of falling back to upstream `main` — removes the phantom
  "update available" and prevents an update from pulling upstream Hermes over OTTO.
- Fix: **Settings → About → Release notes** now opens the OTTO releases page
  (`cmetech/otto`) instead of upstream.
- Fix: **See what's new** now lists OTTO commits (`git log HEAD..origin/otto`).
- Fix: macOS self-update relaunch now resolves the rebuilt `OTTO.app` (was
  searching for a non-existent `Hermes.app`).

## v0.1.0 — 2026-07-12

- First OTTO desktop release: Windows (nsis + msi) and macOS (dmg + zip)
  installers, built by CI in `cmetech/otto` and published as GitHub Release assets.
- Thin-shell install model: first launch bootstraps the OTTO source build
  (managed uv + clone `cmetech/hermes-agent@otto` + build). Self-update via
  `git pull` + rebuild.
- Unsigned builds (SmartScreen / Gatekeeper warn on first run) — signing is a
  planned follow-up.
