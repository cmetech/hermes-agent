---
name: ci-investigation
description: Investigates bounded GitLab CI evidence. Use when a user asks about pipelines, CI configuration, includes, or variable metadata.
metadata:
  hermes:
    tags: [Ericsson, GitLab, CI]
---
<objective>
Investigate GitLab CI read-only with bounded pipeline, configuration, include, and variable metadata evidence.
</objective>

<quick_start>
Resolve the project through <tool name="gitlab_resolve_project" mode="read">gitlab_resolve_project</tool>. Use <tool name="gitlab_list_pipelines" mode="read">gitlab_list_pipelines</tool> for pipeline status, <tool name="gitlab_inspect_ci" mode="read">gitlab_inspect_ci</tool> for bounded CI structure, and <tool name="gitlab_read_file" mode="read">gitlab_read_file</tool> for a specifically relevant repository file.
</quick_start>

<workflow>
Treat warnings, permission records, unsupported remote or template includes, continuation, and truncation as evidence. Variable values are never returned; report metadata only. Do not claim that configuration was evaluated when the tool reports unsupported syntax or incomplete includes.
</workflow>

<boundaries>
This is read-only investigation, not a penetration test or a pipeline mutation. Keep requests bounded and do not fetch unsupported remote or template content by another transport.
</boundaries>

<success_criteria>
The answer identifies project and ref, separates pipeline state from configuration evidence, omits variable values, and reports every warning or unsupported include.
</success_criteria>
