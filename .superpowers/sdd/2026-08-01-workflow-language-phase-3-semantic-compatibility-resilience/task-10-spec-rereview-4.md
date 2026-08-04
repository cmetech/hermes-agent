# Phase 3 Task 10 Specification Closure Rereview 4

**Review date:** 2026-08-02

**Task 10 baseline:** `bee92ad4c2b81d63c27e266f84299a3a52a5dc6e`

**Prior closure evidence commit:** `62398ab6d`

**Environment compatibility repair:** `d907b651c5e580bba6ac368e68b6833808cfc911`

**Reviewed HEAD:** `d907b651c5e580bba6ac368e68b6833808cfc911`

**Reviewed tree:** `9dae77172d2bce1b0d60cc591ed8659c6bd6b9f5`

**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 1
- Minor: 0

## Scope reviewed

I independently read the complete repository instructions, the approved Phase
3 design, the Task 10 plan and Task 11 consumer boundary, every retained Task
10 specification and quality review through rereview 3, the complete Task 10
implementation/fix history, and the exact fourth repair
`62398ab6d..d907b651c`. I traced explicit and inherited environment
normalization, byte framing and bounds, the private transport pipe, executable
search and error reconstruction, descriptor allocation/collision/CLOEXEC
behavior, setup/spawn/exec cleanup, read-only validation, identity pinning,
exact-number remapping, argv/cwd/stdio/signal/PID/session/process-tree
compatibility, caller ownership, concurrent launches, native-Windows gating,
the customization ledger, and the Task 10/Task 11 scope boundary. I made no
production or test edits; this report is my only file change.

## Important finding

### I-1 — PATH search reports the last substantive failure instead of direct `Popen`'s first failure

The new bootstrap correctly avoids `os.environ`, but it now implements
executable search itself. At `tools/managed_process.py:128-140`, every PATH
candidate is attempted and every error other than `ENOENT`/`ENOTDIR` assigns
`saved_error = last_error`. Consequently a later substantive failure overwrites
the first one. Direct `subprocess.Popen` follows Python's `_execvpe` contract:
it retains the first error other than `ENOENT`/`ENOTDIR`, finishes searching,
and reports that saved error if no candidate succeeds.

A real, read-only production-facade diagnostic made the divergence
deterministic. The first PATH directory contained a non-executable `probe`
file, producing `EACCES`; the second contained a self-referential `probe`
symlink, producing `ELOOP`. With the same explicit minimal environment and
argv, the results were:

```text
direct:  PermissionError errno=13 filename='probe'
managed: OSError          errno=62 filename='probe'
```

The managed failure closed every internal pin/status/environment handle, left
the caller-owned nominated pipe usable, and produced zero parent descriptor
growth. The cleanup is sound, but the generic launch facade changed the
authoritative exception class and errno. This matters at the Task 10 seam:
callers may classify `EACCES` differently from `ELOOP`, and the approved
handoff explicitly requires direct-`Popen` PATH resolution and errno parity
before Task 11 consumes the primitive.

**Required remediation:** Preserve only the first non-`ENOENT`/`ENOTDIR`
failure while continuing the candidate search, matching `os._execvpe` and
direct `Popen`. Add a real regression with two differently failing PATH
candidates that asserts exact exception class, errno, filename, no child
effect, zero internal descriptor delta, and retained caller ownership. Rerun
the focused Task 10 gate and obtain fresh closure rereviews.

## Closure status of prior findings

- **Exact explicit environment transport is closed.** The parent snapshots
  explicit empty/minimal, string, bytes, mixed, path-like, non-UTF-8, and
  duplicate-normalized-key entries into a length-framed private descriptor.
  The final raw `execve` receives those entries in mapping order without
  Python/platform additions. Omitted `env` snapshots inherited bytes and the
  final target matches the direct inherited environment. Ambiguous mixed
  `str`/`bytes` PATH authority is rejected with the direct exception before
  internal handles are acquired.
- **Environment privacy and bounds are sound.** Values travel in the private
  pipe, never bootstrap argv or the bounded status payload. Status failure
  contains only errno and `strerror`; no new log, journal, API/Desktop,
  evidence, or public projection carries environment data. The transport is
  bounded at 16 MiB including framing, reads and writes in bounded chunks,
  closes both ends on success and every exception, and cannot alias nominated
  descriptors, pins, or the status channel. A live 1 MiB transport followed
  by target `E2BIG` returned promptly with zero descriptor growth; an
  over-bound payload failed before allocation and retained caller ownership.
- **Read-only authority remains closed.** Owned pins are inspected with
  `F_GETFL` and only `O_RDONLY` is admitted. Writable files, read/write files,
  pipe write ends, sockets, and inspection failures are rejected before child
  effects without adopting caller handles.
- **Descriptor identity remains closed.** CLOEXEC duplicates pin each
  validated open-file description through launch and the bootstrap remaps each
  pin to its exact nominated child number. Pins and both private pipe pairs are
  allocated above every target, so remapping cannot overwrite another pin or
  private channel. Deterministic close/reuse, multiple-descriptor, and
  concurrent-launch tests preserve the original identities.
- **Executable, signal, and cleanup compatibility remains closed outside the
  new PATH ordering defect.** Omitted/`None`, explicit empty, absolute custom,
  missing `str`/`bytes`/`PathLike`, invalid/throwing executable, and
  `restore_signals` cases preserve direct authority, filename identity,
  signal outcome, PID-in-place exec, and caller ownership. Every operation
  after internal allocation is guarded by cleanup; success, rejection,
  `Popen` failure, transport failure, bootstrap exec failure, and status-read
  failure close internal handles and kill/reap a created child when required.
- **Windows, ledger, and scope remain bounded.** Native Windows rejects a
  nonempty request before Job Object creation while empty requests retain the
  historical Job assignment/resume/termination/reap path. The customization
  entry remains adjacent to the historical process-tree entry with the copied
  upstream identity and exact expected subject. No workflow value, spill
  materialization, shell lexer/prologue, rendered-command evidence, executor
  wiring, API/Desktop expansion, recovery behavior, or other Task 11/14 work
  entered Task 10.

## Fresh verification evidence

All Python tests were run through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Focused Task 10 gate — `tests/tools/test_managed_process.py`,
   `tests/tools/test_process_registry.py`, and
   `tests/scripts/test_workflow_merge_gate.py`: **3 files, 225 tests passed, 0
   failed, no retries**.
2. Strict customization validation —
   `../../.venv/bin/python scripts/check_upstream_customizations.py --strict
   --base-ref HEAD`: **PASS**.
3. Ruff on `tools/managed_process.py`, `tests/tools/test_managed_process.py`,
   `tests/tools/test_process_registry.py`, and
   `tests/scripts/test_workflow_merge_gate.py`: **PASS**.
4. `git diff --check 62398ab6d..d907b651c` and production/test/ledger
   `git diff --check bee92ad4c..d907b651c -- ':!*.md'`: **clean**.
5. Before this report was written, the branch was exactly
   `feat/workflow-language-phase-3-semantic-compatibility-resilience`, HEAD and
   tree matched the pinned identities above, and the worktree was clean.

## Final assessment

The fourth repair closes the explicit-environment mutation defect with a
bounded private descriptor transport while preserving environment bytes,
privacy, cleanup, caller ownership, process identity, and the narrow Task 10
scope. Task 10 is not yet ready to close because the replacement PATH search
selects the last substantive candidate error rather than direct `Popen`'s
first one. One small compatibility correction plus a real multi-error PATH
regression and fresh closure review are required before Task 11 begins.
