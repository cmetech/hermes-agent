# Agent Handoff Stage 3 discarded-receipt re-review — Claude

**Date:** 2026-09-03

**Reviewer:** Claude Code 2.1.259, Claude Opus 5, high effort

**Candidate:** `951fc20680613e95c7d78c443f976c4ccfd1cfe1`

**Tree:** `c63ba55819875ddc789d339a7abdd844e275be4c`

**Verdict:** **BLOCK**

This is a normalized capture of the independent report from a clean detached
worktree. The reviewer verified the 31-commit, 18-path, `+2135/-84` scope and
found one reproducible Important production defect.

## Finding

### RR-G01 — an accepted but discarded queued return wedged the delivery ledger

A busy gateway could accept a durable `handoff_return` into its FIFO and enter
receipt-pending state. Two existing discard paths could then remove that queued
event before transcript persistence: stale-session-lock healing and conversation
scope reset. Because the live process retained the delivery identity and the
durable receipt-pending claim consumed no further attempts, the abandoned row
could remain pending indefinitely and keep a newer terminal return out of
`due_deliveries`.

The reviewer reproduced both discard paths against the production gateway and
SQLite store. After 25 and 30 replays respectively, the older delivery remained
`pending`, `attempts=1`, and `failure_code=delivery_receipt_pending`; the newer
terminal attention row existed but was not due.

The suggested arbitrary receipt-poll budget was not adopted because a live but
slow queued turn could be duplicated merely because time elapsed. The controller
instead reconciled the concrete discard boundaries: known discarded internal
returns release their exact durable reservation and process identity, while
ordinary receipt polling remains unbounded for a still-owned queued turn.

## Other dispositions and verification

The reviewer passed the prior attention, configuration, dispatch, receipt,
owner-rotation, true-restart, FIFO-capacity, Raft, restart-drain, peer-policy,
Desktop/TUI, and binding-clarification cases. The required 14-file gate reported
387 passed, 0 failed, and 1 native-Windows skip. The detached worktree remained
clean. No live credentials, services, inference destinations, or external
network were used.
