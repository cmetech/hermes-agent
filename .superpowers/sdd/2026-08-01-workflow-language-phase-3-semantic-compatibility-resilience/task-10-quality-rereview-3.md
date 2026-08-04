# Phase 3 Task 10 Independent Quality Rereview 3

**Review date:** 2026-08-02

**Task 10 baseline:** `bee92ad4c2b81d63c27e266f84299a3a52a5dc6e`

**Prior closure evidence commit:** `d77382edd`

**Compatibility repair:** `60f0ca7f72dc364c272ebef37db8c45bf2fe2ef7`

**Reviewed tree:** `24b58a726d0205e47532df7dd31eeb5663fe1fa6`

**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 1
- Minor: 0

## Scope and evidence reviewed

I independently read the complete repository instructions, the approved Phase
3 design, the Task 10 plan and Task 11 consumer boundary, all six preceding
Task 10 review/rereview reports, and the full original implementation plus all
three repair ranges. I traced normalization before allocation, every internal
pin/status cleanup path, read-only inspection, open-file-description pinning,
exact child-number remapping, the isolated Python bootstrap and status
protocol, explicit executable selection and parent-side OSError reconstruction,
signal disposition handling, argv/environment/cwd/stdio/session/process-group/
PID behavior, caller ownership, concurrency, Windows fail-closed behavior, the
customization ledger, and Task 10/Task 11 scope separation. I made no production
or test edits; this retained report is my only repository change.

Fresh automated evidence at the pinned HEAD:

1. Focused Task 10 gate, through the required wrapper with retries disabled:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0
   scripts/run_tests.sh tests/tools/test_managed_process.py
   tests/tools/test_process_registry.py
   tests/scripts/test_workflow_merge_gate.py` — **3 files, 219 tests passed,
   0 failed, no retries**.
2. Strict customization validation:
   `../../.venv/bin/python scripts/check_upstream_customizations.py --strict
   --base-ref HEAD` — **PASS**.
3. Ruff on every Python file changed in the complete Task 10 range — **PASS**.
4. `git diff --check d77382edd..HEAD` and production/test/ledger
   `git diff --check bee92ad4c..HEAD -- ':!*.md'` — **clean**.

Read-only production-facade diagnostics additionally compared direct
`subprocess.Popen` with the inherited-descriptor path for valid and invalid
`restore_signals` integer semantics; default and ignored dispositions for every
available one of `SIGPIPE`, `SIGXFZ`, and `SIGXFSZ` with both
`restore_signals=True` and `False`; string, bytes, `Path`, and custom
`PathLike` executables; non-UTF-8 and ordinary explicit environments; and
path-like working directories. Those diagnostics exposed the environment
finding below. The signal, executable errno/filename, and cwd cases matched.

## Important finding

### I-1 — The Python bootstrap widens and mutates an explicit target environment

The inherited-descriptor path passes the caller's `env` to the intermediate
Python process, but the bootstrap executes the requested target with
`os.execvpe(executable, argv, os.environ)`
(`tools/managed_process.py:72`). `os.environ` is the Python runtime's
post-startup environment, not necessarily the exact environment mapping the
caller supplied to `Popen`. Python and platform startup can add or normalize
entries before the final exec.

This is observable on the review platform with no mocks. Given the explicit
minimal environment:

```python
{"PATH": "/usr/bin:/bin", "HERMES_DIAG": "plain"}
```

direct `Popen(["/usr/bin/env"], env=...)` printed exactly those two entries.
`ManagedProcessTree.spawn(..., env=..., inherited_descriptors=[read_fd])`
printed those entries plus:

```text
__CF_USER_TEXT_ENCODING=0x1F5:0x0:0x0
LC_CTYPE=C.UTF-8
```

The same widening occurred when the explicit mapping used bytes and a
non-UTF-8 value; the original bytes survived, but the two unrequested entries
were still added. The target therefore does not receive the caller-authorized
environment, even though direct `Popen` does.

This is not merely cosmetic for the planned consumer. The workflow Bash and
script executors deliberately construct `allowed_env` from a small allow-list
(`plugins/workflow/executors/bash.py:59-69` and
`plugins/workflow/executors/script.py:202-216`) and pass it explicitly to
`ManagedProcessTree.spawn()`. Task 11 will combine that path with inherited
spill descriptors. The current bootstrap can silently add variables outside
that allow-list, and `LC_CTYPE` can change shell locale-sensitive behavior such
as character classes and byte/text handling. Thus descriptor use changes
target command semantics and weakens the existing environment boundary.

**Required remediation:** Preserve the exact explicit `env` mapping through
the final exec without relying on Python's mutated `os.environ`, including
str/bytes keys and values and non-UTF-8 bytes. If no explicit `env` is supplied,
retain direct inherited-environment behavior. Add real direct-vs-managed tests
that execute a non-Python target with a deliberately minimal environment and
assert exact environment bytes/entries, absence of Python/platform additions,
caller descriptor ownership, and zero internal descriptor leaks. Also add an
executor-level regression proving the Bash allow-list remains exact once Task
11 supplies inherited descriptors.

## Closure status of prior findings

- **Read-only authority remains closed.** Every nominated descriptor is pinned
  first, inspected with `F_GETFL`, and accepted only as `O_RDONLY`. Writable
  files, pipe write ends, read/write handles, sockets, and inspection failures
  fail before child creation without adopting caller handles.
- **Descriptor identity remains closed.** Owned `F_DUPFD_CLOEXEC` pins survive
  caller-number close/reuse and are remapped to exact unique child numbers.
  Pins and the private status channel remain above every nominated target, and
  concurrent launches preserve independent identities.
- **Executable authority and cleanup remain closed.** Executable and
  `restore_signals` normalization now occurs before any internal descriptor is
  allocated. Omitted/`None`, explicit empty, missing, successful, invalid,
  throwing `PathLike`, bytes, and `Path` cases preserve target selection plus
  the relevant direct exception type, errno, and filename identity. Success,
  rejection, `Popen` failure, bootstrap exec failure, and status-read paths
  close internal handles while retaining caller ownership.
- **Signal behavior remains closed.** Integer/index conversion matches direct
  `Popen` for booleans, zero/nonzero integers, `__index__`, and invalid values.
  The serialized final-exec dispositions matched direct behavior for default
  and ignored `SIGPIPE`/`SIGXFSZ` states with both restore settings (and the
  same logic covers `SIGXFZ` where available). PID, session, process group,
  termination, and reap identity remain in place across the bootstrap.
- **Status and collision behavior remain sound.** Only parent-constructed
  numeric bootstrap metadata is parsed before the child-side guard. CLOEXEC
  EOF denotes successful final exec; bounded/malformed failure data fails
  closed, and parent-side errors enter kill/wait cleanup. Exact remapping cannot
  overwrite another pin or the status handle.
- **Windows and empty requests remain bounded.** Native Windows rejects every
  nonempty request before Job creation. Empty requests allocate no descriptor
  pins/status channel and retain the established direct Windows/POSIX path.
- **Scope remains correct.** The primitive contains no workflow values, spill
  materialization, shell lexer/prologue, rendered-command evidence,
  API/Desktop projection, persistent-session behavior, or other Task 11/14
  implementation. The customization entry remains adjacent to the historical
  process-tree entry and accurately describes the generic seam.

## Final assessment

The third repair closes the preceding setup-leak, signal-disposition, and
filename-identity findings while retaining the original read-only and identity
fixes. Task 10 is still not ready to close because the intermediate Python
runtime mutates an explicit target environment before final exec. That defect
reaches Task 11's concrete Bash consumer and violates its existing environment
allow-list. One bounded compatibility repair and fresh closure reviews are
required before descriptor-backed Bash substitution begins.
