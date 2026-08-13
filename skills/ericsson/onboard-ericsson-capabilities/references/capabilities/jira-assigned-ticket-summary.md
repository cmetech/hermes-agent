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
  skills: [skills/ericsson/jira]
  plugins: [plugins/ericsson-jira]
  mcp_servers: []
  workflows: [workflows/my-tickets-summary.yml]
  tools: [jira_my_tickets]
platforms: [macos, linux, windows]
configuration:
  - {name: base_url, kind: static-setting, required: true, guidance: Configure the exact Jira origin through the standalone connector's Tools settings.}
  - {name: auth_mode, kind: static-setting, required: true, guidance: Choose bearer PAT or basic email/API-token authentication through Tools.}
  - {name: pat, kind: static-secret, required: true, guidance: When bearer mode is selected store the token only through the protected write-only Tools field.}
  - {name: email, kind: static-setting, required: true, guidance: Configure the Jira account email when basic mode is selected.}
  - {name: api_token, kind: static-secret, required: true, guidance: When basic mode is selected store the API token only through the protected write-only Tools field.}
  - {name: rest_api_version, kind: static-setting, required: true, guidance: Keep automatic v3-to-classified-v2 compatibility unless the deployment requires an explicit version.}
  - {name: transport, kind: static-setting, required: true, guidance: Prefer native or automatic transport and select curl only for a proven compatibility deployment.}
  - {name: curl_executable, kind: static-setting, required: false, guidance: Select an approved absolute executable only when compatibility transport is required.}
  - {name: request_timeout_seconds, kind: static-setting, required: false, guidance: Keep the request deadline within its bounded Tools range.}
  - {name: default_max_results, kind: static-setting, required: false, guidance: Keep the finite default result count within its bounded Tools range.}
reads: [assigned unresolved Jira issues and fields needed for the digest]
writes: [workflow run state only]
artifacts: [bounded ticket and summary workflow outputs]
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

The bundled workflow is fixed at 25 and read-only; it does not accept an adjustable
limit input. Use bounded Jira search for a different explicit query.

## Reads and writes

It reads assigned Jira issues and returns bounded workflow state. It performs no Jira
write or email delivery.

## Readiness

Readiness requires the explicitly enabled standalone connector, protected Tools
configuration without revealing values, authentication, permission, then a small read-only query.
A direct `jira_my_tickets(max_results=...)` probe is separate from workflow execution
and may use a smaller result count; the bundled workflow remains fixed at 25.

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

Inspect the bounded fetch and summary outputs in workflow state. An empty result after
a read error is not a valid empty-workload summary.

## Troubleshooting

Separate missing configuration, rejected authentication, permissions, true zero
matches, and truncation. After an interrupted email, verify whether it sent before
any forced rerun.
