# Ericsson GitLab remaining reads — blocker-only adversarial plan rereview

## 1. Review identity and verified inputs

- Reviewer: Codex 5.6, xhigh reasoning, clean independent rereview
- Review date: 2026-08-30
- Scope: the three immutable amended specs, the three immutable amended plans,
  and the reconciliation named by the rereview prompt
- Repositories inspected read-only:
  - Hermes: `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`
  - authoritative source: `/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities`
- External authority: official GitLab API documentation only
- No real GitLab instance was contacted and no credential was read or exposed.
- Independence: no prior review or Claude output was read or searched. Under
  `docs/reviews`, only the rereview prompt and its explicitly named
  reconciliation were read.

All immutable inputs matched before substantive review:

| Input | Expected SHA-256 | Observed SHA-256 |
|---|---|---|
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-ci-read-coverage-design.md` | `485d3dfe95d6712f8aa6f11abf61ccbb1144510fcecf450bf1f7fb89f04b6922` | `485d3dfe95d6712f8aa6f11abf61ccbb1144510fcecf450bf1f7fb89f04b6922` |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-repository-discovery-design.md` | `9ebbd6ac94edc830c3b77500ad1380748b9f068e45c7e218080355cceb48eb7e` | `9ebbd6ac94edc830c3b77500ad1380748b9f068e45c7e218080355cceb48eb7e` |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-release-inbox-design.md` | `ce5722c1d269852e593d4ad48b87f30731306e2a3779da26f79c235c9887f25b` | `ce5722c1d269852e593d4ad48b87f30731306e2a3779da26f79c235c9887f25b` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md` | `74bb2e593ea199795e435d6a49dd94330e09f412190cc66d86fdbf2291b1bbd1` | `74bb2e593ea199795e435d6a49dd94330e09f412190cc66d86fdbf2291b1bbd1` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md` | `25809584df8c1d4a2e00bc7c05e08d9160451d66b1b3950c8730108441acc9f6` | `25809584df8c1d4a2e00bc7c05e08d9160451d66b1b3950c8730108441acc9f6` |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md` | `5ab3dffa61c4d25e2513513cd565b49366c15be86e3d297413aad49ac1c877c6` | `5ab3dffa61c4d25e2513513cd565b49366c15be86e3d297413aad49ac1c877c6` |
| `docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-review-reconciliation.md` | `79a4c8232d5900e0a670e651cdb0d18859f8ea9944fccd88dcd20750144a3094` | `79a4c8232d5900e0a670e651cdb0d18859f8ea9944fccd88dcd20750144a3094` |

## 2. Verdict

**BLOCK**

There are zero Critical findings and five Important findings. Each is an
implementation blocker under the prompt's gate: two listed GREEN commands
cannot pass after the planned changes, one public operation is specified
against response fields the selected official endpoint does not return, the
managed-byte parity command does not instantiate its own prerequisites, and a
documented-invalid MR scope/filter combination is explicitly allowed.

## 3. Critical/Important findings

| ID | Severity | Slice / task | Blocker |
|---|---|---|---|
| RR-I-01 | Important | Release/inbox Task 6, source | The prescribed optional-positional `SUPPRESS` default is immediately removed by the next existing parser branch. |
| RR-I-02 | Important | CI Task 5, source | A second fixed GitLab operation count remains in a required GREEN test and becomes stale as soon as the four CI tools are registered. |
| RR-I-03 | Important | Repository Task 5, source/spec | `GET /search?scope=projects` does not document the required `namespace`, `archived`, or `visibility` response members. |
| RR-I-04 | Important | CI Task 0/9/10 and downstream vendor gates, Hermes | Every parity command omits the source directory and expected SHA that the planned test requires, so the gate must skip or fail rather than prove parity. |
| RR-I-05 | Important | Release/inbox Task 5, source/spec | The plan allows `scope=created_by_me` plus `author=@me` and maps it to `author_id`, but GitLab documents `author_id` only with `scope=all` or `scope=assigned_to_me`. |

## 4. Full blocker proofs

### RR-I-01 — optional positional omission is not implementable as written

1. **Exact task and repository.** Release/inbox plan, source Task 6 Steps 2 and
   5. Step 2 requires parsing `gitlab mr list` with no positional to yield `{}`
   rather than `{"project": None}` at
   [release plan line 645](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md:645).
   Step 5 prescribes setting `nargs="?"` and
   `default=argparse.SUPPRESS` “after repeatable positional handling” at
   [release plan line 687](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md:687).
2. **Source evidence.** In the authoritative source parser,
   `_argument_kwargs` initializes `default=argparse.SUPPRESS` at
   [parser.py line 213](/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/plugins/ericsson-connector-cli/parser.py:213),
   handles repeatable positionals at
   [parser.py line 239](/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/plugins/ericsson-connector-cli/parser.py:239),
   and then unconditionally removes both `dest` and `default` for every
   positional at
   [parser.py line 244](/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/plugins/ericsson-connector-cli/parser.py:244).
   The plan's new block is inserted before that unconditional removal.
3. **Violated invariant.** An absent optional positional must be omitted from
   the parsed argument mapping while a present value must retain the existing
   descriptor-backed schema validation and description.
4. **Failure scenario and impact.** `gitlab mr list` executes without a
   positional. Because `default=SUPPRESS` is popped, `argparse` supplies its
   ordinary optional-positional default (`None`). The connector emits
   `{"project": None}` instead of `{}`. The exact Task 6 parser assertion stays
   red and the source slice cannot reach its required GREEN gate.
5. **Why listed steps do not catch or fix it.** The new test catches it, but the
   only implementation step reintroduces `SUPPRESS` at a location where the
   existing branch immediately deletes it. No later task changes that branch.
6. **Smallest correction.** Amend Task 6 Step 5 so the final positional cleanup
   preserves `default=argparse.SUPPRESS` for optional, nonrepeatable
   positionals. For example, pop the default only for required/repeatable
   positionals, or move the optional override after the cleanup. Require both a
   direct `_argument_kwargs` assertion and an end-to-end no-positional parse.
7. **Runnable RED/GREEN command.** After adding the Step 2 cases, this command
   is RED with the prescribed implementation and must be GREEN after the plan
   correction:

   ```bash
   cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities
   .venv/bin/python -m pytest -q \
     tests/test_connector_cli_gitlab_port.py \
     tests/test_connector_cli_descriptors.py \
     -k 'merge_request or optional_positional'
   ```

### RR-I-02 — a required source test retains a stale fixed operation count

1. **Exact task and repository.** CI plan, source Task 5. Its file list includes
   `tests/test_connector_cli_gitlab_port.py` at
   [CI plan line 772](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md:772).
   Step 1 removes one fixed count from `test_gitlab_plugin.py` and two fixed
   descriptor counts, then asks the port test only for new parser/host cases at
   [CI plan line 790](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md:790).
   Step 5 requires that port test to be GREEN at
   [CI plan line 885](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md:885).
2. **Source evidence.** The port test already has a complete relational
   registration assertion comparing `registration.operations` to every schema
   at
   [test_connector_cli_gitlab_port.py line 141](/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/tests/test_connector_cli_gitlab_port.py:141),
   but it also still asserts `len(registration.operations) == 30` at
   [line 150](/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/tests/test_connector_cli_gitlab_port.py:150).
3. **Violated invariant.** Tests must assert behavior/relationships rather than
   frozen enumeration counts, and every hand-authored count affected by a slice
   must be named in that slice's task/file/commit.
4. **Failure scenario and impact.** Task 5 appends four CI schemas and plugin
   operations. The relational assertion correctly expands to 34 entries, but
   the untouched `== 30` assertion fails. The plan's mandatory Task 5 GREEN,
   full source gate, and handoff gate cannot pass.
5. **Why listed steps do not catch or fix it.** The required test command catches
   the failure. The amendment names and removes sibling frozen counts but never
   instructs the implementer to remove this exact one. Later slices add still
   more operations without repairing it.
6. **Smallest correction.** In CI Task 5 Step 1, explicitly delete line 150.
   The adjacent exact dictionary equality is already the durable full-inventory
   contract, so no replacement count is needed.
7. **Runnable RED/GREEN command.** This is RED after registering the four tools
   unless the missed assertion is removed, and GREEN with the correction:

   ```bash
   cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities
   .venv/bin/python -m pytest -q \
     tests/test_gitlab_plugin.py \
     tests/test_connector_cli_descriptors.py \
     tests/test_connector_cli_gitlab_port.py
   ```

### RR-I-03 — visible-project output requires fields absent from the selected endpoint

1. **Exact task and repository.** Repository-discovery spec requires namespace,
   archived, and visibility facts at
   [repository spec line 126](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/specs/2026-08-30-ericsson-gitlab-repository-discovery-design.md:126).
   Source Task 5's fixture invents `namespace`, `archived`, and `visibility` at
   [repository plan line 462](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md:462),
   directs malformed-field tests for all three at
   [line 492](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md:492),
   and requires the normalizer to return them from exactly
   `/api/v4/search?scope=projects` without per-result resolution at
   [line 501](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md:501).
2. **Source/API evidence.** The target implementation is the new
   `_normalize_project_search_result`/`search_projects` pair in authoritative
   [operations.py](/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/plugins/ericsson-gitlab/operations.py:2306),
   reusing the existing strict `_paginate` normalizer path. GitLab's official
   [Search API, scope `projects`](https://docs.gitlab.com/api/search/#scope-projects)
   example returns `id`, description/name/path identities, timestamps, branch,
   repository URLs, topics, counts, and activity. It does **not** return a
   `namespace` object, `archived`, or `visibility`.
3. **Violated invariant.** Request/response contracts must accept documented
   valid GitLab shapes without weakening malformed-data handling, and output
   must not claim facts unavailable from the chosen endpoint.
4. **Failure scenario and impact.** A GitLab server returns the exact documented
   project-search shape. A strict implementation of Step 3 either rejects the
   first item as `invalid_remote_data` because the three fields are absent, or
   fabricates/omits fields and violates the approved output contract. Therefore
   a normal visible-project search cannot satisfy both the plan and GitLab's
   documented API.
5. **Why listed steps do not catch or fix it.** The fixture includes the
   undocumented members, so all positive tests can pass against a response the
   selected endpoint is not documented to produce. The malformed cases reinforce
   the false requirement. The plan also forbids the only proposed way it could
   fetch those facts while retaining this endpoint: per-result project reads.
6. **Smallest correction.** Amend the spec and Task 5 to use only fields
   documented by Search API. Derive a display namespace from the already
   validated `path_with_namespace` if the product needs it, and remove
   archived/visibility from this operation. Alternatively, deliberately switch
   to the Projects API and re-review its search semantics, pagination, and
   response contract; that is the larger correction. Add the official example
   shape, with all three disputed members absent, as the positive fixture.
7. **Runnable RED/GREEN command.** The new documented-shape test must be RED
   under the current Task 5 contract and GREEN after correction:

   ```bash
   cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities
   .venv/bin/python -m pytest -q \
     tests/test_gitlab_exploration.py \
     -k 'search_projects and documented_shape'
   ```

### RR-I-04 — the managed-byte parity test is never invoked with its prerequisites

1. **Exact task and repository.** CI Task 0 Step 3 defines a deterministic
   Hermes test that compares every managed byte and inventory **when**
   `ERICSSON_CAPABILITIES_DIR` and an expected full SHA are supplied at
   [CI plan line 145](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md:145).
   Yet Task 9 Step 5 runs the test without either prerequisite at
   [CI plan line 1360](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md:1360),
   and the final gate repeats the omission at
   [CI plan line 1423](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md:1423).
   Repository Task 8 Step 3 and release Task 8 Step 4 inherit the same bare
   invocation at
   [repository plan line 766](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md:766)
   and
   [release plan line 921](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md:921).
2. **Source evidence.** Hermes' vendor script derives manifest
   source/destination pairs at
   [vendor-ericsson.mjs line 224](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/scripts/vendor-ericsson.mjs:224),
   deletes stale managed destinations during reconciliation at
   [line 680](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/scripts/vendor-ericsson.mjs:680),
   and stages/publishes the snapshot at
   [line 1200](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/scripts/vendor-ericsson.mjs:1200).
   The manifest records only the SHA string at
   [capabilities/ericsson.json line 104](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/capabilities/ericsson.json:104);
   SHA-string equality alone does not compare current managed bytes to the
   source tree.
3. **Violated invariant.** Each slice must prove exact source ancestry,
   inventory, package digests, and managed-byte equality before integration and
   before the next slice branches.
4. **Failure scenario and impact.** The planned parity test receives neither a
   source directory nor an expected SHA. If it skips without those inputs, all
   listed “parity” gates pass without checking bytes. If it fails closed, Task
   0 and every downstream deterministic gate cannot pass. There is no behavior
   consistent with both the test's stated conditional contract and the listed
   commands that proves parity.
5. **Why listed steps do not catch or fix it.** The vendor command sets
   `ERICSSON_CAPABILITIES_DIR` only for the separate Node process. That
   process-local assignment is not exported to later `scripts/run_tests.sh`
   invocations. The plan does not even assign a name to the expected-SHA input,
   so an implementer cannot infer a reproducible invocation from the text.
6. **Smallest correction.** Define one exact expected-SHA input name in Task 0
   (for example `ERICSSON_CAPABILITIES_EXPECTED_SHA`) and prefix every parity
   invocation with both values. The test must fail rather than skip when it is
   designated as an integration gate and either value is missing.
7. **Runnable acceptance command.** With the proposed name, this is the minimum
   real gate that every slice should list:

   ```bash
   cd "$HERMES_WT"
   ERICSSON_CAPABILITIES_DIR="$SOURCE_WT" \
   ERICSSON_CAPABILITIES_EXPECTED_SHA="$SOURCE_SHA" \
     scripts/run_tests.sh -q tests/hermes_cli/test_ericsson_vendor_parity.py
   ```

### RR-I-05 — matching `created_by_me` plus `author=@me` is mapped to an unsupported query

1. **Exact task and repository.** The release/inbox spec says actor filters may
   be combined only where GitLab supports the combination at
   [release spec line 131](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/specs/2026-08-30-ericsson-gitlab-release-inbox-design.md:131).
   Source Task 5 Step 5 declares a matching native scope plus `@me` valid, then
   requires every `@me` role to be resolved and mapped to an ID parameter at
   [release plan line 579](/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md:579).
2. **Source/API evidence.** This extends the existing authoritative
   `GitLabOperations.list_merge_requests` at
   [operations.py line 1445](/Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities/plugins/ericsson-gitlab/operations.py:1445).
   GitLab's official [List merge requests](https://docs.gitlab.com/api/merge_requests/#list-merge-requests)
   contract says `author_id` must be combined with `scope=all` or
   `scope=assigned_to_me`; it does not support `scope=created_by_me` plus
   `author_id`.
3. **Violated invariant.** Every allowed scope/filter combination must be a
   documented valid GitLab request; contradictory or unsupported combinations
   must fail before transport and native personal scopes must avoid unnecessary
   identity lookup.
4. **Failure scenario and impact.** The valid schema/CLI request
   `scope=created_by_me, author=@me` is accepted. Step 5 calls `/api/v4/user`
   and sends `/api/v4/merge_requests?scope=created_by_me&author_id=7`. That is
   outside the documented combination, so GitLab may reject it even though the
   request is semantically just the native scope. A public supported CLI form
   therefore produces avoidable remote failure.
5. **Why listed steps do not catch or fix it.** The positive native-scope test
   uses no actor. The `@me` test uses the default `scope=all`. Contradiction
   tests cover only a different username. No test exercises the exact matching
   native-scope/`@me` branch that Step 5 explicitly allows.
6. **Smallest correction.** Canonicalize a native personal scope plus its
   matching `@me` actor to the native scope alone: do not call `/user` and do
   not send the redundant actor parameter. Add one case for each matching
   native role, with the author case explicitly asserting no `author_id` under
   `created_by_me`. Continue rejecting a nonmatching username.
7. **Runnable RED/GREEN command.** Add the matching-native-actor test, then run:

   ```bash
   cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities
   .venv/bin/python -m pytest -q \
     tests/test_gitlab_exploration.py \
     -k 'matching_native_scope or contradictory_personal or explicit_me'
   ```

## 5. Previously reported blocker dispositions

The reconciliation was treated as a claim and each consolidated item was
checked against the amended plans and current source. `PARTIAL` and `OPEN`
items below remain blockers where tied to a finding above.

| Consolidated item | Disposition | Independent rereview result |
|---|---|---|
| V-01 — source/vendor drift and safe reconciliation | PARTIAL | Task 0 now requires a complete inventory, ports retained Hermes authority back to source, and protects Jira Defect Loop before vendoring. The claimed managed-byte proof is not actually invoked with its inputs (RR-I-04). |
| F-01 / I-10 — all fixed inventory/count sites | PARTIAL | The plan removes the named plugin/descriptor totals, but misses `tests/test_connector_cli_gitlab_port.py:150` (RR-I-02). |
| F-02 — unsupported `uniqueItems` | RESOLVED | The schema omits it and the operation-level duplicate-status rejection is explicit and tested before transport. |
| F-03 / I-03 — absent optional positional uses `SUPPRESS` | OPEN | The amendment sets `SUPPRESS` before an existing unconditional `pop("default")` (RR-I-01). |
| F-04 / I-01 — documented nullable/optional user shape | RESOLVED | The plans explicitly cover missing optional user fields where documented and the shared normalizer retains required display-safe `state` where its consumed contracts require it. |
| F-05 — To-Do input enums | RESOLVED | The amended action and target-type allowlists match the current official To-Dos API; unknown bounded remote values are treated as data rather than caller filters. |
| F-06 / I-04 — write attempts visible and blocked before dispatch | RESOLVED | The runner extracts attempted underlying names from the transcript, independently denies approval, and intercepts `registry.dispatch` before GitLab handlers. |
| F-07 / I-02 — pipeline extraction and response-shape fidelity | RESOLVED | CI tasks name the shared pipeline normalizer/extractor, documented optional fields, MR pipeline paging, and focused malformed/null cases. |
| F-08 / I-06 — cumulative corpus union | RESOLVED | Each slice updates `read_tools` and `intent_tools` cumulatively and tests the union against qualified skill-owned reads. |
| F-09 — optional project validation/description | RESOLVED | The release plan retains MR listing in `_GITLAB_PROJECT_OPERATIONS` specifically for validation/description while changing only requiredness. RR-I-03 is a new, separate Search API response-shape defect. |
| F-10 — executable source/vendor parity | OPEN | The test contract is specified, but none of the mandatory invocations supplies its conditional source/SHA inputs (RR-I-04). |
| F-11 / I-09 — Hermes Python wrapper | RESOLVED | Hermes pytest invocations consistently use `scripts/run_tests.sh`; direct `python -m pytest` commands are confined to the source repository. |
| F-12 / I-07 — two explicit model families | RESOLVED | The runner requires distinct explicit Anthropic/Claude and OpenAI/GPT namespaces, passes exact IDs into config and `AIAgent`, and asserts the resolved model. |
| F-13 — runnable helper references | RESOLVED | The amended runner imports existing helper behavior, defines its own worktree root, and names the needed backward-compatible harness extension. |
| I-05 — exact complete sequences and intent fulfillment | RESOLVED | Exact ordered sequences, clarification prefixes, wrong-order/incomplete multi-intent rejection, and `required_intents` fulfillment are all required and unit-tested. |
| I-08 — slice integration and ancestry | RESOLVED | The plans require source `main` integration, exact vendoring to Hermes `base`, recorded full SHAs, and ancestor checks before the next slice branches. |

## 6. Cross-plan dependency and integration audit

The intended dependency chain is now explicit and otherwise sound:

```text
Task 0 reconciliation
  -> CI source main / Hermes base exact SHAs
  -> repository source main / Hermes base exact SHAs
  -> release/inbox source main / Hermes base exact SHAs
```

- CI Task 0 makes reconciliation an integration prerequisite rather than a
  file-presence assumption.
- The repository plan consumes the exact CI source/Hermes SHAs and checks
  ancestry before creating its worktrees.
- The release/inbox plan does the same with the repository SHAs.
- Each source slice runs focused plus full source tests, records a clean full
  SHA, and vendors only after source integration. Hermes remains on `base`; the
  literal synchronization-only `main` is not used for development.
- The planned Node vendor command is scoped to manifest-managed Ericsson paths,
  while unrelated user changes are protected by clean-worktree gates.
- Prompt-caching and deferred-tool architecture do not change: one ordinary
  conversation retains the permanent tool array, then progressively opens the
  router, one qualified skill, deferred descriptions, and reads.

The chain is not executable yet. CI Task 5 stops on RR-I-02, release Task 6
stops on RR-I-01, repository Task 5 can pass only against a non-authoritative
synthetic response because of RR-I-03, and every cross-repository byte-equality
checkpoint is non-proving or non-runnable because of RR-I-04. Those defects
must be corrected in the plans before their sequential SHA gates can provide
meaningful ancestry.

## 7. Routing-evaluator soundness audit

Apart from the vendor-parity invocation defect, the amended evaluator design
meets the required routing and safety invariants:

- It uses the existing ordinary Hermes agent loop, not a classifier request or
  model-authored dispatcher.
- It keeps the permanent tool array and system prompt stable; skills and tool
  descriptions are progressively disclosed through existing mechanisms.
- It extracts `skill_view`, `tool_search`, `tool_describe`, wrapper calls, and
  direct assistant calls in order, including the underlying attempted name.
- A write attempt is scored from the transcript even when approval blocks it;
  approval is patched to deny without stdin and `registry.dispatch` is also
  intercepted before any GitLab implementation or network path.
- Allowed reads receive bounded fake JSON. A real GitLab handler is never
  called, the source `.env` is not copied, only the selected provider credential
  key is admitted into the isolated home, and fake GitLab origin/PAT values are
  used.
- The live run requires two distinct, explicit model families and records the
  exact resolved IDs rather than inferring family by substring.
- Corpus `read_tools`, qualified skill ownership, `intent_tools`,
  `required_intents`, and exact ordered sequences remain cumulative across the
  three slices. Unit tests explicitly cover transcript-only writes, incomplete
  project-search and multi-intent prefixes, wrong ordering, unsafe output
  paths, family validation, and the no-network stub.

RR-I-05 is an operation-contract defect rather than an evaluator escape: the
evaluator can route safely to a read that still constructs an unsupported
GitLab query. The evaluator does not compensate for operation-level request
contract errors, so the source test correction remains necessary.

## 8. Non-blocking Minor notes

None. All other observations were either proven resolved or not elevated
without official/source evidence.

## 9. Final implementation gate

**Implementation must not start from these plans.** Amend the plans/spec where
identified to resolve RR-I-01 through RR-I-05, re-hash the new immutable inputs,
and rerun this blocker-only gate. Readiness requires zero Critical/Important
findings and explicit GREEN evidence from the commands above, including a
parity invocation supplied with the exact source directory and full expected
SHA.
