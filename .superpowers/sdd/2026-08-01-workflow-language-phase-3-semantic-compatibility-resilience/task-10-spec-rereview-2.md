# Phase 3 Task 10 Specification Closure Rereview 2

**Review date:** 2026-08-02

**Task 10 baseline:** `bee92ad4c2b81d63c27e266f84299a3a52a5dc6e`

**Executable-authority fix:** `2d720810a5ab0e8a610a0bc0b884c23312588f26`

**Reviewed HEAD:** `2d720810a5ab0e8a610a0bc0b884c23312588f26`

**Reviewed tree:** `32d0ac59c364867c60489c916fe357e0571f5ef8`

**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 1
- Minor: 0

## Scope reviewed

I independently read the repository instructions, the complete approved Phase
3 design, the Task 10 plan and Task 11 consumer boundary, all four retained
Task 10 review/rereview reports, and the full original implementation plus both
fix ranges. I traced read-only validation, identity pinning, exact-number
remapping, the isolated Python bootstrap, executable selection and error
reconstruction, the CLOEXEC status protocol, every surrounding cleanup path,
caller ownership, argv/environment/cwd/stdio/signal/session/process-group/PID
continuity, concurrency, POSIX tree containment, native-Windows fail-closed
behavior, the customization ledger, and Task 10/Task 11 scope separation. I
made no production or test edits; this report is the only retained file change.

## Important finding

### I-1 — Invalid explicit `executable` values leak every internal launch descriptor

The executable-authority fix correctly distinguishes omitted/explicit-`None`
from an explicit empty executable, but executable normalization still occurs
after the read-only pins and private status pipe have been allocated and before
the method enters the cleanup-owning `try/finally`.

Specifically, `tools/managed_process.py:681-699` pins each caller descriptor,
opens the two private status descriptors, then evaluates
`os.fsdecode(os.fspath(raw_executable))`. The cleanup `try/finally` does not
begin until `tools/managed_process.py:724-726`. If `os.fspath()` or
`os.fsdecode()` rejects or raises from an explicit executable, control leaves
the method without closing the owned pin, status-read descriptor, or
status-write descriptor.

A read-only production-facade diagnostic using one nominated pipe read end and
`executable=object()` reproduced the defect deterministically:

```text
TypeError expected str, bytes or os.PathLike object, not object
before 5 after 8 delta 3
```

Thus every rejected call leaks three internal descriptors. Repetition can
exhaust the parent process's descriptor limit even though no child is created.
The caller-owned pipe remains open, so caller ownership is preserved, but Task
10's fixed launch seam does not yet close all internally owned pins/status
handles on every setup/spawn failure as required by the original identity-pin
remediation and the closure handoff.

**Required remediation:** Move executable normalization before internal
descriptor allocation or place all post-allocation setup under the same
`finally` that closes pins and both status descriptors. Add a real regression
test with an invalid explicit executable (and preferably a raising
`os.PathLike`) that asserts no child effect, the direct compatible exception,
zero parent FD delta, and unchanged caller-descriptor usability. Rerun the
focused Task 10 gate and obtain fresh closure rereviews.

## Closure status of prior findings

- **Original read-only finding remains closed.** POSIX pins are inspected with
  `F_GETFL`; only `O_RDONLY` is admitted. Writable regular files, pipe write
  ends, read/write descriptors, sockets, and inspection failures are rejected
  before child creation without adopting caller handles.
- **Original identity finding remains closed.** Owned CLOEXEC duplicates pin
  the validated open-file descriptions through launch. The bootstrap remaps
  them to exact nominated child numbers, and the deterministic close/reuse and
  concurrent-launch tests prove the child receives the original identity.
- **Executable-authority rereview finding is behaviorally closed for its exact
  cases.** Omitted and explicit `None` select `argv[0]`; an explicit empty
  executable remains empty and fails closed without executing `argv[0]`; a
  missing custom executable propagates the matching OSError class, errno, and
  filename; a successful custom executable preserves exact argv and PID-in-place
  exec behavior. The focused tests also prove caller ownership and no internal
  descriptor delta for those cases.
- **The new I-1 is a separate cleanup gap.** It arises before bootstrap launch
  when explicit executable normalization itself fails.

## Other contract assessment

- The contract remains bounded to at most 64 unique integer descriptors above
  standard input/output/error. Closed descriptors, duplicates, booleans,
  non-integers, standard/negative descriptors, raw `pass_fds`, `shell=True`,
  and caller `preexec_fn` are rejected through the intended fail-closed paths.
- Nonempty native-Windows requests still fail before Job Object creation.
  Empty requests retain the established Windows suspended-child, assignment,
  resume, identity, termination, resource, and reap behavior.
- Successful inherited launches pass only owned pins plus the private status
  handle into the bootstrap. CLOEXEC EOF remains the success authority; target
  exec failure returns bounded errno/message evidence synchronously. Existing
  success, Popen failure, bootstrap exec failure, access-inspection failure,
  status-pipe failure, and close/reuse paths close their internal descriptors.
- The bootstrap execs in place, retaining exact target argv, PID, session,
  process group, environment, cwd, stdio, resource accounting, termination,
  escalation, and reap identity for the supported call shape.
- The customization entry remains adjacent to the historical
  `managed-process-tree` entry, retains the copied upstream identity and exact
  expected subject, and describes only the generic read-only pinned-descriptor
  seam.
- No workflow value, spill file, descriptor manifest, shell lexer/prologue,
  rendered-command evidence, executor wiring, API/Desktop expansion, or other
  Task 11/14 behavior has entered Task 10.

## Fresh verification evidence

All Python tests were run through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Focused Task 10 suite — `tests/tools/test_managed_process.py`,
   `tests/tools/test_process_registry.py`, and
   `tests/scripts/test_workflow_merge_gate.py`: **3 files, 212 tests passed, 0
   failed, no retries**.
2. Strict customization gate —
   `../../.venv/bin/python scripts/check_upstream_customizations.py --strict
   --base-ref HEAD`: **PASS**.
3. Ruff on `tools/managed_process.py`, `tests/tools/test_managed_process.py`,
   `tests/tools/test_process_registry.py`, and
   `tests/scripts/test_workflow_merge_gate.py`: **PASS**.
4. `git diff --check 6b66eff5e..2d720810a`: **clean**.
5. Before this report was written, the branch was exactly
   `feat/workflow-language-phase-3-semantic-compatibility-resilience`, HEAD and
   tree matched the identities above, and the worktree was clean.

## Final assessment

The second fix preserves explicit executable authority for the omitted,
`None`, empty, missing-custom, and successful-custom cases and leaves the
original read-only/identity findings closed. Task 10 is still not ready to
close, however, because invalid executable normalization occurs outside the
owned-descriptor cleanup boundary and leaks three internal descriptors without
creating a child. One bounded cleanup fix plus fresh closure review is required
before Task 11 consumes this primitive.
