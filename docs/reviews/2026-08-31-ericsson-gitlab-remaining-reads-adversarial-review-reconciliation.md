# Ericsson GitLab remaining reads — adversarial review reconciliation

Review started: 2026-08-31
Reconciled: 2026-09-01
Status: **FINAL BLOCK**
Completed lanes: Codex GPT-5.6 Sol `xhigh`; Claude Opus (`opus` alias) `xhigh`

This report reconciles two independent adversarial lanes and controller
reproduction against the same immutable candidates. The model lanes found
disjoint issue sets. Neither omission was treated as a refutation: controller
probes reproduced all eight consolidated findings, and the three GitLab
response-shape findings were also checked against GitLab's official API docs.

## Immutable scope verified

| Repository | Base | Candidate | Candidate tree | Range |
|---|---|---|---|---|
| Ericsson source | `0d7654d14db0afe0c688a752a2676d8cabe2f981` | `903700b61eb0e2ceaad1175d6d0be93e38eec89f` | `9928cf8acd8725820eb2bb7c6ff37b8740b82e97` | 39 commits; 45 paths; +8,551/-364 |
| Hermes vendor | `bd1d42fdc0df8ea0c3e9dad3c940f7aed196b4d7` | `a5adf47e03634d76ffef7739a1a5ce8d046e16e6` | `477dec5016060ebe25685180353990a4996d6480` | 15 commits; 30 paths; +4,378/-151 |

Both review lanes used fresh detached clean worktrees. The Hermes manifest
points to the exact source candidate. Codex proved managed-file parity; Opus
independently compared 201 source/Hermes Git blobs with no mismatch or orphan.
No scope or provenance finding was found.

## Verdict and findings

`BLOCK`: one critical credential-disclosure defect, six important
correctness/test-integrity defects, and one minor schema-boundary regression
are reproducible.

| ID | Severity | Finding | Affected behavior |
|---|---|---|---|
| GL-AR-01 | CRITICAL | CI-variable descriptions return the configured PAT without redaction | `gitlab_list_ci_variables`; inherited CI-variable collection |
| GL-AR-02 | IMPORTANT | The shared paginator returns duplicate stable identities when adjacent pages overlap | All list operations using `_paginate`, including the new remaining reads |
| GL-AR-03 | IMPORTANT | Job and pipeline URLs are checked only for origin, not resource identity | Job detail/list and MR/pipeline list links can point at another project or ID |
| GL-AR-04 | IMPORTANT | Routing evidence accepts the correct tool name with missing or wrong required arguments | 45 of 51 clear routing cases do not declare `expected_arguments` |
| GL-AR-05 | IMPORTANT | Annotated tags are rejected because tag-object `target` is required to equal the peeled commit SHA | `gitlab_list_tags`; `hermes gitlab tag list` |
| GL-AR-06 | IMPORTANT | Release reads require `_links.self` to be an API URL although GitLab returns a web URL | `gitlab_list_releases`; `gitlab_read_release` |
| GL-AR-07 | IMPORTANT | Code search requires `basename` to include the extension although GitLab returns the extension-stripped basename | `gitlab_search_code`; `hermes gitlab search code` |
| GL-AR-08 | MINOR | Explicit `project: null` bypasses the non-null schema and becomes a global MR query | `gitlab_list_merge_requests` direct/model invoke boundary |

## GL-AR-01 — CI-variable description can disclose the PAT

Invariant violated: remote text must redact credentials before bounding, and
CI-variable reads must not expose secret material.

Production path:

1. `GitLabOperations.list_ci_variables()` normalizes each remote variable at
   `plugins/ericsson-gitlab/operations.py:5503`.
2. `_variable_metadata()` validates the raw `description` but returns it
   unchanged at `plugins/ericsson-gitlab/operations.py:5481`.
3. `list_ci_variables()` explicitly projects that unchanged field at
   `plugins/ericsson-gitlab/operations.py:5507`.
4. The class already has `_redact_text()` at line 498, but this path never
   calls it.

A network-free probe supplied the configured PAT in a valid remote
description and observed `secret_present: true` in the normalized result.
The same helper is used by inherited project/group CI-variable collection.

Why tests miss it: current tests prove that the remote `value` field is
discarded and malformed metadata fails safely. They use benign descriptions
and never place the configured credential in a valid description.

Smallest root fix: make `_variable_metadata()` redact `description` with the
configured credential before applying the UTF-8 output bound. Add regression
coverage for both direct `list_ci_variables()` and inherited CI collection,
including a credential split near the byte boundary.

## GL-AR-02 — `_paginate` admits duplicate identities

Invariant violated: every list must be deduplicated by stable identity and be
continuation-safe.

Production path:

1. `_paginate()` fetches adjacent pages at
   `plugins/ericsson-gitlab/operations.py:3620`.
2. Every normalized item is appended unconditionally at line 3637.
3. The helper has no stable-identity selector or seen set.
4. The new branch, tag, project/code search, release, To-Do, job, MR-pipeline,
   pipeline-job, MR, and CI-variable reads all route through this helper.

A network-free two-page probe returned job ID 41 on both pages. The result was
`[41, 41]`, with no rejection or deduplication. Page overlap is realistic when
page-number pagination races a concurrently changing collection.

Why tests miss it: pagination fixtures use distinct records. The separate
inherited-variable collector has its own identity set, which does not protect
the generic paginator or direct variable-list operation.

Smallest root fix: require each `_paginate()` caller to supply the operation's
stable identity and deduplicate before consuming the result budget. Add an
overlapping-page regression for one generic list and continuation regressions
that prove an item is neither repeated nor skipped when resuming.

## GL-AR-03 — same-origin URLs can disagree with returned identity

Invariant violated: returned URLs must agree with the resolved project and
resource ID.

Production path:

1. `_normalize_job()` checks the payload ID against `expected_id`, but validates
   the remote `web_url` only with `_same_origin_url()` at
   `plugins/ericsson-gitlab/operations.py:2102` and returns it at line 2144.
2. `_normalize_pipeline_summary()` performs the same origin-only check at
   lines 3895-3898 and has no project-path or expected-ID argument.
3. The surrounding result therefore claims one project/job or pipeline while
   exposing a link to another same-origin resource.

A network-free job probe requested project `division/platform/team/repo`, job
41, and supplied a valid same-origin URL ending in
`/other/project/-/jobs/99`. The operation returned ID 41 with that mismatched
URL. A corresponding pipeline probe accepted pipeline ID 900 with a URL for
another project/pipeline.

Why tests miss it: tests cover a foreign origin and a mismatched payload ID,
but not a same-origin URL whose project path or terminal resource ID differs.

Smallest root fix: construct canonical job and pipeline URLs from the resolved
project path and validated ID, as the job normalizer already does for nested
commit and pipeline links. Alternatively, validate the exact decoded URL path
and ID. Add sibling regressions for job detail, pipeline jobs, pipeline lists,
and MR-pipeline lists.

## GL-AR-04 — routing cases can pass with unusable arguments

Invariant violated: clear routing cases must select the owned read with the
required arguments.

Production path:

1. `expected_arguments_match()` in
   `scripts/gitlab_skill_routing_livetest.py:242` iterates only the optional
   `expected_arguments` map and returns `True` when the map is absent.
2. The final pass predicate at line 379 checks tool-name sequence and this
   vacuous argument result.
3. In `plugins/ericsson-gitlab/routing_cases.json`, 45 of 51 clear,
   non-ambiguous cases have no `expected_arguments` contract.

A synthetic trace for `mr-project-discovery-clear` called
`gitlab_list_merge_requests` with `{}`. It satisfied the exact sequence,
intent, routing milestones, safety, and argument checks even though the prompt
names a required project and the operation cannot fulfill the request without
it.

Why tests miss it: corpus tests check that operations and skills exist and that
selected personal-MR scopes are present. They do not require argument contracts
for every clear case or negatively test missing/wrong project, IID, pipeline,
query, ref, tag, status, or scope arguments.

Smallest root fix: add the required argument subset to every clear routing case
and make the corpus validator reject clear cases lacking such a contract when
their prompt contains required operands. Add negative harness tests using the
right operation with empty and deliberately wrong arguments.

## GL-AR-05 — annotated tags fail normalization

Invariant violated: documented GitLab responses must normalize into the
advertised bounded tag read.

`_normalize_tag()` parses `target` and `commit.id` independently at
`plugins/ericsson-gitlab/operations.py:1345-1365`, then rejects the payload at
line 1366 unless they are equal. GitLab documents different meanings for
annotated tags: `target` is the tag object's ID, while `commit.id` identifies
the peeled commit. Equality holds only for lightweight tags. See the
[GitLab Tags API](https://docs.gitlab.com/api/tags/).

A network-free public-operation probe returned `invalid_remote_data` for an
annotated tag with two valid 40-character SHAs. Existing fixtures set
`target == commit.id` even when `message` and `created_at` make the fixture an
annotated tag, so the green tests encode the defect.

Smallest root fix: retain independent full/short SHA validation but remove the
`target == commit.id` requirement. Add documented lightweight and annotated
payload regressions through `gitlab_list_tags` and the CLI adapter.

## GL-AR-06 — release reads reject GitLab's documented self URL

Invariant violated: release URL validation must accept the documented
same-origin canonical identity.

`_normalize_release_summary()` accepts a same-origin `_links.self` at lines
1424-1425. Both `read_release()` and `list_releases()` then require its path to
equal `/api/v4/projects/<id>/releases/<tag>` at lines 1635-1638 and 1674-1679.
GitLab's documented response instead returns the user-facing web path
`/<namespace>/<project>/-/releases/<tag>`. See the
[GitLab Releases API](https://docs.gitlab.com/api/releases/).

Network-free list and detail probes using that documented web URL both returned
`invalid_remote_data`. The test factory constructs an API URL, so every current
release test confirms the connector's assumption rather than GitLab's contract.

Smallest root fix: validate the decoded canonical web release path against the
resolved project path and tag, or derive the returned web URL solely from those
validated identities. Add list/detail regressions using the official response
shape. Also confirm whether `milestones` can be absent and default it to an
empty list if the deployed GitLab versions permit omission.

## GL-AR-07 — code search rejects documented `basename`

Invariant violated: documented GitLab blob-search matches must normalize into
the advertised bounded code-search result.

`_normalize_code_match()` reads `path`, `filename`, and `basename` at
`plugins/ericsson-gitlab/operations.py:1174-1177`, then requires `basename` to
equal the final path segment including its extension. GitLab's documented blob
result uses `basename: "README"` while both `path` and `filename` are
`"README.md"`. See the [GitLab Search API](https://docs.gitlab.com/api/search/#scope-blobs).

A network-free search probe with `basename: "router"` and
`path/filename: "src/router.py"` returned `invalid_remote_data`. Existing
fixtures use `basename: "router.py"`, again pinning the implementation's
assumption instead of the provider contract.

Smallest root fix: derive the returned filename from the validated path and
stop requiring the provider's `basename` to equal its last segment. Preserve
the `filename == path` compatibility check only while that field remains in the
supported GitLab contract. Add root and nested documented blob results.

## GL-AR-08 — explicit null project becomes a global MR query

Invariant violated: invoke validation must agree with the published JSON
schema.

The `gitlab_list_merge_requests` schema permits a project string or positive
integer, or omission for a global/personal query; it does not permit `null`.
`tools.invoke()` checks only key presence and allowlisting at
`plugins/ericsson-gitlab/tools.py:760-772`. Dispatch then uses
`values.get("project")` at line 860, conflating an absent property with an
explicit null.

A network-free probe passed `{"project": null}` directly through `invoke()`.
It succeeded as an unscoped query and returned `project: null`, despite the
schema's non-null `oneOf`. This does not expand the authenticated user's GitLab
permissions, so the impact is minor, but malformed model/plugin input can
silently broaden the requested search.

Smallest root fix: reject explicit null when `project` is present, while
preserving omission as the intentional global form. Add a direct-invoke and
registered-handler regression.

## SuperCLI port and surface assessment

The classic shell CLI has a dedicated `hermes gitlab` tree with these groups:

`project`, `group`, `branch`, `tag`, `release`, `search`, `commit`, `mr`,
`todo`, `repository`, `file`, `pipeline`, `job`, `variable`, and `ci`.

The remaining-read work added eleven command leaves and upgraded two existing
commands/mappings:

| SuperCLI command | Hermes command | Change |
|---|---|---|
| `gitlab branch list` | `hermes gitlab branch list <project>` | New leaf |
| `gitlab job view` | `hermes gitlab job show <project> <job-id>` | New leaf |
| `gitlab mr pipeline list` | `hermes gitlab mr pipeline-list <project> <iid>` | New leaf |
| `gitlab pipeline job list` | `hermes gitlab pipeline job-list <project> <pipeline-id>` | New leaf |
| `gitlab project search` | `hermes gitlab project search --query <query>` | New leaf |
| `gitlab release list` | `hermes gitlab release list <project>` | New leaf |
| `gitlab release view` | `hermes gitlab release show <project> <tag>` | New leaf |
| `gitlab search code` | `hermes gitlab search code <project> --query <query>` | New leaf |
| `gitlab tag list` | `hermes gitlab tag list <project>` | New leaf |
| `gitlab todo list` | `hermes gitlab todo list` (optional `--project`) | New leaf |
| `gitlab variable list` | `hermes gitlab variable list <project>` | New leaf; metadata only |
| `gitlab mr list` | `hermes gitlab mr list [project]` | Existing command widened for global/personal scopes |
| `gitlab mr diff` | `hermes gitlab mr show <project> <iid>` | Existing read now supplies bounded structured diffs |

Intentional exclusions remain unchanged: webhook enumeration, To-Do
completion, release creation, job/pipeline cancellation, pipeline creation,
MR rebase, project creation/editing, and other new writes.

| Surface | Dedicated GitLab command/UI | Shared remaining-read capability |
|---|---|---|
| Classic CLI | **Yes.** `hermes gitlab ...` is an argparse command tree. | Yes, when the provider plugin is configured. |
| TUI | **No.** No `/gitlab` slash command or GitLab-specific component was added. | **Yes, conditionally.** Normal chat reaches the shared `ericsson-gitlab` model toolset when the plugin is enabled/configured and selected for the CLI platform. |
| Desktop | **No.** No GitLab-specific panel, button, form, or command palette entry was added. | **Yes, conditionally.** Desktop chat uses the same gateway/agent toolset and generic plugin/toolset configuration. |

Therefore the implementation provides cross-surface conversational capability,
not dedicated TUI/Desktop command parity. If the requirement was that all
three surfaces expose equivalent visible GitLab controls, it is not complete.

## Verification evidence and limitations

- Codex verified exact SHAs, trees, ancestry, diff statistics, detached clean
  worktrees, manifest provenance, and source/Hermes byte parity.
- Opus independently verified the same immutable scope, all frozen artifact
  hashes, and exact parity across 201 managed Git blobs.
- Focused production tests for job reads, pipeline-job continuation,
  CI-variable value omission, and cross-origin URL rejection passed.
- Focused CLI parser, operation binding, migration mapping, and routing-corpus
  tests passed after providing the immutable Hermes worktree to source tests.
- Opus ran two focused source selections: 610 and 288 tests, for **898 passing**
  tests total. Migration/catalog generation checks and catalog validation also
  passed. These green tests did not contain the documented provider shapes for
  GL-AR-05 through GL-AR-07.
- Controller reran network-free synthetic probes and reproduced all eight
  consolidated findings. The official GitLab tag, release, and search docs
  independently confirm the three disputed remote response shapes.
- The live two-family routing matrix was not rerun. GL-AR-04 concerns its
  pass predicate and corpus argument coverage, which were proven with a
  synthetic transcript.
- No live GitLab request, credential use, write, webhook, push, release, PR, or
  brand operation was performed.
- Codex did not report GL-AR-05 through GL-AR-08, and Opus did not report
  GL-AR-01 through GL-AR-04. Controller reproduction retained both independent
  sets; omission by one reviewer is not counter-evidence.

## Required next actions

1. Fix GL-AR-01 before any release or real CI-variable use.
2. Fix GL-AR-05 through GL-AR-07 before advertising the affected SuperCLI
   replacements as supported reads.
3. Fix GL-AR-02 through GL-AR-04 and add the specified pagination, identity,
   and negative routing regressions.
4. Fix GL-AR-08 at the shared invoke boundary.
5. Run read-only UAT for each newly reviewed GitLab read against an approved
   non-production instance, recording only redacted bounded identities and
   counts. This is the most efficient way to catch provider-fixture drift.
6. Re-run focused source tests, migration/catalog checks, routing, re-vendoring,
   installed inventory, and exact source/Hermes parity.

Counts: **1 CRITICAL, 6 IMPORTANT, 1 MINOR**.
