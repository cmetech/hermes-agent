# Task 11 Quality and Security Closure Review

## Verdict

**FAIL.** Task 11 is not ready for quality/security closure.

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 2 |
| Minor | 0 |

The fix package correctly closes the inherited-descriptor identity race (N-1), and the direct top-level `(( ... ))` case is now rejected. However, I-1 remains open: POSIX backslash-newline removal can form unsupported multi-character shell operators after the current lexer has classified a reference, and indexed-array assignment subscripts still admit references into an arithmetic evaluation context.

## Authenticated review identity

- Worktree: `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-language-phase-3-semantic-compatibility-resilience`
- Branch: `feat/workflow-language-phase-3-semantic-compatibility-resilience`
- Reviewed HEAD: `fb866299d46031f16e6eb557891e4f216c3ca9c6`
- Reviewed tree: `a283230cc80f9b359fe2d999a331032a18e15305`
- Task 11 base: `25fc0397aafcd9b373169c189a2566a2fa570aff`
- Fix-package base: `0afb27113e97278394d5e2b680cde66bbc02bb72`
- Authenticated package: `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-11-fix4-review.diff`
- Package SHA-256: `6952087a41e26291b65e50c420055584cbf0fea5d611278d115377c0e6fac46e`
- The checkout was clean at identity verification. `git diff --check` was clean for both `25fc0397a..fb866299d` and `0afb27113..fb866299d`.

The review covered the full Task 11 range and the authenticated fix package, not only the final commit. I read the approved Phase 3 design, implementation plan, Task 11 brief/report, prior Task 11 spec and quality reviews, the complete central implementation files, and the Task 11 security/compatibility tests. I traced admission through schema/resource/scheduler preflight, rendering, descriptor publication, process launch, result mapping, and cleanup. Runtime observations used only benign numeric/string data.

## Prior-finding dispositions

| Finding | Disposition | Evidence |
|---|---|---|
| I-1: references admitted in shell-evaluated arithmetic and sibling grammar contexts | **NOT ADDRESSED** | The final fix recognizes an immediate top-level `((` at `bash_rendering.py:666-671`, but it does not model operators formed by backslash-newline removal and does not recognize indexed-array assignment subscripts. Q-1 and Q-2 below are concrete remaining cases. |
| N-1: a closed/reused inherited descriptor could be accepted as a different readable object | **ADDRESSED** | The renderer captures `(st_dev, st_ino, S_IFMT)` immediately after pipe creation; `ManagedProcessTree.spawn()` validates aligned identities; `_pin_read_only_descriptors()` first creates a stable `F_DUPFD_CLOEXEC` duplicate and compares that duplicate's identity before launch. Mismatch cleanup and workflow error mapping are covered. |

The earlier I-2/I-3/I-4 corrections remain intact in the reviewed tree: admission and runtime share the classifier, scalar and output candidates are jointly validated, failure evidence remains value/path-free, and cleanup/limits remain bounded.

## Findings

### Critical

None.

### Important

#### Q-1 — Backslash-newline removal can form an unsupported operator after classification

**Locations:** `plugins/workflow/bash_rendering.py:567-578`, `plugins/workflow/bash_rendering.py:638-671`, `plugins/workflow/bash_rendering.py:876-886`, `plugins/workflow/bash_rendering.py:907-953`, and `plugins/workflow/bash_rendering.py:1137-1257`.

The classifier skips a backslash and its following newline as two physical source characters. POSIX/Bash shell processing removes that pair before token recognition. Consequently, two characters that the classifier inspected separately can become one multi-character operator in the shell's logical input. The immediate `$((`/`$(`/`${`/`<<`/`((` recognizers therefore do not see every operator that the shell sees.

Benign reproduction 1:

```text
template: (\
( $USER_MESSAGE )); printf '%s' arithmetic
value:    1
classifier result for $USER_MESSAGE: quote=None (admitted as a simple token)
/bin/sh result: exit 0, stdout "arithmetic"
```

After line-continuation removal, the shell sees `(( 1 ))`; the reference is in arithmetic syntax, not a simple command token.

Benign reproduction 2:

```text
template: printf '%s' "$\
(printf '%s' $USER_MESSAGE)"
value:    7
classifier result for $USER_MESSAGE: quote='"' (admitted as double-quoted text)
/bin/sh result: exit 0, stdout "7"
```

After removal, the shell sees command substitution. The same root cause applies conservatively to other joined operator prefixes such as `$` plus `{` and `<` plus `<`.

This violates the Phase 3 fail-closed contract: admission and rendering can label a reference as a supported simple-token context even though the executing shell places it in a nested/evaluated or heredoc context. The behavior is template-structure dependent and is not repaired by inline quoting or descriptor-backed spill transport.

**Required correction:** recognize shell operators on a logical input stream that has POSIX backslash-newline pairs removed while retaining an exact map to physical source spans, or reject any reference whose context depends on such a join. Apply the same logical-coordinate handling to admission and rendering. Add admission and direct-executor coverage for joined arithmetic, command-substitution, parameter-expansion, and heredoc operators, with both scalar and output references and inline/spill values where applicable.

#### Q-2 — Indexed-array assignment subscripts remain an unclassified arithmetic context

**Locations:** `plugins/workflow/bash_rendering.py:638-671` and `plugins/workflow/bash_rendering.py:876-886` (classification), with shared admission at `plugins/workflow/bash_rendering.py:907-953` and runtime enforcement at `plugins/workflow/bash_rendering.py:1137-1153`.

The final fix recognizes bare top-level `(( ... ))`, but the generic word-state fallback still treats a reference inside an indexed-array assignment subscript as an ordinary unquoted token. Bash evaluates that subscript arithmetically.

Benign reproduction:

```text
template: a[$USER_MESSAGE]=9; printf '%s' "${a[2]}"
value:    1+1
classifier result for $USER_MESSAGE: quote=None (admitted as a simple token)
Bash result: exit 0, stdout "9"
```

The numeric expression selects index `2`, demonstrating that the admitted reference is interpreted by arithmetic grammar. This is the sibling subscript case explicitly left open by the prior I-1 review, so recognizing only a literal top-level `((` is insufficient.

**Required correction:** add grammar-aware fail-closed handling for indexed-array assignment and compound-assignment subscript positions, or conservatively reject reference candidates in assignment words whose subscript grammar cannot be proven simple. Add admission and direct-executor tests for scalar/output references and inline/spill values, including platform/shell compatibility expectations.

### Minor

None.

## Descriptor identity seam assessment

N-1 can be closed.

- `_SpillTransport.from_snapshots()` captures a path-free identity from each pipe read end immediately after `os.pipe()` (`plugins/workflow/bash_rendering.py:93-109`). The identity is private rendering state and does not enter public evidence.
- `InheritedDescriptorIdentity` requires exact, non-negative integer fields and captures device, inode, and file type using `fstat()` (`tools/managed_process.py:170-194`). This is sufficient to distinguish the intended pipe from the regular-file reuse described by N-1 without granting path authority.
- `ManagedProcessTree.spawn()` preserves its descriptor count, uniqueness, standard-descriptor, shell/pre-exec, and native-Windows fail-closed checks. Optional identities must be a correctly aligned sequence of the dedicated type (`tools/managed_process.py:815-879`).
- `_pin_read_only_descriptors()` atomically duplicates each caller descriptor first, then compares the stable duplicate's identity and verifies read-only access (`tools/managed_process.py:258-311`). A concurrent close/reuse after duplication cannot change which open file description the pin denotes. All internal pins created before a mismatch are closed; ownership of caller descriptors is unchanged.
- The Bash executor passes the aligned identities and maps descriptor-validation failure to stable `bash_spill_integrity` evidence only when a spill descriptor exists (`plugins/workflow/executors/bash.py:214-241`). The surrounding `finally` paths still close/join renderer publication and process/output resources.
- Descriptor count remains bounded at 64. Native Windows continues to reject inherited-descriptor launch; inline substitution remains available there. Native Windows was not available in this Darwin review environment, so that branch was assessed statically and by its fault-injection tests.

I found no additional descriptor race, cleanup leak, evidence disclosure, or API-compatibility defect in the reviewed seam.

## Compatibility, bounds, cleanup, and API assessment

- Archon v3 gating remains localized; legacy Bash execution and Loop workflow paths are not routed through the new renderer.
- Byte accounting remains UTF-8 based, per-value and aggregate spill bounds are checked before materialization, and descriptor count remains bounded.
- Descriptor-backed values are still copied from immutable byte snapshots and never reopened through a spill pathname.
- Renderer identity fields are excluded from representation/comparison and are not included in failure metadata. Evidence contains counts, sizes, and digests rather than raw values or paths.
- Publication threads, pipe ends, pinned descriptors, subprocess trees, and output streams retain explicit ownership and cleanup paths on render, publication, spawn, cancellation, timeout, limit, and result failures.
- The optional identity parameter is backward compatible for generic `ManagedProcessTree.spawn()` callers; callers that do not supply identities retain the existing validation behavior.
- No model-tool surface, system-prompt construction, or conversation-history behavior changes in Task 11, so the narrow-waist and prompt-cache contracts are unaffected.

Apart from Q-1 and Q-2, I found the implementation appropriately bounded and the descriptor API coherent.

## Test assessment

Fresh review run, using only the required wrapper with retries disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
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

Result: 11 files, 484 passed, 0 failed, 1 skipped, 14.3 seconds, 14 workers.
```

No direct `pytest` invocation was used. The suite provides strong coverage for inline/spill equivalence, output/scalar shared admission, evidence, limits, cleanup, descriptor faults, reference ordering, and legacy/Loop compatibility. Its green result does not close the review because it lacks the benign backslash-newline operator-joining and indexed-array subscript cases above. Those observations were independently reproduced with the classifier and the locally available `/bin/sh` and Bash interpreters; they were diagnostic observations, not alternate test invocations.

## Closure recommendation

Do not close Task 11 and do not advance to Task 12 on this tree. Close N-1, keep I-1 open, correct Q-1 and Q-2, add the missing admission/direct-executor regressions, then rerun the full Task 11 acceptance set through `scripts/run_tests.sh` with retries disabled and obtain a fresh independent closure review.
