# Phase 3 Task 10 Specification Closure Rereview 3

**Review date:** 2026-08-02

**Task 10 baseline:** `bee92ad4c2b81d63c27e266f84299a3a52a5dc6e`

**Prior closure evidence commit:** `d77382edd`

**Final compatibility fix:** `60f0ca7f72dc364c272ebef37db8c45bf2fe2ef7`

**Reviewed HEAD:** `60f0ca7f72dc364c272ebef37db8c45bf2fe2ef7`

**Reviewed tree:** `24b58a726d0205e47532df7dd31eeb5663fe1fa6`

**Verdict:** PASS

## Severity summary

- Critical: 0
- Important: 0
- Minor: 0

## Scope reviewed

I independently read the complete repository instructions, the approved Phase
3 design and Task 10 plan, every retained Task 10 specification and quality
review, the complete Task 10 implementation/fix history, and the exact final
repair `d77382edd..60f0ca7f`. I traced the public descriptor contract,
read-only validation, open-file-description pinning, exact-number remapping,
the isolated bootstrap and status protocol, setup and failure cleanup,
executable and signal compatibility, caller ownership, concurrent launches,
process identity/session/tree behavior, native-Windows gating, the
customization ledger, and the Task 10/Task 11 boundary. I made no production or
test edits; this report is my only file change.

## Findings

No Critical, Important, or Minor specification findings.

## Closure of all prior findings

### Read-only authority and pinned identity remain closed

The POSIX path owns `F_DUPFD_CLOEXEC` duplicates of each nominated descriptor,
then inspects the pinned open-file description with `F_GETFL`. Only
`O_RDONLY` is admitted. Writable and read/write regular descriptors, pipe
write ends, sockets, and inspection failures are rejected before child
creation, with internal pins closed and caller handles retained.

Pins are allocated above every nominated target and stay live across `Popen`.
The bootstrap remaps each pin to its exact nominated child number with `dup2`
and closes the original pin. Its private status pipe is allocated above both
targets and pins, so remapping cannot overwrite another pin or the error
channel. The deterministic competing-thread close/reuse test proves that a
caller number replaced after validation does not substitute a different
open-file identity in the child. Multi-descriptor and concurrent-launch tests
prove exact number/content isolation. A live 64-descriptor diagnostic also
confirmed the inclusive contract bound and retained caller ownership.

### Executable authority and setup cleanup are closed

The final fix performs explicit executable/path-like normalization and
`restore_signals` index conversion before acquiring any pin or status handle.
Invalid objects, a raising `__fspath__`, and an invalid restore option therefore
match direct `Popen` exceptions without creating a child or leaking internal
descriptors. Every operation after the first internal handle is acquired is
inside the single cleanup-owning `try/finally`; pinning, status-pipe creation,
bootstrap argument construction, `Popen`, status reads, exec failure, and
successful launch all close their internal handles. Repeated failure tests
prove zero parent descriptor-count growth and continued usability of the
caller-owned descriptor.

The bootstrap carries an explicit executable discriminator rather than a
truthiness sentinel. Omitted and explicit `None` select `argv[0]`; an explicit
empty executable stays empty and fails closed without executing `argv[0]`;
missing and successful custom executables retain direct launch authority and
argv behavior. Parent-side error reconstruction retains the original
executable object while the bootstrap receives a separately serialized path,
so missing `str`, `Path`, and non-UTF-8 `bytes` executables preserve the direct
exception class, errno, filename value, and filename type. Caller descriptors
remain owned and usable across each outcome.

### Signal and process behavior are closed

The parent serializes the exact final-exec disposition required for
`SIGPIPE`, `SIGXFZ`, and `SIGXFSZ`: default when `restore_signals=True`, and
the caller disposition when false. The isolated bootstrap reapplies those
states after Python startup and before exec, without `preexec_fn`. Real
non-Python direct-versus-managed tests prove the false/default-parent and
true/ignored-parent cases produce identical signal termination/output. The
remaining combinations follow directly from the two-state serialization:
ignored is retained only when requested, while caught handlers become the
default disposition at exec as required.

The bootstrap execs in place, preserving PID, argv, environment, cwd, stdio,
session/process-group configuration, resource identity, termination,
escalation, and reap ownership. The CLOEXEC status descriptor makes EOF the
successful-final-exec signal; bounded exec errors return synchronously and
enter the existing child kill/wait cleanup path. Default session leadership,
descendant cleanup, process-registry behavior, concurrent launches, and caller
ownership all remain covered by live tests.

## Contract, platform, ledger, and scope assessment

- The named argument accepts at most 64 unique integer descriptors above
  stdin/stdout/stderr. Standard/negative descriptors, booleans and other
  non-integers, duplicates, closed descriptors, raw `pass_fds`, `shell=True`,
  and caller `preexec_fn` fail closed through the intended paths.
- `close_fds=True` plus only the owned pins and private status descriptor in
  `pass_fds` prevents unrelated inheritable descriptors from reaching the
  target.
- Native Windows rejects every nonempty inherited-descriptor request before
  Job Object creation or child launch. Empty requests retain the historical
  suspended-child assignment/resume, identity, termination, resource, and
  query-proven reap behavior.
- The `managed-process-inherited-descriptors` customization entry remains
  adjacent to the historical `managed-process-tree` entry, preserves the
  copied upstream identity and required expected subject, and accurately
  records the bounded read-only, pinned-identity, cleanup, caller-ownership,
  and Windows contracts.
- The final repair changes only `tools/managed_process.py` and
  `tests/tools/test_managed_process.py`. It adds no workflow value, spill file,
  descriptor manifest, shell lexer/prologue, command evidence, executor
  wiring, API/Desktop projection, recovery behavior, or other Task 11 work.
  The earlier gate/projection drift repairs remain bounded to already-delivered
  Phase 3 truth.

## Fresh verification evidence

All Python tests were run through the repository wrapper with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Focused Task 10 gate — `tests/tools/test_managed_process.py`,
   `tests/tools/test_process_registry.py`, and
   `tests/scripts/test_workflow_merge_gate.py`: **3 files, 219 tests passed, 0
   failed, no retries**.
2. Strict customization validation —
   `../../.venv/bin/python scripts/check_upstream_customizations.py --strict
   --base-ref HEAD`: **PASS**.
3. Ruff on `tools/managed_process.py`, `tests/tools/test_managed_process.py`,
   `tests/tools/test_process_registry.py`, and
   `tests/scripts/test_workflow_merge_gate.py`: **PASS**.
4. `git diff --check d77382edd..60f0ca7f`: **clean**. The complete Task 10
   range reports only the already-retained Markdown hard-break whitespace in
   the original quality-review report, not production, test, ledger, or final
   fix whitespace.
5. Before this report was written, the branch was exactly
   `feat/workflow-language-phase-3-semantic-compatibility-resilience`, HEAD and
   tree matched the identities above, and the worktree was clean.

## Final assessment

Task 10 now supplies the intended narrow generic primitive: bounded,
read-only, identity-pinned POSIX descriptor inheritance at exact child numbers
with direct-launch executable and signal authority, exhaustive internal-handle
cleanup, caller ownership, and preserved process-tree behavior. Native Windows
continues to fail closed for nonempty requests, the customization ledger is
accurate, and Task 11 remains untouched. Task 10 is specification-complete and
ready to close.
