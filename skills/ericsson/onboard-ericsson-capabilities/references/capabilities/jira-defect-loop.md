---
id: jira-defect-loop
display_name: Jira Defect Loop
aliases: [batch defect automation, assigned Jira defect triage, Jira GitLab batch loop]
goals:
  - Triage and fix all my assigned Jira defects.
  - Explain the bounded Jira Defect Loop.
  - Review Jira Defect Loop run artifacts.
maturity: available
recommendation_eligible: true
source_flows: [docs/flows/jira-defect-loop.md]
implementation:
  skills: [skills/ericsson/jira-to-gitlab]
  plugins: [plugins/workflow, plugins/ericsson-jira, plugins/ericsson-gitlab]
  mcp_servers: []
  workflows: [workflows/jira-defect-loop.yml]
  tools: [jira_my_tickets, jira_get_issue, jira_add_comment, gitlab_resolve_project, gitlab_list_repository_tree, gitlab_read_file, gitlab_create_branch, gitlab_commit_changes, gitlab_create_merge_request, gitlab_read_merge_request]
platforms: [macos, linux, windows]
configuration: []
reads: [assigned Jira issues and linked GitLab repository and merge request context]
writes: [individually approved Jira comments, branches, commits, and merge requests]
artifacts: [typed per-ticket run records, ordered aggregate JSON, aggregate Markdown]
demonstrations: []
troubleshooting: [connector readiness, malformed or over-limit manifests, per-ticket reconciliation stops, current approval requirements]
---

# Jira Defect Loop

## What it solves

The workflow triages an immutable manifest of assigned defects and composes bounded,
per-ticket Jira/GitLab outcomes with durable batch history.

## Try saying

- “Can Co-Worker triage and fix all my assigned Jira defects?”
- “Explain how the Jira Defect Loop handles approvals.”
- “Show me the JSON and Markdown artifacts from my defect batch.”

The workflow makes exactly one ordered, deduplicated Jira manifest with at most 25
keys. Tickets discovered after that read wait for the next run.

## Questions

Confirm that the Jira and GitLab connectors are ready. Explain that every outward
operation receives its own current approval; batch intent never authorizes a write.
The assigned-and-unresolved Jira filter, 25-ticket limit, and manifest order are fixed.
Review each exact write preview, output format and artifact destination, exclusions,
and warnings. Reconcile an uncertain write before any rerun.

## Reads and writes

The workflow reads Jira tickets and bounded linked GitLab repository context. It may
create a branch, commit, merge request, or Jira comment only after approval for that
exact target, payload, and intent digest. It sends no email and writes no spreadsheet.

## Readiness

`available`: the durable `loop_group` processes one immutable manifest key per
iteration, up to 25. `jira-to-gitlab` remains available for one-ticket work. The other
seven assessed legacy iterative flows remain unmigrated.

## Demonstration

Use a read-only or fixture-backed run for demonstration. Never approve Jira comments,
branches, commits, or merge requests merely to test configuration.

## Artifacts

Hermes authenticated workflow history stores a typed per-ticket terminal record and
final ordered aggregate JSON and Markdown. It is the batch system of record.

## Troubleshooting

Malformed, duplicate, or over-limit manifest responses fail before writes. Expected
domain outcomes are terminal records and allow the next ticket. An uncertain write is
not `safely_skipped`: it stops for read-only reconciliation and is never replayed
blindly. Do not treat model triage as permission.
