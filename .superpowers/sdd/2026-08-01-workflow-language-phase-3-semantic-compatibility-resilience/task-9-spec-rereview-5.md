# Phase 3 Task 9 Specification Rereview 5

**Verdict:** PASS

**Reviewed HEAD:** `00ecae5cd6780ee2443e1a0f421bea3cbf1536fb`

**Reviewed tree:** `ff6b5509f2a09105f204a42dd6b622666daded76`

**Fix baseline:** `6b9926dfe`

**Severity counts:** 0 Critical, 0 Important, 0 Minor

## Scope reviewed

I read the approved Phase 3 design, the complete Task 9 plan, all ten prior
Task 9 specification and quality reports, the full cumulative Task 9 diff, and
the complete `6b9926dfe..00ecae5cd` fifth-fix diff. I traced the sealed retry
authority from v3 admission and durable residual grant through the workflow
executor, outer isolated worker, real inline child worker, provider transport,
authenticated broker snapshot, executor conversion, scheduler classification,
and store charge. I separately inspected broker connection, request, response,
shutdown, malformed-frame, replay, cancellation, and cleanup behavior. I also
rechecked the legacy and strict-v3-no-inline branches, all earlier Task 9
findings, prompt/cache/tool invariants, and the Task 10 boundary. I made no
production or test edits.

The fifth fix closes both remaining quality findings. Broker work and shutdown
are finite, authenticated, bounded, and fail shut, while the new real nested
matrix proves the provider-to-store composition for sealed grants one through
five. All earlier Task 9 findings remain closed.

## Closure evidence

### 1. Broker connection, authentication, request, response, and shutdown are bounded

The shared authority now uses a loopback TCP socket with a 50 ms accept poll,
500 ms client/server I/O deadlines, an eight-client worker bound, and a
16-connection listen backlog (`agent/plugin_agent.py:86-93,163-194,243-307`).
Each accepted connection is isolated in a bounded worker, so a client stalled
before authentication or midway through an authenticated frame cannot hold the
accept loop or the state lock. The external call and frame I/O occur outside
the locked counter update (`agent/plugin_agent.py:309-394`). Legitimate
reservations therefore continue while a stalled connection times out, and all
provider-count decisions remain serialized only for the short in-memory state
transition.

`close()` does not connect back to the broker or perform an authenticated
shutdown handshake. It atomically marks the authority closed, closes the
listener, shuts down every tracked client socket, joins the accept thread with
a finite timeout, and gives the bounded client-worker set one finite reap
window (`agent/plugin_agent.py:403-431`). The stalled unauthenticated,
authenticated-partial-frame, close/reap, and never-responding-server
regressions prove legitimate reservation, client failure, and cleanup return
within deterministic bounds (`tests/agent/test_plugin_agent.py:1551-1670`).
The existing cancellation-owned-authority test remains green, as does the new
real nested cancellation/store test.

### 2. The private wire protocol is raw, bounded, authenticated, and fail shut

Application frames are four-byte length-prefixed canonical ASCII JSON and are
rejected outside 1 through 1,024 bytes (`agent/plugin_agent.py:96-133`). Every
request has an exact four-field shape: version, reserve/snapshot operation,
fresh 32-byte base64 nonce, and SHA-256 HMAC. The server validates shape,
version, operation, nonce encoding/length, HMAC, and nonce freshness before it
touches authority state (`agent/plugin_agent.py:309-351`). Successful and
denied reservations plus snapshots return a response bound to the same nonce
and authenticated by a response HMAC; the client validates version, nonce, and
MAC before accepting count or exhaustion evidence (`agent/plugin_agent.py:
163-240,352-378`).

Malformed JSON, forged MACs, oversized frames, truncated frames, replayed
nonces, connection failures, and absent responses produce no reservation and
fail shut. The independent raw-frame test covers valid authenticated response,
immediate replay, forged authentication, malformed JSON, a declared 1,025-byte
frame, and a following legitimate reservation without corrupting broker state
(`tests/agent/test_plugin_agent.py:1523-1548,1673-1711`). No pickle,
provider response, prompt, output value, path, or public API/Desktop evidence
crosses this private channel.

### 3. Real outer and inline-child workers spend one durable authority

The end-to-end matrix starts a real local OpenAI-compatible provider, admits a
strict Archon v3 prompt node with a declared inline agent, executes it with a
real `PluginAgentRunner`, and crosses the outer worker, actual
`workflow_agent` child runner/worker, provider transport, `AgentNodeExecutor`,
`RunScheduler`, and `RunStore` (`tests/plugins/workflow/test_retry.py:536-636`).
It covers every sealed grant from one through five in both sequential-child
and concurrent-child modes. The provider's observed transport count equals the
grant, the stored audit total equals that real count, additional provider
attempts equal `grant - 1`, `provider_attempts_exact` remains true, and durable
`retry_consumed` equals the sealed grant.

For grants one through four, exhaustion at the parent/child composition
boundary is terminal `provider_attempt_grant_exhausted`, known no-effect, and
has neither retry nor reconciliation. Grant five completes successfully. A
new scheduler and store do not replay either outcome or reopen provider
capacity. The cancellation composition blocks a real child provider request,
cancels through the store while the nested worker tree is live, then proves a
cancelled terminal run, no later provider call after restart, and no leaked
authority thread (`tests/plugins/workflow/test_retry.py:639-729`). Together
with the retained process-race, nested-grandchild, and direct transport tests,
this closes sequential, concurrent, exhaustion, cancellation, restart,
exact-charge, audit, and cleanup seams without substituting direct broker calls
for the production composition.

### 4. Compatibility, earlier findings, and scope remain intact

- V3 requested/effective/capped retry evidence and the one-workflow-plus-
  additional-provider charge equation remain unchanged. Actual recovery,
  fallback, structured repair, OpenAI, Anthropic, Codex, Bedrock, MoA,
  streaming, and summary launches still draw from the sealed authority.
- Ordinary and structured provider totals are converted exactly once.
  Missing, malformed, exception, and otherwise unauthenticated evidence still
  consumes the grant conservatively and remains durably inexact.
- Cancellation remains first, fatal failures precede outward uncertainty,
  exhaustion is terminal known-no-effect, cleanup failure atomically journals
  its charge with the ownership block, and retry wake/claim remains a durable
  one-winner CAS across both scheduler entrypoints and restart.
- Only strict Archon v3 execution opts into the sealed request authority.
  Unversioned, `hermes-legacy`, generic plugin-agent, and legacy inline-agent
  requests retain their false default and historical retry-cycle/fallback
  behavior. Strict-v3 requests without inline agents retain the reviewed local
  locked counter and do not create a broker.
- The cumulative Task 9 diff changes no system-prompt bytes, cached prefix,
  message history, role alternation, model-tool schema, API/Desktop projection,
  raw-provider surface, managed-process descriptor inheritance, Bash
  substitution, or other Task 10 behavior.

## Verification evidence

All Python tests were run only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Exact Task 9 gate plus worker and inline-agent regressions:
   `test_phase3_execution_semantics.py`, `test_retry.py`,
   `test_provider_failures.py`, `test_ai_executor.py`,
   `test_parallel_scheduler.py`, `test_coordinator_multiprocess.py`,
   `test_shutdown_recovery.py`, `test_plugin_agent.py`, and
   `test_node_agents.py` — **9 files, 373 tests passed, 0 failed, no
   retries**.
2. Broad persistence, crash/cancellation, legacy fallback, OpenAI, Anthropic,
   Codex, Bedrock, streaming, summary, MoA, and app-server gate — **18 files,
   564 tests passed, 0 failed, no retries**.
3. Combined review execution — **27 files, 937 tests passed, 0 failed, no
   retries**.
4. Ruff over all cumulative Task 9 production and test Python files — **PASS**.
5. `git diff --check e89c5dce4..00ecae5cd` and
   `git diff --check 6b9926dfe..00ecae5cd` — **PASS**.
6. The assigned HEAD and tree matched exactly, and the worktree was clean
   before this retained report was written.

## Final assessment

Task 9 is specification-complete. One strict Archon v3 workflow attempt now
has one finite, authenticated, non-multiplying provider-call authority across
the outer and real inline-child worker tree. Its actual transports, exact or
conservative evidence, durable charge, exhaustion, cancellation, cleanup, and
restart behavior compose without changing legacy behavior, prompt caching,
the narrow core/tool waist, or the Task 10 boundary.
