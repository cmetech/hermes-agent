# Phase 3 Task 10 Specification Review 1

**Verdict:** PASS

**Reviewed HEAD:** `66be2d5362ffa2f1ac34a2c0a7533c331bab4474`

**Reviewed tree:** `1a811970a9ac609adebdac32fc15aba0e3d033c6`

**Task baseline:** `bee92ad4c`

**Severity counts:** 0 Critical, 0 Important, 0 Minor

## Scope reviewed

I read the repository instructions, the complete Task 10 plan, the approved
Phase 3 safe-large-Bash-substitution design boundary that consumes this
primitive in Task 11, and the full three-commit
`bee92ad4c..66be2d536` production/test/ledger diff. I inspected the complete
`ManagedProcessTree.spawn()` path, the pre-existing process identity,
termination, resource, Windows Job Object, and reap tests, the new descriptor
tests, the adjacent customization-ledger entries, and both post-implementation
release-gate repair commits. I made no production or test edits.

The implementation is the intended narrow generic seam. It knows only about
bounded descriptors and process creation; it contains no workflow value,
spill, Bash-rendering, evidence, or Task 11 behavior.

## Findings

No Critical, Important, or Minor specification findings.

## Contract assessment

- `ManagedProcessTree.spawn()` exposes only the explicit
  `inherited_descriptors` argument. It rejects the raw `pass_fds` escape hatch,
  non-sequences, booleans and other non-integers, standard/negative
  descriptors, duplicates, closed descriptors, and lists above 64 entries.
  The limit admits exactly 64 unique open integer descriptors greater than 2.
- A non-empty POSIX request forces `close_fds=True` and supplies exactly the
  validated tuple as `pass_fds`, even when a caller requested
  `close_fds=False`. The live child test reads the nominated read-only pipe
  endpoint, proves an unrelated explicitly-inheritable descriptor is closed,
  and proves `start_new_session` still establishes the child as session
  leader.
- Descriptor ownership remains with the caller on successful spawn and on
  spawn failure. The primitive neither closes nor silently adopts the
  nominated handles. Existing process-tree ownership remains intact: the new
  live descendant test proves termination and reap, while the unchanged suite
  continues to prove process identity, escalation, resources, descendant
  cleanup, direct-child reap, and Windows Job Object behavior.
- Native Windows rejects every non-empty descriptor request before Job Object
  creation or `Popen`. Empty requests follow the pre-existing Windows and
  POSIX paths unchanged. The validation does not weaken suspended-child Job
  assignment, owner-held Job handles, failure cleanup, or query-proven
  quiescence.
- The new `managed-process-inherited-descriptors` customization entry is
  immediately adjacent to the historical `managed-process-tree` entry. The
  historical entry and its expected subject are untouched. The extension
  records only the owned spawn symbol and descriptor contract, names the exact
  tests, uses expected subject `feat(process): inherit bounded child
  descriptors`, contains appropriate merge/removal guidance, is marked
  `upstream_candidate: true`, and copies the historical
  `last_verified_upstream` identity instead of advancing it.

## Follow-up commit classification

The two commits after the Task 10 implementation are exact necessary
release-gate drift repairs:

1. `1d237adaa` adds the six already-implemented Phase 3 semantic suites to the
   base merge gate and its behavioral selection assertion. It changes no
   production behavior and adds no Task 11 coverage.
2. `66be2d536` raises the existing detail-model normalizer bound from 2 to 3
   and updates stale compatibility expectations to the already-authored v3
   normalized truth. It adds no API field, endpoint, projection, evidence
   payload, Desktop authority, or Task 14 recovery surface.

Across all three commits there is no large-value materialization, workflow
spill file, descriptor manifest, shell prologue, rendered-command evidence,
or executor call-site wiring. Task 11 has not begun.

## Verification evidence

All Python tests were run only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Focused generic suite — `tests/tools/test_managed_process.py`,
   `tests/tools/test_process_registry.py`, and
   `tests/scripts/test_workflow_merge_gate.py`: **3 files, 199 tests passed, 0
   failed, no retries**.
2. Strict customization gate —
   `../../.venv/bin/python scripts/check_upstream_customizations.py --strict
   --base-ref HEAD`: **PASS**.
3. Live base merge gate —
   `scripts/test_workflow_merge_gate.sh --phase base`: **53 Python files,
   2,556 tests passed; installed-distribution test passed; 11 Desktop test
   files, 155 tests passed; 0 failures and no Python retries**. The gate
   reported `TESTED_BASE_SHA=66be2d5362ffa2f1ac34a2c0a7533c331bab4474`.
4. `git diff --check bee92ad4c..66be2d536` — clean.
5. The reviewed worktree was clean before this retained report was written.

## Final assessment

Task 10 satisfies its approved generic descriptor-inheritance contract and
preserves the existing managed-process safety envelope on POSIX and Windows.
The ledger amendment is correctly isolated, both follow-up commits are bounded
baseline repairs, and neither Task 11 implementation nor Task 14 surface
expansion has leaked into this task. Task 10 is ready to close.
