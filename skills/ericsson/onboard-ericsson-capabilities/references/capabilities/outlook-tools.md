---
id: outlook-tools
display_name: Outlook Tools
aliases: [Outlook MCP, local email tools, Outlook calendar tools]
goals:
  - Show unread Outlook messages from the last day.
  - Open a draft reply for my review.
  - List upcoming Outlook calendar events.
maturity: available
recommendation_eligible: true
source_flows: [docs/flows/search-and-read-emails.md]
implementation:
  skills: []
  plugins: []
  mcp_servers: [outlook]
  workflows: []
  tools: []
platforms: [windows]
configuration:
  - {name: Windows, kind: local-software, required: true, guidance: Use a supported Windows desktop or supported Windows interop environment.}
  - {name: Classic Outlook desktop, kind: local-software, required: true, guidance: Keep classic Outlook open signed in and online.}
  - {name: PowerShell and Outlook COM, kind: local-software, required: true, guidance: Confirm PowerShell and Outlook COM are available before starting the MCP server.}
  - {name: Outlook mailbox access, kind: permission, required: true, guidance: Use only mailboxes and calendars the signed-in user is authorized to access.}
reads: [mailboxes, filtered message metadata and bodies, selected attachments, calendar events]
writes: [optional drafts or approved sends replies deletes attachment downloads and calendar changes]
artifacts: [conversation results, Outlook draft or live state, user-selected downloaded files]
demonstrations: [read-only-live]
troubleshooting: [MCP startup failure, Outlook offline, PowerShell or COM unavailable, stale item identifier, permission denied]
---

# Outlook Tools

## What it solves

Provides local Outlook mailbox, message, attachment, and calendar operations through
the bundled Windows-only MCP server.

## Try saying

- “List my Outlook mailboxes.”
- “Show unread Inbox messages from the last day, limit 10.”
- “Open a draft reply and let me review it before anything is sent.”

Follow up with mailbox, folder, sender, subject, timeframe, unread, or limit filters;
request preview; choose format or destination; inspect exclusions/warnings; or rerun.

## Questions

Expect only missing scope questions. Attachment download destinations and every
message/calendar mutation are confirmed separately.

## Reads and writes

List/read operations access local Outlook data. Draft, send, reply, delete, download,
and calendar changes are distinct actions; a live write is never a readiness test.

## Readiness

Check Windows support, server discovery/startup, Outlook/PowerShell/COM, mailbox
permission, then `mailbox_list` and a small `message_list` before any draft or write.

## Demonstration

The current safe live demonstration is a permitted read-only mailbox/message list.
It requires no API key and never sends, replies, deletes, or changes a calendar.

## Artifacts

Direct results appear in chat. Drafts and changes appear in Outlook; downloaded
attachments use the confirmed destination. Keep message bodies out of diagnostics.

## Troubleshooting

Report Outlook closed/offline separately from no matches. Re-list before using stale
message positions, and inspect uncertain side effects before any rerun.
