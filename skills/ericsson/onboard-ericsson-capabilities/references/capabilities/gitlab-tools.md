---
id: gitlab-tools
display_name: GitLab Tools
aliases: [Ericsson GitLab, repository tools, merge request tools, GitLab CI tools, "<brand> gitlab commands", Ericsson GitLab connector CLI]
goals:
  - Explore nested groups, subgroups, and visible projects recursively.
  - Inspect recent commits, commit details, comments, and discussions.
  - Discover merge requests and inspect their commits and discussions.
  - Research a GitLab repository or merge request with bounded evidence.
  - Inspect GitLab pipelines, job logs, and CI configuration without reading variable values.
  - Reply to and resolve merge request discussions, inspect approvals, approve, and SHA-pin a merge.
  - Preview and perform explicitly approved GitLab repository, review, and CI recovery writes.
  - Run deterministic `<brand> gitlab ...` shell commands without replacing natural-language GitLab conversations.
maturity: available
recommendation_eligible: true
source_flows: []
implementation:
  skills: [skills/ericsson/gitlab]
  plugins: [plugins/ericsson-gitlab, plugins/ericsson-connector-cli]
  mcp_servers: []
  workflows: []
  tools:
    - gitlab_resolve_project
    - gitlab_list_group_projects
    - gitlab_list_repository_tree
    - gitlab_read_file
    - gitlab_read_merge_request
    - gitlab_list_commits
    - gitlab_read_commit
    - gitlab_list_commit_comments
    - gitlab_list_commit_discussions
    - gitlab_list_merge_requests
    - gitlab_list_merge_request_commits
    - gitlab_list_merge_request_discussions
    - gitlab_list_pipelines
    - gitlab_read_pipeline
    - gitlab_inspect_ci
    - gitlab_job_log
    - gitlab_retry_job
    - gitlab_play_job
    - gitlab_retry_pipeline
    - gitlab_create_branch
    - gitlab_create_named_branch
    - gitlab_commit_changes
    - gitlab_create_merge_request
    - gitlab_create_mr_note
    - gitlab_reply_to_discussion
    - gitlab_resolve_discussion
    - gitlab_merge_request_approvals
    - gitlab_approve_merge_request
    - gitlab_merge_merge_request
    - gitlab_update_merge_request
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
reads: [canonical group and project identity, recursive subgroup and project discovery, bounded repository files and trees, commit history and feedback, merge request discovery, diffs, discussions, and approval state, pipelines and tail-biased job logs, CI structure and variable metadata without values]
writes: [explicitly previewed and host-approved branches, atomic commits, and merge request creation, merge request notes, discussion replies and resolution changes, merge request approval, SHA-pinned merge, and metadata updates, CI job retry and play, pipeline retry]
artifacts: [canonical GitLab links and identities, bounded evidence, continuation, content-warning, and truncation facts, dry-run previews, proven or reconciled write identities, explicit write_ambiguous outcomes]
demonstrations: [read-only-live, approved-live]
troubleshooting: [plugin disabled, missing or invalid configuration, permission denial, conflict or changed SHA, truncated or untrusted evidence, write_ambiguous outcome with no blind retry, unsupported cancel or rebase request]
---

# GitLab Tools

## What it solves

The standalone Ericsson GitLab plugin provides 30 tools: 18 bounded reads and
12 approval-gated writes. It covers recursive group discovery, repository and
commit research, actionable merge-request review, and CI diagnosis and recovery.

## Direct shell commands

Natural-language GitLab requests remain available for research, review, and
cross-system workflows. The always-loaded facade makes `<brand> gitlab --help`
visible before enablement and exposes deterministic leaves such as
`<brand> gitlab pipeline view group/project 918`. The facade has no GitLab
configuration and is not a connector to enable; execution still requires
`ericsson-gitlab` enabled and configured in the active profile. Every direct
write requires exactly one of `--dry-run` and `--confirm`, and origins,
credentials, certificate paths, and profile selection stay off argv.

The read surface resolves projects; lists group projects, repository trees,
commits, commit comments and discussions, merge requests, merge-request commits
and discussions, and pipelines; reads files, commits, merge requests, exact
pipeline metadata, CI job-log tails, and merge-request approval state; and
inspects CI structure and variable metadata without returning variable values.

The write surface creates ticket-derived or explicitly named branches, atomic
commits, and merge requests; posts merge-request notes; replies to and resolves
or reopens discussions; approves, SHA-pins and merges, or updates merge requests;
and retries jobs, starts manual jobs, or retries failed and canceled pipeline
jobs. Pipeline/job cancellation and merge-request rebasing remain deliberately
unavailable.

## Try saying

- “Research this project at its default branch and explain the relevant files.”
- “Show me every subgroup and project under sd-macs-att-rnam-hosting.”
- “What was the latest commit in this repo, and are there comments on it?”
- “What merge requests were created in the last 24 hours?”
- “Send me a daily digest of new commits and merge requests for this project.”
- “Review merge request 42 from bounded GitLab evidence.”
- “Read every discussion on merge request 42, draft replies, and resolve only the threads I approve.”
- “Show approval state, then approve and merge only the exact SHA I reviewed.”
- “Inspect this pipeline and CI include structure without variable values.”
- “Read the tail of failed job 991, explain the failure, and preview a retry.”

Specify the project filter and ref, ask for a read or write preview, choose the
answer format and artifact destination, name exclusions, preserve each warning,
and rerun safe reads when needed. Never rerun an ambiguous write.

## Questions

Expect the exact project, ref or ticket identity and desired evidence boundary.
For review actions, expect the merge-request IID, discussion ID, reviewed head
SHA, message, resolution choice, and merge options. For CI actions, expect the
job or pipeline ID. For every write, confirm the exact dry-run preview and do
not provide credentials in chat.

## Reads and writes

Reads return canonical identities with explicit page, item, byte, time, warning,
untrusted-content, and truncation facts. Job logs are tail-biased and byte-capped;
CI variable values are never returned.

All 12 writes accept `dry_run` and require visible host approval whose scope is
derived from the current call's exact arguments. Start with `dry_run=true` and
have the user authorize that exact preview. The nine review-loop and CI writes
then require a new call with `confirm=true`; they refuse a call that supplies
neither intent. The original branch, atomic-commit, and merge-request creation
tools have no `confirm` argument: after their preview, execution uses
`dry_run=false` and remains host-approved. Discussion replies and resolution
are separate decisions.
Inspect approval state before approving, and pass the exact reviewed SHA when
approving and merging so GitLab refuses a moved branch.

Some older create operations can return a narrowly proven reconciled identity.
The review-loop and CI mutations are single-attempt operations. If any write
returns `write_ambiguous`, stop and report that its outcome is unknown. Never
blindly retry or silently reinterpret it as success; a safe read can describe
current state but cannot prove which actor produced it.

## Readiness

The plugin is disabled by default. Enable `ericsson-gitlab`, configure `origin` and
the protected `pat`, optionally configure both mTLS paths, pass readiness, then
start a fresh conversation. Qualified guidance appears as
`ericsson-gitlab:repository-research`, `ericsson-gitlab:merge-request-review`, and
`ericsson-gitlab:ci-investigation`, and
`ericsson-gitlab:gitlab-activity-digest` only while the plugin is enabled.

## Demonstration

Prefer a bounded read-only live demonstration such as project resolution,
merge-request discovery, approval-state inspection, or a capped job-log tail.
A live repository, review, merge, job, or pipeline mutation is never a
demonstration unless the user chooses the exact action, reviews its dry-run,
and grants argument-scoped host approval.

## Artifacts

Inspect canonical links, identities, reviewed SHAs, discussion and approval
state, continuations, content warnings, truncation, dry-run actions, and proven
or narrowly reconciled results at the user-selected destination. Preserve
`write_ambiguous` as an unknown outcome. Never present incomplete evidence or
an uncertain mutation as complete.

## Troubleshooting

Separate disabled-plugin, configuration, certificate, permission, conflict,
remote-data, capacity, cancellation, transient, and `write_ambiguous` failures.
A changed SHA requires a fresh read, review, preview, and decision. An ambiguous
write is never blindly rerun. Explain that pipeline/job cancellation and
merge-request rebasing are excluded rather than suggesting an alternate
transport.
