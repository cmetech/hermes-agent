---
name: jira
description: Routes Ericsson Jira setup, readiness, ticket research, triage, search, and approved comments. Use when a user asks about Jira capabilities or needs help enabling the standalone connector.
metadata:
  hermes:
    tags: [Ericsson, Jira]
---

<objective>
Explain the standalone Jira connector, establish readiness safely, and route enabled-plugin work to its qualified guidance.
</objective>

<setup>
The bundled `ericsson-jira` connector is disabled for every profile until the user explicitly enables it. Use `hermes tools` or the Tools settings UI to enable it and configure its ordinary settings and protected write-only secret fields. Existing settings or credentials never enable it automatically. Start a fresh conversation after changing plugin enablement so the tool and qualified-skill surface is stable.
</setup>

<readiness>
Check, in order: connector enabled, configuration present, authentication accepted, permissions adequate, then a small read-only <tool name="jira_my_tickets" mode="read">jira_my_tickets</tool> or exact <tool name="jira_get_issue" mode="read">jira_get_issue</tool> probe. Installation and secret presence alone are not readiness proof. Never use a comment as a configuration test.
</readiness>

<routing>
When enabled, use `ericsson-jira:ticket-research` for bounded issue/search evidence and fix summaries. Use `ericsson-jira:defect-triage` for one-ticket categorization and separately approved comment guidance. The plugin also provides <tool name="jira_search_issues" mode="read">jira_search_issues</tool> for explicit bounded JQL and <tool name="jira_add_comment" mode="write">jira_add_comment</tool> for a reviewed current-action write.
</routing>

<boundaries>
Do not request credentials in chat, expose protected values, run a write as a readiness check, or claim exact multi-ticket defect-loop parity.
</boundaries>
