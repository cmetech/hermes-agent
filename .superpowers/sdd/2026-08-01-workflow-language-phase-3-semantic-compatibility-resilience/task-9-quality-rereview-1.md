# Phase 3 Task 9 Quality Rereview 1

**Reviewed candidate:** `7d4aa28465a1f766930efcda2bcaa6bb1ceaac9d`

**Reviewed tree:** `b8f8fad9d880097474d67edfd6e344355fa2ab55`

**Task baseline:** `e89c5dce4`

**Verdict:** CHANGES REQUIRED

**Findings:** 0 Critical, 1 Important, 0 Minor

## Scope reviewed

I read the complete approved Phase 3 design and implementation plan, both
original Task 9 review reports, the full implementation and closure-fix diffs,
and the real isolated-agent provider loop. I inspected ordinary and structured
total-call conversion, exact/conservative provenance, repair and fallback
paths, fatal/outward classification, cleanup-failure journal authority,
restart and ownership behavior, both scheduler entrypoints, retry-wake
fencing, cancellation/shutdown boundaries, legacy isolation, and Task 10
scope. I made no production or test edits.

The closure fix correctly converts ordinary worker total calls exactly once,
retains conservative provenance through durable storage, gives fatal classes
precedence over outward uncertainty, makes pre-provider execution-integrity
failure an exact zero-provider result, and journals cleanup-failure charge and
metadata with the ownership block. One execution-level budget defect remains.

## Important finding

### I1. Fallback and primary-recovery cycles can exceed the sealed provider grant

Task 9 requires every provider call, including fallback calls, to draw from one
sealed grant and requires actual total calls never to exceed
`effective_total_attempts`. The workflow boundary passes the remaining grant as
`PluginAgentRunRequest.max_api_attempts` and the worker assigns it to
`AIAgent._api_max_retries` (`plugins/workflow/executors/ai.py:979` and
`agent/plugin_agent_worker.py:1530`). The worker's provider wrapper only counts
calls for later audit; it does not reject a call after the request-wide grant
has been consumed (`agent/plugin_agent_worker.py:1531-1548`).

Inside the real conversation loop, `max_retries` bounds only the current retry
cycle. Successful fallback activation resets `retry_count` to zero, including
after the current provider exhausts its cycle
(`agent/conversation_loop.py:4509-4535`), and primary transport recovery also
resets the counter (`agent/conversation_loop.py:4509-4525`). Other fallback
sites perform the same reset. Therefore a sealed grant of three can issue
three calls on the primary provider and then up to three more on the fallback;
a transport-recovery cycle can similarly receive a fresh allowance. The final
worker audit may truthfully report six calls, but
`validated_provider_total_call_count()` rejects a total above the grant and the
scheduler conservatively persists only the sealed charge. That keeps the
journal numerically bounded while masking the fact that the provider side
already exceeded the authority.

The new fallback tests do not execute this path. They substitute a fake
`agent_runner` that performs one `run()` call and hand-author an audit total of
one or two (`tests/plugins/workflow/test_retry.py:453-507` and `:589-636`), so
they prove conversion/provenance but not enforcement by the real isolated
host loop.

Required correction: enforce one request-wide provider-call budget at the
isolated worker boundary (or an equivalently authoritative seam) so fallback,
credential/transport recovery, and every internal retry cycle share the same
remaining total. Add a real worker/host-loop regression that exhausts or
partially consumes the primary cycle, activates fallback/recovery, and proves
the provider-call count never exceeds `max_api_attempts`; then carry the exact
bounded total through executor, scheduler, and store.

## Closed original findings

- Ordinary v3 text results now convert total worker calls to additional calls
  once, while legacy continues through its existing retry-count unit.
- Missing, invalid, exception, fallback-without-count, and structured-repair
  evidence consume the grant conservatively and persist
  `provider_attempts_exact: false`.
- Cancellation remains first, the closed fatal set precedes outward
  uncertainty, and outward transient/unknown outcomes retain safe
  reconciliation behavior.
- `cleanup_failed` now records the combined charge and sanitized attempt
  metadata in the same authoritative journal transition that installs the
  cleanup ownership block; restart does not replay the node.
- `advance()` and `advance_all()` converge through `_execute_claim()` and
  `_persist_result()`, and no Task 10 descriptor surface appears in the diff.

## Verification evidence

All Python tests were run only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Exact Task 9 gate: `test_phase3_execution_semantics.py`, `test_retry.py`,
   `test_provider_failures.py`, `test_ai_executor.py`,
   `test_parallel_scheduler.py`, `test_coordinator_multiprocess.py`, and
   `test_shutdown_recovery.py` — **7 files, 255 tests passed, 0 failed, no
   retries**.
2. Adjacent persistence/recovery gate: `test_store.py`,
   `test_crash_recovery.py`, and `test_cancel_node.py` — **3 files, 58 tests
   passed, 0 failed, no retries**.
3. Ruff on all six closure-fix production/test files — **PASS**.
4. `git diff --check e89c5dce4..7d4aa2846` — **PASS**.

The reviewed production tree was clean before retained review reports were
written. This rereview added only this report; another independent reviewer
was concurrently writing the specification rereview report.

## Final assessment

The closure fix is strong on durable accounting and crash-safe ownership, but
Task 9 cannot close while the real provider loop can spend a fresh allowance
after fallback or primary recovery. The ledger must constrain actual calls,
not merely cap the charge recorded after an overrun.
