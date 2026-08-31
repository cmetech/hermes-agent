# Adversarial plan review prompt — remaining Ericsson GitLab reads

Give everything below this introduction to a fresh reviewer with read and
shell access to both repositories. The reviewer must assess the three approved
specifications and executable implementation plans for the remaining read-only
GitLab coverage migrated from SuperCLI 0.14.1:

1. CI read coverage;
2. repository discovery; and
3. releases and personal inbox.

This is an adversarial **plan-quality, source-grounding, and routing-reliability
review**. It is not an implementation task and not a code review of features
that do not exist yet. Try to falsify the plans before implementation begins.
Find false source assumptions, omitted approved behavior, invented interfaces,
unsafe or lossy result contracts, incorrect task ordering, non-executable TDD
steps, unreliable natural-language routing gates, documentation drift, and
acceptance gates that could let an incomplete port ship.

Do not modify production code, tests, generated files, specifications, plans,
documentation, Git history, branches, worktrees, or refs. Do not create a
branch, commit, rebase, merge, push, publish, release, or contact a live GitLab
server. The caller may authorize one exact review-report path; that report is
the only permitted write. Otherwise return the report as your final response.

## Role and proof burden

You are a skeptical principal engineer experienced with Python tool plugins,
REST clients, GitLab APIs, JSON-schema tool contracts, paginated collections,
CLI migration, LLM tool selection, progressive-disclosure skills, prompt
caching, deterministic evals, source-first vendoring, and strict TDD plans.

Assume every plan claim is unproven until traced to:

1. one normative requirement in the approved specification;
2. current authoritative Ericsson source or an explicitly new artifact;
3. current Hermes runtime, plugin, skill, deferred-tool, and vendoring behavior;
4. the real SuperCLI mapping/documentation authority where migration parity is
   claimed; and
5. an explicit task, RED test, GREEN change, verification command, commit
   boundary, and acceptance gate.

Do not report the absence of future functions/files as an implementation bug.
Report whether the plan creates them in the correct repository, through real
interfaces, with complete proof. Existing names, test filenames, comments,
prior approvals, and plausible snippets are leads rather than evidence.

Review all three slices together. They deliberately build one shared routing
corpus and live harness in sequence; a sound local task can still be invalid if
it assumes an artifact, corpus shape, tool inventory, generated document, or
vendored byte created differently by another plan.

## Repository state and preservation rules

### Hermes target/distribution repository

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

At prompt creation:

- development branch: `base`;
- reviewed planning commit: `4e8c3d61d2a283ab4c812ec9fe0f296f7b6c2944`;
- approved specification commit: `69a338ba29`;
- `origin/base`: `2f200a1db9fc97b528ab0c1d5eff533a89916f6e`;
- literal `main` is synchronization-only;
- unrelated untracked `.otto/`, `docs/assessments/`, `docs/design/`,
  `docs/handoffs/`, and `docs/plans/` content is user-owned; and
- `docs/superpowers/` is ignored by a repository rule even though these six
  reviewed artifacts are committed.

### Authoritative Ericsson source repository

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities`

At prompt creation:

- branch: `main`;
- expected HEAD: `0d7654d14db0afe0c688a752a2676d8cabe2f981`;
- status: clean; and
- this repository owns connector implementation, tests, plugin skills,
  SuperCLI mappings, generated migration docs, and onboarding source.

Begin by recording branch, exact SHA, status, and worktree list for both
repositories. Preserve all worktrees and user files. Use read-only commands.
If a reviewed artifact hash differs, stop with `REVIEW_INPUT_CHANGED`; do not
silently review another revision or mutate state to match.

## Immutable review inputs

Read all six files completely and verify these SHA-256 hashes:

| Artifact | Expected SHA-256 |
| --- | --- |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-ci-read-coverage-design.md` | `6d903ea5b532095789258d7c60ccb6d05652e928a73c4bd2c33c52db4151198e` |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-repository-discovery-design.md` | `70b668aecd0a5b397926c859089eca44240f178c1f1c5d4645c5f59005500dff` |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-release-inbox-design.md` | `87fe40b20981ac878f6359c26e2c4a85d5d2e5469e9a98310751cc5dafd8e1d8` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md` | `7727df3384bd8f6832737167212c0a5b2bdd99e092eb55499f17bb2a39f132d8` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md` | `ab1bc9139072b0ad32c387bef1f8de91dfa8d0522dfe378073d70e1ed560bbbe` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md` | `4df500b6a92f60df9bf0934b56290dae824602670e46533624b65c912debb513` |

## Sources of truth to inspect

Read both repository `AGENTS.md` files completely. Then inspect enough of each
listed area to prove or disprove every premise used by the plans.

### Ericsson source

- `plugins/ericsson-gitlab/{operations,tools,client,models,application,__init__}.py`
- `plugins/ericsson-gitlab/_common/`
- `plugins/ericsson-gitlab/plugin.yaml`
- every existing GitLab plugin skill under
  `plugins/ericsson-gitlab/skills/`
- `skills/ericsson/gitlab/SKILL.md`
- onboarding GitLab reference, its catalog builder/validator, and generated
  catalog contract
- `plugins/ericsson-connector-cli/{descriptors,parser}.py`
- `plugins/ericsson-connector-cli/mappings/supercli-0.14.1.yaml`
- `plugins/ericsson-connector-cli/scripts/build_migration_docs.py`
- `docs/cli-migration/supercli-0.14.1.md`
- `docs/connector-porting/{gitlab-baseline,gitlab-behavior-map}.md`
- `tests/test_gitlab_{client,plugin,reads,ci,exploration,skills}.py`
- `tests/test_connector_cli_{gitlab_port,descriptors,parser,migration,docs}.py`
- `sets/ericsson.json`, `docs/README.md`, and `docs/configuration.md`

The historical plan at
`docs/superpowers/plans/2026-08-15-ericsson-gitlab-coverage.md` may explain
intent but is not authority over the six frozen review inputs or current code.

### Hermes runtime/distribution

- `scripts/vendor-ericsson.mjs` and its tests
- `capabilities/{ericsson,ericsson-vendored-paths}.json`
- vendored `plugins/ericsson-gitlab`, `plugins/ericsson-connector-cli`, and
  `skills/ericsson`
- `tests/hermes_cli/test_ericsson_connector_distribution.py`
- `tests/hermes_cli/test_ericsson_connector_surfaces.py`
- `tests/plugins/workflow/test_ericsson_connector_toolsets.py`
- `scripts/tool_search_livetest.py` and `scripts/LIVETEST_README.md`
- the real skill loader, `skill_view`, `tool_search`, `tool_describe`, tool
  registry/dispatch, plugin enablement, toolset construction, and agent loop
  reached by the proposed live harness

Use relevant Git history when a missing or odd behavior may be intentional.
When network access is available, official GitLab API documentation may be
used to validate response fields and endpoint query compatibility. Current
checked-out code remains the authority for local interfaces. Record any remote
documentation claim separately from a verified source claim.

## Approved delivery scope

The plans add or extend exactly these read behaviors:

| Slice | New/extended operations |
| --- | --- |
| CI | `gitlab_read_job`, `gitlab_list_pipeline_jobs`, `gitlab_list_merge_request_pipelines`, `gitlab_list_ci_variables` |
| Repository | `gitlab_list_branches`, `gitlab_list_tags`, `gitlab_search_code`, `gitlab_search_projects` |
| Release/inbox | `gitlab_list_releases`, `gitlab_read_release`, `gitlab_list_todos`, project-optional/personal-scope extension of `gitlab_list_merge_requests` |

Webhook listing is deliberately excluded. Also excluded are mutations, To-Do
completion, release creation, clone/archive/download, global blob search,
arbitrary URL fetches, a new dependency, a new core model tool, and a separate
classifier request that swaps tool schemas mid-conversation.

The approved routing architecture keeps the conversation prefix/tool surface
stable. The normal agent progressively loads the thin always-indexed `gitlab`
router, one focused qualified plugin skill, deferred tool schemas, and then
read operations in the ordinary agent loop. It does not make one LLM call to
classify a category and a second call with a replaced category toolset.

Implementation must happen source-first in `ericsson-capabilities`, pass its
full gate, be committed there, and only then be vendored byte-for-byte into
Hermes from the exact clean source SHA. Generated migration/onboarding files
are outputs of their checked-in authorities, not hand-edited authorities.

## Non-negotiable invariants

A demonstrated violation is Critical or Important according to consequence.

1. **Exact scope.** All twelve approved new/extended read behaviors are
   implemented; webhooks and every listed mutation/non-goal remain excluded.
2. **Source-first ownership.** No shared connector/skill/mapping/documentation
   behavior is authored first or only in Hermes.
3. **Stable model context.** No preliminary classifier call, dynamic per-turn
   tool-array replacement, or mid-conversation system-prompt mutation is
   introduced.
4. **One routing authority per intent.** The thin router chooses a focused
   skill; focused skills own tool decisions without duplicated conflicting
   instructions. CI, repository, release/tag, and personal/project MR near
   neighbors have explicit precedence.
5. **Read-only routing.** Static and live cases never allow a write tool.
   Untrusted remote titles, descriptions, snippets, notes, traces, and assets
   are data, never instructions.
6. **No live GitLab in routing evals.** The harness intercepts before every
   underlying `gitlab_*` handler and fails closed on any non-allowlisted call.
7. **Two independent model families.** Clear cases run once on configured
   Claude and OpenAI/Codex models; ambiguous cases run three times per model
   and may only choose an allowed safe sequence or ask for clarification.
8. **Strict remote data.** Required identifiers and documented field types are
   validated. Missing optional GitLab fields stay absent/`None`; malformed
   evidence is not silently coerced into plausible facts.
9. **Bounded reads.** Collection limits, page ceilings, continuation,
   truncation, cancellation, deadline, and partial-result behavior use the
   connector's established machinery and never claim completeness falsely.
10. **Sensitive-value boundary.** CI variables expose metadata only, never
    values. Search snippets are redacted/bounded. Release asset URLs are
    admitted only under the approved same-origin rule. Errors never leak raw
    bodies, headers, PATs, paths, or certificate material.
11. **Identity semantics.** Positive IDs, project path/URL resolution, refs,
    tags, `@me`, current user lookup, global versus project MR identity, and
    cross-project MR links retain enough canonical identity for safe follow-up.
12. **Schema/runtime parity.** Tool schema names, required fields, enums,
    defaults, bounds, invoke dispatch, manifest inventory, qualified skill
    declarations, and CLI descriptors match the operation contracts exactly.
13. **Migration truth.** SuperCLI rows are changed only when replacement
    behavior exists. Generated migration docs and onboarding catalog are
    rebuilt from authority; webhook and remaining limitations stay explicit.
14. **Executable TDD.** Every RED fails for the stated missing behavior; every
    GREEN names real symbols and enough implementation to pass; focused and
    full commands are valid in the named repository; task dependencies are
    ordered across all three plans.
15. **Exact vendoring.** The source worktree is clean before vendoring, the
    recorded full SHA matches `vendoredFrom`, managed bytes match, unrelated
    Hermes paths remain untouched, and ignored live transcripts are not
    committed.
16. **No speculative surface.** Reuse existing clients, envelope, pagination,
    parser, descriptor, skill, deferred-tool, and harness patterns. No new
    core tool, dependency, or generic abstraction is added without an existing
    consumer in these plans.

## Required review method

### 1. Build traceability and dependency matrices

- Map every normative specification requirement and acceptance criterion to
  concrete plan tasks and tests.
- Rate each plan task `complete`, `partial`, `missing`, or `contradicted`.
- Identify orphan requirements, plan work without approved authority, and
  cross-plan ordering assumptions.
- Verify that sequential source/vendoring commits cannot lose corpus entries,
  skill ownership, generated docs, or earlier slice behavior.

### 2. Audit exact API and normalization contracts

For every operation, trace validation, endpoint/path/query construction,
pagination, response shape, normalized output, links/identity, warnings,
continuation, errors, and schema/dispatch/manifest exposure. Challenge assumed
GitLab response nesting and optional fields. Verify reuse of existing helpers
does not subtly change established pipeline or MR results.

Explicitly inspect:

- optional nested job `pipeline`, `commit`, `user`, runner, artifacts, and
  timestamps;
- pipeline-job `include_retried` query encoding;
- MR-pipeline project-local IID and pagination;
- project CI variable metadata without `value`, including malformed rows;
- branch/tag commit summaries and release-versus-tag separation;
- project blob search redaction, cap, pagination, and continuation semantics;
- global project search and its visibility/identity results;
- release list/detail tag encoding, notes, evidence, and asset-source/link URL
  admission;
- To-Do action/target/state allowlists across supported GitLab versions; and
- global/project MR scopes, `@me` translation, current-user lookup, pagination,
  references, fork/cross-project identity, and backward compatibility.

### 3. Audit CLI and generated documentation

Confirm each proposed command is expressible by the current descriptor/parser
model, including optional project positionals for MR listing. Trace descriptor
sets, parser validation, invoke dispatch, mapping tests, generator inputs, and
generated output. Reject hand edits that will be overwritten or mappings that
claim parity while a required argument/result is unavailable.

### 4. Audit natural-language routing architecture and eval validity

Trace the actual Hermes skill/deferred-tool path. Confirm the thin router is
always discoverable in the intended surface and qualified plugin skills/tools
become available through real runtime behavior without changing the cached
prefix/tool array. Check that primary ownership is unambiguous without making
multi-intent requests impossible.

For the corpus and live harness, verify:

- all referenced skill/tool names exist at the point each plan runs;
- allowed-first and allowed-follow-up semantics represent realistic safe
  sequences, including clarification;
- clear and ambiguous case repetition is enforced rather than merely stored;
- the model identifiers/family gate cannot accept two aliases of one family;
- plugin copying, enablement, fake credentials/origin, module reset, and
  registry interception reach the real agent path;
- interception occurs before any network-capable handler, including unexpected
  GitLab names;
- reports prove router view, focused skill view, schema description, and
  underlying tool calls without retaining secrets or raw model/tool output;
- nonzero status is guaranteed for wrong skill, wrong first read, disallowed
  follow-up, any write, no call on a clear case, or missing required model
  family; and
- CI creates the shared corpus/runner once while later plans only extend it.

### 5. Audit strict-TDD and repository safety

For every task, confirm exact files, current symbols, test helpers, Python/Node
commands, expected RED reason, GREEN completeness, staging scope, commit
boundary, ignored-file behavior, worktree/base branch safety, source full gate,
and final cleanliness assertions. Reject placeholders that leave a product or
API decision to the implementer.

Run only small read-only existing checks when needed to validate a premise. Do
not create future test files or run the vendor script during review.

## Specific premises to challenge

Return `SUPPORTED`, `UNSUPPORTED`, or `INSUFFICIENTLY ESTABLISHED` for each:

1. Existing `_request`, collection, continuation, validation, envelope, and
   normalization helpers support every new operation without a new layer.
2. Extending the existing pipeline summary helper does not change current
   pipeline-list/read behavior.
3. Planned job normalization handles real optional GitLab response members
   while still rejecting malformed required evidence.
4. The code-search aggregate cap and continuation remain honest under the
   maximum per-page/page-ceiling combination.
5. Global/project MR listing can share one normalizer without losing project
   identity, especially for fork/cross-project results.
6. To-Do enum allowlists are compatible with the connector's supported GitLab
   versions and fail clearly on unknown remote values.
7. Same-origin release asset filtering is applied to every returned asset URL
   without breaking valid relative or canonical links.
8. The current CLI parser/descriptors can support an optional project
   positional without changing unrelated commands.
9. The routing corpus has one stable schema that all three plans can extend
   without order-dependent failures or duplicate ownership.
10. The proposed runner reuses `scripts/tool_search_livetest.py` through stable
    importable helpers and can load an enabled vendored standalone plugin in an
    isolated Hermes home.
11. Registry interception prevents all real GitLab access while preserving
    authentic skill/tool discovery and model selection behavior.
12. The two-family routing gate proves the intended reliability claim rather
    than only testing prompt wording or mocked direct dispatch.
13. Generated migration/onboarding documents have one clear editable authority
    and the plans never stage stale generated output.
14. The source-first and exact-vendor commands work from the proposed isolated
    worktrees and preserve the real `base`/literal-`main` branch contract.
15. No accepted SuperCLI read-only GitLab behavior remains omitted other than
    the explicitly excluded webhook operation and documented non-goals.

## Severity and verdict

- **CRITICAL** — false architecture or source-ownership premise requiring
  redesign, a path to credential/secret disclosure, real unintended writes or
  network calls, or loss of a major existing connector contract.
- **IMPORTANT** — a realistic missing/incorrect step can ship a materially
  wrong read, unsafe/lossy evidence, unreliable routing, false migration claim,
  broken CLI surface, non-executable task, or incomplete source/vendor result.
- **MINOR** — bounded plan clarity, diagnostic, maintainability, or additional
  verification issue with a concrete consequence but no release blocker.

Do not inflate severity. A preference, speculative hardening idea, or missing
test already covered by another credible gate is not a finding.

Verdict:

- `BLOCK` if any Critical or Important finding remains;
- `CONDITIONAL` if only Minor findings remain and one requires an explicit
  product decision; or
- `READY FOR IMPLEMENTATION` only with zero Critical/Important findings and
  complete required matrices.

## Ten-element finding proof standard

Every finding must include:

1. stable ID and severity;
2. concise title;
3. affected specification section, plan task, repository, and surface;
4. exact plan text/omission plus direct source evidence;
5. violated approved invariant or decision;
6. realistic implementation/runtime scenario;
7. concrete wrong result and consequence;
8. why another task/test/framework gate does not cover it;
9. the smallest correction that closes the whole gap without scope creep; and
10. exact RED test, command, or acceptance assertion to add/change.

If proof is incomplete, put the concern under unresolved questions rather than
promoting it to a finding. Do not use another review report as evidence.

## Required report

Produce a self-contained Markdown report containing:

1. reviewer model, reasoning setting when known, date, platform, exact
   repository states, and verified input hashes;
2. overall verdict;
3. severity-sorted finding table and ten-element proof for every finding;
4. specification-to-plan traceability matrix for all three slices;
5. all-task coverage/dependency matrix (CI Tasks 1–10, repository Tasks 1–8,
   release/inbox Tasks 1–8);
6. operation/API/schema/CLI/migration coverage matrix for all twelve
   new/extended behaviors;
7. routing ownership and near-neighbor matrix;
8. routing corpus/live-harness validity assessment;
9. verdict on all sixteen invariants;
10. verdict on all fifteen specific premises;
11. strict-TDD file/symbol/command/ordering audit;
12. verified-complete areas and why;
13. required corrections ordered by severity/dependency;
14. unresolved questions and the exact evidence needed;
15. source-grounding ledger (`VERIFIED`, `MISSING`, `AMBIGUOUS`, or
    `UNCHECKABLE`) for every cited existing path/symbol; and
16. exact command/evidence ledger distinguishing commands actually run from
    commands merely inspected.

If there are no findings, say so explicitly and still provide every matrix and
premise/invariant verdict. End with exactly one of:

- `IMPLEMENTATION MUST NOT BEGIN UNTIL CRITICAL AND IMPORTANT PLAN FINDINGS ARE RESOLVED.`
- `IMPLEMENTATION MAY BEGIN ONLY AFTER THE LISTED PRODUCT DECISIONS ARE APPROVED.`
- `THE REVIEWED SPECIFICATIONS AND PLANS ARE READY FOR IMPLEMENTATION.`

Do not implement corrections. Stop after producing the report.
