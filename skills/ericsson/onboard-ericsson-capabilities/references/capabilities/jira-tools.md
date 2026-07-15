---
id: jira-tools
display_name: Ericsson Jira Tools
aliases: [Jira issue tools, Jira ticket lookup, Jira comment tool]
goals:
  - Show the unresolved Jira tickets assigned to me.
  - Get the details of a Jira issue.
  - Prepare a Jira comment for my approval.
maturity: available
recommendation_eligible: true
source_flows: []
implementation:
  skills: []
  plugins: [plugins/ericsson-jira]
  mcp_servers: []
  workflows: []
  tools: [jira_my_tickets, jira_get_issue, jira_add_comment]
platforms: [macos, linux, windows]
configuration:
  - {name: JIRA_BASE_URL, kind: static-setting, required: true, guidance: Configure the Jira site URL in protected Tools & Keys.}
  - {name: JIRA_PAT, kind: static-secret, required: true, guidance: Enter the Jira token only in protected Tools & Keys and never in chat.}
reads: [assigned Jira issue summaries, selected Jira issue details and comments]
writes: [Jira comment only after explicit approval]
artifacts: [tool result in conversation, optional user-requested local summary]
demonstrations: [read-only-live]
troubleshooting: [missing configuration, authentication or permission error, issue not found, uncertain comment result]
---

# Ericsson Jira Tools

## What it solves

Lists assigned issues, retrieves one issue, and adds a comment when the user has
reviewed and explicitly approved that write.

## Try saying

- “Show my unresolved Jira tickets.”
- “Get the current details for ABC-123.”
- “Draft a Jira comment and let me preview it before posting.”

Follow up with the supported result limit, ask for a preview, choose a summary format
or destination, request exclusions and warnings, or ask how to rerun safely. Status
or project narrowing would be a local filter over bounded returned results and may
miss matching issues outside that result window.

## Questions

The Co-Worker asks only for missing scope, issue key, or exact approved comment text.

## Reads and writes

Listing and detail lookup are reads. `jira_add_comment` changes Jira and is never
used as a test; show the destination issue and final comment before approval.

## Readiness

Check discovery, protected configuration presence, authentication, then a small
read-only lookup. Do not print the token or infer readiness from its variable name.

## Demonstration

Use a permitted read-only list/get for the current demonstration. Never post a live
comment for demonstration; a synthetic fixture is not yet bundled.

## Artifacts

Tool results appear in the conversation unless the user selects a safe local
destination and format for a summary.

## Troubleshooting

Distinguish missing credentials, denied permissions, invalid issue keys, and network
errors. If a comment outcome is uncertain, inspect before any rerun.
