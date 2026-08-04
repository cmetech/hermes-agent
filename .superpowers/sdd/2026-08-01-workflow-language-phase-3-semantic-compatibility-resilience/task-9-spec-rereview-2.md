# Phase 3 Task 9 Specification Rereview 2

**Verdict:** CHANGES REQUIRED

**Reviewed HEAD:** `849b0036334c732eb14fe4dd44a5b7824b8b4c2f`

**Reviewed tree:** `36d9f5906f8ccd7ec3e4df3f20a1678f926b4575`

**Fix baseline:** `7023b7f2849c8c4c77da4d81d78299925d20f094`

**Severity counts:** 0 Critical, 3 Important, 0 Minor

## Scope reviewed

I read the approved Phase 3 retry design, the complete Task 9 plan, all four
prior Task 9 specification and quality reports, and the full closure-fix diff.
I traced the new worker-local reservation through both provider entry methods,
the real recovery and fallback loop, structured repair residual grants, the
executor's total-to-additional conversion, scheduler failure classification,
durable ledger charge, cancellation, legacy isolation, prompt/tool behavior,
and Task 10 scope. I made no production or test edits.

The fix does prevent the tested chat-completions primary/recovery/fallback
composition from launching a real provider call beyond grants one through
five. The prior provenance, conversion, fatal/outward, cleanup, accounting,
and concurrency findings remain closed. Three integration gaps prevent Task 9
from closing.

## Findings

### Important 1 — The sealed absolute grant is enforced for legacy and ordinary plugin-agent requests too

Phase 3 must preserve exact unversioned and `hermes-legacy` behavior, and the
Task 9 contract specifically retains the legacy fallback path. The new guard
is installed unconditionally for every `PluginAgentRunRequest` in
`agent/plugin_agent_worker.py:1537-1572`. The request protocol carries no v3
sealed-ledger identity, and `AgentNodeExecutor` supplies the same
`max_api_attempts` field for both strict v3 and legacy executions at
`plugins/workflow/executors/ai.py:939-979`.

Before this fix, `max_api_attempts` continued to drive the core's per-cycle
retry counter while recovery and fallback retained their established reset
behavior. The retained core regression
`tests/run_agent/test_32646_fallback_429_after_timeout.py:222-288` deliberately
performs four provider calls across recovery and fallback with
`_api_max_retries = 2`. Under the worker's new unconditional guard, the same
legacy/non-v3 request is stopped at two calls. A default request with a grant
of one cannot reach its existing fallback at all.

The new tests instantiate bare `PluginAgentRunRequest` objects and treat all
of them as sealed v3 requests (`tests/agent/test_plugin_agent.py:181-188`), so
they do not prove legacy isolation. The absolute reservation needs an explicit
sealed-v3 request identity (or an equivalently authenticated boundary) and
must retain observation-only accounting plus existing fallback/recovery
semantics for legacy and other plugin-agent callers. Add a paired v3/legacy
real-loop regression.

### Important 2 — Delegating provider entry methods reserve twice for one API try

The worker independently wraps `_interruptible_streaming_api_call` and
`_interruptible_api_call`, and each wrapper reserves and increments before
calling its captured method (`agent/plugin_agent_worker.py:1542-1572`). These
entry methods are not disjoint. The real streaming implementation delegates
to `agent._interruptible_api_call()` for direct routing and, importantly, for
every `codex_responses` request
(`agent/chat_completion_helpers.py:2273-2289`). Because that attribute is the
second wrapped method, one provider API try reserves twice.

With a sealed grant of one, the outer streaming wrapper consumes the only
reservation and the inner non-streaming wrapper raises
`provider_attempt_grant_exhausted` before any provider call. With a larger
grant, the worker reports two provider attempts for one actual call and can
prematurely exhaust the ledger. That violates both exact call evidence and the
requirement that grants one through five authorize that many real provider
tries.

The new real-loop helper fixes `api_mode` to `chat_completions`, replaces only
`AIAgent._interruptible_api_call`, and uses a mock client that selects the
non-streaming branch (`tests/agent/test_plugin_agent.py:59-188`). It never
enters the streaming-to-non-streaming delegation path. Make the reservation
reentrancy-safe or place it at a single transport-launch seam, then add real
Codex/delegation rows proving one reservation and one exact audit count per
provider try.

### Important 3 — Real grant exhaustion becomes unknown-outcome reconciliation

The guard publishes `failure_kind = provider_attempt_grant_exhausted`
(`agent/plugin_agent_worker.py:1693-1696`). `AgentNodeExecutor` has no mapping
for that stable condition, so it converts it to `error_code = agent_failed`
(`plugins/workflow/executors/ai.py:1188-1214`) while retaining the exact full
provider count.

At the scheduler, v3 assigns `known_no_effect = False` unless metadata
explicitly says true (`plugins/workflow/scheduler.py:3489-3498`).
`classify_failure()` checks that false/unknown condition before the exhausted
budget equation (`plugins/workflow/scheduler.py:318-325`), so the real guard
result becomes `UNKNOWN_OUTCOME` and the node is paused for reconciliation.
The denied `grant + 1` launch itself had no effect and the durable ledger is
already fully consumed; Task 9 requires a stable bounded exhaustion outcome,
not an operator reconciliation prompt for an attempt that was never launched.

The worker tests stop at the raw result and a standalone ledger charge
(`tests/agent/test_plugin_agent.py:1371-1416`). They do not run this new failure
kind through `AgentNodeExecutor -> RunScheduler -> RunStore`. Preserve the
causal retry/failure classification or explicitly map sealed exhaustion to a
known, terminal bounded outcome, and add the full boundary test asserting no
retry, no reconciliation, exact one-time charge, and stable restart state.

## Verified closures and invariants

- The worker's locked prelaunch check caps actual calls in the tested
  chat-completions primary recovery and fallback cycles for grants 1 through 5.
- Structured repair still receives only the residual grant calculated by the
  executor and the tested worker cannot exceed that request-local residual.
- Cancellation already set before worker launch produces zero provider calls.
- Ordinary and structured exact totals are converted to additional calls once;
  missing/invalid evidence remains conservative and explicitly inexact.
- Fatal failures precede outward uncertainty; cleanup failure retains its
  atomic charge and ownership block; retry wake/claim fencing remains intact.
- Prompt bytes, system-prefix stability, tool visibility, and audit redaction
  are unchanged in the exercised path.
- The Task 9 diff contains no Task 10 descriptor-inheritance implementation.

## Verification evidence

All Python tests were run only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Exact Task 9 gate plus the new worker tests:
   `test_phase3_execution_semantics.py`, `test_retry.py`,
   `test_provider_failures.py`, `test_ai_executor.py`,
   `test_parallel_scheduler.py`, `test_coordinator_multiprocess.py`,
   `test_shutdown_recovery.py`, and `test_plugin_agent.py` — **8 files,
   331 tests passed, 0 failed, no retries**.
2. Core recovery/fallback regression:
   `test_32646_fallback_429_after_timeout.py` — **1 file, 5 tests passed, 0
   failed, no retries**. Its four-call/two-cycle assertion is retained legacy
   behavior and demonstrates why the worker guard must be v3-gated.
3. `ruff check` over all eleven Task 9 changed production/test files —
   **PASS**.
4. `git diff --check e89c5dce4..849b00363` — **PASS**.

The worktree was clean before this retained report was written. This review
modified only this report.

## Final assessment

The closure fix solves the originally reported fallback multiplication for
the tested chat-completions path, but it applies the new semantics outside v3,
double-reserves on real nested transport routing, and sends its own exhaustion
signal to reconciliation. Gate the absolute grant to sealed v3 execution,
make reservation count real provider tries exactly once across both entry
methods, and prove the resulting exhaustion through the durable scheduler
boundary before the next closure rereview.
