# Agent Handoff Stage 3 queued-return re-review — Codex

**Date:** 2026-09-03

**Reviewer:** Codex CLI 0.152.0, `gpt-5.6-sol`, `xhigh`

**Candidate:** `2234ab62f8fb86bd9025d774f540529addaa9a1a`

**Tree:** `39c4cc222964861e0360f4a5867a23444c09cc0a`

**Verdict:** **BLOCK**

This is a normalized capture of the independent report returned to stdout.
The pass verified the immutable 19-commit, 16-path, `+1398/-31` scope and
found one reproducible IMPORTANT production defect.

## Finding

### IMPORTANT — gateway released a reservation before actual turn admission

`GatewayRunner._deliver_completion_notification()` durably reserved the
delivery, but `_inject_watch_notification()` treated
`BasePlatformAdapter.handle_message()` returning as model-turn admission. A
busy adapter only queued the event. The immediate transcript receipt was
therefore absent, and the gateway released the reservation before the queued
turn had persisted its delivery ID.

That let a later terminal return become due while the obsolete `needs_input`
return remained queued. The ordinary pending-text merge could combine their
texts while retaining only the older delivery ID. Suppressed receipt retries
also consumed the eight-attempt budget despite only one adapter submission,
eventually failing the durable row before its receipt appeared.

The deterministic probes observed both delivery IDs cross the adapter boundary
and an accepted-pending delivery exhaust all eight attempts after one adapter
submission. The required root fix was to retain the durable reservation across
adapter queue acceptance, poll the transcript without consuming attempts,
settle only after the delivery ID persists, and route distinct handoff returns
through an identity-preserving FIFO.

## Other dispositions and verification

The reviewer passed the YAML/config fail-closed remediation, legacy peer
transport policy, Desktop/TUI dispatch fencing, the production Bot/Desktop
constructor boundary, and the intentionally deferred native-Windows lock. The
required 14-file gate reported:

```text
381 passed, 0 failed, 1 native-Windows skip
```

No live credentials, services, inference, or external network were used. The
worktree remained detached and clean.
