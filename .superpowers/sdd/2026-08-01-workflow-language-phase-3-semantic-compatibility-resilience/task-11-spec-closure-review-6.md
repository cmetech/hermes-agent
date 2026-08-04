# Task 11 Independent Functional Specification Closure Review 6

## Verdict

**PASS.** The Fix Round 8 closure correction closes the one Important
authored-end defect retained by both Closure Review 5 reports. Task 11 now
satisfies the approved functional contract and may close before Task 12 begins.

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 0 |
| Minor | 0 |

## Authenticated identity and package evidence

- Worktree:
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-phase-3-semantic-compatibility-resilience`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Current report-only HEAD:
  `77543e6a4d04659e0f3efb8203162017fa2a9f5f`
- Current report-only tree:
  `510704d9097c6f9f9ed5b3e0c5fa416731031b8d`
- Reviewed implementation commit:
  `49ffbccfe4b9424f3b6542cfdf9df4bc9ef537e0`
- Reviewed implementation tree:
  `7560124795fca2d7c2f167874423e38042f40d83`
- Reviewed implementation subject:
  `fix(workflow): preserve authored bash token ends`
- Correction base/review-doc commit:
  `0782af4f5550c0d24eb3f735d8ef09af196d8158`
- Task 11 base:
  `25fc0397aafcd9b373169c189a2566a2fa570aff`
- Fix Round 8 closure-correction package:
  `task-11-fix8-closure1-review.diff`
- Closure-correction package SHA-256:
  `53fc3cf6af6cecebaed042b3aba88982af42ce5753209103a33494d5be4c3b40`
- Full Task 11 package:
  `task-11-final-review-6.diff`
- Full-package SHA-256:
  `8d422cdade6b549f17aa7f43988f800e2d072a0fec903e2bfb5c8ec02f7f8fa6`

I independently regenerated both authenticated diff bodies. The first hash is
the exact output of `git diff -U10 0782af4f5..49ffbccf`; the second is the
exact output of `git diff -U10 25fc0397..49ffbccf`. Both byte-for-byte hashes
match the retained package files.

`git diff 49ffbccf..77543e6a4` changes only the retained
`task-11-report.md` (85 added lines). There is no production, test, harness,
customization, release-gate, Task 12, or Phase 4 delta after the reviewed
implementation tree. The checkout remained on the required feature branch and
clean through identity verification and both fresh test gates.

## Review scope and method

I read the complete root `AGENTS.md`, the approved Phase 3 design and plan,
the final Task 10 specification and quality closure reviews, the Task 11 brief
and complete current report, and every retained Task 11 specification and
quality review/rereview through Closure Review 5. I authenticated both review
packages against Git, inspected the complete correction diff and live
implementation, and traced the Bash classifier through schema admission,
scheduler preflight, strict resource resolution, direct executor rendering,
descriptor publication and Task 10 pinning, attempt evidence, the durable-code
catalog, legacy/version gates, native-Windows behavior, and the Task 12/Phase 4
boundary.

I specifically audited every `_logical_token_match()` and shell-word consumer,
all cursor advancement near those consumers, the adjacent quote/comment/
heredoc/nesting state machine, physical source offsets, complexity guards, and
all non-test callers of `bash_output_references()`,
`classify_bash_reference_spans()`, and `render_v3_bash()`. Diagnostics and test
data were benign. Python tests were run only through `scripts/run_tests.sh`
with retries disabled.

## Closure of the retained authored-end finding

The correction replaces the former boolean `shell_word_at()` interface with
`shell_word_end()`. It first uses `_logical_token_match()` to match a fixed
logical token through any active physical `\\\n` continuations, then skips
active continuations immediately following the token, validates the physical
boundaries on both sides, and returns the actual authored end. It returns
`None` on a non-word match. The before-boundary walk also crosses immediately
preceding active continuations so a token cannot be recognized inside a
larger logical word.

Every consumer now uses that returned physical end:

- `function_declaration_name_end()` starts its name scan at the authored end
  of `function`;
- `coprocess_declaration_end()` uses the authored end of `coproc`, and its
  compound-starter probes use the same helper;
- conditional `[[` and `]]` opening/closing advance to the authored end;
- every nested command prefix (`while`, `until`, `select`, `then`, `else`,
  `elif`, `time`, `for`, `if`, `do`, `-p`, `!`, and `{`) advances to the
  returned end; and
- `case`, `in`, and `esac` state transitions advance to the returned end.

The exhaustive live search found no remaining boolean shell-word helper and
no reconstruction with `start + len(word)` or `position += len(word)` for a
logical shell word. The sole remaining token-length cursor increment is for
physical case terminators (`;;&`, `;;`, and `;&`), which cannot contain an
active continuation and are intentionally outside the logical-word helper.
Direct operator consumers already retain `_logical_token_match()`'s returned
end.

The correction tests cover every internal continuation split and the
immediately-after-token continuation form for the full multi-character
consumer set. They cover scalar and strict-output candidates, schema
admission, scheduler preflight, and direct inline/spill execution. Rejected
contexts prove no resolver, spill, stdout, or process-launch side effect.
Quoted and escaped literal controls retain their physical offsets and literal
meaning. The new continued-token complexity canary preserves linear growth and
an absolute indexed-read cap.

No new compatibility defect was found around quote removal, comments,
heredoc delimiter/body handling, command/backtick/process/arithmetic/parameter
nesting, arrays, conditionals, extglobs, brace expansions, here strings, or
phase re-entry. The correction consumes more accurate authored boundaries; it
does not introduce a normalized shadow string or a second parser authority.

## Prior finding disposition

| Prior finding | Final disposition | Live evidence |
|---|---|---|
| Initial spec I-1 — Archon profile alone activated v3 behavior | **Closed** | `BashExecutor` requires both effective Archon profile and sealed normalizer version 3; the scheduler passes the recorded version. Legacy and admitted v1/v2 stay on the prior renderer. |
| Initial spec I-2 / spec rereview remainder — admission and scheduler did not share Bash context authority | **Closed** | Schema admission, scheduler preflight, strict resources, and direct rendering all use `bash_output_references()` / `classify_bash_reference_spans()` in Bash mode. Escaped/comment candidates are filtered before output resolution. |
| Initial spec I-3 — Windows inline and shell read-failure behavior unproved | **Closed** | Retained behavioral tests cover exact inline Windows-gated argv/empty inheritance and real-shell nonzero read failure despite the sentinel `printf`. |
| Initial quality I-1 — premature command-frame exit and missed dialect contexts | **Closed** | Unified authored-source frame state covers nested case/heredoc/function/coproc/conditional state, legacy arithmetic, ANSI-C quoting, and the later arithmetic/process/phase siblings. |
| Initial quality I-2 — strict output parsing preceded literal context authority | **Closed** | Grammar-neutral candidates are context-classified before strict parsing; malformed escaped/comment text remains literal and physical error offsets are rebased correctly. |
| Initial quality I-3 — verified spill inode remained mutable | **Closed** | Verified bytes are copied into bounded immutable snapshots, source pathnames are removed, and the shell receives anonymous publication descriptors. |
| Initial quality I-4 — pre-spawn descriptor ownership was incomplete | **Closed** | Renderer construction and the executor's outer `finally` own transport/read descriptors across evidence, callbacks, output setup, spawn, publication, artifacts, and every return/fault. |
| Quality rereview N-1 — descriptor number could be closed/reused for another readable object | **Closed** | Path-free identities captured at pipe creation are checked on stable `F_DUPFD_CLOEXEC` pins before child creation; mismatch maps to `bash_spill_integrity`. |
| Closure 1 Q-1 — active continuation could join unsupported operators | **Closed** | The authored-source scanner recognizes joined command, arithmetic, parameter, conditional, heredoc, process, and related operators while preserving physical spans. |
| Closure 1 Q-2 / spec closure I-1 — indexed assignment subscripts admitted arithmetic data | **Closed** | Direct, compound, wrapper, builtin, quoted/dequoted, function/coproc, redirection, and fd-heredoc command-position variants are rejected before resolution or launch. |
| Closure 2 I-1 — command-position variants missed indexed assignments | **Closed** | Command position survives leading redirections, declarations/coprocesses, wrapper options, and assignment builtins. |
| Closure 2 I-2 — unsupported array range overwrote an escaped-literal decision | **Closed** | `decide_range()` preserves established literal decisions; ignored candidates acquire no dependency, resolution, or spill. |
| Closure 2 I-3 — quoted-heredoc body continuations were globally removed | **Closed** | Quoted bodies preserve literal physical pairs while unquoted bodies retain active continuation processing. |
| Closure 3 spec I-1 / quality I-4 — physical comment handling hid a following real heredoc | **Closed** | One authored-source phase-state scanner preserves physical comment termination and queues following heredocs in the correct phase. |
| Closure 3 quality I-1 — fd-prefixed heredoc consumed command position | **Closed** | Descriptor words 0-9 are cleared for `<<`/`<<-` without changing ordinary numeric-word behavior. |
| Closure 3 quality I-2 — wrapper/builtin/assignment recognition ignored quote removal | **Closed** | Bounded quote removal retains authored coordinate mappings and recognizes single, double, escaped, and concatenated forms. |
| Closure 3 quality I-3 — joined `<<-` could be misparsed | **Closed** | Heredoc parsing follows continuations around the complete operator and delimiter, queues multiples, and retains tab stripping. |
| Closure 4 spec I-1 — process-substitution command bodies were admitted | **Closed** | Unquoted `<(...)` and `>(...)` open bounded command frames; scalar/output references inside fail before side effects while quoted literals remain data. |
| Closure 4 quality I-1 — shallow physical pre-scan invented/missed heredocs | **Closed** | Fix Round 8 removed the divergent pre-scan; arithmetic/conditional false tokens and nested quoted heredocs share one phase-aware authority. |
| Closure 4 quality I-2 — prior continuation lost comment word-start state | **Closed** | Unified authored state preserves separator/word-start and physical comment semantics through prior continuations. |
| Closure 5 common I-1 — logical shell-word consumers discarded authored ends | **Closed by this correction** | Every consumer uses `shell_word_end()`'s physical end; full internal/after-token matrices, shared boundaries, physical controls, and linearity checks pass. |

No prior Critical or Minor finding was retained. No new finding was established.

## Goal-backward Task 11 contract trace

| Contract family | Result | Evidence |
|---|---|---|
| Archon-v3-only activation and historical compatibility | **PASS** | Secure rendering requires Archon plus normalizer 3. Unversioned, Hermes legacy, and admitted Archon v1/v2 preserve the previous threshold/pathname renderer and evidence shape. |
| Shared static/runtime Bash context authority | **PASS** | Admission, scheduler preflight, strict resource rendering, and direct executor defense call the same bounded classifier before resolution or byte-size selection. |
| Approved simple-token contexts and replacement table | **PASS** | Unquoted spill replacement is `"${VAR}"`, double-quoted replacement is `${VAR}`, and single-quoted replacement is `'"${VAR}"'`; inline/spill tests preserve exact word containment and data semantics. |
| Escapes, comments, heredocs, quotes, redirections, nesting, and ambiguous states | **PASS** | Escaped/comment candidates remain literal. Heredoc delimiters/bodies, command/process/backtick/arithmetic/parameter substitutions, arrays, conditionals, ANSI-C, extglob, brace expansion, and unterminated/ambiguous states fail closed where required. |
| UTF-8 byte boundaries and content identity | **PASS** | 32,767/32,768/32,769-byte and multibyte boundaries pass. Empty values, spaces, quotes, dollar signs, backticks, globs, Unicode, terminal `x`, metacharacters, and trailing newlines remain exact in all three quote contexts. |
| NUL/count/per-value/aggregate bounds and deduplication | **PASS** | NUL is `bash_substitution_nul`; limits are 500,000 bytes per value, 64 distinct files, and 2,000,000 aggregate bytes; repeated exact bytes reuse one spill. |
| Descriptor-relative materialization and immutable consumption | **PASS** | No-follow/exclusive `0600` creation, bounded writes, `fsync`, regular/single-link and identity/size/digest checks precede detached snapshots and anonymous pipe publication. The shell never reopens a pathname. |
| Task 10 inherited-descriptor authority and lifecycle | **PASS** | Only bounded read descriptors plus aligned path-free identities reach `ManagedProcessTree`; stable pins are checked before exec, unrelated handles stay closed, parent reads close after pin/exec confirmation, and publisher/process/output ownership is exception-safe. |
| Exact command and evidence authority | **PASS** | The immutable rendered command is exact `argv[-1]`. Evidence contains only bounded template/rendered sizes and digests, spill count/total/content digests, and descriptor manifest; no command text, value, path, or private identity is projected. |
| Durable catalog | **PASS** | `bash_substitution_nul`, `bash_substitution_limit`, `bash_spill_integrity`, and `bash_reference_context_unsupported` are registered for Archon normalizer v3 and exercised through real behavior paths. |
| Native Windows behavior | **PASS within the specified boundary** | Large v3 values fail closed before launch when descriptor inheritance is unavailable; inline values retain the existing platform-gated `bash -c` construction and managed containment. Native Job Object execution remains the expected local platform skip. |
| Physical offsets and bounded linearity | **PASS** | Authored spans remain ordered/disjoint and map to original source. First-character guards, fixed nesting, the continuation matrices, and doubling/absolute indexed-read canaries preserve linear behavior. |
| Prompt cache, narrow waist, and privacy | **PASS** | No model tool, live system-prompt/history mutation, behavioral environment setting, raw value/path API, provider/session surface, or Desktop authority was added. |
| Task 12 and Phase 4 boundary | **PASS** | No `PluginAgentSessionMissingError`, persistent-session recovery, or Task 12 implementation appears in the Task 11 range. `loop.until_bash` remains on its pre-existing `secure_v3=False` Phase 4-deferred renderer. |

## Fresh verification

All Python tests used `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`. No direct `pytest` invocation was used.

### Exact eleven-file closure set

The exact retained closure command covered:

- `tests/plugins/workflow/test_phase3_bash_substitution.py`
- `tests/plugins/workflow/test_phase3_code_catalog.py`
- `tests/plugins/workflow/test_bash_e2e.py`
- `tests/tools/test_managed_process.py`
- `tests/plugins/workflow/test_security_boundaries.py`
- `tests/plugins/workflow/test_performance_bounds.py`
- `tests/plugins/workflow/test_resources.py`
- `tests/plugins/workflow/test_strict_output_references.py`
- `tests/plugins/workflow/test_phase3_bash_lexer_security.py`
- `tests/plugins/workflow/test_phase3_bash_reference_ordering.py`
- `tests/plugins/workflow/test_phase3_bash_descriptor_faults.py`

Fresh result: **11 files, 1,799 tests passed, 0 failed in 26.5 seconds**,
with 14 workers, the existing native-Windows managed-process platform skip,
and no retry available or used.

### Affected resource/scheduler/performance set

The fresh affected set covered `test_resources.py`, `test_scheduler.py`,
`test_parallel_scheduler.py`, and `test_performance_bounds.py`.

Fresh result: **4 files, 106 tests passed, 0 failed in 14.9 seconds**, with
14 workers and no retry available or used.

### Static and identity gates

- Ruff check on all four correction production/test files: **PASS**.
- Ruff format check on the three format-clean correction files: **PASS**.
  `test_performance_bounds.py` retains its documented pre-existing whole-file
  formatter drift and was not mechanically rewritten.
- `git diff --check 0782af4f5..49ffbccf`: **PASS**.
- `git diff --check 25fc0397..49ffbccf`: **PASS**.
- Final branch/HEAD/tree reauthentication: **PASS**, unchanged from the pinned
  report-only identity above; worktree clean before this report write.

## Closure decision

Task 11 is **functionally specification-complete** on implementation tree
`7560124795fca2d7c2f167874423e38042f40d83`. The authored-end correction closes
the last retained Important finding without reopening any prior context,
descriptor, content, compatibility, evidence, platform, complexity, privacy,
legacy, Task 12, or Phase 4 boundary. This independent review has **0 Critical,
0 Important, and 0 Minor findings**. Task 11 may close after the separate
quality closure review agrees on the same production tree; Task 12 must not
begin before both closures are recorded.
