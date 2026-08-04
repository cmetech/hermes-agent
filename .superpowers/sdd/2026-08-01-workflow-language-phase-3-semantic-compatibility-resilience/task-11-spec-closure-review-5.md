# Task 11 Independent Functional Specification Closure Review 5

## Verdict

**FAIL.** Fix Round 8 closes the process-substitution finding retained by
Closure Review 4, but Task 11 still does not satisfy the approved bounded,
fail-closed Bash reference-context contract.

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 1 |
| Minor | 0 |

The remaining defect is in the shared Bash parser's logical-token/physical-
offset boundary. `shell_word_at()` recognizes reserved words through active
backslash-newline continuations but exposes only a boolean. The top-level
`function` and `coproc` declaration helpers then advance by the unsplit token's
raw character count rather than the authored match end. They lose the compound-
command boundary and admit a later array subscript reference as simple-token
text. Both inline and spill materialization can therefore feed resolved data
into Bash arithmetic-subscript grammar.

## Authenticated identities and packages

- Worktree:
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-phase-3-semantic-compatibility-resilience`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Task 11 base:
  `25fc0397aafcd9b373169c189a2566a2fa570aff`
- Task 11 base tree:
  `4f1c15c8d1f3653b1d44e7882f2d50914199237b`
- Reviewed implementation commit:
  `7fdcbee8158aa57bef00ace43fab94e82cf566e8`
- Reviewed implementation tree:
  `7e0d50d38b3171ad5e6b3dcfc03ad16a4800555a`
- Reviewed implementation parent:
  `dbe91d95ab1ec8228ca7717b004b5236f7205f26`
- Reviewed implementation author/committer:
  `Corey Ellis <corey@cmetech.io>`
- Reviewed implementation subject:
  `fix(workflow): unify bash parser phase state`
- Current docs-only HEAD:
  `db23ced425d8e3f47a297525a5fa199b17a86a52`
- Current docs-only tree:
  `18870b4f07a19986a4b42009250b4d8fe224a397`
- Current docs-only subject:
  `docs(workflow): record task 11 fix round 8`
- Fix Round 8 package:
  `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-11-fix8-review.diff`
- Fix Round 8 package SHA-256:
  `33c6a551ee786927e92f484d0a0867cfe5dbede3f7b02e032cb6efc88029f6a3`
- Full Task 11 package:
  `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-11-final-review-5.diff`
- Full Task 11 package SHA-256:
  `2cde354a98ab67d47cb4e053e703b5ebf921d7f3c41a968b79687970d662623d`
- The Fix Round 8 package is byte-identical to
  `git diff --no-ext-diff -U10 dbe91d95a..7fdcbee81`; the full package is
  byte-identical to
  `git diff --no-ext-diff -U10 25fc0397a..7fdcbee81` (`cmp` exited 0 for
  both).
- `git diff 7fdcbee81..db23ced42` changes only the retained
  `task-11-report.md`. There is no production, test, harness, customization,
  release-gate, Task 12, or Phase 4 delta after the reviewed implementation
  tree.
- `git diff --check` was clean for both authenticated ranges. The worktree was
  clean and on the required feature branch at intake; the final worktree delta
  is only this permitted retained review report.

I read the complete root `AGENTS.md`, the approved Phase 3 design and plan,
their approval reviews, Task 10's final specification/quality closures, Task
11's brief and current report, every retained Task 11 specification and quality
review/rereview through Closure Review 4, both authenticated diffs, and the live
implementation. I traced schema admission, grammar-neutral candidate discovery,
the shared Bash classifier, scheduler preflight, strict resource resolution,
direct executor rendering and launch, descriptor publication/pinning, evidence
and catalog registration, compatibility gates, release/customization ownership,
and the focused behavioral tests. Diagnostics used only benign values.
Repository tests were invoked only through `scripts/run_tests.sh`, with retries
disabled.

## Important finding

### I-1 — Joined `function`/`coproc` reserved words lose their authored end and admit arithmetic subscripts

**Locations:** `plugins/workflow/bash_rendering.py:639-655,809-871,889-914,
1250-1271`; sibling raw-length consumers at
`plugins/workflow/bash_rendering.py:1272-1299,1314-1340`; shared consumers at
`plugins/workflow/schema.py:1084-1117`,
`plugins/workflow/scheduler.py:1496-1537`,
`plugins/workflow/resources.py:797-812,944-965`, and
`plugins/workflow/executors/bash.py:38-110`.

`_logical_token_match()` returns the physical authored end of a logical token
whose spelling may cross active `\\\n` continuations. `shell_word_at()` uses
that end to validate the following word boundary, including continuations after
the token, but returns only `True`/`False`. Its declaration consumers discard
the authored end:

```python
if not shell_word_at(start, "function"):
    return None
cursor = start + len("function")

if not shell_word_at(start, "coproc"):
    return None
keyword_end = start + len("coproc")
```

For a continuation *within* the reserved word, the raw count lands inside its
physical spelling. For a continuation *after* the word, the count lands before
the continuation that `shell_word_at()` already treated as transparent. The
helper consequently fails to consume the declaration name. The outer scanner
then treats the name/body as ordinary top-level command text and loses the
compound-command command-position boundary. An indexed assignment in the body
is no longer recognized as an unsupported arithmetic-subscript context.

Fresh benign scalar diagnostics returned admitted spans for all four forms:

```text
'funct\\\nion f { items[$USER_MESSAGE]=9; }; f'
  -> ((21, 34, None),)
'function\\\n f { items[$USER_MESSAGE]=9; }; f'
  -> ((21, 34, None),)
'co\\\nproc worker { items[$USER_MESSAGE]=9; }; wait'
  -> ((24, 37, None),)
'coproc\\\n worker { items[$USER_MESSAGE]=9; }; wait'
  -> ((24, 37, None),)
```

The strict output-reference path is affected identically. For the corresponding
`$producer.output` forms, `classify_bash_reference_spans()` admitted physical
spans `(21, 37)` for both function forms and `(24, 40)` for both coprocess
forms; `bash_output_references()` returned the `producer` dependency at each
same physical span.

This is executable Bash grammar, not merely a scanner disagreement. GNU Bash
5.3.15 executed the joined `funct\\\nion` declaration, consumed the benign
rendered scalar `1+1` as an array arithmetic subscript, and printed `9` from
`${items[2]}` with status 0. `render_v3_bash()` also admitted all four forms
with a 32,769-byte benign value and created one 32,769-byte spill plus one
inherited descriptor for each. Thus the error precedes value-size selection and
affects scalar/output candidates and inline/spill transports.

Schema admission and scheduler preflight use `bash_output_references()`;
resource substitution and the Bash executor use the same classifier through
`render_v3_bash()`. The single wrong decision therefore propagates through all
approved authorities, including the direct runtime defense, rather than being
contained to one caller.

I also audited every other `shell_word_at()` consumer that can discard the
logical match end. The nested command-prefix list (`while`, `until`, `select`,
`then`, `else`, `elif`, `time`, `for`, `if`, `do`, `-p`, `!`, `{`) and nested
`case`/`in`/`esac` transitions likewise advance by raw token length. A matrix of
continuations within and after those reserved words retained fail-closed
classification for candidates in unsafe positions because candidates visited
while a nested command frame is open are already rejected. The conditional
`]]` consumer is correct because it advances with the returned logical-match
end. I found no second exploitable acceptance in the sibling matrix, but the
raw-length sites are the same incomplete phase-state interface and should not
remain as latent divergent offset authorities.

**Required correction:** make shell-word recognition return the consumed
physical end (including transparent continuations after the token), or pair its
boolean with a single authored-end helper. Use that returned end in
`function_declaration_name_end()`, `coprocess_declaration_end()`, every nested
command prefix, and the `case`/`in`/`esac` transitions; do not reconstruct a
physical cursor with `start + len(word)`. Conservatively reject if a declaration
cannot be consumed unambiguously. Add scalar and output-reference regressions
for continuations within and immediately after both `function` and `coproc` at
schema admission, scheduler preflight, and direct inline/spill execution.
Rejection must occur before output resolution, spill or output creation, and
process launch as `bash_reference_context_unsupported`. Retain direct forms,
ordinary non-keyword words, physical offset assertions, and guarded linear-
probe coverage. Add focused restoration/fail-closed cases for all sibling raw-
length consumers so the authored-end correction cannot regress their nested
frame behavior.

## Prior-finding disposition

| Prior requirement | Decision | Evidence |
|---|---|---|
| Archon-v3-only activation and legacy isolation | **CLOSED** | Secure Bash rendering remains gated on the Archon profile plus sealed normalizer 3; legacy/unversioned/v1/v2 behavior is unchanged. |
| Shared admission/scheduler/runtime authority | **WIRED, NOT CLOSED** | All boundaries call the same classifier, but I-1 proves that authority still admits one executable arithmetic context. |
| Native-Windows inline/large-value behavior | **CLOSED** | Inline argv and unavailable-descriptor gates remain covered by simulated/static behavior; the sole native platform skip is expected on this Darwin host. |
| Strict parse only after Bash context classification | **CLOSED** | Grammar-neutral candidates are classified before strict output parsing, with physical error offsets preserved. |
| Spill inode immutability, publication, read failures, ownership, and cleanup | **CLOSED** | Anonymous publisher pipes, verified snapshots, fault handling, and lifecycle tests remain green. |
| Descriptor close/reuse identity and unrelated-handle isolation | **CLOSED** | Pinned descriptor identity and N-1 reuse protections remain intact. |
| Active joined operators and direct/indexed command-position variants | **CLOSED for retained cases; I-1 is a sibling reserved-word gap** | Logical operators, direct function/coproc forms, wrappers, builtins, and quote-removed assignments remain covered. Continuation-joined declaration words were absent. |
| Heredoc fd prefixes, quote removal, joined `<<-`, physical comments, and quoted-body semantics | **CLOSED** | Retained heredoc matrices and physical-coordinate/linear guards pass. |
| Process substitution | **CLOSED** | Fix Round 8 recognizes unquoted `<(` and `>(` as bounded command frames and rejects scalar/output references inside them across shared authorities while retaining quoted literals and ordinary redirections. |
| Extglob, brace expansion, here-string maximal munch, direct authored offsets, and guarded linear probes | **CLOSED** | Fix Round 8's unified phase-state paths fail closed for ambiguous extglob/brace candidates, consume `<<<` maximally, preserve authored spans, and retain bounded probes. |

## Goal-backward Task 11 contract trace

| Contract family | Result | Evidence |
|---|---|---|
| Archon-v3-only activation; legacy/unversioned/v1/v2 unchanged | **PASS** | Secure rendering requires Archon plus sealed normalizer 3. Historical normalizers retain the prior renderer, and Phase 4 `loop.until_bash` remains deferred. |
| Schema admission, scheduler preflight, and direct runtime defense share one authority | **FAIL** | The authority is consistently wired, but I-1 means every boundary admits the same joined-declaration arithmetic subscript. |
| Grammar-neutral output/scalar discovery; strict parse after classification | **PASS except I-1** | Candidate order, malformed-reference precedence, and physical offsets remain correct; joined declarations receive the wrong context decision. |
| UTF-8 byte authority and content limits | **PASS** | The 32,768-byte inline threshold, 500,000-byte value cap, 64-spill cap, 2,000,000-byte aggregate cap, NUL rejection, deduplication, and multibyte boundaries remain covered. |
| Exact unquoted/double/single replacement table and content identity | **PASS** | Approved replacement forms and real-shell identity coverage are unchanged. I-1 concerns where those exact bytes are admitted, not their transport integrity. |
| Escapes, comments, quotes, heredocs, nesting, arrays, and ambiguous-state rejection | **FAIL** | Fix Round 8 closes process substitution and its adjacent state matrix, but active continuations in `function`/`coproc` still erase a compound-command boundary and expose arithmetic grammar. |
| Descriptor-relative creation, immutable publication, verification, races, and read failures | **PASS** | Detached verified snapshots, anonymous publication, identity/content checks, and fail-closed `bash_spill_integrity` paths remain intact. |
| Descriptor lifecycle and unrelated-handle isolation | **PASS** | Bounded nominated descriptors, pinning, ownership transfer, parent release, publisher joining, cleanup, and reuse checks remain coherent. |
| Exact `argv[-1]` authority, evidence privacy, and durable catalog | **PASS** | Execution uses the immutable rendered command; evidence remains bounded to hashes/sizes/counts/descriptors, and all four durable Bash codes remain catalogued and behavior-linked. |
| Native Windows behavior | **PASS for specified platform gates; I-1 is grammar-level** | Large values fail closed without descriptor support and inline argv remains platform-gated. Classification must reject I-1 before platform launch. |
| Physical offsets, parity, and bounded linear behavior | **PASS for covered paths** | Ordered authored spans, continuation parity, nesting caps, and guarded large-input probes pass. I-1 is caused by discarding an already-computed authored end. |
| Narrow waist, prompt cache, privacy, compatibility, customization ledger, and release gate | **PASS** | No core model tool, prompt/history mutation, behavioral environment setting, raw value/path evidence, provider/session surface, or unowned customization was added. |
| Task 12 and Phase 4 boundary | **PASS** | No persistent-session recovery/classification implementation or Phase 4 loop materialization was started. |

## Fresh verification

Only the canonical repository wrapper was used, with retries disabled:

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

Fresh result: **11 files, 1,338 tests passed, 0 failed in 21.6 seconds**,
with 14 workers, one expected native-Windows platform skip in managed-process
coverage, and no retry available or used.

The affected resource/scheduler/performance command was also run through the
same wrapper:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_resources.py \
  tests/plugins/workflow/test_scheduler.py \
  tests/plugins/workflow/test_parallel_scheduler.py \
  tests/plugins/workflow/test_performance_bounds.py
```

Fresh result: **4 files, 105 tests passed, 0 failed in 15.1 seconds**, with
14 workers and no retry available or used.

`ruff check` passed for all four Fix Round 8 implementation/test files, and
`git diff --check` passed for the fix-only and full Task 11 ranges.

These green runs are valid evidence for retained compatibility, content,
descriptor, admission, scheduling, runtime, process-substitution, and
complexity matrices. They do not alter the FAIL verdict: no repository test
places a scalar or output reference in an indexed assignment inside a
`function` or `coproc` declaration whose reserved word contains or is followed
by an active continuation. The benign classifier, renderer, spill, and Bash
diagnostics did not invoke an alternate test framework and left no repository
files behind.

## Closure decision

Task 11 remains **open**. Fix Round 8 closes every finding retained by Closure
Review 4, and the byte/content, process-substitution, descriptor, evidence,
catalog, legacy, Windows-gate, release-boundary, and Task 12/Phase 4 isolation
contracts remain sound. Correct I-1 across the complete authored-end consumer
set, add the missing shared-authority/no-launch regressions, rerun the expanded
Task 11 suite with retries disabled, and obtain fresh independent specification
and quality closure before starting Task 12.
