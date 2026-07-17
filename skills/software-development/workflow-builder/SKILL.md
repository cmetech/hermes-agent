---
name: workflow-builder
description: "Author complete portable workflow packages and diagnose them before trust, execution, or scheduling."
version: 1.0.0
platforms: [darwin, linux, windows]
metadata:
  hermes:
    category: software-development
    related_skills: [workflow]
---

# Portable workflow builder

Build a complete neutral package in the Archon-compatible YAML shape while
keeping execution policy in its Hermes sidecar. Work one decision at a time.
First ask what outcome is wanted, then inputs, outward effects, execution
environment, dependencies, overlap behavior, and resource ceilings. Play the
resulting graph back in plain language before writing files.

Read [references/portable-schema.md](references/portable-schema.md) before
authoring and use
[references/authoring-checklist.md](references/authoring-checklist.md) for the
final gate.

## Author the whole package

Create one package root containing `workflows/` and every referenced resource:
long prompts belong in `commands/`, executable helpers in `scripts/`, and local
server definitions in `mcp/`. Do not leave a resource reference for a later
step. Use published Archon field names and tool aliases exactly; never guess an
unknown alias or add unsupported fields. Keep vendor policy outside portable
YAML.

Use a command template when a prompt is long or reused. Insert an `approval`
node immediately before an outward action unless the user explicitly opts out
after the impact is explained. Use `context: fresh` whenever provider, model,
system prompt, tool scope, skill set, MCP set, hooks, agents, or another cache
fingerprint field differs. Never mutate a shared cached session.

Declare required invocation inputs under the neutral sidecar's
`delivery_defaults.inputs`. Give each a `text`, `file`, `directory`, or `json`
kind, whether it is required, and a byte ceiling where applicable. File and
document paths are copied into immutable snapshots before admission; nodes must
not reopen the original mutable path. Default overlap to `queue`; use `allow`
or `forbid` only after explaining bounded overlap or refusal.

## Diagnose before offering execution

Run:

```bash
hermes workflow doctor PATH/TO/workflows/NAME.yaml --json
```

Never call a model or connect to MCP during doctor. Resolve every blocking
finding first. Show the doctor result in plain language: package digest,
trust state, scripts/shell, tools, skills, MCP servers and required variables,
providers/network access, outward actions, secrets by name only, execution
environment, immutable inputs, overlap policy, and effective admission and
resource ceilings.

Never write trust into YAML or a sidecar. Never silently trust manually
supplied code. Before recording trust for any newly authored package digest,
obtain explicit confirmation of that exact digest-bound risk summary. A byte
change requires a new doctor and confirmation.

Only after doctor says the package is runnable and the user has made the trust
decision may you offer:

```bash
hermes workflow run NAME --arguments '...' --idempotency-key KEY --json
```

or Hermes' existing cron path. Scheduling must use a one-shot `repeat=1` job
when one-shot behavior is requested and must preserve immutable-input and
idempotency requirements. Do not create a scheduler or runtime outside the
workflow plugin.
