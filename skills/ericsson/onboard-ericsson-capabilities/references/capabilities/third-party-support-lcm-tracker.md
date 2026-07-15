---
id: third-party-support-lcm-tracker
display_name: 3PP Support and LCM Tracker
aliases: [third-party lifecycle tracker, 3PP LCM, EOS EOM spreadsheet]
goals:
  - Check lifecycle and EOS dates in this third-party tracker.
  - Explain how the planned LCM enrichment records evidence.
  - Tell me what is missing before lifecycle workbook updates can run.
maturity: planned-not-implemented
recommendation_eligible: false
source_flows: [docs/flows/third-party-support-lcm-tracker.md]
implementation:
  skills: []
  plugins: []
  mcp_servers: []
  workflows: []
  tools: []
platforms: [macos, linux, windows]
configuration: []
reads: [planned workbook sheet column mapping and vendor reference sources]
writes: [planned new enriched workbook after review]
artifacts: [planned row checkpoints, evidence provenance, previews, enriched workbook]
demonstrations: []
troubleshooting: [spreadsheet tools absent, research workflow absent, inaccessible evidence, unsafe formula or URL]
---

# 3PP Support and LCM Tracker

## What it solves

The source flow researches product lifecycle data and proposes evidence-backed
updates beside existing workbook values. It is not ported.

## Try saying

- “Check lifecycle and EOS dates in this 3PP tracker.”
- “Explain the planned LCM evidence and confidence fields.”
- “What is missing before Co-Worker can update this workbook?”

Row filters, preview, spreadsheet format/destination, exclusions, warnings, and
checkpoint rerun behavior are planned design only.

## Questions

Discuss desired outcome without asking for a real workbook or credentials while the
capability is unavailable.

## Reads and writes

No current port reads or writes the workbook. A future design would keep the input
unchanged and write a reviewed new file with source URLs and retrieval dates.

## Readiness

`planned-not-implemented`: spreadsheet tools, safe research, structured validation,
checkpoints, review workflow, and tests are absent.

## Demonstration

No runnable demonstration exists; do not fabricate lifecycle results or provenance.

## Artifacts

No current artifact is generated. Planned checkpoints, evidence distribution,
previews, and workbook are not output at a selectable destination.

## Troubleshooting

The blocker is missing implementation. Never claim model knowledge is authoritative,
inaccessible evidence proves absence, or a safe full rerun exists.
