# Agent Handoff Stage 3 dispatch re-review — Codex

**Date:** 2026-09-03

**Reviewer:** Codex CLI 0.152.0, `gpt-5.6-sol`, `xhigh`

**Candidate:** `62bd97a2c2f935c6be9a5149338b3502e744d9ff`

**Tree:** `1dc438731fcd8ad9aeb56b47685741bd0ddc41b1`

**Verdict:** **BLOCK**

This is a normalized capture of the independent report returned to stdout.
The full pass verified the immutable 14-commit, 14-path, `+1188/-29` scope and
found three reproducible IMPORTANT production defects.

## Findings

### IMPORTANT — S3-HANDOFF-001A: completed reservation blocked a later terminal return

A successfully delivered `needs_input` return retained `dispatch_started_at`.
When the conversation later succeeded, the old delivered row remained current
attention and the reservation predicate suppressed the new terminal row from
`due_deliveries()`. The deterministic probe produced two current attention
rows and no due row.

The required root fix was to make the marker represent only an active pending
reservation: clear it on terminal settlement, let later attention acknowledge
already-settled rows, and fence newer delivery only behind an earlier pending
reservation.

### IMPORTANT — S3-HANDOFF-001B: Desktop/TUI bypassed the dispatch reservation

`tui_gateway.server._dispatch_handoff_return()` checked the transcript and then
called `_run_prompt_submit()` without the store-owned dispatch CAS used by the
messaging gateway. Supersession could therefore win durably while the obsolete
input return still crossed the Desktop/TUI model boundary.

The required root fix was to call the shared reservation immediately before
Desktop/TUI submission and close the claim without submitting if it failed.

### IMPORTANT — S3-HANDOFF-002A: dangling config symlink looked genuinely missing

`read_user_config_raw()` mapped every `FileNotFoundError` to an absent config.
An existing symlink with an unavailable target could therefore pass strict
directory validation and select a colliding compatibility destination.

The required root fix was to treat ENOENT as absence only when no directory
entry exists; an existing dangling symlink must fail closed.

## Other dispositions and verification

The reviewer passed ordinary attention supersession, gateway receipt fencing,
legacy peer transport policy, production constructors, and the intentional
native-Windows fail-closed boundary. The required 14-file gate reported:

```text
378 passed, 0 failed, 1 native-Windows skip
```

No live credentials, services, inference, or external network were used. The
worktree remained detached and clean.
