# Phase 3 Task 9 Specification Rereview 4

**Verdict:** PASS

**Reviewed HEAD:** `c9801009fc4b230162c4d291b14efa93f200c57b`

**Reviewed tree:** `c9d1d73e154e31a99adf2db8a54c12b5604ac7fd`

**Fix baseline:** `9a9274e67ace9cf022ab188443d38097286e1bb0`

**Severity counts:** 0 Critical, 0 Important, 0 Minor

## Scope reviewed

I read the approved Phase 3 design, the complete Task 9 plan, all eight prior
Task 9 specification and quality reports, the full cumulative Task 9 diff, and
the complete `9a9274e67..c9801009f` fourth-fix diff. I traced the sealed retry
authority from v3 admission and durable residual grant through the workflow
executor, top-level isolated request, inline-agent dispatcher, child worker,
provider transport reservation, broker snapshot, executor conversion,
scheduler charge, failure classification, cleanup, cancellation, and restart.
I also rechecked direct transport coverage, exact/conservative provenance,
fallback and structured repair, legacy isolation, prompt/cache/tool invariants,
private wire data, and Task 10 scope. I made no production or test edits.

The fourth fix closes the sole remaining finding. A strict-v3 parent and all
of its inline child processes now spend one authenticated request-local grant;
the parent publishes the broker's aggregate count rather than a child-local or
parent-local subtotal. All previous Task 9 findings remain closed.

## Closure evidence

### 1. Parent, child, and concurrent descendants share one atomic authority

The host creates one `_ProviderAttemptAuthority` only when a sealed request has
inline agents and does not already carry an inherited authority
(`agent/plugin_agent.py:1094-1137`). The broker validates a grant from one
through five, binds only to `127.0.0.1`, generates a 256-bit authentication
key, serializes reserve decisions through one state authority, and denies the
first request beyond the grant without incrementing the successful-attempt
count (`agent/plugin_agent.py:82-277`).

The inline dispatcher copies both the sealed marker and the same private
authority descriptor into every child request instead of copying an
independent counter (`agent/plugin_agent_worker.py:1032-1051`). Nested and
concurrent processes therefore authenticate to the same broker. The grant
matrix and process-race regressions prove exactly `grant` successful
reservations for grants one through five, with all surplus callers receiving
`provider_attempt_grant_exhausted`; the nested-process regression proves a
grandchild client contributes to the same count. The parent/child composition
then spends two parent and two child reservations, sequentially and
concurrently, and reports exactly four aggregate provider attempts.

### 2. Aggregate evidence is converted and charged once

For a shared sealed request, every direct provider launch calls the inherited
reservation capability. The worker snapshots the broker after the synchronous
conversation and uses that aggregate count as `audit.provider_attempts`
(`agent/plugin_agent_worker.py:1542-1617,1713-1749`). Child audit values are
not added separately, so there is no double charge. A denied reservation sets
the shared exhaustion bit; the top-level worker consequently returns a failed
result with the stable `provider_attempt_grant_exhausted` identity even if the
parent conversation would otherwise complete.

The already-reviewed executor converts this total-call value to additional
provider attempts exactly once, and the scheduler adds one workflow attempt
exactly once. Exact broker evidence remains exact. Missing, malformed, worker
exception, and other unauthenticated evidence continue to consume the full
grant conservatively with `provider_attempts_exact: false`. Structured repair
remains sequential and receives only the validated residual grant, while
fallback, recovery, summaries, and direct transports reserve before their
actual launches.

### 3. Exhaustion, cancellation, cleanup, and restart remain safe

The broker atomically rejects races at the ceiling and records exhaustion
without opening another provider call. The stable exhaustion result remains
known-no-effect, terminal, fully charged, and outside retry or reconciliation.
Cancellation is checked before reservation; host cancellation, timeout,
worker error, and ordinary completion all leave `_exchange_worker()` through
the authority-closing `finally` block. The retained process-tree cancellation
tests prove descendants are terminated, and the new cancellation regression
proves the private authority thread is closed rather than leaked.

The authority is request-local and is never durable authority. On an eligible
later workflow retry, the scheduler derives the next grant from durable
`effective_total_attempts - retry_consumed` and the new isolated request gets a
fresh broker for only that residual. Unknown or invalid execution evidence is
charged conservatively, so a crash cannot reopen unproven capacity. Durable
retry-wake/claim CAS, cleanup-failure ownership and charge, both scheduler
entrypoints, cancellation boundaries, and restart no-replay tests remain
green.

### 4. Capability, protocol, compatibility, and scope boundaries are intact

The authority descriptor has an exact four-field/versioned schema, requires
the loopback host, validates the port and a 32-byte base64 authentication key,
and is permitted only with `sealed_provider_attempt_grant=true`
(`agent/plugin_agent.py:87-171,751-760`). Broker application frames use
bounded raw JSON bytes through `send_bytes`/`recv_bytes`; no pickle payload is
used. The descriptor is a private request field, omitted when absent and never
copied into result, audit, store, API, Desktop, prompt, message history, or raw
provider evidence.

Unversioned, `hermes-legacy`, and generic plugin-agent requests retain the
false sealed default. A legacy inline child inherits false and no authority;
the historical observation wrappers and recovery/fallback cycle resets remain
unchanged. A sealed request without inline agents retains the previously
reviewed worker-local counter. Only strict Archon v3 execution sets the sealed
marker in production. No system-prompt bytes, role alternation, tool schema,
tool visibility, MCP/skills selection, or prompt-cache prefix changes occur.

The cumulative Task 9 diff adds no managed-process descriptor inheritance,
Bash materialization, generic child-descriptor API, public provider response,
path-taking endpoint, or other Task 10 work.

## Previous finding disposition

- Ordinary and structured total provider counts are converted to additional
  attempts once; exact and conservative provenance remains durable.
- Fatal and sealed-contract failures precede outward uncertainty, while
  cancellation remains first and uncertain outward effects never replay.
- Cleanup failure atomically records its charge with its ownership block.
- Recovery, fallback, Codex, Anthropic, Bedrock, MoA, streaming, summary, and
  structured-repair launches draw from the sealed direct-transport authority.
- Legacy callers retain their historical per-cycle retry and fallback
  behavior.
- The final inline-agent gap is closed by the shared authenticated authority
  and aggregate broker snapshot described above.

## Verification evidence

All Python tests were run only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Exact Task 9 gate plus worker and inline-agent regressions:
   `test_phase3_execution_semantics.py`, `test_retry.py`,
   `test_provider_failures.py`, `test_ai_executor.py`,
   `test_parallel_scheduler.py`, `test_coordinator_multiprocess.py`,
   `test_shutdown_recovery.py`, `test_plugin_agent.py`, and
   `test_node_agents.py` — **9 files, 357 tests passed, 0 failed, no
   retries**.
2. Broad persistence, crash recovery, cancellation, inline-agent, direct
   transport, timeout, Anthropic, Bedrock, MoA, and summary gate — **12 files,
   338 tests passed, 0 failed, no retries**.
3. Correctly located fallback, Codex app-server/runtime/session, streaming,
   and MoA suites — **7 files, 238 tests passed, 0 failed, no retries**.
4. Ruff over all cumulative Task 9 production and test Python files —
   **PASS**.
5. `git diff --check e89c5dce4..c9801009f` — **PASS**.
6. The pinned HEAD and tree matched the assignment, and the worktree was clean
   before this retained review report was written.

## Final assessment

Task 9 is specification-complete. One sealed Archon v3 workflow attempt now
has one non-multiplying provider-call authority across the parent and every
inline child process, while durable accounting, failure safety, legacy
behavior, prompt caching, the narrow core/tool waist, and the Task 10 boundary
remain intact.
