# Agent Handoff Stage 3 restart-drain re-review — Codex

**Date:** 2026-09-03

**Reviewer:** Codex CLI 0.152.0, `gpt-5.6-sol`, `xhigh`

**Candidate:** `ff05217f576411946777b82b71600caa4d0437f1`

**Tree:** `e9eeaba5fa9760e7f9590dbdeb4b497fddb8b576`

**Verdict:** **BLOCK**

This is a normalized capture of the independent report returned to stdout.
The reviewer verified the immutable 29-commit, 18-path, `+2112/-71` scope in a
clean detached worktree and found one reproducible Important production
defect.

## Finding

### IMPORTANT — restart-draining FIFO rejection reported adapter acceptance

`request_restart()` sets `_draining=True` while adapters remain live. For an
active session, the earlier draining branch called the bounded queue helper but
ignored its Boolean result, sent the ordinary queued status, and returned
success. The later non-control-internal branch that propagates the handoff
backpressure marker was unreachable.

With the FIFO already at its 32-event cap, a due handoff return was therefore
dropped but entered `delivery_receipt_pending`: it consumed one attempt,
retained an impossible receipt reservation and process identity, suppressed
same-process retry after capacity became available, and ordered a newer return
behind a turn that was never admitted.

The deterministic probe used the production runner, base adapter, SQLite store,
and existing handoff fixtures. It observed `reported_handled: true`, queue depth
32, no admission, no backpressure marker, one consumed attempt, and retained
receipt-pending state.

The required root fix was to route non-control internal events through the
observable FIFO/backpressure path before generic restart-drain messaging. That
keeps a single acceptance boundary and prevents synthetic returns from
receiving a false user-message-style queued response.

## Other dispositions and verification

The reviewer approved the preceding attention, dispatch, receipt, owner,
restart, configuration, peer-policy, ordinary-capacity, and Raft remediations,
as well as both binding design clarifications. The required 14-file gate
reported:

```text
386 passed, 0 failed, 1 native-Windows skip
```

The separate Raft test file reported 8 passed, 0 failed. No live credentials,
services, inference destinations, external network, or Desktop renderer was
used. The worktree remained detached and clean.
