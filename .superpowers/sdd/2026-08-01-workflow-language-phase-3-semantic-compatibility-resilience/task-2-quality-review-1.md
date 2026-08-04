# Phase 3 Task 2 Quality Review 1

**Reviewed commit:** `b820d53ee83b782692b5df58f3b79d17982970db`

**Reviewed tree:** `10b2754034e3292783d470496ea37378f9001bb7`

**Baseline:** `3309dd7e086b86e872f14f3633dbcc0720382b5b`

**Scope:** Task 2's execution-semantics codec, immutable snapshot sealing,
admission callers, scheduler reload path, durable-code registration, and
focused/adjacent regression tests.

**Verdict:** CHANGES REQUIRED

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 3 |
| Minor | 0 |

## Strengths

- The new projection has a small, immutable representation and validates exact
  top-level, limits, node, and retry field sets before use.
- Requested retry counts remain distinct from effective totals, including the
  six-requested/five-effective cap boundary.
- `resources.json`, `input_manifest_digest`, and the sealed-tree digest bind the
  new projection to the existing authenticated snapshot rather than creating a
  second mutable persistence channel.
- Production admission callers converge on `RunStore.prepare_run_snapshot()`,
  while legacy snapshots omit the Phase 3 field and retain their prior digest.
- Both the exact Task 2 gate and additional scheduler/store/deadline/retry tests
  pass with retries disabled.

## Findings

### Important 1 — Canonical verification accepts a different numeric representation

**Evidence:** `plugins/workflow/execution_semantics.py:112-122`,
`plugins/workflow/execution_semantics.py:380-410`

The reader accepts either `int` or `float` for every seconds field, converts the
value to `float`, rebuilds the expected projection, and then compares Python
dictionaries. Python considers `300 == 300.0`, so an authenticated projection
whose canonical builder emitted `300.0` is also accepted when the sealed JSON
contains `300`. Other lexically different JSON-number encodings have the same
problem after `json.loads()`.

A direct behavior check at the reviewed commit changed
`ai_idle_timeout_seconds` from `300.0` to `300`; the reader accepted it and
returned `300.0`. The semantic values agree, but the canonical bytes and their
digest do not. This weakens the approved exact/canonical snapshot authority and
permits multiple byte identities for one supposedly canonical projection.

**Required remediation:** Make the durable representation have one accepted
numeric type/encoding and verify canonical bytes at the authenticated read
boundary, not only Python value equality. At minimum reject integer encodings
for the five seconds fields and all non-null per-node seconds fields; preferably
centralize canonical execution-semantics serialization and verify the stored
projection/resources bytes against it. Add tamper tests for `300.0` -> `300`,
equivalent exponent/decimal spellings, ordering/whitespace where applicable,
and confirm the stable mismatch code.

### Important 2 — Scheduled resume discards the stable execution-mismatch code

**Evidence:** `plugins/workflow/scheduler.py:2019-2065`,
`plugins/workflow/store.py:5859-5887`

An unscheduled v3 resume propagates `WorkflowExecutionSemanticsError` with
`workflow_execution_semantics_mismatch`. The same error during scheduled
promotion is captured by the broad `except Exception` path and sent to
`_fail_scheduled_package_preparation()`, which always persists
`schedule_revalidation_failed`. The Phase 3 code and bounded diagnostic path
are therefore lost specifically on one of the admission/resume boundaries
named by Task 2.

The run still fails closed before claim, so this is not an execution bypass.
It does violate the stable error/evidence contract and makes scheduled
execution-semantics drift indistinguishable from unrelated authorization or
package preparation failures.

**Required remediation:** Handle `WorkflowExecutionSemanticsError` explicitly
before the generic scheduled-preparation catch and atomically terminalize the
queued run with its stable bounded code/path while consuming the opaque
scheduled authorization. Preserve the generic code for unrelated scheduled
revalidation failures. Add a real scheduled-promotion tamper test proving no
claim occurs and `last_error`/journal evidence retains
`workflow_execution_semantics_mismatch`.

### Important 3 — The required parity tests do not compare parity or exercise scheduled promotion

**Evidence:** `tests/plugins/workflow/test_phase3_execution_semantics.py:198-351`,
`tests/plugins/workflow/test_phase3_execution_semantics.py:373-455`,
`tests/plugins/workflow/test_phase3_code_catalog.py:121-154`

Task 2 requires the same Archon package plus authenticated sidecar to traverse
CLI, API, gateway, showcase, scheduled promotion, and direct-store admission,
with identical canonical projection bytes and digest. The tests instead create
separate packages per surface and assert only each projection's five-field
`limits` mapping. They never compare the complete node projection bytes or
`input_manifest_digest` across surfaces. No Task 2 change was made to
`test_scheduled_runs.py`, and the changed-config test calls the private
`_prepare_run_package()` helper on an unscheduled run rather than exercising a
scheduled promotion/restart. The code-catalog test likewise calls verifier
functions directly despite the task requiring real load/admission or resume
behavior.

These tests would remain green if one boundary drifted in per-node retry,
timeout source/capped bits, canonical serialization, digest binding, or
scheduled mismatch handling. That is material regression exposure around the
central purpose of this task.

**Required remediation:** Add one table-driven parity fixture using the same
package, profile limits, and sidecar limits for all six boundaries. Capture the
complete canonical `phase3_execution_semantics` bytes and
`input_manifest_digest`, assert equality, restart the store/scheduler, and run
the genuine scheduled-promotion authorization path. Route the catalog
completeness cases through those real resume failures rather than direct helper
calls alone.

## Verification evidence

The exact Task 2 gate passed through the repository runner with flaky retries
disabled:

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

8 files, 291 tests passed, 0 failed; no retries.
```

Additional affected tests also passed:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_scheduler.py \
  tests/plugins/workflow/test_store.py \
  tests/plugins/workflow/test_deadlines.py \
  tests/plugins/workflow/test_retry.py

4 files, 62 tests passed, 0 failed; no retries.
```

`git diff --check` passed for the reviewed range, and Ruff passed on all Task 2
production and test files. The worktree was clean before independent review
reports were written. This review modified no production or test files.
