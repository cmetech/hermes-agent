# Task 11 Quality and Security Closure Review 6

## Verdict

**PASS.** Task 11 is quality/security complete on the authenticated
implementation tree and is ready to close before Task 12 begins.

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 0 |
| Minor | 0 |

The Fix Round 8 authored-end correction closes the one Important finding from
Closure Review 5. Every bounded logical shell-word consumer now advances with
the matcher's physical authored end, including transparent active
continuations within and immediately after the token. I found no remaining raw
logical-length advancement, no new fail-open or compatibility defect, and no
regression in the previously closed content, descriptor, lifecycle, evidence,
legacy, Windows-gate, or phase-boundary contracts.

## Authenticated identity and scope

- Worktree:
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-phase-3-semantic-compatibility-resilience`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Task 11 base:
  `25fc0397aafcd9b373169c189a2566a2fa570aff`
- Task 11 base tree:
  `4f1c15c8d1f3653b1d44e7882f2d50914199237b`
- Authored-end correction base:
  `0782af4f5550c0d24eb3f735d8ef09af196d8158`
- Correction-base tree:
  `4294741da5f875963d01263468ef80c63b5f156a`
- Reviewed implementation commit:
  `49ffbccfe4b9424f3b6542cfdf9df4bc9ef537e0`
- Reviewed implementation tree:
  `7560124795fca2d7c2f167874423e38042f40d83`
- Implementation author/subject:
  `Corey Ellis <corey@cmetech.io>` /
  `fix(workflow): preserve authored bash token ends`
- Checkout HEAD before this report:
  `77543e6a4d04659e0f3efb8203162017fa2a9f5f`
- Checkout tree before this report:
  `510704d9097c6f9f9ed5b3e0c5fa416731031b8d`
- `49ffbccfe..77543e6a4` changes only
  `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-11-report.md`.
  There is no production, test, harness, ledger, Task 12, or Phase 4 delta
  after the reviewed implementation tree.
- The checkout was clean at intake and after all diagnostics/test runs. This
  permitted review report is the only review-authored worktree delta.

The correction changes four files: `plugins/workflow/bash_rendering.py`,
`tests/plugins/workflow/test_performance_bounds.py`,
`tests/plugins/workflow/test_phase3_bash_lexer_security.py`, and
`tests/plugins/workflow/test_phase3_bash_substitution.py` (318 insertions, 36
deletions). I read the complete root `AGENTS.md`, approved Phase 3 design and
plan plus their approval reviews, the continue handoff, Task 11 brief/report,
the retained Task 10 reviews, every retained Task 11 specification and quality
review through Closure Review 5, the authenticated packages, the complete
changed implementation/test files, and the adjacent schema, resources,
scheduler, executor, managed-process, cleanup, evidence, catalog, legacy,
Windows, and Phase 4/Task 12 boundaries. Diagnostics used only benign
placeholders and numeric/string values.

## Package authentication

- `task-11-fix8-closure1-review.diff` has SHA-256
  `53fc3cf6af6cecebaed042b3aba88982af42ce5753209103a33494d5be4c3b40`.
  It is byte-identical to
  `git diff -U10 0782af4f5..49ffbccfe` (`cmp` exit 0); the Git-generated body
  has the same SHA-256 and byte size 28,355.
- `task-11-final-review-6.diff` has SHA-256
  `8d422cdade6b549f17aa7f43988f800e2d072a0fec903e2bfb5c8ec02f7f8fa6`.
  It is byte-identical to
  `git diff -U10 25fc0397a..49ffbccfe` (`cmp` exit 0); the Git-generated body
  has the same SHA-256 and byte size 536,950.
- `git diff --check` is clean for both the correction-only and complete Task
  11 implementation ranges.
- Ruff reports `All checks passed!` for all four correction files.

## Closure of the retained authored-end finding

### Matcher authority

`_logical_token_match()` returns the authored cursor after matching fixed
shell syntax across active physical continuations
(`bash_rendering.py:371-386`). The new `shell_word_end()` preserves that
coordinate, consumes any transparent continuations immediately after the
word, checks the physical-before/logical-after shell boundaries, and returns
the authored end rather than a boolean (`bash_rendering.py:639-655`).

The boundary logic is appropriately conservative:

- a continuation before the candidate word is walked backward only to test
  the true preceding logical boundary;
- continuations inside the word are consumed by `_logical_token_match()`;
- continuations after the word are consumed before checking the following
  separator; and
- joined non-keywords such as `if\\\ntrue` do not acquire reserved-word
  semantics because the following logical byte is not a separator.

### Complete consumer audit

Every call site now either uses the returned authored end directly or uses the
helper only as a side-effect-free predicate where a later pass intentionally
consumes the same starter:

- top-level and nested `function WORD` declarations assign `keyword_end` and
  continue from it (`bash_rendering.py:809-833`);
- direct/named `coproc` declarations use the authored `keyword_end`; their
  compound-starter check is predicate-only because the outer scanner must
  parse the starter itself (`bash_rendering.py:835-872`);
- conditional open/close consume the authored `[[`/`]]` ends
  (`bash_rendering.py:1067-1090,1223-1234`);
- all nested command prefixes (`while`, `until`, `select`, `then`, `else`,
  `elif`, `time`, `for`, `if`, `do`, `-p`, `!`, `{`) bind and assign the
  authored end (`bash_rendering.py:1271-1298`); and
- `esac`, `in`, and `case` each bind and assign the authored end
  (`bash_rendering.py:1313-1344`).

Repository search found no remaining `shell_word_at`, no
`start + len(<logical word>)`, and no `position += len(<logical word>)` in the
classifier. The sole remaining `position += len(terminator)` advances one of
the directly matched physical case terminators `;;&`, `;;`, or `;&`; it is not
a logical shell-word consumer and cannot recreate the finding.

Fresh benign probes also exercised internal and post-token continuations for
`[[` and `]]`; all four forms retained
`bash_reference_context_unsupported`, confirming that the same authored-end
contract covers the conditional operator siblings.

### Behavioral matrix and bounds

The new generator enumerates every internal split plus immediately-after-word
continuation for the complete declaration/prefix/case word set and both scalar
and output references (`test_phase3_bash_lexer_security.py:18-107,161-216`).
It proves:

- unsafe nested references remain rejected;
- quoted physical text retains its authored offsets and admitted quote
  context; and
- escaped token/reference text remains literal.

The shared admission, scheduler-preflight, and direct-runtime matrices add the
four formerly fail-open roots (function, direct/named coproc, and then/case),
both reference families, and inline/spill sizes. Rejection occurs before
resolver access, `variables-v3`, stdout/stderr creation, spawn intent, or
process launch.

The added complexity canary repeats a continued `then` command prefix at
2,048 and 4,096 repetitions, retains the existing doubling envelope, and adds
an absolute ten-index-read-per-authored-byte ceiling
(`test_performance_bounds.py:134-156`). Existing continuation, quoted-heredoc,
and dollar-dense bounds were not relaxed. The full performance file passes.

## Full Task 11 quality/security assessment

| Contract family | Result | Evidence |
|---|---|---|
| Archon-v3-only activation and legacy preservation | **PASS** | Secure rendering still requires both `archon-2026-07` and sealed normalizer 3. Legacy, unversioned, and admitted Archon v1/v2 keep prior rendering. |
| Shared admission/scheduler/runtime authority | **PASS** | Schema and scheduler call `bash_output_references()`; strict resources and the executor revalidate through the same classifier before resolving or materializing. |
| Fail-closed Bash contexts and false-positive compatibility | **PASS** | Function/coproc, prefixes, conditionals, case state, arrays, heredocs, comments, process substitution, arithmetic, parameters, backticks, ANSI-C, extglob, braces, here-strings, escapes, quote removal, nested frames, and ambiguous states have no contrary retained case. |
| UTF-8 sizes, NUL, deduplication, and aggregate limits | **PASS** | 32,768-byte inline threshold, 500,000-byte per-value cap, 64 distinct spills, and 2,000,000 aggregate bytes are unchanged and behaviorally covered. |
| Exact value transport | **PASS** | Unquoted/double/single replacements retain exact data semantics, including spaces, metacharacters, Unicode, terminal `x`, and trailing newlines. |
| Spill creation and immutable content authority | **PASS** | Descriptor-relative no-follow/exclusive creation, mode/regular/single-link/identity/size/digest checks, detached bounded snapshots, path removal, and anonymous pipes remain intact. |
| Descriptor identity and Windows behavior | **PASS within platform evidence** | Task 10 still pins read-only identities at exact child numbers. Large v3 values fail closed without descriptor support; inline Windows argv remains contained. Native Windows Job execution is the existing Darwin-host skip. |
| Lifecycle and side-effect ordering | **PASS** | Classification precedes output resolution and all filesystem/process side effects. Renderer, publisher, executor, managed process, outputs, callbacks, and parent/child descriptors retain total cleanup on success and faults. |
| Evidence and privacy | **PASS** | Exact `argv[-1]` remains authority; evidence contains only bounded hashes, sizes, counts, descriptor numbers, and digests—never command/value text, paths, identities, or mutable handles. |
| Complexity and maintainability | **PASS** | One authored-source phase-state classifier owns quote/frame/comment/heredoc/command decisions. Guarded token probes and fixed nesting retain linear bounds. |
| Narrow waist, prompt cache, and phase boundaries | **PASS** | No model tool, prompt/history mutation, API/path endpoint, behavior env setting, Task 12 session behavior, or Phase 4 loop materialization exists in Task 11. |

## Prior-finding dispositions

All prior Task 11 findings remain closed. In particular:

- profile-only v3 activation, inconsistent scheduler authority, and
  parse-before-literal ordering remain corrected;
- mutable verified spill inodes, pre-spawn ownership gaps, publisher faults,
  and descriptor close/reuse identity substitution remain corrected;
- nested case/command substitution, legacy/bare arithmetic, ANSI-C,
  conditionals, process substitution, extglob, brace expansion, and here-string
  contexts remain fail-closed;
- direct and prefixed indexed assignments, quote-removed wrappers/builtins,
  numeric-fd heredocs, joined `<<-`, multiple heredocs, quoted-body physical
  continuations, and prior-continuation comments retain their matrix coverage;
  and
- Closure Review 5's logical-token/physical-cursor defect is **ADDRESSED** by
  the single authored-end interface and complete consumer conversion above.

No unrelated residual correctness, security, compatibility, cleanup,
boundedness, privacy, complexity, legacy, Windows-gate, Task 12, or Phase 4
finding was established.

## Fresh verification

All Python tests ran only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

Exact eleven-file closure set:

```text
tests/plugins/workflow/test_phase3_bash_substitution.py
tests/plugins/workflow/test_phase3_code_catalog.py
tests/plugins/workflow/test_bash_e2e.py
tests/tools/test_managed_process.py
tests/plugins/workflow/test_security_boundaries.py
tests/plugins/workflow/test_performance_bounds.py
tests/plugins/workflow/test_resources.py
tests/plugins/workflow/test_strict_output_references.py
tests/plugins/workflow/test_phase3_bash_lexer_security.py
tests/plugins/workflow/test_phase3_bash_reference_ordering.py
tests/plugins/workflow/test_phase3_bash_descriptor_faults.py
```

Fresh result: **11 files, 1,799 tests passed, 0 failed**, 14 workers, 26.6
seconds, retries disabled and therefore unavailable/unused. The managed-process
file retained its existing native-Windows platform skip.

Affected resources/scheduler/performance set:

```text
tests/plugins/workflow/test_resources.py
tests/plugins/workflow/test_scheduler.py
tests/plugins/workflow/test_parallel_scheduler.py
tests/plugins/workflow/test_performance_bounds.py
```

Fresh result: **4 files, 106 tests passed, 0 failed**, 14 workers, 15.1
seconds, retries disabled and therefore unavailable/unused.

## Closure recommendation

Close Task 11 on implementation commit `49ffbccfe4b9424f3b6542cfdf9df4bc9ef537e0`
and tree `7560124795fca2d7c2f167874423e38042f40d83`. The retained docs-only HEAD does
not change the reviewed implementation. Task 12 may begin only after the
controller confirms the independent specification closure against the same
tree and records this report artifact. No integration, push, publication,
branch/worktree deletion, brand propagation, or literal-`main` action is
authorized by this review.
