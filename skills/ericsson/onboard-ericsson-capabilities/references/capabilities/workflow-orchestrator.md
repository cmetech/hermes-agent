---
id: workflow-orchestrator
display_name: Workflow Orchestrator
aliases: [workflow runner, workflow status, deterministic automation]
goals:
  - Show me which workflows are available.
  - Run a validated Ericsson workflow.
  - Resume or inspect an interrupted workflow run.
maturity: available
recommendation_eligible: true
source_flows: []
implementation:
  skills: [skills/ericsson/workflow-orchestrator]
  plugins: []
  mcp_servers: []
  workflows: []
  tools: []
platforms: [macos, linux, windows]
configuration: []
reads: [workflow YAML, workflow inputs, node outputs and state through workflow control]
writes: [unique workflow run state and declared node outputs, workflow-specific approved side effects]
artifacts: [workflow state, declared node output files, optional Kanban mirror]
demonstrations: [synthetic-offline]
troubleshooting: [failed node, interrupted side effect, stalled run, changed workflow, rejected or cancelled run]
---

# Workflow Orchestrator

## What it solves

Runs validated workflow YAML deterministically while preserving order, conditions,
approvals, state, artifacts, and safe recovery.

## Try saying

- “What Ericsson workflows do I have?”
- “Run the inbox-digest workflow.”
- “Resume this interrupted workflow run safely.”

Follow up with workflow/input filters, ask to preview an approval, choose output
format/destination through the workflow, inspect exclusions/warnings, or resume.

## Questions

Expect only missing workflow inputs or an approval decision. The agent does not ask
again for inputs already recorded in the run.

## Reads and writes

The controller reads YAML and run state and alone writes `state.json`. External
effects belong to marked workflow nodes and follow their explicit approval gates.

## Readiness

List and validate the workflow first, then verify each referenced tool/dependency.
Structural readiness does not imply that a domain integration is authenticated.

## Demonstration

Safely demonstrate list/validate or a fictional no-side-effect lifecycle. Explain
expected states and artifact destination before starting.

## Artifacts

Inspect state and declared outputs in the unique run directory. Use control commands,
never direct edits, to understand failures, exclusions, warnings, and completion.

## Troubleshooting

Report failed, interrupted, in-progress, stalled, rejected, and cancelled distinctly.
Resume through the controller; reconcile uncertain side effects before forced rerun.
