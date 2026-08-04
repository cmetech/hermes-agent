# Phase 3 Task 2 Specification Closure Rereview 1

**Reviewed HEAD:** `ad2157a8ef217cbe540ed89f826f96062fa80bcb`

**Reviewed tree:** `0990f4e994aeb16ab47f6c81c0ab8801fa6194d3`

**Fix baseline:** `62d719c17`

**Task implementation baseline:** `b820d53ee83b782692b5df58f3b79d17982970db`

**Verdict:** CHANGES REQUIRED

**Findings:** 0 Critical, 1 Important, 0 Minor

## Scope and evidence

I reread the original Task 2 specification and quality reviews, rechecked the
approved design's immutable-admission, resume, persistence, error, and testing
contracts plus the complete Task 2 plan, and inspected the complete
`62d719c17..ad2157a8e` production/test fix diff. The fix remains bounded to the
Task 2 execution-semantics codec, scheduler-load/failure path, catalog tests,
and focused execution-semantics tests. It introduces no Task 3 reference
behavior or other later Phase 3 production scope.

The exact Task 2 gate passed through the required wrapper with flaky file
retries disabled:

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

Result: 8 files, 299 tests passed, 0 failed, no retries.
```

Adjacent affected suites also passed with retries disabled:

```text
scripts/run_tests.sh \
  tests/plugins/workflow/test_scheduler.py \
  tests/plugins/workflow/test_store.py \
  tests/plugins/workflow/test_deadlines.py \
  tests/plugins/workflow/test_retry.py

Result: 4 files, 62 tests passed, 0 failed, no retries.

scripts/run_tests.sh tests/plugins/workflow/test_schedule_revalidation.py

Result: 1 file, 64 tests passed, 0 failed, no retries.
```

The fix range passes `git diff --check`, and the worktree was clean before this
report was written.

## Closed findings

### Original specification I-1 — closed

`plugins/workflow/scheduler.py:2062-2106` now selects the Archon-v3 branch
before the legacy `_run_execution_limits()` call. The v3 helper at
`plugins/workflow/scheduler.py:1986-2033` seeds the five semantic fields from
the authenticated projection, excludes their sidecar names from resumed
resolution, and preserves only the existing non-Phase-3 lifecycle/resource
controls. Legacy alone retains the original resolver branch.

`tests/plugins/workflow/test_phase3_execution_semantics.py:694-777` makes the
legacy resolver raise if called for v3, proves zero calls, supplies a sidecar
that would be incompatible with changed current semantic values, and verifies
the sealed five fields plus non-Phase-3 parallelism, process, descendant, and
shutdown controls. This closes the current-config authority defect without
changing legacy behavior.

### Original quality I-1 — closed

The reader now requires `float` for every non-null seconds value, while keeping
attempt counts and retry delays strict integers
(`plugins/workflow/execution_semantics.py:112-129`, `354-389`). The
authenticated v3 load boundary also reserializes the complete resources
document with the store's canonical JSON rules and compares the raw bytes
before accepting the projection (`plugins/workflow/scheduler.py:2073-2096`).

Behavior tests at
`tests/plugins/workflow/test_phase3_execution_semantics.py:780-833` reseal
integer, exponent, alternate-decimal, whitespace, and field-order variants and
prove `workflow_execution_semantics_mismatch` before any claim. This closes
the alternate-byte/numeric-identity defect.

### Original quality I-2 production behavior — closed

`plugins/workflow/scheduler.py:2128-2144` now catches
`WorkflowExecutionSemanticsError` before the generic scheduled-preparation
catch and routes its bounded stable code/path through the existing atomic
package-validation failure transition. That transition consumes the opaque
scheduled authorization, fails the run, and prevents a claim. The focused
test proves the stable last error, durable journal code, consumed
authorization, and zero executor calls once an authorization reaches this
path.

### Original parity and catalog findings — partially closed

The same-package fixture now admits one authenticated Archon package with the
same sidecar and input through CLI, Gateway, API, scheduled API admission,
showcase, and direct store. It compares the complete canonical semantics
serialization, complete `resources.json` digest, and persisted
`input_manifest_digest` across all six admission surfaces
(`tests/plugins/workflow/test_phase3_execution_semantics.py:377-636`).

The catalog test now drives both mismatch codes through authenticated store and
scheduler resume rather than calling the codec/verifier directly
(`tests/plugins/workflow/test_phase3_code_catalog.py:121-207`). That closes the
behavior-link requirement.

## Important finding

### I-1 — The scheduled closure tests bypass the real scheduled authorization and revalidation boundary

The original quality remediation and the Task 2 handoff require a genuine
scheduled promotion after restart/current-config change, plus a genuine
scheduled mismatch proving stable code and no claim. The new tests do create a
scheduled run through API admission, but neither traverses the load-bearing
production authorization path:

- the parity/restart test creates a private authorization directly with
  `RunStore._scheduled_promotion_authorization(..., lambda: None)` and calls
  `_prepare_run_package()` itself
  (`tests/plugins/workflow/test_phase3_execution_semantics.py:641-648`); it
  never calls `RunScheduler._authorize_scheduled_promotion()`,
  `verify_sealed_snapshot()`, `revalidate_scheduled_run()`, or promotion;
- the stable-mismatch test manually constructs incomplete scheduled metadata,
  then monkeypatches `_authorize_scheduled_promotion()` with another private
  no-op verifier
  (`tests/plugins/workflow/test_phase3_execution_semantics.py:836-903`). The
  scheduler's real authorization/revalidation implementation cannot run in
  that test.

Consequently the suite still cannot detect an integration regression where
the real scheduled boundary rejects or rewrites the failure before the new
stable-code branch, where restarted execution binding/catalog/trust evidence
is not accepted, or where changed configuration affects the real promotion
path. This is the remaining part of the original specification I-2 / quality
I-3 finding, not a request for new scope.

**Required remediation:** Use the established real scheduled-revalidation
fixtures/patterns from `tests/plugins/workflow/test_schedule_revalidation.py`:
admit the Archon-v3 package through `start_api_run`, restart `RunStore` and
`RunScheduler` with a real `WorkflowRunnerBinding` and `ExecutionFence`, change
the current semantic configuration, and call `advance()` or `advance_all()`
without replacing `_authorize_scheduled_promotion`. Prove the scheduled run
passes real revalidation and consumes its original sealed semantics. Add the
corresponding real-boundary mismatch case (for example, inject the codec
mismatch after a genuine authorization-compatible snapshot rather than
forging a no-op authorization) and retain the assertions for the stable code,
consumed authorization, zero claims/executor calls, and durable journal event.

## Conclusion

The production authority, canonical-byte, stable scheduled-failure, legacy,
non-Phase-3-control, six-admission-parity, and catalog defects are otherwise
closed and all focused/adjacent tests are green. Task 2 still lacks the
explicitly required genuine scheduled restart/revalidation proof because both
new scheduled tests bypass that boundary. One bounded test fix and focused
rereview are required before specification closure.
