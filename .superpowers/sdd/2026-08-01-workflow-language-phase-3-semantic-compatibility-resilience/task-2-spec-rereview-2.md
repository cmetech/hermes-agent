# Phase 3 Task 2 Final Specification Closure Rereview

**Reviewed HEAD:** `849405605ca6b391906b46032a5f4d0c40c0695e`

**Reviewed tree:** `67be8f04c3a2c81d9d86643f2ea36360f56fe2ab`

**Fix baseline:** `e8c36c6c5`

**Task implementation baseline:** `b820d53ee83b782692b5df58f3b79d17982970db`

**Verdict:** PASS

**Findings:** 0 Critical, 0 Important, 0 Minor

## Scope and evidence

I reread the remaining Important finding in
`task-2-spec-rereview-1.md`, inspected the complete
`e8c36c6c5..849405605` fix diff, and rechecked the approved Task 2 scheduled
admission, restart, immutable-semantics, stable-failure, and no-claim
contracts. The closure commit changes only
`tests/plugins/workflow/test_phase3_execution_semantics.py`; it makes no
production change and introduces no Task 3 or later Phase 3 scope.

The exact Task 2 gate passed through the required repository wrapper with
flaky file retries disabled:

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

The directly adjacent scheduled/store/scheduler gate also passed with retries
disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
scripts/run_tests.sh \
  tests/plugins/workflow/test_schedule_revalidation.py \
  tests/plugins/workflow/test_scheduler.py \
  tests/plugins/workflow/test_store.py

Result: 3 files, 103 tests passed, 0 failed, no retries.
```

The closure range passes `git diff --check`, and the worktree was clean before
this report was written.

## Remaining Important finding — closed

The replacement tests now traverse the genuine scheduled production boundary.
The small `_RecordingAuthorizationRunStore` at
`tests/plugins/workflow/test_phase3_execution_semantics.py:48-58` only records
the authorization returned by `super()`; it does not replace, bypass, or
weaken its verifier. Neither scheduled test calls the private authorization
factory directly, replaces `_authorize_scheduled_promotion`, or supplies a
no-op verification callback.

### Scheduled success after restart and configuration change

`tests/plugins/workflow/test_phase3_execution_semantics.py:389-727` now:

- admits the same trusted Archon-v3 package through real scheduled API
  admission with a production `WorkflowRunnerBinding`;
- retains the complete six-boundary canonical semantics/resource bytes and
  manifest-digest parity assertions;
- rewrites the profile's current semantic configuration after admission;
- creates a new `RunStore`, acquires a new coordinator lease, constructs a new
  `RunScheduler` with the real binding and its `ExecutionFence`, and calls
  `advance()` rather than `_prepare_run_package()`;
- therefore exercises the unmodified
  `_authorize_scheduled_promotion()` -> `verify_sealed_snapshot()` ->
  `revalidate_scheduled_run()` -> promotion chain;
- proves the first sealed node succeeds, the run records real
  `schedule_revalidation` evidence, and the exact admitted `resources.json`
  bytes remain unchanged despite the new current configuration; and
- proves exactly one real authorization was issued and consumed and exactly
  one `run_promoted` event was journaled.

This closes the real scheduled restart/change-config portion of the finding.

### Scheduled execution-semantics mismatch

`tests/plugins/workflow/test_phase3_execution_semantics.py:918-1025` now:

- creates a trusted profile package and schedules it through
  `start_api_run()` with the production binding;
- restarts the store, reseals the deliberately inconsistent semantics plus the
  scheduled snapshot identities so genuine snapshot/catalog/trust
  revalidation can complete;
- constructs a new scheduler with the production binding and a real
  coordinator `ExecutionFence` and calls `advance()` without replacing the
  authorization method;
- proves issuance of exactly one store-backed authorization, which establishes
  that the real authorization/revalidation chain succeeded before the
  execution-semantics verifier failed;
- proves the authorization was consumed, the durable terminal code remains
  `workflow_execution_semantics_mismatch`, the journal carries that validation
  code, node attempts remain empty, the worker-claim table remains empty, and
  the executor is never invoked.

This closes the stable-code/no-claim/consumption/journal portion of the
finding at the real scheduled boundary.

## Regression and scope audit

All findings from the original Task 2 specification/quality reviews and the
first closure rereview remain closed at this HEAD:

- v3 resume never invokes the legacy five-field current-config resolver;
- non-Phase-3 controls remain available and legacy retains its original path;
- canonical raw JSON/numeric representation is enforced before use;
- scheduled semantic mismatch retains its stable bounded code and fails before
  claim;
- one authenticated package has complete canonical bytes/digest parity across
  CLI, API, Gateway, showcase, scheduled API admission, and direct store;
- changed configuration cannot replace admitted scheduled semantics; and
- both mismatch catalog entries are linked to real scheduler resume failures.

The final closure commit is test-only and uses existing production admission,
binding, fencing, authorization, revalidation, promotion, failure, and journal
interfaces. It does not alter runtime behavior, prompt/tool surfaces, legacy
snapshots, API schemas, Desktop projections, Phase 4 loops/includes, or Phase
5 portability behavior.

## Conclusion

The final scheduled-boundary evidence is genuine and complete. Task 2 has
final specification closure at `849405605ca6b391906b46032a5f4d0c40c0695e`
with 0 Critical, 0 Important, and 0 Minor findings.
