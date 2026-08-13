---
name: ticket-research
description: Research one explicit Jira ticket with bounded issue and search evidence. Use when the user wants context, links, or a fix summary before choosing an action.
metadata:
  hermes:
    tags: [Ericsson, Jira, Research]
---

<objective>
Research one explicit ticket at a time, preserve Jira facts and warnings, and produce a bounded fix summary without inventing repository or issue state.
</objective>

<workflow>
Use <tool name="jira_get_issue" mode="read">jira_get_issue</tool> when the key is known. Use <tool name="jira_search_issues" mode="read">jira_search_issues</tool> only with explicit bounded JQL, a result limit, and the smallest safe field set. Use <tool name="jira_my_tickets" mode="read">jira_my_tickets</tool> only when the user asks for their assigned unresolved work. Select one explicit ticket before deeper reasoning.

Summarize the problem, relevant status/priority/type/labels, normalized description and recent comment evidence, detected GitLab links, gaps, and a proposed verification boundary. A fix summary is guidance for the active agent, not authorization to change Jira or source control.
</workflow>

<safety>
Never ask for or display credentials. Preserve truncation, incomplete evidence, permission, and not-found outcomes. Do not turn an empty result after failure into an empty-workload claim. This skill performs reads only.
</safety>

<success_criteria>
The response names the exact issue key, distinguishes facts from inference, cites bounded Jira evidence, lists gaps and warnings, and does not imply that a write occurred.
</success_criteria>
