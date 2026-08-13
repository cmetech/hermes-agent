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
source_flows: [docs/flows/jira-single-ticket-showcase.md]
implementation:
  skills: [skills/ericsson/jira]
  plugins: [plugins/ericsson-jira]
  mcp_servers: []
  workflows: [workflows/jira-single-ticket-showcase.yml]
  tools: [jira_my_tickets, jira_search_issues, jira_get_issue, jira_add_comment]
platforms: [macos, linux, windows]
configuration:
  - {name: base_url, kind: static-setting, required: true, guidance: Configure the exact Jira HTTP(S) origin through Tools.}
  - {name: auth_mode, kind: static-setting, required: true, guidance: Choose bearer PAT or basic email/API-token authentication through Tools.}
  - {name: pat, kind: static-secret, required: true, guidance: Store the bearer personal access token only through the protected write-only field when bearer mode is selected.}
  - {name: email, kind: static-setting, required: true, guidance: Configure the Jira account email when basic mode is selected.}
  - {name: api_token, kind: static-secret, required: true, guidance: Store the basic Jira API token only through the protected write-only field when basic mode is selected.}
  - {name: rest_api_version, kind: static-setting, required: true, guidance: Keep automatic v3-to-classified-v2 compatibility unless the deployment requires an explicit version.}
  - {name: transport, kind: static-setting, required: true, guidance: Prefer native or automatic transport; select curl explicitly only for a proven compatibility deployment.}
  - {name: curl_executable, kind: static-setting, required: false, guidance: Select an approved absolute curl executable only when the compatibility transport is required.}
  - {name: request_timeout_seconds, kind: static-setting, required: false, guidance: Keep the request deadline within the bounded Tools range.}
  - {name: default_max_results, kind: static-setting, required: false, guidance: Keep the finite default result count within the bounded Tools range.}
reads: [assigned Jira issue summaries, selected Jira issue details and comments]
writes: [Jira comment only after explicit approval]
artifacts: [tool result in conversation, optional user-requested local summary]
demonstrations: [read-only-live]
troubleshooting: [missing configuration, authentication or permission error, issue not found, uncertain comment result]
---

# Ericsson Jira Tools

## What it solves

Lists assigned issues, performs bounded explicit search, retrieves one issue, and
adds a reconciled comment when the user has reviewed and explicitly approved that write.

## Try saying

- “Show my unresolved Jira tickets.”
- “Search project ABC for open bugs, maximum 20.”
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

The plugin is disabled by default. Enable it through Tools, configure the selected
authentication fields, start a fresh conversation, then check discovery,
authentication, permission, and a small read-only lookup. Do not print a token or
infer readiness from configured values. Qualified ticket-research and defect-triage
guidance appears only while the plugin is enabled.

## Demonstration

Use a permitted read-only list/get for the current demonstration. Never post a live
comment for demonstration; a synthetic fixture is not yet bundled.

## Artifacts

Tool results appear in the conversation unless the user selects a safe local
destination and format for a summary.

## Troubleshooting

Distinguish missing credentials, denied permissions, invalid issue keys, and network
errors. If a comment outcome is uncertain, inspect before any rerun.
