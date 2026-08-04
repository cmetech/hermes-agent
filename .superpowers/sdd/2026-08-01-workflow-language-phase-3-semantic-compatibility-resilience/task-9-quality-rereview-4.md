# Phase 3 Task 9 Quality Rereview 4

**Reviewed candidate:** `c9801009fc4b230162c4d291b14efa93f200c57b`

**Reviewed tree:** `c9d1d73e154e31a99adf2db8a54c12b5604ac7fd`

**Task baseline:** `e89c5dce4`

**Verdict:** CHANGES REQUIRED

**Findings:** 0 Critical, 2 Important, 0 Minor

## Scope reviewed

I read the approved Phase 3 retry design, the complete Task 9 plan, all eight
prior Task 9 specification/quality reports, the full cumulative Task 9 diff,
and the complete `ca7028c2d..c9801009f` fourth-fix diff. I read both changed
production modules in full and traced authority creation, authenticated wire
validation, reservation/snapshot framing, concurrent and nested descendants,
inline request propagation, provider launch callbacks, audit publication,
runner cancellation/timeout cleanup, process-tree ownership, failure
classification, and durable ledger charging. I also rechecked strict-v3
gating, legacy and no-inline paths, prompt/cache/tool-schema invariants, and
Task 10 scope. I made no production or test edits.

The fourth fix closes the accounting escape from the previous rereview: a
strict-v3 parent with declared inline agents now receives one host-owned broker,
all descendants inherit its authenticated descriptor, successful reservations
are atomic across processes, and the parent publishes the broker's aggregate
count. Legacy and strict-v3 nodes without inline agents retain their existing
paths. Two broker-quality gaps still prevent closure.

## Important findings

### I1. One stalled loopback client can defeat the broker's wall-time and shutdown bounds

The new authority is a single-threaded TCP server. `Listener.accept()` performs
the authentication handshake synchronously, then `_serve()` performs an
unbounded `recv_bytes()` before it can accept another client
(`agent/plugin_agent.py:204-216`). The matching client path also has no connect,
authentication, send, or receive deadline (`agent/plugin_agent.py:114-138`).

This creates two concrete liveness failures. A local process can connect to the
loopback port and stop during the authentication handshake without knowing the
authkey, preventing every legitimate descendant from reserving. An
authenticated descendant can likewise stop after authentication but before
sending a complete frame. The outer worker will eventually reach its normal
idle/wall timeout, but cleanup then calls `_ProviderAttemptAuthority.close()`.
That method attempts a second blocking authenticated `Client()` connection
*before* closing the listener (`agent/plugin_agent.py:256-277`). If `_serve()`
is still blocked on the first connection, the shutdown client's handshake can
also wait forever. The two-second `join()` is never reached, so the supposedly
bounded attempt can hang the scheduler thread after its worker tree has already
been terminated.

Authentication prevents forged reservations but does not bound unauthenticated
handshake work, and a one-client serial server makes this an availability and
lifecycle issue. Give accept/authentication/request/response and shutdown
finite deadlines, ensure one stalled connection cannot block other clients,
and make `close()` able to close/unblock the listener without first completing
another potentially blocking handshake. Add a stalled unauthenticated client,
stalled authenticated client, concurrent legitimate reservation, cancellation,
and close/reap matrix proving the authority thread and runner return within a
small deterministic bound.

### I2. The tests still substitute direct reservations for the real nested runner and omit the required exhaustion-to-store path

The previous closure finding required a real strict-v3 parent/child composition
that proves the child worker transport, parent continuation, durable exact
charge, exhaustion classification, cancellation, and restart all use one
authority. The fourth-fix tests split this across lower-level fakes instead:

- `test_strict_inline_agent_inherits_one_private_provider_authority` only
  inspects the constructed child request
  (`tests/plugins/workflow/test_node_agents.py:191-242`).
- `test_worker_audit_includes_real_inline_child_process_reservations`
  monkeypatches `PluginAgentRunner` with `ProcessChildRunner`, whose child
  subprocess calls `_reserve_shared_provider_attempt()` directly instead of
  running the actual child worker/provider transport
  (`tests/agent/test_plugin_agent.py:1560-1692`). It exercises only the
  successful grant-four case.
- The grant-one-through-five race test drives the broker directly and never
  crosses `PluginAgentRunner`, `plugin_agent_worker`, `AgentNodeExecutor`, the
  scheduler, or the store (`tests/agent/test_plugin_agent.py:1431-1465`).

There is consequently no regression that makes a real parent spend a call,
runs a real child provider call, exhausts during a child or parent
continuation, and verifies the stored `provider_attempts_exact`,
`retry_consumed`, terminal `provider_attempt_grant_exhausted`, and absence of
replay after cancellation/restart. This is the highest-risk new path: two
worker process levels, an authenticated side channel, process-tree cleanup,
and durable accounting. The component tests are useful but do not establish
that those seams compose. Add the real end-to-end matrix requested by the
previous review for grants one through five, sequential/concurrent descendants,
exhaustion at each boundary, cancellation, and restart.

## Closed findings and retained strengths

- The authority owns one locked aggregate counter; successful parent, child,
  and grandchild reservations cannot exceed the sealed grant even under the
  tested process race.
- The descriptor is loopback-only, versioned, exact-field validated, protected
  by a fresh 256-bit authkey, bounded to 1,024-byte frames, omitted from repr,
  and propagated only with a sealed request. Reservation denial is fail-closed.
- Parent audit uses the broker snapshot, so successful inline-child calls no
  longer disappear from the node-wide provider total. Child-local totals are
  not added a second time.
- Broker creation is limited to sealed requests that actually declare inline
  agents. Legacy requests do not inherit an authority, and strict-v3 no-inline
  requests keep the existing in-process lock/counter.
- Ordinary and structured count conversion, conservative provenance, fatal and
  outward classification, cleanup accounting, retry-wake CAS, and direct
  transport enforcement remain intact from earlier fixes.
- The change adds no prompt bytes, model tool kind, API projection, raw provider
  surface, descriptor-inheritance primitive, Bash materialization, or other
  Task 10 work.

## Verification evidence

All Python tests were run only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Exact Task 9 gate plus inline-agent regressions:
   `test_phase3_execution_semantics.py`, `test_retry.py`,
   `test_provider_failures.py`, `test_ai_executor.py`,
   `test_parallel_scheduler.py`, `test_coordinator_multiprocess.py`,
   `test_shutdown_recovery.py`, `test_plugin_agent.py`, and
   `test_node_agents.py` — **9 files, 357 tests passed, 0 failed, no
   retries**.
2. Adjacent persistence, crash/cancellation, OpenAI fallback, Anthropic,
   Codex, Bedrock, streaming, summary, MoA, and app-server coverage — **18
   files, 564 tests passed, 0 failed, no retries**.
3. Combined review execution — **27 files, 921 tests passed, 0 failed, no
   retries**.
4. Ruff over all 19 cumulative Task 9 production/test Python files — **PASS**.
5. `git diff --check e89c5dce4..c9801009f` and
   `git diff --check ca7028c2d..c9801009f` — **PASS**.
6. The pinned HEAD/tree matched the assignment, and the worktree was clean
   before this retained report was written.

## Final assessment

The fourth fix establishes the intended single aggregate authority in code and
closes the original independent-child allowance. Task 9 is not quality-complete
while an unbounded serial IPC handshake can strand the scheduler during broker
shutdown and while the real nested worker-to-store exhaustion/restart path is
still replaced by component fakes in tests.
