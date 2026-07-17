---
id: workflow-builder
display_name: Workflow Builder
aliases: [build an automation, workflow designer, YAML workflow creation]
goals:
  - Help me build a repeatable workflow for this task.
  - Design an Outlook digest workflow with an approval before email.
  - Change my workflow to post to Teams only when results exist.
maturity: available
recommendation_eligible: true
source_flows: []
implementation:
  skills: [skills/software-development/workflow-builder]
  plugins: [plugins/workflow]
  mcp_servers: []
  workflows: []
  tools: []
platforms: [macos, linux, windows]
configuration: []
reads: [user workflow goal, workflow schema, currently visible tools and MCP capabilities]
writes: [new or explicitly approved updated workflow YAML]
artifacts: [validated workflow YAML]
demonstrations: [synthetic-offline]
troubleshooting: [unknown tool, unsupported schema feature, invalid input reference, filename mismatch, validation warning]
---

# Workflow Builder

## What it solves

Interviews the user one question at a time, plays back a proposed deterministic
automation, writes confirmed YAML, and validates it for the orchestrator.

## Try saying

- “Help me build a repeatable workflow for this task.”
- “Design an on-demand Jira summary with an approval before email.”
- “Change this workflow to post to Teams only when results exist.”

Follow up with source filters, request the plain-language preview, choose output
format/destination, ask about exclusions/warnings, or request a safe fresh rerun.

## Questions

Expect one question at a time about goal, trigger, tools, ordering, outputs,
conditions, approvals, inputs, delivery, and slug; known choices are reused.

## Reads and writes

It reads the portable schema and visible capabilities. After playback confirmation
it writes a complete package and runs `hermes workflow doctor PACKAGE --json`.

## Readiness

The builder itself needs no key, but it must reference only tools actually visible.
Validation warnings and each downstream capability's readiness remain separate.

## Demonstration

Build and validate a fictional no-side-effect workflow at an approved destination;
do not execute outward actions simply to prove the builder works.

## Artifacts

Inspect the digest-bound package, especially resources, approvals, outward actions,
immutable inputs, overlap policy, resource ceilings, and warnings before run or cron.

## Troubleshooting

Remove invented tools and unsupported fields. Fix every coded doctor finding, then
rerun doctor and review the new digest before trust, execution, or scheduling.
