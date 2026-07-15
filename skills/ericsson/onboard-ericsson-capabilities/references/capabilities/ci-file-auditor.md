---
id: ci-file-auditor
display_name: CI File Auditor
aliases: [GitLab CI audit, pipeline policy audit, CI security review]
goals:
  - Audit these GitLab projects for CI policy coverage.
  - Find unsafe CI variables or pipeline practices.
  - Explain what is missing before CI audits can run.
maturity: planned-not-implemented
recommendation_eligible: false
source_flows: [docs/flows/ci-file-auditor.md]
implementation:
  skills: []
  plugins: []
  mcp_servers: []
  workflows: []
  tools: []
platforms: [macos, linux, windows]
configuration: []
reads: [planned project list and read-only GitLab CI pipeline and variable metadata]
writes: [planned local evidence and audit reports only]
artifacts: [planned per-project evidence, findings report, policy coverage report]
demonstrations: []
troubleshooting: [GitLab capability absent, included file warning, permission denial, incomplete evidence]
---

# CI File Auditor

## What it solves

The documented design would inspect GitLab CI evidence and evaluate security,
engineering-practice, and named-policy coverage. It is not implemented.

## Try saying

- “Can Co-Worker audit these GitLab CI projects?”
- “Explain the planned CI policy coverage report.”
- “What is missing before CI File Auditor can run?”

Filters, preview, output format, destination, exclusions, warnings, and rerun behavior
can be discussed as planned design only; none is currently executable.

## Questions

Co-Worker may clarify the informational question but must not solicit credentials or
project files for a capability that cannot run.

## Reads and writes

No current component reads or writes this data. A future design would use read-only
GitLab metadata and local reports, never CI variable values.

## Readiness

`planned-not-implemented`: GitLab tools, bounded collection, validated analysis,
evidence persistence, workflow, and tests are absent. Do not run readiness probes.

## Demonstration

No demonstration is available; do not simulate an audit as successful live output.

## Artifacts

No artifact is generated. Evidence/findings/policy reports describe the planned
format only and must not be presented at a destination as actual results.

## Troubleshooting

The blocker is missing implementation, not user configuration. Do not claim a
penetration test, proof of deployed security, or a safe rerun path.
