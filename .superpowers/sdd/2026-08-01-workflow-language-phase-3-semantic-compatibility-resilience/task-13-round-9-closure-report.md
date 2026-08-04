# Task 13 Round 9 Closure Report

Date: 2026-08-04

## Outcome

Closed all three functional findings from `task-13-quality-rereview-9.md` in one
test-driven correction:

1. Notification retry and terminal/dead-letter paths now retain only exact,
   allowlisted, value-free delivery reasons. Unknown or free-form input is
   normalized to `notification delivery failed` in both the outbox row and any
   durable decision fact.
2. A confirmed-missing cross-run recovery whose fresh worker pauses now returns
   an explicit bounded recovery projection. It retains only exact scalar
   provider-attempt accounting and the validated approval kind/action digest
   needed to resume, records `fresh_execution_failed`, and drops session,
   provider, model, usage, audit, fingerprint, warning, output, and other
   provider-controlled fields. The ordinary non-recovery paused path is
   unchanged.
3. Recovery-order tests no longer treat arbitrary `RuntimeError` instances as
   acceptable. Malformed/order-damaged store readers assert exact
   `JournalRecoveryError` messages, while the notification reader asserts its
   exact `NotificationReconciliationError` projection. Existing successfully
   sanitized reader cases remain supported.

No threat-model analysis, threat/security test, or adversarial/exploit
validation was performed.

## Root cause

### Notification delivery compatibility

Round 9 ignored the `error` argument in both `NotificationOutbox.fail()` and
`NotificationOutbox.terminal_fail()` and hardcoded the generic diagnostic.
That erased the coordinator's already-bounded `delivery_store_unavailable`
receipt and made retryable and terminal projections lose useful stable state.

The correction centralizes exact allowlist normalization and uses the resulting
stable reason consistently for `last_error` and terminal/dead-letter facts.
Prefix-shaped or free-form details such as adapter exception type/history text
do not match the allowlist and become the fixed generic reason.

### Selected-recovery paused execution

The ordinary `result.status == "paused"` branch returned directly after copying
the full worker-derived metadata. It never reached `with_recovery_failure()`, so
the newly added paused member in that helper was dead for the normal path.

The correction adds a dedicated `recovery_selected` paused branch. It uses the
same bounded accounting projection as selected-recovery failures and separately
validates the resumable approval descriptor. The failure helper no longer
claims to handle paused results.

### Test precision

`_assert_private_values_absent()` had been widened from the workflow journal
domain error to all `RuntimeError` instances. An unrelated runtime failure could
therefore satisfy an order test merely by omitting its canary.

The helper is narrowed back to `JournalRecoveryError`. Tests that require
fail-closed behavior now assert the exact domain type and stable message at the
call site; notification reconciliation asserts its separate domain wrapper.

## TDD evidence

### Required existing coordinator RED

Command:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_coordinator.py
```

Result: **38 passed / 1 failed**. The sole failure was
`test_gateway_retryable_delivery_receipt_requeues_outbox_row`, with expected
`delivery_store_unavailable` but actual `notification delivery failed`.

### Test-first correction RED

The direct notification compatibility/normalization tests, confirmed-missing
paused executor test, and precise order-domain assertions were added before any
production edit.

First command:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_notifications.py tests/plugins/workflow/test_persistent_session_recovery.py
```

Initial result: **151 passed / 3 failed**. Two failures were the intended
missing production behaviors (stable delivery reason erased; selected recovery
pause lacked `fresh_execution_failed`). The third established the precise
existing journal message as `journal sequence gap: expected 1, received 2`;
the test assertion was corrected to that unchanged production taxonomy.

The same command was rerun before production edits.

Result: **152 passed / 2 failed**. The only failures were the two intended
behavioral REDs:

- allowlisted `delivery_store_unavailable` still persisted as the generic
  reason;
- confirmed-missing fresh paused execution returned unbounded metadata and no
  recovery outcome.

All narrowed malformed/order assertions passed against the existing production
taxonomy during this RED run.

## GREEN evidence

### Gate A

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_notifications.py tests/plugins/workflow/test_coordinator.py tests/plugins/workflow/test_persistent_session_recovery.py
```

Result: **3 files / 193 passed / 0 failed**.

### Gate B

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_persisted_sessions.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_journal_reserve_fanout.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_shutdown_recovery.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_notifications.py tests/plugins/workflow/test_coordinator.py
```

Result: **12 files / 415 passed / 0 failed**.

### Gate C

```text
../../.venv/bin/ruff check plugins/workflow/notifications.py plugins/workflow/executors/ai.py tests/plugins/workflow/test_notifications.py tests/plugins/workflow/test_persistent_session_recovery.py
git diff --check
```

Result: **all checks passed / clean diff check**.

Retries were disabled for every test command. Only ordinary functional tests
were run.

## Changed files

- `plugins/workflow/notifications.py`
- `plugins/workflow/executors/ai.py`
- `tests/plugins/workflow/test_notifications.py`
- `tests/plugins/workflow/test_persistent_session_recovery.py`
- `.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/task-13-round-9-closure-report.md`

The owned coordinator test file required no edit; its existing compatibility
assertion now passes.

## Self-review

- The delivery allowlist contains exact codes emitted by the authenticated
  gateway delivery path and receipt status fallbacks. It does not accept
  prefixes, arbitrary bounded text, exception type suffixes, paths, sessions,
  or provider response content.
- Retryable and terminal/dead-letter persistence use the same normalized
  reason. Direct tests cover an accepted stable code and rejected free-form
  input for both paths.
- The recovery-paused result retains only a validated lower-case SHA-256 action
  digest and the stable `approval` kind, which are the fields the resume path
  uses for interaction identity/action grant. Worker payload fields such as
  command, description, history, or provider detail are not copied.
- Accounting projection accepts non-negative integer attempt counts and boolean
  state flags only; arbitrary scalar strings are not admitted.
- The selected-recovery paused behavioral test exercises the real
  `AgentNodeExecutor` shared-to-fresh transition and verifies request order,
  resumability data, exact bounded metadata, recovery outcome, and absence of
  private canaries/fingerprint.
- Ordinary non-recovery paused handling remains on its original return path.
- Test taxonomy was not changed in production. Exact stable messages are
  asserted for malformed authority, sequence damage, prefix commitment damage,
  and the notification reconciliation wrapper.
- The two pre-existing ignored rereview artifacts and all unrelated files were
  preserved.
