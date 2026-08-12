---
name: jira-to-gitlab
description: Guides an approved Jira-to-GitLab delivery flow. Use when a user asks to research a Jira ticket, prepare a GitLab fix, and report its status.
metadata:
  hermes:
    tags: [Ericsson, Jira, GitLab]
---
<objective>
Use the active agent to turn explicit Jira ticket context into bounded GitLab research, a reviewed fix proposal, approval-gated writes, and truthful per-ticket status.
</objective>

<quick_start>
Read assigned tickets with <tool name="jira_my_tickets" mode="read">jira_my_tickets</tool> or an exact ticket key with <tool name="jira_get_issue" mode="read">jira_get_issue</tool>. Treat not_found as final evidence. Resolve the project and default branch with <tool name="gitlab_resolve_project" mode="read">gitlab_resolve_project</tool> before repository reads.
</quick_start>

<tool_contract>
<tool name="gitlab_list_repository_tree" mode="read">gitlab_list_repository_tree</tool>
<tool name="gitlab_read_file" mode="read">gitlab_read_file</tool>
<tool name="gitlab_read_merge_request" mode="read">gitlab_read_merge_request</tool>
<tool name="gitlab_list_pipelines" mode="read">gitlab_list_pipelines</tool>
<tool name="gitlab_inspect_ci" mode="read">gitlab_inspect_ci</tool>
<tool name="gitlab_create_branch" mode="write">gitlab_create_branch</tool>
<tool name="gitlab_commit_changes" mode="write">gitlab_commit_changes</tool>
<tool name="gitlab_create_merge_request" mode="write">gitlab_create_merge_request</tool>
<tool name="jira_add_comment" mode="write">jira_add_comment</tool>
</tool_contract>

<workflow>
The active agent researches bounded evidence, explains the intended changes, and obtains visible host approval. Preview GitLab mutations in dry-run form before requesting approval. Reconcile every approved write, stop on an uncertain outcome, and do not retry an ambiguous mutation. Add a Jira comment only after separate approval and only with exact confirmed facts.
</workflow>

<status>
Return per-ticket status with ticket key, project, branch, commit, merge request, warnings, and attention needed. The legacy multi-ticket loop_group behavior is deferred; process only the explicitly bounded ticket set and never imply hidden aggregation.
</status>

<success_criteria>
Every ticket has truthful status, the default branch and GitLab identities are canonical, every outward action has host approval, and uncertain or not_found outcomes remain visible.
</success_criteria>
