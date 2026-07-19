---
name: workflow
description: Use when operating portable workflows, inspecting run health or evidence, handling durable interactions, recovering stalled work, or managing workflow retention.
version: 1.1.0
platforms: [darwin, linux, windows]
metadata:
  hermes:
    category: productivity
---

<objective>
Operate the workflow plugin through its deterministic CLI contract without
creating duplicate runs, guessing syntax, bypassing human gates, or treating a
UI/client projection as workflow authority.
</objective>

<quick_start>
Resolve `PRODUCT_CLI` once. Read the active profile's `brand.json` slug when
present; otherwise use `hermes` only for a neutral installation. Never execute
the literal token `PRODUCT_CLI`.

Before mutation, run `PRODUCT_CLI workflow doctor NAME --mode background
--compat-report --json` (or `--mode foreground` only for an explicitly requested
local run). Read the schema-versioned envelope and use
`result.command_contract` as the exact argv authority. Do not invent flags from
memory.
</quick_start>

<essential_principles>
- Use `PRODUCT_CLI workflow` only. Never edit RunStore, journals, snapshots,
  sessions, queue order, or evidence files.
- Treat stdout JSON as authoritative: dispatch on `schema_version`, `ok`, and
  `error.code`; use the process exit only as its documented coarse category.
- Derive one stable `intent_key` from the source's durable message/action ID.
  Reuse it for every retry of that semantic start. A new run requires a new
  intentional key.
- Execute one mutating command at a time. Never probe mutating variants in
  parallel, use `|| true`, pipe `yes`, or mask an exit status used for a
  decision.
- `--no-wait` is background admission, not best effort. On
  `coordinator_unavailable`, report that no run was accepted. Use the published
  foreground argv only when the user explicitly chooses local foreground work.
- Stop at approval/input/reconciliation gates. Never approve an outward action
  for the user. Use the returned run ID, interaction ID, and state version.
- Continue polling only while status shows semantic progress, a future retry,
  an unresolved human gate, or a valid durable wake. Stop and report
  `stalled`, `coordinator_unavailable`, conflict, or terminal state.
- Report observed events, outputs, artifacts, hashes, and verification facts.
  Never promise success, infer an ETA from node counts, or expose prompts,
  reasoning, secrets, raw environment, or unrestricted arguments.
</essential_principles>

<identity_and_scope>
Chat provenance from a shell-spawned CLI is a profile-local administrative
claim, not authenticated remote identity. Do not invent or pass an operator
scope flag. Remote/Desktop authorization must come from its verified server
boundary; a run ID alone is never an authorization bypass.
</identity_and_scope>

<process>
1. Inspect `list/show` for catalog questions and doctor/preflight before starts.
2. Gather and validate mandatory inputs before mutation; explain trust, digest,
   tools/scripts/MCP, network/outward effects, execution mode, and limits.
3. Render exactly one argv from `command_contract`, substituting the correct
   identifier kind. Showcase ID selects a showcase; general lifecycle commands
   always use the returned run ID.
4. Execute once and interpret the JSON envelope. Preserve the intent key.
5. At a gate, present the durable interaction and wait. For a user decision,
   refresh status and pass its current interaction ID and state version.
6. For cleanup, inspect impact first; execute only with the returned bound
   confirmation token after explicit confirmation.
</process>

<red_flags>
- Trying several run/approve syntaxes to see which works
- Generating a new idempotency key after timeout or transport failure
- Polling a runnable run after its coordinator is unavailable
- Using a showcase ID with `status`, `approve`, `reject`, or `events`
- Treating expected showcase failure as overall workflow success
- Continuing past a human gate without the user's decision
</red_flags>

<success_criteria>
- Exact syntax came from the current runtime `command_contract`.
- One semantic start produced one durable run ID.
- Every mutation used current authoritative identifiers and ran serially.
- Human gates stopped for the user; unavailable/stalled states stopped polling.
- The final report distinguishes lifecycle outcome from evidence-backed claims.
</success_criteria>
