# Task 11 Independent Functional Specification Closure Review 2

## Verdict

**FAIL.** Fix Round 5 closes the two reported examples, but Task 11 still does
not satisfy the approved fail-closed Bash-context contract.

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 3 |
| Minor | 0 |

The remaining findings are all benign compatibility/correctness cases in the
same bounded lexer authority: command-position tracking misses several real
indexed-array assignment positions, an escaped reference inside an array
subscript is rejected instead of ignored, and continuation removal is applied
inside a quoted here-document even though the shell preserves it there.

## Authenticated identities and package

- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Task 11 base: `25fc0397aafcd9b373169c189a2566a2fa570aff`
- Base tree: `4f1c15c8d1f3653b1d44e7882f2d50914199237b`
- Reviewed implementation commit:
  `4e762aeed7add45de8396d58a481f106ae04a033`
- Reviewed implementation tree:
  `8bffdd94c63ceedb475e54ac2dbd53e0445be7be`
- Current docs-only HEAD:
  `fa23c53608d490dd5b7475546977b03c4420350e`
- Current docs-only tree:
  `0a6003b079b7e3e82dba1de979bd88667a6d671f`
- Authenticated package:
  `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-11-final-review-2.diff`
- Package SHA-256:
  `7285911982412a818f5ac380173cd2ef176a6a93920513ce091acad47523d6ec`
- The package body starts at line 43 and is byte-identical to
  `git diff -U10 25fc0397a..4e762aeed`; both bodies have SHA-256
  `2f787686f33c865c9075b3e438b33238896974499f546102f4274351a459aa08`.
- `git diff 4e762aeed..fa23c5360` changes only the retained Task 11 report.
  No production, test, harness, ledger, or other implementation surface differs
  between the reviewed implementation and current HEAD.
- The worktree was on the required feature branch and clean at intake.

I read the complete repository `AGENTS.md`, approved Phase 3 design and plan,
Task 11 brief and current report, every prior Task 11 specification and quality
review/rereview (including both failed closure reviews), the applicable review
and verification instructions, the authenticated full-range package, and the
live implementation and test authorities relevant to every Task 11 contract.

## Findings

### Critical

None.

### Important

#### I-1 — Command-position tracking still admits real indexed-array assignment contexts

**Locations:** `plugins/workflow/bash_rendering.py:440-442,543-626,712-719,873-904,1083-1096`

Fix Round 5 rejects the four directly tested forms:

```text
items[$USER_MESSAGE]=9
items[$USER_MESSAGE]+=9
items=([$USER_MESSAGE]=9)
items+=([$USER_MESSAGE]=9)
```

It does so only when `top_level_command_position` remains true (or when the
scanner is already inside a recognized compound assignment). The new word
tracker consumes redirection operands and some Bash command prefixes as if
they were the command name, turning that flag false before the assignment.
It also has no top-level handling equivalent to the existing nested
`function`/`coproc` handling.

Benign classifier observations on the reviewed tree therefore admit all of
these spans with unquoted simple-token context:

```text
> /dev/null items[$USER_MESSAGE]=9
function f { items[$USER_MESSAGE]=9; }; f
coproc worker { items[$USER_MESSAGE]=9; }; wait
builtin declare -a items[$USER_MESSAGE]=9
command declare -a items[$USER_MESSAGE]=9
```

The direct form with a redirection after the assignment is rejected, showing
that the distinction is tracking order rather than different subscript
semantics. Bash 5.3 benign executions of the equivalent rendered forms with
value `1+1` select index `2` and print `9` for the redirection, function,
coprocess, and declaration-builtin cases. They are arithmetic subscript
positions, not approved ordinary command-word data.

The same classifier is shared by schema admission, scheduler preflight, and
direct inline/spill rendering, so this is consistently under-rejected at every
authority. Classification occurs before value-size handling, and the defect
therefore applies to scalar and output values in both inline and spill modes.
On Bash-compatible hosts the value is interpreted as arithmetic; on other
`/bin/sh` implementations the admitted command may instead fail as unsupported
syntax. Neither is the required pre-launch
`bash_reference_context_unsupported` result.

**Required correction:** make command-position tracking preserve shell command
position across redirection operators and their operands, and account for the
supported Bash function/coprocess and assignment-builtin prefix forms (or
conservatively reject when that position cannot be proven). Add scalar/output
admission, scheduler, and direct no-launch coverage at inline/spill sizes for
the grammar-equivalent forms, while retaining ordinary argument text such as
`printf '%s' items[$USER_MESSAGE]=9`.

#### I-2 — Array-range rejection overwrites the escaped-reference decision

**Locations:** `plugins/workflow/bash_rendering.py:444-451,704-709,731-742,873-890,1115-1126`

The escape branch correctly marks a span beginning immediately after `\` as
literal (`True`). When the surrounding subscript closes, however,
`unsupported_range()` writes `False` over every decision in the bracket range.
As a result, both of these benign literal forms fail with
`bash_reference_context_unsupported`:

```text
items[\$USER_MESSAGE]=9
items=([\$USER_MESSAGE]=9)
```

The same result occurs for an escaped `$producer.output`. This contradicts the
explicit Task 11 rule that escaped references are ignored. It also creates
over-rejection at admission: text which is not a workflow substitution is
treated as an unsupported substitution context. Because
`bash_output_references()` deliberately inventories scalar and output
candidates before returning only admitted output tokens, schema and scheduler
inherit the same false blocker; `render_v3_bash()` repeats it for direct
runtime defense.

**Required correction:** preserve an already-established literal/ignored
decision when an unsupported grammar range is marked, or remove literal spans
from grammar-range decisions through an equivalent single authority. Add
scalar/output coverage for direct and compound array forms across admission,
scheduler preflight, and direct execution, including an above-inline value
that proves no spill is created for the ignored token.

#### I-3 — Global continuation removal misparses quoted here-document bodies

**Locations:** `plugins/workflow/bash_rendering.py:364-390,393-418,453-532`

`_logical_bash_input()` removes every active backslash-newline pair before the
lexer has identified quote or here-document state. That is correct for ordinary
logical shell input and for an unquoted here-document, but not for a
quote-protected here-document body, where Bash preserves the pair.

This benign command is valid and the reference after the delimiter is an
ordinary double-quoted command word:

```text
cat <<'EOF' >/dev/null
line\
EOF
printf '%s' "$USER_MESSAGE"
```

Bash 5.3 returns status 0 and prints `safe` when the value is `safe`. The
reviewed classifier instead joins `line\` to the physical delimiter, sees a
logical `lineEOF` body line, and raises
`bash_reference_context_unsupported` as an unterminated here-document. Thus a
safe post-here-document scalar/output reference is blocked at admission and
runtime. This is an over-rejection introduced by applying the new logical-line
normalization without the heredoc quoting rule.

**Required correction:** make continuation normalization aware of quoted
here-document bodies while retaining logical-to-physical coordinate mapping.
Active continuation removal must remain enabled for ordinary shell input,
continued comments, operators, and unquoted here-document processing. Add
post-delimiter scalar/output tests for single-quoted, double-quoted, and
backslash-quoted delimiters through admission, scheduler, and direct
inline/spill rendering.

### Minor

None.

## Prior closure findings and exact Fix Round 5 disposition

| Required decision | Result | Evidence |
|---|---|---|
| Joined logical operators | **Closed for the reported operator forms** | The physical-to-logical scan exposes joined `((`, `$(`, `$((`, `${`, `$[`, `[[`, and `<<` to the existing bounded lexer. Scalar/output admission and direct inline/spill no-launch tests cover each listed form. I-3 is a distinct quoted-heredoc continuation error, so overall continuation closure still fails. |
| Direct indexed-array assignment | **Closed for the four tested forms only** | Direct assignment/augmented assignment and compound assignment/append subscripts are rejected. I-1 shows grammar-equivalent command-position variants remain admitted. |
| Scalar and output candidates | **PASS for covered forms; FAIL overall** | `bash_output_references()` jointly classifies both candidate kinds and direct rendering reclassifies actual substitutions. I-1 and I-2 affect both kinds identically. |
| Admission/runtime/scheduler authority | **PASS as one shared authority; incorrect outcomes remain** | Schema, scheduler, strict resources, and renderer all use the same classifier. This prevents divergence but propagates I-1 through I-3 consistently. |
| Inline/spill behavior | **PASS for admitted/rejected covered forms; FAIL overall** | Classification precedes byte/materialization work, and tests prove the new examples do not launch or create `variables-v3`. I-1 is admitted regardless of size; I-2 prevents a literal above-inline token from being ignored. |
| Physical source offsets | **PASS** | Logical spans map back to physical boundaries; the retained malformed-reference test after a continuation reports the original offset. |
| Continued comments | **PASS** | A single active continuation extends a comment before candidate classification, so a following reference remains literal with zero spills. |
| Continued escaped references | **PASS for the added ordinary form; FAIL in array ranges** | `printf ... \\` + newline + `\$USER_MESSAGE` remains literal. I-2 shows the array-range decision overwrites the same escape authority. |
| Consecutive-backslash parity | **PASS** | The logicalizer removes newline only when the current backslash follows an even count of consecutive backslashes, so odd-length runs activate continuation and even-length runs preserve the physical newline. The retained even-run regression exercises the distinction. |
| Command-position compatibility | **FAIL** | Argument-like `items[...]` and `[[ ... ]]` text remains admitted as intended, but I-1 shows redirection operands and Bash prefixes incorrectly change assignment recognition. |
| Compound assignments and value text | **PASS for covered shapes** | Element `[subscript]=value` is rejected while bracket text embedded in an already selected element value remains admitted and exact. Prefix variants remain part of I-1. |
| Bounded linear behavior | **PASS** | Logicalization is one forward pass with an O(n) boundary map; the bounded classifier remains iterative. The fresh performance test doubles 4,096 to 8,192 continuations and enforces linear indexed reads. |
| No over-rejection of approved simple/literal contexts | **FAIL** | The tested quoted/argument/compound-value contexts pass, but escaped array literals (I-2) and a safe reference after a quoted heredoc (I-3) are rejected. |

## Remaining Task 11 contract trace

| Contract family | Result | Evidence |
|---|---|---|
| Archon-v3-only activation and historical behavior | **PASS** | `BashExecutor` requires both effective Archon profile and sealed normalizer version 3. Archon v1/v2 and Hermes legacy retain their prior renderer/pathname behavior. |
| UTF-8 bounds and content preservation | **PASS** | Byte thresholds are 32,768 inline, 500,000 per spill, 64 descriptors, and 2,000,000 aggregate; deduplication is by exact bytes. Real-shell tests cover 32,767/32,768/32,769, multibyte data, empty/metacharacter/Unicode values, terminal `x`, and trailing newlines in all three quote contexts. |
| Descriptor creation and content identity | **PASS** | Creation is descriptor-relative, no-follow, exclusive, mode `0600`, bounded, fsynced, regular/single-link/identity/size/digest verified, snapshotted, unlinked, and published through anonymous pipes. |
| Descriptor-number identity closure | **PASS** | `_SpillTransport` captures path-free pipe identity. `ManagedProcessTree` first creates stable `F_DUPFD_CLOEXEC` pins, then checks aligned expected identity and read-only mode before child creation. Close/reuse mismatch maps to `bash_spill_integrity`. |
| Lifecycle and races | **PASS** | Renderer ownership begins immediately, publication is bounded to one joined thread, caller read descriptors close after pin-and-exec confirmation, internal pins close on every outcome, and fault tests cover construction, publication, callback, output, spawn, cleanup, and descriptor reuse paths. |
| Exact command/evidence authority and privacy | **PASS** | The exact immutable rendered command is `argv[-1]`; evidence contains only template/rendered sizes and digests, spill counts/totals/content digests, and descriptor numbers. No command text, raw values, spill paths, identities, or mutable handles are projected. |
| Durable code catalog | **PASS** | All four Bash codes are registered for Archon v3 and behavior-linked tests cover NUL, limit, integrity, and context emitters without a duplicate public list. |
| Native Windows and platform behavior | **PASS by source and simulated tests; native execution unavailable** | Large v3 values fail before launch when descriptor inheritance is unavailable; inline values retain the exact platform-gated Bash `-c` argv and managed containment. This Darwin host cannot execute native Windows Job Object behavior. |
| Release-gate/customization evidence | **PASS** | The generic optional identity seam is recorded in the existing Task 10 ledger entry, and the base merge gate selects all four Task 11 Bash suites once alongside managed-process coverage. Fix Round 5 did not change the generic seam or gate. |
| Prompt cache, narrow waist, and privacy boundaries | **PASS** | No model tool, live system prompt, history, API endpoint, path-taking surface, raw provider/session surface, or behavioral environment setting was added. |
| Task 12 and Phase 4 boundary | **PASS** | No persistent-session implementation was started. `loop.until_bash` remains on its prior `secure_v3=False` materialization path as explicitly deferred to Phase 4. |

## Fresh verification

I ran the expanded existing Task 11 suite only through the required wrapper,
with retries disabled and no direct test-framework invocation:

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

Fresh result: **11 files, 581 passed, 0 failed, 1 platform skip in 17.4
seconds**, with 14 workers and no retry.

The green result validates the retained test matrix but does not alter the
FAIL verdict: none of the existing tests covers the three concrete cases above.
The benign classifier/shell diagnostics used to confirm the findings were not
test-framework invocations and did not mutate repository files.

## Closure decision

Task 11 remains **open**. The exact prior logical-operator examples and the four
direct array forms are fixed, and descriptor identity closure remains sound,
but the complete requirement is not met while I-1 through I-3 remain. Do not
advance Task 12 or claim the Phase 3 Bash boundary closed until these Important
findings are corrected, the affected focused suites are extended and rerun
with retries disabled, and a fresh independent closure review passes on one
authenticated implementation tree.
