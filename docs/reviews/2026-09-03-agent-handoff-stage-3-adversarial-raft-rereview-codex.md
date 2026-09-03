# Agent Handoff Stage 3 Raft re-review — Codex

**Date:** 2026-09-03

**Reviewer:** Codex CLI 0.152.0, `gpt-5.6-sol`, `xhigh`

**Candidate:** `c8ed1474fde94b83929ab420b98ec7f9326d37ad`

**Tree:** `212a311c7e3f2ebc51828f4e1480792168ca5463`

**Verdict:** **BLOCK**

This is a normalized capture of the independent report returned to stdout.
The pass verified the immutable 27-commit, 16-path, `+2046/-69` scope and
found one reproducible IMPORTANT production defect.

## Finding

### IMPORTANT — Raft bypassed the handoff FIFO/backpressure boundary

The bundled Raft adapter overrides `handle_message()` for its wake-hint
behavior. While its session was busy, the override directly used the adapter's
single pending slot and returned without calling the registered runner-owned
busy handler. A second handoff return replaced the first queued return, but the
gateway saw no backpressure marker and classified both as accepted. The first
durable row could then remain receipt-pending and suppressed in the same
process even though no corresponding turn could persist its receipt.

A bounded probe used the real gateway injection method, real Raft adapter, and
an installed busy handler. It reported `busy_handler_calls: 0`, replaced the
older delivery ID with the newer one, emitted no backpressure marker, and
returned gateway acceptance.

The required root fix was to route non-control internal events through
`BasePlatformAdapter.handle_message()` before applying Raft's wake-only busy
shortcut. This preserves ordinary Raft wake behavior while reusing the shared
FIFO, identity, cap, and backpressure boundary.

## Other dispositions and verification

The reviewer passed attention supersession and dispatch ordering, same-process
owner rotation, true-process restart recovery, standard-adapter queue
backpressure, configuration fail-closed behavior, legacy peer transport policy,
both binding clarifications, and the intentional native-Windows fail-closed
boundary. The required 14-file gate reported:

```text
386 passed, 0 failed, 1 native-Windows skip
```

No live credentials, services, inference, external network, or Raft bridge was
used. The worktree remained detached and clean.
