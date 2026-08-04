# Phase 3 Task 9 Quality Rereview 2

**Reviewed candidate:** `849b0036334c732eb14fe4dd44a5b7824b8b4c2f`

**Reviewed tree:** `36d9f5906f8ccd7ec3e4df3f20a1678f926b4575`

**Task baseline:** `e89c5dce4`

**Verdict:** CHANGES REQUIRED

**Findings:** 0 Critical, 3 Important, 0 Minor

## Scope reviewed

I read the complete approved Phase 3 design and implementation plan, all four
prior Task 9 review reports, the full cumulative Task 9 production/test diff,
and the complete second-fix diff. I traced the sealed retry grant from
admission through both scheduler entrypoints, executor construction, isolated
worker launch, primary recovery, fallback, structured repair, durable charge,
retry wake, cancellation, cleanup blocking, and restart. I also inspected the
real streaming and non-streaming transport dispatchers rather than treating
the two wrapped worker methods as synonymous with provider calls.

The second fix adds a locked request-wide reservation and correctly prevents
the tested Chat Completions recovery/fallback loop from launching call
`grant + 1`. The reservation is atomic across independent callers, the
original provider method runs outside the lock, pre-existing cancellation is
checked before reservation, and the ordinary tested audit remains bounded.
However, the guard is not gated to v3, and its method-entry unit is neither
one-to-one nor onto the actual provider transports. The resulting exhaustion
marker also loses the known no-effect/exhausted classification at the workflow
boundary.

## Important findings

### I1. The request-wide cap changes legacy and generic plugin-agent behavior

Phase 3 requires exact unversioned and `hermes-legacy` behavior. The approved
fix requirement was to enforce one absolute provider-call budget for sealed v3
execution without changing the core's existing retry/fallback semantics for
other callers.

The new reservation is installed unconditionally for every
`PluginAgentRunRequest` at `agent/plugin_agent_worker.py:1537-1572`. The
request has no v3/sealed-total marker (`agent/plugin_agent.py:73-107`), and the
legacy workflow AI path constructs the same request with
`max_api_attempts=granted_provider_attempts` at
`plugins/workflow/executors/ai.py:939-979`. Direct generic and inline-agent
requests use the same worker and default field as well.

Before this fix, `max_api_attempts` set the existing AIAgent per-cycle retry
limit; primary recovery and fallback could intentionally reset that cycle.
The new unconditional wrapper turns it into one absolute request total and
raises before a later cycle can use the historical allowance. Thus a legacy
workflow or generic plugin-agent request with fallback now performs fewer
calls and can fail where the reviewed baseline continued. The new real-loop
tests call `PluginAgentRunRequest` directly and assert the new cap without a
v3 marker, so they encode the compatibility regression rather than proving
isolation.

Required correction: make the absolute sealed-total contract explicit and
opt-in from Archon normalizer-v3 execution (including its residual structured
repair requests). Leave existing `max_api_attempts` semantics exact for legacy
and generic callers. Add the same real recovery/fallback composition under v3
and legacy, proving only v3 receives the absolute ceiling.

### I2. Wrapping both interruptible entrypoints does not count actual provider launches

The fix reserves at entry to both
`_interruptible_streaming_api_call` and `_interruptible_api_call`
(`agent/plugin_agent_worker.py:1542-1572`). Those are orchestration methods,
not disjoint provider-launch primitives:

- Codex Responses enters the streaming method, which delegates to the
  non-streaming method at `agent/chat_completion_helpers.py:2282-2291`.
  Both wrappers reserve. With a grant of one, the outer wrapper consumes the
  only slot and the inner wrapper raises before any Codex provider request;
  larger grants count each real Codex request twice and prematurely exhaust
  the ledger.
- Bedrock Converse can execute `client.converse_stream()` and, when streaming
  permission is denied, execute `client.converse()` inside the same outer
  method call (`agent/chat_completion_helpers.py:2338-2364`). That performs
  two provider calls under one reservation. At the ceiling it can exceed the
  sealed authority, and the worker then publishes the undercount as exact.

The new helper forces `api_mode: chat_completions`, replaces only
`AIAgent._interruptible_api_call`, and installs a mocked OpenAI client
(`tests/agent/test_plugin_agent.py:59-188`). That makes the conversation choose
the non-streaming method directly, so none of the new tests executes either
nested delegation or a multi-transport entrypoint. The adjacent Codex and
Bedrock tests pass because they do not run through the worker guard.

Required correction: reserve at a seam that is exactly one per external
provider launch, or thread one idempotent reservation token through nested
entrypoint delegation while separately reserving every inline transport
fallback. Add worker-integrated Chat Completions, Codex Responses, Bedrock
stream-to-nonstream fallback, and structured residual tests. Assert actual
transport calls, audit totals, and durable charges agree exactly and never
exceed the sealed v3 grant.

### I3. Grant exhaustion is persisted as an unknown outcome instead of an exhausted known-no-effect failure

When the guard blocks call `grant + 1`, it adds the private
`provider_attempt_grant_exhausted` audit marker
(`agent/plugin_agent_worker.py:1693-1696`). `AgentNodeExecutor` does not map
that marker to a stable transient/exhausted code or set `known_no_effect`; it
falls through to `agent_failed` at
`plugins/workflow/executors/ai.py:1189-1227`.

At the scheduler boundary, v3 derives `known_no_effect` as false unless the
executor explicitly set it. `classify_failure()` checks unknown-effect state
before budget exhaustion for non-transient codes
(`plugins/workflow/scheduler.py:298-337`). Therefore the exact, fully charged
attempt pauses for reconciliation as `UNKNOWN_OUTCOME` instead of terminating
as exhausted. The blocked extra call itself performed no effect, and the
guard knows the exact bounded count, so reconciliation is both misleading and
can hold a run/lane indefinitely.

The new tests stop at the worker result and a separately constructed
`RetryLedgerGrant` (`tests/agent/test_plugin_agent.py:1371-1455`); they never
pass the exhaustion result through `AgentNodeExecutor`, scheduler, and store.

Required correction: carry a stable internal exhaustion identity through the
executor as exact, known-no-effect capacity exhaustion (without exposing a new
public raw-provider shape), then prove the real worker-to-store path becomes a
terminal exhausted failure with one durable charge and no retry,
reconciliation prompt, or provider replay.

## Earlier finding disposition

- **Ordinary text total-call conversion:** closed for transports whose worker
  count is one-to-one; total calls are converted to additional calls exactly
  once at the v3 executor boundary.
- **Conservative provenance:** closed for the reviewed runner exception,
  missing/invalid audit, and structured-repair paths; inexact evidence consumes
  the grant and remains `provider_attempts_exact: false`.
- **Fatal/outward ordering and execution-integrity classification:** closed;
  cancellation remains first, fatal precedes outward uncertainty, and the real
  pre-provider entitlement failure is terminal with zero provider calls.
- **Cleanup-failure durable charge:** closed; charge and sanitized metadata are
  journaled with the cleanup ownership block, and restart does not replay.
- **Primary recovery/fallback multiplication:** closed only for the tested
  non-streaming Chat Completions path. Findings I1-I3 prevent general closure.
- **Retry wake, concurrency, and cancellation:** the durable wake/claim CAS,
  both scheduler entrypoints, locked reservation increment, and pre-set
  cancellation tests remain sound. No deadlock was found in the new lock
  scope because the external call occurs after releasing the lock.
- **Scope invariants:** no prompt bytes, system prompt, tool schema, historical
  messages, API projection, or Task 10 descriptor surface changed. The
  unconditional legacy execution change in I1 is the remaining scope breach.

## Verification evidence

All Python tests were run only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Exact Task 9 gate plus the worker regression file:
   `test_phase3_execution_semantics.py`, `test_retry.py`,
   `test_provider_failures.py`, `test_ai_executor.py`,
   `test_parallel_scheduler.py`, `test_coordinator_multiprocess.py`,
   `test_shutdown_recovery.py`, and `test_plugin_agent.py` — **8 files,
   331 tests passed, 0 failed, no retries**.
2. Adjacent store/crash/cancellation and real transport regressions:
   `test_store.py`, `test_crash_recovery.py`, `test_cancel_node.py`,
   `test_32646_fallback_429_after_timeout.py`,
   `test_cron_inline_api_call_62151.py`,
   `test_bedrock_interrupt_post_worker.py`, and
   `test_codex_ttfb_watchdog.py` — **7 files, 92 tests passed, 0 failed, no
   retries**. These transport tests do not compose their paths with the worker
   wrapper and therefore do not close I2.
3. Ruff over all eleven cumulative Task 9 production/test files — **PASS**.
4. `git diff --check e89c5dce4..849b0036` and the fix-only diff check —
   **PASS**.
5. The pinned HEAD/tree and clean worktree were verified before writing this
   report. This review changed no production or test file.

## Final assessment

The common ledger, persistence, provenance, classification, retry wake, and
tested Chat Completions grant enforcement are strong. Task 9 still cannot
close because the enforcement is not v3-gated, does not count all supported
transports in the required unit, and routes its own exact exhaustion into
unknown-outcome reconciliation. Close those three integration contracts with
real worker-to-store coverage before the final quality rereview.
