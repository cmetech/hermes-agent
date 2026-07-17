---
name: workflow
description: "Operate portable workflows: list or show/describe automation and topology/diagram; run or schedule; inspect active/recent runs, status/health/progress, wait reasons, failure/retry diagnostics and next steps; provide input, handle approval, reject, resume, retry, reconcile, cancel, cleanup, or reset sessions."
version: 1.0.0
platforms: [darwin, linux, windows]
metadata:
  hermes:
    category: productivity
---

# Portable workflow operator

Before any command, resolve `PRODUCT_CLI` once from the active product. Read
`$HERMES_HOME/brand.json` when available and use its `slug` as the executable
name: LOOP24 uses `loop24`, OTTO uses `otto`, and a neutral Hermes Agent install
without a brand descriptor uses `hermes`. Replace `PRODUCT_CLI` in every command
template; do not execute it literally or use another product's executable.

Use `PRODUCT_CLI workflow` as the only workflow control plane. Never edit the run
store, session registry, snapshots, event journal, or graph order directly.
This is an edge capability: do not add a model tool or alter the system prompt.

If the command is unavailable, tell the user to run
`PRODUCT_CLI plugins enable workflow`; do not emulate the runtime with shell scripts.

## Scope and identity

For chat operations, derive the operator scope from the authenticated Hermes
profile plus current user and conversation identity. Pass that scope for every
`runs`, `status`, `events`, action, and `reset-sessions` operation. An explicit
run ID is not an authorization bypass. The local CLI remains profile-scoped.

Derive exactly one idempotency key from the authenticated conversation identity
and originating message/action identity. Re-delivery reports the existing run.
Desktop uses one UUID per Run-button action. Cron uses its schedule ID plus the
scheduled UTC fire instant. Never silently change a workflow's queue, allow, or
forbid overlap policy; explain the resulting queue/overlap/refusal first.

## Read before acting

Use JSON for every command. Use `PRODUCT_CLI workflow list --json` for catalog
questions, `show NAME --json` for description, requirements, approvals,
schedules, risk, and topology, and `runs`, `status`, or `events --tail 50` for
execution questions. Explain truthful graph progress, current nodes, elapsed
time, health, semantic progress, retry/approval/input state, sanitized errors,
artifacts, verification evidence, and `next_actions`. Never infer an ETA from
node counts. Never read raw run files, full prompts, hidden reasoning, secrets,
raw environment values, or unrestricted tool arguments.

Natural-language routing:

- “What workflows can I run?” → `list --json`.
- “What does X do?” → `show X --json`.
- “Which are running/waiting?” → `runs --status ... --json`.
- “How far is run X?”, “what is it waiting on?”, or “what happens next?” →
  `status X --json`; use `events X --tail 50 --json` for diagnostics.
- “Why did it fail?” or “is the node making progress?” → status plus events.
- Approval, rejection, input, resume, retry, reconcile, cancellation,
  abandonment, cleanup, and session reset map to the same-named CLI actions.

Never guess a run for a destructive action. If more than one run could match,
show a sanitized selection and ask which one. Confirm cancel, abandon, cleanup,
cross-scope session reset, and any outward action. Never approve an outward
action for the user.

## Inputs, trust, and starting work

Before `run` or schedule creation, obtain every mandatory input. Resolve each
user path, prove it is readable and of the declared file/directory/type and
within its size bound, then show the exact selected artifact list. Run doctor,
show compatibility, digest-bound trust/risk, requested tools/scripts/MCP,
providers/network/outward actions, execution environment, and resource limits.
Ask for confirmation when selection is ambiguous or outward impact requires it.
Never start work and then request a mandatory input.

Use `PRODUCT_CLI workflow run NAME --arguments ... --idempotency-key KEY --json`.
For background work, report the run ID and how notifications/continuation work;
do not hold a worker while queued, backing off, or awaiting the user.

For scheduling, snapshot every required input before saving the cron job, or
use an explicitly selected refresh-at-fire policy that revalidates before
admission. Schedule with `skills=["workflow"]` and Hermes' existing one-shot
`repeat=1` plumbing. The workflow RunStore is still the lifecycle authority.
`approvals.cron_mode` remains authoritative. A paused gate or inner tool
approval must deliver the run ID and continuation instructions, then release
the cron worker; later approval resumes exactly once.

## Topology by surface

Always explain `topology_text`. On classic CLI, Ink TUI, dashboard-embedded TUI,
messaging, unknown surfaces, and terminal output, stop there. Do not claim that
Mermaid source was rendered. On Desktop chat, output `topology_text` first as
the accessible/copyable form, then, only when `topology_mermaid` is non-null,
place its raw value inside exactly one `mermaid` fence. Never put a fence in
JSON and never omit the text fallback.

## Durable interactions

Use compare-and-set interaction identifiers/versions returned by status. For
`provide-input`, `approve`, `reject`, `resume`, `retry`, or `reconcile`, report
whether this action applied or another concurrent decision already won. A
rejection response is bounded workflow data and may trigger only the declared
bounded rework path. Cancellation never implies an outward action was undone.

Use dry-run cleanup first. Reset sessions only through
`PRODUCT_CLI workflow reset-sessions`; name the workflow, scope, and node, and obtain
explicit confirmation for cross-scope removal.
