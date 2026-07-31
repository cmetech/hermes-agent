# Portable package and language profiles

The backend contract is authoritative. Resolve `PRODUCT_CLI` as described in
the parent skill, then query it before authoring:

```bash
PRODUCT_CLI workflow schema --profile archon-2026-07 --json
```

Use `definition_schema` for portable YAML, `sidecar_schema` for the Hermes
companion, and `compatibility_codes` for field status and enforcement-phase
metadata. An enforcement phase is not a delivery date or promise. A field
annotated `x-hermes-status: blocking` is not available under that profile even
when its JSON shape is structurally valid.

## Package layout

```text
package/
├── workflows/name.yaml
├── workflows/name.hermes.yaml
├── commands/long-prompt.md
├── scripts/helper.py or helper.ts
└── mcp/server.yaml
```

Create resource directories only when referenced. Every resource must exist
inside the package and becomes part of its digest. Symlinks and escaping paths
are rejected. The `.hermes.yaml` companion is metadata, not a daemon, worker,
container, or second workflow process. It cannot change `nodes` or
`depends_on`, contain secret values, or declare trust.

## Profile choice

New packages default to:

```yaml
language_compatibility: archon-2026-07
```

An absent declaration preserves current `hermes-legacy` behavior. Use that
unversioned form for identical package bytes read by a pre-Phase-1 backend,
because older strict companion parsers reject the new field. An explicit
`hermes-legacy` declaration is suitable only when every reader recognizes
`language_compatibility`.

Phase 2 supports Archon AI `output_format` and `output_type`. `output_format`
is normalized as bounded Draft 2020-12 JSON Schema when the package loads, and
direct `$node.output.field` condition references are rejected only when every
closed schema branch proves that field path impossible. `output_type` is
an open, case-sensitive semantic label. A successful Archon output-producing
node publishes only its winning attempt; the same declaration under legacy
does not publish a typed artifact.

Under `hermes-legacy`, these same declarations retain their existing warning
semantics: `output_format` emits `legacy_output_format_post_validation`, and
`output_type` emits `legacy_output_type_not_published` because no typed artifact
is published.

The following declarations remain intentionally blocked under Archon:

| Field | Archon contract code | Archon enforcement phase | Current legacy meaning and warning code |
| --- | --- | ---: | --- |
| Node `idle_timeout` | `archon_idle_timeout_semantics_unavailable` | 3 | Positive seconds without reinterpretation; `legacy_idle_timeout_seconds`. Archon millisecond normalization is deferred to Phase 3. |
| Bash/script `timeout` | `archon_timeout_semantics_unavailable` | 3 | Positive seconds; `legacy_timeout_seconds`. |
| Node `retry` | `archon_retry_semantics_unavailable` | 3 | `max_attempts` counts total attempts and `delay_ms` is milliseconds; `legacy_retry_total_attempts`. |
| `maxBudgetUsd` | `archon_budget_enforcement_unavailable` | 5 | Provider-capability mapping only; not a portable guarantee. |
| Workflow/node `sandbox` | `archon_sandbox_enforcement_unavailable` | 5 | Provider/backend capability only; resource limits are not a sandbox. |

When a blocked declaration is requested, apply the two-choice recipe in the parent skill. Do not
rewrite the request into legacy silently. Companion `limits` and
`resource_limits` may be included inside the omit-and-remain-Archon choice to
tighten Hermes execution policy without claiming the blocked Archon semantics;
they are not a third choice.

Selecting effective or explicit legacy also produces the profile warning
`legacy_language_profile`. Phase numbers above only report the generated
contract's enforcement-phase metadata; they do not promise when support ships.

The generated catalog also publishes loader/profile failures that are not tied
to one declared inventory field: `workflow_language_profile_unsupported`,
`workflow_normalizer_version_unsupported`, legacy `unknown_top_level_field`,
and Archon `archon_unknown_top_level_field`. Preserve those codes when
reporting validation failures; do not collapse them to a generic parse error.

## Portable YAML shape

Required top-level fields are `name`, `description`, and a nonempty `nodes`
array. Each node has `id`, exactly one node-type payload, and optional
`depends_on`. Node types are `command`, `prompt`, `bash`, `script`, `loop`,
`approval`, and `cancel`. Graph and `$node.output` references must be upstream.

Common fields include `when`, `trigger_rule`, `context`, `idle_timeout`,
`always_run`, `output_type`, plus the deferred `retry`. AI nodes may use
provider/model selection, `persist_session`, `allowed_tools`, `denied_tools`,
`hooks`, `mcp`, `skills`, inline `agents`, reasoning controls, `systemPrompt`, and
fallbacks when doctor confirms the Hermes mapping. Tool aliases such as
`Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, and
`Agent` are resolved by doctor; unknown aliases block.

MCP and skills remain options on `command` and `prompt`. They are not node
kinds. Script nodes with `uv` or `bun` are existing execution behavior, not a
new structured-data node kind.

Treat `idle_timeout` as profile-sensitive even though it is structurally valid
on every node. Hermes legacy interprets the authored value as seconds and
emits `legacy_idle_timeout_seconds`. Under `archon-2026-07`, it blocks with
`archon_idle_timeout_semantics_unavailable`; do not convert or reinterpret the
value until Phase 3 supplies Archon millisecond normalization.

For a structured Archon output, declare a bounded local JSON Schema directly
on the AI node:

```yaml
name: structured-summary
description: Produce a validated summary and consume its answer
nodes:
  - id: summarize
    prompt: Summarize the evidence as JSON.
    output_type: Report/V1
    output_format:
      $schema: https://json-schema.org/draft/2020-12/schema
      type: object
      required: [answer]
      properties:
        answer: {type: string}
      additionalProperties: false
  - id: consume
    depends_on: [summarize]
    bash: 'printf "%s\n" "$summarize.output.answer"'
```

Put `language_compatibility: archon-2026-07` in the matching Hermes companion.
Do not place it in the portable definition.

`when: $summarize.output.answer != ''` is valid; a reference to an undeclared
field of a closed object is blocked with
`structured_output_field_impossible`. Open objects, optional declared fields,
and schema branches that permit the field remain admissible for runtime
evaluation.

Schemas must be self-contained Draft 2020-12 documents. `$ref` may point only
below the same document's `$defs`; reject external, absolute, unresolved, and
cyclic references, `$dynamicRef`, `$id`, `$anchor`, and `$dynamicAnchor`.
Preserve these contract ceilings when generating a schema:

| Dimension | Maximum |
| --- | ---: |
| Canonical schema bytes | 65,536 |
| Nesting depth | 32 |
| Traversed schema nodes | 4,096 |
| Object properties | 1,024 |
| Local references | 256 |
| One regex / all regex text | 1,024 / 16,384 bytes |
| One enum | 1,024 values |
| Canonical output | 500,000 bytes |

The optional `jsonschema` validator must be usable before an Archon structured
node can contact its provider; run
`python -m pip install 'hermes-agent[mcp]'` (or install the `all` extra) when
doctor reports it unavailable. Schemaless Archon workflows remain runnable
without that extra. Legacy intentionally keeps post-provider validation.

Hermes honors native structured output only for a trusted direct runtime with
an explicit declaration. Custom endpoints, aggregators, undeclared routes, and
community model metadata do not imply native support; a complete Hermes-managed
loop uses bounded prompt adaptation instead. A runtime that is unsupported or
drifts from the admitted decision fails before the provider request.

Prompt-adapted invalid output may receive at most one action-free repair. The
fresh one-turn repair has no original task/history, tools, hooks, MCP, skills,
agents, delegation, fallback, persistent session, or interactive prompts. It
is forbidden for outward or uncertain effects, cancellation, exhausted shared
budgets, or responses over 256,000 bytes. Diagnostics are capped at 16,384
bytes. Native validation misses are never repaired.

Accepted output becomes one complete canonical JSON value: UTF-8, compact,
Unicode-code-point-sorted object keys, finite numbers only, and no prose,
Markdown fence, extra JSON value, or trailing newline. Downstream consumers,
hashing, evidence, publication, and preview all use those same bytes.

For `output_type`, JSON publishes as `content.json` with `application/json`;
other UTF-8 output publishes as `content.md` with
`text/markdown; charset=utf-8`. Empty text is valid. The bundle and
`metadata.json` live under an opaque publication ID; metadata is capped at
65,536 bytes. Publication is claim-checked, atomic, journaled, and recoverable
only from the corroborated winning attempt. Evidence contains bounded metadata
rather than content. Authenticated preview is bounded to 64 KiB and download
uses the opaque ID.

Do not synthesize Phase 3 behavior. Phase 2 adds no timeout units/defaults,
retry counts/classes, strict missing-output/reference/field handling, condition
coercion/precedence, large Bash spill/quoting, persistent-session recovery,
`maxBudgetUsd` portability, new node kinds, `include`, or `loop_group`.

Script nodes require `runtime: uv` or `runtime: bun`; named scripts resolve
below `scripts/`. Named command templates resolve below `commands/`. MCP names
resolve below `mcp/` and start only within the isolated worker.

Use `context: shared` only with an unambiguous predecessor and identical cache
fingerprint. Otherwise use `fresh`.

OTTO V1 is not an alternate Hermes schema. Reject `steps`, `produces`,
`context_from`, `verify`, and `iterate` as workflow-authoring keys and translate
the desired behavior into a contract-derived `nodes` graph.

## Hermes companion policy

The generated companion inventory includes language, delivery, service,
retention, display tags, outward-action, execution-environment, overlap,
pause-lane, concurrency, lifecycle/resource-limit, required-secret, and
scheduling metadata. Unknown companion fields are rejected under both
profiles.

Input declarations use:

```yaml
delivery_defaults:
  inputs:
    evidence:
      kind: file       # text | file | directory | json
      required: true
      max_bytes: 1048576
overlap_policy: queue  # queue | allow | forbid
```

Input sources are admitted into immutable `inputs/` snapshots. A node needing a
file consumes that run snapshot, not the original source path. Package trust is
profile-owned and digest-bound outside the package.
