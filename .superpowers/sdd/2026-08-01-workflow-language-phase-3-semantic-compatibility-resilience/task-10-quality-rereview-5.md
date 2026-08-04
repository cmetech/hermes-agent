# Phase 3 Task 10 Independent Quality Rereview 5

**Review date:** 2026-08-02

**Task 10 baseline:** `bee92ad4c2b81d63c27e266f84299a3a52a5dc6e`

**Prior closure evidence commit:** `5c08398d5`

**PATH-error repair:** `403d752ccd55a9d778274de4d966ca706b63e104`

**Reviewed HEAD:** `403d752ccd55a9d778274de4d966ca706b63e104`

**Reviewed tree:** `2bf6197019f1354c34808b4d1028893ddcc6b4e1`

**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 1
- Minor: 0

## Scope and evidence reviewed

I independently read the complete repository instructions, the approved Phase
3 design, the Task 10 plan and Task 11 consumer boundary, every retained Task
10 specification and quality review through rereview 4, the complete Task 10
implementation/fix history, and the exact fifth repair
`5c08398d5..403d752cc`. I traced the raw `execve` PATH loop and CPython
`os._execvpe` precedence, executable-candidate construction, original and
serialized environment mappings, final environment vectors, the private
environment/status pipes, descriptor allocation and remapping, all parent and
child cleanup paths, read-only identity pins, argv/executable/cwd/stdio/signal/
PID/session/process-tree behavior, native-Windows rejection, the customization
ledger, and the Task 10/Task 11 boundary. I made no production or test edits;
this report is my only authored repository file.

Fresh automated evidence at the pinned HEAD:

1. Focused Task 10 gate, through the required wrapper with retries disabled:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0
   scripts/run_tests.sh tests/tools/test_managed_process.py
   tests/tools/test_process_registry.py
   tests/scripts/test_workflow_merge_gate.py` — **3 files, 229 tests passed,
   0 failed, no retries**.
2. Strict customization validation:
   `../../.venv/bin/python scripts/check_upstream_customizations.py --strict
   --base-ref HEAD` — **PASS**.
3. Ruff on every Task 10 changed Python file — **PASS**.
4. `git diff --check 5c08398d5..HEAD` and production/test/ledger
   `git diff --check bee92ad4c..HEAD -- ':!*.md'` — **clean**.

The real PATH regressions additionally compare direct `Popen` and the managed
facade for first `EACCES` followed by `ELOOP`, later success after a meaningful
failure, and both orderings of all-not-found `ENOENT`/`ENOTDIR`. Code inspection
confirms the same first-substantive rule covers `ENAMETOOLONG`, `ENOEXEC`, and
other `OSError` values: the first error outside `ENOENT`/`ENOTDIR` is retained,
later candidates are still attempted, and the final not-found error is used
only when no substantive error or successful candidate exists. A separate
no-mock diagnostic exposed the finding below.

## Important finding

### I-1 — PATH authority is recomputed from a normalized environment and can execute a target direct `Popen` does not select

The parent correctly calls `os.get_exec_path(kwargs.get("env"))` before it
allocates internal handles (`tools/managed_process.py:821-828`), but it throws
that authoritative result away. The environment serializer normalizes every
key with `os.fsencode()` and collapses those normalized entries into a plain
byte-keyed dictionary (`tools/managed_process.py:156-168`). The child then
recomputes executable candidates from that reconstructed dictionary
(`tools/managed_process.py:84-115`).

That is not equivalent to direct `Popen` for every environment mapping it
accepts. Direct `Popen`/`os.get_exec_path` obtains PATH through the original
mapping's exact `'PATH'`/`b'PATH'` lookup behavior; converting a path-like key
to `b'PATH'` can turn a key that did not authorize executable search into the
authoritative PATH in the bootstrap.

A real diagnostic used this accepted environment and an executable script
inside the referenced directory:

```python
environment = {Path("PATH"): str(bin_dir)}
```

With argv `['hermes-probe']`:

- direct `subprocess.Popen(..., env=environment)` raised
  `FileNotFoundError`, errno `ENOENT`, filename `'hermes-probe'`, and did not
  execute the script;
- `ManagedProcessTree.spawn(..., env=environment,
  inherited_descriptors=[read_fd])` normalized the key, found the script, ran
  it with return code 0, and created its side effect.

The managed launch had zero internal-descriptor growth and left the caller's
pipe usable, so cleanup and caller ownership are sound. The defect is target
selection: enabling inherited descriptors changes a caller-accepted mapping
from “target not found” into successful execution. Custom mapping `get()`
semantics and normalized duplicate PATH-like keys are the same bug class.
Because this can run a different executable rather than merely change error
presentation, it remains an Important fail-open compatibility issue at the
generic process boundary.

**Required remediation:** Resolve and snapshot the exact executable candidate
sequence from the original environment mapping in the parent, before internal
descriptor allocation, and carry that bounded byte sequence privately to the
bootstrap rather than recomputing it from normalized final-environment
entries. Preserve direct absolute/relative executable behavior, missing and
empty PATH, string/bytes/mixed mappings, accepted path-like keys, custom
mapping lookup semantics, candidate order, first-substantive errno precedence,
later success, public filename identity, privacy, bounds, and cleanup. Add a
real direct-versus-managed regression proving a path-like `PATH` key cannot
change target selection or create effects, with zero internal FD delta and
continued caller-descriptor ownership.

## Closure status of prior findings

- **Fix round 5 closes the named PATH errno-precedence finding.** The raw loop
  now saves only the first error outside `ENOENT`/`ENOTDIR`, continues searching
  for a later success, and otherwise retains the last not-found result. The
  direct comparison preserves exception class, errno, public filename, no
  target effect, zero descriptor growth, and caller ownership for the exercised
  sequences.
- **Explicit final-environment authority remains byte-exact.** Empty, minimal,
  string, bytes, mixed, non-UTF-8, duplicate-normalized-key, and omitted
  environments reach the final raw `envp` without Python-added variables. The
  new finding concerns executable-search authority derived from the mapping,
  not the final target environment vector.
- **Environment transport remains bounded, private, live, and cleaned.** The
  16 MiB length-framed payload is carried outside bootstrap argv, consumed
  concurrently, checked for partial/trailing/oversized data, and closed on
  setup, launch, write, exec, status, and success paths. Values do not enter
  status errors, evidence, logs, APIs, or Desktop projections.
- **Read-only authority and exact identity remain closed.** Each nominated
  descriptor is pinned with `F_DUPFD_CLOEXEC`, admitted only as `O_RDONLY`, and
  remapped to its exact child number. Writable/read-write descriptors, pipe
  write ends, sockets, and inspection failures fail before child effects.
  Deterministic close/reuse and concurrent launches retain the pinned object,
  while unrelated descriptors stay closed and caller handles stay owned.
- **Executable, signal, process, and cleanup behavior remain closed outside
  the new mapping-authority defect.** Omitted/`None`, empty, absolute, relative,
  missing, bytes, path-like, non-UTF-8, invalid, and throwing executables retain
  their established argv and public filename behavior when candidate authority
  is the same. PID-in-place exec, cwd, stdio, signal dispositions, session and
  process-group state, termination, escalation, resource identity, and reap
  ownership remain preserved. Every internal pin and private-pipe descriptor is
  closed across rejection, spawn failure, transport failure, exec failure, and
  success.
- **Platform, ledger, and scope remain bounded.** Native Windows rejects every
  nonempty inherited-descriptor request before Job creation. Empty requests
  retain the historical path. The customization entry remains adjacent to the
  historical process-tree entry, and no workflow value, spill materialization,
  Bash lexer/prologue, rendered-command evidence, API/Desktop expansion,
  recovery behavior, or other Task 11/14 work entered Task 10.

## Final assessment

Fix round 5 correctly preserves CPython's first-substantive PATH error while
continuing to later candidates and retaining the last all-not-found error. All
focused, strict, lint, and whitespace gates are green, and the previous
descriptor, environment-vector, executable, signal, cleanup, process-tree,
Windows, and scope closures remain intact. Task 10 is still not ready to close,
however, because the child recomputes PATH candidates from normalized
environment entries and can execute a target that direct `Popen` rejects as
missing. One bounded candidate-authority repair and fresh closure reviews are
required before Task 11 consumes this primitive.
