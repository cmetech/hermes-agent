---
id: tol-generation
display_name: TOL Generation
aliases: [test case generation, requirements to spreadsheet, TOL test cases]
goals:
  - Turn this requirements document into structured test cases.
  - Explain the planned TOL test-case spreadsheet flow.
  - Tell me what parser and export pieces TOL Generation needs.
maturity: planned-not-implemented
recommendation_eligible: false
source_flows: [docs/flows/tol-generation.md]
implementation:
  skills: []
  plugins: []
  mcp_servers: []
  workflows: []
  tools: []
platforms: [macos, linux, windows]
configuration: []
reads: [planned user-selected requirements document and parsing options]
writes: [planned local parsed data test-case data and new spreadsheet]
artifacts: [planned parse report, traceable test cases, spreadsheet preview, spreadsheet]
demonstrations: []
troubleshooting: [parser absent, spreadsheet exporter absent, parse gaps, schema failure]
---

# TOL Generation

## What it solves

The source flow turns requirements into traceable telecom test cases and a spreadsheet.
No document parser, export tool, or coordinating workflow is ported.

## Try saying

- “Turn this requirements document into structured test cases.”
- “Explain how the planned TOL flow preserves traceability.”
- “What is missing before TOL Generation can run?”

Document filters, preview, format, destination, exclusions, warnings, and rerun choices
are design topics only until the parser/export implementation exists.

## Questions

Answer scope questions without asking the user to upload proprietary requirements for
an unavailable capability. Do not guess what “TOL” expands to.

## Reads and writes

No current port reads or writes documents. The planned design keeps parsing local,
validates structured cases, and writes a new spreadsheet without overwriting input.

## Readiness

`planned-not-implemented`: Docling parser tooling, schema validation, safe spreadsheet
export, traceability checks, workflow, and tests are missing.

## Demonstration

No runnable synthetic or live demonstration exists; do not fake parsed coverage.

## Artifacts

No current artifact is created. Planned parse, JSON, preview, and spreadsheet outputs
must be labeled future design rather than files at a real destination.

## Troubleshooting

Missing implementation is the blocker. Never claim every requirement is covered,
external processing is local, or a rerun is available.
