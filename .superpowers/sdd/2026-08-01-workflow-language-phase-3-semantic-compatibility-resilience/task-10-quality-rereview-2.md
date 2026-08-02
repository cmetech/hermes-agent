# Phase 3 Task 10 Independent Quality Rereview 2

**Review date:** 2026-08-02
**Task 10 baseline:** `bee92ad4c`
**Prior closure evidence commit:** `b827a84b9`
**Executable-authority fix:** `2d720810a5ab0e8a610a0bc0b884c23312588f26`
**Reviewed tree:** `32d0ac59c364867c60489c916fe357e0571f5ef8`
**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 2
- Minor: 1

## Scope and evidence reviewed

I read the repository instructions, the approved Phase 3 Task 10 plan and
descriptor/Bash boundary, all four preceding Task 10 review reports, the full
original and repair ranges, and the exact executable-authority repair
`b827a84b9..2d720810a`. I traced the complete descriptor validation, pinning,
bootstrap, status-pipe, cleanup, process identity, termination, Windows, and
customization-ledger paths. I compared the inherited-descriptor facade with
real direct `subprocess.Popen` calls for omitted, `None`, empty, missing,
successful, invalid-type, bytes, and path-like executables; checked signal
state across the bootstrap; and reviewed malformed/partial/EOF status handling,
FD allocation/collisions, CLOEXEC, stdio, environment, cwd, process-group,
session, caller ownership, concurrency, and Task 10/Task 11 scope. I made no
production or test edits; this report is my only file change.

Fresh automated evidence at the pinned HEAD:

1. Focused Task 10 gate, through the required wrapper with retries disabled:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0
   scripts/run_tests.sh tests/tools/test_managed_process.py
   tests/tools/test_process_registry.py
   tests/scripts/test_workflow_merge_gate.py` — **3 files, 212 tests passed,
   0 failed, no retries**.
2. Strict customization validation:
   `../../.venv/bin/python scripts/check_upstream_customizations.py --strict
   --base-ref HEAD` — **PASS**.
3. Ruff on every Task 10 changed Python file — **PASS**.
4. `git diff --check b827a84b9..HEAD` — **clean**. The complete Task 10 range
   reports only already-retained Markdown hard-break whitespace in the original
   review report, not production/test/ledger whitespace introduced by this fix.
5. The reviewed worktree was clean before this report was written.

## Important findings

### I-1 — Pre-launch executable normalization can leak every internal descriptor

The descriptor path allocates and owns the pinned data descriptors and both
ends of the bootstrap status pipe at `tools/managed_process.py:681-692`. It then
normalizes the caller's explicit executable with
`os.fsdecode(os.fspath(raw_executable))` at lines 693-700. That normalization
and the following bootstrap-argument construction occur before the outer
`try/finally` begins at line 726, so an exception there bypasses all cleanup at
lines 791-802.

A real diagnostic passed `executable=object()`, which direct `Popen` rejects
with `TypeError` without creating a child. The managed descriptor facade also
raised `TypeError`, but `psutil.Process().num_fds()` increased by **3 on every
call**: one owned descriptor pin plus the two owned status-pipe descriptors.
Three calls produced deltas `+3`, `+6`, and `+9`. The caller's nominated pipe
remained open, so this is specifically an unbounded internal-owner leak rather
than caller adoption. A throwing path-like object's `__fspath__` and a throwing
truth conversion for `restore_signals` reach the same unguarded interval.

This violates the Task 10 guarantee that internal pins/status handles close on
every launch outcome and turns repeated invalid generic spawn requests into
process-wide descriptor exhaustion.

**Required remediation:** Move every operation after the first internal handle
is acquired under one cleanup guard, including executable/path-like
normalization, `restore_signals` interpretation, mapping construction, and
bootstrap argv construction. Add direct-facade comparisons for invalid and
throwing path-like executables that prove no child effects, unchanged caller
ownership, and a zero FD-count delta on every failure.

### I-2 — `restore_signals=False` does not preserve the caller's signal state

The real bootstrap is a new Python interpreter. Python startup installs ignored
dispositions for signals including `SIGPIPE`. The bootstrap explicitly resets
`SIGPIPE`, `SIGXFZ`, and `SIGXFSZ` only when `restore_signals` is true
(`tools/managed_process.py:50,64-68`). When the caller requests
`restore_signals=False`, there is no restoration of the disposition that
existed before Python startup, so the final executable inherits Python's
bootstrap disposition rather than the parent's disposition as direct `Popen`
does.

The discrepancy is externally observable with no mocks. With the parent
setting `SIGPIPE` to `SIG_DFL`, this direct launch:

`Popen(['/bin/sh', '-c', 'kill -PIPE $$; echo survived'],
restore_signals=False)`

terminated with return code `-13` and no output. The same launch through
`ManagedProcessTree.spawn(..., inherited_descriptors=[read_fd],
restore_signals=False)` returned `0` and printed `survived`. Thus a target that
direct `Popen` terminates continues executing after the descriptor bootstrap.
This changes both target side effects and process outcome at a generic process
boundary.

**Required remediation:** Preserve the parent's relevant pre-bootstrap signal
dispositions when `restore_signals=False`, without installing a Python
`preexec_fn` path. Add a real direct-vs-managed test using a non-Python target
and explicit parent dispositions, covering both false and true behavior plus
unchanged PID/session/process-tree cleanup.

## Minor finding

### M-1 — Exec-failure propagation changes path-like and bytes filename identity

The repaired discriminator correctly distinguishes omitted/`None` from an
explicit empty executable and preserves the selected executable's bytes for
successful execution. However, line 698 always converts an explicit
`os.PathLike` or `bytes` executable to `str`, and line 753 uses that converted
value as the reconstructed exception's filename.

Real comparisons showed:

- direct `Popen(..., executable=Path('/definitely/missing'))` raised
  `FileNotFoundError` with the original `Path` object in `exc.filename`, while
  the managed descriptor path supplied `'/definitely/missing'` as `str`; and
- direct `Popen(..., executable=b'/definitely/missing-\xff')` retained the
  original `bytes` filename, while the managed path supplied the surrogate-
  escaped `str` form.

The errno and referenced filesystem bytes remain correct, so this is not a
fail-open execution-authority defect, but it falls short of exact generic
`Popen` exception compatibility and can break typed filename handling.

**Required remediation:** Retain the original executable object for parent-side
exception reconstruction while separately serializing the filesystem path for
the bootstrap. Add bytes and path-like missing-executable comparisons that
assert exception type, errno, and filename value/type, as well as leak-free
cleanup and caller ownership.

## Confirmed closures and positive findings

- The preceding executable-authority finding is closed for its named cases.
  Omitted and explicit-`None` executable values select `argv[0]`; explicit
  empty remains empty and fails closed with `EACCES`; missing custom
  executables preserve errno/filename for string inputs; and a successful
  custom executable retains target argv/argv0 semantics. The target side-effect
  sentinel does not run on the empty or missing failures.
- The original read-only finding remains closed. Pins are validated with
  `F_GETFL & O_ACCMODE`; write-only/read-write files, pipe write ends, sockets,
  and inspection failures are rejected before child creation, while read-only
  files and pipe read ends are accepted without caller adoption.
- The original descriptor-identity finding remains closed. Owned
  `F_DUPFD_CLOEXEC` pins survive caller-number close/reuse, are allocated above
  every target, and are remapped to exact child numbers. Status descriptors
  are above both target and pin ranges, so `dup2` cannot clobber another pin or
  the status channel. Concurrent successful launches retain independent pinned
  identities.
- The CLOEXEC status descriptor makes EOF authoritative after the final
  successful `exec`; bounded errno payloads fail closed, malformed payloads map
  to `EIO`, and parent read/parse failures enter the established kill/wait
  cleanup path. Trusted bootstrap metadata parsing occurs before its child-side
  exception guard, but the parent constructs its fixed numeric shape and no
  caller value can alter that structure.
- Normal string argv, explicit string executable, env, cwd, stdio,
  `start_new_session`, process group, user/group/umask, PID continuity,
  process-tree containment, termination/escalation, resource identity, and
  reap behavior remain compatible under the exercised paths. Raw `pass_fds`,
  `shell=True`, and `preexec_fn` cannot bypass the named descriptor contract.
- Native Windows still rejects every nonempty descriptor request before Job
  Object creation. Empty requests retain the historical suspended-child Job
  assignment/resume and cleanup path.
- The primitive remains generic and bounded at 64 descriptors. It contains no
  workflow value, spill path, Bash substitution, rendered-command evidence,
  API/Desktop projection, recovery behavior, or other Task 11 implementation.
  The customization entry remains adjacent to the historical process-tree
  entry and accurately describes the intended narrow seam.

## Final assessment

The executable omitted/`None`/empty authority repair is correct for its direct
cases, and the original read-only and identity-pinning defects remain closed.
Task 10 is still not ready to close: invalid executable normalization leaks
owned descriptors outside the cleanup guard, and `restore_signals=False`
changes target execution and outcome across the Python bootstrap. The smaller
path-object filename mismatch should be corrected in the same bounded
compatibility round. No Task 11 work should begin until a fresh closure review
confirms all three findings are resolved.
