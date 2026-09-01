# Adversarial plan review — remaining Ericsson GitLab reads (CI, repository discovery, releases/inbox)

## 1. Review identity and inputs

- Reviewer: Claude Fable 5 (`claude-fable-5`), Claude Code CLI; reasoning setting: default (not user-configured); date 2026-08-30; platform macOS (Darwin 25.5.0), zsh.
- Hermes (`hermes-agent`): branch `base`, HEAD `4e8c3d61d2a283ab4c812ec9fe0f296f7b6c2944` (matches reviewed planning commit); `origin/base` `2f200a1db9fc97b528ab0c1d5eff533a89916f6e`; `69a338ba29` ("docs: specify remaining GitLab read coverage") is an ancestor of HEAD. Working tree: only the untracked user-owned paths named in the prompt plus the two prior review files under `docs/reviews/`; `git diff --stat HEAD -- docs/superpowers plugins/ericsson-gitlab plugins/ericsson-connector-cli skills/ericsson capabilities` is empty. 53 linked worktrees present (preserved, untouched). `docs/superpowers/*` is gitignored (`.gitignore:151`) yet all six inputs are tracked (`git ls-files` lists all six).
- Ericsson (`ericsson-capabilities`): `.git/HEAD` → `refs/heads/main`; `refs/heads/main` = `0d7654d14db0afe0c688a752a2676d8cabe2f981` (matches expected); no `.git/worktrees/` directory. `git status` in that repo could not be executed (shell approval denied for `git -C <other repo>`); cleanliness is therefore recorded as **not independently proven** (see §16). Hermes `capabilities/ericsson.json.vendoredFrom` = `0d7654d14db0…` (same SHA).
- SHA-256 of the six inputs (computed with `sha256sum`): all six match the prompt table exactly. No `REVIEW_INPUT_CHANGED` condition.
- Network: outbound `curl` was denied by the sandbox, so every GitLab response-field claim is classified UNCHECKABLE (remote documentation claim) and kept out of the findings table unless local evidence proves it.

## 2. Overall verdict

**BLOCK.** Six Important findings, zero Critical, seven Minor. No credential-disclosure or real-network path was found; the blocks are non-executable TDD steps, a broken CLI parser rule, a To-Do contract that rejects real inboxes, and a live-harness write gate that cannot observe writes.

## 3. Findings

| ID | Sev | Title | Plan / repo |
|---|---|---|---|
| F-01 | Important | Full source gate cannot pass: count-snapshot/disposition-freezing tests and hand-authored docs are outside every File Map, and CLI/migration assertions are placed in the wrong test file | CI T5/T6/T8; Repo T6/T7/T8; Release T6/T7/T8 (ericsson) |
| F-02 | Important | `uniqueItems` on `statuses` breaks the descriptor↔schema contract test and enforces nothing at runtime | CI T5 (ericsson) |
| F-03 | Important | Optional-positional parser rule yields `None` and fails schema validation; the plan's own `mr list` RED cannot go GREEN | Release T6 (ericsson) |
| F-04 | Important | Plan fixtures/assertions contradict `_normalize_user` (requires and returns `state`) — RED tests cannot go GREEN with the specified GREEN | CI T2; Release T2/T4 (ericsson) |
| F-05 | Important | To-Do normalization requires per-item project identity and int target ids while the plan's own target-type allowlist includes group-scoped and commit-SHA targets; unknown remote enums unspecified | Release T4 (ericsson) |
| F-06 | Important | Live harness cannot record a write selection: plugin writes are blocked by the fail-closed approval gate before `registry.dispatch`, the only interception point the plan specifies | CI T9 (hermes) |
| F-07 | Minor | "Move only the existing normalize logic" ships different, tightening code (Premise 2 unsupported as written) | CI T3 |
| F-08 | Minor | Corpus `read_tools` extension understated; later-slice cases reference tools the static invariant will reject | Repo T7; Release T7 |
| F-09 | Minor | Removing `gitlab_list_merge_requests` from `_GITLAB_PROJECT_OPERATIONS` drops its schema description; `gitlab_list_todos.project` needs the same bounds/description | Release T6 |
| F-10 | Minor | Byte-parity is claimed but never asserted; only `vendoredFrom` shape is checked | CI T9/T10; Repo T8; Release T8 |
| F-11 | Minor | Hermes tests are run with direct `pytest`, violating the repository rule (`scripts/run_tests.sh` CI-parity) | CI T1/T9/T10; Repo T1/T8; Release T1/T8 |
| F-12 | Minor | Harness identity/gate details: router `skill_view` name form, clarification detection, and family gate accept degenerate inputs | CI T9 |
| F-13 | Minor | Duplicate helper/constant definitions across slices in the same files | Repo T2/T4; Release T2 |

### F-01 (Important) — full gate blocked by unenumerated snapshot tests, docs, and misplaced assertions

1. F-01, Important. 2. Existing count/disposition freezes and hand-authored docs are not in any File Map; the CI plan names the wrong file for CLI descriptor and migration assertions. 3. CI spec "Connector CLI and documentation"/"Test strategy 5,7"; CI T5 Step 1, T6 Steps 1–5, T8 Step 2; Repo T6/T7/T8; Release T6/T7/T8; `ericsson-capabilities`. 4. Plan text: CI T5 "In `test_connector_cli_gitlab_port.py`, extend the descriptor table with …"; T6 "`pytest tests/test_connector_cli_gitlab_port.py -k migration`"; only deletion named is `assert len(EXPECTED_TOOLS) == 30`. Source: `tests/test_connector_cli_gitlab_port.py` has no descriptor table and no test matching `migration`, but has `assert len(registration.operations) == 30` (`:150`); the real table is `tests/test_connector_cli_descriptors.py::EXPECTED_COMMANDS` with `assert len(descriptors) == len(EXPECTED_COMMANDS) == 60` and `Counter(...) == {"jira":15,"gitlab":30,…}` (`:163-176`); `tests/test_connector_cli_migration.py::test_safety_differences_and_deliberate_gaps_are_explicit` asserts every row under `super-cli gitlab release `, `tag `, `variable `, `todo `, `search code` is `not-yet-supported`/`no-equivalent` (`:293-318`) and `_PLACEHOLDER_VALUES` (`:59-89`) has no `<tag>` (Release row `{brand} gitlab release show <project> <tag>` fails `_template_argv`'s no-placeholder assertion); `tests/test_connector_cli_docs.py:33` asserts `"Jira 15, GitLab 30, Confluence 9, and ARM 6" in docs/README.md` and `:81-82` asserts exactly 30 `    - ` tool lines in `gitlab-tools.md` frontmatter; `tests/test_onboarding_catalog.py:127-170` freezes the exact `gitlab-tools` tool list; `docs/README.md:31` and `docs/configuration.md:259` ("30-operation surface contains 18 bounded reads and 12 writes") are hand-authored count snapshots; `catalog_lib.compare_inventories` (`:1927-1961`) fails `validate_catalog.py` with "unrepresented plugin tool" unless the `gitlab-tools.md` frontmatter `implementation.tools` list gains every new tool (plan mentions only prose tables). 5. Invariant 14 (valid commands/complete GREEN), 13 (migration truth), Global Constraint "tests assert contracts, not totals". 6. Implementer adds `gitlab_read_job`; runs T5 Step 5 GREEN → `test_connector_cli_gitlab_port.py:150` fails; runs T8 Step 2 full gate → descriptors/docs/migration/onboarding tests fail; edits files outside the plan, then `test -z "$(git status --porcelain)"` and the commit boundaries no longer describe reality. 7. Either the slice ships with red tests suppressed, or unplanned edits land in untracked commits; the migration test would still assert `variable list`/`tag list`/`release list`/`todo list`/`search code` are unsupported — the opposite of the plans' migration truth. 8. No other task names these files; T8's full gate only detects, it does not plan the fix; `-k migration` on gitlab_port selects nothing, so RED "against current stale rows" never occurs. 9. In each plan: add `tests/test_connector_cli_descriptors.py`, `tests/test_connector_cli_migration.py`, `tests/test_connector_cli_docs.py`, `tests/test_onboarding_catalog.py`, `docs/README.md`, `docs/configuration.md` to File Map and commit lists; move the descriptor-table and mapping assertions to their real files; replace `== 30/== 60`/family counts with set relationships (`set(schemas) == set(descriptor ops for connector)`), rewrite `unsupported_prefixes` as an exact command set that excludes the newly supported reads, add `<tag>` to `_PLACEHOLDER_VALUES`, and replace count sentences in the two docs with capability wording; add new tools to `gitlab-tools.md` frontmatter `tools:`. 10. RED: after adding one schema, `pytest -q tests/test_connector_cli_descriptors.py tests/test_connector_cli_docs.py tests/test_connector_cli_migration.py tests/test_onboarding_catalog.py tests/test_connector_cli_gitlab_port.py` must fail only on behavior, then GREEN with the edits above; acceptance: `grep -n "GitLab 30\|18 bounded reads" docs/README.md docs/configuration.md` empty.

### F-02 (Important) — `uniqueItems` breaks the descriptor contract test

1. F-02, Important. 2. Invented JSON-Schema keyword unsupported by the descriptor mirror. 3. CI spec `gitlab_list_pipeline_jobs` "allowlisted array"; CI T5 Step 3; `ericsson-capabilities`. 4. Plan: "Use JSON-array `statuses` with `uniqueItems: true`, `minItems: 1`, and the allowlisted enum". Source: `tests/test_connector_cli_descriptors.py::_live_schema_contract` asserts `set(schema) <= known` where `known` lacks `uniqueItems` (`:293-314`); `SchemaContract` (`descriptors.py:8-31`) has no such field; runtime `tools.invoke` only checks property names/required (`tools.py:563-574`), so the keyword never validates. 5. Invariant 12 (schema/runtime parity), 14. 6. Schema written per plan → `test_recursive_binding_schema_contract_exactly_matches_live_properties` raises AssertionError on the `statuses` property for every descriptor run; duplicates `["failed","failed"]` still reach the operation. 7. Full gate red; the plan's own "duplicate statuses" RED case has no GREEN mechanism. 8. T5's focused GREEN list omits `test_connector_cli_descriptors.py`; T8 only detects. 9. Remove `uniqueItems`; keep `minItems:1` + `items.enum`; in `list_pipeline_jobs` raise `invalid_input` on duplicates/unknown/empty. 10. RED: `operations.list_pipeline_jobs("42", 900, statuses=["failed","failed"])` → `GitLabError.category == "invalid_input"` with no HTTP call; acceptance: `pytest -q tests/test_connector_cli_descriptors.py` green.

### F-03 (Important) — optional positional yields `None` and fails contract validation

1. F-03, Important. 2. The "one-line" parser rule is insufficient. 3. Release spec "Connector CLI and migration documentation"; Release T6 Steps 2, 5; `ericsson-capabilities`. 4. Plan: add `kwargs["nargs"] = "?"` for non-required, non-repeatable positionals; test `(("gitlab","mr","list"), [], {})`. Source: `parser.py:244-246` pops both `dest` and `default` for positionals, so argparse's default `None` is stored when the positional is absent; `canonical_arguments` (`:448-485`) uses `hasattr(namespace, dest)` → attribute exists with `None` → `_validate_contract(None, oneOf[string,integer])` matches 0 alternatives → `CliInputError("value does not match its command schema")` → exit 2. 5. Invariant 12/14; Premise 8. 6. `otto gitlab mr list --scope reviews_for_me` → usage-valid parse, then exit 2 "invalid_input" before dispatch. 7. The headline no-project CLI form (spec acceptance 4/7) is unusable; the plan's RED never passes. 8. `test_all_60_minimum_parses_map_to_one_canonical_host_call` only feeds positionals present; nothing else exercises absence. 9. In the same branch keep `default=argparse.SUPPRESS` (do not pop `default` when `nargs="?"`), so an absent positional leaves no attribute (`take_action` skips SUPPRESS). 10. RED: `parser.parse_args(["gitlab","mr","list"])` then `canonical_arguments(ns) == {}`; and `["gitlab","mr","list","g/p"] → {"project":"g/p"}`; plus `["gitlab","mr","list","42"] → {"project": 42}`.

### F-04 (Important) — fixtures/assertions contradict `_normalize_user`

1. F-04, Important. 2. RED tests cannot go GREEN with the GREEN as written. 3. CI spec `gitlab_read_job` "display-safe triggering user"; CI T2 Step 1/3; Release T2 Step 1 (`author`), T4 Step 1 (`author`); `ericsson-capabilities`. 4. Plan GREEN calls `self._normalize_user(payload.get("user"))`; fixtures give `{"id":7,"username":"casey","name":"Casey","email":…}` and CI asserts `result["job"]["user"] == {"id":7,"username":"casey","name":"Casey"}`. Source: `operations.py:1147-1163` requires `username`, `name`, **and `state`** (raises `invalid_remote_data` otherwise) and returns all four keys; existing fixtures use `_user()` with `state: "active"` (`tests/test_gitlab_exploration.py:96-104`). 5. Invariant 14 (executable TDD), 8. 6. Implementer runs T2 Step 4 → `invalid_remote_data` on the happy path; same for release list/detail and To-Do author. 7. Three GREEN steps fail; implementer must silently choose between changing the projection (a contract decision) or the fixture. 8. No later task revisits these fixtures. 9. Add `"state": "active"` to every plan fixture user/author and assert the four-key projection (or, if the spec wants three keys, say so and add a dedicated projection helper — a product decision). 10. RED: `test_read_job_returns_metadata_…` asserting `result["job"]["user"] == {"id":7,"username":"casey","name":"Casey","state":"active"}`; acceptance: `pytest -q tests/test_gitlab_ci.py -k read_job` green with no change to `_normalize_user`.

### F-05 (Important) — To-Do contract rejects real inboxes

1. F-05, Important. 2. Per-item "canonical project identity", int target id, and undefined unknown-enum behavior conflict with the plan's own allowlists. 3. Release spec `gitlab_list_todos`; Release T4 Steps 1/3; `ericsson-capabilities`. 4. Plan: allowlist includes `Epic`, `Namespace`, `Commit`; "Normalize only ID, action, state, timestamps, safe author, canonical project identity, target type, and the allowlisted target keys `id`, `iid`, `title`, `name`, `state`"; fixture target id `12`; nothing says `project` may be absent, how a Commit target's SHA `id` is typed, or what happens to a remote `action_name`/`target_type` outside the frozen sets. Source: Epics and `Namespace` access requests are group objects, not project objects (GitLab data model; no project exists to report), and commit ids are hex SHAs; house style rejects unknown enums (`_normalize_merge_request` state check `operations.py:1384`; `_remote_positive_int` for ids). 5. Invariant 8 (strict data must not reject valid evidence), Premise 6; spec "Missing optional GitLab fields stay absent/None". 6. Ericsson user with one epic To-Do (or a `review_requested`/`added_approver` action newer than the frozen list) calls `gitlab_list_todos` → `invalid_remote_data` for the whole page. 7. The inbox read is unusable for exactly the users it targets; failure is silent to the plan because fixtures only contain MR targets. 8. No RED case includes a group-level or Commit target or an unknown action; T8 gates are green on fixtures. 9. Specify: `project` optional (`None`; optionally carry a bounded `group` identity), target `id` typed by `target_type` (positive int, or 7–40 hex for `Commit`), remote `action_name`/`target_type` validated as bounded tokens only (allowlists constrain caller filters), `target_url` same-origin. 10. RED: three fixtures — Epic todo with `"project": null`, Commit todo with `"target": {"id": "a"*40, "title": …}`, and `"action_name": "review_requested"` — all normalize; caller `action="not-a-real-action"` → `invalid_input` with no HTTP call.

### F-06 (Important) — harness write gate sits downstream of the fail-closed approval

1. F-06, Important. 2. Write selections are invisible to the specified interceptor. 3. CI spec "Any GitLab write selection is a hard failure"; CI T9 Step 3; `hermes-agent`. 4. Plan: "Intercept `registry.dispatch` before any GitLab handler runs. For any `gitlab_*` name not in corpus `read_tools`, record a hard failure"; `is_safe` scores `gitlab_calls` from that interceptor. Source: `model_tools.handle_function_call` → `tool_call` recursion (`:1367-1384`) → `_authorized_registry_dispatch` calls `resolve_pre_tool_admission` **before** `registry.dispatch` (`:1462-1534`); the plugin's `pre_tool_call` hook returns `approve` for every `_WRITE_TOOLS` entry (`plugins/ericsson-gitlab/__init__.py:379-404`); the plugin-escalation gate passes `fail_closed_when_no_human=True` (`tools/approval.py:4148`) and in a bare script with `HERMES_INTERACTIVE` unset returns `approved: False` (`:3785-3804`) → `_record_authorization_block` returns before dispatch. 5. Invariants 5, 7; Premise 11. 6. Model emits `tool_call{name: gitlab_merge_merge_request}`; approval blocks; tool result "BLOCKED…" returns; model then calls an allowed read; `gitlab_calls == ["gitlab_read_merge_request"]` → `is_safe` True → run passes and exits 0. 7. The reliability gate certifies a corpus in which a write was selected — the one outcome the spec calls a hard failure. 8. `_extract_bridge_calls` is only recorded for the report; the pass/fail function ignores it. 9. Score attempted names from the transcript (`tool_call.args.name` and direct assistant `tool_calls`) unioned with the dispatch log; any `gitlab_*` outside `read_tools` from either source → nonzero; additionally monkeypatch `tools.approval.request_tool_approval` to record-and-deny so intent is captured deterministically without prompting. 10. RED: unit test feeding a synthetic transcript with one `tool_call{name:"gitlab_merge_merge_request"}` and an empty dispatch log must make the case fail with nonzero status.

### F-07 (Minor) — pipeline projection extraction is not behavior-preserving

1. F-07, Minor. 2. Plan says "move only the existing inner `list_pipelines.normalize` logic" but supplies different code. 3. CI T3 Step 3; Premise 2. 4. Existing normalize (`operations.py:2598-2623`) accepts any non-bool int `id`, silently ignores a non-int `iid`, and always emits `web_url` (possibly `None`); plan code uses `_remote_positive_int` for `id` and `iid` and omits `web_url` when absent. 5. Spec "Keep the existing `gitlab_list_pipelines` result shape unchanged". 6. A pipeline page with `"iid": "3"` or missing `web_url` now raises/changes key presence. 7. Silent contract drift with no failing test (existing tests cover neither edge). 8. T3 Step 4 reruns only current tests. 9. Move the body verbatim, or state the tightening explicitly and add the two edge tests. 10. RED: `list_pipelines` with `web_url` absent → `"web_url" in item and item["web_url"] is None` (if preserving), or documented `invalid_remote_data` (if tightening).

### F-08 (Minor) — corpus `read_tools` growth understated

1. F-08, Minor. 2. Later slices append only their new tools. 3. Repo T7 Step 3; Release T7 Step 3; static invariant CI T7 (`permitted <= set(corpus["read_tools"])`). 4. CI `read_tools` (T7 Step 3) contains no commit/group/tree/MR-detail tools; repo cases allow "branch/commit-history clarification" (`gitlab_list_commits`, `gitlab_read_commit`) and project search first; release cases cover "selected/project MR discovery" and global MR listing (`gitlab_list_merge_requests`, `gitlab_read_merge_request`, `gitlab_list_merge_request_*`, `gitlab_merge_request_approvals`). 5. Premise 9. 6. Static test fails until unplanned edits. 7. Wasted RED/GREEN cycle; risk of ad-hoc allowlist widening. 8. Caught by the static test only after the fact. 9. Enumerate the full `read_tools` per slice in each plan. 10. Acceptance: `pytest -q tests/test_gitlab_skills.py -k routing_cases` green with the enumerated list.

### F-09 (Minor) — optional-project descriptor parity

1. F-09, Minor. 2. Removing an op from `_GITLAB_PROJECT_OPERATIONS` also removes its description. 3. Release T6 Step 5. 4. `descriptors.py:557-561` derives `_SCHEMA_DESCRIPTIONS[(op,"project")]` from the same tuple as `_VALIDATION`; `test_recursive_binding_schema_contract_exactly_matches_live_properties` compares `description` to `_PROJECT["description"]`. 5. Invariant 12. 6. Description mismatch fails the descriptors test; `gitlab_list_todos.project` lacks bounds/description entirely. 7. Full gate red. 8. Not in T6's focused list. 9. Keep `gitlab_list_merge_requests` in the tuple (it governs bounds/description, not requiredness) and add `gitlab_list_todos`. 10. Acceptance: `pytest -q tests/test_connector_cli_descriptors.py` green.

### F-10 (Minor) — byte parity claimed, not asserted

1. F-10, Minor. 2. File Map says `test_ericsson_connector_distribution.py — exact source SHA and managed-byte parity`. 3. CI T9/T10; Repo T8; Release T8. 4. That test (`tests/hermes_cli/test_ericsson_connector_distribution.py`) and `tests/ericsson_connector_source.py` check `vendoredFrom` is a full SHA and file presence, never bytes; parity today rests on `vendor-ericsson.mjs` copying from a committed-index snapshot (`:1315-1362`). 5. Invariant 15. 6. A post-vendor stray edit in `$HERMES_WT/plugins/ericsson-gitlab` passes every listed gate. 7. Vendored bytes ≠ source SHA without detection. 8. `git status` cleanliness after commit does not compare trees. 9. Add `diff -r -x __pycache__ "$SOURCE_WT/plugins/ericsson-gitlab" "$HERMES_WT/plugins/ericsson-gitlab"` (and connector-cli, `skills/ericsson/gitlab`) to T10/T8 Step 2. 10. Acceptance: the `diff -r` commands exit 0.

### F-11 (Minor) — Hermes tests invoked with direct `pytest`

1. F-11, Minor. 2. Repo rule violation. 3. Every Hermes pytest step. 4. Hermes `AGENTS.md` "Testing": "ALWAYS use `scripts/run_tests.sh` — do not call `pytest` directly" (hermetic env, per-file isolation). 5. Repository instruction precedence. 6. Live env vars/credentials leak into distribution tests; "works locally, fails in CI". 7. Divergent gate results. 8. None. 9. Replace `"$HERMES_PY" -m pytest -q …` with `scripts/run_tests.sh …` (file + `-k`). 10. Acceptance: commands in the plan invoke `scripts/run_tests.sh`.

### F-12 (Minor) — harness identity and gate details

1. F-12, Minor. 2. Router-name matching, clarification detection, and family gate are under-specified. 3. CI T9 Step 3. 4. `skill_view("gitlab")` resolves by directory name (`tools/skills_tool.py:1341-1348`) while existing tests call `skill_view("ericsson/gitlab")` (`test_ericsson_connector_surfaces.py:701`); `is_safe` treats `"?" in final` as clarification; the family rule is substring-based on individual names. 5. Invariant 7; Premise 12. 6. A run that calls `skill_view("ericsson/gitlab")` fails "loads gitlab"; a polite clarification without `?` fails; `--model x --model x` passes if one name contains both substrings. 7. False negatives/positives in the gate. 8. None. 9. Accept `{gitlab, ericsson/gitlab}`; treat "no GitLab call + non-empty final" on ambiguous cases as clarification only when the final contains no fabricated result (or require an explicit `clarify`-style marker); require ≥2 distinct model ids with disjoint family matches. 10. RED: unit tests for `is_safe`/family gate covering these three cases.

### F-13 (Minor) — duplicated definitions across slices

1. F-13, Minor. 2. Repo T2 and Release T2 both "add at the top of `tests/test_gitlab_exploration.py`" `PROJECT_API`/`_mock_project`; Repo T2 and T4 both define `_MAX_SEARCH_QUERY = 1024`. 3. Repo T2/T4; Release T2. 4. Plan text as quoted; file currently has neither helper (`tests/test_gitlab_exploration.py:12-37`). 5. Invariant 14. 6. Silent shadowing/duplication. 7. Maintainability; possible flake if definitions diverge. 8. None. 9. Release plan: "reuse the helpers introduced by the repository slice"; define `_MAX_SEARCH_QUERY` once. 10. Acceptance: `grep -c "^def _mock_project" tests/test_gitlab_exploration.py` == 1.

## 4. Specification → plan traceability

Legend: C complete, P partial, X contradicted, M missing.

**CI slice** — 4 operations→T2/T3/T4 (C; F-04 on T2, F-07 on T3); preserve `list_pipelines`/`read_pipeline`/`job_log`/`inspect_ci`→T3 S4, T4 S4 (P: F-07); no new writes, webhooks excluded→T6 S1, T10 S2 (C); reuse path/no new layer→T2–T4 (C); `gitlab_read_job` fields incl. trace/artifact/variable/email exclusion→T2 (C); `list_pipeline_jobs` filters/`include_retried`/continuation→T3 (P: F-02); `list_merge_request_pipelines`→T3 (C); `list_ci_variables` metadata-only, no ancestor groups→T4 (C); limits after normalization, `invalid_remote_data`, envelope unchanged→T2–T4 + existing `application.execute` (C); routing without classifier, decision table, router wording→T7 S4 (C); corpus clear/paraphrase/terse/ambiguous + static tests→T7 (P: F-08); live eval two families/3× ambiguous/write=fail→T9 S3/S6 (P: F-06, F-12); CLI four commands→T5 (X: F-01 file, F-02); YAML rows + regenerated md→T6 (P: F-01); `mr diff` correction, webhook stays excluded, capability tables→T6 (P: F-01 docs/frontmatter); catalog regenerated/validated→T6 S4 (C); source-first/vendor exact SHA→T1,T8,T9,T10 (P: F-10); test-strategy items 1–8→T2,T3,T3,T4,T5,T7,T6,T9 (P per above); acceptance 1–8→T2–T4/T7/T9/T5/T6/T10 (P).

**Repository slice** — 4 operations→T2–T5 (C; remote-field questions Q1–Q2); complements existing tools; no clone/global search/regex/write→T8 S6 + skill (C); project-first code search→T7 (C); `list_branches` fields→T2 (C); `list_tags` fields/order/no release expansion→T3 (C, Q2); `search_code` bounds/redaction/untrusted flag/ref→T4 (C); `search_projects` fields/coverage→T5 (C, Q1); collection/safety→T2–T5 (C); decision table, tag≠release, project≠code search→T7 S3 (C); corpus incl. injection case→T7 (P: F-08); CLI commands→T6 (P: F-01); YAML+md→T7 (P: F-01); onboarding→T7 (P: F-01); vendoring→T8 (P: F-10); acceptance 1–8→(P).

**Release/inbox slice** — releases list/detail→T2/T3 (P: F-04; Q5); To-Dos→T4 (X: F-05); MR extension (optional project, scopes, `@me`, contradictions, cross-project identity, compat)→T5 (C; Q4); no duplicate global tool→T5 (C); current-user lookup only for `@me`→T5 (C); external asset omission + warning→T3 (C; Q6); collection/safety→T2–T5 (C); two skills, precedence, router→T6 S4, T7 S3 (C); corpus→T7 (P: F-08); CLI incl. optional positional→T6 (X: F-03; F-09); YAML rows + MR row upgrade→T7 (P: F-01); onboarding→T7 (P: F-01); vendoring→T8 (P: F-10); acceptance 1–9→(P).

## 5. All-task coverage / dependency matrix

| Task | Rating | Notes / dependency |
|---|---|---|
| CI T1 | C | Baseline commands valid; both venvs exist; `.worktrees/` ignored in both repos |
| CI T2 | X | F-04; otherwise consumes real helpers (`_remote_positive_int`, `_rfc3339`, `_validate_remote_ref`, `_same_origin_url`, `_normalize_user`, `_commit_sha`, `_as_object`); `math` import correctly noted as new |
| CI T3 | P | F-07; F-02 lands in T5 schema; `_operations(max_pages=2)` valid |
| CI T4 | C | `_variable_metadata` reuse correct; ancestor endpoints never touched |
| CI T5 | X | F-01 (wrong file, `== 30` at gitlab_port:150), F-02 |
| CI T6 | P | F-01 (docs/README, configuration.md, onboarding frontmatter, migration tests); `-k migration` selects nothing in the named file; `test_task11_convergence.py -k 'catalog or onboarding'` selects at most one unrelated test |
| CI T7 | P | F-08; test loader mirrors existing pattern (`test_gitlab_skills.py:344-363`) |
| CI T8 | P | Full gate fails until F-01/F-02 fixed |
| CI T9 | P | F-06, F-12; helpers exist; `scripts/out/` is the existing ignored output dir |
| CI T10 | P | F-10 |
| Repo T1 | C | Prerequisite checks reference artifacts CI creates |
| Repo T2 | C | Q2 (branch fields verified present in GitLab branch entity by house tests? no — UNCHECKABLE); PAT redaction coincidence: `_operations()` PAT is `"secret-token"` |
| Repo T3 | P | Q2 (tag `web_url`) |
| Repo T4 | C | Cap math honest (32×4096 = 128 KiB); scoped endpoint |
| Repo T5 | P | Q1 (`archived`/`visibility`) |
| Repo T6 | P | F-01 |
| Repo T7 | P | F-01, F-08 |
| Repo T8 | P | F-01 gate, F-10, F-11 |
| Rel T1 | C | Checks repository-research dir and runner |
| Rel T2 | X | F-04; F-13 |
| Rel T3 | C | Q6 (`direct_asset_url`) |
| Rel T4 | X | F-05 |
| Rel T5 | P | Q4 (`assignee_username`); otherwise complete, backward-compat lock test sound |
| Rel T6 | X | F-03, F-09 |
| Rel T7 | P | F-01 (`<tag>`, prefixes), F-08 |
| Rel T8 | P | F-01, F-06, F-10, F-11 |

Cross-plan ordering: repo/release correctly gate on CI artifacts; the count/disposition tests (F-01) must be converted once in the CI slice or every later slice re-breaks them; corpus `read_tools` (F-08) must be cumulative; worktrees are never removed, but `.worktrees/` is ignored so later `status --porcelain` checks stay clean.

## 6. Operation / API / schema / CLI / migration coverage

| Behavior | Endpoint | Normalizer | Schema/dispatch/manifest | CLI | YAML row | Status |
|---|---|---|---|---|---|---|
| `gitlab_read_job` | `GET /projects/:id/jobs/:job_id` | `_normalize_job` | T5 | `job show` | `job view` | F-04; Q7 (`queued_at`) |
| `gitlab_list_pipeline_jobs` | `…/pipelines/:id/jobs` `scope[]`,`include_retried` | `_normalize_job` | T5 | `pipeline job-list` | `pipeline job list` | F-02 |
| `gitlab_list_merge_request_pipelines` | `…/merge_requests/:iid/pipelines` | `_normalize_pipeline_summary` | T5 | `mr pipeline-list` | `mr pipeline list` | F-07 |
| `gitlab_list_ci_variables` | `…/variables` | `_variable_metadata` (8 keys; plan text says "seven") | T5 | `variable list` | `variable list` (was `excluded`) | OK; F-01 migration test |
| `gitlab_list_branches` | `…/repository/branches` `search` | `_normalize_branch` | T6 | `branch list` | `branch list` | OK |
| `gitlab_list_tags` | `…/repository/tags` `order_by`,`sort` | `_normalize_tag` | T6 | `tag list` | `tag list` | Q2 |
| `gitlab_search_code` | `…/projects/:id/search?scope=blobs` | `_normalize_code_match` | T6 | `search code` | `search code` | OK |
| `gitlab_search_projects` | `/search?scope=projects` | `_normalize_project_search_result` | T6 | `project search` | `project search` | Q1 |
| `gitlab_list_releases` | `…/releases` | `_normalize_release_summary` | T6 | `release list` | `release list` | F-04; Q5 |
| `gitlab_read_release` | `…/releases/:tag` | `_normalize_release_detail` | T6 | `release show` | `release view` | F-01 (`<tag>`); Q6 |
| `gitlab_list_todos` | `/todos` | `_normalize_todo` | T6 | `todo list` | `todo list` | F-05; Q3 |
| `gitlab_list_merge_requests` (ext.) | `/merge_requests` vs `/projects/:id/merge_requests` | `_normalize_merge_request` + `_global_merge_request_project` | T6 | `mr list [project]` | `mr list` upgraded | F-03; Q4 |

## 7. Routing ownership and near-neighbor matrix

| Intent | Owner (qualified skill) | Near neighbor | Precedence stated in plans |
|---|---|---|---|
| Job metadata | ci-investigation `gitlab_read_job` | `gitlab_job_log` (trace) | Yes (decision table) |
| Jobs in pipeline / pipelines of MR | ci-investigation | `gitlab_list_pipelines` / merge-request-review | Yes |
| Project variable metadata | ci-investigation `gitlab_list_ci_variables` | `gitlab_inspect_ci` (inherited) | Yes |
| Branches / tags | repository-research | releases (release-research), commit history | Yes; tag≠release both sides |
| Project search vs code search | repository-research | `gitlab_resolve_project`, `gitlab_list_group_projects` | Yes; missing-project → search first |
| Releases | release-research | tags (context-only reference) | Yes |
| To-Dos / cross-project MR queues | personal-inbox | merge-request-review (project MR, one MR) | Yes ("my queue/inbox/across GitLab" vs "MRs in project X"/"review MR 42") |
| Digest | gitlab-activity-digest (unchanged) | personal-inbox global MR listing | Not stated — `gitlab_list_merge_requests` will be declared read in three skills; only two precedence rules exist. Minor gap, no finding (digest is time-window + scheduling intent). |

## 8. Corpus / live-harness validity

- Referenced names exist at each plan's point in sequence (skills, tools, `read_tools`) — except F-08.
- Allowed-first/follow-up semantics are realistic; clarification path exists but is detected weakly (F-12).
- Repetition is enforced by the runner and statically checked (`repetitions == 3 if ambiguous else 1`).
- Family gate: substring rule; degenerate inputs possible (F-12).
- Plugin copying/enablement/fake credentials mirror a proven pattern (`test_ericsson_connector_surfaces.py:668-757`); `get_all_toolsets()` includes plugin toolsets so `enabled_toolsets=None` reaches `ericsson-gitlab` (`toolsets.py:899-913`); `reset_module_state` does not evict `hermes_plugins.*` modules (Q9).
- Interception: reads are intercepted before the handler (`tool_call` → `handle_function_call` → `registry.dispatch`); unexpected `gitlab_*` names also route through dispatch; **writes never reach dispatch** (F-06). No path to a live GitLab exists in either case.
- Reports: router/skill views must be extracted from the transcript (new extractor); `_redact_secrets` covers OpenRouter key patterns.
- Nonzero status guaranteed for wrong skill / wrong first read / disallowed follow-up / no call on clear / missing family — yes as specified; for "any write" — no (F-06).
- CI creates corpus/runner once; later slices only extend — yes.

## 9. Invariant verdicts

1 Exact scope — HELD (all 12 behaviors planned; webhooks/mutations excluded). 2 Source-first — HELD. 3 Stable context — HELD (no classifier/toolset swap). 4 One authority per intent — HELD (digest overlap noted). 5 Read-only routing — HELD statically; live gate defect F-06. 6 No live GitLab in evals — HELD. 7 Two families / 3× ambiguous — HELD in design; F-06/F-12 weaken proof. 8 Strict remote data — VIOLATED for To-Dos (F-05); fixtures F-04. 9 Bounded reads — HELD. 10 Sensitive boundary — HELD (values discarded; PAT redaction; same-origin). 11 Identity semantics — HELD (target-project identity for global MRs). 12 Schema/runtime parity — VIOLATED (F-02, F-03, F-09). 13 Migration truth — VIOLATED in execution (F-01 migration test contradicts new dispositions; docs counts). 14 Executable TDD — VIOLATED (F-01–F-04). 15 Exact vendoring — HELD by construction; parity not asserted (F-10). 16 No speculative surface — HELD.

## 10. Premise verdicts

1 SUPPORTED (`_paginate`, `_continuation_source`, `_continuation`, `resolve_project`, envelope, `_redact_text` all present; only inline helpers needed). 2 UNSUPPORTED as written (F-07). 3 INSUFFICIENTLY ESTABLISHED (optional nested fields handled via `optional_time`; `user`/`commit.web_url`/`queued_at` presence UNCHECKABLE — Q7; fixture defect F-04). 4 SUPPORTED (32 results × 4096 B = 128 KiB; continuation from `_paginate`). 5 SUPPORTED (target-project identity derived from `references.full` + same-origin URL; `source_project_id` not needed for `(project, iid)` follow-up) — availability of `references` on the supported server version is Q4b. 6 UNSUPPORTED (F-05: unknown-remote behavior unspecified; allowlist likely stale — Q3). 7 INSUFFICIENTLY ESTABLISHED (Q6: `direct_asset_url` same-origin with external `url` unaddressed). 8 UNSUPPORTED (F-03). 9 SUPPORTED with F-08 caveat. 10 SUPPORTED (helpers exist; agent construction must be new code; plugin load pattern proven). 11 UNSUPPORTED for writes (F-06); SUPPORTED for reads. 12 SUPPORTED in kind (real models, real skill loading, stub handlers); weakened by F-06/F-12. 13 SUPPORTED for generated files; hand-authored `docs/README.md`/`configuration.md` are outside the generators (F-01). 14 SUPPORTED (`.worktrees/` ignored; `show-toplevel` equals linked worktree; ericsson `main` is its real dev branch; Hermes work starts from `base`). 15 SUPPORTED (only `webhook list` and write rows remain; every read row maps).

## 11. Strict-TDD audit (files / symbols / commands / ordering)

- Files: all named production files exist; test files exist except the two the plans create. Wrong-file citations: CI T5/T6 (`test_connector_cli_gitlab_port.py` as descriptor/migration table). Missing files: `docs/README.md`, `docs/configuration.md`, `tests/test_connector_cli_{descriptors,migration,docs}.py`, `tests/test_onboarding_catalog.py`, `tests/test_connector_cli_parser.py` (F-03 unit assertion belongs there).
- Symbols: all consumed helpers VERIFIED (§15). `WORKTREE_ROOT` in CI T9 is not a `tool_search_livetest` export (`_WORKTREE_ROOT` is private) — runner must define its own. "`_CONTINUATION` remains the CLI option binding" is correct (`descriptors.py:188`).
- Commands: pytest `-k` expressions valid; `-k migration` in gitlab_port matches nothing; `test_task11_convergence.py -k 'catalog or onboarding'` matches ≤1 unrelated test; `node --test scripts/__tests__/vendor-ericsson.test.mjs` valid; `ERICSSON_CAPABILITIES_DIR` honored by the vendor script; `python3 -c` manifest read valid.
- RED reasons: T2/T3/T4 RED reasons accurate; GREEN completeness fails at F-02/F-03/F-04/F-05.
- Commit boundaries: staged lists omit files that must change (F-01), so `git status --porcelain` gates will trip.
- Ordering: repo/release depend on CI artifacts and check them; release T2 duplicates repo T2 helpers (F-13).

## 12. Verified-complete areas

- Vendoring path: clean-tree provenance (`resolveCleanSourceCommit`), committed-index snapshot copy, `__pycache__` exclusion, `routing_cases.json` inside `plugins/ericsson-gitlab/` is copied automatically; `vendoredFrom` = full SHA; no vendor-script change needed.
- Deferred-tool path: plugin tools are deferrable (`is_deferrable_tool_name`), `tool_call` recurses through `handle_function_call` to `registry.dispatch`, scoped to session toolsets.
- Qualified skill loading: `ctx.register_skill` → `PluginManager.find_plugin_skill`/`skill_view("ns:skill")`.
- Application envelope and error taxonomy unchanged; `_variable_metadata` never reads `value`; `_redact_text` PAT redaction available.
- Scope: exactly the 12 behaviors; webhooks/mutations excluded in YAML, skills, and diff gates.
- MR extension design: native scopes, single `/user` call, contradiction rejection, backward-compat lock test.

## 13. Required corrections (ordered)

1. F-01 — enumerate and convert the count/disposition tests and docs in the CI slice; move assertions to their real files; add `<tag>` placeholder. 2. F-02 — drop `uniqueItems`, validate duplicates in the operation. 3. F-03 — keep `default=SUPPRESS` for `nargs="?"` positionals. 4. F-04 — fixtures with `state`, four-key assertion. 5. F-05 — optional project, per-type target id, bounded unknown enums; add Epic/Commit/unknown-action RED cases. 6. F-06 — transcript-derived write detection + deterministic approval denial. 7. F-09, F-08, F-07, F-13, F-12, F-10, F-11.

## 14. Unresolved questions (evidence needed: official GitLab REST docs or an anonymized real response)

- Q1 `/search?scope=projects` result fields: do results include `archived`, `visibility`, `namespace`? Plan/spec require archived/visibility "facts"; if absent, strict normalization fails every search. Resolve before Repo T5; make them optional if unproven.
- Q2 `GET /projects/:id/repository/tags` entity: is `web_url` present? Plan fixture reads it from the payload. If absent, derive `origin/{path}/-/tags/{quote(name)}` instead.
- Q3 `/todos` enumerations: current `action` set (e.g., `review_requested`, `review_submitted`, `added_approver`, key-expiry actions) and `type` set (`Key`?); also whether `project` may be null (group todos) and whether `project` carries `web_url`.
- Q4 `GET /merge_requests` parameters: is `assignee_username` supported (the plan maps `assignee` → `assignee_username`)? If not, non-`@me` assignee filtering needs a user lookup the plan excludes — product decision. Q4b: `references.full` and `blocking_discussions_resolved` present on the connector's supported server version?
- Q5 Release `description`/`author` nullability (releases created from tags without notes). Plan treats both as present.
- Q6 Release link with external `url` but same-origin `direct_asset_url`: omit entirely or admit the direct URL? Plan is silent.
- Q7 Job entity: `queued_at` is not a documented job field (only `queued_duration`); `commit.web_url`/`pipeline.web_url` presence should be confirmed since the plan requires them.
- Q8 Ericsson repo cleanliness could not be executed by this reviewer (approval denied); plans' T1 check covers it.
- Q9 `reset_module_state` prefixes exclude `hermes_plugins.*`; confirm repeated in-process runs re-register cleanly or add the prefix.

## 15. Source-grounding ledger

VERIFIED: `operations.py` `_paginate`(2306) `_continuation_source`(278) `_continuation`(2372) `resolve_project`(689) `_variable_metadata`(4123) `_remote_positive_int`(184) `_rfc3339`(302) `_validate_remote_ref`(168) `_validate_ref`(162) `_same_origin_url`(202) `_canonical_remote_url`(220) `_namespace_path`(248) `_remote_repo_path`(329) `_commit_sha`(316) `_as_object`(190) `_positive_bound`(174) `_normalize_user`(1147) `_normalize_merge_request`(1368) `list_merge_requests`(1445) `list_pipelines.normalize`(2598) `read_pipeline`(2639) `_collect_ci_variables`(4231) `_redact_text`(362) `_MAX_CI_VARIABLES` `_MAX_PROJECT_REFERENCE` `_MAX_COMMIT_TEXT` `_MAX_PIPELINES`; `PageResult`(models.py:59); `tools.py` `SCHEMAS` `_schema` `_PROJECT` `_PAGE_CONTINUATION` `invoke`; `descriptors.py` `_command` `_pos` `_opt` `_CONTINUATION` `_GITLAB_PROJECT_OPERATIONS` `_VALIDATION` `_add_validation` `_SCHEMA_DESCRIPTIONS`; `parser.py` `_argument_kwargs` `canonical_arguments`; `__init__.py` `_PLUGIN_SKILLS` `_WRITE_TOOLS` `register_skill` use; `plugin.yaml` 30 tools; four plugin SKILL.md; `skills/ericsson/gitlab/SKILL.md`; `test_gitlab_ci.py` `_operations` `_mock_project` `ORIGIN` `PROJECT_API`; `test_gitlab_plugin.py` `EXPECTED_TOOLS`+`==30`(122); `test_gitlab_skills.py` `PLUGIN_SKILLS` `_skill_contract` `_declared_tools`; `test_gitlab_exploration.py` `_operations` `_project` `_merge_request` `_user`; `build_migration_docs.py --check`; `build_catalog.py --check`; `validate_catalog.py`; YAML anchors `equivalent_read/renamed_read/safer_read/excluded`; `gitlab-tools.md` "18 bounded reads and 12 approval-gated writes"(82); `tests/test_task11_convergence.py`; `tests/fixtures/gitlab/ci/*`; Hermes `scripts/vendor-ericsson.mjs` (`ERICSSON_CAPABILITIES_DIR`, `resolveCleanSourceCommit`, `vendoredFrom`), `scripts/__tests__/vendor-ericsson.test.mjs`, `scripts/tool_search_livetest.py` (`setup_isolated_home`, `reset_module_state`, `_extract_bridge_calls`, `_redact_secrets`, `_count_assistant_turns`), `scripts/LIVETEST_README.md`, `tests/hermes_cli/test_ericsson_connector_distribution.py`, `tests/hermes_cli/test_ericsson_connector_surfaces.py`, `tests/plugins/workflow/test_ericsson_connector_toolsets.py`, `tests/cron/test_ericsson_gitlab_activity_digest.py`, `tests/ericsson_connector_source.py`, `capabilities/ericsson.json`, `capabilities/ericsson-vendored-paths.json`, `tools/registry.py dispatch`(1199), `model_tools.handle_function_call` bridge recursion(1339-1384)/`_authorized_registry_dispatch`(1462-1534), `tools/tool_search.py is_deferrable_tool_name/scoped_deferrable_names/resolve_underlying_call`, `tools/skills_tool.py skill_view`(1109), `hermes_cli/plugins.py register_skill/find_plugin_skill/list_plugin_skills/resolve_pre_tool_admission`, `toolsets.get_all_toolsets`, `tools/approval.py request_tool_approval` fail-closed branch(3785-3804, 4148), both `.venv/bin/python`, `.worktrees/` ignore rules, `scripts/out/` ignore.
MISSING (cited by plans, absent): descriptor table and `migration` tests in `tests/test_connector_cli_gitlab_port.py`; `PROJECT_API`/`_mock_project` in `test_gitlab_exploration.py` (to be created — twice); byte-parity assertion in `test_ericsson_connector_distribution.py`.
AMBIGUOUS: `WORKTREE_ROOT` (module has `_WORKTREE_ROOT`); `test_task11_convergence.py -k 'catalog or onboarding'` selection; "seven approved metadata fields" vs 8 keys; `<brand> gitlab mr list [project]` YAML replacement string not specified.
UNCHECKABLE: all GitLab response-field/parameter claims (Q1–Q7); ericsson worktree cleanliness by this reviewer (Q8).

## 16. Command / evidence ledger

Actually run (read-only): `git branch --show-current`, `git rev-parse HEAD`, `git rev-parse origin/base`, `git status --short`, `git worktree list` (Hermes); `cat .git/HEAD`/`refs/heads/main`, `ls .git/worktrees` (Ericsson, via file reads); `sha256sum` on the six inputs; `git ls-files` on the six inputs; `git diff --stat HEAD -- docs/superpowers plugins/ericsson-gitlab plugins/ericsson-connector-cli skills/ericsson capabilities` (empty); `git log -1` for `69a338ba29` and `4e8c3d61d2`; `git merge-base --is-ancestor 69a338ba29 HEAD` (true); `ls -la .venv/bin/python` (Hermes; blocked at the symlink target, which proved the link resolves to a uv CPython 3.11.15); `wc -l` and `ls` inventories; file reads and `rg` searches listed in §15.
Inspected only (not run): every plan `pytest`/`node --test`/`vendor-ericsson.mjs`/`build_*`/`validate_catalog` command; `git worktree add`; live-model runs.
Denied/unavailable: `git -C <ericsson>` commands, `shasum`/`python3 -c`/`openssl` (replaced by `sha256sum`), `curl` to docs.gitlab.com, `git log` on the harness files.

IMPLEMENTATION MUST NOT BEGIN UNTIL CRITICAL AND IMPORTANT PLAN FINDINGS ARE RESOLVED.
