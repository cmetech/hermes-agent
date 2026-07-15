---
id: teams-tools
display_name: Ericsson Teams Tools
aliases: [Microsoft Teams tools, Teams channels, Graph device code]
goals:
  - Show me which Teams and channels I can access.
  - Read recent messages from a selected Teams channel.
  - Draft a Teams channel message for approval.
maturity: available
recommendation_eligible: true
source_flows: []
implementation:
  skills: []
  plugins: [plugins/ericsson-teams]
  mcp_servers: []
  workflows: []
  tools: [teams_auth, teams_list, teams_channels, teams_read, teams_send, teams_reply]
platforms: [macos, linux, windows]
configuration:
  - {name: ERICSSON_GRAPH_CLIENT_ID, kind: static-setting, required: false, guidance: Use the protected Tools & Keys override only when the organization provides a supported public-client ID.}
  - {name: msal, kind: local-software, required: true, guidance: Install the Python dependency only after user approval.}
  - {name: Microsoft device code, kind: interactive-sign-in, required: true, guidance: Start device-code sign-in only after approval and complete it in the Microsoft browser page.}
  - {name: Microsoft Graph permissions, kind: permission, required: true, guidance: Required delegated access must be granted by the appropriate organization process.}
reads: [signed-in user Teams, channels, and selected channel messages]
writes: [Teams channel message or reply only after explicit approval]
artifacts: [tool result in conversation, local token cache outside chat]
demonstrations: [read-only-live]
troubleshooting: [missing msal, device-code timeout, permission denial, no accessible teams, uncertain send result]
---

# Ericsson Teams Tools

## What it solves

Uses Microsoft Graph device-code authentication to list teams/channels, read channel
messages, and send or reply only when explicitly approved.

## Try saying

- “Show me which Teams and channels I can access.”
- “Read the latest messages in this channel.”
- “Draft a Teams reply and show me a preview before sending.”

Follow up with the supported team, channel, and message count parameters; request a
preview; choose a summary format or destination; inspect exclusions or warnings; or
rerun a read. Date narrowing would be a local filter over bounded returned messages
and may miss matching messages outside that result window.

## Questions

Expect one question for the team/channel or message scope. Sign-in and any send are
asked separately at the moment they are needed.

## Reads and writes

List/read operations access Graph data. Send/reply changes Teams and is never a
configuration test. The local MSAL cache stays outside ordinary chat and summaries.

## Readiness

Confirm plugin discovery, Python `msal`, optional client-ID override, device-code
sign-in, delegated permissions, then list teams read-only. Never paste device codes.

## Demonstration

The current demonstration stops after a permitted read-only list/get. A synthetic
fixture is not yet bundled, and no live write is used as a substitute.

## Artifacts

Results are conversational unless a safe summary destination and format are chosen;
authentication cache contents are never shown or copied.

## Troubleshooting

Separate dependency, sign-in timeout, consent, permission, and zero-access cases. An
uncertain send is inspected before any rerun to avoid duplicate messages.
