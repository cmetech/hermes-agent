# Task 11 Independent Functional Specification Closure Review 3

## Verdict

**FAIL.** Fix Round 6 closes the three findings retained by Closure Review 2,
but Task 11 still does not satisfy the approved fail-closed here-document
contract.

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 1 |
| Minor | 0 |

The remaining defect is benign and local to the shared bounded Bash lexer: a
backslash immediately before the newline of a physical `#` comment is removed
before comment recognition. Bash ends the comment at that newline instead.
Consequently, a real here-document beginning on the next physical line can be
joined into the comment in the classifier, and references in its body are
admitted as ordinary command words.

## Authenticated identities and package

- Worktree:
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-phase-3-semantic-compatibility-resilience`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Task 11 base:
  `25fc0397aafcd9b373169c189a2566a2fa570aff`
- Task 11 base tree:
  `4f1c15c8d1f3653b1d44e7882f2d50914199237b`
- Reviewed implementation commit:
  `58dc9defa31c7319b2a5e1c9730622c06609e9dd`
- Reviewed implementation tree:
  `f48d3565fb7e46640578135b9972482f57dde617`
- Current docs-only HEAD:
  `b09f8e243203cf82ccdf496c79e1c53e552910ab`
- Current docs-only tree:
  `b1fde53f6cf2ad0122ebbc15d91042976a623279`
- Authenticated package:
  `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-11-final-review-3.diff`
- Package SHA-256:
  `30bbf9035fd84244cde12778455b404c22ab7a67ca6617f7f5fa955dd37c613c`
- The package diff body after its 47-line manifest is byte-identical to
  `git diff -U10 25fc0397a..58dc9defa` (`cmp` exited 0). Both bodies have
  SHA-256
  `6991bd06a68c1da783e06736926e80ab98c2ee02913b9b016449a812ad81f07b`.
- `git diff 58dc9defa..b09f8e243` changes only the retained
  `task-11-report.md`. There is no production, test, harness, customization,
  or release-gate delta after the reviewed implementation tree.
- `git diff --check 25fc0397a..58dc9defa` was clean. The worktree was on the
  required feature branch and clean before this permitted report write, after
  the fresh test run, and at final identity verification.

I read the complete root `AGENTS.md`, the approved Phase 3 design and plan,
Task 11 brief and current report, and every retained prior Task 11
specification and quality review/rereview through Closure Review 2. I
authenticated the full-range package against Git and traced the live Bash
classifier, physical/logical coordinate handling, schema admission, strict
resource renderer, scheduler preflight, executor launch, descriptor seam,
evidence/catalog, compatibility gates, and focused tests. Diagnostics used
only benign strings and numbers. Repository tests were invoked only through
`scripts/run_tests.sh`, with retries disabled.

## Important finding

### I-1 — Comment-line logicalization hides a real following here-document

**Locations:** `plugins/workflow/bash_rendering.py:421-506`, especially
`494-503`; `plugins/workflow/bash_rendering.py:509-549`, especially
`530-541`; shared consumers at `plugins/workflow/schema.py:1105`,
`plugins/workflow/scheduler.py:1530`,
`plugins/workflow/resources.py:801,961`, and
`plugins/workflow/bash_rendering.py:1322-1364,1552-1565`.

The new physical pre-scan correctly sees a here-document introduced after a
physical comment line: `_quoted_heredoc_body_ranges()` skips the comment only
to its newline, then parses a following `<<` and locates its body. However,
`_logical_bash_input()` subsequently removes every odd-parity
backslash-newline outside a quoted body without knowing that the backslash is
already comment text. That joins the next physical line onto the comment.

Bash does not do that. Once `#` starts a comment, the backslash in the comment
does not escape its terminating newline. Therefore this benign template has a
real here-document whose body contains the recognized reference:

```sh
# ignored \
<<'EOF'
printf '%s' "$USER_MESSAGE"
EOF
```

The production classifier instead sees the logical first line as
`# ignored <<'EOF'`, never queues the here-document in the authoritative
classifier, and admits `$USER_MESSAGE` as a double-quoted simple-token span.
The same diagnostic was repeated for unquoted `EOF`, single-quoted `'EOF'`,
double-quoted `"EOF"`, and backslash-quoted `\EOF`; all four forms were
admitted. The observed decisions were respectively:

```text
EOF    -> ((31, 44, '"'),)
'EOF'  -> ((33, 46, '"'),)
"EOF"  -> ((33, 46, '"'),)
\EOF   -> ((32, 45, '"'),)
```

A separate benign `/bin/bash` observation distinguishes the shell behavior:
with `printf '%s' body` between that delimiter and `EOF`, followed by
`printf '%s' after`, Bash prints only `after`. The would-be command is body
text, proving the second physical line starts a real here-document rather
than continuing the comment.

This is an under-rejection at the exact explicit here-document boundary.
For a quoted delimiter, pre-rendering can change text that Bash itself would
keep literal; for an unquoted delimiter, the admitted value enters a shell
expansion surface. Because `bash_output_references()` and
`render_v3_bash()` share this classifier, the result propagates consistently
through schema admission, scheduler preflight, direct runtime defense, and
both inline and spill rendering. Value size is considered only after the
wrong admission decision. Scalar and output-reference candidates therefore
have the same defect.

**Required correction:** make continuation removal respect physical comment
state as well as quoted here-document-body state, or replace the two-pass
pre-scan/logicalizer with one bounded phase-aware stream that cannot lose a
real pending heredoc. Add scalar and output-reference regressions for all
delimiter quote forms where a physical comment ending in backslash precedes a
real heredoc. Cover schema admission, scheduler preflight, and direct
inline/spill no-launch behavior. The reference must fail before resolution,
spill creation, output creation, or process launch as
`bash_reference_context_unsupported`.

## Fix Round 6 retained-finding disposition

| Closure Review 2 requirement | Decision | Evidence |
|---|---|---|
| I-1 command-position variants: leading redirection, `function`, direct/named `coproc`, `command`/`builtin` wrappers and options, assignment builtins, quoted assignment arguments | **CLOSED** | Top-level state now retains command position across redirection operands and the enumerated prefixes; scalar/output admission, scheduler, and inline/spill direct no-launch matrices cover the retained forms. Ordinary `command`/`builtin` argument text remains admitted. |
| I-2 escaped-literal precedence | **CLOSED** | `decide_range()` no longer overwrites an established literal decision with a later unsupported-range decision. Direct and compound escaped scalar/output subscript forms acquire no dependency, perform no resolution, create no spill, and are ignored by scheduler preflight. |
| I-3 quoted-heredoc physical continuation restoration | **CLOSED for the reported direct forms; overall heredoc closure FAILS on I-1 above** | Direct and logically joined `<<` operators preserve body backslash-newline bytes for single-, double-, and backslash-quoted delimiters; unquoted and split-unquoted delimiters retain active continuation removal; physical offsets and linear bounds are tested. The new physical-comment predecessor case exposes a distinct mismatch between the pre-scan and logical classifier. |

## Goal-backward Task 11 contract trace

| Contract family | Result | Evidence |
|---|---|---|
| Archon-v3-only activation; legacy and historical normalizers unchanged | **PASS** | Secure rendering requires both Archon and sealed normalizer 3. Legacy and Archon v1/v2 remain on their prior renderer/pathname behavior. |
| UTF-8 byte authority and content bounds | **PASS** | The live constants and tests retain 32,768 inline bytes, 500,000 per value, 64 distinct spills, and 2,000,000 aggregate bytes; 32,767/32,768/32,769 and multibyte boundaries remain covered. |
| Exact unquoted/double/single replacement table and content identity | **PASS** | Spill rendering retains the approved three exact forms. Real-shell tests cover spaces, quotes, dollars, backticks, globs, Unicode, empty values, terminal `x`, and trailing newlines, including double-quoted word containment. |
| Escaped-reference precedence, comments, nesting, arithmetic/parameter/command/backtick/conditional rejection, ambiguous states | **FAIL** | Earlier forms and Fix Round 6 escaped-array precedence remain covered, but I-1 admits references from a real heredoc body after a physical comment line. |
| Quoted heredoc physical continuation semantics | **FAIL overall** | Direct, joined-operator, split-delimiter, quote-form, coordinate, scheduler/runtime, inline/spill, and linear cases added in Fix Round 6 pass. I-1 demonstrates that the physical pre-scan and logical classifier still disagree when the heredoc follows a physical comment ending in backslash. |
| Descriptor-relative creation, immutable-byte publication, path/inode races, read failure, and fixed descriptor identity | **PASS** | No relevant post-review delta exists. Detached snapshots, anonymous pipes, path removal, stable `F_DUPFD_CLOEXEC` pinning, expected identity checks, and original-bytes-or-integrity-failure tests remain intact. |
| Descriptor lifecycle and unrelated-handle isolation | **PASS** | Ownership guards cover construction, evidence, callbacks, output setup, spawn, publication, cleanup, and descriptor reuse; only bounded nominated handles cross launch. |
| Exact `argv[-1]`, evidence privacy, and durable catalog | **PASS** | The immutable rendered command is execution authority. Evidence remains bounded to sizes, counts, descriptor numbers, and digests, with no command text, values, paths, identities, or handles. All four Bash codes remain catalogued and behavior-linked. |
| Native Windows behavior | **PASS by source and simulated local tests; native execution unavailable** | Large v3 values fail closed when descriptor inheritance is unavailable; inline values retain the existing platform-gated Bash argv and managed containment. This Darwin host cannot execute native Windows Job Objects. |
| Bounded linear behavior and physical offsets | **PASS for covered paths** | The fresh 10-test performance file passes, including doubled quoted-heredoc bodies; logical-to-physical offset regressions remain green. I-1 is a semantic state error, not an observed complexity or offset failure. |
| Prompt cache, narrow waist, and privacy boundaries | **PASS** | No model tool, prompt/history mutation, behavioral environment setting, API/path endpoint, raw value, or provider/session surface was added. |
| Task 12 and Phase 4 boundary | **PASS** | No persistent-session implementation or Phase 4 loop materialization was started; `loop.until_bash` retains its explicitly deferred path. |

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

Fresh result: **11 files, 765 tests passed, 0 failed in 15.3 seconds**, with
14 workers, one native-Windows platform skip, and no retry available or used.

The green result is valid evidence for the retained matrix, but it does not
alter the FAIL verdict: no repository test places a recognized reference in a
real heredoc body whose operator follows a physical `#` comment ending in a
backslash. The benign classifier and shell diagnostics above did not invoke an
alternate test framework and did not mutate repository files.

## Closure decision

Task 11 remains **open**. Fix Round 6 closes all three findings retained by
Closure Review 2, and the descriptor/content/evidence contracts remain sound,
but the explicit here-document fail-closed requirement is still incomplete.
Correct I-1, add the missing shared-authority regressions, rerun the expanded
Task 11 suite with retries disabled, and obtain fresh independent specification
and quality closure before starting Task 12.
