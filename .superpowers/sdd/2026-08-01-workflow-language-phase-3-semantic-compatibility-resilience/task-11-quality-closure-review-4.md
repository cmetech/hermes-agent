# Task 11 Quality and Security Closure Review 4

## Verdict

**FAIL.** Task 11 is not ready for quality/security closure.

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 2 |
| Minor | 0 |

Fix Round 7 closes the four exact examples retained by closure review 3:
numeric-fd heredocs preserve command position, quote-removed wrapper and
assignment-builtin words are recognized, joined `<<-` forms are queued, and a
comment beginning at an already-known physical word boundary is preserved.
The physical heredoc/comment pre-scan is still not shell-phase-aware, however.
It invents a heredoc from arithmetic shift syntax and misses a real heredoc in
a command substitution nested under outer double quotes. It also loses comment
word-start state when that comment is reached through an earlier active line
continuation, which can make the shared strict classifier admit a real heredoc
body reference. These are compatibility and fail-closed boundary defects in
the grammar classes Fix Round 7 intended to close.

## Authenticated identity and scope

- Worktree:
  `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-phase-3-semantic-compatibility-resilience`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Task 11 base: `25fc0397aafcd9b373169c189a2566a2fa570aff`
- Task 11 base tree: `4f1c15c8d1f3653b1d44e7882f2d50914199237b`
- Fix Round 7 base: `88e5ff94b81bdaeb6dcd5c3bc355e543b155e256`
- Fix Round 7 base tree: `a5c28343744a2975baebe37ad03b1420400d465b`
- Reviewed implementation commit:
  `edd0f9ce4c96f844045a4894d7faba218e01850c`
- Reviewed implementation tree:
  `8eafcf9a8e6e1831d3689dba133e99da8d11b52f`
- Reviewed implementation subject:
  `fix(workflow): close bash parser compatibility gaps`
- Checkout HEAD before this report:
  `c82f099e0a06f8fc94fc52aa3eaac664986d33d8`
- Checkout tree before this report:
  `8bda78498be58e1ac7f905dffd0d52920e4f2bc9`
- Checkout HEAD subject:
  `docs(workflow): record task 11 fix round 7`
- `edd0f9ce4..c82f099e0` changes only the retained Task 11 report. There is no
  production, test, harness, or ledger delta after the reviewed implementation
  tree.
- Authenticated Fix Round 7 package:
  `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-11-fix7-review.diff`
- Package SHA-256:
  `1365e8af42c67d2ff47377a64cbae3aed10ce23908db31506e5b5338a73d2299`
- The package diff body is byte-identical to
  `git diff -U10 88e5ff94b..edd0f9ce4` (`cmp` exited 0); both bodies have
  SHA-256 `4a365985661a7abf0430dc01bb1d9e582f060848e32de59e0be7b9d78be60c4a`.
- `git diff --check` was clean for Fix Round 7 and the complete Task 11
  implementation range. The worktree was clean before this permitted report
  write.

I read the complete root `AGENTS.md`, approved Phase 3 design and plan, their
final approval rereviews, Task 11 brief/report, every retained Task 11
specification and quality review through closure review 3, the authenticated
Fix Round 7 package, every changed production/test file in full, and the live
admission, scheduler, renderer, executor, managed-process identity, lifecycle,
and evidence seams required to decide closure. Parser observations used only
benign strings and numeric expressions.

## Prior-finding dispositions

| Prior finding or contract | Disposition | Evidence |
|---|---|---|
| Original specification I-1 — Archon profile alone activated v3 | **ADDRESSED and unaffected** | `BashExecutor` still activates `secure_v3` only for the Archon 2026-07 profile plus sealed normalizer v3. Fix Round 7 does not change executor gating. |
| Original specification I-2 — context authority absent/inconsistent at admission and scheduler | **ADDRESSED and unaffected** | Schema admission, strict resource rendering, scheduler preflight, and direct rendering still share `bash_output_references()` / `classify_bash_reference_spans()`. The new defects therefore propagate consistently rather than reflecting parser drift. |
| Original specification I-3 — native-Windows inline/read-failure coverage | **ADDRESSED and unaffected** | Fix Round 7 has no executor or managed-process delta. Native Windows execution remains unavailable on this Darwin host. |
| Original quality I-2 — strict grammar before escaped/comment filtering | **ADDRESSED and unaffected** | Literal decisions still precede strict output parsing. No precedence regression was established. |
| Original quality I-3 — mutable verified spill inode | **ADDRESSED and unaffected** | Verified bytes remain detached into immutable snapshots; shell launch receives anonymous read-only publication descriptors rather than reopening spill pathnames. |
| Original quality I-4 — pre-spawn descriptor ownership | **ADDRESSED and unaffected** | Renderer, publisher, executor, process, and stream cleanup paths have no Fix Round 7 delta. |
| N-1 — descriptor number close/reuse could substitute another readable object | **ADDRESSED and unaffected** | `InheritedDescriptorIdentity` remains path-free; `ManagedProcessTree.spawn()` obtains a stable `F_DUPFD_CLOEXEC` duplicate before comparing identity, checks read-only access, caps inheritance at 64, and fails closed on native Windows. The executor still hands the aligned identities to spawn and maps failure to `bash_spill_integrity`. |
| Closure 3 I-1 — fd-prefixed heredoc consumed command position | **ADDRESSED** | Fix Round 7 clears numeric descriptor words 0-9 when `begin_heredoc()` succeeds and adds leading/multiple `<<`/`<<-` matrices. |
| Closure 3 I-2 — quote removal absent from wrapper/builtin/assignment tracking | **ADDRESSED** | `_quote_removed_bash_word()` and `_assignment_subscript_bounds()` cover single-, double-, backslash-, and concatenated quote-removal forms, including EOF word finalization. |
| Closure 3 I-3 — joined `<<-` misparsed | **ADDRESSED for the reported class** | `_parse_physical_bash_heredoc_delimiter()` follows active continuations around the operator and `-`; every-boundary, multiple-queue, and tab-stripping regressions pass. Important I-1 is a separate context-recognition failure in the pre-scan. |
| Closure 3 I-4 — physical comment logicalization hid a following heredoc | **Direct examples addressed; grammar class NOT CLOSED** | A comment recognized at the pre-scan's current word boundary is preserved. Important I-2 shows that the boundary is lost when the comment begins after an earlier active continuation. |

## Findings

### Critical

None.

### Important

#### I-1 — The physical heredoc pre-scan is context-blind and both invents and misses heredocs

**Locations:** `plugins/workflow/bash_rendering.py:437-535,538-578,662-681`;
coverage gap at
`tests/plugins/workflow/test_phase3_bash_substitution.py:138-143,152-156,706-719`
and the joined/top-level heredoc matrices.

`_continuation_preserved_ranges()` is a second, shallow shell scan used whenever
the template contains any backslash-newline. It tracks one flat quote and
physical `word_start`, but has none of the main classifier's command,
arithmetic, conditional, parameter-expansion, or nested command-substitution
frames. At every unquoted physical `<` it calls the physical heredoc parser.
Consequently, `1 << 2` inside arithmetic is queued as a heredoc with delimiter
`2`. Conversely, an actual heredoc inside `$(...)` nested under an outer
double-quoted word is hidden by the flat outer quote. In both cases the pre-scan
fails to mark the later quoted heredoc body as a literal-continuation range;
`_logical_bash_input()` removes its body backslash-newline and the authoritative
classifier rejects the otherwise valid script as unterminated.

Confirmed benign production observations:

```text
template 1: (( x = 1 << 2 ))
            cat <<'EOF' >/dev/null
            line\
            EOF
            printf '%s' "$USER_MESSAGE"
classifier: bash_reference_context_unsupported — unterminated here-document
/bin/bash:  exit 0, stdout "safe"

template 2: printf '%s' "$(cat <<'EOF' >/dev/null
            line\
            EOF
            printf nested)"
            printf '%s' "$USER_MESSAGE"
classifier: bash_reference_context_unsupported — unterminated here-document
/bin/bash:  exit 0, stdout "safe"
```

The false-token test at lines 138-143 includes arithmetic and conditional
forms, but those fixtures contain no body continuation and therefore bypass the
physical pre-scan entirely. The quoted-heredoc fixtures are top-level, so they
do not exercise shell grammar re-entry inside a quoted command substitution.
This is a valid-workflow compatibility failure and demonstrates that the
physical pass does not yet implement the stated false-heredoc contract.

Replace the independent flat-context decision with one bounded phase-aware
physical/logical stream, or give the pre-scan the same bounded nesting and
quote-re-entry semantics as the classifier while preserving authored offsets
and literal quoted-body bytes. Add false arithmetic/conditional operators
before later quoted heredocs and quoted heredocs nested inside command
substitutions, including outer-double-quoted, scalar/output, admission,
scheduler, inline/spill, and no-launch cases.

#### I-2 — An active continuation before a comment loses word-start state and under-rejects a real heredoc body

**Locations:** `plugins/workflow/bash_rendering.py:461-535,538-578,723-754`;
shared consumers at `plugins/workflow/bash_rendering.py:1451-1497`,
`plugins/workflow/schema.py:1084-1117`,
`plugins/workflow/scheduler.py:1496-1537`, and
`plugins/workflow/resources.py:797-812,944-965`; coverage gap at
`tests/plugins/workflow/test_phase3_bash_substitution.py:131-136,632-657,722-725,1369-1388`.

At lines 502-505, the physical pre-scan skips every non-single-quoted
backslash plus following byte and unconditionally sets `word_start = False`.
That is wrong when an active backslash-newline follows a separator. For a
colon command followed by a space and that continuation, the following `#`
begins a Bash comment at logical word-start, but the pre-scan no longer
recognizes it. It then treats the
comment's own backslash-newline as an active continuation. The logicalizer
joins a following `<<EOF` into the comment, so the main lexer never queues the
real heredoc Bash sees on that next physical line.

Confirmed benign production observations:

```text
template:   : \
            # comment \
            <<EOF >/dev/null
            $USER_MESSAGE
            EOF
            printf '%s' after
classifier: ((33, 46, None),)  # body reference admitted
/bin/bash:  exit 0, stdout "after"; the reference is heredoc body text

template:   : \
            # comment \
            printf '%s' "$USER_MESSAGE"
classifier: ()                 # later executable reference treated as comment
/bin/bash:  with USER_MESSAGE=VISIBLE, exit 0, stdout "VISIBLE"
```

The first result violates the explicit fail-closed rule for an unquoted
heredoc body. The same wrong admission occurs with single-, double-, and
backslash-quoted delimiters, where rewriting would also violate Bash's literal
body semantics. The second result shows the complementary compatibility
failure: a workflow reference Bash executes is left unrendered because the
classifier believes it is comment text. Since admission, scheduler preflight,
and rendering share this authority, there is no later corrective layer.

Current Fix Round 7 comment tests begin directly at a known physical
word-start; they do not enter a comment through an earlier active continuation.
Carry the preceding separator/word-start state across continuation removal,
then preserve a recognized comment only through its Bash physical newline.
Add prior-continuation comment cases followed by unquoted/quoted heredocs and
by an ordinary safe reference across scalar/output, admission, scheduler, and
direct inline/spill no-launch paths.

### Minor

None.

## Other quality, security, compatibility, and privacy assessment

- The shared classifier still gives consistent admission, scheduler, and
  renderer outcomes. The findings are defects in that one authority, not
  bypasses between duplicated parsers.
- Numeric-fd command position, shell quote removal, directly joined `<<-`,
  multiple heredoc queues, tab stripping, EOF finalization, escaped candidate
  precedence, and the direct physical-comment fixtures are materially fixed.
- Physical-to-logical reference coordinate mapping passes the retained offset
  tests. No distinct coordinate corruption was established in this review.
- The physical/logical passes remain bounded forward scans in the exercised
  cases. The continuation, quoted-heredoc, physical-comment, and joined
  multi-heredoc read-count canaries passed. No separate superlinear path was
  established; the two findings are state/grammar errors.
- UTF-8 inline/per-value/aggregate bounds, exact quote replacement table,
  deduplication, immutable snapshot publication, descriptor identity,
  descriptor count/read-only checks, parent closure, native-Windows large-value
  gate, and exact `argv[-1]` evidence have no adverse Fix Round 7 delta.
- Evidence remains limited to sizes, counts, descriptor numbers, and digests;
  no command text, value, spill path, descriptor identity, session data, or
  provider data is projected.
- Legacy, admitted Archon v1/v2, and Phase 4 Loop execution remain outside the
  secure-v3 branch. No model tool, prompt prefix/history, API endpoint,
  environment setting, Task 12 session behavior, or release branch changed.
- Native Windows Job Object and descriptor behavior could not be executed on
  this Darwin host. Fix Round 7 does not modify that seam; the existing native
  Windows fail-closed contract remains represented by platform-neutral tests
  and the platform skip.

## Fresh test assessment

Only the repository wrapper was used, with retries disabled:

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

Fresh result: **11 files, 1,131 tests passed, 0 failed, 20.3 seconds, 14
workers**. The managed-process file reported one platform skip. Retries were
disabled, so no file retry or flaky pass was available.

The suite is strong for the enumerated Fix Round 7 matrices, exact
content/bounds, descriptor identity and faults, cleanup, legacy/version gating,
shared authority, authored-coordinate mapping, and linear-read canaries. Its
green result does not cover a false nested token interacting with a later
quoted-heredoc body continuation, a quoted heredoc under outer-double-quoted
command substitution, or a comment reached after an earlier active
continuation. The classifier-versus-`/bin/bash` observations above were benign
diagnostic commands, not direct test-framework invocations.

## Closure recommendation

Do not close Task 11 and do not advance to Task 12 on this implementation
tree. Keep N-1 and the earlier descriptor, lifecycle, privacy, fd-heredoc,
quote-removal, and joined-`<<-` corrections closed. Repair I-1 and I-2 in one
bounded parser fix round, add the missing scalar/output admission, scheduler,
direct inline/spill, physical-offset, no-launch, and linear-read regressions,
rerun the exact eleven-file acceptance set through `scripts/run_tests.sh` with
retries disabled, and obtain another independent closure review against one
exact implementation tree.
