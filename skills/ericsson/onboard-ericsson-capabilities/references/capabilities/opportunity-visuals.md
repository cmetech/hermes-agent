---
id: opportunity-visuals
display_name: Opportunity Visuals
aliases: [opportunity infographic, wins visual, pipeline progression]
goals:
  - Create an Ericsson wins visual from an opportunity spreadsheet.
  - Show positive opportunity progression over selected months.
  - Explain exclusions and warnings in a generated visual.
maturity: available
recommendation_eligible: true
source_flows: [docs/flows/image-generation.md]
implementation:
  skills: [skills/ericsson/opportunity-visuals]
  plugins: []
  mcp_servers: []
  workflows: []
  tools: []
platforms: [macos, linux, windows]
configuration:
  - {name: openpyxl, kind: local-software, required: false, guidance: Install only with approval when the selected input is XLSX.}
  - {name: Playwright, kind: local-software, required: false, guidance: Install only with approval when PNG output is requested.}
  - {name: Chromium, kind: local-software, required: false, guidance: Install the local browser only with approval when PNG output is requested.}
reads: [user-selected local CSV JSON or XLSX]
writes: [new local visual artifact directory after confirmation]
artifacts: [source summary, normalized data, exclusions, render manifest, SVG, HTML, optional PNG]
demonstrations: [synthetic-offline]
troubleshooting: [missing input, ambiguous columns, optional PNG dependency, unwritable destination]
---

# Opportunity Visuals

## What it solves

Creates deterministic Ericsson opportunity wins, losses, and progression visuals
from a user-selected local data file.

## Try saying

- “Create an Ericsson wins visual from this opportunity spreadsheet.”
- “Show positive progression from January through June.”
- “Explain which rows were excluded from this visual.”

Follow up with a date or stage filter, request a preview, choose SVG, HTML, or PNG
format, set a destination, ask for exclusions or warnings, or request a safe rerun.

## Questions

Expect one-at-a-time questions about source, view, range, stage semantics, filters,
formats, dimensions, and destination; known answers are not requested again.

## Reads and writes

The source file is read-only. Writing begins only after playback and destination
confirmation, and creates a new artifact directory rather than overwriting output.

## Readiness

CSV/JSON and SVG/HTML need Python and local file access. XLSX optionally needs
openpyxl; PNG optionally needs Playwright plus local Chromium.

## Demonstration

Use the existing fictional showcase fixture offline. Explain expected counts and
files first, then compare the manifest, exclusions, warnings, and rendered result.

## Artifacts

Inspect the source summary, normalized records, exclusions, warnings, hashes,
render manifest, SVG/HTML pages, and optional PNG in the confirmed directory.

## Troubleshooting

Clarify ambiguous mappings or stage semantics one question at a time. A missing PNG
dependency is a documented fallback to SVG/HTML, not a failed visual run.
