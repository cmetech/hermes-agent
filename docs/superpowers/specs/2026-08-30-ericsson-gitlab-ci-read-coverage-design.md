# Ericsson GitLab CI Read Coverage

**Status:** Approved

**Date:** 2026-08-30

**Target repositories:**

- `ericsson-capabilities` — authoritative connector implementation, skills,
  connector CLI descriptors, migration mapping, documentation, and tests
- `hermes-agent` — exact vendored connector distribution and Hermes-native
  natural-language routing evaluation

## Purpose

Close the remaining read-only SuperCLI gaps needed to move from a pipeline or
merge request to its jobs, inspect one job without reading its trace, and list
CI/CD variable metadata without exposing values.

This is the first of three independent read-coverage slices. It also corrects
two existing documentation errors that affect the whole GitLab migration map:

1. `gitlab_read_merge_request` already returns bounded per-file diffs, so
   `super-cli gitlab mr diff` is not an unsupported gap; and
2. the onboarding page's fixed read/write counts are stale and should be
   replaced with capability tables rather than another count snapshot.

## Approved scope

Add four focused read operations:

| SuperCLI 0.14.1 source command | Connector operation |
|---|---|
| `super-cli gitlab job view` | `gitlab_read_job` |
| `super-cli gitlab pipeline job list` | `gitlab_list_pipeline_jobs` |
| `super-cli gitlab mr pipeline list` | `gitlab_list_merge_request_pipelines` |
| `super-cli gitlab variable list` | `gitlab_list_ci_variables` |

Preserve the existing responsibilities of:

- `gitlab_list_pipelines` for project pipeline discovery;
- `gitlab_read_pipeline` for one pipeline's metadata;
- `gitlab_job_log` for bounded job trace content; and
- `gitlab_inspect_ci` for holistic CI-file, include, branch, pipeline-window,
  and inherited variable-metadata investigation.

No job or pipeline cancel/run/rebase operation is added. Existing retry/play
write tools keep their current approval behavior. Webhooks remain excluded.

## Architecture

The new reads follow the connector's existing narrow path:

```text
tool schema
  -> tools.invoke()
  -> GitLabOperations
  -> shared GitLabClient
  -> GitLab REST API
  -> bounded normalized result
  -> application.execute() safe envelope
```

There is no second GitLab client, generic query layer, core Hermes tool, MCP
server, `glab` process, repository clone, or hidden model call. The operations
reuse canonical project resolution, the shared operation deadline, cancellation,
pagination, same-origin URL validation, display-safe user normalization,
redaction, and the existing stable error taxonomy.

The official GitLab REST contracts used by this slice are:

- `GET /projects/:id/jobs/:job_id` ([Jobs API](https://docs.gitlab.com/api/jobs/));
- `GET /projects/:id/pipelines/:pipeline_id/jobs` ([Jobs API](https://docs.gitlab.com/api/jobs/));
- `GET /projects/:id/merge_requests/:merge_request_iid/pipelines`
  ([Merge requests API](https://docs.gitlab.com/api/merge_requests/)); and
- `GET /projects/:id/variables`
  ([Project-level CI/CD variables API](https://docs.gitlab.com/api/project_level_variables/)).

## Tool contracts

### `gitlab_read_job`

Required inputs:

- `project`: numeric ID, namespace/project path, or same-origin project URL;
- `job_id`: positive GitLab job ID.

The result contains canonical project identity and one normalized job with:

- ID, name, stage, status, ref, tag state, and `allow_failure`;
- created, queued, started, finished, erased, and duration facts when present;
- bounded failure reason;
- pipeline ID/status/web URL;
- commit SHA/short SHA/title/web URL;
- display-safe triggering user; and
- same-origin job web URL.

It does not return trace text, artifact contents, runner authentication data,
variables, raw payload fields, user email, or avatar URLs. A request for logs
belongs to `gitlab_job_log` after the job has been identified.

### `gitlab_list_pipeline_jobs`

Required inputs:

- `project`;
- `pipeline_id`: positive pipeline ID.

Optional inputs:

- bounded `statuses`: an allowlisted array of GitLab job status values;
- `include_retried`, default `false`;
- bounded `max_items`; and
- opaque structured `continuation` returned by the operation.

The result contains canonical project identity, pipeline ID, applied filters,
normalized job summaries using the same identity fields as `gitlab_read_job`,
`count`, `truncated`, and `continuation`.

### `gitlab_list_merge_request_pipelines`

Required inputs:

- `project`;
- `iid`: positive project-local merge-request IID.

Optional inputs are bounded `max_items` and `continuation`. The result contains
canonical project and MR identity plus normalized pipeline summaries compatible
with `gitlab_list_pipelines`, followed by `count`, `truncated`, and
`continuation`.

This operation answers which pipelines belong to an MR. It does not fetch the
jobs in those pipelines automatically.

### `gitlab_list_ci_variables`

Required input: `project`.

Optional inputs are bounded `max_items` and `continuation`.

This operation lists project-level variable metadata only:

- `key`;
- variable `type`;
- `protected`, `masked`, `hidden`, and `raw` flags;
- `environment_scope`; and
- bounded description when present.

GitLab may include each variable's value in the API response. The connector
must discard that field during normalization and must never place it in the
result, an exception, a warning, a log, a fixture snapshot, or a saved eval
trace. The operation does not return ancestor-group variables. Users asking
for inherited project and ancestor-group metadata continue to use
`gitlab_inspect_ci`, whose existing collector already owns that bounded
multi-source behavior.

## Collection and safety invariants

All three collection operations use the existing page-size and page-ceiling
machinery, enforce a result limit after normalization, and return a usable
continuation rather than claiming complete coverage after truncation.

Remote identifiers, booleans, timestamps, durations, nested users, commits,
pipelines, and URLs are type-checked. Invalid response members fail as
`invalid_remote_data`; they are not silently coerced into plausible evidence.

The external envelope remains:

```json
{"success": true, "result": {}}
```

or a safe error with a connector-owned category, message, and optional static
remediation. Raw GitLab response bodies, headers, origin paths, PATs, and
certificate material never enter an error result.

## Natural-language routing

Hermes does not make a separate classifier request and then replace the tool
array. The tool array stays stable for prompt caching. A normal agent turn uses
progressive disclosure:

```text
user request
  -> skill_view("gitlab")
  -> skill_view("ericsson-gitlab:ci-investigation")
  -> tool_describe(selected tools)
  -> tool_call(selected read tools)
```

If the deferred catalog does not expose exact tool names within its listing
budget, the model calls `tool_search` before `tool_describe`. All calls remain
inside the same conversation and ordinary Hermes agent loop.

`ci-investigation` gains a compact decision table:

| User intent | First read operation |
|---|---|
| List or filter project pipelines | `gitlab_list_pipelines` |
| Inspect one pipeline | `gitlab_read_pipeline` |
| Find pipelines attached to one MR | `gitlab_list_merge_request_pipelines` |
| List jobs in one pipeline | `gitlab_list_pipeline_jobs` |
| Inspect one job's metadata | `gitlab_read_job` |
| Read one job's trace/log | `gitlab_job_log` |
| List project variable metadata | `gitlab_list_ci_variables` |
| Audit CI files, includes, branches, and inherited metadata | `gitlab_inspect_ci` |

The always-indexed `gitlab` router continues to route pipeline, job, CI
configuration, include, and variable requests to this qualified skill.

## Routing research and evaluation

The skill design adopts the useful parts of:

- [GitLab's official AI skills](https://gitlab.com/gitlab-org/ai/skills),
  especially the `glab` vocabulary and focused pipeline/MR workflows;
- the community
  [`gitlab-cli-skills`](https://github.com/vince-winkintel/gitlab-cli-skills)
  router with separate CI and job guidance;
- [Anthropic's skill-authoring guidance](https://github.com/anthropics/skills/tree/main/skills/skill-creator)
  on precise activation descriptions and progressive disclosure; and
- [OpenAI's skill-authoring guidance](https://github.com/openai/skills/tree/main/skills/.system/skill-creator)
  on forward tests using realistic prompts.

The GitLab CI pattern skill in
[`wshobson/agents`](https://github.com/wshobson/agents) was also reviewed, but
it targets pipeline authoring rather than operational read-tool selection and
is not used as the routing template.

Their `glab` execution commands are not copied; this connector uses its own
bounded REST operations and safety envelope.

A shared checked-in routing corpus records prompt, expected router, expected
qualified skill, allowed first GitLab read, and allowed follow-up reads. This
slice contributes clear, paraphrased, terse-ID, and ambiguous cases for all
four new operations plus their existing neighbors. Static tests prove every
referenced skill/tool exists and no case permits a write. A Hermes live eval
reuses the existing tool-search live-test pattern with stubbed GitLab handlers,
records the actual `skill_view`/bridge/underlying-tool trace, and never calls a
real GitLab server.

Before completion, clear cases must pass on one configured Claude model and
one configured OpenAI/Codex model. Ambiguous cases run three times per model
and must select an allowed safe route or ask for clarification every time.
Any GitLab write selection is a hard failure.

## Connector CLI and documentation

The connector CLI gains read-only descriptors backed by the same operations:

```text
<brand> gitlab job show <project> <job-id>
<brand> gitlab pipeline job-list <project> <pipeline-id>
<brand> gitlab mr pipeline-list <project> <iid>
<brand> gitlab variable list <project>
```

The curated SuperCLI YAML changes the four source rows from unsupported to a
reviewed read disposition and regenerates, rather than hand-edits,
`docs/cli-migration/supercli-0.14.1.md`.

In the same documentation correction:

- `super-cli gitlab mr diff` maps to the existing
  `gitlab_read_merge_request` / `<brand> gitlab mr show` bounded result;
- webhook list stays explicitly excluded; and
- the onboarding GitLab page replaces fixed read/write totals with grouped
  capability tables.

The onboarding catalog is regenerated and validated because the capability
reference materially changes. Tests assert mapping behavior and referenced
operations, not the total number of tools.

## Source and vendoring boundary

Implementation starts in a clean `ericsson-capabilities` worktree from its
current `main`. Tests and source documentation pass before a source commit is
created. Hermes then vendors that exact clean full SHA onto neutral `base` and
verifies the managed inventory and copied bytes. No shared connector edit is
authored first in `hermes-agent`, literal `main`, or a brand branch.

No vendor-script change is expected because the existing plugin and skill
roots are already managed artifact types.

## Test strategy

Implementation follows red-green-refactor vertical slices:

1. job normalization and separation from trace content;
2. pipeline-job pagination, filters, retried-job behavior, and continuation;
3. MR-pipeline pagination and normalized pipeline compatibility;
4. project variable metadata projection with adversarial secret values;
5. schemas, dispatch, plugin registration, manifest, and CLI descriptors;
6. skill routing contracts and the natural-language corpus;
7. generated migration/onboarding documentation; and
8. exact source-to-vendor and Hermes deferred-tool regression checks.

Tests include permission failures, missing jobs/pipelines/MRs, malformed remote
members, bad continuation, pagination ceilings, cancellation/deadline
propagation, foreign URLs, oversized text, and values that look like PATs,
passwords, private keys, and multiline secrets.

## Acceptance criteria

This slice is complete when:

1. a user can move from MR to pipelines to jobs to one job's metadata using
   bounded read tools;
2. a request for a job log still uses `gitlab_job_log`, not `gitlab_read_job`;
3. project variable metadata can be listed without any value reaching model
   context or persisted eval evidence;
4. natural-language routing selects the correct CI read path across the
   approved model evaluation;
5. the connector CLI and generated SuperCLI map expose all four replacements;
6. `mr diff`, webhook exclusion, and capability documentation are truthful;
7. deterministic safety, pagination, and error tests pass; and
8. the source and vendored connector trees agree at the recorded full SHA.

## Explicitly out of scope

- job or pipeline cancellation;
- pipeline creation;
- merge-request rebase;
- CI variable values or variable writes;
- group-variable standalone enumeration;
- webhook enumeration or mutation;
- a category classifier model call; and
- dynamic per-category toolset replacement.
