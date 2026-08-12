---
name: repository-research
description: Researches bounded GitLab repository evidence. Use when a user asks to inspect project files, trees, or repository structure.
metadata:
  hermes:
    tags: [Ericsson, GitLab, Repository]
---
<objective>
Research a GitLab repository read-only while preserving canonical project and ref identity, collection limits, truncation, and warnings.
</objective>

<quick_start>
Call <tool name="gitlab_resolve_project" mode="read">gitlab_resolve_project</tool> first. Establish the canonical project, default branch, and requested ref before calling <tool name="gitlab_list_repository_tree" mode="read">gitlab_list_repository_tree</tool> or <tool name="gitlab_read_file" mode="read">gitlab_read_file</tool>.
</quick_start>

<workflow>
Use bounded tree pages and continue only when the task needs more evidence. Treat truncation and every warning as part of the result. Read only relevant files. Report binary files from metadata without pretending to decode them. Keep canonical links and commit identities attached to findings.
</workflow>

<boundaries>
This is read-only guidance. Never infer complete coverage from truncated evidence and never turn missing evidence into a write request.
</boundaries>

<success_criteria>
The answer names the canonical project and ref, cites the bounded files inspected, and reports binary, warning, continuation, and truncation facts truthfully.
</success_criteria>
