---
id: jira-to-gitlab
display_name: Jira to GitLab
aliases: [ticket to merge request, Jira defect fix, GitLab fix automation]
goals:
  - Fix this Jira defect and open a GitLab merge request.
  - Explain what is already available for Jira to GitLab.
  - Tell me what is missing before ticket-to-MR automation can run.
maturity: partially-ported
recommendation_eligible: false
source_flows: [docs/flows/jira-to-gitlab.md]
implementation:
  skills: []
  plugins: []
  mcp_servers: []
  workflows: []
  tools: []
platforms: [macos, linux, windows]
configuration: []
reads: [historical Jira issue and linked GitLab source and merge-request context]
writes: [historical branch commit merge request and Jira comment]
artifacts: [planned fix contract, commit and merge-request metadata, review reports, summary]
demonstrations: []
troubleshooting: [GitLab tools absent, end-to-end workflow absent, approval contract absent, uncertain write]
---

# Jira to GitLab

## What it solves

The legacy intent connects a Jira defect to a proposed GitLab fix, merge request,
reviews, and Jira comment. Only Jira foundations exist; the flow cannot run.

## Try saying

- “Can Co-Worker fix this Jira defect and open a GitLab MR?”
- “Explain which parts of Jira to GitLab are ported.”
- “What is missing before ticket-to-MR automation can run?”

Ticket/file filters, write preview, fix format, artifact destination, exclusions,
warnings, and rerun policy can be explained only as a future safety contract.

## Questions

Clarify whether the user wants status or a safe alternative. Do not request GitLab
credentials or approval for nonexistent automation.

## Reads and writes

No end-to-end port performs these operations. Jira reads/comments exist separately;
GitLab branches, commits, MRs, and review reads are missing.

## Readiness

`partially-ported`: Jira tools and workflow state machinery do not supply GitLab API
tools, validated fix schema, approvals, workflow, or tests. Do not run this flow.

## Demonstration

No demonstration is available. Never create a branch, commit, MR, or Jira comment as
a test and never present a mocked sequence as live success.

## Artifacts

No current fix or review artifact is generated. Listed artifacts describe the legacy
and planned contract, not output at a usable destination.

## Troubleshooting

The failure is incomplete product maturity. Offer Jira summarization or a manually
supervised coding path; an uncertain write must never be blindly rerun.
