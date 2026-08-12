---
id: gitlab-tools
display_name: GitLab Tools
aliases: [Ericsson GitLab, repository tools, merge request tools, GitLab CI tools]
goals:
  - Research a GitLab repository or merge request with bounded evidence.
  - Inspect GitLab pipelines and CI configuration without reading variable values.
  - Preview and perform an explicitly approved GitLab branch, commit, or merge request write.
maturity: available
recommendation_eligible: true
source_flows: []
implementation:
  skills: [skills/ericsson/gitlab]
  plugins: [plugins/ericsson-gitlab]
  mcp_servers: []
  workflows: []
  tools:
    - gitlab_resolve_project
    - gitlab_list_repository_tree
    - gitlab_read_file
    - gitlab_read_merge_request
    - gitlab_list_pipelines
    - gitlab_inspect_ci
    - gitlab_create_branch
    - gitlab_commit_changes
    - gitlab_create_merge_request
platforms: [macos, linux, windows]
configuration:
  - name: origin
    kind: static-setting
    required: true
    guidance: Configure the exact HTTPS GitLab origin in the product plugin settings.
  - name: pat
    kind: static-secret
    required: true
    guidance: Store a GitLab personal access token through the product's protected secret surface.
  - name: client_certificate_path
    kind: static-setting
    required: false
    guidance: Optionally configure a bounded regular-file mTLS certificate path together with its key.
  - name: client_key_path
    kind: static-setting
    required: false
    guidance: Optionally configure a bounded regular-file mTLS key path together with its certificate.
reads: [canonical project identity, bounded repository files and trees, merge requests, pipelines, CI structure and variable metadata]
writes: [explicitly previewed and host-approved branches, atomic commits, and merge requests]
artifacts: [canonical GitLab links, bounded evidence, continuation and warning facts, dry-run previews, reconciled write identities]
demonstrations: [read-only-live, approved-live]
troubleshooting: [plugin disabled, missing or invalid configuration, permission denial, truncated evidence, uncertain write]
---

# GitLab Tools

## What it solves

The standalone Ericsson GitLab plugin provides six bounded read tools and three
approval-gated write tools for repository research, merge-request review, CI
investigation, branches, atomic commits, and merge requests.

## Try saying

- “Research this project at its default branch and explain the relevant files.”
- “Review merge request 42 from bounded GitLab evidence.”
- “Inspect this pipeline and CI include structure without variable values.”

Specify the project filter and ref, ask for a read or write preview, choose the
answer format and artifact destination, name exclusions, preserve each warning,
and rerun only safe reads or reconciled writes.

## Questions

Expect the exact project, ref or ticket identity, desired evidence boundary, and
for writes the intended branch/commit/MR facts. Do not provide credentials in chat.

## Reads and writes

Reads return canonical identities with explicit page, item, byte, time, warning,
and truncation facts. Writes require dry-run review plus visible current-invocation
host approval and reconcile ambiguous remote responses before reporting success.

## Readiness

The plugin is disabled by default. Enable `ericsson-gitlab`, configure `origin` and
the protected `pat`, optionally configure both mTLS paths, pass readiness, then
start a fresh conversation. Qualified guidance appears as
`ericsson-gitlab:repository-research`, `ericsson-gitlab:merge-request-review`, and
`ericsson-gitlab:ci-investigation` only while the plugin is enabled.

## Demonstration

Prefer a bounded read-only live demonstration. A live branch, commit, or merge
request is never a demonstration unless the user explicitly chooses the exact
action, reviews its preview, and grants host approval.

## Artifacts

Inspect canonical links, identities, continuations, warnings, dry-run actions, and
reconciled results at the user-selected destination. Never present incomplete
evidence or an uncertain mutation as complete.

## Troubleshooting

Separate disabled-plugin, configuration, certificate, permission, remote-data,
capacity, cancellation, and transient failures. Preserve safe diagnostics, correct
the cause, and avoid blind write reruns.
