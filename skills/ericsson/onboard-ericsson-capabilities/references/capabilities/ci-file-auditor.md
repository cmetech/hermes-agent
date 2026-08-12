---
id: ci-file-auditor
display_name: CI File Auditor
aliases: [GitLab CI audit, pipeline policy audit, CI security review]
goals:
  - Audit these GitLab projects for CI policy coverage.
  - Find unsafe CI variables or pipeline practices.
  - Explain what is missing before CI audits can run.
maturity: partially-ported
recommendation_eligible: false
source_flows: [docs/flows/ci-file-auditor.md]
implementation:
  skills: []
  plugins: [plugins/ericsson-gitlab]
  mcp_servers: []
  workflows: []
  tools: [gitlab_resolve_project, gitlab_read_file, gitlab_list_pipelines, gitlab_inspect_ci]
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

The implemented GitLab reads inspect bounded CI files, pipelines, includes, and
variable metadata. Multi-project policy scoring and report generation remain deferred.

## Try saying

- “Can Co-Worker audit these GitLab CI projects?”
- “Explain the planned CI policy coverage report.”
- “What is missing before CI File Auditor can run?”

Clarify project filters, ref, output format and destination, exclusions, warnings,
preview the bounded collection scope, and set safe read-only rerun behavior.

## Questions

Clarify whether the user wants current bounded evidence or the deferred named-policy
report. Credentials belong only in the product configuration surface.

## Reads and writes

The GitLab plugin performs read-only bounded collection and never returns CI variable
values. The cross-project findings and policy-coverage artifact writer is not ported.

## Readiness

`partially-ported`: GitLab collection and CI investigation guidance are available;
the legacy multi-project workflow, policy evaluator, and persisted reports are not.

## Demonstration

A read-only live inspection can demonstrate the implemented evidence boundary. Do
not present it as a completed policy audit or proof of deployed security.

## Artifacts

Current output is bounded evidence with warning and truncation facts. Findings and
policy reports remain planned and must not be presented at a destination as actual.

## Troubleshooting

Distinguish disabled plugin, configuration, permission, unsupported includes, and
incomplete evidence from the deferred policy layer. Safe reads may be rerun.
