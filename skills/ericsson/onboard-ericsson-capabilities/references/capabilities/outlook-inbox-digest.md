---
id: outlook-inbox-digest
display_name: Outlook Inbox Digest
aliases: [unread email digest, Outlook summary, inbox action list]
goals:
  - Give me a digest of unread Outlook messages.
  - Summarize unread email from the last eight hours.
  - Explain the action-needed items in my latest inbox digest.
maturity: available
recommendation_eligible: true
source_flows: [docs/flows/search-and-read-emails.md]
implementation:
  skills: []
  plugins: []
  mcp_servers: [outlook]
  workflows: [workflows/inbox-digest.yml]
  tools: []
platforms: [windows]
configuration:
  - {name: Windows, kind: local-software, required: true, guidance: Run on a supported Windows desktop.}
  - {name: Classic Outlook desktop, kind: local-software, required: true, guidance: Keep classic Outlook open signed in and online.}
  - {name: PowerShell and Outlook COM, kind: local-software, required: true, guidance: Confirm PowerShell and Outlook COM before starting the MCP server.}
  - {name: Outlook mailbox access, kind: permission, required: true, guidance: Read only mail the signed-in user is authorized to access.}
  - {name: since, kind: workflow-input, required: false, guidance: Choose the lookback duration; the workflow default is one day.}
  - {name: limit, kind: workflow-input, required: false, guidance: Choose the maximum messages; the workflow default is fifteen.}
reads: [unread Inbox metadata and selected full bodies when important or truncated]
writes: [new workflow run artifacts]
artifacts: [inbox JSON, digest Markdown, workflow state]
demonstrations: [read-only-live]
troubleshooting: [Outlook unavailable, partial message collection, workflow failure, sensitive content handling]
---

# Outlook Inbox Digest

## What it solves

Collects recent unread Inbox mail and produces a grouped digest plus at most five
action-needed items without sending or replying.

## Try saying

- “Give me a digest of unread Outlook messages.”
- “Summarize unread Outlook mail from the last eight hours.”
- “Create an inbox digest limited to 15 messages.”

Follow up with timeframe or limit filters, request a collection preview, choose a
summary format or destination, inspect exclusions/warnings, or start a fresh rerun.

## Questions

Expect one-at-a-time questions for timeframe, limit, and whether important truncated
messages may be read in full.

## Reads and writes

It reads unread Inbox data locally and writes only a unique workflow run directory.
The workflow contains no send or reply node.

## Readiness

Validate Windows, Outlook MCP, PowerShell/COM, mailbox access, then a small read-only
message list. Never treat a configured name or zero-result error as ready.

## Demonstration

The current demonstration uses a permitted small read-only live collection; explain
expected message scope first and never send or reply during the test.

## Artifacts

Inspect `inbox.json`, `digest.md`, and workflow state under the reported unique run
destination. The digest should label partial collection and warnings.

## Troubleshooting

Differentiate no matches, Outlook offline, item-read failure, and interrupted
workflow state. Use orchestrator resume rules rather than blindly rerunning a node.
