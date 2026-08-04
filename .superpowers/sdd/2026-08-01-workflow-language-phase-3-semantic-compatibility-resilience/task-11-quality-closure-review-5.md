# Task 11 Quality and Security Closure Review 5

## Verdict

**FAIL.** Task 11 is not ready for quality/security closure.

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 1 |
| Minor | 0 |

Fix Round 8 removes the former split physical/logical parser authority and
closes the retained process-substitution, nested phase-reentry, prior-
continuation comment, here-string, extglob, and brace-expansion examples. One
physical-coordinate defect remains in that unified authority: logical reserved
words may contain active continuations, but several consumers advance by the
raw keyword length instead of the matcher's authored end. That can prematurely
close a command-substitution frame and admit a reference that Bash executes in
the nested command.

## Authenticated identity and package evidence

- Worktree:
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-phase-3-semantic-compatibility-resilience`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Current report-only HEAD:
  `db23ced425d8e3f47a297525a5fa199b17a86a52`
- Current report-only tree:
  `18870b4f07a19986a4b42009250b4d8fe224a397`
- Reviewed implementation commit:
  `7fdcbee8158aa57bef00ace43fab94e82cf566e8`
- Reviewed implementation tree:
  `7e0d50d38b3171ad5e6b3dcfc03ad16a4800555a`
- Reviewed implementation parent / Fix Round 8 base:
  `dbe91d95ab1ec8228ca7717b004b5236f7205f26`
- Task 11 base:
  `25fc0397aafcd9b373169c189a2566a2fa570aff`
- Implementation author/subject:
  `Corey Ellis <corey@cmetech.io>` /
  `fix(workflow): unify bash parser phase state`
- `7fdcbee8..db23ced4` changes only the retained `task-11-report.md`.
  There is no production, test, harness, ledger, Task 12, or Phase 4 delta
  after the reviewed implementation tree.
- Fix Round 8 package `task-11-fix8-review.diff` has SHA-256
  `33c6a551ee786927e92f484d0a0867cfe5dbede3f7b02e032cb6efc88029f6a3`
  and is byte-identical to `git diff -U10 dbe91d95a..7fdcbee81`.
- Complete package `task-11-final-review-5.diff` has SHA-256
  `2cde354a98ab67d47cb4e053e703b5ebf921d7f3c41a968b79687970d662623d`
  and is byte-identical to `git diff -U10 25fc0397a..7fdcbee81`.
- The checkout was clean at intake. Test execution introduced no tracked
  delta. `git diff --check 25fc0397a..7fdcbee81` and Ruff on all four Fix
  Round 8 production/test files passed.

I read the complete root `AGENTS.md`, approved Phase 3 design and plan, the
continue handoff, Task 11 brief/report, all retained Task 10 final reviews and
Task 11 reviews, the complete changed implementation and test files, and the
adjacent schema, resource, scheduler, executor, managed-process, descriptor,
cleanup, evidence, catalog, legacy, and Phase 4 boundaries. The review was
read-only except for this permitted report. Diagnostics used only benign
placeholder strings and numeric/string values.

## Prior-finding dispositions

| Prior finding or contract | Disposition |
|---|---|
| Archon profile alone activated v3 | **ADDRESSED and unaffected.** Secure rendering still requires Archon plus sealed normalizer 3; legacy and admitted v1/v2 paths remain outside it. |
| Admission/runtime/scheduler used different reference authorities | **ADDRESSED and unaffected.** They share `bash_output_references()` and `classify_bash_reference_spans()`. |
| Strict parsing preceded escape/comment authority | **ADDRESSED and unaffected.** Grammar-neutral candidates are classified before strict output parsing. |
| Mutable verified spill inode | **ADDRESSED and unaffected.** Verified bytes are detached to bounded immutable snapshots and published anonymously. |
| Pre-spawn descriptor and publisher ownership | **ADDRESSED and unaffected.** Renderer, publisher, executor, process, and stream ownership remain exception-safe and bounded. |
| Descriptor close/reuse could substitute another readable object | **ADDRESSED and unaffected.** Path-free identities are compared on stable pinned duplicates before child creation. |
| Joined operators, indexed assignments, quote removal, fd heredocs, physical comments, process substitution, extglob, brace expansion, and phase re-entry retained through Closure Review 4 | **The enumerated cases are addressed.** Fix Round 8's one authored-source scanner removes the divergent pre-scan and the new matrices pass. The finding below is a sibling physical-end defect in reserved-word consumers, not a second lexer authority. |
| Exact byte/content, bounds, cleanup, evidence/privacy, Windows gate, legacy, Task 12, and Phase 4 boundaries | **PASS / unaffected.** No contrary defect was found. |

## Important finding

### I-1 — Logical reserved-word matches are advanced by raw keyword length

**Locations:** `plugins/workflow/bash_rendering.py:371-386,639-655,809-871,1275-1299,1314-1340`.

`_logical_token_match()` correctly returns the physical authored end after
skipping active `\\\n` continuations. `shell_word_at()` uses that end to check
the following boundary, but returns only `bool`. Its consumers then reconstruct
an end with `start + len(keyword)` or `position += len(keyword)`. Once a
continuation occurs inside the word, that reconstructed offset is before the
actual authored end.

The full affected consumer set is:

1. `function_declaration_name_end()` uses `start + len("function")`;
2. `coprocess_declaration_end()` uses `start + len("coproc")`;
3. the nested command-prefix table advances raw lengths for `while`, `until`,
   `select`, `then`, `else`, `elif`, `time`, `for`, `if`, `do`, and `-p`
   (the one-character `!` and `{` entries cannot contain an internal
   continuation); and
4. command-substitution `esac`, `in`, and `case` transitions advance by raw
   lengths.

The `[[` open/close consumers are not affected: they separately retain and use
`_logical_token_match(...)[0]`. Direct operator consumers likewise use the
matcher-authored end. `coprocess_declaration_end()`'s compound-starter probes
do not themselves advance the starter, but the helper still returns the raw
`coproc` end and the later `case`/prefix consumers remain affected. Physical
`case` terminators (`;;`, `;&`, `;;&`) intentionally use physical matching and
are a separate, unaffected contract.

The defect is reachable and can be fail-open, not merely an offset nicety.
These benign Bash 5.3-valid templates were accepted by `bash -n` (status 0),
but the production classifier admitted the nested reference as an ordinary
top-level unquoted span:

```text
$(func\
tion f { case x in x) printf '%s' $USER_MESSAGE;; esac; }; f)
classifier: ((42, 55, None),)

$(copr\
oc C { case x in x) printf '%s' $producer.output;; esac; }; wait "$C_PID")
classifier: ((40, 56, None),)
bash_output_references(): producer at (40, 56)

$(if true; th\
en case x in x) printf '%s' $USER_MESSAGE;; esac; fi)
classifier: ((43, 56, None),)
```

In each template Bash removes the active continuation, recognizes the reserved
word, and executes the reference inside `$(...)`. The classifier stops
tracking the declaration/prefix correctly at the raw offset; the `)` ending the
`case` pattern can then be mistaken for the command-substitution close. The
later reference is rendered instead of failing before resolution, spill/output
creation, or launch with `bash_reference_context_unsupported`. This violates
the approved three-simple-token fail-closed boundary for both scalar and
strict-output references, at inline and spill sizes. It is **Important** because
dynamic bytes can reach nested shell execution with a false simple-token
classification and because evidence/admission then attest the wrong context.

The same raw-end class also causes over-rejection/state drift for continued
`case`/`in`/`esac` forms even when it does not produce the early-close shape.
Those compatibility outcomes should be corrected with the security-relevant
fail-open cases, not treated as a separate finding.

**Required bounded correction:** make the boundary-checked shell-word helper
return the authenticated physical authored end (for example `int | None` or a
small match object) rather than a boolean. Every consumer above must advance
to that returned end; none may reconstruct it from the logical token length.
Keep `_logical_token_match()` as the sole continuation-aware token matcher and
do not restore a normalized shadow string or a second parser.

Add table-driven regressions for an active continuation at every internal
split of each multi-character consumer word. At minimum, cover continued
`function`, named/direct `coproc`, command prefixes followed by a `case`
compound, and `case`/`in`/`esac`; scalar and output references; admission,
scheduler preflight, and direct inline/spill no-launch behavior; a safe
reference after the completed construct; exact authored offsets; quoted
literal false-positive controls; and the existing linear-read bound.

## Other quality, compatibility, bounds, and privacy assessment

- Fix Round 8's classifier otherwise operates directly on authored source and
  preserves physical candidate coordinates. Continuation-aware operator probes
  are first-character guarded, and the existing dollar/continuation/heredoc
  linear-read canaries pass. No independent superlinear path was established.
- Schema admission, strict resource rendering, scheduler preflight, and direct
  rendering consistently use the shared classifier. I-1 therefore propagates
  consistently; there is no additional authority-drift finding.
- UTF-8 inline/per-value/aggregate bounds, NUL handling, deduplication, exact
  quote replacement, detached snapshot publication, read-only descriptor
  identity, exact `argv[-1]`, parent closure, process-tree containment, and
  result cleanup remain intact.
- Evidence remains bounded to sizes, counts, descriptor numbers, and digests;
  no command text, value, spill path, private identity, session, or provider
  data was added.
- Legacy, admitted Archon v1/v2, and Phase 4 Loop rendering remain outside the
  secure-v3 path. No Task 12 code, model tool, prompt/history mutation, API
  endpoint, behavioral environment setting, release branch, or literal
  `main` mutation exists in the reviewed range.
- Native Windows descriptor/Job Object execution was unavailable on this
  Darwin host. Fix Round 8 does not alter the established large-value
  fail-closed or inline platform gates.
- No separate additional correctness, security, compatibility, cleanup,
  boundedness, privacy, legacy, or test-quality finding was established.

## Fresh verification

All Python tests were run only through `scripts/run_tests.sh` with
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

Fresh result: **11 files, 1,338 tests passed, 0 failed**, with the existing
native-Windows managed-process platform skip, 14 workers, 21.6 seconds, and no
retry available or used.

Affected resources/scheduler/performance set:

```text
tests/plugins/workflow/test_resources.py
tests/plugins/workflow/test_scheduler.py
tests/plugins/workflow/test_parallel_scheduler.py
tests/plugins/workflow/test_performance_bounds.py
```

Fresh result: **4 files, 105 tests passed, 0 failed**, 14 workers, 14.5
seconds, and no retry available or used.

These green runs are valid evidence for the retained Fix Round 8 matrices,
descriptor/content/lifecycle contracts, scheduler/resource integration, and
complexity canaries. They do not alter the FAIL verdict because no retained
test places an active continuation inside a consumed reserved word and then
uses a `case` pattern to prove the real nested-command boundary.

## Closure recommendation

Do not close Task 11 and do not advance to Task 12 on this implementation
tree. Keep all prior descriptor, lifecycle, context-authority, compatibility,
privacy, legacy, and Phase 4 dispositions closed. Correct I-1 across the full
consumer set, add the missing shared-authority behavioral regressions, rerun
both fresh gates above with retries disabled, and obtain fresh independent
specification and quality closure against one exact implementation tree.
