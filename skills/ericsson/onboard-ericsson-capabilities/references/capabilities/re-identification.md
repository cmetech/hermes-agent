---
id: re-identification
display_name: Re-Identification
aliases: [restore pseudonymized file, privacy-vault restoration, token mapping restore]
goals:
  - Restore a legacy pseudonymized file.
  - Explain why Re-Identification cannot run.
  - Tell me which mapping the old restoration flow requires.
maturity: planned-not-implemented
recommendation_eligible: false
source_flows: [docs/flows/re-identification.md]
implementation:
  skills: []
  plugins: []
  mcp_servers: []
  workflows: []
  tools: []
platforms: [macos, linux, windows]
configuration: []
reads: [historical anonymized file session key and protected token mapping]
writes: [historical protected restored copy]
artifacts: [planned restored document and safe restoration summary]
demonstrations: []
troubleshooting: [mapping-producing capability unavailable, missing or ambiguous mapping, authorization risk]
---

# Re-Identification

## What it solves

The legacy flow restores sensitive values using a matching protected mapping.
No restoration port exists, and its Pseudonymization mapping dependency is unavailable.

## Try saying

- “Can Co-Worker restore a legacy pseudonymized file?”
- “Explain why Re-Identification cannot run.”
- “What mapping does the old restoration flow require?”

Session filters, restoration preview, output format/destination, exclusions, warnings,
and rerun controls are historical design topics only.

## Questions

Answer status without asking the user to paste or upload an anonymized file, session
mapping, or restored sensitive content.

## Reads and writes

No current implementation reads mappings or writes restored files. Historical reads
and writes describe the source flow only.

## Readiness

`planned-not-implemented`: protected mapping storage, lookup/restore tools, controls,
and tests are absent; the required mapping-producing capability is unavailable.

## Demonstration

No demonstration is available. Never guess token values or simulate disclosure.

## Artifacts

No restored artifact is generated. Do not claim a session key alone proves a mapping
or identify a destination for nonexistent output.

## Troubleshooting

Fail status-honestly on unavailable mapping and implementation. Do not infer a new
roadmap promise or suggest an unsafe manual rerun with sensitive mappings.
