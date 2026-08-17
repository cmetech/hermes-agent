---
id: jira-tools
display_name: Ericsson Jira Tools
aliases: [Jira issue tools, Jira ticket lookup, Jira issue management]
goals:
  - Discover bounded Jira issues, fields, projects, transitions, assignable users, and link types.
  - Read assigned work, explicit searches, issue details, and recent comments.
  - Preview and perform an explicitly approved Jira comment, transition, assignment, field update, label change, issue creation, or issue link.
maturity: available
recommendation_eligible: true
source_flows: [docs/flows/jira-single-ticket-showcase.md]
implementation:
  skills: [skills/ericsson/jira]
  plugins: [plugins/ericsson-jira]
  mcp_servers: []
  workflows: [workflows/jira-single-ticket-showcase.yml]
  tools:
    - jira_my_tickets
    - jira_search_issues
    - jira_get_issue
    - jira_add_comment
    - jira_list_fields
    - jira_get_project
    - jira_list_transitions
    - jira_search_assignable_users
    - jira_transition_issue
    - jira_assign_issue
    - jira_update_fields
    - jira_manage_labels
    - jira_create_issue
    - jira_list_link_types
    - jira_link_issues
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
reads: [assigned Jira issue summaries, bounded explicit search results, selected Jira issue details and recent comments, field and project metadata, available transitions, assignable users, configured issue link types]
writes: [approved Jira comment, approved workflow transition, approved assignment or unassignment, approved field update, approved label change, approved issue creation, approved issue link]
artifacts: [bounded tool result in conversation, dry-run write preview, reconciled remote identity or explicit ambiguity warning, optional user-requested local summary]
demonstrations: [read-only-live, approved-live]
troubleshooting: [plugin disabled, missing or invalid configuration, authentication or permission denial, invalid or unavailable Jira identity, truncated filtered evidence, uncertain write result]
---

# Ericsson Jira Tools

## What it solves

Provides all 15 standalone Jira tools: three core issue reads, five discovery
reads, and seven approval-gated writes. It can list assigned work, search with an
explicit bound, read one issue and recent comments, discover fields, project
metadata, transitions, assignable users, and link types, then preview and perform
one selected mutation.

## Try saying

- “Show my unresolved Jira tickets.”
- “Search project ABC for open bugs, maximum 20.”
- “Get the current details for ABC-123.”
- “List the fields and valid transitions I can use for ABC-123.”
- “Find an assignable user in project ABC.”
- “Draft a Jira comment and let me preview it before posting.”
- “Preview creating a Bug in ABC; do not submit it yet.”
- “Show the issue-link direction, then preview linking ABC-123 to ABC-456.”

Follow up with the supported result limit, exact discovery filter, a dry-run
preview, output format or destination, exclusions, preserved warnings, or safe
rerun guidance. Local filters over bounded search results can miss remote matches;
when the whole remote result set was not scanned, treat the filtered total as
unknown rather than complete.

## Questions

The Co-Worker asks only for missing scope and prerequisites: exact project or
issue keys, bounded JQL, the field/transition/user/link identity returned by a
discovery read, intended values, and whether the user wants preview or execution.
Never guess workflow IDs, custom-field IDs, issue types, assignees, or link
direction.

## Reads and writes

`jira_my_tickets`, `jira_search_issues`, `jira_get_issue`, `jira_list_fields`,
`jira_get_project`, `jira_list_transitions`, `jira_search_assignable_users`, and
`jira_list_link_types` are reads. The seven write tools are `jira_add_comment`,
`jira_transition_issue`, `jira_assign_issue`, `jira_update_fields`,
`jira_manage_labels`, `jira_create_issue`, and `jira_link_issues`. Each write is
argument-scoped and host-approval-gated. Show the exact destination and dry-run
action first; never use a mutation as a connectivity test and never blindly rerun
an ambiguous write.

## Readiness

The plugin is disabled by default. Enable it through Tools, configure the selected
authentication fields, start a fresh conversation, then check discovery,
authentication, permission, and a small read-only lookup. Do not print a token or
infer readiness from configured values. Resolve write prerequisites with the
corresponding discovery read. Qualified ticket-research and defect-triage guidance
appears only while the plugin is enabled.

## Demonstration

Prefer a bounded read-only discovery or issue lookup. A live mutation is never a
demonstration unless the user selects the exact action, reviews its preview, and
grants current host approval. A synthetic fixture is not yet bundled for this
general tool surface.

## Artifacts

Inspect bounded results, truncation and warning facts, dry-run actions, and any
reconciled issue/comment/link identity in the conversation unless the user selects
a safe local destination and format for a summary. An ambiguity warning is an
outcome, not evidence of success.

## Troubleshooting

Distinguish disabled-plugin, configuration, authentication, permission, invalid
input, missing discovery prerequisite, remote-data, capacity, deadline, and
transient failures. Preserve filtered-search truncation warnings. For any uncertain
write, inspect the exact target before considering a rerun; if reconciliation fails
or does not match, preserve the original ambiguity.
