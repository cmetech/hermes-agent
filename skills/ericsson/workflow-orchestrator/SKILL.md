---
name: workflow-orchestrator
description: "Run, monitor, approve, and recover deterministic Ericsson workflows defined as YAML (Langflow-replacement orchestrations). Use when the user asks to run a workflow, schedule one, check the status or results of a workflow run, approve/reject a waiting run, or resume/cancel a run. Workflows live in $HERMES_HOME/workflows/."
version: 1.0.0
author: Ericsson (cmetech)
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [Ericsson, Workflows, Orchestration, Automation]
    related_skills: [workflow-builder]
---

# Workflow Orchestrator

You execute workflow YAML files deterministically. The control script owns
ordering, conditions, approvals, and state — **you NEVER decide step order,
skip steps, or re-run steps yourself**. You supply the intelligence inside
each node.

Control script (all commands print one JSON object; non-zero exit = error):

    CTL="${HERMES_SKILL_DIR}/scripts/workflow_ctl.py"
    python3 "$CTL" <command> ...

## Running a workflow

1. Find it: `python3 "$CTL" list` (workflows library + recent runs).
2. Validate if newly written: `python3 "$CTL" validate <path>`.
3. Start: `python3 "$CTL" start <path> [--input key=value ...]` → note `run_id`
   and the `report` block.
4. **Kanban mirror (optional):** if `report.kanban` is `"on"`, or `"auto"` AND
   the kanban tools (kanban_create etc.) are available to you, create a kanban
   task titled `workflow: <name> <run_id>`, then
   `python3 "$CTL" set-kanban --run <run_id> --task-id <id>`. If kanban is
   `"off"` or the tools are unavailable under `"auto"`, silently continue —
   state.json is always the source of truth.
5. Loop:
   - `python3 "$CTL" next --run <run_id>`
   - `action == "execute"` → do the node's work following `node.prompt`
     exactly (kind `script`: run `node.command` verbatim in the run dir and
     capture stdout). Write any declared `node.output` file into `run_dir`.
     Then `python3 "$CTL" record --run <run_id> --node <id> --status ok
     --output <file> --summary "<one line>"`. On failure:
     `record ... --status failed --error "<what happened>"` and STOP the loop.
   - `action == "wait_approval"` → see Approvals below. STOP the loop.
   - `action == "done"` → finish (see Finishing).
   - `action == "failed"` → report the failed node and its error; suggest
     `resume`. STOP.
   - `action == "in_progress"` → another session is executing that node; say so and STOP. `action == "interrupted"` → the node crashed mid-run; relay the JSON `hint` (it names the resume command). STOP.
   - `action == "stalled"` → nothing is runnable; run `status --run <run_id>` and relay its per-node picture with the JSON hint. STOP.
   - `action == "rejected"` / `"cancelled"` → the run is closed. For a rejection, `status --run <run_id>` shows the reason in the gate node's `approval` field; a cancel records no reason. Report that and STOP.
   - If a kanban task is set, add a short comment after each `record`.
6. Never edit state.json, never re-order nodes, never improvise on a
   workflow_ctl error — report the error verbatim and stop.

## Approvals (human-in-the-loop)

- Interactive session: present the material named in the approval `message`
  (show the relevant output files), ask the user to approve or reject, then
  `python3 "$CTL" approve --run <id> [--response "..."]` or
  `reject --run <id> --reason "..."` and continue the loop.
- Background run (cron): you cannot wait. Deliver a message through the job's
  normal channel: what run is waiting, what to review, and that the user can
  say "approve run <id>" or "reject run <id> because ...". Then stop; the
  approving session continues the run later.

## Status questions

"How did my <workflow> run go?" / "what workflows do I have?" →
`python3 "$CTL" status --run <id>` / `status --workflow <name>` /
`list`. Summarize: status, per-node results, artifact files (link them),
and errors. This works whether or not kanban was enabled.

## Recovery

- Failed run: `python3 "$CTL" resume --run <id>` re-runs the failed node.
- Crashed mid-node (`interrupted`): `resume --run <id>`. If the node has
  `side_effects: true` the script refuses — confirm with the user whether the
  action (e.g. an email) already happened, then either
  `resume --run <id> --force-node <node>` or `skip --run <id> --node <node>
  --reason "..."`.
- Workflow file edited mid-run: `resume --run <id> --accept-changes` (or
  `restart <path>` for a fresh run).
- Clean slate: `cancel --run <id>`, `clean --run <id>`,
  `clean --workflow <name> --keep 5`, or `clean --all --older-than 30d`.

## Finishing

Summarize what ran, per-node one-liners, and list artifact file paths in the
run dir so they are easy to open. If a kanban task is set, mark it complete
with a closing comment. For background runs the final summary goes out via
the job's delivery channel.

## Pitfalls

- Do not parse state.json yourself — always go through workflow_ctl.
- Do not retry a failed node without `resume` (it enforces side-effect safety).
- The full YAML schema is in `${HERMES_SKILL_DIR}/references/workflow-schema.md`
  (read it when writing or debugging a workflow file).
