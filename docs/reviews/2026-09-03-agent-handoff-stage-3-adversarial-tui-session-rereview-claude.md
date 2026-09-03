# Agent Handoff Stage 3 TUI session re-review — Claude

**Date:** 2026-09-03

**Reviewer:** Claude Code 2.1.259, Claude Opus 5, high effort

**Candidate:** `5af122a0fd72196f0331e8d51fff07a5bc94263d`

**Tree:** `76c403556a0dae87488c32b6cfe623587035e05f`

**Verdict:** **BLOCK**

This is a normalized capture of the independent report from a clean detached
worktree. The reviewer verified the immutable 43-commit, 22-path,
`+2810/-98` scope and independently found the same Important TUI session
defect as Codex.

## Finding

### RR-TUI-001 — Desktop/TUI session pinned busy after return settlement

The new receipt-pending lifecycle made a persisted receipt replay the ordinary
terminal step for every successful Desktop/TUI return. The poller set
`running=True` before that replay, but the dispatcher returned success after
durable completion without launching the turn thread that normally clears the
flag.

The reviewer exercised the real notification poller in both an isolated
receipt-only probe and a full publish, turn, callback, and republish sequence.
Both completed one durable delivery with one model turn, then left the session
permanently busy. User prompts, later returns, loop ticks, and kanban dispatch
could all remain blocked behind the nonexistent turn.

The smallest root fix was to release the poller's no-turn session claim at the
shared persisted-receipt branch and add a regression beginning from the
production `running=True` state.

## Other dispositions and verification

The reviewer passed the long-turn attempt accounting and all preceding
attention, configuration, gateway queue, owner/restart, capacity, Raft, drain,
discard, peer-policy, and binding-clarification cases. The required 14-file
gate reported 395 passed, 0 failed, and 1 native-Windows skip. A broader sweep
had one unrelated ambient plugin-discovery failure outside the remediation
range. No live credentials, services, inference destinations, or external
network were used. The worktree remained detached and clean.
