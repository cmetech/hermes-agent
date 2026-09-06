# Workflow Studio loop-group authoring contract review

## Verdict: FAIL

Four candidate-range defects qualify as IMPORTANT. No CRITICAL or MINOR findings were found.

### LGC-001 — IMPORTANT — Reference-capable body fields are not machine-readable

Production evidence:

- The published strict rule enumerates only the basic execution fields in [language_schema.py](/private/tmp/hermes-loop-contract-adversarial.UMK4h8/codex/plugins/workflow/language_schema.py:3684).
- The scoped rule instead applies generically to an entire body node in [language_schema.py](/private/tmp/hermes-loop-contract-adversarial.UMK4h8/codex/plugins/workflow/language_schema.py:3497).
- The unchanged runtime authority selectively interpolates additional fields—`systemPrompt`, agent `description`/`prompt`, and hook response strings—in [schema.py](/private/tmp/hermes-loop-contract-adversarial.UMK4h8/codex/plugins/workflow/schema.py:2199).

Violated invariants: 6, 13, 20.

A bounded probe placed `$producer.output` in different fields of the same body-node shape without declaring the dependency:

```text
systemPrompt          REJECTED  loop_group_scope_invalid
agents.*.description  REJECTED  loop_group_scope_invalid
model                  ADMITTED
agents.*.model         ADMITTED
```

The contract offers no machine-readable way to distinguish these fields. A Studio consumer that follows the explicit `strict-output-reference.field_paths` misses real loader errors in `systemPrompt`, agents, and hooks. A consumer that interprets `nodes[].loop_group.nodes[]` as “scan every string” falsely rejects literal fields such as `model`.

Therefore full visual reference validation requires Studio to reproduce the private `_interpolated_node_templates()` inventory, contrary to the claimed no-Hermes-specific-guesses boundary.

### LGC-002 — IMPORTANT — Scoped producer collision precedence is omitted

Production evidence:

- Current and outer producer domains are both published for body surfaces, but no overlap or resolution order is defined in [language_schema.py](/private/tmp/hermes-loop-contract-adversarial.UMK4h8/codex/plugins/workflow/language_schema.py:3496).
- The unchanged loader gives a matching body ID unconditional precedence over an outer dependency in [schema.py](/private/tmp/hermes-loop-contract-adversarial.UMK4h8/codex/plugins/workflow/schema.py:2054).
- The same descriptor also omits the required first-iteration `$LOOP_PREV` behavior; the unchanged runtime returns the empty string for a known whole-output reference in [resources.py](/private/tmp/hermes-loop-contract-adversarial.UMK4h8/codex/plugins/workflow/resources.py:879).

Violated invariants: 6, 13, 20.

The probe used both a root node and a body node named `producer`. The group depended on the root producer, while the consumer depended on the body producer. Their structured schemas deliberately disagreed:

```text
$producer.output.body_only   ADMITTED
$producer.output.outer_only  REJECTED
                             scoped-reference-structured-path-impossible
```

Both published producer-domain predicates match the same unqualified identifier, but only private loader code establishes that the body schema wins. A contract-only consumer can therefore select the outer schema and accept a document Hermes rejects.

The corpus has no root/body identifier-collision case and does not publish or assert the iteration-one empty-string result.

### LGC-003 — IMPORTANT — Any slash-containing companion value receives a scoped semantic code

Production evidence:

- The contract says scoped companion syntax is exactly `group/child` in [language_schema.py](/private/tmp/hermes-loop-contract-adversarial.UMK4h8/codex/plugins/workflow/language_schema.py:3231).
- The implementation classifies an unknown value as scoped merely when it contains `/` in [schema.py](/private/tmp/hermes-loop-contract-adversarial.UMK4h8/codex/plugins/workflow/schema.py:2744).

Violated invariants: 10, 11, 13.

Observed results:

```text
group/missing         unknown_sidecar_node  scoped-companion-reference-unknown-node
group/child/extra     unknown_sidecar_node  scoped-companion-reference-unknown-node
unrelated/slash/value unknown_sidecar_node  scoped-companion-reference-unknown-node
```

At the merge base, `group/child/extra` produced the same native code and path but no semantic code. The false portable diagnostic is introduced by this candidate.

This conflates an exact but unknown `group/child` reference with arbitrary malformed slash-delimited text. Studio cannot use the semantic code to decide whether to repair a missing group/child or report malformed syntax. Assignment fields were separately probed and correctly received no scoped semantic code.

### LGC-004 — IMPORTANT — Legacy contract bytes and digest change

Production evidence:

- Every semantic rule, including legacy rules, gains a `kind` property unconditionally in [language_schema.py](/private/tmp/hermes-loop-contract-adversarial.UMK4h8/codex/plugins/workflow/language_schema.py:3748).
- `workflow schema` emits that contract directly through [schema_cli.py](/private/tmp/hermes-loop-contract-adversarial.UMK4h8/codex/plugins/workflow/schema_cli.py:58).

Violated invariant: 18.

A merge-base archive and the candidate were imported in separate processes using the same interpreter:

| Artifact | Canonical bytes | Canonical SHA-256 | Embedded contract digest |
|---|---:|---|---|
| Merge base | 226,976 | `f4de08444f110fb8c4e2b36d9246df4194b8a70dde8bae495a381d757fdf6e07` | `sha256:692c22a06fa61aa1949d25fa4ce12b3345cac3fadd7f562558c3f5394d35617f` |
| Candidate | 227,103 | `1c5c01cbc9160b5d749724a0095ec87a1e1ce579b0b8f5207f2392d7b8b94124` | `sha256:a9eb070e857ae859a486fa8862025528c9c0dfe93d2258b0340be71dbd3516d7` |

Normalizer selection remains v2 and no scoped rule is added to legacy, but the artifact is not byte-identical as required.

## Locked invariant matrix

| # | Result | Evidence |
|---:|:---:|---|
| 1 | PASS | Exact detached HEAD/tree, merge base, 11 commits, 17 paths, numeric diff, clean `diff --check`, and final clean status verified. |
| 2 | PASS | Archon selects v6; legacy selects v2; scoped rules/cases are absent from legacy. Supplemental snapshot run passed all snapshot tests after isolating the sandboxed boot-time call. |
| 3 | PASS | No persisted graph, scheduler, executor, store, or second validator was added. Runtime field authorities remain shared. |
| 4 | PASS | Work constants and factors feed runtime and publication. Ninety approval/retry/default/loop combinations were byte-identical between merge base and candidate; 4,096/4,097 cases behave correctly. |
| 5 | FAIL | Duplicate dependencies are admitted: `depends_on: [producer, producer]` survives normalization. This is byte-identical merge-base behavior, not charged as a candidate finding. Other tested topology failures reject. |
| 6 | FAIL | LGC-001 and LGC-002: field applicability, collision precedence, and iteration-one semantics are incomplete. |
| 7 | PASS | Runtime and contract use first terminal in authored definition order; corpus projection identifies `first-terminal`, independently of topology order. |
| 8 | PASS | Versioned tri-state policy accepts `possible`/`unknown` and rejects only `impossible`; independent test interpreter agrees with the loader. |
| 9 | PASS | Numeric dual modes, pointers, cycles, object/array keywords, combinators, unknown keywords, and dotted-key behavior are published and exercised. |
| 10 | FAIL | LGC-003 publishes a false branch-specific semantic code for malformed slash values. Native code/path/message behavior otherwise remains compatible. |
| 11 | FAIL | Assignment fields remain excluded, but arbitrary slash-delimited outward-action values are incorrectly classified as scoped references. |
| 12 | PASS | Corpus data is non-executable; every included case is compiled through Hermes and independently checked for validity, native/semantic code, path, scope, severity, blocking state, and projections. |
| 13 | FAIL | The corpus lacks the real surface-discrimination, root/body collision, malformed companion-format, and duplicate-dependency cases exposed above. |
| 14 | PASS | 11 legacy and 43 Archon case IDs are unique and deterministic. Exact 64/65-case and UTF-8 byte bounds fail closed before stdout. |
| 15 | PASS | Exact schema/corpus actions use early startup. Profile, help, version, oneshot, and ordinary dispatch tests pass apart from the documented sandboxed ordinary-doctor child. |
| 16 | PASS | Installed POSIX execution was deterministic, emitted no stderr, loaded from the installed target, and created neither `HOME` nor `HERMES_HOME`. |
| 17 | UNPROVEN | Offline POSIX wheel build/install/console execution passed. Resolver fixtures cover both directory layouts, but native Windows execution did not occur. |
| 18 | FAIL | LGC-004: legacy contract bytes and digest changed. Envelope and size limits themselves remain fail-closed. |
| 19 | PASS | The file appears once in the base gate and once in one matrix slice executed for Ubuntu, macOS, and Windows. |
| 20 | FAIL | No scheduler/executor/store effects changed, but LGC-001/LGC-002 force Phase B to make Hermes-specific guesses. |

## Top adversarial reproductions

1. Root/body identifier collision:

   - Root and body both named `producer`.
   - Both are legitimate dependencies.
   - Body schema contains only `body_only`; root schema contains only `outer_only`.
   - Hermes admits `body_only` and rejects `outer_only`; the contract publishes no reason to select the body producer first.

2. Reference-surface ambiguity:

   - `systemPrompt: Use $producer.output` is rejected for missing dependency.
   - `model: $producer.output` is admitted.
   - Both lie under the published whole-node scoped applicability, while neither appears in the explicit strict field list.

3. Malformed companion value:

   - `outward_action_nodes: [group/child/extra]`
   - Merge base: native `unknown_sidecar_node`, no semantic code.
   - Candidate: native `unknown_sidecar_node` plus `scoped-companion-reference-unknown-node`.

4. Legacy artifact differential:

   - Same profile and normalizer v2.
   - Same four semantic rules.
   - Candidate adds `kind` to all four, changing bytes and digest.

5. Inherited duplicate-edge admission:

   - `depends_on: [producer, producer]`
   - Both merge base and candidate admit and preserve both entries.
   - This fails locked invariant 5 but is not candidate-range causality.

## Test integrity and unchanged callers

The conformance tests are materially independent: they parse authored YAML, call `_compile_workflow_source_document`, derive scope from actual authored nodes, compare native and semantic diagnostics separately, validate source-byte preservation, and reconstruct projections from normalized objects.

The structured-path tests contain an independent interpreter for the published prefix expressions and proof policy. Work-factor differential testing found no merge-base runtime drift across 90 combinations.

The principal mutation blind spots correspond directly to the findings:

- No test distinguishes interpolated body strings from literal string fields.
- No case overlaps a body ID with an outer dependency ID.
- No companion case uses more or fewer than one slash.
- No test pins legacy contract bytes against the merge base.
- Corpus ordering is deterministic but not pinned to a golden digest/order fixture.

The new gate tests examine operational configuration rather than comments or implementation prose. The base-gate fixture could not reach its command stub here because `node` is unavailable; direct inspection still confirmed exactly one occurrence in both configured locations.

Relevant unchanged runtime behavior was traced through template enumeration, scoped reference selection, primary-sink promotion, first-iteration output resolution, loop-group normalization, snapshot readers, and sidecar validation. The 90-case merge-base differential found no work-arithmetic change. No scheduler, executor, persistence, Git, pointer-move, provider, or outward-effect file changed.

## Verification ledger

### Immutable scope

All exited 0:

```bash
git status --short --branch
git rev-parse HEAD HEAD^{tree}
git merge-base c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d HEAD
git rev-list --count c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD
git diff --check c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD
git diff --name-status c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d..HEAD
```

Observed:

```text
HEAD:       869c6519cf86e9df6a903851ccf9d2ee2fc427fa
Tree:       fd176c315a258d2d7dcc493442a174fba91d7a05
Merge base: c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d
Commits:    11
Paths:      17
Diff:       3,886 insertions, 135 deletions
diff --check: clean
```

Imports were confirmed to resolve from:

```text
/private/tmp/hermes-loop-contract-adversarial.UMK4h8/codex/plugins/workflow/schema.py
```

### Required focused Python run

The exact requested command exited 1:

```bash
HERMES_PYTHON=/Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent/.worktrees/workflow-studio-loop-group-contract/.venv/bin/python \
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_language_conformance.py \
  tests/plugins/workflow/test_phase3_language.py \
  tests/plugins/workflow/test_phase4_language.py \
  tests/plugins/workflow/test_phase4_snapshot.py \
  tests/plugins/workflow/test_phase5_language.py \
  tests/plugins/workflow/test_phase5_provider_snapshot.py \
  tests/plugins/workflow/test_phase6_language.py \
  tests/plugins/workflow/test_language_snapshot.py \
  tests/plugins/workflow/test_cli.py -q
```

Result:

```text
1,093 passed
99 failed
0 retries
```

All 99 failures were caused by the sandbox denying the unchanged macOS `sysctl` call reached through `psutil.boot_time()`:

- `test_phase4_snapshot.py`: 8
- `test_phase5_provider_snapshot.py`: 9
- `test_language_snapshot.py`: 63
- `test_cli.py`: 19

The seven other files passed, including 703 language-schema tests, 78 Phase 6 tests, and 14 conformance tests.

A supplemental in-process probe replaced only `psutil.boot_time()` with a fixed synthetic value. It produced:

```text
237 passed, 1 failed, 46 warnings
```

The sole remaining failure launched an unpatched ordinary-doctor subprocess and received machine exit 8 rather than 7 from the same sandbox condition. No candidate snapshot failure remained.

### Required installed-distribution run

The exact requested command exited 1 before executing the selected test:

```bash
HERMES_PYTHON=/Users/coreyellis/Developer/personal/github.com/cmetech/hermes-agent/.worktrees/workflow-studio-loop-group-contract/.venv/bin/python \
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_installed_distribution_e2e.py \
  -m integration -k \
  installed_distribution_exposes_deterministic_workflow_schema_corpus -q
```

Result:

```text
0 executed
8 deselected
1 fixture setup error
```

`uv build` was denied access to `/Users/coreyellis/.cache/uv/sdists-v9/.git`. Supplying a temporary `UV_CACHE_DIR` did not reach the child because the canonical runner intentionally strips that variable.

A supplemental offline build used the already-installed matching `setuptools 83.0.0` and `wheel 0.46.3` with `--no-build-isolation`. Wheel build, target install, prefix install, and POSIX console execution passed. The installed corpus command ran twice:

```text
return code: 0
stderr:      0 bytes
stdout:      120,914 bytes
SHA-256:     0acb40f34041b0925dd56efb3bf6ad9cfe175b3ecd815c77750fb02a95050bd2
identical:   true
module:      /private/tmp/hermes-loop-review-wheel-site/plugins/workflow/language_conformance.py
```

Two native-layout resolver fixture tests passed through `scripts/run_tests.sh`.

### Bounds and determinism

Synthetic corpus emission:

```text
160,000 ASCII JSON bytes: accepted
160,001 ASCII JSON bytes: rejected, stdout empty
159,999 multibyte JSON bytes: accepted
160,001 multibyte JSON bytes: rejected, stdout empty
64 cases: accepted
65 cases: rejected, stdout empty
```

Across three hash-seed/timezone/locale combinations, both contracts and corpora were byte-identical:

```text
634,716 bytes
SHA-256 101c06bd5a59ad9a3a584969702a7d62af52d4f01d3b009a3b6cc0722e8f7090
```

Actual contract sizes:

```text
legacy: 227,103 / 284,000 usable bytes
Archon: 279,431 / 284,000 usable bytes
reserve: 4,000 bytes
```

### Gate checks

```bash
HERMES_PYTHON=... HERMES_TEST_FILE_RETRIES=0 \
scripts/run_tests.sh tests/scripts/test_workflow_merge_gate.py \
-k 'task_2_language_conformance' -q
```

Result: one matrix assertion passed; the base-gate execution assertion failed because the environment has no `node`, so the fixture gate exited before creating its capture log. Static production inspection confirms one base-gate occurrence and one matrix occurrence.

### Static checks

The required local Ruff path does not exist:

```text
.venv/bin/ruff check .
exit 127: .venv/bin/ruff does not exist
```

The borrowed review interpreter’s Ruff passed:

```text
All checks passed!
```

It emitted three pre-existing invalid-`noqa` warnings.

The Windows footgun audit exited 1 with 33 findings across 15 changed-file scan targets. Direct range inspection confirmed the candidate adds none and fixes one explicit UTF-8 read in `hermes_cli/uninstall.py`; the reported production issue at line 131 and test-file findings are pre-existing. Native Windows was not executed.

## Unverified platforms and residual uncertainty

- Native Windows console generation and execution remain UNPROVEN.
- The canonical installed-distribution fixture did not execute because of shared-cache sandbox permissions; the supplemental POSIX install used no build isolation.
- The sandbox prevents ordinary macOS boot-identity sampling through `psutil`.
- No full repository suite was run. The known full-suite baseline remains red and was not attributed to this candidate.
- No network, credentials, live services, or external models were used.
- Temporary probes used only synthetic data and isolated `/private/tmp` paths.

## Final checkout status

Final scope revalidation produced:

```text
## HEAD (no branch)
HEAD:       869c6519cf86e9df6a903851ccf9d2ee2fc427fa
Tree:       fd176c315a258d2d7dcc493442a174fba91d7a05
Merge base: c1dc7a23e1e987f7f64a1bee89b224af4d4adf5d
Commits:    11
Paths:      17
Diff:       3,886 insertions, 135 deletions
```

`git branch --show-current` was empty, `git diff --check` remained clean, and the detached review checkout ended with no tracked or untracked changes.
