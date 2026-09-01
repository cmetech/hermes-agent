# Blocker-only adversarial rereview — remaining Ericsson GitLab reads (Fable 5)

## 1. Review identity and verified input hashes

- Reviewer: Claude Fable 5, independent blocker-only rereview, read-only inspection.
- Date: 2026-08-30. Hermes checkout on `base` at planning commit `4e8c3d61d2`;
  source `ericsson-capabilities/main` at `0d7654d14db0afe0c688a752a2676d8cabe2f981`
  (clean, equals Hermes `capabilities/ericsson.json` `vendoredFrom`).
- No Codex rereview output was read. No GitLab instance was contacted; API facts
  come from `docs.gitlab.com` only (fetched 2026-08-30).

All seven inputs matched the pinned SHA-256 values before review:

| File | Observed SHA-256 | Match |
| --- | --- | --- |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-ci-read-coverage-design.md` | `485d3dfe…4b6922` | ✅ |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-repository-discovery-design.md` | `9ebbd6ac…48eb7e` | ✅ |
| `docs/superpowers/specs/2026-08-30-ericsson-gitlab-release-inbox-design.md` | `ce5722c1…87f25b` | ✅ |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md` | `74bb2e59…1bbd1` | ✅ |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md` | `25809584…9cf6` | ✅ |
| `docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md` | `5ab3dffa…877c6` | ✅ |
| `docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-review-reconciliation.md` | `79a4c823…3094` | ✅ |

(Full digests are identical to the seven values listed in the rereview prompt.)

## 2. Verdict

**BLOCK.** Zero Critical findings; seven Important findings remain. Every one
is a small, local plan correction — none reopens architecture, scope, safety
policy, or the approved read-only surface. Most previously blocked areas are
genuinely resolved (see §5); the residue is executability defects introduced or
left incomplete by the amendments.

## 3. Critical/Important findings table

| ID | Sev | Plan / task | One-line defect | Check |
| --- | --- | --- | --- | --- |
| R-01 | Important | Release plan Task 6 Step 5 | The one-line `nargs="?"` rule is inserted before `parser.py:244-246`, which pops `default` for every positional, so `argparse.SUPPRESS` is discarded and an omitted `project` becomes `None`, not absent | 4 |
| R-02 | Important | Release plan Task 5 Step 1 | The "must pass before the extension" lock test asserts `filters["scope"] == "all"`, a key the current operation never returns; Step 4 then lists the same test as RED | 9 |
| R-03 | Important | Release plan Task 8 | Hermes `tests/plugins/workflow/test_installed_distribution_e2e.py:186-204` freezes the plugin-skill list by dict equality; not named in Files, gate, or `git add` | 2 |
| C-01 | Important | CI plan Task 7 Steps 1/3/4 | `gitlab_read_pipeline` is in the corpus `read_tools` seed and the decision table but is added to no skill's ownership set, so the two Task 7 static gates contradict each other | 6, 9 |
| C-02 | Important | CI plan Task 7 ↔ Task 9; repo/release Task 7 | `intent_tools` is consumed as a corpus-level key by three later steps but never created or validated where the corpus is authored; source SHA is sealed before the gap surfaces | 6 |
| C-03 | Important | CI plan Task 1 Step 3 | Hermes baseline runs `tests/scripts/test_gitlab_skill_routing_livetest.py`, created only in Task 9; the baseline cannot pass and the plan says to stop on baseline failure | 9 |
| D-01 | Important | Repository plan Tasks 2–3 | `-k branch_listing` / `-k tag_listing` match none of the named tests; RED and GREEN both select zero tests and Step 4 commits behind a vacuous gate | 9 |

## 4. Full proof for each blocker

### R-01 — optional MR positional: `default` is popped after the plan's insertion point

1. **ID/severity:** R-01, Important.
2. **Task/repo:** Release plan Task 6 Step 5 (lines 687-709), `ericsson-capabilities`.
3. **Source evidence:** `plugins/ericsson-connector-cli/parser.py:213-247` —
   `kwargs = {"dest": dest, "default": argparse.SUPPRESS, ...}` (216);
   repeatable-positional handling at 239-241 (`kwargs["nargs"] = "+"`); then,
   after the plan's stated insertion point, lines 244-246:
   `if binding.source == "positional": kwargs.pop("dest"); kwargs.pop("default")`.
   `canonical_arguments` (`parser.py:437-451`) skips a binding only when
   `not hasattr(namespace, dest)`. Descriptor today:
   `descriptors.py:1027` `positionals=(_pos("project", value_type="string_or_integer"),)`;
   `_pos` accepts `required=` (`descriptors.py:78-95`).
   Stdlib check run locally: positional `nargs="?"` with `default=SUPPRESS` →
   attribute absent; with `default` popped → attribute present, value `None`.
4. **Violated requirement:** Check 4 ("omits the field when absent"); release
   plan Task 6 Step 2 ("must produce `{}`, not `{"project": None}`");
   reconciliation F-03/I-03 claims `default=argparse.SUPPRESS` is kept.
5. **Failure scenario:** Implementer inserts the two lines exactly where told
   ("after repeatable positional handling"). Line 246 pops `default`. Running
   `<brand> gitlab mr list` with no positional stores `project=None`;
   `canonical_arguments` sees the attribute, `_validate_contract(None, …)` fails
   (or `project: None` is forwarded). The headline no-project personal queue
   never reaches the global endpoint from the CLI.
6. **Why not caught/fixed:** The plan's own test does go RED and stays RED, but
   the plan prescribes a fix that cannot make it GREEN; the implementer must
   improvise in a shared parser with no guidance about the pop.
7. **Smallest correction:** Move the rule after the pop and stop popping
   `default` for optional positionals — replace lines 244-246 with:
   `if binding.source == "positional": kwargs.pop("dest");
   if binding.required or binding.repeatable: kwargs.pop("default")
   else: kwargs["nargs"] = "?"` (default already `SUPPRESS` from line 216).
   Update Task 6 Step 5 text accordingly; keep the parser unit assertion.
8. **RED/GREEN:** `"$SOURCE_PY" -m pytest -q tests/test_connector_cli_gitlab_port.py -k 'mr and list'`
   — RED before (`{"project": None}`/contract error), GREEN after; plus
   `-k 'positional and nargs'` for the parser unit assertion.

### R-02 — backward-compat lock test cannot pass on current source

1. **ID/severity:** R-02, Important.
2. **Task/repo:** Release plan Task 5 Step 1 (lines 457-481; assertion at 478)
   and Step 4 (line 549), `ericsson-capabilities`.
3. **Source evidence:** `plugins/ericsson-gitlab/operations.py:1445-1548` —
   `list_merge_requests` returns `"filters": {state, source_branch,
   target_branch, search, order_by, sort, created_after, updated_after,
   lookback_hours}` (1532-1542); no `scope` key. `scope="all"` is only a
   request param (1499).
4. **Violated requirement:** Check 9 (steps must be executable as written);
   the plan says "Run this test now; it must pass before the extension."
5. **Failure scenario:** `result["filters"]["scope"]` raises `KeyError` on
   unmodified `main`; the mandated precondition fails; Step 4 then includes
   `backward_compatible` in the RED set, so the plan asserts both "must pass
   now" and "expect RED" for the same test.
6. **Why not caught/fixed:** No step reconciles the contradiction; an agentic
   worker following "must pass" halts or edits the test ad hoc.
7. **Smallest correction:** Remove the `filters["scope"]` line from the Step 1
   lock test (keep `route.called`, `project.id == 42`, `state == "opened"`),
   and assert `filters["scope"] == "all"` only in Step 2's post-extension tests.
   Drop `backward_compatible` from the Step 4 RED `-k` expression.
8. **RED/GREEN:** `"$SOURCE_PY" -m pytest -q tests/test_gitlab_exploration.py -k backward_compatible`
   must PASS on current `main` before Task 5 Step 5 and after it.

### R-03 — unnamed frozen Hermes inventory: installed-distribution e2e

1. **ID/severity:** R-03, Important.
2. **Task/repo:** Release plan Task 8 (Files 870-873, Step 4 923-933, Step 6
   954-964), `hermes-agent`.
3. **Source evidence:** `tests/plugins/workflow/test_installed_distribution_e2e.py:170-204`
   — `test_installed_distribution_contains_complete_gitlab_connector` asserts
   a dict equal to `{"plugin_skills": ["ci-investigation", "gitlab-activity-digest",
   "merge-request-review", "repository-research"], …}` built from
   `plugin.glob("skills/*/SKILL.md")`. The release slice adds
   `release-research` and `personal-inbox` skill directories (Task 6).
4. **Violated requirement:** Check 2 (every changed inventory named in the
   correct task/file/commit); reconciliation F-01/I-10 "all three plans now
   enumerate and update those authorities".
5. **Failure scenario:** Vendor snapshot lands on `base`; the plan's Step 4 gate
   (distribution, surfaces, toolsets, cron, routing, parity) is green; the full
   suite / CI then fails on the e2e dict equality with no plan step to fix it.
6. **Why not caught/fixed:** The file is absent from the Files list, the Step 4
   gate, the Step 6 `git add`, and the reconciliation's inventory enumeration.
7. **Smallest correction:** Add
   `tests/plugins/workflow/test_installed_distribution_e2e.py` to Task 8 Files
   (extend the `plugin_skills` list to six names), to the Step 4 gate, and to
   Step 6 `git add`.
8. **RED/GREEN:** `scripts/run_tests.sh -q tests/plugins/workflow/test_installed_distribution_e2e.py -k gitlab_connector`
   — RED after vendoring, GREEN after the list update.

### C-01 — `gitlab_read_pipeline` has no owner but is in the corpus seed and decision table

1. **ID/severity:** C-01, Important.
2. **Task/repo:** CI plan Task 7 Step 1 (lines 1037-1039), Step 3 (1100-1122,
   entry at 1115), Step 4 (1165-1168), `ericsson-capabilities`.
3. **Source evidence:** `tests/test_gitlab_skills.py:17-69` — current
   `PLUGIN_SKILLS` reads: repository-research (8), merge-request-review (9),
   gitlab-activity-digest (3), ci-investigation (4: resolve_project, read_file,
   list_pipelines, inspect_ci). `gitlab_read_pipeline` appears in none.
   `test_plugin_skills_declare_only_their_exact_tool_contract`
   (`test_gitlab_skills.py:127-134`) requires declared `<tool mode="read">`
   == `PLUGIN_SKILLS[skill]["read"]`. Spec decision table row "Inspect one
   pipeline → gitlab_read_pipeline" (CI spec line 208).
4. **Violated requirement:** Checks 6 and 9 ("exact skill-inventory changes in
   Hermes/source"); the plan's own static test
   `assert set(corpus["read_tools"]) == registered_reads` (line 1062).
5. **Failure scenario:** Step 1 extends ci-investigation with "all four new
   names plus existing `gitlab_job_log`" → union = 20 names. Step 3 seeds 21
   names including `gitlab_read_pipeline` → `read_tools` equality fails. If the
   implementer instead drops it from the corpus, Step 4's decision table makes
   the SKILL.md declare it → exact-contract test fails. No GREEN path exists as
   written.
6. **Why not caught/fixed:** Both gates are in Task 7 Step 5 but the plan gives
   contradictory inputs; the reconciliation (F-08/I-06) only addressed the
   union rule, not this omission.
7. **Smallest correction:** In Task 7 Step 1, extend
   `PLUGIN_SKILLS["ci-investigation"]["read"]` with the four new names plus
   `gitlab_job_log` **and `gitlab_read_pipeline`**; state that
   `gitlab_read_pipeline` gains its first owner here.
8. **RED/GREEN:** `"$SOURCE_PY" -m pytest -q tests/test_gitlab_skills.py -k 'routing_cases or exact_tool_contract'`.

### C-02 — `intent_tools` consumed by three later steps, never created

1. **ID/severity:** C-02, Important.
2. **Task/repo:** CI plan Task 7 Step 3 (corpus shape 1097-1153) and Step 1
   static test (1045-1077) vs Task 9 Step 3 (1339-1341); repository plan Task 7
   Step 1 (643) and Step 3 (657); release plan Task 7 Step 3 (804).
3. **Source evidence:** `plugins/ericsson-gitlab/routing_cases.json` does not
   exist yet; the CI plan is its only authoring step, and its shape has exactly
   `version`, `read_tools`, `cases`. Later plans say "Append … to corpus
   `read_tools` and its `intent_tools` mapping" and "Keep the corpus-level
   `intent_tools` mapping complete." CI Global Constraints forbid hand-editing
   vendored Ericsson paths in Hermes; Task 8 seals the source SHA before Task 9.
4. **Violated requirement:** Check 6 (`intent_tools` … stay cumulative across
   all slices); reconciliation I-05 ("fulfill every required intent through
   `intent_tools`").
5. **Failure scenario:** Implementer builds the corpus per Task 7, commits, runs
   Task 8, vendors in Task 9 Step 1, then at Task 9 Step 3 needs
   `CORPUS_DATA["intent_tools"]`. Options are all bad: hand-edit the vendored
   file (forbidden), respin source and re-vendor (unplanned), or hardcode the
   map in the runner — after which the repository/release plans "append" to a
   corpus key that does not exist and the runner's map silently stops covering
   `search_projects`, `list_todos`, etc. (either every completed route fails
   for the new slice, or the intent check is vacuous).
6. **Why not caught/fixed:** Task 7's static test does not assert the key or
   that every `required_intents` value is mapped to a subset of `read_tools`.
7. **Smallest correction:** Add `"intent_tools": {"list_pipeline_jobs":
   ["gitlab_list_pipeline_jobs"], "inspect_job": ["gitlab_read_job",
   "gitlab_job_log"], …}` to the Task 7 shape; add to the static test
   `assert {i for c in cases for i in c["required_intents"]} <= set(corpus["intent_tools"])`
   and `all(set(v) <= set(corpus["read_tools"]) for v in corpus["intent_tools"].values())`;
   change Task 9 Step 3 to "read the corpus `intent_tools` map".
8. **RED/GREEN:** `"$SOURCE_PY" -m pytest -q tests/test_gitlab_skills.py -k routing_cases`
   (RED until the key exists), then `scripts/run_tests.sh -q tests/scripts/test_gitlab_skill_routing_livetest.py -k intent`.

### C-03 — CI Task 1 Hermes baseline names a file that only Task 9 creates

1. **ID/severity:** C-03, Important.
2. **Task/repo:** CI plan Task 1 Step 3 (lines 233-238), `hermes-agent`.
3. **Source evidence:** `tests/scripts/test_gitlab_skill_routing_livetest.py`
   is absent from Hermes `base` (verified with `test -e`); CI plan Task 9
   creates it (line 1242) and first runs it at Task 9 Step 4 (1355).
   `tests/hermes_cli/test_ericsson_vendor_parity.py` is also absent today but is
   created by Task 0 Step 3, so it is legitimately present at Task 1.
4. **Violated requirement:** Check 9; the step's own expectation "both commands
   pass before new tests are written … Stop and resolve".
5. **Failure scenario:** `scripts/run_tests.sh -q tests/scripts/test_gitlab_skill_routing_livetest.py …`
   errors on the missing path; the worker stops per plan text or silently
   edits the gate.
6. **Why not caught/fixed:** No later step corrects Task 1; the amendment added
   the path to satisfy F-11 (use `run_tests.sh`) without checking existence.
7. **Smallest correction:** Delete
   `tests/scripts/test_gitlab_skill_routing_livetest.py` from Task 1 Step 3 (it
   is exercised in Task 9 Steps 4-5 and Task 10).
8. **Acceptance:** `scripts/run_tests.sh -q tests/hermes_cli/test_ericsson_vendor_parity.py tests/hermes_cli/test_ericsson_connector_distribution.py tests/hermes_cli/test_ericsson_connector_surfaces.py`
   passes on the Task 1 Hermes worktree.

### D-01 — repository plan RED/GREEN `-k` expressions select zero tests

1. **ID/severity:** D-01, Important.
2. **Task/repo:** Repository plan Task 2 Steps 2/4 (lines 229, 269:
   `-k branch_listing`) and Task 3 Steps 2/4 (330, 346: `-k tag_listing`),
   `ericsson-capabilities`.
3. **Source evidence:** The only named tests are
   `test_list_branches_returns_flags_commit_identity_and_continuation` (185)
   and `test_list_tags_orders_and_omits_expanded_release_fields` (294); neither
   name, module (`test_gitlab_exploration`), nor any marker contains
   `branch_listing`/`tag_listing`. pytest `-k` with no match deselects
   everything and exits 5 ("no tests ran").
4. **Violated requirement:** Check 9 (steps must actually go RED and GREEN).
5. **Failure scenario:** Step 2 "RED" prints "no tests ran" (misread as a
   failure); Step 4 "GREEN" also runs zero tests and the very next command is
   `git commit` — untested branch/tag code is committed behind a vacuous gate.
   The other plans' `-k` expressions (`read_job`, `pipeline_jobs`,
   `list_ci_variables`, `search_code`, `search_projects`, `list_releases`,
   `read_release`, `list_todos`, MR set) do match their tests.
6. **Why not caught/fixed:** Task 8's full-suite gate would eventually run the
   tests, but the per-task TDD evidence the plan promises never exists.
7. **Smallest correction:** Use `-k list_branches` and `-k list_tags` (or the
   exact test names) in Tasks 2 and 3.
8. **Acceptance:** `"$SOURCE_PY" -m pytest -q tests/test_gitlab_exploration.py -k list_branches --collect-only`
   lists ≥1 test; the same with `-k branch_listing` lists none today.

## 5. Previously reported blocker disposition

| Consolidated item | Disposition | Evidence |
| --- | --- | --- |
| V-01 source/vendor divergence | **RESOLVED** (Task 0) | Premise verified: Hermes-only `capabilities/workflows/jira-defect-loop.{yml,hermes.yaml}` and `workflow-packages/ericsson/workflows/jira-defect-loop.{yaml,hermes.yaml}`; `digests.json`, `skills/ericsson/jira-to-gitlab/SKILL.md`, onboarding `catalog.json`, `jira-defect-loop.md`, `plugins/outlook-mcp/outlook-cli.ps1` differ; `scripts/vendor-ericsson.mjs:784-798` marks previous-not-current paths `publish:false` and `publishTransaction` (1103-1172) removes them; `compatibilityManifestOverlay` (643-678) protects the hand-authored `confluence-research`. Task 0 reconciles into source first, forbids deletion/reversion, and gates Task 1 on SHAs. |
| F-01 / I-10 fixed inventories | **PARTIAL** | Source inventories are named (`test_gitlab_plugin.py:122`, `test_connector_cli_descriptors.py:167-176`, `test_connector_cli_docs.py`, `test_onboarding_catalog.py`, `test_connector_cli_migration.py` prefixes/placeholders, prose in `docs/README.md:31`, `docs/configuration.md:259`, `gitlab-tools.md:82-83,141`). Missed: Hermes `test_installed_distribution_e2e.py:196-202` (→ R-03). Unnamed but gate-covered: `test_connector_cli_gitlab_port.py:150 == 30` (Minor). |
| F-02 `uniqueItems` | **RESOLVED** | CI Task 5 Step 3 removes it; duplicate statuses rejected as `invalid_input` pre-transport in Task 3. |
| F-03 / I-03 optional positional | **OPEN** (→ R-01) | Amendment's `default=argparse.SUPPRESS` is popped by existing `parser.py:244-246`. |
| F-04 / I-01 user projection | **RESOLVED** | `_normalize_user` (`operations.py:1148-1163`) requires `state`; fixtures include it; job `user=None` handled; GitLab docs show job `user` may be null. |
| F-05 To-Do contracts | **RESOLVED** | Caller `action`/`type` allowlists equal the documented sets verbatim; remote values bounded, project/group optional, Commit target SHA. |
| F-06 / I-04 approval before dispatch | **RESOLVED** | Seam `hermes_cli.plugins.resolve_pre_tool_admission` (7414) → `tools.approval.request_tool_approval` (4063) runs in `model_tools.py:1466-1493` before `registry.dispatch` (1534); bridge unwraps `tool_call` to the underlying name before hooks (`model_tools.py:1339-1367`); blocked result is recorded under the underlying name (`agent/tool_dispatch_helpers.py:621-627`). Plan patches approval to record-and-deny and scores transcript names. |
| F-07 / I-02 pipeline extraction | **RESOLVED** | Characterization first, `web_url: None` preserved, tightening documented (CI Task 3). |
| F-08 / I-06 corpus cumulative | **PARTIAL** (→ C-01, C-02) | Union rule adopted; `gitlab_read_pipeline` ownership omitted; `intent_tools` never created. |
| F-09 `_GITLAB_PROJECT_OPERATIONS` | **RESOLVED** | Table only feeds `_VALIDATION` (`descriptors.py:246-249`); requiredness is per-binding `_pos(required=)`. |
| F-10 vendor parity test | **RESOLVED** | Manifest pair derivation mirrors `sourceDestinationPairs` (`vendor-ericsson.mjs:193-246`); exclusions match line 702. (Minor: handle overlay skill + `plugins/workflow` skip.) |
| F-11 / I-09 `run_tests.sh` | **RESOLVED** | All Hermes pytest invocations use `scripts/run_tests.sh` (exists; forwards paths and flags). Source uses its venv pytest. |
| F-12 / I-07 model families | **RESOLVED** | Distinct `--claude-model`/`--openai-model`, provider namespaces (`anthropic/…`, `openai/…` via openrouter), exact model passed to config and `AIAgent` (current harness hardcodes `anthropic/claude-haiku-4.5` at `tool_search_livetest.py:392`, which the plan overrides). |
| F-13 shared helpers | **RESOLVED** | `PROJECT_API`/`_mock_project` defined once in repository Task 2; release Task 2 reuses. |
| I-05 complete routes | **PARTIAL** (→ C-02) | Ordered-tuple equality and prefix+question semantics specified in `is_safe`; `intent_tools` key missing from the corpus. |
| I-08 integration ancestry | **RESOLVED** | CI Task 0 Step 4 / Task 10 Step 4; repo Task 1 / Task 8 Step 7; release Task 1 / Task 8 Step 8 all require `merge-base --is-ancestor` on recorded full SHAs. |

## 6. Cross-plan dependency and integration audit

- **Order and SHA ancestry:** CI → repository → release is enforced by explicit
  `CI_*`, `REPOSITORY_*` SHA variables and `merge-base --is-ancestor` gates; file
  presence is not used as the checkpoint. ✅
- **Source-first / Hermes `base` only:** every shared edit starts in a source
  worktree; Hermes receives `vendor-ericsson.mjs` output from a clean worktree
  with `vendoredFrom` equality; literal `main` and brand branches untouched. ✅
- **Task 0 reconciliation:** premise verified (see V-01). Parity test is
  implementable from `sets/ericsson.json`; note the Hermes vendored manifest
  carries `skills/ericsson/confluence-research` via compatibility overlay (not
  in source, not in the ledger) — the parity test must key on the ledger
  (`capabilities/ericsson-vendored-paths.json`, 27 entries) rather than manifest
  `skills` equality. No workflow-package digest *builder* exists in either repo;
  the check is `tests/test_manifest.py::test_workflow_package_is_complete_and_digest_bound`.
- **Shared corpus/harness:** `routing_cases.json` and
  `scripts/gitlab_skill_routing_livetest.py` are created once (CI) and only
  extended later; `--slice repository|release-inbox` reuse is consistent.
  Gaps: C-01, C-02.
- **Shared test helpers:** `test_gitlab_ci.py` already has `ORIGIN`,
  `PROJECT_API`, `_operations(**client_options)` (supports `max_pages`),
  `_mock_project` ✅; `test_gitlab_exploration.py` has `_operations`, `_project`,
  `_merge_request` ✅ and gains `PROJECT_API`/`_mock_project` in repository
  Task 2 ✅. Fixture refs `refs/merge-requests/7/head` pass
  `_git_ref_is_valid` (`operations.py:140-159`) ✅.
- **Hermes inventories touched by later slices:** surfaces/distribution named
  (release Task 8) ✅; `test_installed_distribution_e2e.py` missed (R-03).
  `test_ericsson_connector_surfaces.py:734` filters by its own `TOOL_NAMES`, so
  new tools do not break it ✅.
- **Prompt caching / deferred tools:** no classifier request, no tool-array
  swap, no system-prompt mutation, no new core tool; runner patches only
  `registry.dispatch` and the approval request. ✅ (Check 10.)

## 7. Routing-evaluator soundness audit

- **Writes blocked before dispatch, still visible:** confirmed seam and
  ordering (§5 F-06). Because the bridge unwraps `tool_call` before hooks, the
  attempted underlying name is present both in the assistant `tool_call`
  arguments and in the blocked tool-result message; the plan's transcript-level
  scoring and `record-and-deny` approval patch are sound. ✅
- **No real GitLab I/O:** dispatch stub returns bounded generic JSON for
  allowlisted reads and a denial otherwise; fake origin/PAT only. ✅
- **No source `.env` copy:** current `setup_isolated_home` copies
  `~/.hermes/.env` wholesale (`tool_search_livetest.py:266-270`) and
  `~/.hermes/auth.json` (263-264); the plan's `credential_keys` extension
  removes the `.env` copy. `auth.json` copying is not addressed (Minor). ✅ for
  the checked requirement.
- **Two explicit families:** required distinct flags, namespace check, exact
  model to config and `AIAgent`, resolved model recorded. ✅
- **Ordered / multi-intent / clarification:** `is_safe` compares an ordered
  tuple to allowed sequences; a prefix passes only with `clarification_allowed`
  and a trailing question; wrong order and incomplete non-asking routes fail;
  ambiguous cases ×3 per model. Intent coverage depends on `intent_tools`
  (C-02). Clarification detection is a trailing-`?` heuristic (Minor).
- **Case validity:** static test proves skills/tools exist and no write is
  permitted; corpus IDs unique; repetitions bound to `ambiguous`. ✅ subject to
  C-01.

## 8. Non-blocking Minor notes

1. CI Task 3 job-status allowlist omits documented `waiting_for_callback`
   (Jobs API status values); harmless, but list it or state the exclusion.
2. CI Task 0 Step 2 "workflow-package digest builder/checker": no builder
   exists; name `tests/test_manifest.py::test_workflow_package_is_complete_and_digest_bound`
   and say digests are hand-computed (sha256).
3. Parity test guidance: skip `plugins/workflow` and treat overlay skills
   (`confluence-research`) as Hermes-owned, per `vendor-ericsson.mjs:198,643-678`.
4. Unnamed but gate-covered fixed counts: `test_connector_cli_gitlab_port.py:150
   (== 30)`, `test_connector_cli_docs.py:33` handbook string — add to CI Task 5/6
   text for completeness.
5. `docs/README.md:86-89` and `docs/configuration.md:265-271` hand-list the
   qualified GitLab skills; release Task 7 should name them (truthfulness only).
6. New `release-research`/`personal-inbox` SKILL.md bodies must contain the
   literals `read-only`, `bounded`, and `warning`/`truncat`
   (`test_gitlab_skills.py:135-141`) — say so in release Task 6 Step 4.
7. `setup_isolated_home` also copies `~/.hermes/auth.json`; not needed for an
   API-key provider — skip it or document why it is retained.
8. CI Task 4 says "seven approved metadata fields" but projects eight keys.
9. `_release_payload["_links"]["self"]` uses an API URL; docs show the release
   web page URL (`/-/releases/<tag>`); both same-origin, but mirror the docs.
10. A sibling checkout `hermes-agent-windows-secret-storage` carries the same
    frozen sets; out of scope but will diverge.

API facts checked against docs.gitlab.com and found consistent with the plans:
MR `scope` includes `reviews_for_me`; `assignee_username[]` is a string array,
`author_username`/`reviewer_username` strings, `*_id` integers; responses carry
`project_id` and `references.full`; pipeline-jobs `scope[]`/`include_retried`;
job `pipeline`/`commit` lack `web_url`, `user` nullable; To-Do `action`/`type`
sets; releases `order_by released_at|created_at`, `_links.self`,
`assets.count/sources/links`; tags have no `web_url`; blobs return
`basename,data,path,filename,id,ref,startline,project_id`; project variables
include `value` in list responses (correctly discarded by the plan).

## 9. Final implementation gate statement

Not converged. Implementation may begin only after R-01, R-02, R-03, C-01,
C-02, C-03, and D-01 are corrected in the three plans and a further
blocker-only rereview returns zero Critical/Important findings. The approved
architecture, scope, safety invariants, source-first vendoring boundary, and
routing-evaluator design are otherwise sound and unchanged.
