# Task 13 Fix Round 9 — Independent Code-Quality Rereview

## Verdict

**Needs fixes**

Round 9's journal-order commitment and centralized verified reader are internally coherent in the reviewed diff, but the change introduces one confirmed notification compatibility regression and leaves the newly named paused-recovery path outside its own allowlisting/outcome logic. The new order-validation tests also accept overly broad runtime failures instead of proving the intended deterministic failure contract.

## Scope and evidence

- Fix base: `d6ed3aeb359a920fe806719b09ca9aaac68756bf`
- Reviewed HEAD: `18b0c8d8697a49ea3beb8c006a110c2994ad0e5f`
- Reviewed tree: `a1634e52267f663a25b98c86e18dce788be75a02`
- Review scope: only the supplied Round 9 diff and concrete changed-contract call sites.
- Controller evidence accepted: canonical Task 13 gate, 10 files / 360 passed / 0 failed; non-overlapping siblings, 8 files / 451 passed / 0 failed; retries disabled.
- One narrowly focused functional check was run because the changed notification contract conflicted with an existing named caller/test:
  `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_coordinator.py`
  Result: **38 passed / 1 failed**. The sole failure was `test_gateway_retryable_delivery_receipt_requeues_outbox_row`.
- No threat-model analysis, security test, exploit validation, or broad test rerun was performed.

## Findings

### Important 1 — Global notification error erasure breaks the existing delivery contract

**Evidence:** `plugins/workflow/notifications.py:879-925`, `plugins/workflow/coordinator.py:267-281`, `tests/plugins/workflow/test_coordinator.py:798-826`

`NotificationOutbox.fail()` now ignores its `error` argument and always persists `"notification delivery failed"` at `notifications.py:900-920`. `terminal_fail()` does the same at `notifications.py:848-863`. This applies to every notification, not only recovery-derived data. The gateway coordinator deliberately passes the bounded delivery receipt reason (`receipt.detail or receipt.status`) at `coordinator.py:270-281`; Round 9 discards that reason.

This is a confirmed functional regression. The focused coordinator file failed at `test_coordinator.py:826`: the established `last_error == "delivery_store_unavailable"` contract now returns `"notification delivery failed"`. Operators also lose the reason needed to distinguish retryable delivery failures, and the `error` parameters become misleading dead inputs.

**Required correction:** preserve a bounded, value-free delivery reason for ordinary notification failures (for example, an allowlisted stable reason/code), and apply recovery-specific redaction only where recovery data requires it. Keep the existing coordinator test green and add coverage showing unsafe free-form detail is normalized without erasing known stable receipt reasons.

### Important 2 — Recovery-selected paused results bypass the new sanitization and outcome path

**Evidence:** `plugins/workflow/executors/ai.py:802-847`, `plugins/workflow/executors/ai.py:1304-1312`, `plugins/workflow/executors/ai.py:1416-1418`

Round 9 adds `"paused"` to `with_recovery_failure()` at `ai.py:805-810`, where selected-recovery results are reduced to an allowlist and assigned `fresh_execution_failed`. However, the actual paused result path at `ai.py:1416-1418` returns a new `NodeExecutionResult` directly and never calls that helper. The metadata being returned was populated immediately beforehand with session, provider, model, usage, audit, fingerprint, and warnings at `ai.py:1304-1312`, plus the pending interaction.

Consequently, the new `paused` branch is ineffective for the normal worker-paused path: it neither applies the Round 9 metadata contract nor records a recovery outcome. This leaves recovery state/evidence inconsistent with failed, cancelled, and interrupted siblings and leaves the behavior untested by the new Round 9 cases.

**Required correction:** route recovery-selected paused results through an explicit bounded paused-recovery projection that preserves the sanitized interaction needed to resume while applying the intended recovery outcome/state semantics. Add a behavioral test using a fresh recovery worker that returns `status="paused"` and assert both resumability and the bounded recovery record.

### Minor 1 — The new order-validation helper treats any `RuntimeError` as an acceptable fail-closed result

**Evidence:** `tests/plugins/workflow/test_persistent_session_recovery.py:3522-3533`, `tests/plugins/workflow/test_persistent_session_recovery.py:4257-4280`

`_assert_private_values_absent()` was widened from `JournalRecoveryError` to every `RuntimeError`. The malformed-v3 authority tests and order-damage reader matrix therefore pass for unrelated runtime failures as long as the exception text does not contain the canary. That does not prove the implementation reached the deterministic journal-order validation or returned its fixed failure taxonomy.

**Required correction:** catch only the expected domain errors for each reader and assert the stable error type/message (or explicitly assert a successful sanitized projection). Do not let arbitrary `RuntimeError` failures satisfy order-validation tests.

## Finding counts

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 2 |
| Minor | 1 |

## Final assessment

Task quality is **Needs fixes**. The notification regression is independently reproduced at the exact reviewed HEAD, and the paused recovery branch is not actually covered by the sanitizer to which Round 9 added it. Both Important findings should be corrected before approval; the test helper should be narrowed in the same fix round so the deterministic order contract is genuinely exercised.
