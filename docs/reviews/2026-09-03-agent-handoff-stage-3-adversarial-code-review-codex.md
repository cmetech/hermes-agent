# Agent Handoff Stage 3 adversarial code review — Codex

**Date:** 2026-09-03

**Reviewer:** Codex CLI 0.152.0, `gpt-5.6-sol`, `xhigh`

**Immutable candidate:** `2affe5e02307475274cb3d72c24af59f72682945`

**Tree:** `390603dadb2ff6cb8373e4751b6b097bea0ce6b6`

**Verdict:** **BLOCK**

This is a normalized capture of the independent review returned to stdout.
Formatting is condensed; the findings and dispositions are unchanged. The
review ran in its own clean detached worktree and did not read the Claude lane
or any reconciliation.

## Findings

### IMPORTANT — S3-HANDOFF-001: an obsolete input wake could duplicate a terminal wake

The ledger retained an unacknowledged `needs_input` delivery after a newer
terminal observation created another delivery. Both delivery IDs loaded the
current handoff snapshot, so a host absent until terminal completion could
receive two terminal notifications and start two equivalent model turns.

The proposed root fix was to supersede older unacknowledged delivery
projections atomically when a newer attention event commits, without deleting
the older row or source event.

### IMPORTANT — S3-HANDOFF-002: malformed YAML could select compatibility transport

The directory resolver read configuration through the merged/last-known-good
loader. A syntax error could therefore appear as an empty directory and fall
through to a colliding local profile, bare peer, or relay compatibility target
instead of failing before transport.

The proposed root fix was a strict raw-file validation at the directory trust
boundary, before any lookup or fallback.

### IMPORTANT — S3-HANDOFF-003: shared conversation model accepted a deadline or no return route

The reviewer interpreted the shared prompt as requiring `HandoffSpec` itself
to reject all conversation deadlines and all route-less conversations.

The controller did not accept this finding. The authoritative Stage 3 plan
makes `return_route` optional and constrains the live Bot and Desktop
constructors, not every internal conversation value. Those constructors pass
`deadline_at=None` and closed host-derived routes.

## Verification

The required 34-file Python gate collected and ran real tests:

```text
1,371 passed, 0 failed, 5 platform-specific skips
```

Desktop dependencies were unavailable in this detached lane. No live
credentials, inference, services, or external network were used. The worktree
remained detached and clean.
