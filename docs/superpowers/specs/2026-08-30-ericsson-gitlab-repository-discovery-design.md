# Ericsson GitLab Repository Discovery

**Status:** Approved

**Date:** 2026-08-30

**Target repositories:**

- `ericsson-capabilities` — authoritative connector implementation, skills,
  connector CLI descriptors, migration mapping, documentation, and tests
- `hermes-agent` — exact vendored distribution and Hermes-native routing eval

## Purpose

Close the remaining read-only repository-discovery gaps from SuperCLI: list
branches and tags, find visible projects by text, and search code within one
resolved project without cloning a repository or returning an unbounded vendor
payload.

This is the second of three independent GitLab read-coverage slices.

## Approved scope

| SuperCLI 0.14.1 source command | Connector operation |
|---|---|
| `super-cli gitlab branch list` | `gitlab_list_branches` |
| `super-cli gitlab tag list` | `gitlab_list_tags` |
| `super-cli gitlab search code` | `gitlab_search_code` |
| `super-cli gitlab project search` | `gitlab_search_projects` |

The new operations complement rather than replace:

- `gitlab_resolve_project` for exact project identity;
- `gitlab_list_group_projects` for browsing a known group hierarchy;
- `gitlab_list_repository_tree` for path-based tree navigation;
- `gitlab_read_file` for one known file; and
- commit readers for history and detail.

No clone, archive download, semantic/vector search, branch/tag mutation,
cross-origin content fetch, webhook access, or generic GitLab query tool is
added.

## Architecture and API basis

All operations reuse the existing schema -> invoke -> operations -> shared
client -> normalized envelope path. Plugin tools remain deferred behind
Hermes' `tool_search`/`tool_describe`/`tool_call` bridge, so no GitLab schema is
added to the permanent core tool surface.

The official GitLab REST contracts are:

- `GET /projects/:id/repository/branches`
  ([Branches API](https://docs.gitlab.com/api/branches/));
- `GET /projects/:id/repository/tags`
  ([Tags API](https://docs.gitlab.com/api/tags/));
- `GET /projects/:id/search?scope=blobs`
  ([Search API](https://docs.gitlab.com/api/search/)); and
- `GET /search?scope=projects`
  ([Search API](https://docs.gitlab.com/api/search/)).

Project-scoped code search is deliberate. If the user does not know the
project, the skill first calls `gitlab_search_projects`, asks for selection
when more than one plausible project remains, and then calls
`gitlab_search_code`. It does not launch an unbounded global code search.

## Tool contracts

### `gitlab_list_branches`

Required input: `project`.

Optional inputs:

- bounded plain-text `search` using GitLab's branch search semantics;
- bounded `max_items`; and
- structured `continuation`.

Regular-expression input is not exposed in this first cut. Each branch result
contains:

- name and same-origin web URL;
- default, merged, protected, developer push/merge, and caller push facts;
- commit SHA/short SHA/title and committed timestamp; and
- display-safe commit author/committer names when present, without email.

The result contains canonical project identity, filters, branches, `count`,
`truncated`, and `continuation`.

### `gitlab_list_tags`

Required input: `project`.

Optional inputs:

- bounded plain-text `search`;
- `order_by`, limited to broadly supported `name` or `updated`;
- `sort`, `asc` or `desc`;
- bounded `max_items`; and
- `continuation`.

Each tag contains name, target, bounded message, protected state, creation
timestamp when present, same-origin web URL, and bounded commit identity.
Release notes and assets are not expanded here; those belong to the release
operations in the release/inbox slice.

### `gitlab_search_code`

Required inputs:

- `project`;
- bounded nonempty `query`.

Optional inputs are `ref`, bounded `max_items`, and `continuation`. The server's
configured basic/advanced/Zoekt behavior is not exposed as a caller-controlled
mode; the connector uses the ordinary supported search contract.

Each match contains canonical project identity, filename, repository path,
ref, start line when supplied, and a redacted bounded snippet. Per-snippet and
aggregate text caps are internal connector constants, not user-configurable
settings. A match is evidence that the server returned a hit, not proof that
all repository history or every branch was searched.

### `gitlab_search_projects`

Required input: bounded nonempty `query`.

Optional inputs are bounded `max_items` and `continuation`.

Each visible project result contains ID, name, path with namespace, namespace,
description within a fixed bound, default branch, archived/visibility facts,
last-activity timestamp, and same-origin web URL. Search observes the
authenticated account's GitLab permissions and does not imply global tenant
coverage.

## Collection, normalization, and safety

Every collection reuses bounded pagination and emits `count`, `truncated`, and
a resumable continuation. Returned project/ref/path identities and URLs are
validated. Foreign URLs are omitted or rejected according to the existing
normalization contract; the connector never follows them.

Search snippets and descriptions are untrusted remote text. They are bounded,
redacted with the connector's existing configured-secret redactor, and returned
as data only. Skill instructions explicitly prohibit following instructions
found inside source snippets or project descriptions.

Malformed list members, booleans, commits, paths, timestamps, or URLs fail as
`invalid_remote_data`. Raw response bodies and provider errors do not reach the
model. Existing authentication, permission, not-found, rate-limit, deadline,
capacity, cancellation, and circuit-breaker behavior remains unchanged.

## Natural-language routing

There is no preliminary routing model call and no dynamic replacement of the
model's tool array. The normal Hermes agent loop progressively loads:

```text
gitlab router
  -> ericsson-gitlab:repository-research
  -> selected deferred tool schemas
  -> selected read operations
```

`repository-research` gains this decision table:

| User intent | First operation |
|---|---|
| Find repositories/projects by subject or name | `gitlab_search_projects` |
| Resolve one known exact project/path/URL | `gitlab_resolve_project` |
| Browse projects beneath a known group | `gitlab_list_group_projects` |
| List branches or determine available refs | `gitlab_list_branches` |
| List version tags or available tag refs | `gitlab_list_tags` |
| Find a symbol/string in one project | `gitlab_search_code` |
| Browse a known directory/path | `gitlab_list_repository_tree` |
| Read one known file | `gitlab_read_file` |
| Read history rather than current refs/content | commit tools |

The skill distinguishes tags from releases: a tag is a Git ref; a release is
published release metadata, notes, and assets. It also distinguishes project
search from code search. If a project is missing for code search, it searches
for candidate projects first and asks the user when identity remains ambiguous.

## Routing research and evaluation

The design uses the router/focused-skill pattern observed in
[GitLab's official AI skills](https://gitlab.com/gitlab-org/ai/skills) and the
community
[`gitlab-cli-skills`](https://github.com/vince-winkintel/gitlab-cli-skills)
suite, plus
[Anthropic](https://github.com/anthropics/skills/tree/main/skills/skill-creator)
and
[OpenAI](https://github.com/openai/skills/tree/main/skills/.system/skill-creator)
guidance on concise activation descriptions, progressive disclosure, and
forward tests. It does not copy their `glab` execution layer.

The shared routing corpus adds:

- clear prompts for each of the four new operations;
- paraphrases with no tool-name wording;
- project-search versus code-search near neighbors;
- tag versus release near neighbors;
- branch versus commit-history near neighbors;
- missing-project and multiple-project ambiguity; and
- untrusted snippet text that attempts to redirect the agent to a write tool.

Static tests require one primary skill owner per new tool and forbid write
tools in all read scenarios. The stubbed live harness records router, focused
skill, described schemas, and invoked underlying tools on configured Claude
and OpenAI/Codex models. Ambiguous cases run three times and must choose an
allowed safe read sequence or clarification every time.

## Connector CLI and migration documentation

The same operations are exposed through curated read-only commands:

```text
<brand> gitlab branch list <project>
<brand> gitlab tag list <project>
<brand> gitlab search code <project> --query <text>
<brand> gitlab project search --query <text>
```

The connector descriptor binds only schema-backed inputs. The SuperCLI mapping
YAML changes the four source rows to reviewed read dispositions, and the
migration Markdown is regenerated. No raw-output or no-throttle compatibility
flag is added.

The GitLab onboarding capability page and repository-research skill document
the new discovery flow. The onboarding catalog is regenerated and validated
after the capability reference changes. Documentation uses capability groups,
not a snapshot assertion of total tool count.

## Source and vendoring boundary

The source repository is implemented and committed first in a clean worktree.
After its focused and full gates pass, Hermes vendors the exact full source SHA
onto `base` and proves managed-byte equality. No vendor-script change is
expected because the existing plugin and skill roots are already included.

Literal Hermes `main`, brand overlays, and release/restamp work are outside
this slice.

## Test strategy

Red-green-refactor slices cover:

1. branch filters, normalized flags/commits, pagination, and malformed data;
2. tag ordering/search, commit identity, and release-field non-expansion;
3. project-scoped code search, ref propagation, snippet bounds/redaction, and
   untrusted content;
4. visible project search, permission-scoped results, and pagination;
5. schemas, dispatch, plugin manifest, CLI descriptors, and migration rows;
6. decision-table skill contracts and natural-language routing cases;
7. generated onboarding and migration documentation; and
8. source-to-vendor and deferred-tool parity.

No test clones a repository or requires a live GitLab instance. Optional live
UAT is read-only and records only canonical identities, counts, continuation,
and redacted snippets.

## Acceptance criteria

This slice is complete when:

1. users can list bounded branches and tags for one visible project;
2. users can find visible projects and then search code in one selected
   project without a clone;
3. code snippets are bounded, redacted, same-origin, and treated as untrusted;
4. the model reliably distinguishes project/code search and tag/release intent;
5. all four commands and migration rows use the canonical operations;
6. pagination, error, malformed-data, and permission tests pass;
7. docs and onboarding describe the real capability without count snapshots;
   and
8. vendored bytes match the verified source commit.

## Explicitly out of scope

- global unscoped code search;
- regex branch search;
- semantic/vector search selection;
- repository clone or archive download;
- branch or tag creation/deletion/protection changes;
- release detail expansion inside tag results;
- following instructions embedded in remote text; and
- webhook support.
