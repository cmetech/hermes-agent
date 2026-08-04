# Task 11 Independent Functional Specification Closure Review 4

## Verdict

**FAIL.** Fix Round 7 closes all four findings retained by Closure Review 3,
but Task 11 still does not satisfy the approved bounded, fail-closed Bash
reference-context contract.

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 1 |
| Minor | 0 |

The remaining defect is local to the shared bounded Bash classifier. Unquoted
Bash process substitutions (`<(...)` and `>(...)`) are not represented as
nested command contexts. The scanner instead treats `<` or `>` as an ordinary
redirection and the following `(` as a top-level separator. A scalar or output
reference inside the process-substitution command is consequently admitted as
ordinary simple-token text instead of blocking as
`bash_reference_context_unsupported`.

## Authenticated identities and package

- Worktree:
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-phase-3-semantic-compatibility-resilience`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Task 11 base:
  `25fc0397aafcd9b373169c189a2566a2fa570aff`
- Task 11 base tree:
  `4f1c15c8d1f3653b1d44e7882f2d50914199237b`
- Reviewed implementation commit:
  `edd0f9ce4c96f844045a4894d7faba218e01850c`
- Reviewed implementation tree:
  `8eafcf9a8e6e1831d3689dba133e99da8d11b52f`
- Current docs-only HEAD:
  `c82f099e0a06f8fc94fc52aa3eaac664986d33d8`
- Current docs-only tree:
  `8bda78498be58e1ac7f905dffd0d52920e4f2bc9`
- Reviewed implementation parent:
  `88e5ff94b81bdaeb6dcd5c3bc355e543b155e256`
- Reviewed implementation author/subject:
  `Corey Ellis <corey@cmetech.io>` / `fix(workflow): close bash parser compatibility gaps`
- Authenticated package:
  `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-11-final-review-4.diff`
- Package SHA-256:
  `426e00b0127aa60ebdba4896e184bfa94cefbc6879a370d91776dfa805b28e2c`
- The package body beginning at line 53 is byte-identical to
  `git diff -U10 25fc0397a..edd0f9ce4` (`cmp` exited 0). Both bodies have
  SHA-256
  `f3712d94348f5a1479a55f287a05a9e4438b19c11d7577b0e199ce7beb884c17`.
- `git diff edd0f9ce4..c82f099e0` changes only the retained
  `task-11-report.md`. There is no production, test, harness, customization,
  release-gate, Task 12, or Phase 4 delta after the reviewed implementation
  tree.
- `git diff --check 25fc0397a..edd0f9ce4` was clean. The worktree was clean
  and on the required feature branch at intake; the final worktree delta is
  only this permitted retained review report.

I read the complete root `AGENTS.md`, the approved Phase 3 design and plan,
Task 11 brief and current report, all prior Task 11 specification and quality
reviews/rereviews through Closure Review 3, the authenticated full-range diff,
and the live implementation. I traced schema admission, grammar-neutral
candidate discovery, shared Bash classification, scheduler preflight, strict
resource resolution, direct executor rendering and launch, descriptor
publication/pinning, evidence and catalog registration, compatibility gates,
the release/customization boundary, and the focused behavioral tests.
Diagnostics used only benign strings. Repository tests were invoked only via
`scripts/run_tests.sh`, with retries disabled.

## Important finding

### I-1 — Process-substitution command bodies are admitted as top-level simple-token text

**Locations:** `plugins/workflow/bash_rendering.py:965-970,1105-1126,1178-1190,1402-1428`;
shared consumers at `plugins/workflow/schema.py:1105`,
`plugins/workflow/scheduler.py:1530`,
`plugins/workflow/resources.py:798-802,944-965`, and
`plugins/workflow/executors/bash.py:40-101`.

The classifier creates bounded nesting frames for `$(` command substitution,
arithmetic, parameter expansion, backticks, conditionals, and related retained
forms. It has no corresponding recognition for Bash process substitution.
For `cat <($USER_MESSAGE)`, the `<` path marks a redirection operand, then the
generic top-level separator path processes `(` and resets command position.
No frame is open when the reference start is visited, so the reference receives
the current top-level quote decision and is admitted. The same happens for
`>(...)` and for strict output references.

The benign live classifier diagnostic returned:

```text
'cat <($USER_MESSAGE)'  -> ((6, 19, None),)
'cat >($USER_MESSAGE)'  -> ((6, 19, None),)
'cat <($producer.output)' -> ((6, 22, None),)
'cat >($producer.output)' -> ((6, 22, None),)
```

`bash_output_references()` likewise returned the `producer` dependency at
physical span `(6, 22)` for both output-reference forms. A benign shell
diagnostic confirms that `/bin/bash` executes the content between `<(` and
`)` as a nested command and exposes its output through the substitution. The
review host's `/bin/sh` rejects this Bash-only syntax, which makes the current
behavior platform-dependent rather than safe: the native-Windows Bash path and
other Bash-compatible launch paths can execute it, while a rejecting shell
fails only after admission and launch.

This is outside the approved three simple-token contexts and is a missing
nesting boundary in the exact shared classifier that is supposed to reject
contexts not proven safe. It is also structurally adjacent to `$()` command
substitution, which the contract explicitly blocks. Because
`bash_output_references()` and `render_v3_bash()` share the classifier, the
under-rejection propagates through schema admission, scheduler preflight,
inline rendering, spill rendering, and direct runtime defense. Scalar and
output-reference candidates are both affected, and value size is considered
only after the incorrect context decision.

**Required correction:** recognize unquoted `<(` and `>(` as bounded nested
command contexts and fail closed for every recognized scalar/output reference
inside them, including nested parentheses and quote forms, or reject the whole
ambiguous construct whenever it contains a candidate. Add shared-authority
regressions for both operators and both reference families at schema admission,
scheduler preflight, and direct inline/spill no-launch boundaries. Rejection
must occur before output resolution, spill creation, output creation, or
process launch as `bash_reference_context_unsupported`. Keep quoted literal
`'<(...)'` / `"<(...)"` text and unrelated ordinary redirections from being
over-rejected.

## Fix Round 7 retained-finding disposition

| Closure Review 3 requirement | Decision | Evidence |
|---|---|---|
| fd-prefixed heredoc command position | **CLOSED** | Numeric descriptor words are cleared at `<<`/`<<-` before they can consume command position. The matrix covers descriptors 0-9, leading and multiple redirections, both heredoc operators, scalar/output admission, scheduler preflight, and direct inline/spill no-launch behavior. The implementation's `isdigit()` check also remains bounded to the immediately adjacent authored word, so whitespace-separated ordinary numeric commands are not widened into descriptors. |
| Quote-removed wrapper, builtin, option, and concatenated assignment words | **CLOSED** | `_quote_removed_bash_word()` dequotes single, double, backslash, and concatenated words while retaining source coordinates. Command/builtin wrappers and options, assignment-builtin names, quoted/escaped/concatenated indexed assignments, and EOF finalization are covered. Ordinary argument text remains admitted. |
| Joined `<<-` at all boundaries, multiple queued heredocs, and tab stripping | **CLOSED** | The physical parser follows active continuations between both `<` characters and before/after `-`, parses split delimiters, queues multiple documents in order, applies tab stripping only for `<<-`, preserves physical coordinates, and has linear-bound coverage. |
| Physical-comment semantics and heredoc-body classification | **CLOSED** | Continuation preservation records physical comments only through their physical newline and preserves backslash-newline bytes in quoted heredoc bodies while removing active joins in unquoted bodies. All delimiter quote forms, comment predecessors, false heredoc-looking tokens in quotes/arithmetic/conditionals/comments, scheduler/runtime, offsets, and linear behavior are covered. No contrary benign case was confirmed in this rereview. |

## Goal-backward Task 11 contract trace

| Contract family | Result | Evidence |
|---|---|---|
| Archon-v3-only activation; legacy/unversioned/v1/v2 unchanged | **PASS** | Secure rendering requires Archon plus sealed normalizer 3. Historical normalizers retain the previous inline/path renderer, and Phase 4 `loop.until_bash` remains on its deferred path. |
| Schema admission, scheduler preflight, and direct runtime defense share one authority | **FAIL** | The shared authority is consistently wired, but I-1 means it consistently admits process-substitution command bodies at every boundary. |
| Grammar-neutral output/scalar candidate discovery; strict parse only after context classification | **PASS except I-1** | Candidates are found before strict output parsing, escaped/comment literals retain precedence, malformed admitted references preserve physical offsets, and scalar candidates participate in the same classification. The missing process-substitution frame is the remaining context error. |
| UTF-8 byte authority and content limits | **PASS** | Live constants and tests retain 32,768 inline bytes, 500,000 per value, 64 distinct spills, and 2,000,000 aggregate bytes, with 32,767/32,768/32,769 and multibyte boundaries, NUL, deduplication, and combined-cap cases. |
| Exact unquoted/double/single replacement table and content identity | **PASS** | The approved replacement forms are unchanged. Real-shell tests cover empty values, spaces, quotes, dollars, backticks, globs, Unicode, terminal `x`, trailing newlines, and double-quoted containment for inline and spill values. |
| Escapes, comments, quotes, false heredocs, nesting, and ambiguous-state rejection | **FAIL** | Escaped candidates/comments and retained quote, heredoc, arithmetic, parameter, command-substitution, backtick, conditional, array-subscript, function, coprocess, and ambiguity matrices pass without confirmed benign over-rejection. I-1 leaves the adjacent `<(...)`/`>(...)` command-nesting class under-rejected. |
| Descriptor-relative creation, immutable publication, content/identity verification, races, and read failures | **PASS** | Detached immutable snapshots feed anonymous pipes; path removal, symlink/swap defenses, regular/single-link checks, digest/size checks, publisher faults, fixed inherited identity, and fail-closed `bash_spill_integrity` behavior remain intact. |
| Descriptor lifecycle and unrelated-handle isolation | **PASS** | Bounded nominated read descriptors are pinned with expected identity before exec confirmation; construction, callbacks, output setup, spawn faults, parent release, publisher joining, cleanup, and descriptor reuse preserve ownership and isolation. |
| Exact `argv[-1]` authority, evidence privacy, and durable catalog | **PASS** | The immutable rendered command is the executed authority. Evidence contains only bounded hashes, sizes, counts, descriptor numbers/manifests, and content digests, never command text, values, paths, identities, or handles. All four durable Bash codes remain catalogued and behavior-linked. |
| Native Windows behavior | **PASS for the specified inline/large-value gates; I-1 is cross-platform** | Large v3 values fail closed when inherited descriptors are unavailable; inline values retain platform-gated Bash argv and managed containment. Native Windows execution is unavailable on this Darwin host. Process substitution must be classified before platform launch rather than delegated to whichever shell happens to accept it. |
| Physical offsets, parity, and bounded linear behavior | **PASS for covered paths** | Candidate and logical-to-physical mappings remain ordered/disjoint; even/odd backslash parity, joined heredocs, comments, quoted bodies, dollar-dense inputs, nesting caps, and large-input read bounds are tested. I-1 is a missing grammar state, not an observed offset or complexity regression. |
| Narrow waist, prompt cache, privacy, legacy behavior, customization ledger, and release gate | **PASS** | No model tool, system-prompt/history mutation, behavioral environment setting, raw value/path evidence, API endpoint, or provider/session surface was added. The managed-descriptor customization entry and merge-gate suites cover the owned upstream seams without widening Task 11 runtime scope. |
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

Fresh result: **11 files, 1,131 tests passed, 0 failed in 18.7 seconds**,
with 14 workers and no retry available or used.

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

Fresh result: **4 files, 104 tests passed, 0 failed in 16.3 seconds**, with
14 workers and no retry available or used.

These green runs are valid evidence for the retained compatibility, content,
descriptor, admission, scheduling, runtime, and complexity matrices. They do
not alter the FAIL verdict: no repository test places a recognized scalar or
output reference inside an unquoted Bash process substitution. The benign
classifier and shell diagnostics did not invoke an alternate test framework
and did not mutate repository files.

## Closure decision

Task 11 remains **open**. Fix Round 7 closes every finding retained by Closure
Review 3, and the byte/content, descriptor, evidence, catalog, legacy,
Windows-gate, release-boundary, and Task 12/Phase 4 isolation contracts remain
sound. Correct I-1, add the missing shared-authority/no-launch regressions,
rerun the expanded Task 11 suite with retries disabled, and obtain fresh
independent specification and quality closure before starting Task 12.
