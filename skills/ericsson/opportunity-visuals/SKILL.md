---
name: opportunity-visuals
description: Create Ericsson opportunity progression visuals.
version: 1.0.0
author: Corey Ellis (@cmetech)
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [Ericsson, Opportunities, Visualization, Sales]
---

# Opportunity Visuals Skill

Turn opportunity pipeline history into exact Ericsson-branded progression
tables. Its file helpers are local and deterministic; it is not a general
image generator.

## When to Use

Use for natural-language requests that combine opportunity/deal data with
wins, losses, all-stage progression, positive progression, a Loop24
opportunity image, or an Ericsson slide-ready progression visual. Do not use
for a generic image, photo edit, unrelated chart, or a request that merely
contains the word opportunity. Route those generic image requests to the
ordinary image capability. Never send opportunity data to `image_generate`.

## Prerequisites

Python and local file access provide CSV/JSON plus SVG/HTML. XLSX requires
`openpyxl>=3.1.5`. PNG additionally requires `playwright>=1.52` and local
Chromium. Missing PNG support does not block SVG/HTML.

## How to Run

1. Inspect the request and source metadata before asking anything.
2. Ask only for missing decisions, one question at a time, following
   `references/interview-guide.md`.
3. Read `references/data-contract.md`; confirm ambiguous columns, months, and
   stage semantics without changing display labels.
4. Run `scripts/prepare_opportunities.py inspect` as needed, then run
   `scripts/prepare_opportunities.py analyze` with the proposed view, mapping,
   months, semantics, and filters. Analysis is read-only and creates no output
   artifacts.
5. Ask about each output-affecting unresolved terminal status one question at
   a time. Add confirmed non-terminals to `non_terminal_stages` and rerun
   analyze. Then resolve any remaining inclusion-affecting direction one
   question at a time, updating semantics and rerunning after each answer.
6. Play back source, view, range, rules, filters, formats, and destination.
   Wait for the user to confirm before writing.
7. Run `scripts/prepare_opportunities.py prepare` into a new timestamped output
   directory, then run `scripts/render_opportunity_visual.py` using
   `normalized-data.json`.
8. Read `exclusions.json` and `render-manifest.json`; report included,
   excluded, warning, page, and PNG fallback counts with artifact paths.

## Quick Reference

- Views: wins, losses, all-stage progression, positive progression.
- Default size: 1920×1080.
- Canonical output: SVG; preview: HTML; optional raster: PNG.
- No helper-specific API key and no remote renderer in the file helpers.
- Source files are read-only and blank months remain blank.

## Procedure

Use the exact CLI forms documented in `references/data-contract.md`. If
inspection reports more than one sheet or ambiguous columns/months, ask one
resolving question and rerun. Use analyze to find unknown transitions before
preparing artifacts; confirm terminal status, rerun, confirm direction, then
rerun again. Do not play back or prepare while analysis contains an
output-impact unknown. For confidential data, explain that file helpers remain
local but minimal metadata and stage labels used for the interview may enter
the model-backed chat. Do not ask the user to paste confidential rows unless
their configured model and privacy policy permit it. Confirm the output
directory, then play back the execution summary before writing. Never overwrite
an existing run directory by default.

## Pitfalls

- Do not invent, rename, carry forward, or backfill stages.
- Do not infer monthly probability movement from one current Probability field.
- Do not classify an unknown stage order as forward or backward.
- Do not render user values as HTML or SVG markup.
- Do not claim PNG failure means the SVG/HTML run failed.
- Do not use this skill for generic image generation.

## Verification

Confirm artifact hashes and counts in `render-manifest.json`, review every
warning/exclusion, and apply `references/visual-rules.md`. For a showcase,
use only the repository-owned synthetic fixture pack when it is available.
