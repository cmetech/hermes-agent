# Agent Handoff Stage 3 owner/restart re-review — Codex

**Date:** 2026-09-03

**Reviewer:** Codex CLI 0.152.0, `gpt-5.6-sol`, `xhigh`

**Candidate:** `91820352f225e6530fc2b332741df0fef27a771c`

**Tree:** `8c3941d705746fc6b39e774922eac29ae53c96ee`

**Verdict:** **BLOCK**

This is a normalized capture of the independent report returned to stdout.
The pass verified the immutable 23-commit, 16-path, `+1753/-57` scope and
found two reproducible IMPORTANT production defects.

## Findings

### IMPORTANT — fresh-process receipt recovery poisoned the new identity guard

A new gateway process recovering a receipt-pending delivery inserted the
delivery identity into its process-local in-flight set, then returned from the
restart-reconciliation branch before the shared cleanup `finally`. The durable
reservation was released, but the new process retained the temporary identity.
Its next normal retry self-suppressed and tried to defer a receipt without a
dispatch reservation, producing `HandoffStateConflict` instead of submitting
the one keyed retry.

The required root fix was to process restart-only recovery before installing
the identity, or remove the temporary identity before returning. The regression
must use two runner instances sharing one temporary handoff database.

### IMPORTANT — a full busy FIFO still reported false acceptance

The same full-queue defect found in the preceding capacity pass remained:
the 32-entry FIFO dropped a handoff return while the base adapter reported it
handled. That created an impossible receipt-pending reservation and prevented
correct retry or later-return progress.

The required root fix was an observable, retryable capacity rejection that
does not consume a delivery attempt.

## Other dispositions and verification

The reviewer passed same-process supervisor-owner rotation, configuration
fail-closed behavior, legacy peer transport policy, the production constructor
boundary, and the intentionally deferred native-Windows lock. The required
14-file gate reported:

```text
384 passed, 0 failed, 1 native-Windows skip
```

No live credentials, services, inference, or external network were used. The
worktree remained detached and clean.
