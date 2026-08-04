# Phase 3 Task 10 Specification Closure Rereview 6

**Review date:** 2026-08-02

**Task 10 baseline:** `bee92ad4c2b81d63c27e266f84299a3a52a5dc6e`

**Prior closure evidence commit:** `94ab8ceb9`

**Executable-search-authority repair:** `9976161faf55f461f3f9ab3b56760a995bf6170f`

**Reviewed HEAD:** `9976161faf55f461f3f9ab3b56760a995bf6170f`

**Reviewed tree:** `727b6f3b9356e79bd54ec648e9dba25d61de1093`

**Verdict:** PASS

## Severity summary

- Critical: 0
- Important: 0
- Minor: 0

## Scope reviewed

I independently read the complete repository instructions, the approved Phase
3 design, the Task 10 plan and Task 11 consumer boundary, every retained Task
10 specification and quality review through rereview 5, the complete Task 10
implementation and repair history, and the exact sixth repair
`94ab8ceb9..9976161fa`. I traced original-mapping environment iteration and
PATH lookup order, executable-candidate serialization and bounded private
transport, raw final-environment construction, PATH error precedence and later
success, read-only validation, open-file-description pinning, exact child-number
remapping, executable and signal authority, bootstrap/status cleanup, caller
ownership, concurrency, process identity/session/tree behavior, native-Windows
gating, the customization ledger, and Task 10/Task 11 scope separation. I made
no production or test edits; this report is my only repository change.

## Findings

No Critical, Important, or Minor specification findings.

## Closure of the executable-search-authority finding

The parent now derives the executable search sequence directly from the
original environment mapping with `os.get_exec_path(environment)`. It does so
after snapshotting `environment.items()`, in the same order as direct
`subprocess.Popen`: final environment entries are collected first, then PATH
authority is resolved. The resulting directory sequence is converted to
filesystem bytes once and carried privately in the existing descriptor
transport. The isolated bootstrap consumes that fixed sequence and no longer
recomputes PATH from the normalized byte-keyed environment used only to start
the intermediate Python process.

This closes the prior fail-open behavior for accepted path-like keys. A
`Path("PATH")` key, as well as path-like case variants, receives the same
default search authority as direct `Popen`; normalizing the raw final
environment entry can no longer make its value executable-search authority.
Recognized string and bytes `PATH` keys retain their direct authority.
Mappings containing a recognized key plus a path-like key that normalizes to
the same bytes retain the recognized original lookup result rather than the
collapsed final-environment dictionary's result. Simultaneous recognized
string and bytes PATH keys retain `os.get_exec_path`'s direct ambiguous-input
failure before internal descriptors are acquired.

The custom stateful mapping regression proves ordering rather than merely
testing a plain dictionary: its PATH lookup changes only after `items()` is
read, and both direct and managed launches select the same later-authorized
target. Final environment entries remain the exact ordered bytes snapshotted
before that lookup. This preserves duplicate-normalized and mixed-key raw
environment behavior without letting those entries become a second search
authority.

Search metadata shares the environment frame's 16 MiB ceiling. Its count and
per-directory length fields are included in the bound, incomplete or trailing
data still fails closed, and directory bytes containing NUL are rejected
before child creation. Search directories travel only through the private
pipe: they do not enter bootstrap argv, the bounded status payload, logs,
journals, API/Desktop projections, or workflow evidence. Explicit executable
paths containing a directory bypass PATH lookup exactly as direct `Popen`
does, while relative names use the fixed original-mapping candidate order.

The new real regressions cover path-like `PATH`/`Path`/`path` keys, recognized
string and bytes PATH keys, duplicate-normalized mappings, custom mapping
access order, and the shared transport bound. The existing real regressions
continue to cover omitted and empty environments, non-UTF-8 and mixed raw
entries, first-substantive errno precedence, later-candidate success, and both
orders of all-not-found `ENOENT`/`ENOTDIR`. Failed candidate cases prove no
target effect, zero internal-descriptor growth, and continued caller-handle
ownership.

## Full Task 10 contract assessment

### Bounded read-only descriptor authority and identity

The named `inherited_descriptors` argument accepts no more than 64 unique exact
integers above standard input/output/error. It rejects booleans and other
non-integers, standard or negative numbers, duplicates, closed descriptors,
raw `pass_fds`, `shell=True`, and caller `preexec_fn` through bounded
fail-closed paths. POSIX descriptors are duplicated with
`F_DUPFD_CLOEXEC`; only pinned open-file descriptions reporting `O_RDONLY`
through `F_GETFL` are admitted. Writable/read-write files, pipe write ends,
sockets, and inspection failures cannot create a target or transfer caller
ownership.

Owned pins remain live through bootstrap launch and are allocated above every
nominated target. The bootstrap remaps each pin to its exact child descriptor
number, then closes the pin. Status and environment pipes are allocated above
targets and pins, so remapping cannot clobber another pin or private channel.
Deterministic caller-number close/reuse, multi-descriptor, 64-descriptor, and
concurrent-launch coverage proves that the child receives the validated open
file descriptions. `close_fds=True` and the exact private `pass_fds` set keep
unrelated descriptors closed.

### Launch compatibility, cleanup, and process ownership

Explicit final environments remain byte-exact rather than inheriting Python
startup additions. Omitted environments retain their parent snapshot, and
empty/minimal, string, bytes, mixed, path-like, non-UTF-8, and
duplicate-normalized entries remain covered. Environment values and search
directories share a bounded, length-framed, concurrently consumed private
transport that handles payloads above pipe capacity and closes both ends on
success and every failure.

Omitted/`None`, empty, absolute, relative, missing, successful, invalid, and
throwing string/bytes/path-like executables retain direct selection and public
exception authority. Signal dispositions required by `restore_signals` are
reapplied after isolated Python startup without `preexec_fn`. The target execs
in place, preserving argv, PID, cwd, stdio, session/process-group identity,
process-tree containment, resource accounting, termination, escalation, and
reap ownership. CLOEXEC EOF remains authoritative for final-exec success;
bounded failure status is reconstructed synchronously, and malformed or
partial status fails closed.

All operations after internal allocation remain inside the cleanup-owning
boundary. Pinning, pipe creation, `Popen`, payload transport, status reads,
target exec failure, and successful launch close internal handles while
leaving every caller-owned nominated descriptor untouched. Parent-side launch
failure kills and waits for any created bootstrap child.

### Platform, ledger, and phase boundary

Native Windows rejects every nonempty inherited-descriptor request before Job
Object creation or child launch. Empty requests retain the historical
suspended-child Job assignment/resume, identity, termination, resource, and
query-proven reap path.

The `managed-process-inherited-descriptors` customization entry remains
adjacent to the historical `managed-process-tree` entry, copies rather than
advances its upstream identity, retains the required expected subject, and
accurately records the generic bounded seam. The sixth repair changes only
`tools/managed_process.py` and `tests/tools/test_managed_process.py`. The
complete Task 10 range contains no workflow spill materialization, shell
lexer/prologue, descriptor-to-digest manifest, rendered-command evidence,
executor wiring, API/Desktop spill surface, persistent-session recovery, or
other Task 11 implementation.

## Fresh verification evidence

All Python tests were run through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Focused Task 10 gate — `tests/tools/test_managed_process.py`,
   `tests/tools/test_process_registry.py`, and
   `tests/scripts/test_workflow_merge_gate.py`: **3 files, 238 tests passed, 0
   failed, no retries**.
2. Strict customization validation —
   `../../.venv/bin/python scripts/check_upstream_customizations.py --strict
   --base-ref HEAD`: **PASS**.
3. Ruff on `tools/managed_process.py`, `tests/tools/test_managed_process.py`,
   `tests/tools/test_process_registry.py`, and
   `tests/scripts/test_workflow_merge_gate.py`: **PASS**.
4. `git diff --check 94ab8ceb9..HEAD` and production/test/ledger
   `git diff --check bee92ad4c..HEAD -- ':!*.md'`: **clean**.
5. A separate direct-facade diagnostic proved empty and omitted relative-name
   environments select the same target as direct `Popen`, and an explicit
   executable path does not invoke a custom mapping's raising PATH lookup:
   **PASS**.
6. Before this report was written, the branch was exactly
   `feat/workflow-language-phase-3-semantic-compatibility-resilience`, HEAD and
   tree matched the pinned identities above, and the worktree was clean.

## Final assessment

Fix round 6 closes the original-mapping executable-search-authority defect
without changing the established descriptor, raw environment, executable,
signal, cleanup, process-tree, Windows, ledger, or scope guarantees. Task 10
now provides the approved narrow generic bounded child-descriptor inheritance
primitive and is specification-complete. It is ready to close before Task 11
begins.
