# Task 11 Quality and Security Closure Review 2

## Verdict

**FAIL.** Task 11 is not ready for quality/security closure.

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 2 |
| Minor | 0 |

Fix Round 5 closes the concrete joined-operator safety examples from Q-1 and
the four direct array-assignment examples from Q-2, but it does not close the
whole grammar classes. Indexed assignments are still admitted through real
top-level function, coprocess, command-prefix, and quoted assignment-builtin
forms. In addition, the new context-blind logicalization rejects a valid
reference after a quoted here-document whose body contains a literal
backslash-newline pair.

## Authenticated identity and scope

- Worktree:
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-phase-3-semantic-compatibility-resilience`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Task 11 base: `25fc0397aafcd9b373169c189a2566a2fa570aff`
- Task 11 base tree: `4f1c15c8d1f3653b1d44e7882f2d50914199237b`
- Fix Round 5 base: `55ab83c99f89da52aa7b5aac662dc9f6a53d6dd1`
- Fix Round 5 base tree: `470ad74a2b3d0f674d32567575f20a8c89678ba4`
- Reviewed implementation commit:
  `4e762aeed7add45de8396d58a481f106ae04a033`
- Reviewed implementation tree:
  `8bffdd94c63ceedb475e54ac2dbd53e0445be7be`
- Checkout HEAD before this report:
  `fa23c53608d490dd5b7475546977b03c4420350e`
- Checkout tree before this report:
  `0a6003b079b7e3e82dba1de979bd88667a6d671f`
- The only `4e762aeed..fa23c5360` delta is the retained
  `task-11-report.md`; there is no production or test delta after the reviewed
  implementation tree.
- Authenticated package:
  `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-11-fix5-review.diff`
- Package SHA-256:
  `0df9cf9b836045284e75d84d91f32498092c921ff6a92652e7389afc523d3b3e`
- The package diff body is byte-identical to
  `git diff -U10 55ab83c99..4e762aeed` (`cmp` exited 0); both bodies have
  SHA-256 `3b1282934edcb2e8d86f5b2593c2592457a7a4a0a0df93e93b05871bfa3cbb29`.
- `git diff --check` was clean for both the Fix Round 5 range and the complete
  Task 11 implementation range. The worktree was clean at review start.

I read the complete root `AGENTS.md`, approved Phase 3 design and plan plus
their approval reviews, Task 11 brief/report, every retained prior Task 11
specification and quality review/rereview, the exact Fix Round 5 package, all
four Fix Round 5 files in full, and the Task 11 production seams required to
trace schema admission, strict resource rendering, scheduler preflight,
executor launch, evidence, and inherited-descriptor identity validation.
Runtime observations used only benign numeric/string values.

## Prior-finding dispositions

| Prior concern | Disposition | Evidence |
|---|---|---|
| Q-1 — active backslash-newline can join an unsupported operator | **Direct safety cases addressed; continuation handling not closed** | `_logical_bash_input()` removes odd-parity active continuations, maps existing physical span boundaries into logical coordinates, and the shared classifier rejects the tested joined `$(`, `${`, `$((`, `$[`, `[[`, `<<`, and bare `((` forms. Physical error offsets and even-backslash compatibility are covered. Important finding I-2 below is a new over-rejection caused by applying that removal inside quoted here-document bodies where it is not active. |
| Q-2 — array-assignment subscripts are arithmetic contexts | **NOT ADDRESSED as a grammar class** | Direct indexed, augmented, compound, and compound-append assignments now reject. Important finding I-1 demonstrates top-level function/coprocess, command-prefix, and quoted assignment-builtin forms that still admit the same arithmetic subscript. |
| N-1 — descriptor-number close/reuse can substitute a readable object | **ADDRESSED and unaffected** | Fix Round 5 changes only Bash classification and tests. The path-free pipe identity captured at `bash_rendering.py:104`, aligned identity handoff at `executors/bash.py:219-220`, stable-duplicate comparison in `tools/managed_process.py:258-311`, and spawn validation at `tools/managed_process.py:822-865,930-932` have no Fix Round 5 delta. The prior original-bytes-or-`bash_spill_integrity` contract therefore remains closed. |

## Findings

### Critical

None.

### Important

#### I-1 — Q-2 still admits arithmetic subscripts through real command-position forms

**Locations:** `plugins/workflow/bash_rendering.py:593-625,704-719,873-904,1083-1096`; coverage gap at `tests/plugins/workflow/test_phase3_bash_substitution.py:200-226,553-615`.

`finish_top_level_word()` recognizes direct assignment builtins and a small
reserved-word set, but not top-level `function`, `coproc`, `command`, or
`builtin`. Once one of those words clears `top_level_command_position`, the
first command inside a Bash `function NAME { ...; }` or `coproc [NAME] { ...; }`
body, and an assignment builtin behind `command`/`builtin`, is no longer
eligible for array-subscript recognition. Separately, the array detector is
guarded by `quote is None`, although `declare`, `typeset`, `local`,
`readonly`, and `export` parse quoted assignment arguments after shell quote
removal.

Benign diagnostic observations from the production classifier:

| Template form | Classifier result |
|---|---|
| `items[$USER_MESSAGE]=9` | rejected |
| `f() { items[$USER_MESSAGE]=9; }; f` | rejected |
| `case x in x) items[$USER_MESSAGE]=9;; esac` | rejected |
| `function f { items[$USER_MESSAGE]=9; }; f` | **admitted** as unquoted |
| `coproc { items[$USER_MESSAGE]=9; }` | **admitted** as unquoted |
| `coproc C { items[$USER_MESSAGE]=9; }` | **admitted** as unquoted |
| `declare "items[$USER_MESSAGE]=9"` | **admitted** as double-quoted |
| `command declare items[$USER_MESSAGE]=9` | **admitted** as unquoted |
| `builtin declare items[$USER_MESSAGE]=9` | **admitted** as unquoted |

With the benign value `1+1`, `/bin/bash` executed the function, quoted
`declare`, and `command declare` forms successfully and each read back `9`
from `items[2]`. The value is therefore being interpreted by Bash arithmetic
grammar, not preserved as simple-token data. The decision is independent of
inline/spill size and applies equally to scalar and output-reference spans.
Because admission (`schema.py:1105`), strict rendering
(`resources.py:801,961`), and scheduler preflight (`scheduler.py:1530`) all
reuse `bash_output_references()`/the same classifier, this is consistent
shared fail-open behavior rather than authority drift.

The new tests cover only the four direct templates in
`_ARRAY_SUBSCRIPT_CONTEXTS`. Add admission, direct no-launch, and real
scheduler regressions for scalar/output references at inline/spill sizes across
the compound-command and assignment-builtin forms above. Command-position
tracking must model those prefixes/bodies, while preserving the existing safe
ordinary-argument and compound-value bracket cases. Quoted assignment-builtin
arguments need grammar-aware treatment rather than the ordinary quoted-word
allowance.

#### I-2 — Logicalization removes literal continuations from quoted here-document bodies

**Locations:** `plugins/workflow/bash_rendering.py:364-418,453-480,483-531,1098-1103`; coverage gap at `tests/plugins/workflow/test_phase3_bash_substitution.py:177-197,619-688`.

`_logical_bash_input()` removes every odd-parity backslash-newline pair before
the lexer knows whether it is inside a quoted here-document body. The
here-document state retains only `(delimiter, strip_tabs)`, not whether the
delimiter was quoted. `consume_heredocs()` then searches the already-flattened
stream for a delimiter line. In a quoted or backslash-quoted here-document,
backslash-newline is literal body data, so flattening can erase the line break
that makes the following delimiter recognizable.

Concrete benign example:

```sh
cat <<'EOF'
body\
EOF
printf '%s' "$USER_MESSAGE"
```

The production classifier rejects the later reference as
`bash_reference_context_unsupported` with “unterminated here-document.” The
locally available `/bin/sh` executes the same template successfully (after a
benign `safe` replacement), exits 0, and writes `body\\\nsafe`. A `<<\EOF`
delimiter has the same result. This is a Fix Round 5 compatibility regression:
the reference is outside a correctly terminated quoted here-document and is a
supported double-quoted simple token.

The joined-operator tests exercise an unquoted joined `<<`, continued comments,
escaped references, and unrelated continuations, but no literal continuation
inside a quoted here-document followed by a safe reference. Logicalization
must respect the shell phase and the delimiter's quotedness while retaining
the physical boundary map. Add quoted, backslash-quoted, and unquoted
here-document behavior tests, including a safe reference after the delimiter;
the first two must preserve the literal body pair, while an active unquoted
pair remains subject to logical joining and fail-closed body classification.

### Minor

None.

## Other quality, compatibility, and privacy assessment

- The physical/logical boundary map is linear and maps tested existing spans
  back to authored coordinates. The even/odd backslash parity cases, comments,
  escaped references, joined operator prefixes, and retained syntax-error
  offsets are materially covered. I found no count/time-bound regression in
  that path; the dedicated linear-read test passed.
- Schema admission, strict resource rendering, and scheduler preflight still
  share one Bash classification authority. This is architecturally correct,
  but it propagates both findings consistently to all three layers.
- The exact inline/spill quote replacement table, byte/count limits,
  detached-byte publication, descriptor ownership, legacy normalizer branch,
  and native-Windows large-value gate have no relevant Fix Round 5 change.
- Fix Round 5 adds no model tool, system-prompt/history mutation, API surface,
  raw command/value/path evidence, or Phase 4/Task 12 behavior. Existing Bash
  evidence remains bounded to sizes, counts, descriptor numbers, and digests.
- Legacy and admitted Archon v1/v2 dispatch remain outside secure v3
  classification. The new here-document regression affects only Archon v3;
  legacy bytes are unchanged.

## Fresh test assessment

Only the repository wrapper was used for tests, with retries disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_bash_substitution.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_bash_e2e.py \
  tests/tools/test_managed_process.py \
  tests/plugins/workflow/test_security_boundaries.py \
  tests/plugins/workflow/test_performance_bounds.py \
  tests/plugins/workflow/test_resources.py \
  tests/plugins/workflow/test_strict_output_references.py \
  tests/plugins/workflow/test_phase3_bash_lexer_security.py \
  tests/plugins/workflow/test_phase3_bash_reference_ordering.py \
  tests/plugins/workflow/test_phase3_bash_descriptor_faults.py
```

Fresh result: **11 files, 581 tests passed, 0 failed, 1 platform skip, 15.7
seconds, 14 workers**. No flaky retry was available or used.

This is strong evidence for the enumerated joined operators, direct assignment
forms, bounds, descriptor faults, ordering, real-shell content preservation,
and legacy compatibility. It does not change the FAIL verdict because the
suite lacks the command-position and quoted-here-document cases above. The
small classifier/shell commands used to establish the findings were benign
diagnostic observations, not alternate test-framework invocations.

## Closure recommendation

Do not close Task 11 and do not advance to Task 12 on this tree. Keep N-1
closed. Treat Q-1's enumerated joined-operator safety cases as fixed, but repair
I-2 before declaring continuation handling complete. Keep Q-2 open until the
full indexed-assignment grammar is rejected in every actual command-position
form, including quoted assignment-builtin arguments. Add the missing behavioral
regressions, rerun the exact eleven-file acceptance set through
`scripts/run_tests.sh` with retries disabled, and obtain a fresh independent
quality closure review.
