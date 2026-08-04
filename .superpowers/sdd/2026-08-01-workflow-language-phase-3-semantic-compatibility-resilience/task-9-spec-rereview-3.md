# Phase 3 Task 9 Specification Rereview 3

**Verdict:** CHANGES REQUIRED

**Reviewed HEAD:** `9a9274e67ace9cf022ab188443d38097286e1bb0`

**Reviewed tree:** `757e57d25db12a2488ee5fbe99d813591cf28853`

**Fix baseline:** `849b0036334c732eb14fe4dd44a5b7824b8b4c2f`

**Severity counts:** 0 Critical, 1 Important, 0 Minor

## Scope reviewed

I read the approved Phase 3 design, the complete Task 9 plan, all six prior
Task 9 specification and quality reports, the full cumulative Task 9 diff, and
the complete `849b00363..9a9274e67` third-fix diff. I traced the sealed retry
authority from v3 executor construction through the isolated request wire,
worker reservation, every in-scope provider transport launch, exact audit
conversion, durable scheduler charge, failure classification, retry wake,
cleanup blocking, cancellation, and restart. I also rechecked the legacy
branch, structured repair residual grant, prompt/tool invariants, and Task 10
scope. I made no production or test edits.

The third fix closes all findings from the original review and both prior
rereview rounds. One additional nested-provider accounting gap prevents Task
9 from closing.

## Finding

### Important 1 — A strict-v3 `workflow_agent` child receives a fresh unsealed grant and its provider calls never charge the parent ledger

Task 9 defines a provider attempt as every model/provider API try inside the AI
executor and requires one non-multiplying sealed ceiling across the workflow
attempt. Archon command/prompt nodes can admit `agents`; the executor passes
those definitions into the sealed parent request and exposes the synchronous
`workflow_agent` tool (`plugins/workflow/executors/ai.py:657-697,905-915,963`).

The worker's inline dispatcher constructs a new `PluginAgentRunRequest` for
each child at `agent/plugin_agent_worker.py:1030-1075`. It copies the parent's
full `max_api_attempts`, but it neither sets
`sealed_provider_attempt_grant=True` nor shares the parent's reservation
counter. Because the new field defaults false, the child worker takes the
legacy observation branch and may use recovery/fallback cycles beyond even
that copied per-cycle value. More fundamentally, the child runs in a separate
worker and its observed provider attempts are never added to the parent's
`provider_attempt_counter`; `_build_inline_agent_handler()` returns only the
sanitized child response/status (`agent/plugin_agent_worker.py:1076-1094`).

Consequently, a sealed parent with grant three can consume one parent provider
call that invokes `workflow_agent`, let the child execute provider calls under
a fresh unsealed allowance, and then consume another parent continuation. The
parent publishes only its two direct reservations as exact. Actual provider
calls can exceed the sealed grant while the durable ledger undercharges them.
Merely copying the boolean marker would still create two independent ceilings;
the child must receive only a safely shared/reserved remainder and its exact or
conservative consumption must charge the same parent request-wide authority.

This is an existing concrete inline-agent consumer, so closing it does not
justify a speculative general hook or Task 10 descriptor implementation. Add
a strict-v3 parent-to-real-child composition for grants one through five,
fallback/exhaustion/cancellation, and exact/inexact child evidence, proving the
combined parent plus child transports never exceed the one sealed total.

## Closure evidence

### 1. The request-wide ceiling is explicitly sealed-v3-only

`PluginAgentRunRequest.sealed_provider_attempt_grant` defaults to `False`, is
validated as a boolean, and round-trips old wire frames as false
(`agent/plugin_agent.py:98,138,178,536`). The only production authority that
sets it true is `AgentNodeExecutor` for strict Archon v3 execution
(`plugins/workflow/executors/ai.py:982-983`). Structured repair inherits that
authenticated marker while receiving only
`granted_provider_attempts - first_provider_attempts`
(`plugins/workflow/executors/ai.py:469-473,484-510`). Approval and ordinary
plugin-agent callers retain the false default.

The worker installs the absolute reservation only for a sealed request
(`agent/plugin_agent_worker.py:1541-1565`). Otherwise it retains the exact
pre-v3 observation wrappers over the two orchestration entrypoints and never
turns `max_api_attempts` into a cross-cycle absolute ceiling
(`agent/plugin_agent_worker.py:1566-1586`). The paired real-loop regressions
prove v3 is bounded for grants 1 through 5 while the legacy two-attempt cycle
still performs its established four calls across recovery and fallback.

### 2. Direct sealed transports now reserve their launches exactly once

The strict worker no longer wraps delegating orchestration methods. Instead it
installs one request-local callback whose locked prelaunch reservation rejects
call `grant + 1`, and the callback is inert for every non-sealed caller
(`agent/provider_attempts.py:1-15` and
`agent/plugin_agent_worker.py:1543-1565`). This removes the previous Codex
streaming-to-nonstreaming double count while preserving the legacy observer.

I traced each in-scope transport seam:

- OpenAI-compatible nonstreaming and MoA calls reserve immediately before
  `chat.completions.create`, including the direct cron dispatcher
  (`agent/chat_completion_helpers.py:407-436`).
- OpenAI streaming reserves immediately before its stream create
  (`agent/chat_completion_helpers.py:2703-2710`). The two direct iteration-limit
  summary calls reserve independently, while Codex and Anthropic summaries
  delegate to their already-reserved transports
  (`agent/chat_completion_helpers.py:2091-2105,2117-2149`).
- Anthropic's streaming path reserves immediately before `messages.stream`
  (`agent/chat_completion_helpers.py:3122-3134`). Its nonstreaming helper passes
  the same callback into `create_anthropic_message`, which reserves separately
  before the attempted stream and before `messages.create` only when the first
  transport is unavailable (`run_agent.py:5041-5058` and
  `agent/anthropic_adapter.py:2882-2925`).
- Codex Responses reserves inside each real `responses.create` retry, not at
  the delegating streaming/nonstreaming entrypoints
  (`agent/codex_runtime.py:1246-1263`). The app-server path reserves once for
  the one opaque delegated `run_turn` transport
  (`agent/codex_runtime.py:691-698`).
- Bedrock nonstreaming reserves immediately before `converse`; streaming
  reserves before `converse_stream`, and an IAM-denied stream must reserve a
  second grant before the distinct `converse` fallback
  (`agent/chat_completion_helpers.py:407-420,2334-2371`). The grant-one and
  grant-two regression proves the fallback cannot launch without its own
  reservation.

Thus nested transport-entrypoint delegation contributes no reservation by
itself, every listed direct transport try contributes one, fallback/recovery
and summary tries draw from the same locked grant, and the worker's direct
audit counter is the number of successful reservations. The real worker matrix
covers grants 1 through 5, primary recovery, fallback, structured residual
execution, and prelaunch cancellation. It does not compose a sealed parent
with the existing `workflow_agent` child dispatcher described in Important 1.

### 3. Exhaustion has one stable, durable, no-replay outcome

When a sealed launch would exceed the grant, the worker preserves the exact
bounded provider total and emits only the internal
`provider_attempt_grant_exhausted` identity
(`agent/plugin_agent_worker.py:1552-1561,1680-1717`). The executor converts the
worker's total-call evidence exactly once to additional attempts, preserves it
as exact, maps the stable code, and records `known_no_effect: true`
(`plugins/workflow/executors/ai.py:1158-1177,1191-1200`).

The scheduler charges one workflow attempt plus those additional provider
attempts exactly once (`plugins/workflow/scheduler.py:3327-3361`). With the
sealed grant fully consumed, classification reaches `EXHAUSTED` before any
unknown-error retry path (`plugins/workflow/scheduler.py:295-334,3480-3508`),
so persistence terminates the node rather than scheduling retry or entering
reconciliation (`plugins/workflow/scheduler.py:3544-3570`). The end-to-end
store regression asserts the exact durable total, stable error code, absent
reconciliation interaction, and zero executor replay after scheduler/store
restart.

### 4. Earlier Task 9 contracts remain closed

- V3 normalization retains requested retries, requested total, effective
  total, cap evidence, AI/deterministic defaults, explicit one/five, and caps
  one through five. Legacy still treats `max_attempts` as its historical total
  ceiling with its 1,000 ms delay.
- Ordinary and structured worker totals are converted to additional provider
  attempts exactly once. Missing, malformed, exception, repair, and fallback
  evidence consume the grant conservatively and remain durably
  `provider_attempts_exact: false`.
- Cancellation wins before fatal/outward classification; the fatal closed set
  precedes outward uncertainty; deterministic retry still requires explicit
  authorization plus known no-effect/transient classification.
- Cleanup failure journals its bounded retry charge and metadata atomically
  with the ownership block. Retry wake and claim remain fenced by the durable
  one-winner CAS across both scheduler entrypoints and multiprocess restart.
- Prompt bytes, cached system prefix, message history, role alternation, tool
  visibility, and the model tool schema are unchanged. The small callback seam
  has a concrete sealed-workflow consumer and is inert otherwise; it adds no
  core tool or prompt surface.
- The cumulative Task 9 diff contains no descriptor inheritance, Bash
  materialization, Task 10 implementation, public raw-provider projection, or
  speculative extension hook.

## Verification evidence

All Python tests were run only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Exact Task 9 gate plus the isolated-worker regression:
   `test_phase3_execution_semantics.py`, `test_retry.py`,
   `test_provider_failures.py`, `test_ai_executor.py`,
   `test_parallel_scheduler.py`, `test_coordinator_multiprocess.py`,
   `test_shutdown_recovery.py`, and `test_plugin_agent.py` — **8 files,
   335 tests passed, 0 failed, no retries**.
2. Broad persistence, recovery, cancellation, legacy fallback, OpenAI,
   Anthropic, Codex, Bedrock, streaming, summary, and app-server gate:
   `test_store.py`, `test_crash_recovery.py`, `test_cancel_node.py`,
   `test_32646_fallback_429_after_timeout.py`,
   `test_cron_inline_api_call_62151.py`,
   `test_bedrock_interrupt_post_worker.py`, `test_codex_ttfb_watchdog.py`,
   `test_anthropic_adapter.py`, `test_anthropic_kwargs_sanitize.py`,
   `test_codex_app_server_runtime.py`, `test_codex_app_server_session.py`,
   `test_codex_app_server_integration.py`, `test_streaming.py`,
   `test_turn_finalizer_iteration_limit_exit.py`, and
   `test_non_stream_stale_timeout.py` — **15 files, 513 tests passed, 0
   failed, no retries**.
3. Ruff over all eighteen cumulative Task 9 production/test Python files —
   **PASS**.
4. `git diff --check e89c5dce4..9a9274e67` — **PASS**.
5. The pinned HEAD and tree matched the assignment, and the worktree was clean
   before this retained review report was written.

## Final assessment

The third fix correctly gates the direct transport reservation to v3, counts
the reviewed OpenAI/Anthropic/Codex/Bedrock launches once, preserves legacy,
and makes exhaustion stable and terminal. Task 9 is not specification-complete
while an admitted strict-v3 `workflow_agent` child receives an independent
unsealed allowance and its provider calls do not charge the parent's durable
combined ledger. Close that cross-worker composition before proceeding.
