# Phase 3 Task 9 Quality Rereview 3

**Reviewed candidate:** `9a9274e67ace9cf022ab188443d38097286e1bb0`

**Reviewed tree:** `757e57d25db12a2488ee5fbe99d813591cf28853`

**Task baseline:** `e89c5dce4`

**Verdict:** CHANGES REQUIRED

**Findings:** 0 Critical, 1 Important, 0 Minor

## Scope reviewed

I read the complete approved Phase 3 design and implementation plan, all six
prior Task 9 specification/quality reports, the full cumulative Task 9 diff,
and the complete third-fix diff. I traced the sealed provider grant through
the request wire protocol, ordinary and structured AI execution, transport
reservation, primary recovery, fallback, Codex delegation, Anthropic
stream/create fallback, Bedrock stream/converse fallback, structured repair,
inline workflow agents, durable charge/classification, retry wake, cleanup,
restart, cancellation, and both scheduler entrypoints. I also checked false
default behavior, per-agent callback isolation, prompt/tool invariants, and
legacy behavior. I made no production or test edits.

The third fix closes the three previous findings for direct provider
transports: the absolute guard is explicit and defaults false, only Archon v3
requests opt in, reservation occurs at actual transport launch rather than
both orchestration entries, Codex delegation does not double reserve, Bedrock
stream denial reserves the fallback separately, and grant exhaustion reaches
the store as an exact known-no-effect terminal result. One existing nested
provider path still bypasses the node-wide grant.

## Important finding

### I1. Inline workflow-agent calls receive a new unsealed full grant and are absent from the parent ledger

The Task 9 ceiling is per workflow node: every provider call made inside the
AI executor must draw from the one sealed remaining grant. Inline agents are
an existing command/prompt option executed synchronously as a tool inside that
same node, so their provider calls cannot receive an independent allowance.

The v3 executor correctly sends the outer request with
`sealed_provider_attempt_grant=True` and the node's remaining grant
(`plugins/workflow/executors/ai.py:942-984`). The worker installs the guarded
counter only on that outer `AIAgent` instance
(`agent/plugin_agent_worker.py:1537-1565`) and later publishes only that
counter as exact provider evidence (`agent/plugin_agent_worker.py:1684-1703`).

When the outer model invokes `workflow_agent`, however,
`_build_inline_agent_handler()` constructs a new `PluginAgentRunRequest` and
launches a new `PluginAgentRunner` process
(`agent/plugin_agent_worker.py:1031-1071`). The child receives the parent's
full `max_api_attempts` at line 1046, but the constructor omits
`sealed_provider_attempt_grant`, so it takes the protocol's false default.
Its calls therefore use legacy observation-only accounting and are never
reserved against or reported into the parent's guarded counter. The parent
can spend calls before the tool invocation, the child can spend up to another
full retry cycle (including its own recovery/fallback resets), and the parent
can continue afterward while durable metadata still claims the outer count is
the exact node total. Multiple allowed descendants multiply the escape.

The existing inline-agent test uses a fake child runner and a `None` parent and
asserts only synchronous/bounded dispatch
(`tests/plugins/workflow/test_node_agents.py:140-188`). The new Task 9 worker
tests cover direct fallback, repair, Codex delegation, and legacy isolation,
but do not compose a sealed v3 parent with `workflow_agent`
(`tests/agent/test_plugin_agent.py:1428-1566`). Consequently all current gates
remain green while the real nested provider budget is neither shared nor
accounted.

Required correction: make v3 inline descendants reserve from the same
request-wide remaining authority and return their exact charges to the parent
without changing legacy inline-agent behavior. A child-local copy of the full
grant is insufficient. Add a real parent-worker/child-runner regression in
which the parent spends at least one call, invokes one or more inline agents,
and attempts to continue; assert total actual transports across the process
tree never exceed the original grant, the stored exact count equals those
transports, exhaustion is terminal/known-no-effect, and cancellation/restart
cannot reopen capacity.

## Closed earlier findings and retained strengths

- `sealed_provider_attempt_grant` is a validated boolean, round-trips on the
  wire, defaults false for old frames/generic callers, and is enabled only by
  strict Archon v3 workflow requests. The structured repair request carries
  the marker while receiving only the residual grant.
- Direct Chat Completions, Codex Responses/app-server, Anthropic Messages,
  Bedrock Converse, streaming fallbacks, and iteration-limit summaries reserve
  at their transport launch seams. The reservation callback is per `AIAgent`
  and locked, and external calls run outside the lock.
- A denied call does not increment the count. The successful calls already
  reserved remain exact, and `provider_attempt_grant_exhausted` is mapped to
  known-no-effect before the scheduler persists one full terminal charge with
  no reconciliation or replay.
- Ordinary text and structured totals are converted to additional calls once;
  missing/invalid evidence remains conservative and inexact. Fatal provenance,
  outward-action precedence, cleanup-failure atomic accounting, retry-wake CAS,
  cancellation, and both scheduler entrypoints remain intact.
- The direct legacy recovery/fallback regression retains four calls across two
  historical cycles with a two-attempt per-cycle value. No prompt prefix,
  message history, tool schema, API projection, or Task 10 descriptor behavior
  changed.

## Verification evidence

All Python tests were run only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Exact Task 9 gate plus worker regressions:
   `test_phase3_execution_semantics.py`, `test_retry.py`,
   `test_provider_failures.py`, `test_ai_executor.py`,
   `test_parallel_scheduler.py`, `test_coordinator_multiprocess.py`,
   `test_shutdown_recovery.py`, and `test_plugin_agent.py` — **8 files,
   335 tests passed, 0 failed, no retries**.
2. Adjacent persistence, cancellation, inline-agent, Bedrock, retained
   fallback, and MoA regressions: `test_store.py`, `test_crash_recovery.py`,
   `test_cancel_node.py`, `test_node_agents.py`,
   `test_bedrock_interrupt_post_worker.py`,
   `test_32646_fallback_429_after_timeout.py`, `test_moa_loop_mode.py`,
   `test_moa_streaming.py`, and `test_moa_slot_api_mode.py` — **9 files,
   128 tests passed, 0 failed, no retries**.
3. Ruff over all 18 cumulative Task 9 production/test files — **PASS**.
4. `git diff --check e89c5dce4..9a9274e67` and
   `git diff --check df8672b77..9a9274e67` — **PASS**.
5. The pinned HEAD/tree and clean worktree were verified before this report was
   written. This review changed no production or test file.

## Final assessment

The third fix is sound for direct transports and closes the prior gating,
double-counting, and exhaustion-classification findings. Task 9 cannot close
while the existing inline-agent option can launch separately budgeted provider
processes inside one v3 node and leave the parent's supposedly exact durable
ledger unaware of them.
