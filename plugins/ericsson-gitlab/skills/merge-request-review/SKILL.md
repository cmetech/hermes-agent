---
name: merge-request-review
description: Reviews bounded GitLab merge request evidence. Use when a user asks for an active-agent code review of a merge request.
metadata:
  hermes:
    tags: [Ericsson, GitLab, Review]
---
<objective>
Perform one read-only merge request review from bounded, canonical GitLab evidence using the active agent.
</objective>

<quick_start>
Resolve identity with <tool name="gitlab_resolve_project" mode="read">gitlab_resolve_project</tool>, then read the merge request with <tool name="gitlab_read_merge_request" mode="read">gitlab_read_merge_request</tool>. Use <tool name="gitlab_list_repository_tree" mode="read">gitlab_list_repository_tree</tool> and <tool name="gitlab_read_file" mode="read">gitlab_read_file</tool> only for files relevant to the review.
</quick_start>

<workflow>
The active agent evaluates correctness, regression risk, tests, and maintainability from the returned evidence. Preserve warnings and truncation. Distinguish verified findings from questions. A user who wants a branch, commit, comment, or merge request separately requests that action, which remains subject to visible host approval.
</workflow>

<boundaries>
This skill is read-only. Do not invent numerical certainty, invoke a hidden reviewer, or imply write authority.
</boundaries>

<success_criteria>
The review identifies the exact project and merge request, ties each finding to bounded evidence, and clearly reports incomplete or unavailable context.
</success_criteria>
