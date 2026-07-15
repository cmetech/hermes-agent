---
id: windows-laptop-diagnostic
display_name: Windows Laptop Diagnostic
aliases: [Windows system report, laptop troubleshooting, PowerShell diagnostic]
goals:
  - Diagnose a problem on my Windows laptop.
  - Explain the planned Windows laptop diagnostic.
  - Tell me what the diagnostic report would inspect.
maturity: planned-not-implemented
recommendation_eligible: false
source_flows: [docs/flows/windows-laptop-diagnostic.md]
implementation:
  skills: []
  plugins: []
  mcp_servers: []
  workflows: []
  tools: []
platforms: [windows]
configuration: []
reads: [planned read-only Windows hardware process service power and network evidence]
writes: [planned timestamped local diagnostic report only]
artifacts: [planned complete or labeled partial local report and interpretation]
demonstrations: []
troubleshooting: [reviewed script absent, timeout, partial collection, sensitive system inventory]
---

# Windows Laptop Diagnostic

## What it solves

The source intent collects read-only Windows evidence for an agent to interpret. The
reviewed narrow script and skill are not packaged, so it cannot run.

## Try saying

- “Can Co-Worker diagnose my Windows laptop?”
- “Explain the planned laptop diagnostic capability.”
- “What would the diagnostic report inspect?”

Evidence filters, report preview, output format/destination, exclusions, warnings,
and rerun behavior can be explained only as future design.

## Questions

Clarify the informational request without asking for real system inventories or
permission to run arbitrary PowerShell.

## Reads and writes

No current port reads the machine or writes a report. A future fixed script would
collect read-only evidence and label partial results without performing remediation.

## Readiness

`planned-not-implemented` and Windows-only: reviewed script, narrow invocation,
timeout/cancellation, retention/redaction guidance, skill, and tests are missing.

## Demonstration

No demonstration is available. Do not run the unreviewed legacy script as a workaround.

## Artifacts

No current report exists. Planned output must remain local, redact sensitive system
details before sharing, and never be claimed complete at a destination after timeout.

## Troubleshooting

Missing implementation is not a local configuration failure. Do not recommend an
automatic remediation or arbitrary-PowerShell rerun.
