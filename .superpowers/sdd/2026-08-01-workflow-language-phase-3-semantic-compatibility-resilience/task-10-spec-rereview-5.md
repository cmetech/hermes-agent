# Phase 3 Task 10 Specification Closure Rereview 5

**Review date:** 2026-08-02

**Task 10 baseline:** `bee92ad4c2b81d63c27e266f84299a3a52a5dc6e`

**Prior closure evidence commit:** `5c08398d5`

**PATH precedence repair:** `403d752ccd55a9d778274de4d966ca706b63e104`

**Reviewed HEAD:** `403d752ccd55a9d778274de4d966ca706b63e104`

**Reviewed tree:** `2bf6197019f1354c34808b4d1028893ddcc6b4e1`

**Verdict:** PASS

## Severity summary

- Critical: 0
- Important: 0
- Minor: 0

## Scope reviewed

I independently read the complete repository instructions, the approved Phase
3 design, the Task 10 plan and Task 11 consumer boundary, every retained Task
10 specification and quality review through rereview 4, the complete Task 10
implementation and repair history, and the exact fifth repair
`5c08398d5..403d752cc`. I traced the raw PATH candidate search and error
reconstruction, string/bytes/mixed environment authority, later-candidate
success, all-not-found fallback, environment framing and liveness, read-only
validation, open-file-description pinning, exact-number remapping, executable
and signal authority, setup/spawn/exec cleanup, caller ownership, process
identity/session/tree behavior, native-Windows gating, the customization
ledger, and the Task 10/Task 11 scope boundary. I made no production or test
edits; this report is my only repository change.

## Findings

No Critical, Important, or Minor specification findings.

## Closure of the PATH precedence finding

The final repair changes the raw `execve` candidate loop so `saved_error` is
assigned only when it is still `None` and the current errno is neither
`ENOENT` nor `ENOTDIR`. This matches direct `Popen`/`_execvpe` precedence:

- the first substantive error remains authoritative even if a later candidate
  fails with a different substantive errno;
- search continues after that saved error, so a later executable can still
  succeed and replace no failure with a false negative;
- when every candidate is `ENOENT` or `ENOTDIR`, the last candidate error
  remains the fallback; and
- if PATH produces no candidates, the stable `ENOENT` fallback remains.

The new real regressions prove the first-error case with `EACCES` followed by
`ELOOP`, a later successful executable after an initial `EACCES`, and both
orders of `ENOENT`/`ENOTDIR`. They compare the public exception class, errno,
and filename with direct `Popen`; prove failed candidates have no target
effect; retain zero internal-descriptor growth; and leave the caller-owned
descriptor usable. The repair changes only the saved-error assignment and
does not alter candidate construction, raw argv/environment vectors, status
framing, descriptor mappings, or cleanup.

A separate read-only production-facade diagnostic repeated the first-error
comparison with a string PATH, a bytes PATH, and a bytes PATH in a mixed
string/bytes environment. Each matched direct `Popen` exactly as
`(PermissionError, EACCES, "probe")`, retained zero descriptor growth, and
left the nominated caller descriptor usable. An ambiguous simultaneous string
and bytes PATH remained rejected with the same direct exception before
internal allocation.

## Full Task 10 contract assessment

### Bounded read-only descriptor authority

The named `inherited_descriptors` argument accepts at most 64 unique exact
integers above stdin/stdout/stderr. It rejects standard or negative numbers,
booleans and other non-integers, duplicates, closed descriptors, raw
`pass_fds`, `shell=True`, and caller `preexec_fn` through the intended
fail-closed paths. POSIX descriptors are duplicated with `F_DUPFD_CLOEXEC`,
and the owned open-file descriptions are inspected with `F_GETFL`; only
`O_RDONLY` is admitted. Writable regular files, read/write files, pipe write
ends, sockets, and inspection failures cannot reach target execution and do
not transfer caller ownership.

### Identity pinning and exact child numbers

Owned pins remain live through the intermediate launch and are allocated above
every nominated target. The isolated bootstrap remaps each pin to the exact
nominated child number with `dup2`, then closes the original pin. Its status
and environment pipes are allocated above both targets and pins, so mappings
cannot clobber another pin or private channel. The deterministic caller-number
close/reuse regression proves that a replacement at the same numeric slot
cannot substitute a different open-file identity in the target; multi-handle
and concurrent launches retain exact independent identities. `close_fds=True`
and the explicit private `pass_fds` set keep unrelated descriptors closed.

### Environment, executable, signal, and process compatibility

Explicit empty/minimal, string, bytes, mixed, path-like, non-UTF-8, and
duplicate-normalized-key environments are length-framed through a private
descriptor and reconstructed as raw final-exec entries rather than using the
intermediate Python runtime's mutated `os.environ`. Omitted environments use a
parent byte snapshot. The transport is bounded at 16 MiB including framing,
uses bounded reads/writes, handles payloads above pipe capacity without
deadlock, carries no value in bootstrap argv or status text, and closes both
private handles on success and every failure. PATH authority is validated
before allocation, including fail-closed ambiguous string/bytes PATH input.

Omitted and explicit-`None` executable values select `argv[0]`; explicit empty
remains empty and fails closed; successful and missing custom string, bytes,
and path-like executables retain direct authority, argv behavior, exception
class, errno, and filename identity. Invalid and throwing path-like values are
rejected before internal allocation. The bootstrap reapplies the final-exec
signal dispositions required by `restore_signals` without `preexec_fn`, and
execs in place, preserving target PID, argv, cwd, stdio, session/process-group
identity, process-tree containment, resource accounting, termination,
escalation, and reap ownership.

The CLOEXEC status descriptor makes EOF authoritative for successful final
exec. Bounded exec failures return synchronously; malformed status fails
closed; and parent-side failure kills and waits for any created bootstrap
child. Pinning, pipe creation, `Popen`, environment transport, status reads,
target exec failure, and successful launch all close internal handles while
leaving every caller-owned nominated handle untouched.

### Platform, ledger, and phase boundary

Native Windows rejects every nonempty inherited-descriptor request before Job
Object creation or child launch. Empty requests retain the historical
suspended-child Job assignment/resume, identity, termination, resource, and
query-proven reap behavior.

The `managed-process-inherited-descriptors` customization entry remains
adjacent to the historical `managed-process-tree` entry, copies rather than
advances its upstream identity, retains the required expected subject, and
accurately records the bounded generic seam and its tests. The primitive and
its Task 10 range contain no workflow value, spill file, descriptor manifest,
shell lexer/prologue, rendered-command evidence, executor wiring, API/Desktop
spill surface, session recovery, or other Task 11 implementation.

## Fresh verification evidence

All Python tests were run through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Focused Task 10 gate — `tests/tools/test_managed_process.py`,
   `tests/tools/test_process_registry.py`, and
   `tests/scripts/test_workflow_merge_gate.py`: **3 files, 229 tests passed, 0
   failed, no retries**.
2. Strict customization validation —
   `../../.venv/bin/python scripts/check_upstream_customizations.py --strict
   --base-ref HEAD`: **PASS**.
3. Ruff on `tools/managed_process.py`, `tests/tools/test_managed_process.py`,
   `tests/tools/test_process_registry.py`, and
   `tests/scripts/test_workflow_merge_gate.py`: **PASS**.
4. `git diff --check 5c08398d5..HEAD` and production/test/ledger
   `git diff --check bee92ad4c..HEAD -- ':!*.md'`: **clean**.
5. The direct read-only PATH diagnostic covered string, bytes, mixed, and
   ambiguous PATH environments with exact direct-facade parity, zero internal
   descriptor growth, and retained caller ownership: **PASS**.
6. Before this report was written, the branch was exactly
   `feat/workflow-language-phase-3-semantic-compatibility-resilience`, HEAD and
   tree matched the pinned identities above, and the worktree was clean.

## Final assessment

Fix round 5 closes the final PATH-search compatibility defect without changing
the established descriptor, environment, executable, signal, cleanup,
process-tree, Windows, ledger, or scope guarantees. Task 10 now provides the
approved narrow generic bounded child-descriptor inheritance primitive and is
specification-complete. It is ready to close before Task 11 begins.
