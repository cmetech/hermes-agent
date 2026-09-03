# Agent Handoff Stage 3 adversarial code review — Claude

**Date:** 2026-09-03

**Reviewer:** Claude Code 2.1.257, `claude-opus-5`, high effort

**Immutable candidate:** `2affe5e02307475274cb3d72c24af59f72682945`

**Tree:** `390603dadb2ff6cb8373e4751b6b097bea0ce6b6`

**Verdict:** **BLOCK**

This is a normalized capture of the independent review returned to stdout.
Formatting is condensed; the findings and dispositions are unchanged. The
review ran in its own clean detached worktree and did not read the Codex lane
or any reconciliation.

## Findings

### IMPORTANT — HOFF-W01: native Windows local compatibility delivery failed closed

The reviewer reported that native Windows Gateway/Desktop friendly local sends
could return `local_cli_lock_unavailable` because Stage 1 supplies only the
POSIX destination lock.

The controller did not accept this as a Stage 3 defect. Native Windows
destination locking is explicitly deferred to Stage 5, and the accepted
boundary requires the existing POSIX-only transport to fail closed rather than
send without serialization.

### IMPORTANT — HOFF-G01: a gateway return could be re-injected before persistence

The gateway released both its durable delivery claim and its in-process
identity immediately after an asynchronous adapter accepted a return. Adapter
acceptance precedes transcript persistence, so a supervisor replay in that
window could submit the same delivery to the model again.

The proposed root fix was to retain the producer identity until the transcript
receipt exists, while releasing the durable lease so restart reconciliation
remains authoritative.

### MINOR — HOFF-P01: legacy peer CLI stopped honoring ambient transport policy

The shared Runs client deliberately bypasses ambient proxies for controlled
handoffs. Reusing that default from `hermes peer dm` also changed the legacy
CLI's established installed-opener/proxy behavior.

The proposed root fix was a closed opt-in on the shared client used only by the
legacy peer CLI. Redirect credential stripping must remain mandatory in both
policies.

## Verification

After one load-sensitive unchanged setup race, the exact Python rerun reported:

```text
1,371 passed, 0 failed, 5 platform-specific skips
```

The lane also reported:

```text
Desktop focused: 66 passed, 0 failed; typecheck passed
Installed wheel: 2 passed, 0 failed
```

No live credentials, inference, services, or external network were used. The
worktree remained detached and clean.
