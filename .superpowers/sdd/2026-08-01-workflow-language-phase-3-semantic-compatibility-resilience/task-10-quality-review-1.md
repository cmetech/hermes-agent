# Phase 3 Task 10 Independent Quality Review 1

**Review date:** 2026-08-02  
**Task 10 baseline:** `bee92ad4c`  
**Implementation:** `8481a4f0524e460e09ce16f2a94252ae794ecd07`  
**Reviewed HEAD:** `66be2d5362ffa2f1ac34a2c0a7533c331bab4474`  
**Reviewed tree:** `1a811970a9ac609adebdac32fc15aba0e3d033c6`  
**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 2
- Minor: 0

## Scope and evidence reviewed

I read the repository development guide, the complete approved Phase 3 design,
the complete implementation plan and Task 10 contract, and the full three-commit
`bee92ad4c..66be2d536` change. I traced `ManagedProcessTree.spawn()` validation,
POSIX `Popen(pass_fds=...)`, caller descriptor ownership on success and failure,
process identity capture, spawn-exception cleanup, concurrent tree close,
POSIX group termination/escalation/reaping, the Windows Job Object branch,
process-registry callers, the customization ledger, merge-gate selection, and
the bounded API/Desktop compatibility corrections. I checked for Task 11 spill
materialization/rendering and Task 14 projection expansion; neither was added.
The review made no production or test edits; this retained report is its only
file change.

Fresh verification used the repository wrapper with flaky file retries
disabled:

1. Task 10 focused and adjacent gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/tools/test_managed_process.py tests/tools/test_process_registry.py tests/scripts/test_workflow_merge_gate.py`
   — 3 files, 199 tests passed, 0 failed.
2. Strict customization validation:
   `../../.venv/bin/python scripts/check_upstream_customizations.py --strict --base-ref HEAD`
   — passed.
3. Live base customization gate:
   `HERMES_TEST_FILE_RETRIES=0 scripts/test_workflow_merge_gate.sh --phase base`
   — 53 Python files / 2,556 tests passed; installed-distribution integration
   1 passed; Desktop Vitest 11 files / 155 tests passed; Desktop TypeScript
   check passed; `TESTED_BASE_SHA=66be2d5362ffa2f1ac34a2c0a7533c331bab4474`.
4. Ruff on every changed Python file — clean.
5. `git diff --check bee92ad4c..66be2d5362ffa2f1ac34a2c0a7533c331bab4474`
   — clean.

I also ran two read-only production-facade diagnostics. One passed the write
end of a pipe as an inherited descriptor and observed the child write through
it. The other replaced a validated descriptor with a different pipe at the
same integer immediately inside the `Popen` handoff and observed the child read
`replacement`, not the bytes associated with the descriptor at validation.

## Important findings

### I-1 — The primitive accepts writable descriptors despite the read-only contract

`ManagedProcessTree.spawn()` validates integer shape, the 64-entry bound,
standard descriptor exclusion, uniqueness, raw `pass_fds` conflicts, native
Windows exclusion, and openness through `os.fstat()`
(`tools/managed_process.py:500-526`). It never validates descriptor access mode.
Consequently both `O_WRONLY` and `O_RDWR` descriptors are accepted and inherited.

The Task 10 contract introduces this seam for a nominated read-only descriptor,
and Task 11 relies on keeping only verified read-only spill descriptors through
launch. The positive test's pipe read end happens to be read-only, but no
negative test proves the generic launch boundary rejects writable authority.
The review diagnostic passed a pipe's write end and the child successfully
wrote `child-write` through it. That is more authority than the planned seam
advertises, and a future caller error can turn the bounded data-input channel
into a child-to-parent or file-mutation capability.

**Required remediation:** On POSIX, validate the effective access mode at the
launch boundary (for example with bounded `fcntl(F_GETFL)` handling) and reject
`O_WRONLY`/`O_RDWR` plus inspection failures before child creation. Add real
tests for a regular file and pipe covering read-only acceptance, write-only and
read/write rejection, no child creation, and unchanged caller ownership. Keep
native Windows fail-closed before Job creation.

### I-2 — Validation does not pin descriptor identity through process launch

The code calls `os.fstat(descriptor)` and then later supplies only the integer
to `subprocess.Popen(pass_fds=inherited)`
(`tools/managed_process.py:519-526,535-548`). There is no pinned duplicate,
identity token, or documented/exercised exclusive-ownership handoff spanning
those operations. File descriptors are process-global. If another thread or a
cleanup path closes one after validation and an unrelated open reuses the same
number before `Popen` snapshots it, the child inherits the replacement. It may
also change access mode after a future I-1 check.

The review reproduced this deterministically by closing the validated pipe read
descriptor inside the `Popen` handoff, installing a different pipe at the same
integer, and invoking the real `Popen`; the child printed `replacement`. Current
tests cover an already-closed descriptor and prove caller ownership after a
synthetic spawn exception, but they do not cover close/reuse between validation
and launch. Thus the promised exact nominated descriptor is exact only by
number, not by the validated open-file identity.

This becomes security-relevant for Task 11: command evidence and the spill
manifest must refer to the verified object the shell reads, never an unrelated
descriptor that reused its number.

**Required remediation:** Define and implement a race-safe descriptor handoff
that pins the validated open-file descriptions until child creation while
preserving the fixed child descriptor numbers Task 11 requires, or otherwise
make exclusive ownership an enforceable launch object rather than an
unenforced convention. Add a deterministic validation-to-spawn close/reuse
test proving the child either reads the originally validated object or launch
fails before child effects. Also prove pinned/internal handles are closed on
success and every spawn-exception path without closing caller-owned handles.

## Positive findings

- The type checks reject booleans, floats, strings, integer subclasses, standard
  descriptors, negative values, duplicates, closed handles, and lists above 64
  before spawning. The input sequence is snapshotted to a tuple.
- Nonempty native-Windows requests fail before Job Object creation. Empty
  requests retain the existing suspended-child, Job assignment, resume,
  identity, and kill-on-close behavior.
- POSIX launch forces `close_fds=True` and supplies exactly the nominated
  `pass_fds`, overriding a conflicting `close_fds=False`; the live test proves
  an unrelated explicitly inheritable descriptor is closed in the child.
- Caller handles stay open after successful spawn and synthetic `Popen`
  failure. Spawn exceptions retain the existing bounded child kill/wait and
  Windows Job cleanup path.
- Default `start_new_session`, descendant termination, escalation, concurrent
  `close()` serialization, process identity, registry integration, and direct
  child reaping remain intact in the focused and live gates.
- Raw `pass_fds` cannot bypass the bounded named contract. No existing
  `ManagedProcessTree` caller uses that raw escape hatch.
- The customization amendment is adjacent to rather than folded into the
  historical managed-process entry, preserves its upstream identity, and keeps
  workflow values, shell syntax, spill files, and evidence outside the generic
  primitive.
- The merge-gate additions select the six already-implemented Phase 3 focused
  suites exactly once. The compatibility correction merely admits the already
  sealed normalizer v3 and updates stale expectations to current backend truth;
  it adds no fields, raw data, paths, recovery state, or Task 14 endpoint.

## Final assessment

Task 10 preserves process-tree containment, bounded descriptor counts, caller
handle ownership, native-Windows behavior, live customization gates, and the
narrow generic waist. All automated gates are green. It is not ready for Task
11, however, because the new primitive neither enforces its read-only authority
nor pins the validated descriptor identity through launch. Both gaps sit at the
exact security boundary Task 11 will use and require a bounded RED/fix round
before large Bash values are wired to it.
