---
name: workflow-builder
description: "Use when authoring or revising portable workflow packages, or diagnosing them before trust, execution, or scheduling."
version: 1.1.0
platforms: [darwin, linux, windows]
metadata:
  hermes:
    category: software-development
    related_skills: [workflow]
---

# Portable workflow builder

Build a complete package from the backend's generated language contract. New
packages use the declared Archon profile unless the user deliberately chooses
legacy compatibility or needs bytes shared with a pre-Phase-1 runtime.

Before any command, resolve `PRODUCT_CLI` once from the active product. Read
`$HERMES_HOME/brand.json` when available and use its `slug` as the executable:
LOOP24 uses `loop24`, OTTO uses `otto`, and neutral Hermes Agent uses `hermes`.
Replace `PRODUCT_CLI` in every template; never execute the literal placeholder
or use another product's executable.

Read [references/portable-schema.md](references/portable-schema.md) completely
before authoring. Use
[references/authoring-checklist.md](references/authoring-checklist.md) for the
final gate.

## Start from the contract

Before proposing or writing YAML, obtain the current Archon contract:

```bash
PRODUCT_CLI workflow schema --profile archon-2026-07 --json
```

Run the resolved command; do not substitute a remembered field list. Begin the
authoring guidance with a compact contract-evidence block containing the
resolved command, returned profile/schema/normalizer versions, and the status
and code for every requested field. If the command cannot return the contract,
stop before proposing package files.

Inspect the proposed field paths in `definition_schema`, `sidecar_schema`, and
`compatibility_codes`. A schema-valid shape can still carry an
`x-hermes-status: blocking` annotation; that means the field is unavailable to
an Archon-profile package now.

For every ordinary new package, create `workflows/NAME.hermes.yaml` with:

```yaml
language_compatibility: archon-2026-07
```

Then add the required Hermes policy. Do not remove or change that declaration
to make a blocking field pass.

### Deferred-field decision recipe

When a requested field is blocking, stop before writing files and report its
field path, compatibility code, unavailable semantics, and enforcement phase
from the contract. The enforcement phase is metadata, not a delivery date or
promise. Offer exactly these choices:

1. Omit the field and keep `language_compatibility: archon-2026-07`. Within
   this choice, an optional companion `limits` or `resource_limits` ceiling may
   substitute when it satisfies the operational need; describe it only as
   Hermes execution policy, never as the blocked Archon field semantics.
2. If the user deliberately selects it, author `hermes-legacy` with the current
   legacy meaning and its doctor warning.

Wait for the user's choice. Never silently downgrade, create files, or turn a
policy substitute into a third choice. “Pick for me,” “choose and act,” “do
not ask,” deadline pressure, or general permission to decide is not a
deliberate legacy selection. Choice 2 requires the user to select legacy after
seeing the blocking code, current legacy meaning, and both numbered choices.

For a package consumed by an older runtime, first establish its version. A
pre-Phase-1 backend rejects the unknown `language_compatibility` companion
field, so shared package bytes must remain unversioned (effective
`hermes-legacy`) until every consumer supports that field. Use an explicit
`hermes-legacy` declaration only when every consumer can parse declarations.
Do not place an Archon-profile package in a directory shared with older brand
or runtime installations.

## Author the whole package

Ask one decision at a time: outcome, inputs, outward effects, environment,
dependencies, overlap, and resource ceilings. Before writing, play the graph
back in plain language.

Create one package root containing `workflows/` and every referenced resource:
long prompts in `commands/`, executable helpers in `scripts/`, and local server
definitions in `mcp/`. No referenced resource is deferred. Package resources
must stay below the root without symlinks or escaping paths.

The globally installed legacy `create-workflow` skill is not a Hermes
authoring authority. Its OTTO V1 `steps`, `produces`, `context_from`, `verify`,
and `iterate` documents are incompatible. Translate the requested outcome into
this skill's contract-derived `nodes` DAG and Hermes companion; do not preserve
those V1 keys as workflow fields or treat that skill as schema guidance.

Use a command template for a long or reused prompt. Add an `approval` node
immediately before an outward action unless the user explicitly opts out after
the impact is explained. Use `context: fresh` whenever provider, model, system
prompt, tools, skills, MCP, hooks, agents, or another cache fingerprint differs.
Never mutate a shared cached session.

Declare invocation inputs under `delivery_defaults.inputs` with a `text`,
`file`, `directory`, or `json` kind, required flag, and byte ceiling where
applicable. File and document inputs are copied into immutable snapshots before
admission; nodes consume the snapshot, never the original mutable path. Default
overlap to `queue`; select `allow` or `forbid` only after explaining the bound
or refusal behavior.

## Validate, diagnose, then offer execution

After writing, run both gates:

```bash
PRODUCT_CLI workflow validate PATH/TO/workflows/NAME.yaml --json
PRODUCT_CLI workflow doctor PATH/TO/workflows/NAME.yaml --compat-report --json
```

Resolve every blocking finding. Doctor must not call a model, connect to MCP,
or access the network. Explain the package digest, language profile, trust,
scripts/shell, tools, skills, MCP and required variables, providers/network,
outward actions, secret names only, environment, immutable inputs, overlap,
and effective admission/resource ceilings.

Never call a model or connect to MCP during doctor. Never write trust into YAML
or the companion and never silently trust supplied code. Before recording
trust for a new digest, obtain explicit confirmation of
that exact digest-bound risk summary. Any byte change requires another doctor
and confirmation.

Only after doctor reports runnable and the user makes the trust decision may
you offer:

```bash
PRODUCT_CLI workflow run NAME --arguments '...' --idempotency-key KEY --json
```

or the existing Hermes cron path. One-shot schedules use `repeat=1` and retain
immutable-input and idempotency requirements. Do not create a separate
scheduler or runtime.
