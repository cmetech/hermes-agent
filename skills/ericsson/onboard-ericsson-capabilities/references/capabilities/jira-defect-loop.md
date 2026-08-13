---
id: jira-defect-loop
display_name: Jira Defect Loop
aliases: [batch defect automation, assigned Jira defect triage, Jira GitLab batch loop]
goals:
  - Triage and fix all my assigned Jira defects.
  - Explain the planned Jira Defect Loop.
  - Tell me why the batch defect workflow cannot run.
maturity: partially-ported
recommendation_eligible: false
source_flows: [docs/flows/jira-defect-loop.md]
implementation:
  skills: []
  plugins: []
  mcp_servers: []
  workflows: []
  tools: []
platforms: [macos, linux, windows]
configuration: []
reads: [historical assigned Jira issues and linked GitLab repository and review context]
writes: [historical comments branches commits merge requests and optional email]
artifacts: [planned triage classifications, per-ticket run records, reviews, skipped reasons, aggregate summary]
demonstrations: []
troubleshooting: [Phase 6 loop_group absent, batch aggregation absent, per-ticket uncertainty, batch side-effect risk]
---

# Jira Defect Loop

## What it solves

The legacy batch flow triages assigned defects and composes per-ticket fix outcomes.
Jira/GitLab foundations and explicitly single-ticket paths exist, but safe batch execution does not.

## Try saying

- “Can Co-Worker triage and fix all my assigned Jira defects?”
- “Explain how the planned Jira Defect Loop handles approvals.”
- “Why can’t the batch defect workflow run yet?”

Ticket filters, triage preview, summary format/destination, exclusions, warnings, and
rerun policy may be discussed only as planned behavior.

## Questions

Clarify the informational goal, then offer available Jira summary guidance. Do not
collect a batch or authorize writes for an unimplemented flow.

## Reads and writes

No batch flow currently reads or changes these systems. The historical design includes
many high-consequence GitLab/Jira writes and optional Outlook delivery.

## Readiness

`partially-ported`: ticket research/triage and supervised one-ticket workflows are
available. Exact batch iteration, per-ticket isolation under concurrency, aggregate
terminal state, and safe rerun semantics remain deferred to Phase 6 `loop_group`.

## Demonstration

No demonstration is available. Jira comments, branches, commits, MRs, and email must
never be used as configuration tests.

## Artifacts

No triage or per-ticket artifacts are produced. Planned records and aggregate summary
are not available at any destination.

## Troubleshooting

Do not treat model triage as permission or claim the single-ticket showcase is batch
parity. There is no safe batch rerun; offer Jira summary, search, or one ticket instead.
