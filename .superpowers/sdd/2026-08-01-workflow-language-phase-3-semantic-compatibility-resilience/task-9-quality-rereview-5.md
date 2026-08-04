# Phase 3 Task 9 Quality Rereview 5

**Reviewed candidate:** `00ecae5cd6780ee2443e1a0f421bea3cbf1536fb`

**Reviewed tree:** `ff6b5509f2a09105f204a42dd6b622666daded76`

**Fifth-fix baseline:** `6b9926dfe`

**Task baseline:** `e89c5dce4`

**Verdict:** PASS

**Findings:** 0 Critical, 0 Important, 0 Minor

## Scope reviewed

I read the approved Phase 3 retry design, the complete Task 9 plan, all prior
Task 9 specification and quality reports, the full cumulative Task 9 diff, and
the complete `6b9926dfe..00ecae5cd` fifth-fix diff. I traced the sealed provider
authority through host creation, private request propagation, outer and inline
worker parsing, provider-transport reservation, aggregate audit publication,
executor conversion, scheduler classification and charging, durable storage,
cancellation, restart, and cleanup.

I reviewed the replacement broker in depth for loopback confinement, descriptor
validation, HMAC framing, nonce replay, malformed and oversized input, stalled
and unauthenticated clients, concurrency caps, atomic reservation, client and
server deadlines, active-socket cleanup, close/accept races, worker lifecycle,
thread safety, resource exhaustion, failure behavior, and platform portability.
I also rechecked legacy and no-inline isolation, prompt/cache/tool invariants,
the private evidence boundary, every earlier Task 9 correction, and absence of
Task 10 implementation. I made no production or test edits.

The fifth fix closes both findings from quality rereview 4. The authority no
longer performs a blocking authentication handshake on its accept thread or
requires a second authenticated connection to shut down. The new end-to-end
matrix crosses the real nested worker/provider/executor/scheduler/store seams
and proves the shared ceiling and durable outcome rather than substituting
direct broker calls.

## Closure evidence

### 1. Broker I/O and shutdown are bounded and fail closed

The broker binds an ordinary TCP socket only to `127.0.0.1`, limits its listen
backlog and active clients, and gives `accept`, each accepted connection, and
each client request explicit short timeouts (`agent/plugin_agent.py:86-93,
163-210,243-307`). The accept thread performs no authentication or frame read;
it delegates each admitted connection to a bounded daemon worker. One stalled
unauthenticated or partial authenticated connection therefore occupies at most
one of eight bounded slots for at most the 0.5-second I/O deadline and cannot
serialize legitimate reservations behind it.

`close()` marks the authority closed under the connection lock, signals the
accept loop, closes the listener, shuts down every captured active socket, and
joins the accept and client workers within explicit deadlines
(`agent/plugin_agent.py:403-431`). The accept/add race is fenced by rechecking
the close event while holding the same connection lock; an accepted connection
cannot be added after the close snapshot and left active. Client workers remove
their socket/thread entries and release the bounded semaphore in `finally`.
There is no shutdown handshake, blocking second client, unbounded receive, or
unbounded join remaining.

The new regressions prove that an unauthenticated stalled socket and an
authenticated partial frame do not block a concurrent legitimate reservation,
that a silent server makes the client fail shut inside the deadline, and that
close unblocks a stalled connection and restores the broker-thread set. The
existing cancellation exchange regression additionally proves the owning
runner closes the private authority while terminating its worker tree.

### 2. Authentication, framing, replay, and authority state remain sound

Descriptors retain an exact versioned four-field schema, require loopback, a
valid TCP port, and an exact 32-byte base64 key. Requests and responses use
bounded length-prefixed canonical ASCII JSON, fresh 256-bit nonces, and
SHA-256 HMACs verified with constant-time comparison
(`agent/plugin_agent.py:96-240,309-386`). Unknown fields and operations,
malformed JSON/base64, non-ASCII frames, zero/oversized lengths, invalid MACs,
and replayed live-window nonces are rejected without changing authority state.
The replay window is bounded, and the locked grant counter remains the final
authority even after an old nonce ages out: no accepted request can increment
past the sealed grant.

The reservation counter, exhausted bit, nonce set, and nonce queue are mutated
under one state lock. Successful reservations are atomic; denied reservations
set exhaustion without incrementing. Network work and client joins occur
outside that state lock. At most eight connection workers and 1,024 bytes per
frame are admitted, so stalled or malformed local traffic has bounded thread,
socket, memory, and processing cost. The implementation uses portable Python
AF_INET sockets, timeouts, locks, events, and daemon threads; it adds no Unix-
only descriptor or pathname dependency.

### 3. The nested integration proof is real and covers durable outcomes

`test_real_inline_worker_tree_charges_one_durable_provider_ledger` does not
call the broker directly. It starts a real loopback Chat Completions HTTP
provider, admits an Archon-v3 workflow into a real temporary `RunStore`, runs
the real `RunScheduler` with `PluginAgentRunner`, and causes the outer model to
invoke declared `workflow_agent` tools. Those tools launch actual inline
`PluginAgentRunner` worker processes whose provider transports reserve through
the inherited authority. Results then return through the real outer worker,
`AgentNodeExecutor`, scheduler classification, and durable store.

The matrix covers grants one through five in both sequential and concurrent
child modes. For every row, actual HTTP transports equal the sealed grant,
stored `retry_consumed` equals that grant, additional attempts equal
`grant - 1`, aggregate audit evidence equals the actual transports, and
`provider_attempts_exact` remains true. Grants below five terminate with the
stable `provider_attempt_grant_exhausted` code, known-no-effect evidence, no
reconciliation, and no interaction. Grant five succeeds. Reopening the store
and advancing with a fresh scheduler preserves the terminal state and exact
charge without another provider request.

The companion real cancellation test blocks an actual child provider request
after launch, cancels through `RunStore`, releases the transport, and proves
the active scheduler returns cancelled, the provider-call ceiling is retained,
the authority thread is reaped, and reopening/advancing the stored run cannot
replay either parent or child work. Together with the exhaustion matrix, this
covers the required real store/classification/restart/cancel/exhaustion seams.

### 4. Previous fixes, compatibility, and scope boundaries remain intact

- Ordinary and structured worker totals are converted from total calls to
  additional calls exactly once. Missing, invalid, or exception evidence
  consumes the grant conservatively and remains inexact.
- Direct Chat Completions, Codex, Anthropic, Bedrock, fallback, recovery,
  streaming, summary, structured repair, parent, child, and concurrent
  descendant launches all spend the sealed request-wide authority. Denial is
  terminal known-no-effect exhaustion, never reconciliation or replay.
- Fatal contract/resource/cleanup classes retain precedence over outward
  uncertainty; cleanup failure retains its atomic durable charge; retry wakes
  and claims remain one-winner CAS operations; cancellation still wins before
  new allocation.
- The shared authority is created only for a sealed request with inline agents.
  Strict-v3 no-inline requests retain the reviewed worker-local path, while
  unversioned, `hermes-legacy`, and generic plugin-agent requests retain the
  false sealed default and historical retry/fallback behavior.
- The descriptor remains a private request field. It is not projected into
  audit results, the store, API/Desktop surfaces, provider content, prompts,
  message history, or model tool schemas. No prompt prefix, role alternation,
  tool visibility, MCP/skills option, or cache behavior changed.
- The cumulative Task 9 diff contains no generic managed-process child-
  descriptor inheritance, Bash materialization, public path/raw-provider
  surface, or other Task 10 implementation.

## Verification evidence

All Python tests were run only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Combined exact and broad Task 9 gate across workflow execution semantics,
   retry/provider composition, AI execution, parallel and multiprocess
   scheduling, shutdown/recovery, real plugin-agent and inline-agent paths,
   store/crash/cancellation, fallback, Anthropic, Codex, Bedrock, streaming,
   MoA, summary, and timeout regressions — **27 files, 937 tests passed, 0
   failed, no retries**.
2. Ruff over all nineteen cumulative Task 9 production/test Python files —
   **PASS**.
3. `git diff --check e89c5dce4..00ecae5cd` and
   `git diff --check 6b9926dfe..00ecae5cd` — **PASS**.
4. The pinned HEAD and tree matched the assignment, and the worktree was clean
   before this retained review report was written.

## Final assessment

Task 9 is quality-complete. The request-wide provider authority is now bounded,
authenticated, concurrent, deadline-safe, and cleanly reaped, while the real
nested execution path proves exact non-multiplying charging and safe terminal,
cancellation, and restart behavior. I found no remaining Critical, Important,
or Minor issue.
