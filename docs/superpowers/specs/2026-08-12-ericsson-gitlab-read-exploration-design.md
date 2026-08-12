# Ericsson GitLab Read Exploration and Activity Digests

**Status:** Approved

**Date:** 2026-08-12

**Target repositories:**

- `ericsson-capabilities` — authoritative connector implementation, plugin
  skills, connector documentation, and connector tests
- `hermes-agent` — exact vendored connector distribution plus generic
  cross-surface Kanban, cron, ACP, and packaging verification

## Purpose

Extend the Ericsson GitLab connector so a user can begin with a known group,
discover its visible subgroup and project hierarchy recursively, inspect recent
commits and their review feedback, and explore merge requests and discussions.
The same read-only capability must work from ordinary natural-language chat,
Kanban workers, and recurring cron jobs such as daily commit or merge-request
digests.

The implementation stays in the standalone `ericsson-gitlab` plugin. It does
not add GitLab-specific tools to Hermes core, mutate a conversation's toolset,
invoke a hidden model, use `glab`, clone repositories, or introduce a second
transport implementation for Kanban or cron.

## Approved scope

The first release is read-only. It includes:

1. recursive discovery below a known GitLab group path;
2. recent commit listing and single-commit inspection;
3. commit comments and threaded commit discussions;
4. merge-request discovery, detail, commits, diffs, and discussions;
5. natural-language skill routing for interactive, Kanban, and cron use;
6. rolling activity windows suitable for daily digests; and
7. bounded, redacted, profile-isolated operation on every surface.

Posting comments, replying to discussions, resolving threads, approving or
merging merge requests, and creating or modifying repositories remain out of
scope. Existing approval-gated write tools keep their current behavior.

## Design choice

Use focused read tools backed by shared connector operations and targeted
skills.

This was selected over one large `gitlab_explore` action tool and over adding
many modes to existing tools. Focused schemas give the model a clearer choice,
keep each response independently bounded, make remote-data validation easier,
and permit narrow retries after partial failure. Because plugin tools are
deferred, these additions do not become permanent Hermes core model-tool
surface.

## API basis

The implementation uses GitLab REST API v4:

- `GET /groups/:id` for canonical group identity;
- `GET /groups/:id/descendant_groups` for the visible hierarchy;
- `GET /groups/:id/projects?include_subgroups=true` for visible projects;
- `GET /projects/:id/repository/commits` for commit history;
- `GET /projects/:id/repository/commits/:sha` for commit detail;
- `GET /projects/:id/repository/commits/:sha/comments` for commit comments;
- `GET /projects/:id/repository/commits/:sha/discussions` for commit threads;
- `GET /projects/:id/merge_requests` for merge-request discovery;
- `GET /projects/:id/merge_requests/:iid/commits` for MR commits; and
- `GET /projects/:id/merge_requests/:iid/discussions` for MR notes and threads.

The existing detailed merge-request reader remains the bounded metadata and
diff operation. If its legacy GitLab diff endpoint is deprecated by the target
API version, its transport may be migrated to the supported paginated diff
endpoint without changing the public tool contract.

## Tool contracts

### `gitlab_list_group_projects`

Inputs:

- `group`: numeric ID, URL-encoded path source, full path, or same-origin URL;
- `recursive`: boolean, default `true`;
- `include_shared`: boolean, default `false`;
- `include_archived`: boolean, default `false`;
- optional bounded `search` text;
- bounded `max_groups` and `max_projects`; and
- explicit continuation values returned by an earlier invocation.

The operation resolves the root group, enumerates visible descendants, and
lists visible projects. Its normalized result contains canonical root-group
identity, subgroup entries with `id`, `name`, `full_path`, `parent_id`, and
canonical URL, and project entries with `id`, `name`,
`path_with_namespace`, owning namespace, default branch, archived state, last
activity timestamp, and canonical URL. Projects are grouped or readily
groupable by their owning namespace so the caller can present the hierarchy
instead of only a flat list.

Empty visible subgroups remain visible. Shared projects are excluded unless
explicitly requested and are identified as shared when returned. A result must
not claim complete hierarchy coverage when either collection is truncated.

### `gitlab_list_commits`

Inputs:

- `project`;
- optional `ref`, defaulting through canonical project resolution;
- optional repository `path` filter;
- optional RFC 3339 `since` and `until`;
- optional bounded `lookback_hours`; and
- bounded `max_items` plus continuation.

`lookback_hours` is evaluated at invocation time using an injectable clock and
translated into an absolute API window. It is mutually exclusive with an
explicit `since` value. This keeps prompts such as “the last 24 hours” reliable
inside fresh cron sessions without requiring model-side timestamp arithmetic.

Each normalized commit contains full and short SHA, title, complete message,
author and committer display names, authored and committed timestamps, parent
SHAs, and canonical URL. Email addresses are deliberately omitted.

### `gitlab_read_commit`

Reads one commit by full SHA, abbreviated SHA accepted by GitLab, branch, or
tag. It returns the normalized commit identity plus bounded statistics when
present. It does not automatically fetch potentially large comments,
discussions, or diffs.

### `gitlab_list_commit_comments`

Returns bounded ordinary commit comments with comment body, display-safe
author identity, timestamp, optional file path and line metadata, truncation,
and continuation. Author email and avatar data are omitted.

### `gitlab_list_commit_discussions`

Returns bounded commit discussions and their bounded notes. The result
preserves discussion ID, individual-versus-threaded status when supplied,
note body, display-safe author identity, timestamps, system-note status,
resolution fields, and bounded diff-position metadata. It never implies that
truncated notes represent a complete discussion.

### `gitlab_list_merge_requests`

Inputs include project, state, source/target branches, search text, ordering,
bounded result size, explicit `created_after` or `updated_after`, optional
`until`, and optional `lookback_hours`.

“New merge requests” maps to creation time. “Recently active” or “updated
merge requests” maps to update time. The skill must preserve this distinction
instead of silently substituting one for the other.

Normalized summaries include project identity, IID, title, state, draft flag,
source and target branches, display-safe author, created/updated/merged/closed
timestamps, labels, user-note count, discussion-resolution summary when
available, and canonical URL. Expensive merge-status rechecks are not enabled
by default.

### `gitlab_list_merge_request_commits`

Returns bounded normalized commit summaries for one MR, with truncation and
continuation.

### `gitlab_list_merge_request_discussions`

Returns bounded individual notes and threaded/diff discussions for one MR.
It preserves note type, body, author, timestamps, system-note status,
resolvable/resolved state, resolver identity, and bounded position/suggestion
metadata. It is read-only even when the authenticated user could resolve a
thread.

## Collection and response invariants

All collection tools reuse the connector's existing pagination machinery and
return explicit `truncated`, `warnings`, and continuation data. Multiple remote
collections, such as descendant groups plus projects, identify continuation
by source rather than pretending one page token advances both collections.

Limits are enforced both before and after normalization. Remote list members,
identifiers, timestamps, booleans, nested objects, and canonical URLs are
type-checked. Invalid or contradictory response shapes fail as
`invalid_remote_data`; they are not partially trusted.

The connector preserves the existing safe error taxonomy for invalid input,
configuration, authentication, permission, not-found, capacity, timeout,
transient transport, and invalid remote data. Raw HTML, redirect bodies,
tokens, certificate material, response headers containing credentials, and
unbounded provider error bodies never reach the model.

## Natural-language skills

### `repository-research`

Expand its discovery description and instructions to cover:

- known-group recursive exploration;
- unknown subgroup and project discovery;
- recent commit history;
- single-commit detail, comments, and discussions; and
- the distinction between bounded evidence and complete coverage.

It must explicitly state that pipelines are not a proxy for commit history.

### `merge-request-review`

Expand it to cover MR discovery before the user knows an IID, recent/new MR
filters, MR commit history, general notes, diff discussions, and unresolved
review feedback. Existing code-review behavior remains read-only.

### `gitlab-activity-digest`

Add a connector-owned skill whose discovery description mentions natural
requests for daily or recurring GitLab commit and merge-request summaries.
The skill supports both immediate reporting and interactive cron setup.

For interactive scheduling requests, it instructs the active agent to create a
cron job with:

- a self-contained canonical project path or ID;
- an explicit rolling time window and whether it means created or updated;
- the qualified `ericsson-gitlab:gitlab-activity-digest` skill;
- access to the `ericsson-gitlab` toolset; and
- delivery to the current conversation unless the user requests otherwise.

During a scheduled execution, the skill never creates or modifies cron jobs.
It calls the read tools, produces a bounded digest, and returns exactly
`[SILENT]` when no matching activity exists.

Example supported requests include:

- “Show every project under `sd-macs-att-rnam-hosting`, including subgroups.”
- “What was the last commit in eventmesh and what review discussion is on it?”
- “What merge requests were created in the last 24 hours?”
- “Every weekday at 9 AM, summarize new MRs from the previous 24 hours.”
- “Send me a daily digest of commits to eventmesh main.”

## Kanban behavior

Kanban does not receive a new GitLab client. A worker runs under its assigned
profile and receives that profile's effective CLI toolsets. When the
`ericsson-gitlab` plugin is enabled and ready, the same deferred tools are
available. A task may explicitly pin a qualified connector skill, and an
ordinary natural-language task can discover the skill through the plugin skill
router.

Kanban tests must prove both the plugin toolset pin and qualified skill
propagation. They must not depend on a live worker process or mutate a real
board.

## Cron behavior

Cron continues to use its existing profile-isolated scheduler. Connector jobs
live and execute under the same profile `HERMES_HOME`, so the plugin setting,
PAT, client key, client certificate, CA bundle, and non-secret origin resolve
from that profile at fire time.

Cron tests must prove that a stored digest job loads the qualified activity
skill, exposes the connector toolset to the future `AIAgent`, and retains a
self-contained rolling-window prompt. The job must surface a safe connector
failure when Cloudflare enrollment, mTLS material, PAT access, or the remote
service is unavailable. It must not reveal credential values or recursively
schedule another job.

## Source and vendoring boundaries

Implementation is source-first in a clean linked worktree of
`ericsson-capabilities`. The existing source checkout contains unrelated user
changes and must not be cleaned, reset, or reused for production edits.

After source tests pass and the source commit is clean, Hermes vendors the
exact source revision using an explicit `ERICSSON_CAPABILITIES_DIR`. The
vendored manifest revision and copied plugin bytes must match that commit.
Hermes-specific changes are limited to generic distribution, cross-surface,
and parity tests unless a confirmed generic integration defect is reproduced.

Literal `main` remains synchronization-only. Hermes development and final
checkout state use `base`.

## Test strategy

Implementation follows red-green-refactor vertical slices:

1. group identity and recursive hierarchy, including nested and empty groups,
   shared/archived controls, permissions, pagination, and malformed data;
2. commit list/detail, rolling and absolute windows, branch/path filters,
   comments, discussions, pagination, and redaction;
3. MR discovery, creation/update window semantics, commits, discussions,
   unresolved state, pagination, and redaction;
4. plugin registration and natural-language skill/tool-reference contracts;
5. cron storage, skill injection, future-session toolset/profile projection,
   empty-result silence, and safe authentication failure;
6. Kanban qualified-skill and plugin-toolset propagation;
7. exact source-to-vendor parity, distribution, ACP/deferred-tool, and
   installed-package regression tests; and
8. live read-only UAT against project `56284` and the
   `sd-macs-att-rnam-hosting` group.

Tests assert behavioral relationships rather than current enumeration counts.
No live test performs a write. Live UAT records canonical identities, time
windows, warnings, truncation, and safe failures without recording secrets.

## Acceptance criteria

The enhancement is complete when:

1. a user can provide `sd-macs-att-rnam-hosting` and receive a bounded visible
   hierarchy containing subgroups and their projects;
2. natural requests for recent commits and commit feedback invoke the correct
   read tools without substituting pipeline data;
3. a user can discover recent MRs and inspect their commits and review threads
   without already knowing the IID;
4. an interactive natural-language request can create a recurring daily GitLab
   digest whose future run loads the connector skill and tools;
5. a Kanban worker can execute the same natural request under its assigned
   profile;
6. unavailable Cloudflare/PAT/mTLS access fails safely and diagnostically;
7. all boundedness, continuation, same-origin, redaction, and read-only
   invariants pass deterministic tests;
8. source and vendored connector trees are byte-identical at the recorded
   source revision; and
9. the Hermes checkout ends on `base` with unrelated user files untouched.
