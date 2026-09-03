# Agent Handoff Stage 3 capacity re-review — Codex

**Date:** 2026-09-03

**Reviewer:** Codex CLI 0.152.0, `gpt-5.6-sol`, `xhigh`

**Candidate:** `83da57266745e74e3bdda6b25765cc8b76e51a53`

**Tree:** `862cb257b8209b5abfdada5b16400ba2164b816c`

**Verdict:** **BLOCK**

This is a normalized capture of the independent report returned to stdout.
The pass verified the immutable 21-commit, 16-path, `+1704/-57` scope and
found one reproducible IMPORTANT production defect.

## Finding

### IMPORTANT — a full busy FIFO stranded a durable handoff return

Stage 3 routed distinct handoff returns through the existing busy-session
FIFO. When that FIFO already held its maximum 32 events,
`_queue_or_replace_pending_event()` logged and dropped the return, but its
caller still reported successful adapter acceptance. The delivery therefore
entered receipt-pending state even though no queued event could ever persist a
transcript receipt. Same-process retries were suppressed, and the retained
dispatch reservation could block a newer terminal return indefinitely.

The deterministic probe used the production store, real base-adapter busy
path, and a full FIFO. It observed zero queued handoff events, one consumed
submission attempt, `delivery_receipt_pending`, a retained reservation, and no
change after three receipt polls.

The required root fix was to make FIFO admission observable across the adapter
boundary. Capacity rejection must retain the cap but release the reservation
and process identity through a non-attempt-consuming backpressure path, then
retry when capacity becomes available.

## Other dispositions and verification

The reviewer passed ordinary attention supersession, dispatch ordering,
accepted-pending receipt reconciliation below capacity, configuration
fail-closed behavior, legacy peer transport policy, both binding
clarifications, and the intentional native-Windows fail-closed boundary. The
required 14-file gate reported:

```text
384 passed, 0 failed, 1 native-Windows skip
```

No live credentials, services, inference, or external network were used. The
worktree remained detached and clean.
