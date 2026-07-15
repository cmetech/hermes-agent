---
name: workflow-builder
description: "Interview the user to design a new deterministic Ericsson workflow (Langflow-style orchestration) on top of the available Ericsson tools, then write and validate the workflow YAML and offer to run or schedule it. Use when the user wants to create, design, or change an automation/orchestration/workflow."
version: 1.0.0
author: Ericsson (cmetech)
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [Ericsson, Workflows, Builder, Automation]
    related_skills: [workflow-orchestrator]
---

# Workflow Builder

You turn an orchestration idea into a validated workflow YAML the
workflow-orchestrator skill can execute. Read the schema FIRST:
`${HERMES_SKILL_DIR}/../workflow-orchestrator/references/workflow-schema.md`.

## Process

1. **Understand the idea.** Restate it in one sentence; confirm.
2. **Interview — one question at a time** (see
   `${HERMES_SKILL_DIR}/references/interview-guide.md`). Cover: goal and
   trigger (on-demand vs scheduled), which tools each step uses (record every
   tool node's exact runtime names in its required `tools` list; offer the
   Ericsson toolsets and MCP tools you can see), step order and data handoff
   (each step's output file), conditions/branches, human approval gates
   (anything outward-facing — email/Teams posts/Jira comments — should have
   one unless the user opts out), inputs with defaults, delivery target.
3. **Play it back in plain human terms** — a numbered list: "1. Fetch …
   2. Summarize … 3. Wait for your approval … 4. Email it to you". Iterate
   until the user confirms.
4. **Write the YAML** to `$HERMES_HOME/workflows/<name>.yml` following the
   schema exactly. Mark outward-facing nodes `side_effects: true`. Keep node
   prompts imperative and self-contained (the orchestrator executes them one
   at a time with no interview context).
5. **Validate**: run
   `python3 ${HERMES_SKILL_DIR}/../workflow-orchestrator/scripts/workflow_ctl.py validate <file>`
   and fix until it reports ok. Show the user any warnings (e.g. unset env vars).
6. **Offer next steps**: run it now (load the workflow-orchestrator skill and
   start it), or schedule it (create a cron job whose prompt is:
   `Run the workflow <name> using the workflow-orchestrator skill.` with the
   user's schedule and delivery channel).

## Pitfalls

- Never invent tools — only reference toolsets/MCP tools that actually exist.
- Never write `$inputs.x` literally inside a `prompt` or `command` string — it
  is NOT interpolated. `$inputs.x` has meaning only in `when:` conditions;
  in prompts, reference inputs by name in plain language (the executing agent
  sees their values in the `inputs` field of `workflow_ctl next`/`status`).
- Never write v2 schema features (loops, retries, parallel, on_reject).
- One node = one responsibility; prefer 3–6 nodes.
- The YAML filename must equal the workflow `name` (slug).
