# Task 13 Round 9 Follow-up — Final Code-Quality Rereview

Date: 2026-08-04

## Verdict

**Approved**

The scoped follow-up closes the remaining `projection_failed` compatibility
gap. The fix diff introduces no new Critical, Important, or Minor quality
finding.

## Scope and evidence

- Fix base: `2e7f1f227784a72f68b24a2112af455b2b74af20`
- Reviewed HEAD: `b7cba382c2eaeff370559fdf049a47ed96b6441e`
- Reviewed tree: `8725359b7331beecaeb8c0e9a4afc4141cb2ee41`
- Scope: only `review-2e7f1f227..b7cba382c.diff` and the directly relevant
  notification producers, normalization/persistence paths, Desktop API route,
  compatibility gate, and changed test fixture.
- Exact-HEAD implementer evidence accepted: isolated RED **15 passed / 1
  failed**; focused GREEN **70 passed / 0 failed**; retries disabled; Ruff and
  diff checks clean.
- No tests were rerun. No threat-model analysis, security testing,
  adversarial validation, or exploit validation was performed.

## Closure assessment

### Exact stable-code vocabulary closes the projection gap

`plugins/workflow/notifications.py:35-71` now recognizes
`projection_failed` through the same exact-value normalizer used by retry and
terminal persistence. The Desktop endpoint supplies precisely that fixed
fallback when its optional error is empty
(`plugins/workflow/dashboard/plugin_api.py:1017-1038`), so the existing
ordinary API path again retains its host-owned diagnostic.

The producer audit is complete for this boundary. The vocabulary contains:

- the four non-delivered `DeliveryReceipt` statuses that can reach failure
  persistence through a status fallback;
- the fixed gateway and delivery-store details
  (`gateway_loop_unavailable`, `adapter_unavailable`,
  `adapter_send_timeout`, `adapter_send_failed`, `invalid_text`, and
  `delivery_store_unavailable`);
- the seven platform-neutral `SEND_ERROR_KINDS` values; and
- the Desktop fallback `projection_failed`.

Acceptance remains `isinstance(error, str) and error in <frozenset>`. There is
no prefix, substring, regex, length-only, or shape-based admission. Dynamic
details such as `delivery_exception:<type>`,
`adapter_send_exception:<type>`, adapter/provider messages, paths, histories,
and arbitrary Desktop text still become the fixed `notification delivery
failed` value. Both `fail()` and `terminal_fail()` continue to share the same
normalizer, so retry, dead-letter, and durable decision-fact behavior remain
compatible.

The implementation uses local literal codes and adds no import from Gateway
adapter modules. It therefore creates no new dependency edge or import-cycle
risk. The change is additive at the normalization boundary and does not alter
receipt classification, retry scheduling, terminal selection, lease ownership,
or API compatibility.

### API tests exercise the real producer boundary

`tests/plugins/workflow/test_notification_delivery.py:177-237` calls the real
`/notifications/{id}/fail` route after leasing a real outbox row. One test
omits `error` and proves the persisted value is `projection_failed`; the other
supplies a free-form canary and proves the persisted value is generic and the
canary is absent from history. These are behavioral API/persistence assertions,
not source snapshots or change-detector tests, and directly cover both sides of
the reported gap.

### The stale fixture adjustment preserves its original contract

The owned adjustment at
`tests/plugins/workflow/test_notification_delivery.py:694-739` replaces a
formerly unsupported bash timeout with a prompt `effort` request that the
gateway's capability-less compatibility assessment deterministically marks
unsupported. It does not weaken or bypass the production gate.

The original test's load-bearing assertions are unchanged: the Gateway command
must return `workflow_compatibility_blocked`, no `run.json` may exist, and the
staging directory must remain empty. `gateway_command._start_gateway_run()`
still invokes `require_runnable()` before trust, store creation, snapshot
preparation, or admission. The fixture therefore continues to test the same
pre-persistence compatibility-block contract and is not test manipulation; it
only replaces a language feature that became runnable with one that remains a
valid blocking input at this call site.

## Findings

No new Critical, Important, or Minor findings.

## Finding counts

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 0 |
| Minor | 0 |

## Blocking concern

None.
