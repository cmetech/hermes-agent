# Agent Handoff Stage 3 TUI receipt re-review — Codex

**Date:** 2026-09-03

**Reviewer:** Codex CLI 0.152.0, GPT-5 Codex runtime

**Candidate:** `907b47b6ae91bc1ac48ed750bffa1c927a7ab0de`

**Tree:** `78635dd906ea44922a6717c80bf38724b0de1e56`

**Verdict:** **BLOCK**

This is a normalized capture of the independent report returned from a clean
detached worktree. The reviewer verified the immutable 41-commit, 21-path,
`+2637/-88` scope and found one reproducible Important production defect.

## Finding

### S3-HANDOFF-001-R1 — a long Desktop/TUI turn exhausted delivery attempts

The supervisor granted a 30-second delivery lease. Desktop/TUI began the
durable dispatch reservation and then held that ordinary lease until the
asynchronous model turn's terminal callback. Unlike the gateway path, it never
entered `delivery_receipt_pending` after successful turn admission.

A legitimate model or tool turn lasting roughly four minutes therefore allowed
the supervisor to reclaim the same non-receipt claim through the eight-attempt
limit. The next claim marked the row `failed/delivery_attempts_exhausted`; the
original callback's epoch was stale, so the eventual transcript receipt could
not settle the row. Attention remained visible but no due replay remained.

The reviewer reproduced the result with the production SQLite store and
clock-controlled lease rotation: attempts reached eight, the ninth claim
returned none, the original callback raised `StaleAdvanceLease`, and no
delivery was due after receipt persistence.

The smallest root fix was to reuse the existing receipt-pending lifecycle in
the TUI dispatcher: retain one live process identity, make owner rotations
receipt-only, settle through a current receipt claim, and release that state
for keyed retry when a true process restart has no matching identity.

## Other dispositions and verification

The reviewer passed every preceding attention, configuration, gateway FIFO,
owner/restart, backpressure, Raft, drain, discard, peer-policy, and binding
clarification case. The required 14-file gate reported 393 passed, 0 failed,
and 1 native-Windows skip; the four-file supplementary gate reported 48 passed.
No live credentials, services, inference destinations, external network, or
Desktop native dependencies were used. The worktree remained detached and
clean.
