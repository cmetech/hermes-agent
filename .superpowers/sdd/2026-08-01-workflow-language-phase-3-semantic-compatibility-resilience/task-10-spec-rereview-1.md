# Phase 3 Task 10 Specification Closure Rereview 1

**Verdict:** PASS

**Reviewed HEAD:** `6b66eff5e1fa6c8cd35db010661b6f42ac1da104`

**Reviewed tree:** `501862ba29c1f8a638c67d478de892879b80e214`

**Closure baseline:** `1bdb4d99d`

**Severity counts:** 0 Critical, 0 Important, 0 Minor

## Scope reviewed

I independently reread the approved Task 10 plan, the Phase 3 design boundary
that consumes this primitive in Task 11, both original Task 10 review reports,
the complete original implementation range, and the full closure fix
`1bdb4d99d..6b66eff5e`. I traced descriptor validation and pinning, fixed-number
remapping, the POSIX bootstrap and synchronous exec-error channel, process
identity/session/tree ownership, every parent-side cleanup path, native-Windows
gating, the customization ledger, and the retained focused/live gates. I made
no production or test edits.

## Findings

No Critical, Important, or Minor specification findings.

## Closure of the original findings

### Writable authority is rejected before child creation

The launch boundary now duplicates each nominated POSIX descriptor with
`F_DUPFD_CLOEXEC` and inspects the pinned open-file description with `F_GETFL`.
Only `O_RDONLY` is admitted. `O_WRONLY` and `O_RDWR` regular files, writable
pipe endpoints, sockets whose access mode is read/write, and access-mode
inspection failures are rejected before `Popen`. Failure closes every internal
pin while retaining caller ownership. Read-only regular files and pipe read
ends remain accepted.

The new tests prove read-only regular-file and pipe consumption, writable
regular-file and pipe rejection without child creation, inspection-failure
cleanup, and caller-handle usability after every rejection. The generic seam
continues to reject standard descriptors, duplicates, closed descriptors,
non-integers, raw `pass_fds`, and lists above 64.

### Validated open-file identity is pinned through exec

The implementation no longer passes caller descriptor integers directly to
`Popen`. It owns CLOEXEC duplicates above every nominated target number,
validates those duplicates, and passes only those pins plus a private status
descriptor into a minimal isolated Python bootstrap. The bootstrap remaps each
pin to the exact nominated child descriptor number with `dup2`, closes the
pins, and synchronously `execvpe`s the requested executable. Because all pins
and the status pipe are allocated above the target range, remapping cannot
clobber another pin or status handle.

A deterministic competing-thread test closes the caller descriptor and reuses
its number after validation but before the `Popen` handoff. The child still
reads the original pinned bytes at the nominated number while the caller's
reused number exposes the replacement. Multi-descriptor and concurrent-launch
tests additionally prove exact-number/content isolation.

## Preserved process contract

- The bootstrap is launched with the original `start_new_session`, environment,
  working-directory, user/group, umask, process-group, stdio, and other
  supported `Popen` settings already applied. It `exec`s in place, so the
  requested program retains the same PID, session, process group, process-tree
  containment, termination/escalation, resource accounting, and reap identity.
- The bootstrap restores the same configured signal behavior and uses no
  `preexec_fn`. Inherited-descriptor launches reject caller `preexec_fn` and
  `shell=True` instead of introducing an unsafe second child-side mutation
  path.
- The private CLOEXEC status descriptor closes on successful target exec. An
  exec failure is returned synchronously to the parent with its errno/message;
  the bootstrap child is killed/reaped by the established spawn-failure path.
  Tests prove missing-executable failure leaves no internal descriptor leak and
  does not close the caller descriptor.
- Pins and status descriptors are closed on pin/inspection failure, status-pipe
  creation failure, `Popen` failure, bootstrap exec failure, and successful
  launch. Caller-owned nominated descriptors are never closed by the primitive.
- `close_fds=True` plus the explicit pin/status `pass_fds` tuple keeps unrelated
  descriptors out of the child. Existing live tests retain session-leader,
  descendant termination, process identity, registry, Job Object, resource,
  escalation, and reap coverage.
- Native Windows still rejects every nonempty descriptor request before Job
  Object creation; empty requests retain the historical Windows path unchanged.

## Boundary and ledger assessment

The customization entry now accurately records read-only enforcement, pinned
open-file identity, exact child-number remapping, internal-handle cleanup,
caller ownership, and Windows fail-closed behavior. It remains adjacent to and
does not alter the historical managed-process entry or its verified upstream
identity. The fix introduces no workflow value knowledge, spill creation,
shell prologue, rendered-command evidence, executor wiring, or other Task 11
behavior.

The earlier merge-gate and compatibility-projection repair commits remain
bounded to already-implemented Phase 3 truth. They add no API field, path,
provider response, Desktop authority, recovery state, or Task 14 surface.

## Verification evidence

All Python tests were run only through `scripts/run_tests.sh` or the repository
live gate, with `HERMES_TEST_FILE_RETRIES=0`.

1. Focused Task 10 suite — `tests/tools/test_managed_process.py`,
   `tests/tools/test_process_registry.py`, and
   `tests/scripts/test_workflow_merge_gate.py`: **3 files, 208 tests passed, 0
   failed, no retries**.
2. Strict customization validation —
   `../../.venv/bin/python scripts/check_upstream_customizations.py --strict
   --base-ref HEAD`: **PASS**.
3. Live base merge gate — `scripts/test_workflow_merge_gate.sh --phase base`:
   **53 Python files, 2,565 tests passed; installed-distribution integration 1
   passed; Desktop Vitest 11 files, 155 tests passed; 0 failures and no Python
   retries**. The gate reported
   `TESTED_BASE_SHA=6b66eff5e1fa6c8cd35db010661b6f42ac1da104`.
4. Ruff on `tools/managed_process.py` and
   `tests/tools/test_managed_process.py`: **PASS**.
5. `git diff --check` for both the fix and complete Task 10 range: **clean**.
6. The reviewed worktree was clean before this retained report was written.

## Final assessment

The closure fix resolves both original Important findings at the exact generic
launch boundary. Task 10 now provides bounded, read-only, identity-pinned POSIX
descriptor inheritance at exact child numbers while preserving process-tree
and Windows behavior, caller ownership, cleanup, the narrow core waist, and
the Task 10/Task 11 separation. Task 10 is specification-complete and ready to
close.
