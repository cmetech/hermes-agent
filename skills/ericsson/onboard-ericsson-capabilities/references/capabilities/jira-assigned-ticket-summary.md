---
id: jira-assigned-ticket-summary
display_name: Jira Assigned-Ticket Summary
aliases: [my Jira tickets, assigned issue digest, Jira workload summary]
goals:
  - Summarize the unresolved Jira tickets assigned to me.
  - Group my assigned Jira work by priority.
  - Create a chat preview of my Jira ticket digest.
maturity: available
recommendation_eligible: true
source_flows: [docs/flows/jira-assigned-tickets-summary.md]
implementation:
  skills: []
  plugins: [plugins/ericsson-jira]
  mcp_servers: []
  workflows: [workflows/my-tickets-summary.yml]
  tools: [jira_my_tickets]
platforms: [macos, linux, windows]
configuration:
  - {name: JIRA_BASE_URL, kind: static-setting, required: true, guidance: Configure the Jira site URL in protected Tools & Keys.}
  - {name: JIRA_PAT, kind: static-secret, required: true, guidance: Enter the Jira token only in protected Tools & Keys and never in chat.}
  - {name: deliver_to, kind: workflow-input, required: false, guidance: Choose chat by default or email only when the optional Windows Outlook delivery path is ready.}
  - {name: Windows, kind: local-software, required: false, guidance: Required only for optional email delivery; chat delivery remains cross-platform.}
  - {name: Classic Outlook desktop, kind: local-software, required: false, guidance: Required only for optional email delivery and must be open signed in and online.}
  - {name: PowerShell and Outlook COM, kind: local-software, required: false, guidance: Required only for optional email delivery.}
  - {name: Outlook mailbox access, kind: permission, required: false, guidance: Required only for optional email delivery to the authorized mailbox.}
  - {name: Outlook MCP, kind: local-software, required: false, guidance: Required only for optional email delivery and must be discoverable and ready.}
reads: [assigned unresolved Jira issues and fields needed for the digest]
writes: [workflow run artifacts, optional approved Outlook email]
artifacts: [tickets JSON, summary Markdown, workflow state]
demonstrations: [synthetic-offline, read-only-live]
troubleshooting: [missing Jira configuration, authentication failure, empty or truncated result, interrupted approved email]
---

# Jira Assigned-Ticket Summary

## What it solves

Builds a prioritized digest of up to 25 unresolved Jira issues assigned to the
authenticated user, preserving keys, status, priority, and detected GitLab links.

## Try saying

- “Summarize the Jira tickets assigned to me.”
- “Give me a priority-grouped preview of my open Jira work.”
- “Explain why a ticket was excluded from my assigned-ticket digest.”

Follow up by choosing chat or optional email, ask for a preview, choose the Markdown
format and run destination, inspect exclusions or warnings, or rerun. The workflow's
assigned-and-unresolved filter is fixed.

## Questions

Expect one question about chat versus optional approved email delivery.
The bundled workflow is fixed at 25; it does not accept an adjustable limit input.

## Reads and writes

It reads assigned Jira issues. The workflow writes local run artifacts; email is a
separate side effect shown for approval after the summary exists.

## Readiness

Chat readiness requires packaging, Jira tool discovery, both Jira configuration names
without values, authentication, then a small read-only query.
A direct `jira_my_tickets(max_results=...)` probe is separate from workflow execution
and may use a smaller result count; the bundled workflow remains fixed at 25.
Email readiness additionally requires Windows, classic Outlook, PowerShell/COM,
mailbox permission, and Outlook MCP discovery and authentication.

## Demonstration

For a credential-free introduction, use shipped fictional fixture
`fixtures/synthetic-jira-tickets.json` (`SYNTH-JIRA-DIGEST-001`) and golden
`fixtures/expected-jira-summary.md`. From this skill directory, validate them with
`python scripts/render_synthetic_jira.py --check`, then render to a new confirmed
destination with `python scripts/render_synthetic_jira.py --output <new-path>`.
The helper refuses an existing output path. This synthetic/offline mode teaches the
summary and artifact shape; it does not validate a live Jira connection. A small
permitted read-only Jira query remains the live readiness demonstration. Neither
mode may add a Jira comment or send email merely to prove configuration.

## Artifacts

Inspect `tickets.json`, `summary.md`, and workflow state in the reported run
directory. An empty result after a read error is not a valid empty-workload summary.

## Troubleshooting

Separate missing configuration, rejected authentication, permissions, true zero
matches, and truncation. After an interrupted email, verify whether it sent before
any forced rerun.
