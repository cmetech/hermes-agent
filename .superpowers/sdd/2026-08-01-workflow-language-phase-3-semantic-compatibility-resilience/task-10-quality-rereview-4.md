# Phase 3 Task 10 Independent Quality Rereview 4

**Review date:** 2026-08-02

**Task 10 baseline:** `bee92ad4c2b81d63c27e266f84299a3a52a5dc6e`

**Prior closure evidence commit:** `62398ab6d`

**Environment compatibility repair:** `d907b651c5e580bba6ac368e68b6833808cfc911`

**Reviewed tree:** `9dae77172d2bce1b0d60cc591ed8659c6bd6b9f5`

**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 1
- Minor: 0

## Scope and evidence reviewed

I independently read the complete repository instructions, the approved Phase
3 design, the Task 10 plan and Task 11 consumer boundary, all eight preceding
Task 10 review/rereview reports, the complete Task 10 implementation and repair
history, and the exact environment repair `62398ab6d..d907b651c`. I traced the
new length-framed environment transport, intermediate-Python and final-exec
environments, raw `execve` vectors and object lifetimes, PATH search and errno
selection, pipe/status collisions and CLOEXEC state, parent/child cleanup,
read-only pinning and exact-number remapping, argv/executable/cwd/stdio/signal/
session/process-group/PID behavior, caller ownership, concurrent launches,
native-Windows rejection, customization-ledger scope, and the Task 10/Task 11
boundary. I made no production or test edits; this retained report is my only
repository change.

Fresh automated evidence at the pinned HEAD:

1. Focused Task 10 gate, through the required wrapper with retries disabled:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0
   scripts/run_tests.sh tests/tools/test_managed_process.py
   tests/tools/test_process_registry.py
   tests/scripts/test_workflow_merge_gate.py` — **3 files, 225 tests passed,
   0 failed, no retries**.
2. Strict customization validation:
   `../../.venv/bin/python scripts/check_upstream_customizations.py --strict
   --base-ref HEAD` — **PASS**.
3. Ruff on every Task 10 changed Python file — **PASS**.
4. `git diff --check 62398ab6d..HEAD` and production/test/ledger
   `git diff --check bee92ad4c..HEAD -- ':!*.md'` — **clean**.
5. The reviewed branch, HEAD, and tree matched the identities above, and the
   worktree was clean before this report was written.

Read-only production-facade diagnostics additionally exercised an explicit
128 KiB environment (larger than the host pipe capacity) through a real target
without deadlock, exact and over-limit 16 MiB serialization boundaries, partial
parent writes, exact empty/minimal/non-UTF-8/duplicate environment vectors,
and direct-versus-managed PATH candidate failures. The PATH comparison exposed
the finding below.

## Important finding

### I-1 — PATH search returns the last meaningful errno instead of direct `Popen`'s first

The raw final-exec loop records every non-`ENOENT`/non-`ENOTDIR` failure in
`saved_error` (`tools/managed_process.py:128-138`). Because line 136 overwrites
the saved value on every candidate, an error from a later PATH directory
replaces the first meaningful execution error. CPython's direct `Popen` search
retains the first such error and uses later candidates only to find a success
or a fallback not-found result.

The difference is deterministic with two PATH entries and no mocks:

- the first directory contains a regular, non-executable `hermes-probe`, so
  `execve` returns `EACCES`;
- the second contains a self-referential `hermes-probe` symlink, so `execve`
  returns `ELOOP`;
- direct `subprocess.Popen(["hermes-probe"], env=...)` raises
  `PermissionError`, errno 13 (`EACCES`), filename `"hermes-probe"`;
- `ManagedProcessTree.spawn(..., inherited_descriptors=[read_fd])` raises
  `OSError`, errno 62 (`ELOOP`), with the same filename.

This changes the exception class and failure classification at the generic
process seam. A caller can consequently distinguish descriptor and
non-descriptor launch paths, and code that classifies permission failures,
filesystem loops, cleanup, or retry safety can take the wrong branch. It is
also directly within the raw-exec replacement added to preserve environment
authority, rather than an unrelated pre-existing behavior.

**Required remediation:** Retain only the first non-`ENOENT`/non-`ENOTDIR`
failure while continuing to try later PATH candidates, matching CPython's
`_execute_child`/`_execvpe` precedence. Add a real direct-versus-managed PATH
test with distinct first and later meaningful errnos. Assert exception class,
errno, public filename, no target effects, zero internal descriptor delta, and
continued caller-descriptor ownership. Also retain a later-success case so the
saved error never prevents a valid candidate from executing.

## Closure status of prior findings

- **Exact explicit environment authority is closed.** The parent snapshots
  keys and values as filesystem bytes, validates names/NULs and a 16 MiB total
  frame, and sends the length-framed entries through a private inherited pipe.
  The bootstrap constructs the final raw `envp` from those entries rather than
  Python's mutated `os.environ`. Empty, minimal, mixed string/bytes/PathLike,
  non-UTF-8, and distinct mixed keys produce the same target entries as direct
  `Popen`; Python-added `LC_CTYPE` and `__CF_USER_TEXT_ENCODING` no longer
  reach the target. Omitted `env` uses a pre-launch byte snapshot and retains
  normal inherited-environment behavior.
- **Environment transport is bounded and live.** The four-byte count and
  eight-byte entry lengths are parsed under the fixed 16 MiB ceiling with
  incomplete, trailing, and oversized data rejected before final exec. The
  parent write loop handles short positive writes; real data exceeding pipe
  capacity is consumed concurrently without a pre-/post-`Popen` deadlock.
  Environment values appear in neither bootstrap argv nor status/error text.
  The environment descriptors are allocated above targets, pins, and status
  handles, passed explicitly, closed on success and failure, and cannot be
  clobbered by fixed-number remapping.
- **Read-only authority and descriptor identity remain closed.** Only pinned
  `O_RDONLY` open-file descriptions are admitted. Writable files, pipe write
  ends, read/write handles, sockets, and inspection failures fail before target
  creation. Owned CLOEXEC pins survive caller-number close/reuse and remap to
  exact child numbers without adopting caller handles.
- **Executable, signal, and cleanup compatibility remain closed outside the
  new PATH precedence defect.** Omitted/`None`, empty, explicit, missing,
  bytes, PathLike, non-UTF-8, and invalid executables retain their selected
  authority and filename identity. Signal dispositions, PID, argv, cwd, stdio,
  session/process group, process-tree termination/escalation, resources, and
  reap ownership remain preserved. Setup, `Popen`, transport, status, exec,
  and success paths close all internal handles and leave caller handles owned.
- **Status and raw-vector handling remain bounded.** CLOEXEC EOF is still the
  successful final-exec authority; failure status exposes only errno-derived
  bounded text. The ctypes argv/environment arrays retain their backing bytes
  through each synchronous `execve` attempt, are NUL terminated, and preserve
  ordered duplicate environment entries supported by direct `Popen`.
- **Platform and scope remain correct.** Native Windows rejects every nonempty
  inherited-descriptor request before Job creation; empty requests retain the
  historical path. The primitive remains generic and contains no workflow
  values, spill files, Bash lexer/prologue, rendered-command evidence,
  API/Desktop surface, session recovery, or other Task 11 work. The adjacent
  customization entry remains accurate.

## Final assessment

Fix round 4 closes the explicit-environment widening defect with a bounded,
non-argv, byte-exact transport and retains all earlier descriptor, executable,
signal, cleanup, process-tree, Windows, and scope guarantees. Task 10 is still
not ready to close because its replacement PATH-search loop changes direct
`Popen` errno precedence and exception classification when multiple candidates
fail meaningfully. One narrow precedence fix and fresh closure review are
required before Task 11 consumes this primitive.
