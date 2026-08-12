---
name: repository-research
description: Use when a user asks to explore GitLab groups, subgroups, projects, repository structure, files, latest commits, commit details, comments, or discussions.
metadata:
  hermes:
    tags: [Ericsson, GitLab, Repository]
---
<objective>
Research GitLab groups and repositories read-only while preserving canonical group, project, ref, and commit identity, collection limits, truncation, and warnings.
</objective>

<quick_start>
For a known group such as `sd-macs-att-rnam-hosting`, call <tool name="gitlab_list_group_projects" mode="read">gitlab_list_group_projects</tool> to discover nested subgroups and projects recursively. For a known project, call <tool name="gitlab_resolve_project" mode="read">gitlab_resolve_project</tool> first. Establish the canonical project, default branch, and requested ref before calling <tool name="gitlab_list_repository_tree" mode="read">gitlab_list_repository_tree</tool>, <tool name="gitlab_read_file" mode="read">gitlab_read_file</tool>, or <tool name="gitlab_list_commits" mode="read">gitlab_list_commits</tool>. Use <tool name="gitlab_read_commit" mode="read">gitlab_read_commit</tool>, <tool name="gitlab_list_commit_comments" mode="read">gitlab_list_commit_comments</tool>, and <tool name="gitlab_list_commit_discussions" mode="read">gitlab_list_commit_discussions</tool> only when the requested commit needs those details.
</quick_start>

<workflow>
Interpret natural requests directly: “What projects are in this group?”, “What is the latest commit?”, “What did that commit change?”, and “Are there comments or discussions on it?” map to the focused reads above. Pipelines are not commit history; never substitute <tool name="gitlab_list_pipelines" mode="context-only">gitlab_list_pipelines</tool> for commit listing. Use bounded pages and continue only when the task needs more evidence. Treat truncation and every warning as part of the result. Read only relevant files. Report binary files from metadata without pretending to decode them. Keep canonical links and commit identities attached to findings.
</workflow>

<boundaries>
This is read-only guidance. Never infer complete coverage from truncated evidence and never turn missing evidence into a write request.
</boundaries>

<success_criteria>
The answer names the canonical group or project and ref, preserves commit identity when relevant, cites bounded evidence, and reports empty subgroups, binary files, warnings, continuation, and truncation truthfully.
</success_criteria>
