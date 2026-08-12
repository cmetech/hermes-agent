---
id: jira-to-gitlab
display_name: Jira to GitLab
aliases: [ticket to merge request, Jira defect fix, GitLab fix automation]
goals:
  - Fix this Jira defect and open a GitLab merge request.
  - Explain what is already available for Jira to GitLab.
  - Tell me what is missing before ticket-to-MR automation can run.
maturity: available
recommendation_eligible: true
source_flows: [docs/flows/jira-to-gitlab.md]
implementation:
  skills: [skills/ericsson/jira-to-gitlab]
  plugins: [plugins/ericsson-jira, plugins/ericsson-gitlab]
  mcp_servers: []
  workflows: [workflows/jira-to-gitlab.yml]
  tools: [jira_get_issue, jira_add_comment, gitlab_resolve_project, gitlab_list_repository_tree, gitlab_read_file, gitlab_read_merge_request, gitlab_create_branch, gitlab_commit_changes, gitlab_create_merge_request]
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

The bounded workflow connects an exact Jira ticket to repository research, an
active-agent fix proposal, approval-gated GitLab writes, review, and a Jira comment.

## Try saying

- “Can Co-Worker fix this Jira defect and open a GitLab MR?”
- “Explain which parts of Jira to GitLab are ported.”
- “What is missing before ticket-to-MR automation can run?”

Clarify ticket and file filters, inspect each write preview, choose fix format and
artifact destination, record exclusions and warnings, and never blindly rerun a write.

## Questions

Clarify the exact ticket key, GitLab project, intended change, and output expectations.
Credentials stay in product settings; approvals apply to exact current actions.

## Reads and writes

The workflow reads Jira and bounded GitLab evidence. Branches, one atomic commit,
merge requests, and Jira comments each remain behind visible approval boundaries.

## Readiness

`available`: enable and configure the GitLab plugin, verify Jira readiness, and use
the reviewed `jira-to-gitlab` workflow with its exact tool and approval contracts.

## Demonstration

Demonstrate bounded reads and dry-run previews first. Never create a branch, commit,
MR, or Jira comment solely to prove the workflow works.

## Artifacts

Inspect per-ticket status, canonical branch/commit/MR links, review evidence,
warnings, and the approved Jira comment at the chosen destination.

## Troubleshooting

Separate readiness, permission, not_found, incomplete evidence, rejected approval,
and uncertain writes. Reconcile uncertainty before any rerun.
