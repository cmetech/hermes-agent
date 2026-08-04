# Task 13 Round 9 Final Closure — Functional Specification Rereview

Date: 2026-08-04

## Verdict

**Approved**

The one open Round 9 functional finding is closed. The Desktop failure API's
fixed `projection_failed` fallback now survives the exact-value delivery-reason
normalizer, while arbitrary caller detail and dynamic producer detail continue
to collapse to `notification delivery failed`. The correction is bounded,
compatible with the prior notification behavior, and confined to the finding.

## Scope

- Fix base: `2e7f1f227784a72f68b24a2112af455b2b74af20`
- Reviewed HEAD: `b7cba382c2eaeff370559fdf049a47ed96b6441e`
- Reviewed tree: `8725359b7331beecaeb8c0e9a4afc4141cb2ee41`
- Read the root and Desktop engineering instructions, Task 13 brief, prior
  Round 9 closure rereview, implementer closure report, supplied exact-range
  diff, and the relevant design/plan compatibility, bounded projection, API,
  Desktop, and notification constraints.
- Reviewed all changed production and test files in full and traced the
  Desktop API, notification outbox, coordinator delivery, Gateway receipt, and
  platform-neutral send-error producer paths.
- Accepted the exact-HEAD implementer evidence: isolated RED `15/1`; focused
  GREEN `70/70`; retries disabled; Ruff and diff checks clean.
- No tests were rerun. No threat-model analysis, security testing, or
  adversarial/exploit validation was performed.

## Closure verification

### Desktop API behavior is covered through the real route

`plugins/workflow/dashboard/plugin_api.py:1018-1038` supplies
`projection_failed` when the Electron failure receipt omits `error`.
`plugins/workflow/notifications.py:36-71` now admits that exact fixed code.
`tests/plugins/workflow/test_notification_delivery.py:177-205` leases a real
Desktop notification, posts to `/notifications/{id}/fail` without an error,
asserts the API outcome, and verifies that durable history retains
`projection_failed`. This directly exercises the previously failing call path;
it is not a helper-only or source-shape test.

### Free-form normalization remains intact

The normalizer still accepts only exact string members of the finite allowlist;
every prefix-shaped, suffixed, non-string, or other free-form input uses the
fixed generic reason. The API request itself bounds `error` to 512 characters
at `plugins/workflow/dashboard/plugin_api.py:947-949`.
`tests/plugins/workflow/test_notification_delivery.py:208-237` posts a private
canary through the same API and proves both the generic durable value and the
canary's absence from history. Existing direct outbox tests additionally cover
both retryable and terminal/dead-letter persistence, including the terminal
decision fact.

### Exact producer vocabulary is complete at this boundary

The 18-member allowlist covers every fixed host-owned failure value emitted by
the two producer families in scope:

- Desktop: `projection_failed`.
- Coordinator/Gateway receipt states: `retryable_failure`,
  `permanent_failure`, `outcome_uncertain`, and `unauthorized`.
- Gateway delivery details: `gateway_loop_unavailable`,
  `adapter_unavailable`, `adapter_send_timeout`, `adapter_send_failed`,
  `invalid_text`, and `delivery_store_unavailable`.
- Every platform-neutral `SEND_ERROR_KINDS` member:
  `too_long`, `bad_format`, `forbidden`, `not_found`, `rate_limited`,
  `transient`, and `unknown`.

Successful `delivered` receipts are acknowledged rather than passed to a
failure normalizer. Dynamic exception strings such as
`delivery_exception:<type>` and `adapter_send_exception:<type>`, as well as
adapter/provider human-readable `SendResult.error` text, are deliberately not
members and remain normalized. No prefix or pattern acceptance was introduced.

### Compatibility and boundedness are preserved

The production change only adds fixed, value-free codes to the existing exact
allowlist. It does not change retry/dead-letter transitions, attempts, timing,
lease ownership, decision-fact construction, API shapes, notification payload
projection, legacy/v1/v2 behavior, or Task 13 recovery semantics. Durable
diagnostics remain one of 18 short constants or the fixed generic reason, so no
caller/provider value can expand the persisted field.

### The fixture adjustment preserves its original behavioral contract

The test was introduced by commit `59941829e` to prove that a declared
incompatibility is refused before run persistence. Its assertions and Gateway
admission path are unchanged. The old Archon Bash timeout fixture became
runnable because Phase 3 intentionally implemented that timeout semantic. The
replacement prompt node's `effort: high` requires an advertised
`reasoning_effort` capability; the test's ordinary `assess_compatibility()` call
provides no such capability and therefore produces the blocking
`provider_field_unsupported` finding. `require_runnable()` executes before the
store is opened for admission, before snapshot preparation, and before
`start_run()`, and the test still asserts `workflow_compatibility_blocked`, no
`run.json`, and an empty staging directory. The adjustment therefore restores
the same pre-persistence contract instead of masking a later failure.

### Scope did not expand

The exact range changes three files: eight production allowlist additions, two
focused API behavior tests plus the one-line stale-fixture repair, and the
closure report. There is no new endpoint, Desktop implementation, recovery
semantic, core tool, Phase 4/5 behavior, or unrelated refactor.

## Finding counts

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 0 |
| Minor | 0 |

## Blocking concern

None.
