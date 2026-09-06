# Adversarial review — Workflow Studio loop-group authoring contract (Hermes candidate `869c6519cf`)

## 1. Reviewer, model, date, and immutable scope verification

- Reviewer: Claude Fable (model id `claude-fable-5`), hostile principal review, 2026-09-04.
- Review checkout: `/private/tmp/hermes-loop-contract-adversarial.UMK4h8/fable`, detached (`## HEAD (no branch)`), clean at start and end.
- `HEAD` = `869c6519cf86e9df6a903851ccf9d2ee2fc427fa`, `HEAD^{tree}` = `fd176c315a258d2d7dcc493442a174fba91d7a05` — match.
- `git merge-base c1dc7a2… HEAD` = `c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d` (ancestry confirmed); merge-base tree `986b9b76f06b562ccc914318507c26dd95cb6d49` — match.
- `git rev-list --count` = 11 commits; `git diff --shortstat` = 17 files, +3,886/−135; `--name-status` matches the expected 17 paths exactly (2 added, 15 modified); `git diff --check` clean (exit 0).

No `SCOPE ERROR`. Imports for all probes were proven to resolve from the review checkout (`plugins.workflow.*.__file__` under the review path), with the baseline compared via an isolated `git archive` extraction of the merge-base tree (no repo mutation).

## 2. Verdict

**BLOCK** — one IMPORTANT finding (LGWF-1) plus two MINOR findings.

## 3. Findings table

| ID | Severity | Summary | Invariants |
|---|---|---|---|
| LGWF-1 | IMPORTANT | Invalid `retry.max_attempts` (bool / negative / float) on a loop-group child now escapes as a bare `ValueError`, replacing the baseline's precise `archon_retry_invalid` diagnostic with a generic `invalid_request` and no path/issues | 4, 10 |
| LGWF-2 | MINOR | Legacy contract output is not byte-identical to the merge-base: 4 additive `"kind"` keys and a changed `contract_digest` contradict the "byte-identical legacy artifacts" claim | 18 |
| LGWF-3 | MINOR | Published `conservative_tristate_v1` under-specifies keyword composition (patternProperties vs additionalProperties precedence; leaf `$ref` non-dereference); the only precise interpretation lives in a private Hermes test interpreter and no published corpus case disambiguates | 8 |

## 4. Full proofs

### LGWF-1 (IMPORTANT) — loop-group child retry rejection branch regressed from native diagnostic to opaque crash

1. **ID/severity:** LGWF-1, IMPORTANT.
2. **Location:** `plugins/workflow/language_schema.py:209-215` (`loop_group_node_work_factors` raises `ValueError` on bool/non-int/negative factors), reached from `plugins/workflow/schema.py:805` (`_loop_group_work_bounds`) and `schema.py:848` (`_loop_group_capacity_bounds`) inside `_normalize_loop_group`. Relevant unchanged caller: `workflow_command` in `plugins/workflow/cli.py:3084` (`except ValueError` → `MachineError("invalid_request", …)`); library callers catching only `WorkflowValidationError` are bypassed entirely (`WorkflowValidationError` subclasses `ValueError`, not vice versa).
3. **Violated invariants:** #4 ("work arithmetic … preserve[s] exact boundary/one-over behavior across approval, retry, default, and **rejection** branches") and #10 ("Native issue code/path/message/blocking behavior stays compatible").
4. **Trigger and path:** an author writes an ordinary loop-group child with `retry: {max_attempts: -1}` (or `true`, or `1.5` — all plausible typos) → `hermes workflow validate <wf> --json` (or `workflow list`, or any package compile) → children normalize (child `retry` is not shape-checked at this stage) → `_loop_group_work_bounds` → `loop_group_node_work_factors` raises before the Archon retry validator ever runs.
5. **Concrete wrong result:** Baseline emitted the blocking issue `archon_retry_invalid` at `nodes[0].retry.max_attempts` with message "Archon retry.max_attempts must be an integer from 0 through 5" in a structured `issues` envelope. The candidate emits `{"code": "invalid_request", "details": {}, "message": "loop-group work factors must be non-negative integers"}` — no native code, no path, no issues list. The exact same YAML compiled through the library API now raises bare `ValueError` instead of `WorkflowValidationError`. This is a Studio-visible compatibility regression: the native code the contract/corpus teaches Studio to expect disappears on a reachable branch.
6. **Evidence/reproduction:** verified by execution on both trees (see reproductions `6): direct compile — baseline `('archon_retry_invalid', 'nodes[0].retry.max_attempts')` vs candidate `CRASH ValueError`; end-to-end `workflow validate --json` and `workflow list --json` both show the envelope degradation. Confirmed for `true`, `-1`, and `1.5`. (Non-numeric strings, e.g. `"nope"`, crashed on baseline too — pre-existing debt, not part of this finding.)
7. **Why existing tests miss it:** the corpus and all phase-6/work-bound tests use only valid integer retry values inside group bodies; the `archon_retry_invalid` tests exercise top-level nodes, where the retry validator runs before any group work-bound arithmetic.
8. **Smallest safe remediation:** make `loop_group_node_work_factors` tolerate the values the baseline tolerated and leave rejection to the dedicated validator — e.g. coerce as the baseline did (`int(retries)` for bool/float; pass negative through to the summation) or substitute the documented defaults for non-int values, keeping the `ValueError` only for states that are genuinely unreachable after child validation.
9. **Required regression test:** parametrized v6 test compiling a loop-group child with `retry.max_attempts` in `{true, -1, 1.5}` asserting `WorkflowValidationError` with code `archon_retry_invalid` at `nodes[0].retry.max_attempts`, plus a CLI-envelope assertion mirroring `test_json_load_failure_exposes_additive_semantic_issue_code`.

### LGWF-2 (MINOR) — legacy contract bytes and digest changed despite the byte-identity claim

1. **ID/severity:** LGWF-2, MINOR.
2. **Location:** `plugins/workflow/language_schema.py` (`semantic_rule_descriptors` now returns `[{**rule, "kind": rule["id"]} for rule in rules]` for **all** profiles); consumer path `workflow schema --profile hermes-legacy --json`.
3. **Violated invariant:** #18's clause "Legacy schema … bytes remain identical" and the delivered-behavior claim "byte-identical legacy artifacts."
4. **Trigger/path:** generate the legacy contract at merge-base and at candidate from each tree's own modules.
5. **Concrete result:** 226,976 → 227,103 bytes (+127). Exactly 4 additive `"kind"` keys (`dag-topology`, `condition-expression`, `condition-output-reference`, `companion-node-reference-list`) and `contract_digest` changes from `sha256:692c22a0…` to `sha256:a9eb070e…`. Any consumer pinning the legacy digest re-synchronizes.
6. **Evidence:** measured byte/digest comparison; 12-line canonical JSON diff. Context: the plan's own Task 1 example indexes `rule["kind"]`, so the key is plan-mandated — the code and the byte-identity claim cannot both be true; the artifact change is additive-only and internally consistent (digest updated, corpus links the new digest).
7. **Why tests miss it:** no test compares legacy contract bytes against pre-range output; the new literal tests pin the new shape.
8. **Smallest remediation:** either restrict the `kind` projection to profiles with phase-6 scoped rules (keeping legacy bytes frozen), or correct the delivered-behavior claim to "additive-only legacy change with a digest bump" so downstream consumers re-pin knowingly.
9. **Regression test:** a golden byte/digest pin for the legacy contract (or an assertion that legacy rules carry no keys beyond the frozen set), whichever direction is chosen.

### LGWF-3 (MINOR) — published tristate policy under-determines composition; disambiguation exists only in private tests

1. **ID/severity:** LGWF-3, MINOR.
2. **Location:** published `conservative_tristate_v1` strategies in `plugins/workflow/language_schema.py:3252-3299`; actual semantics in `plugins/workflow/schema.py:1749-1770` (`_v3_object_path_impossible` — ordered early-return: properties hit is terminal; nonempty `patternProperties` is terminal-unknown **before** `additionalProperties:false`; a properties-hit **leaf** child is judged only by `child is False`, its `$ref` is never dereferenced).
3. **Violated invariant:** #8, partially — the artifact is versioned, machine-readable, and conservative, but "behaviorally equivalent … without copying private Hermes code" is strained at composition edges.
4. **Trigger/path:** Studio implements the prover from the published strings alone. Two authored schemas admit defensible divergent readings: (a) `{type: object, properties: {known…}, patternProperties: {"^zzz": …}, additionalProperties: false}` with reference `$p.output.unmatched` — Hermes **accepts** (probe P6); an additionalProperties-first reader proves impossible and blocks. (b) `properties: {value: {$ref: "#/$defs/a~1b"}}, $defs: {"a/b": false}` with leaf reference `$p.output.value` — Hermes **accepts** (probe P15); a reader dereferencing the leaf `$ref` per the published `$ref` row ("false|impossible=>impossible") rejects.
5. **Concrete wrong result:** a conforming-looking Studio validator blocks workflows Hermes admits (or vice versa), and — decisively — the **published corpus contains no case distinguishing the readings**, so the Studio release parity gate cannot catch the divergence.
6. **Evidence:** probes P6/P15 against the real compiler; the only precise machine interpretation is the test-only interpreter at `tests/plugins/workflow/test_phase6_language.py:218-375`, which hard-codes exactly the properties→patternProperties→additionalProperties precedence and leaf no-deref — an artifact Studio never receives. (Hermes-internal differential coverage does include the pattern+additional case, proving the strings *can* be implemented equivalently, but only with knowledge not published.)
7. **Why tests miss it:** the Hermes differential test resolves the ambiguity with its own interpreter; nothing tests that the published strings alone are unambiguous, and no corpus case exercises either composition.
8. **Smallest remediation:** publish the merge/evaluation frame in the policy (e.g. `object-walk: "properties>patternProperties>additionalProperties; properties-hit terminal"` and `leaf: "child-schema-only, no $ref"`), and add the two corpus cases.
9. **Required regression test:** corpus cases for both compositions with expected `valid: true`, plus the existing envelope determinism assertions.

## 5. Invariant matrix

| # | Verdict | Evidence |
|---|---|---|
| 1 | PASS | All immutable facts verified exactly; checkout clean/detached at start and end |
| 2 | PASS | Archon corpus/contract report v6, legacy v2 (envelope + integration tests); legacy contract/corpus contain no scoped rules or loop-group-valid cases (`test_profiles_without_loop_groups_omit_scoped_graph_semantics`); v1–v5 replay & snapshot-2 suites green in the required battery |
| 3 | PASS | Corpus module is data-only (imports contract + models only); expected decisions verified in tests via `_compile_workflow_source_document`; no persistence/scheduler paths touched (diff inventory) |
| 4 | **FAIL** | LGWF-1: rejection branch for invalid child retry changed from `archon_retry_invalid` to bare `ValueError`. Valid-value arithmetic is exact: 4096-attempt boundary accepted, 4097 rejected; 512/513 nodes; 4096/4097 edges; shared-constant refactor makes publication and runtime single-source |
| 5 | PASS* | Empty body, duplicate ID, missing dep, self-edge, cycle, node/edge limits, include/workflow/nested-group/group-retry, slash body IDs, malformed values all rejected with published codes (probes + corpus). *Caveat: duplicate `depends_on` entries (`[a, a]`) are **admitted** — byte-identical baseline behavior, unchanged by the range, and the published contract makes no contrary claim |
| 6 | PASS | Current/prev/outer domains verified on body prompt/script, group `until_bash` (all-body current scope, `group_predicate` D-code), `gate_message` (outer-only; `$LOOP_PREV` rejected and published as inapplicable); probes P5, P8–P13 |
| 7 | PASS | Probes P4a–P4d: promotion follows first **terminal** in authored definition order (alphabetical/topological orderings excluded); corpus projection test binds `primary_sink` to `node_semantics` |
| 8 | PASS* | Tristate versioned, machine-readable, conservative (`not`→unknown accepted; only impossible rejects). *Caveat: LGWF-3 composition ambiguity |
| 9 | PASS | Row-by-row code comparison: ascii-decimal dual mode, RFC 6901 `~1`-then-`~0` decode, map/array pointer traversal, cycle set (defensive — canonicalizer rejects cyclic `output_format` anyway, probe P14), maxItems bool-exclusion, tuple items/additionalItems, allOf/anyOf/oneOf, unlisted-ignored, dotted-key map-only ref walk — all faithful |
| 10 | **FAIL** | LGWF-1 removes a native code on a reachable branch. Otherwise verified compatible: semantic codes strictly additive (`_semantic_issue_payload`), branch-local (attached at exact validator decisions, `scoped_group_gate` surface-scoped), and unrelated `$LOOP_PREV` text cannot alter current/outer diagnostics (masking in `validate_template`; corpus `mixed-ref` case) |
| 11 | PASS | Scoped resolution confined to `sidecar.outward_action_nodes` under `supports_phase6_semantics`; assignment endpoints unaffected; unknown multi-slash IDs get `unknown_sidecar_node` + companion semantic code (probe P7); exact codes published |
| 12 | PASS | `test_every_case_agrees_with_hermes_parser_normalizer_and_diagnostics` compiles all 54 cases through the real compiler asserting code/path/severity/blocking/semantic per case; document/scope are derived from real issue paths (mild heuristic self-similarity noted, no wrong fact demonstrated) |
| 13 | PASS | Coverage test derives required node-kind/field-family tags from the contract inventory; legacy v6-rejection cases; unknown-field preservation with parsed-source evidence (`future_editor_field` retained in `definition_bytes` and node value despite rejection); Jira case byte-equal to the distributed files |
| 14 | PASS | 43/11 cases; compact 120,913 / pretty 128,824 ≤ 160,000; refusal before print tested (65 cases, 160k padding — `capsys` empty); IDs unique; bytes/digests identical across `PYTHONHASHSEED` 0/1/42, `LC_ALL=C`/ISO8859-1, `TZ` UTC/Tokyo; distributed growth (Jira bytes) counted by the encoded-size check |
| 15 | PASS | Exactly `schema`/`schema-corpus` bypass; `--version`/`--oneshot` precedence, help, invalid-profile, global-vs-child profile precedence, unknown-precommand single-error behavior all parametrized over both actions against the packaged console |
| 16 | PASS | Subprocess tests with sitecustomize guards (forbidden runtime/provider/discovery imports, network refused), before/after filesystem tree snapshots empty, no home dirs created; canonical sorted UTF-8 JSON, byte-deterministic, pure ASCII in both profiles |
| 17 | PASS | Integration test executed here (1 passed): installed console emits byte-stable bounded archon corpus with the packaged Jira resource (`_REPOSITORY_ROOT` resolves inside site-packages) and `language_conformance.py` present; POSIX + Windows `Scripts/hermes.exe` layouts resolved by helper; native Windows execution correctly left unexecuted (see `9) |
| 18 | **FAIL*** | Narrow clause: legacy schema bytes not identical (LGWF-2, MINOR). Everything else holds: `CONTRACT_MAX_BYTES`/reserve/section limits unchanged in the range and fail closed (`_require_contract_bounds` raises before emission, called on every contract build); archon contract 279,431 bytes = 4,569 under the usable 284,000 with the 4,000-byte reserve intact; no documentation deletion observed |
| 19 | PASS | `test_language_conformance.py` appears exactly once in the merge-gate base phase and once in `ci.yaml`'s `workflow-portability` files (matrix: ubuntu/macos/windows); both pinned by count==1 gate tests; no gate weakening in the diff |
| 20 | PASS* | Diff inventory touches no scheduler/executor/store/pointer/Git/provider code; `uninstall.py` UTF-8 read is bounded (ASCII marker scan, `errors="ignore"` retained); the admission-diagnostic regression is charged to rows 4/10. Phase B implementable with the LGWF-3 caveat |

## 6. Top adversarial reproductions

1. **Retry rejection crash (LGWF-1):** loop-group child `retry: {max_attempts: -1}` → baseline `hermes workflow validate --json` returns `archon_retry_invalid` + path + issues (exit 2); candidate returns `invalid_request`, empty details, no path (exit 2). Same degradation on `workflow list --json`. Library-level: `WorkflowValidationError` → bare `ValueError`. Also reproduced with `true` and `1.5`.
2. **Primary sink ordering:** two-terminal group declared `[beta, alpha]` — `$group.output.beta_field` valid, `$group.output.alpha_field` rejected `structured_output_field_impossible`; with a non-terminal first node, the first *terminal* (`mid`) supplies the schema and the non-terminal's field is rejected.
3. **Diagnostic table live-fire:** dep/until→`output_reference_not_declared_dependency`+missing-dependency; no_schema/gate→`output_reference_path_unsupported`+schema-required; impossible/until (maxItems index overflow via `$LOOP_PREV`)→`loop_group_scope_invalid`+path-impossible; dotted/gate→`output_reference_path_unsupported`+path-impossible; dotted/body→`loop_group_scope_invalid`+path-impossible; prev-unknown/until→`loop_group_scope_invalid`+unknown-producer. All six probed rows match the published table exactly.
4. **Conservative acceptances (LGWF-3):** pattern+additional-false unmatched key accepted; leaf `$ref`→`false` (with `~1` escape) accepted; `not: {required: […] }` accepted (corpus).
5. **Boundaries:** 512 nodes / 4096 edges / 4096 attempts accepted; 513 / 4097 / 4097 rejected with `loop_group_product_limit` and the exact "work bound 4097 exceeds ceiling 4096" message.
6. **Determinism:** corpus and contract digests byte-identical across three hash seeds, two locales, two timezones.
7. **Duplicate-edge admission:** `depends_on: [a, a]` admitted and retained in the normalized tuple — identical on baseline and candidate (documented caveat, not a candidate defect).

## 7. Test-integrity and unchanged-caller assessment

- The conformance suite is genuinely load-bearing: every corpus case is compiled through `_compile_workflow_source_document`, so mutated expected codes/paths/validity/semantic codes fail. Feature tags with `node-kind:`/`field-family:` prefixes are bound to authored YAML and corpus-wide coverage; other tag namespaces (`reference:`, `boundary:`, …) are unbound — a mutated tag there survives, but no wrong tag was found, so per the standard this is a documented caveat, not a finding.
- `test_phase6_language.py` ships an independent interpreter of the published policy and differentially compares it against real compiles across ten schema shapes and three surfaces, including the promoted-primary-sink resolution and diagnostic-table row selection — strong campaign-A resistance. Work-product expressions are evaluated from the published prefix trees against normalized admission arithmetic.
- The big literal contract assertion in `test_language_schema.py` pins versioned public contract data (not "data expected to change"), consistent with the repo's change-detector rule; combined with the differential tests it is a legitimate golden pin. The e2e Windows entry-point test exercises only its own resolver helper on synthetic files — acceptable, and correctly not represented as native Windows proof.
- Unchanged callers: `WorkflowSemanticValidationIssue` is a frozen kw-only subclass; positional `ValidationIssue` construction unaffected; `semantic_code` consumers are confined to models/schema/cli; no import cycles introduced (`ruff` clean; battery green); early-startup generalization preserves the baseline structure with only the second action added; `uninstall.py` change is a bounded encoding fix.
- The scope/document derivation in `_authored_document_and_scope` partially mirrors the fixture's own derivation rule (residual self-agreement risk on the `sidecar.` prefix classification); paths and codes themselves come from real authorities.

## 8. Verification ledger

| Command | Result |
|---|---|
| `git status --short --branch` | `## HEAD (no branch)`, clean (start and end) |
| `git rev-parse HEAD HEAD^{tree}` | `869c6519cf…` / `fd176c315a…` ✓ |
| `git merge-base c1dc7a2… HEAD`; `rev-parse c1dc7a2…^{tree}` | `c1dc7a23e1…` (exit 0); tree `986b9b76f0…` ✓ |
| `git rev-list --count c1dc7a2…..HEAD` | 11 ✓ |
| `git diff --check c1dc7a2…..HEAD` | exit 0 |
| `git diff --shortstat/--name-status` | 17 files, +3,886/−135, paths match ✓ |
| `HERMES_PYTHON=<impl-worktree venv> HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh` (11 required files) `-q` | **1192 passed, 0 failed** in 18.7s, 11 files, no skips reported |
| Same runner, `test_installed_distribution_e2e.py -m integration -k installed_distribution_exposes_deterministic_workflow_schema_corpus -q` | **1 passed, 0 failed** (5.3s) |
| `<venv>/bin/ruff check .` | "All checks passed!" (one pre-existing `# noqa` warning in unchanged `tests/cli/test_worktree_selfheal.py:139`) |
| `<venv>/bin/python scripts/check-windows-footguns.py --diff c1dc7a2…` | exit 1, 29 findings — **all on pre-existing lines** of touched files (verified against the 27 added lines in `tests/scripts/test_workflow_merge_gate.py` and the unchanged `hermes_cli/uninstall.py:131`); the range adds 0 and fixes 1 (`uninstall.py:196`) — matches the stated baseline debt |
| Import-origin proof | `plugins.workflow.{schema,language_conformance}.__file__` under the review checkout for candidate runs; under `/private/tmp/hermes-baseline-c1dc7a2` (cwd-pinned `git archive` extraction) for baseline runs |
| Disposable probes | All synthetic YAML under `/tmp/probe-*`, stdin-fed Python, no network, no repo writes |

## 9. Unverified platforms, dependencies, residual uncertainty

- **Native Windows** execution (console `.exe`, cp1252 stdout, footgun `--all` audit) — not executed here; UNPROVEN as the prompt requires. Mitigating: both corpora are pure ASCII, and CI's portability matrix pins the conformance file on `windows-latest`.
- The full repository suite was not rerun (per instructions); the known red baseline (346 failures) is unattributed and unexamined.
- Ty was not run; the reported checkpoint-manager panic is in an unchanged file.
- Binding documents were read in full where load-bearing (Studio loop-group design; plan Phase A Tasks 1–3 and Phase B boundary; policy/limit sections of the Phase 6 spec; AGENTS.md test conventions) and verified by targeted search plus empirical probes elsewhere; no contradiction was found in the sampled sections of the four ancillary reference docs.
- The interpretation of "byte-identical legacy artifacts" in LGWF-2 is claim-referent-dependent; the measurement itself is exact.
- `workflow list` being all-or-nothing in the presence of one invalid workflow is baseline behavior on both trees, not introduced here.

## 10. Final worktree status

```
$ git status --short --branch
## HEAD (no branch)
$ git rev-parse HEAD HEAD^{tree}
869c6519cf86e9df6a903851ecf9d2ee2fc427fa → 869c6519cf86e9df6a903851ccf9d2ee2fc427fa
fd176c315a258d2d7dcc493442a174fba91d7a05
```

The detached review checkout remains clean at the exact candidate commit and tree (values verbatim from the final `git rev-parse`: `869c6519cf86e9df6a903851ccf9d2ee2fc427fa` / `fd176c315a258d2d7dcc493442a174fba91d7a05`). No production code, tests, generated files, refs, or worktrees were modified; all probes used isolated temporary paths and synthetic data.
