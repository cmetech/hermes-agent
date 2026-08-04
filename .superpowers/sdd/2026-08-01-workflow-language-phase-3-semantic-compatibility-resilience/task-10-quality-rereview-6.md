# Phase 3 Task 10 Independent Quality Rereview 6

**Review date:** 2026-08-02

**Task 10 baseline:** `bee92ad4c2b81d63c27e266f84299a3a52a5dc6e`

**Prior closure evidence commit:** `94ab8ceb9`

**Executable-search-authority repair:** `9976161faf55f461f3f9ab3b56760a995bf6170f`

**Reviewed tree:** `727b6f3b9356e79bd54ec648e9dba25d61de1093`

**Verdict:** PASS

## Severity summary

- Critical: 0
- Important: 0
- Minor: 0

## Scope and evidence reviewed

I independently read the complete repository instructions, the approved Phase
3 design, the Task 10 plan and Task 11 consumer boundary, every retained Task
10 specification and quality review through rereview 5, the complete Task 10
implementation/fix history, and the exact sixth repair
`94ab8ceb9..9976161fa`. I traced original-mapping environment access and
executable search resolution, final raw environment-vector construction,
search-path framing and parsing, candidate ordering and errno precedence,
explicit executable behavior, descriptor allocation/remapping/CLOEXEC state,
environment and status transport liveness, every setup/spawn/exec cleanup
path, read-only open-file-description pinning, argv/cwd/stdio/signal/PID/
session/process-tree compatibility, caller ownership, concurrency, native-
Windows rejection, customization-ledger scope, and the Task 10/Task 11
boundary. I made no production or test edits; this report is my only authored
repository file.

## Findings

No Critical, Important, or Minor quality findings.

## Closure of the executable-search-authority finding

The sixth repair no longer reconstructs executable-search authority from the
normalized final environment. The parent first snapshots `environment.items()`
for the final raw `envp`, then invokes `os.get_exec_path(environment)` on the
original mapping and serializes that exact ordered result. This matches direct
`subprocess.Popen`'s POSIX access order and preserves custom mapping side
effects and exceptions rather than substituting dictionary semantics.

The bootstrap parses the privately transported search directories separately
from the final environment entries and joins only that parent-resolved sequence
to a slash-free executable. Path-like keys that normalize to `PATH` therefore
remain final environment entries but cannot grant search authority. Recognized
string or bytes `PATH` keys retain authority; simultaneous recognized string
and bytes keys retain direct `os.get_exec_path` rejection; and a recognized key
cannot be overridden by a later path-like key with the same normalized bytes.
The real regressions prove all of those cases, including a stateful mapping
whose PATH result changes after `items()` is read.

Search metadata shares the existing 16 MiB length-framed private transport and
is charged to the same bound before any internal descriptor or child is
created. The frame carries a bounded count plus bounded directory lengths,
rejects NUL bytes, incomplete data, trailing data, and over-bound payloads,
and never places search directories or environment values in bootstrap argv,
the status payload, logs, evidence, API fields, or Desktop projections. The
child consumes the transport concurrently with the parent writer, so payloads
larger than pipe capacity do not deadlock. Python's retrying `os.read`/`os.write`
semantics, explicit short-write handling, CLOEXEC EOF, and bounded status reads
retain the established partial-I/O and failure behavior.

Candidate behavior now matches direct `Popen` across the complete reviewed
surface: explicit executables containing a directory bypass PATH; omitted and
explicit-`None` executables select `argv[0]`; explicit empty executables remain
fail-closed; empty and relative PATH components remain relative to the final
`cwd`; string, bytes, non-UTF-8, missing, and default PATH values retain order;
the first substantive error remains authoritative; later candidates can still
succeed; and the last `ENOENT`/`ENOTDIR` remains the all-not-found fallback.
Parent-side reconstruction retains the original executable object's exception
filename identity.

## Full Task 10 contract assessment

### Descriptor authority, identity, and ownership

The public argument remains bounded to at most 64 unique exact integers above
stdin/stdout/stderr. It rejects booleans and other non-integers, standard or
negative descriptors, duplicates, closed descriptors, raw `pass_fds`,
`shell=True`, and caller `preexec_fn` through fail-closed paths. POSIX
descriptors are duplicated with `F_DUPFD_CLOEXEC`; only pinned `O_RDONLY`
open-file descriptions are admitted. Writable/read-write files, pipe write
ends, sockets, and access-inspection failures cannot reach child effects and do
not transfer caller ownership.

Pins remain live through launch and are allocated above every nominated target.
The isolated bootstrap remaps each pin to the exact child number, closes the
original pin, and cannot clobber another pin or either private channel.
Deterministic caller-number close/reuse, multiple-descriptor, inclusive
64-descriptor, and concurrent-launch coverage retain the original identities.
Only the pins and private status/environment read handles enter `pass_fds`, so
unrelated inheritable descriptors remain closed.

### Bootstrap compatibility, framing, and cleanup

Final raw environment entries preserve mapping order and distinct normalized
duplicate keys, including string, bytes, path-like, and non-UTF-8 values,
without Python-added variables. Search candidates are a distinct parent-
resolved frame and do not alter those entries. Explicit and inherited
environment cases retain direct target bytes on the exercised platform.

The bootstrap execs in place and retains target PID, argv, cwd, stdio,
requested signal dispositions, session/process group, process-tree ownership,
resource identity, termination, escalation, and reap behavior. Its private
CLOEXEC status descriptor makes EOF authoritative only after successful final
exec. Bounded errno-only failure status contains no environment, executable
candidate, or caller data. Malformed status fails closed, and parent-side
failure kills and waits for any created bootstrap child.

Executable/environment normalization, mapping lookup, bounds, and invalid
path-like failures occur before internal allocation. Pinning, pipe creation,
`Popen`, environment writes, status reads, target exec failure, and successful
launch are all inside the cleanup-owning boundary. Fresh tests and diagnostics
show zero internal descriptor growth, no failed-target effects, and continued
caller-descriptor usability. Concurrent successful launches remain isolated;
the primitive introduces no process-global mutable launch state.

### Platform, ledger, and phase boundary

Native Windows still rejects every nonempty inherited-descriptor request before
Job Object creation or child launch. Empty requests retain the historical
suspended-child assignment/resume, identity, termination, resource, and
query-proven reap path.

The `managed-process-inherited-descriptors` customization entry remains
adjacent to the historical `managed-process-tree` entry, copies rather than
advances its upstream identity, retains the required expected subject, and
accurately records the bounded read-only, identity-pinned generic seam. The
sixth repair changes only `tools/managed_process.py` and
`tests/tools/test_managed_process.py`. It adds no workflow value, spill file,
descriptor manifest, Bash lexer/prologue, rendered-command evidence, executor
wiring, API/Desktop expansion, persistent-session recovery, or other Task 11
or Task 14 behavior.

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
5. Additional no-mock direct-versus-managed diagnostics covered empty and
   relative PATH entries under an explicit `cwd`, an absolute executable,
   stateful mapping access order, lookup exceptions before allocation, target
   output parity, zero internal descriptor growth, and retained caller
   ownership: **PASS**.
6. Before this report was written, the branch was exactly
   `feat/workflow-language-phase-3-semantic-compatibility-resilience`, HEAD and
   tree matched the pinned identities above, and the worktree was clean.

## Final assessment

Fix round 6 closes the remaining fail-open PATH-authority defect by transporting
the original mapping's exact executable-search decision separately from the
byte-exact final environment. It preserves candidate order and errors, framing
bounds and privacy, descriptor identity and ownership, bootstrap compatibility
and cleanup, process-tree and Windows behavior, the customization ledger, and
the Task 10/Task 11 separation. Task 10 is quality-complete with zero findings
and is ready to close before Task 11 begins.
