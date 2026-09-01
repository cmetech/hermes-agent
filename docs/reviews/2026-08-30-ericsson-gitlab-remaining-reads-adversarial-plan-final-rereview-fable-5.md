# Final blocker-only rereview — remaining Ericsson GitLab reads (Claude Fable 5)

## 1. Identity and verified hashes

- Reviewer: Claude Fable 5 (`claude-fable-5`), independent final blocker-only
  rereview per
  `docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-final-rereview-prompt.md`.
- Date: 2026-08-30.
- Hermes checkout: `base` @ `4e8c3d61d2a283ab4c812ec9fe0f296f7b6c2944`;
  `capabilities/ericsson.json` `vendoredFrom` =
  `0d7654d14db0afe0c688a752a2676d8cabe2f981`.
- Source checkout: `ericsson-capabilities` `main` @
  `0d7654d14db0afe0c688a752a2676d8cabe2f981` (clean, `## main...origin/main`).
- Inputs read in full; every SHA-256 matched (`shasum -a 256`):

```text
485d3dfe95d6712f8aa6f11abf61ccbb1144510fcecf450bf1f7fb89f04b6922  docs/superpowers/specs/2026-08-30-ericsson-gitlab-ci-read-coverage-design.md
43a7463614f07c6609582d95eaf6875e345e248fa96c0fa0d101d70e97b0a085  docs/superpowers/specs/2026-08-30-ericsson-gitlab-repository-discovery-design.md
3a158cb273cca71ea6a6da3add96b98da946d86a86bb9764d79e983acb01858f  docs/superpowers/specs/2026-08-30-ericsson-gitlab-release-inbox-design.md
5f4257377e7ee79a992b962d76654bf6acf76e0aaa7c5149f1315ac9ef572ed6  docs/superpowers/plans/2026-08-30-ericsson-gitlab-ci-read-coverage.md
7e2f5a10075b19ea65214e3d4e2e620adfec2e92b5465759d9dcc18c35535bac  docs/superpowers/plans/2026-08-30-ericsson-gitlab-repository-discovery.md
cb3ba13d29c8d0bcb23c2dfcee21809090bcffeee1730589e35e572e3530cc37  docs/superpowers/plans/2026-08-30-ericsson-gitlab-release-inbox.md
ea6a54d16496c5ea969ff314e2c81c72b13277d8cb0e5789a405353211f486c5  docs/reviews/2026-08-30-ericsson-gitlab-remaining-reads-adversarial-plan-review-reconciliation.md
```

- Independence note: no Codex or prior-review report was opened. One
  repo-wide `grep` for the parity env-var names incidentally listed matching
  lines from files under `docs/reviews/`; those lines were not used and none
  of the findings below rely on them. All findings are grounded in the source
  trees, `scripts/run_tests.sh`, and official GitLab API docs
  (`docs.gitlab.com/api/{todos,merge_requests,search}/`).
- Reconciliation was treated as a claim; every disposition was re-verified
  against source.

## 2. Verdict

**BLOCK.**

Two new blockers introduced by the corrections (one Critical, one Important).
All ten prior blockers are otherwise resolved on the evidence; prior item 7
is downgraded to PARTIAL because of NEW-01.

## 3. Critical/Important table and full proofs

| ID | Severity | Plan / task | Summary |
| --- | --- | --- | --- |
| NEW-01 | Critical | CI plan Task 0 §3, Task 1 §3, Task 9 §5, Task 10 §1; repository plan Task 1 §2, Task 8 §3; release plan Task 1 §2, Task 8 §4 | `scripts/run_tests.sh` execs under `env -i` with an explicit allowlist that does not include `ERICSSON_CAPABILITIES_DIR` / `ERICSSON_CAPABILITIES_EXPECTED_SHA`; the fail-closed parity test therefore never receives its inputs and every one of the eight parity invocations fails unconditionally. |
| NEW-02 | Important | CI plan Task 3 (§3 impl, §4 gate, §5 commit) and Task 8 §1 | The shared `_normalize_pipeline_summary` tightening (40-hex `_commit_sha`) breaks the pre-existing valid-case test `tests/test_gitlab_reads.py::test_list_pipelines_is_bounded_paginated_and_normalized_for_public_task8_tool` (fixture `"sha": "abc"`/`"def"`); that file is not in Task 3's file list, gate, or commit, so Task 3 commits a red source tree and no task owns the fix. |

### NEW-01 — parity inputs are stripped by the mandatory Hermes test runner

- **Repository / tasks:** Hermes. CI plan Task 0 Step 3 (lines 148–170),
  Task 1 Step 3 (249–251), Task 9 Step 5 (1410–1412), Task 10 Step 1
  (1485–1487); repository plan Task 1 Step 2 (146–148), Task 8 Step 3
  (780–782); release plan Task 1 Step 2 (154–156), Task 8 Step 4 (954–956).
- **Source evidence:**
  - `scripts/run_tests.sh:299-316` — `exec env -i PATH= HOME= ${WIN_ENV}
    ${TEST_ENV} TZ LANG LC_ALL PYTHONUTF8 PYTHONHASHSEED PYTHONPYCACHEPREFIX
    HERMES_TEST_WORKERS HERMES_TEST_FILE_RETRIES HERMES_RUN_SLOW_PET_TESTS
    HERMES_E2E_BROWSER PYTHONPATH PYTEST_PLUGINS … run_tests_parallel.py`.
  - `scripts/run_tests.sh:144-150` — the only extensible allowlist
    (`TEST_ENV`) forwards exactly `HERMES_TEST_IMAGE HERMES_TEST_WORKERS
    HERMES_TEST_PATHS HERMES_TEST_FILE_TIMEOUT HERMES_TEST_FILE_RETRIES
    HERMES_TEST_SLICE`; comment at 142–143: "Keep this an explicit allowlist
    (no HERMES_TEST_* glob)".
  - `scripts/run_tests_parallel.py:717-723` — per-file pytest subprocess is
    spawned with `env=os.environ`, i.e. the already-stripped environment.
  - `grep -nE 'ERICSSON_CAPABILITIES' scripts/run_tests.sh
    scripts/run_tests_parallel.py` → no match.
  - Runtime reproduction (mirrors the exec line; no writes):
    `ERICSSON_CAPABILITIES_DIR=/tmp/probe
    ERICSSON_CAPABILITIES_EXPECTED_SHA=<40 zeros> env -i PATH="$PATH"
    HOME="$HOME" TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONHASHSEED=0
    .venv/bin/python -c 'import os; print(sorted(k for k in os.environ if
    k.startswith("ERICSSON_")))'` → `[]`.
  - The repo's existing env-driven source resolver
    (`tests/ericsson_connector_source.py:15-16,115-127`) reads
    `ERICSSON_CAPABILITIES_DIR` / `ERICSSON_CAPABILITIES_TEST_EXPECTED_SHA`
    and is documented as a "pre-vendor" path — it only ever worked under a
    direct `pytest` invocation, which the plans now forbid for Hermes.
- **Violated invariant:** CI plan Task 0 Step 3 — the parity test "requires
  `ERICSSON_CAPABILITIES_DIR` and `ERICSSON_CAPABILITIES_EXPECTED_SHA` … It
  fails, never skips, when either input is absent"; global constraint that
  Hermes Python tests run only through `scripts/run_tests.sh`.
- **Concrete failure:** `ERICSSON_CAPABILITIES_DIR="$SOURCE_WT"
  ERICSSON_CAPABILITIES_EXPECTED_SHA="$SOURCE_SHA" scripts/run_tests.sh -q
  tests/hermes_cli/test_ericsson_vendor_parity.py` — inside pytest both
  variables are unset → the test fails closed on every run, on every branch,
  regardless of whether the vendor snapshot is correct. Task 0 Step 3, the
  Task 1 baselines of all three plans, and the Task 8/9/10 gates all halt at
  this line. The two "fixes" an implementer will reach for — make the test
  skip when inputs are absent, or run it with direct `pytest` — each violate
  a mandatory regression check (7 or 9).
- **Why current steps do not fix it:** the corrections added (a) the
  fail-closed parity test and (b) the `scripts/run_tests.sh` requirement, but
  no task touches the runner's allowlist, and the prompt-side check 7 only
  verified that the *command line* supplies both values.
- **Smallest correction (CI plan Task 0 Step 3, before its first parity
  run):** add `scripts/run_tests.sh` to the modified files and extend the
  explicit `_test_var` allowlist at `scripts/run_tests.sh:145-146` with
  `ERICSSON_CAPABILITIES_DIR` and the expected-SHA variable (recommend reusing
  the already-defined name `ERICSSON_CAPABILITIES_TEST_EXPECTED_SHA` from
  `tests/ericsson_connector_source.py:16` so one contract exists; if
  `ERICSSON_CAPABILITIES_EXPECTED_SHA` is kept, forward that). Keep the
  allowlist explicit (no glob). Add one deterministic guard in
  `tests/hermes_cli/test_ericsson_vendor_parity.py` (or a sibling) that
  reads `scripts/run_tests.sh` and asserts both names appear in the
  `_test_var` list, so a runner refactor cannot silently re-strip them. Add
  `scripts/run_tests.sh` to the Task 0 Step 3 `git add`. Update the seven
  later invocations only if the SHA variable is renamed.
- **RED (runnable now):**

```bash
cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent
grep -nE 'ERICSSON_CAPABILITIES' scripts/run_tests.sh scripts/run_tests_parallel.py; echo "grep exit=$?"   # expect: no output, exit=1
ERICSSON_CAPABILITIES_DIR=/tmp/probe ERICSSON_CAPABILITIES_EXPECTED_SHA=$(printf '0%.0s' $(seq 40)) \
env -i PATH="$PATH" HOME="$HOME" TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONHASHSEED=0 \
  .venv/bin/python -c 'import os; print(sorted(k for k in os.environ if k.startswith("ERICSSON_")))'   # expect: []
```

- **GREEN (after the correction, in the Task 0 Hermes worktree):**

```bash
grep -cE 'ERICSSON_CAPABILITIES_(DIR|(TEST_)?EXPECTED_SHA)' scripts/run_tests.sh   # expect: >= 2
ERICSSON_CAPABILITIES_DIR="$RECONCILED_SOURCE_WT" ERICSSON_CAPABILITIES_EXPECTED_SHA="$RECONCILED_SOURCE_SHA" \
  scripts/run_tests.sh -q tests/hermes_cli/test_ericsson_vendor_parity.py          # expect: PASS
ERICSSON_CAPABILITIES_DIR="$RECONCILED_SOURCE_WT" ERICSSON_CAPABILITIES_EXPECTED_SHA=$(printf '0%.0s' $(seq 40)) \
  scripts/run_tests.sh -q tests/hermes_cli/test_ericsson_vendor_parity.py          # expect: FAIL with a revision-mismatch message (fail-closed now exercised, not tripped by absence)
```

### NEW-02 — Task 3's shared pipeline tightening breaks an existing valid-case test that no task owns

- **Repository / tasks:** `ericsson-capabilities`. CI plan Task 3 Step 3
  (`_normalize_pipeline_summary`, lines 600–631), Step 4 gate (659–662), Step
  5 commit (666–669); Task 8 Step 1 (1239–1246).
- **Source evidence:**
  - Current `list_pipelines.normalize`
    (`plugins/ericsson-gitlab/operations.py:2598-2623`) accepts any bounded
    string for `ref`, `sha`, `status`, `source`, `created_at`, `updated_at`
    and any int `iid`.
  - Planned replacement applies `_commit_sha(sha)`
    (`operations.py:316-326` — requires 40 hex chars for a full SHA),
    `_validate_remote_ref`, `_rfc3339(remote=True)`, `_remote_positive_int`.
  - Pre-existing **valid-case** test
    `tests/test_gitlab_reads.py:671-683`
    (`test_list_pipelines_is_bounded_paginated_and_normalized_for_public_task8_tool`)
    feeds `"sha": "abc"` (line 677) and `"sha": "def"` (line 678) and asserts
    a successful two-item result.
  - `tests/test_gitlab_reads.py:686-708`
    (`test_pipeline_list_rejects_cross_origin_web_urls`) also uses
    `"sha": "abc"` (line 697); under the planned normalizer `sha` is
    validated before `web_url`, so this test keeps passing for the wrong
    reason and no longer proves cross-origin rejection.
  - No other GitLab test carries a short SHA (`grep -nE '"sha": "(abc|def|
    [0-9a-f]{1,39})"' tests/` → only those three lines); `gitlab_inspect_ci`
    uses its own collectors (`operations.py:3771,3833`), so `test_gitlab_ci.py`
    is unaffected.
  - Task 3 lists only `operations.py` and `tests/test_gitlab_ci.py`; its
    GREEN gate is `pytest tests/test_gitlab_ci.py -k '… or list_pipelines or
    read_pipeline'` — those two terms select nothing in that file (the
    existing pipeline-list tests live in `test_gitlab_reads.py`); its commit
    adds only those two files.
- **Violated invariant:** the plan's TDD/verification discipline (green
  before commit; "Keep the existing `gitlab_list_pipelines` valid result
  shape unchanged and document the deliberate malformed-data tightening in
  its tests") and the reconciliation's F-07/I-02 disposition, which is not
  true for the file that actually holds the pre-existing valid case.
- **Concrete failure:** after Task 3 Step 3 as written,
  `pytest tests/test_gitlab_reads.py -k list_pipelines_is_bounded` raises
  `GitLabError("invalid_remote_data")`; Task 3 Step 4 still reports GREEN;
  Step 5 commits; the failure first surfaces at Task 8 Step 1, where the plan
  authorizes only formatting of "files already changed" —
  `tests/test_gitlab_reads.py` was never changed, so the implementer is left
  choosing between an unauthorized test edit and loosening the normalizer.
- **Why current steps do not fix it:** the "characterization cases" added in
  Task 3 Step 1 go into `test_gitlab_ci.py`; the file with the real
  pre-existing fixture is out of scope for the task.
- **Smallest correction (CI plan Task 3):** add
  `tests/test_gitlab_reads.py` to Files, to the Step 4 gate, and to the Step 5
  `git add`; in Step 1 instruct: replace the three `"sha": "abc"/"def"`
  literals with 40-hex values (`"a" * 40`, `"b" * 40`), keep every other field
  unchanged so the valid-case test still proves shape/pagination, and keep
  the cross-origin test's `sha` valid so it fails on origin alone (optionally
  add one negative case with a short SHA asserting `invalid_remote_data`,
  documenting the tightening). Change the Step 4 `-k` to run
  `tests/test_gitlab_ci.py tests/test_gitlab_reads.py -k 'pipeline_jobs or
  merge_request_pipelines or list_pipelines or pipeline_list or
  read_pipeline'`.
- **RED (runnable now — shows the lenient fixture the tightening will
  reject):**

```bash
cd /Users/coreyellis/code/github.com/cmetech/otto_hermes/ericsson-capabilities
grep -nE '"sha": "(abc|def)"' tests/test_gitlab_reads.py          # expect: lines 677, 678, 697
.venv/bin/python - <<'EOF'
import sys; sys.path.insert(0, "plugins/ericsson-gitlab")
import operations
try:
    operations._commit_sha("abc"); print("accepted")
except Exception as exc:
    print("rejected:", getattr(exc, "category", exc))                 # expect: rejected: invalid_remote_data
EOF
# After Task 3 as written:
.venv/bin/python -m pytest -q tests/test_gitlab_reads.py -k 'list_pipelines_is_bounded'   # expect: FAIL (invalid_remote_data)
```

- **GREEN (after the correction, post-Task 3):**

```bash
.venv/bin/python -m pytest -q tests/test_gitlab_reads.py tests/test_gitlab_ci.py \
  -k 'list_pipelines or pipeline_list or read_pipeline or pipeline_jobs or merge_request_pipelines'   # expect: PASS
```

## 4. Prior-blocker disposition

| # | Prior blocker | Disposition | Evidence |
| --- | --- | --- | --- |
| 1 | Optional MR positional preserves `SUPPRESS` after final parser cleanup | RESOLVED | Release plan Task 6 §5 replaces `parser.py:244-246` so non-required, non-repeatable positionals keep `default=argparse.SUPPRESS` (set at `parser.py:216`) and gain `nargs="?"`. argparse `_get_values` returns the positional's default for `OPTIONAL` with no strings and `take_action` skips the action when the value is `SUPPRESS`; `canonical_arguments` (`parser.py:448-450`) skips a dest that is not set → `{}` as the plan asserts. Positionals never receive `required=` (`parser.py:242-243`). |
| 2 | Backward-compat MR test passes before extension | RESOLVED | `operations.py:1461` already maps `state="open"` → `"opened"`; `_project_summary` (`operations.py:962-965`) returns `id`; the test's `web_url` override matches `_canonical_remote_url`'s expected `/{project_path}/-/merge_requests/{iid}` (`operations.py:1422-1426`); `_merge_request()` fixture exists (`tests/test_gitlab_exploration.py:123`); `PROJECT_API`/`_mock_project` are created by the repository slice (its Task 2 §1), which the release plan requires as an ancestor (Task 1 §1). |
| 3 | All fixed inventories (incl. installed-distribution e2e) named, gated, committed | RESOLVED | Source: `test_gitlab_plugin.py:122` (`== 30`) and `test_connector_cli_gitlab_port.py:150` deleted in CI Task 5; `test_connector_cli_descriptors.py:167,169,173` relationalised in CI Task 5; `test_connector_cli_docs.py:69` (`30`), `test_onboarding_catalog.py:151` frozen list, `test_connector_cli_migration.py:293` `unsupported_prefixes`, and count prose (`gitlab-tools.md:82-83,141`, `docs/configuration.md:259`, `docs/README.md:31,89`) handled in CI Task 6. Hermes: `test_ericsson_connector_surfaces.py:39-44`, `test_ericsson_connector_distribution.py:345-350`, `test_installed_distribution_e2e.py:197-202` all named, run (release Task 8 §4) and committed (release Task 8 §6). The e2e GitLab inventory test (line 170) is unmarked, so it survives `addopts = -m 'not integration'` (`pyproject.toml:547`), and `run_tests_parallel.py:491-497` includes explicitly named files as-is. `TOOL_NAMES` in surfaces is a probe subset (`:403-405`), not a frozen inventory. |
| 4 | `gitlab_read_pipeline` has a skill owner; `intent_tools` created/validated/cumulative/consumed | RESOLVED | CI Task 7 §1 adds `gitlab_read_pipeline` and `gitlab_job_log` to `PLUGIN_SKILLS["ci-investigation"]["read"]` (currently absent, `tests/test_gitlab_skills.py:60-68`); the corpus test asserts `required_intents ⊆ intent_tools`, non-empty values ⊆ `read_tools`, and `read_tools == union of skill reads` (the 21-name seed matches the post-Task-7 union exactly); repository Task 7 §3 and release Task 7 §3 append to both maps; CI Task 9 §3 consumes `intent_tools` for completed routes. |
| 5 | Baselines reference no later-created tests; `-k` expressions select intended tests | RESOLVED (subject to NEW-01) | CI Task 1 §3 runs only existing files plus the Task 0-created parity test; repository/release baselines run the CI-created runner/tests. `-k` terms verified: `read_job`, `pipeline_jobs`, `merge_request_pipelines`, `list_ci_variables or project_and_ancestor_group_variables` (`test_gitlab_ci.py:602`), `list_branches`, `list_tags`, `search_code`, `search_projects`, `list_releases`, `read_release`, `release and read`, `list_todos`, the global-MR expression, `merge_request`. The `list_pipelines`/`read_pipeline` terms in CI Task 3 §4 select nothing in `test_gitlab_ci.py` (see NEW-02); non-zero selection overall. |
| 6 | Visible-project search uses only documented Search API fields | RESOLVED | Plan fields `id, name, path_with_namespace, description, default_branch, last_activity_at, web_url` all appear in the documented `scope=projects` example; `namespace`/`archived`/`visibility` are neither required nor read; display namespace is derived from the validated path. Blob fields `basename, data, path, filename, ref, startline, project_id` and the `ref` parameter are documented for project-scoped `scope=blobs`. |
| 7 | Every parity invocation supplies source dir + exact SHA; parity test fails closed | PARTIAL | All eight invocations supply both on the command line and Task 0 §3 specifies fail-closed; but `scripts/run_tests.sh` strips both before pytest starts (NEW-01), so the values never reach the test. |
| 8 | Native personal scope + matching `@me` emits only the native scope | RESOLVED | Release spec §Extended contract and plan Task 5 §3/§5: matching `@me` is canonicalised to `scope=<native>` with no `/user` lookup and no actor parameter; other usernames are `invalid_input`. Official docs: `author_id` "Combine with `scope=all` or `scope=assigned_to_me`" — consistent with omitting it under `created_by_me`; `assignee_username[]` is an array (plan sends one element). |
| 9 | Direct pytest only in source; Hermes uses `scripts/run_tests.sh` | RESOLVED | Every Hermes pytest invocation in the three plans goes through `scripts/run_tests.sh`; `"$HERMES_PY"` is used only for `py_compile` and the live runner script. (This is what makes NEW-01 bite.) |
| 10 | Routing evaluator: pre-dispatch writes, no real GitLab I/O, no `.env`/`auth.json` copy, two families, rejects incomplete/wrong-order routes | RESOLVED | See §6. |

## 5. Cross-plan/integration audit

- **Task 0 premise is real.** `diff -rq` of managed paths shows Hermes carries
  `capabilities/workflow-packages/ericsson/workflows/jira-defect-loop.{yaml,hermes.yaml}`,
  a differing `digests.json`, differing `skills/ericsson/jira-to-gitlab/SKILL.md`,
  `…/capabilities/jira-defect-loop.md` and `catalog.json`; the Hermes ledger
  lists `capabilities/workflows/jira-defect-loop.{yml,hermes.yaml}` while
  `sets/ericsson.json` does not. `reconcileManagedPaths`
  (`scripts/vendor-ericsson.mjs:680-687`) `rmSync`s any previously managed
  path absent from the new inventory, so vendoring before reconciliation
  would delete shipped behaviour exactly as Task 0 states. Hermes commits
  `7bc872341b` and `70caca680f` exist as cited.
- **Parity-test inputs.** `copyRec` (`vendor-ericsson.mjs:689-708`) copies
  managed files verbatim (skipping `__pycache__`, `.venv`, `.pytest_cache`,
  `.git`), so byte parity is well-defined; `capabilities/ericsson.json` is
  generated (`vendoredFrom`, overlay skill `confluence-research`) and is not
  in the ledger, matching the plan's exclusions. The ledger is
  destination-only; see Minor note 2.
- **SHA ancestry and ordering.** Each plan gates on the prior slice's
  integrated SHAs with `merge-base --is-ancestor`, and later slices reuse (not
  redefine) `PROJECT_API`/`_mock_project`, `_MAX_SEARCH_QUERY`, corpus maps,
  and the single live runner. Consistent.
- **F-09 verified.** `_GITLAB_PROJECT_OPERATIONS` feeds only `_VALIDATION`
  (`descriptors.py:246-249`) and `_SCHEMA_DESCRIPTIONS` (`:557-561`); it does
  not encode requiredness, so keeping `gitlab_list_merge_requests` there with
  `_pos(..., required=False)` is sound. `_CONTINUATION` (`descriptors.py:188`)
  and `_PAGE_CONTINUATION` (`tools.py:39`) are where the plans say.
- **Helpers exist with the stated signatures:** `_normalize_user` (drops
  email, requires `state`; `operations.py:1148-1163`), `_validate_remote_ref`
  (accepts `refs/merge-requests/7/head`; `:140-171`), `_commit_sha(short=)`,
  `_rfc3339(remote=)`, `_same_origin_url`, `_canonical_remote_url`,
  `_variable_metadata(raw, *, scope, source)` returning the eight metadata
  keys plus `scope`/`source` (`:4124-4166`), `_positive_bound`,
  `_remote_positive_int`, `_as_object`, `_paginate`, `_continuation`,
  `_PLUGIN_SKILLS` tuples (`__init__.py:283`),
  `tests/test_manifest.py::test_workflow_package_is_complete_and_digest_bound`
  (`:389`), `tests/test_gitlab_skills.py` direct `tools.py` loaders (`:304,347`).
- **GitLab API facts** (official docs): To-Do `action` and `type` allowlists
  in release Task 4 match the documented sets exactly; MR list example
  includes `project_id` and `references.full`; `author_id`/`reviewer_id`/
  `assignee_username[]` semantics match the plan.
- **Hermes-side inventories** unaffected by the CI/repository slices (no new
  skills); release Task 8 updates the three exact skill inventories without
  weakening them.

## 6. Routing-evaluator audit

- **Bridge path.** `tools/tool_search.py:35-37` documents that bridge tools
  route through `model_tools.handle_function_call`; that function runs
  `resolve_pre_tool_admission` (`model_tools.py:1468-1470`) before
  `registry.dispatch` (`:1534`). The GitLab plugin registers a
  `pre_tool_call` hook `require_write_approval`
  (`plugins/ericsson-gitlab/__init__.py:379-404`), so a selected write is
  admitted/denied before dispatch — exactly the pre-dispatch case the plan's
  transcript-scoring, record-and-deny approval patch, and dispatch stub are
  designed for. Design holds.
- **No real GitLab I/O.** `registry.dispatch` is intercepted before any
  handler (mirrors the existing hook at `scripts/tool_search_livetest.py:375-381`);
  allowlisted reads return generic JSON; origin/PAT are fake; writes are
  denied. Adequate.
- **Credentials.** Today `setup_isolated_home` copies `auth.json`
  (`tool_search_livetest.py:263-264`) and the real `.env` (`:268-270`); the
  plan's backward-compatible `credential_keys` + `copy_auth=False` arguments
  stop both for the GitLab runner; `_redact_secrets` (`:451-469`) masks the
  provider key. `scripts/out/` is git-ignored (`.gitignore:187`), so the
  default output dir passes the plan's `git check-ignore` guard.
- **Two families.** Distinct, namespace-validated `--claude-model` and
  `--openai-model` (openrouter namespaces, consistent with the base
  harness's provider), resolved model recorded per run; no substring
  inference.
- **Route acceptance.** `is_safe` accepts only an exact ordered
  `allowed_sequences` match, or an allowed clarification whose calls are a
  strict prefix; completed routes must cover every `required_intent` via
  `intent_tools`; unit tests cover incomplete project-search and multi-intent
  prefixes, transcript-only writes, wrong ordering, family validation, output
  enforcement, and the no-network stub. Meets the check. Heuristic caveat in
  Minor note 3.

## 7. Minor notes (non-blocking)

1. **Env-var naming drift.** The plan introduces
   `ERICSSON_CAPABILITIES_EXPECTED_SHA` while the repo already defines
   `ERICSSON_CAPABILITIES_TEST_EXPECTED_SHA` (`tests/ericsson_connector_source.py:16`).
   Pick one (fold into the NEW-01 correction).
2. **"Pairs from the vendor ledger" is underspecified.**
   `capabilities/ericsson-vendored-paths.json` lists destinations only; the
   parity test must re-derive source paths with the manifest rules in
   `sourceDestinationPairs` (`vendor-ericsson.mjs:193-235`): `workflows/X` and
   `.hermes.yaml` sidecars → `capabilities/workflows/`, `mcp/mcp-servers.yaml`
   → `capabilities/mcp-servers.yaml`, `mcpLocal` `mcp/outlook-mcp` →
   `plugins/outlook-mcp`, plugins/skills/workflow-package same-path. Say so.
3. **Clarification heuristic.** `is_safe` treats `final.rstrip().endswith("?")`
   as a genuine question; add a negative unit case (safe prefix + trailing
   non-question) so the heuristic's boundary is pinned.
4. **CI Task 3 §4 `-k` terms** `list_pipelines`/`read_pipeline` match nothing
   in `test_gitlab_ci.py`; correct alongside NEW-02.
5. **Isolated-home plugin copy.** CI Task 9 copies the vendored plugin into
   the isolated home while the bundled `plugins/ericsson-gitlab` is also
   discoverable; enabling the bundled plugin may suffice and avoids a
   duplicate-discovery edge. Implementation detail.
6. **Generated-doc executable replacements.** If release Task 7 changes the
   MR-list replacement to `… mr list [project]`, note that
   `test_connector_cli_migration.py:105` builds argv from replacement tokens
   via `_PLACEHOLDER_VALUES` (angle-bracket keys); handle the optional
   bracket token or keep a `<project>` example row.
7. **Release `_links.self` fixture** is an API URL; GitLab's documented
   `_links.self` is the release web URL. Either is same-origin; consider
   deriving the release URL from project path + encoded tag for stability.

## 8. Final gate statement

`BLOCK`. NEW-01 (Critical) makes every fail-closed parity gate in all three
plans fail unconditionally under the mandated runner; NEW-02 (Important)
leaves the CI plan committing a red source tree at Task 3 with no task owning
the fix. Both corrections are small and localised (one allowlist edit plus a
guard test in CI Task 0 Step 3; one file/fixture addition in CI Task 3). All
ten prior blockers are otherwise resolved on source evidence; no other
Critical/Important defect was found. Re-run this blocker-only gate after
those two amendments.
