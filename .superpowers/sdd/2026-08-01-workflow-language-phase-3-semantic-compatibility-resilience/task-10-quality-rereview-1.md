# Phase 3 Task 10 Independent Quality Rereview 1

**Review date:** 2026-08-02
**Task 10 baseline:** `bee92ad4c`
**Original review evidence commit:** `1bdb4d99d`
**Fix commit:** `6b66eff5e1fa6c8cd35db010661b6f42ac1da104`
**Reviewed tree:** `501862ba29c1f8a638c67d478de892879b80e214`
**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 1
- Minor: 0

## Scope and evidence reviewed

I read the complete Task 10 plan, the approved Phase 3 descriptor and safe-Bash
boundaries, both original Task 10 review reports, and the full
`1bdb4d99d..6b66eff5e` fix. I traced the POSIX bootstrap/status protocol,
read-only access-mode inspection, identity pinning and exact-number remapping,
descriptor collision avoidance, CLOEXEC behavior, caller ownership, spawn and
exec failure cleanup, argv/executable/env/cwd/stdio behavior, signal restoration,
PID/session/process-group continuity, concurrent launches, the unchanged Windows
fail-closed branch, and the customization ledger. I also rechecked Task 11 and
public-surface scope. I made no production or test edits; this report is my only
file change.

Fresh verification used the repository wrapper with file retries disabled:

1. Focused Task 10 gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/tools/test_managed_process.py tests/tools/test_process_registry.py tests/scripts/test_workflow_merge_gate.py`
   — 3 files, 208 tests passed, 0 failed.
2. Strict customization validation:
   `../../.venv/bin/python scripts/check_upstream_customizations.py --strict --base-ref HEAD`
   — passed.
3. Live base customization gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/test_workflow_merge_gate.sh --phase base`
   — 53 Python files / 2,565 tests passed; installed-distribution integration
   1 passed; Desktop Vitest 11 files / 155 tests passed; Desktop TypeScript
   check passed; `TESTED_BASE_SHA=6b66eff5e1fa6c8cd35db010661b6f42ac1da104`.
4. Ruff on every Task 10 changed Python file — clean.
5. `git diff --check 1bdb4d99d..6b66eff5e` — clean.

Read-only production-facade diagnostics additionally confirmed regular files and
pipe read ends report `O_RDONLY`, pipe write ends report `O_WRONLY`, and a
socketpair endpoint reports `O_RDWR` and is rejected without closing the caller
socket. A second diagnostic compared direct `subprocess.Popen` with the real
managed descriptor path for explicit `executable` values and exposed the finding
below.

## Important finding

### I-1 — The bootstrap loses explicit `executable` authority and can execute when direct `Popen` fails closed

The inherited-descriptor path removes `executable` from the real `Popen` call
and serializes it through an empty-string sentinel
(`tools/managed_process.py:688-694`). The bootstrap then selects it with
`sys.argv[3] or sys.argv[5]` (`tools/managed_process.py:51`), so an explicitly
provided empty executable is indistinguishable from an omitted executable. Its
error reconstruction separately uses
`popen_kwargs.get("executable", argv[0])` (`tools/managed_process.py:747-750`),
which returns `None` rather than the default when a caller explicitly supplied
`executable=None`.

The real-facade comparison produced:

- direct `Popen(['/definitely/missing'], executable=None)` —
  `FileNotFoundError(ENOENT)` for `/definitely/missing`;
- managed descriptor spawn with the same arguments — `TypeError` while trying
  to reconstruct the target from `None`;
- direct `Popen([sys.executable, '-c', 'pass'], executable='')` —
  `PermissionError(EACCES)` and no target execution;
- managed descriptor spawn with the same arguments — return code 0 after it
  silently executed `argv[0]`.

The empty-string case is a fail-open change to caller-supplied execution
authority: a value for which the existing `Popen` boundary creates no target
instead launches a different executable. The explicit-`None` case also changes
exec-failure classification, which can misroute cleanup and retry behavior in a
generic caller. The currently planned Bash call site does not pass
`executable`, but Task 10 promises a generic `Popen`-compatible process seam;
the defect is in that seam and should be closed before Task 11 consumes it.

**Required remediation:** Carry an explicit “executable omitted/None”
discriminator into the bootstrap rather than using truthiness. Select `argv[0]`
only for the omitted/`None` case; preserve an explicitly supplied empty string
so the final exec fails with the same errno and without target effects. Build
error propagation from that resolved target without calling `os.fspath(None)`.
Add real tests comparing omitted, explicit `None`, empty, missing custom, and
successful custom executables with direct `Popen`, including unchanged caller
descriptor ownership and zero internal-pin/status-pipe leaks on every failure.

## Closure of the original findings

- **Original I-1 is closed.** Each nominated descriptor is first duplicated
  with `F_DUPFD_CLOEXEC`; `F_GETFL & O_ACCMODE` is checked on that owned open-file
  description. Writable regular files, pipe write ends, read/write descriptors,
  sockets, and inspection failures fail before child creation, while regular
  files and pipe read ends are admitted. Rejection preserves caller ownership.
- **Original I-2 is closed.** Owned pins remain live through the first spawn,
  are the only data descriptors in `pass_fds`, and are remapped below the pin
  range to the exact nominated child numbers before final exec. The deterministic
  close/reuse test proves a competitor can replace the caller's numeric slot
  after pinning without changing the bytes received by the child. Concurrent
  launches use disjoint held descriptors, and success, synthetic spawn failure,
  real exec failure, and partial setup paths close internal pins/status handles
  without adopting the caller handles.

## Additional positive findings

- The internal status pipe is allocated above every target and pin, so the
  bootstrap mapping cannot overwrite its error channel. It is non-inheritable
  before final exec, making EOF authoritative for successful exec; bounded
  errno/message data carries final-exec failure back to the parent.
- Exact argv, environment, cwd, stdin/stdout/stderr, default and explicit
  `start_new_session`, custom process-group state, and PID continuity otherwise
  survive the bootstrap. The explicit signal reset compensates for Python
  installing ignored signal dispositions before the target exec.
- The bootstrap executes as a fresh isolated Python interpreter rather than a
  post-fork `preexec_fn`, avoiding Python-in-a-multithreaded-child hazards.
  `shell=True` and caller `preexec_fn` requests fail before launch rather than
  weakening that property.
- Native Windows still rejects every nonempty request before Job creation;
  empty requests retain the existing suspended-child, Job assignment/resume,
  identity, termination, escalation, query, and reap path.
- The descriptor count remains bounded at 64, raw `pass_fds` cannot bypass the
  named contract, unrelated descriptors remain closed, and default callers with
  no inherited descriptors take the unchanged direct-spawn path.
- The ledger now accurately records read-only enforcement, identity pinning,
  exact-number remapping, internal-handle cleanup, caller ownership, and Windows
  fail-closed behavior. No workflow values, paths, Bash rendering, spill
  materialization, evidence payload, API/Desktop projection, raw provider data,
  or Task 11 implementation entered this task.

## Final assessment

The repair closes both original security findings and preserves the bounded
descriptor/process-tree envelope under the focused, live, and static gates. It
is not yet ready to close because the new bootstrap conflates an explicitly
empty executable with the default and can execute `argv[0]` where the prior
generic process boundary fails closed. One bounded compatibility fix and fresh
closure review are required before Task 11 begins.
