# Task 13 Round 9 Closure — Functional Specification Rereview

Date: 2026-08-04

## Verdict

**Needs fixes**

The selected-recovery pause correction and the exact journal-domain assertions
preserve the Task 13 contract, but the notification failure-reason allowlist
still omits a stable reason emitted by an existing ordinary API path. The
global normalizer therefore continues to erase a known value-free diagnostic
outside persistent-session recovery.

## Scope

- Fix base: `18b0c8d8697a49ea3beb8c006a110c2994ad0e5f`
- Reviewed HEAD: `2e7f1f227784a72f68b24a2112af455b2b74af20`
- Reviewed tree: `8e2a3a714e5a158b17253b75b0fb7957c443bc86`
- Reviewed the supplied diff and the relevant Task 13 production, scheduler,
  store, worker, API, and notification call paths against Task 13 and design
  sections 7–9.
- Accepted the exact-HEAD controller/implementer evidence: coordinator RED
  `38/1`; test-first RED `152/2`; focused GREEN `193/193`; expanded GREEN
  `415/415`; retries disabled; Ruff and diff checks clean.
- No tests were rerun. No threat-model analysis, security testing, or
  adversarial/exploit validation was performed.

## Findings

### Important 1 — The allowlist still erases the Desktop projection failure code

**Evidence:** `plugins/workflow/notifications.py:35-63`,
`plugins/workflow/dashboard/plugin_api.py:1021-1033`,
`plugins/workflow/notifications.py:901-936`

The correction preserves only members of `_STABLE_DELIVERY_FAILURE_REASONS`.
The existing Desktop notification failure endpoint supplies the fixed,
host-owned fallback `projection_failed` when a receipt has no error. That code
is not in the allowlist, so `NotificationOutbox.fail()` changes it to
`notification delivery failed` before writing `last_error` (and before a later
terminal decision fact can retain it).

This is the same ordinary-notification compatibility class that Round 9 was
meant to close: before the global redaction change, the stable
`projection_failed` reason was retained. It is already bounded and value-free,
so erasing it does not advance the closure's normalization goal and prevents an
operator from distinguishing a Desktop projection failure from an unknown
free-form failure. The new tests cover one accepted gateway code and one
arbitrary string, but do not exercise this existing named caller.

**Required correction:** include the fixed Desktop fallback in the centralized
stable-reason vocabulary (or make producers consume one shared stable-code
vocabulary), and add a behavioral test through `fail_notification()` proving
that `projection_failed` is retained while caller-supplied free-form detail is
still normalized. Audit the platform-neutral stable delivery error kinds at the
same boundary so host-owned codes do not silently regress for the same reason.

## Contract checks that passed

- **Selected-recovery paused resumability:** the branch is gated by
  `recovery_selected`, retains only non-negative integer attempt accounting,
  boolean accounting flags, and a validated `approval`/lowercase SHA-256 action
  digest, and records `fresh_execution_failed`. The normal worker constructs
  that digest deterministically, the store recognizes it as interaction
  identity, and approval consumption passes it back as the next worker's
  `approved_action_digest`. Ordinary non-recovery pauses remain on their prior
  path.
- **Bounded recovery metadata:** session ID, fingerprint, provider, model,
  usage, audit payload, warnings, output, and provider-controlled interaction
  fields do not survive the selected-recovery paused projection.
- **Exact journal-domain assertions:** malformed/order-damaged store and
  evidence readers now assert exact `JournalRecoveryError` messages;
  notification reconciliation asserts the exact
  `NotificationReconciliationError` wrapper. Arbitrary `RuntimeError` no longer
  satisfies these cases.
- **Task 13 semantics:** the correction does not alter same-run versus
  cross-run classification, pre-provider selection, private CAS obligation,
  reconciliation ordering, outcome vocabulary, or legacy/v1/v2 behavior.
- **Scope:** production edits are limited to the Task 13 closure paths. No API
  or Desktop projection implementation (Task 14), and no Phase 4/5 capability,
  was added.

## Finding counts

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 1 |
| Minor | 0 |

## Blocking concern

The ordinary Desktop notification path still loses its stable,
value-free `projection_failed` reason. This should be corrected before Task 13
Round 9 closure is approved.
