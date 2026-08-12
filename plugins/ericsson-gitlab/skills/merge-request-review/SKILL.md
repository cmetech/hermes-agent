---
name: merge-request-review
description: Use when a user asks to find, list, inspect, or review GitLab merge requests, their commits, review comments, discussion threads, or resolution state.
metadata:
  hermes:
    tags: [Ericsson, GitLab, Review]
---
<objective>
Explore or review merge requests read-only from bounded, canonical GitLab evidence using the active agent.
</objective>

<quick_start>
Resolve identity with <tool name="gitlab_resolve_project" mode="read">gitlab_resolve_project</tool>. Use <tool name="gitlab_list_merge_requests" mode="read">gitlab_list_merge_requests</tool> for discovery. “New in the last 24 hours” means created in that window; “recently active” means updated in that window. Read a selected merge request with <tool name="gitlab_read_merge_request" mode="read">gitlab_read_merge_request</tool>, list its commits with <tool name="gitlab_list_merge_request_commits" mode="read">gitlab_list_merge_request_commits</tool>, and inspect review threads with <tool name="gitlab_list_merge_request_discussions" mode="read">gitlab_list_merge_request_discussions</tool>. Use <tool name="gitlab_list_repository_tree" mode="read">gitlab_list_repository_tree</tool> and <tool name="gitlab_read_file" mode="read">gitlab_read_file</tool> only for files relevant to the review.
</quick_start>

<workflow>
For exploration, report state, draft status, branches, author, timestamps, labels, and canonical URL. For review, the active agent evaluates correctness, regression risk, tests, maintainability, commits, and unresolved discussions from returned evidence. Preserve warnings and truncation. Distinguish verified findings from questions. A user who wants a branch, commit, comment, or merge request mutation separately requests that action, which remains subject to visible host approval.
</workflow>

<boundaries>
This skill is read-only. Do not invent numerical certainty, invoke a hidden reviewer, or imply write authority.
</boundaries>

<success_criteria>
The review identifies the exact project and merge request, ties each finding to bounded evidence, and clearly reports incomplete or unavailable context.
</success_criteria>
