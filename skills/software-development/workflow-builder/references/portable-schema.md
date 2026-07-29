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

Phase 1 intentionally blocks these Archon declarations:

| Field | Archon contract code | Archon enforcement phase | Current legacy meaning and warning code |
| --- | --- | ---: | --- |
| AI `output_format` | `archon_output_format_unavailable` | 2 | Post-generation JSON Schema validation; `legacy_output_format_post_validation`. |
| Any `output_type` | `archon_output_type_unavailable` | 2 | Accepted but no typed artifact is published; `legacy_output_type_not_published`. |
| Node `idle_timeout` | `archon_idle_timeout_semantics_unavailable` | 3 | Positive seconds without reinterpretation; `legacy_idle_timeout_seconds`. Archon millisecond normalization is deferred to Phase 3. |
| Bash/script `timeout` | `archon_timeout_semantics_unavailable` | 3 | Positive seconds; `legacy_timeout_seconds`. |
| Node `retry` | `archon_retry_semantics_unavailable` | 3 | `max_attempts` counts total attempts and `delay_ms` is milliseconds; `legacy_retry_total_attempts`. |
| `maxBudgetUsd` | `archon_budget_enforcement_unavailable` | 5 | Provider-capability mapping only; not a portable guarantee. |
| Workflow/node `sandbox` | `archon_sandbox_enforcement_unavailable` | 5 | Provider/backend capability only; resource limits are not a sandbox. |

When one is requested, apply the two-choice recipe in the parent skill. Do not
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
`always_run`, plus the deferred `retry` and `output_type`. AI nodes may use
provider/model selection, `persist_session`, `allowed_tools`, `denied_tools`,
`hooks`, `mcp`, `skills`, inline `agents`, reasoning controls, `systemPrompt`, and
fallbacks when doctor confirms the Hermes mapping. Tool aliases such as
`Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, and
`Agent` are resolved by doctor; unknown aliases block.

Treat `idle_timeout` as profile-sensitive even though it is structurally valid
on every node. Hermes legacy interprets the authored value as seconds and
emits `legacy_idle_timeout_seconds`. Under `archon-2026-07`, it blocks with
`archon_idle_timeout_semantics_unavailable`; do not convert or reinterpret the
value until Phase 3 supplies Archon millisecond normalization.

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
