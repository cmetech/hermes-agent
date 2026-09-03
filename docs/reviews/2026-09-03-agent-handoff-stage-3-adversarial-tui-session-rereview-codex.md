# Agent Handoff Stage 3 TUI session re-review — Codex

**Date:** 2026-09-03

**Reviewer:** Codex CLI 0.152.0, GPT-5 runtime

**Candidate:** `5af122a0fd72196f0331e8d51fff07a5bc94263d`

**Tree:** `76c403556a0dae87488c32b6cfe623587035e05f`

**Verdict:** **BLOCK**

This is a normalized capture of the independent report from a clean detached
worktree. The reviewer verified the immutable 43-commit, 22-path,
`+2810/-98` scope and found one reproducible Important production defect.

## Finding

### S3-HANDOFF-001-R1 — receipt reconciliation permanently left TUI busy

The notification poller claimed an idle session by setting `running=True`
before dispatch. Once the handoff delivery ID was visible in the transcript,
the shared TUI dispatcher completed the durable delivery and returned success
without starting a model turn or clearing that poller-owned session claim.

The session therefore remained busy indefinitely. Later handoff returns were
requeued, and later user prompts were accepted into the queue even though no
live model thread remained to drain them. The production dispatcher probe
observed a completed current receipt, `running=True`, and a subsequent prompt
reported as queued.

The smallest root fix was to release the no-turn session claim under the
existing history lock in the persisted-receipt branch and prove that exact
poller-owned state in a regression.

## Other dispositions and verification

The reviewer passed the long-turn receipt-only remediation and all preceding
attention, configuration, gateway FIFO, owner/restart, backpressure, Raft,
drain, discard, peer-policy, and binding-clarification cases. The required
14-file gate reported 395 passed, 0 failed, and 1 native-Windows skip. The
supplementary changed-path and installed tests reported 48 passed. No live
credentials, services, inference destinations, external network, or Desktop
native dependencies were used. The worktree remained detached and clean.
