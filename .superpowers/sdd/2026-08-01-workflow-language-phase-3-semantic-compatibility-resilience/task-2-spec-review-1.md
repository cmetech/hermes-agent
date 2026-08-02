# Phase 3 Task 2 Independent Specification Review

**Reviewed commit:** `b820d53ee83b782692b5df58f3b79d17982970db`

**Reviewed tree:** `10b2754034e3292783d470496ea37378f9001bb7`

**Baseline:** `3309dd7e086b86e872f14f3633dbcc0720382b5b`

**Verdict:** CHANGES REQUIRED

**Findings:** 0 Critical, 2 Important, 0 Minor

## Scope and evidence

I read the root `AGENTS.md`, the approved Phase 3 design's admission,
persistence, API/Desktop, error-contract, and verification requirements, the
plan's global protocol and complete Task 2, and the complete
baseline-to-target production/test diff. The requested commit and tree match,
the worktree was clean before this report, and the implementation remains
bounded to Task 2 admission, snapshot, scheduler-load, and durable-code
surfaces. I found no Task 3 strict-reference implementation or other later
Phase 3 production scope in the diff.

Focused verification was run only through the repository wrapper with flaky
file retries disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_execution_semantics.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_language_snapshot.py \
  tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_api_runtime.py \
  tests/plugins/workflow/test_scheduled_runs.py \
  tests/plugins/workflow/test_showcase_schedule_e2e.py \
  tests/plugins/workflow/test_crash_recovery.py

Result: 8 files, 291 tests passed, 0 failed, no retries.
```

Both the implementation range and current worktree pass `git diff --check`.
The codec otherwise establishes the planned exact top-level, limits, node,
and retry field sets; rejects booleans and invalid finite/range values; seals
the projection into `resources.json` and its digest/tree identities; verifies
the projection against normalized requested semantics; preserves the legacy
resource shape/digest; wires CLI, API, Gateway, and showcase admission to a
resolved authority; and registers both additive mismatch codes.

## Important findings

### I-1 — V3 resume still resolves current configuration before using the sealed authority

`plugins/workflow/scheduler.py:2008` unconditionally calls
`self._run_execution_limits(package)` before the Archon-v3 branch at
`plugins/workflow/scheduler.py:2009-2040` authenticates and applies the sealed
projection. `_run_execution_limits()` rebuilds limits from
`self.profile_execution_limits` plus the sidecar. The five sealed v3 values
eventually overwrite the corresponding fields, but current policy has already
been read and resolved; a changed or newly invalid current-policy combination
can therefore affect or prevent resume before the durable projection becomes
authoritative.

This violates the approved design at `...design.md:294-302` (resume executes
the sealed projection directly and does not call current-config limit
resolution for v3 nodes) and Task 2 at `...plan.md:230-234` (make the v3
scheduler load authenticated effective semantics directly). The changed-config
test at `tests/plugins/workflow/test_phase3_execution_semantics.py:373-416`
changes the five values only to another valid configuration and asserts the
post-overwrite result. It does not prove that `_run_execution_limits()` was
never called, so the prohibited authority read remains green.

**Required remediation:** Select the Archon-v3 path before invoking the legacy
limit resolver. Parse and verify `phase3_execution_semantics` from the
authenticated `resources.json` bytes and construct the v3 execution authority
without resolving those five fields from current configuration. Retain
`_run_execution_limits()` exactly for legacy. Add a resume regression that
fails if the current-config resolver is called for an admitted v3 run (in
addition to proving that changed valid values cannot alter the sealed result).

### I-2 — The required canonical admission-parity and scheduled-admission proof is missing

Task 2 requires one behavioral parity test using the same Archon profile and
authenticated sidecar across CLI, API, Gateway, showcase, scheduled admission,
and direct-store helpers, asserting identical canonical projection bytes and
digest (`...plan.md:205-218`). The added surface tests do not form that
contract:

- CLI, API, Gateway, and showcase use separately created packages and assert
  only the five-field `limits` subobject
  (`tests/plugins/workflow/test_cli.py:256-319`,
  `tests/plugins/workflow/test_api_runtime.py:44-131`,
  `tests/plugins/workflow/test_phase3_execution_semantics.py:272-351`, and
  `tests/plugins/workflow/test_showcase_schedule_e2e.py:67-138`).
- No assertion compares the complete canonical
  `phase3_execution_semantics` bytes or `input_manifest_digest` across those
  authorities, so node applicability, timeout source/cap bits, retry details,
  and exact field parity can drift independently while every new test stays
  green.
- `tests/plugins/workflow/test_scheduled_runs.py` has no Archon-v3 execution
  semantics case at all, and the new showcase test starts an immediate
  showcase rather than exercising scheduled admission/promotion.
- The direct-store test at
  `tests/plugins/workflow/test_phase3_execution_semantics.py:199-244` proves
  explicit/default behavior in isolation, but it does not use the same
  authenticated sidecar or join the surface-parity assertion.

This also leaves the plan's explicit scheduled-promotion handoff unverified,
despite the full scheduled suite passing unchanged.

**Required remediation:** Add an end-to-end parity fixture with one package,
one authenticated sidecar, one resolved authority, and representative
Bash/script/AI node semantics. Admit it through every listed boundary,
including a real scheduled path and direct store, and compare the complete
canonical execution-semantics bytes plus the resulting manifest digest. Keep
the independent direct-store default assertion and prove scheduled resume uses
the same sealed bytes after current configuration changes.

## Review conclusion

The schema codec, sealing, tamper check, legacy gating, and four immediate
admission call sites are substantively aligned and green. Task 2 is not yet
specification-complete because the scheduler still consults current policy on
v3 resume and the required all-boundary canonical/scheduled parity invariant
has not been demonstrated. Both Important findings should be fixed and
independently reverified before Task 2 closes.
