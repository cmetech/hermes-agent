# Task 13 Round 9 Closure Correction — Independent Code-Quality Rereview

Date: 2026-08-04

## Verdict

**Approved**

All three findings from `task-13-quality-rereview-9.md` are closed. The scoped
fix diff introduces no new Critical, Important, or Minor code-quality finding.

## Scope and evidence

- Fix base: `18b0c8d8697a49ea3beb8c006a110c2994ad0e5f`
- Reviewed HEAD: `2e7f1f227784a72f68b24a2112af455b2b74af20`
- Reviewed tree: `8e2a3a714e5a158b17253b75b0fb7957c443bc86`
- Scope: the supplied closure diff and only the concrete compatibility,
  persistence, and resume call paths needed to judge that diff.
- Controller/implementer evidence accepted at exact HEAD: coordinator RED
  **38/1**; test-first RED **152/2**; focused GREEN **193/193**; expanded GREEN
  **415/415**; retries disabled; Ruff and diff checks clean.
- No tests were rerun for this rereview. No threat-model analysis, security
  testing, adversarial validation, exploit validation, or broad regression run
  was performed.

## Prior finding verdicts

### 1. Global notification error erasure broke established delivery reason compatibility

**Closed.**

`plugins/workflow/notifications.py:35-63` now normalizes through an exact stable
reason allowlist. Both retryable failure and terminal/dead-letter persistence
use the same normalized value at `plugins/workflow/notifications.py:843-948`.
This restores the established `delivery_store_unavailable` coordinator receipt
while converting unknown or free-form detail to the fixed
`notification delivery failed` diagnostic. The accepted values match the
bounded delivery statuses and concrete gateway delivery details emitted by the
existing host paths; exception-shaped and adapter-controlled text is not
accepted by prefix or substring.

The tests at `tests/plugins/workflow/test_notifications.py:149-260` exercise
both `fail()` and `terminal_fail()` for the preserved compatibility reason and
for rejected free-form detail, including the durable dead-letter fact. The
pre-existing coordinator compatibility assertion is green in the supplied
evidence.

### 2. Recovery-selected paused results bypassed bounded projection/outcome while needing resumability

**Closed.**

`plugins/workflow/executors/ai.py:802-842` defines narrow projections for exact
non-negative attempt accounting, boolean state flags, and a lowercase SHA-256
approval digest. The selected-recovery paused branch at
`plugins/workflow/executors/ai.py:1439-1450` now uses those projections and
records `fresh_execution_failed`; it no longer returns the worker-derived
session, provider, model, usage, audit, fingerprint, warnings, output, or other
unbounded metadata. Removing `paused` from the ordinary recovery-failure helper
also makes the dedicated path unambiguous.

The retained `{kind: approval, action_digest: ...}` shape is the exact bounded
identity consumed by the existing store approval and action-grant flow
(`plugins/workflow/store.py:14607-14612`, `14737-14843`). Existing behavioral
coverage proves that this digest is accepted, durably converted to a one-shot
grant, and supplied to the next worker request. The new recovery-specific test
at `tests/plugins/workflow/test_persistent_session_recovery.py:543-636` proves
the shared-to-fresh selection, exact bounded result, outcome, request order,
and absence of the private canaries. The ordinary non-recovery paused branch is
unchanged.

### 3. Order-validation helper accepted arbitrary RuntimeError

**Closed.**

`tests/plugins/workflow/test_persistent_session_recovery.py:3622-3634` now
accepts only `JournalRecoveryError` in the shared projection helper. The
order-damage cases assert the exact journal domain error and stable diagnostic,
including sequence gaps, malformed authority, invalid predecessor commitment,
and rewritten prefix. Notification reconciliation is asserted separately as
the exact `NotificationReconciliationError("journal could not be safely corroborated")`
projection at `tests/plugins/workflow/test_persistent_session_recovery.py:4492-4510`.
An unrelated `RuntimeError` can no longer satisfy these tests.

## New findings in the closure diff

No new Critical, Important, or Minor findings.

The notification helper is centralized and exact-match only; the two write
paths remain consistent. The paused-recovery projection is small, locally
named, and shares its accounting rules with selected-recovery failures. The
new tests are behavioral and contract-oriented rather than source snapshots.

## Finding counts

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 0 |
| Minor | 0 |

## Blocking concern

None.
