# Ericsson GitLab Releases and Personal Inbox

**Status:** Approved

**Date:** 2026-08-30

**Target repositories:**

- `ericsson-capabilities` — authoritative connector implementation, skills,
  connector CLI descriptors, migration mapping, documentation, and tests
- `hermes-agent` — exact vendored distribution and Hermes-native routing eval

## Purpose

Complete the approved remaining read-only GitLab surface by adding release
list/detail, user To-Do list, and project-optional merge-request discovery with
authenticated-user filters. This turns requests such as “show releases,” “what
needs my attention,” and “show MRs waiting for my review across GitLab” into
bounded connector reads.

This is the third independent GitLab read-coverage slice. Webhook enumeration
and all remaining mutations are deliberately excluded.

## Approved scope

Add three read operations and extend one existing operation:

| SuperCLI 0.14.1 behavior | Connector contract |
|---|---|
| `super-cli gitlab release list` | `gitlab_list_releases` |
| `super-cli gitlab release view` | `gitlab_read_release` |
| `super-cli gitlab todo list` | `gitlab_list_todos` |
| `super-cli gitlab mr list` without `--project`, including `@me` filters | extend `gitlab_list_merge_requests` |

Do not add a duplicate global-MR tool. The existing list operation selects its
endpoint from whether `project` is present.

`super-cli gitlab release create`, `todo done`, webhook list, MR rebase, job or
pipeline cancel, project mutation, and every other write gap remain excluded.

## Architecture and API basis

The slice uses the existing connector schema, invoke, operations, shared
client, normalization, and application-envelope boundaries. No second client,
generic query executor, core tool, MCP service, `glab` process, or classifier
model is introduced.

The official GitLab REST contracts are:

- `GET /projects/:id/releases` and
  `GET /projects/:id/releases/:tag_name`
  ([Project release API](https://docs.gitlab.com/api/releases/));
- `GET /todos` ([To-Do List API](https://docs.gitlab.com/api/todos/));
- `GET /projects/:id/merge_requests` for project scope; and
- `GET /merge_requests` for authenticated-user/global-visible scope
  ([Merge requests API](https://docs.gitlab.com/api/merge_requests/)).

When an explicit actor filter uses `@me`, the operation resolves the current
authenticated user through GitLab's current-user endpoint. It never asks the
model to guess the username. When the requested intent maps directly to
GitLab's `created_by_me`, `assigned_to_me`, or `reviews_for_me` scope, the
operation uses that native scope without an unnecessary identity lookup.

## Tool contracts

### `gitlab_list_releases`

Required input: `project`.

Optional inputs:

- `order_by`: `released_at` or `created_at`;
- `sort`: `asc` or `desc`;
- bounded `max_items`; and
- structured `continuation`.

Each release summary contains tag name, release name, bounded description
summary, created/released timestamps, upcoming-release state, display-safe
author when present, commit identity when present, same-origin release URL, and bounded
counts for milestones and assets. Full asset detail belongs to
`gitlab_read_release`.

### `gitlab_read_release`

Required inputs:

- `project`;
- `tag`: bounded release tag name.

The normalized result contains canonical project identity plus:

- tag name, release name, bounded redacted description;
- created/released timestamps and upcoming state;
- display-safe author and commit identity when present;
- bounded milestone summaries;
- bounded source archive entries; and
- bounded release links/assets whose returned URLs are on the configured
  GitLab origin.

External asset URLs are not followed or returned. Their omission is reported
as a count/warning so the response does not imply complete asset coverage.
The operation does not download release assets or collect release evidence.

### `gitlab_list_todos`

No input is required.

Optional inputs:

- `project`, resolved to a project ID when supplied;
- `state`, `pending` by default or `done` when explicitly requested;
- allowlisted `action`;
- allowlisted `target_type`;
- bounded `max_items`; and
- `continuation`.

Each To-Do item contains ID, action, state, created/updated timestamps,
display-safe author, canonical project identity when present, canonical group
identity when present, target type, and a bounded target summary containing
only stable identity/title/state and same-origin URL fields that are present.
The target identifier is type-aware: project resources use positive integer
IDs while commit targets may use a validated commit SHA. A missing project on
a group-, namespace-, key-, or commit-scoped To-Do is valid. Caller filter
allowlists follow the supported GitLab API contract; remote `action_name` and
`target_type` values are validated as bounded data so a newer server value
does not invalidate the whole inbox page. Target bodies, arbitrary raw
payloads, and embedded instructions are omitted.

The operation is read-only. Marking a To-Do done remains excluded.

### Extended `gitlab_list_merge_requests`

`project` becomes optional. Existing project-scoped inputs and behavior remain
backward compatible.

New optional inputs:

- `scope`: `all`, `created_by_me`, `assigned_to_me`, or `reviews_for_me`;
- bounded `author`, `assignee`, and `reviewer` username filters, each accepting
  the special value `@me`;
- existing state, branch, search, order, time-window, limit, and continuation
  inputs.

The operation uses:

- `/projects/:id/merge_requests` when `project` is present; and
- `/merge_requests` when it is absent.

Actor filters can be combined only where GitLab supports the combination.
Mutually exclusive or contradictory scope/filter combinations fail as
`invalid_input` before transport. A matching native personal scope plus its
redundant `@me` actor is canonicalized to the native scope alone, without a
current-user lookup or actor query parameter; for example,
`scope=created_by_me, author=@me` sends only `scope=created_by_me` because
GitLab does not document `author_id` with that scope.
`invalid_input` rather than being silently weakened.

Every result includes canonical project identity. Project-scoped results retain
the existing top-level project summary. Cross-project results include project
ID and validated namespace/path on each MR, derived from bounded GitLab identity
fields and same-origin MR URLs without an unbounded per-item fetch loop.

The normalized MR fields and created-versus-updated time-window semantics remain
compatible with the existing operation.

## Collection, normalization, and safety

All collections are bounded and return `count`, `truncated`, and usable
continuation. Limits apply after normalization as well as at the HTTP layer.

Release descriptions, To-Do titles, project names, and MR text are untrusted
remote content. They are bounded, redacted, and never interpreted as skill or
tool instructions. Remote errors and response bodies remain behind the stable
connector error taxonomy.

All returned URLs must be on the configured GitLab origin. External release
links are represented only by safe omission metadata. User emails, avatar URLs,
credentials, certificate paths, and raw provider fields are not returned.

The safe application result remains `{"success": true, "result": ...}` or a
connector-owned error category/message/remediation envelope.

## Natural-language routing

Hermes uses one ordinary tool-calling conversation, not a classifier request
followed by a changed toolset. The stable flow is:

```text
user request
  -> skill_view("gitlab")
  -> skill_view(focused qualified skill)
  -> tool_search if needed
  -> tool_describe(selected schemas)
  -> tool_call(selected reads)
```

Two focused qualified skills are added:

### `release-research`

Owns published release discovery and detail:

| Intent | Operation |
|---|---|
| List published releases | `gitlab_list_releases` |
| Read notes/assets for one release tag | `gitlab_read_release` |
| List raw Git tags | `gitlab_list_tags` from `repository-research` |

### `personal-inbox`

Owns authenticated-user work queues:

| Intent | Operation |
|---|---|
| GitLab notifications, attention queue, or To-Dos | `gitlab_list_todos` |
| MRs authored by me across GitLab | global `gitlab_list_merge_requests` |
| MRs assigned to me across GitLab | global `gitlab_list_merge_requests` |
| MRs requesting my review across GitLab | global `gitlab_list_merge_requests` |

`merge-request-review` continues to own one selected MR, project-specific MR
discovery, commits, diffs, discussions, approval state, and review reasoning.
The precedence rule is:

- “my queue/inbox/across GitLab” -> `personal-inbox`;
- “MRs in project X” or “review MR 42” -> `merge-request-review`.

The always-indexed `gitlab` router gains release and personal-inbox routes but
does not duplicate their tool instructions.

## Routing research and evaluation

The skill split follows patterns confirmed in
[GitLab's official AI skills](https://gitlab.com/gitlab-org/ai/skills) and the
community
[`gitlab-cli-skills`](https://github.com/vince-winkintel/gitlab-cli-skills)
suite's separate release and To-Do skills.
[Anthropic](https://github.com/anthropics/skills/tree/main/skills/skill-creator)
and
[OpenAI](https://github.com/openai/skills/tree/main/skills/.system/skill-creator)
authoring guidance informs concise trigger descriptions, progressive
disclosure, and realistic forward tests. `glab` commands are reference
vocabulary only; execution remains through connector tools.

The shared routing corpus adds clear, paraphrased, and ambiguous prompts with
ordered safe sequences, required intents, and explicit clarification policy
for:

- release versus tag;
- To-Do versus MR review queue;
- project-specific versus cross-project MR listing;
- authored/assigned/reviewer `@me` intent;
- one selected MR versus an inbox of MRs; and
- multi-intent requests combining To-Dos and review requests.

Static tests prove every new tool has one primary skill owner, both qualified
skills are registered, the thin router names them, and no read case permits a
write. The stubbed Hermes live eval records the actual progressive-disclosure
trace on configured Claude and OpenAI/Codex models. Ambiguous cases repeat
three times and must complete every required intent through an allowed ordered
read path or ask a genuine clarification every time;
any write selection is a hard failure.

## Connector CLI and migration documentation

The connector CLI exposes:

```text
<brand> gitlab release list <project>
<brand> gitlab release show <project> <tag>
<brand> gitlab todo list [--project <project>]
<brand> gitlab mr list [project] [actor/filter options]
```

The existing optional `project` positional support in descriptor metadata is
completed with the smallest parser change: a non-required positional uses
argparse `nargs="?"`. Existing project-positional invocations remain valid, and
the no-project form becomes available without inventing a second MR-list
command.

The SuperCLI mapping YAML changes release list/view and To-Do list to reviewed
read dispositions and upgrades the MR-list row from the current
project-required limitation to project-optional parity. Generated migration
Markdown is rebuilt; it is not hand-edited.

The GitLab onboarding capability page documents release/inbox examples and
keeps webhook list and To-Do mutation explicitly excluded. The onboarding
catalog is regenerated and validated. No test freezes the total number of
tools, commands, reads, or writes.

## Source and vendoring boundary

Implementation and source tests complete in a clean `ericsson-capabilities`
worktree first. A clean source commit is then vendored by full SHA into Hermes
`base`, where managed inventory, copied bytes, plugin skill loading, deferred
tool access, and live routing eval are verified.

No shared edit starts in the Hermes vendor copy. Literal `main`, brand
regeneration, and release publication remain out of scope.

## Test strategy

Red-green-refactor slices cover:

1. release listing, ordering, pagination, and bounded summaries;
2. release detail, same-origin assets, external-link omission, and malformed
   release data;
3. To-Do filters, generic target normalization, project resolution, and
   pagination;
4. project-optional MR endpoint selection and backward compatibility;
5. native scopes, explicit actor filters, `@me` resolution, contradictory
   combinations, and cross-project identity;
6. registration of `release-research` and `personal-inbox`, router updates,
   and natural-language corpus cases;
7. optional positional CLI parsing and generated migration/onboarding docs;
   and
8. exact source-to-vendor and deferred-tool verification.

Tests include permission-limited To-Dos, missing releases, foreign asset URLs,
malformed targets, repeated project IDs, bad actor identity, pagination
ceilings, cancellation/deadline propagation, and remote text containing
instruction-injection or secret-shaped content.

## Acceptance criteria

This slice is complete when:

1. users can list releases and inspect one release without downloading assets;
2. external release URLs are not followed or returned as trusted links;
3. users can list a bounded authenticated To-Do inbox without mutating it;
4. `gitlab_list_merge_requests` works with and without a project and supports
   native “created/assigned/reviews for me” intent plus explicit `@me` filters;
5. every cross-project MR retains canonical project identity;
6. natural-language requests reliably select release, inbox, or selected-MR
   skills and tools across the approved live eval;
7. connector CLI and generated SuperCLI documentation reflect the new reads;
8. webhook and remaining writes stay explicitly excluded; and
9. deterministic source, vendor, security, pagination, and error gates pass.

## Explicitly out of scope

- release creation, update, deletion, evidence collection, or asset download;
- To-Do completion or creation;
- webhook enumeration or mutation;
- merge-request rebase or other new MR writes;
- returning external asset URLs;
- user email/avatar/raw identity projection;
- a separate classifier model; and
- dynamic category-specific toolset replacement.
