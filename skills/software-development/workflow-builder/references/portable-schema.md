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

### Current normalizer selection

New and default `archon-2026-07` contracts and admissions use normalizer v4.
Current `hermes-legacy` contracts use v2. Explicit and sealed v1, v2, and v3
remain supported compatibility inputs, and resume preserves their pinned
semantics.

<!-- workflow-language-version-selection -->
```json
{
  "current_normalizer_by_profile": {
    "hermes-legacy": 2,
    "archon-2026-07": 4
  },
  "supported_normalizer_versions": [1, 2, 3, 4, 5]
}
```

Phase 3 supports Archon AI `output_format` and `output_type`. `output_format`
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

Phase 3 supports node timeout and retry authoring under Archon:

- Bash/script `timeout` is positive finite milliseconds; omission requests
  120,000 ms before the sealed subprocess ceiling is applied.
- AI `idle_timeout` is positive finite milliseconds; omission uses the sealed
  AI idle ceiling.
- `retry.max_attempts` counts retries after the initial attempt. AI nodes
  default to two retries; deterministic Bash/script nodes default to none.
- Every output reference names a direct dependency. Conditions use strict
  typed scalar comparisons and fail on syntax, missing-value, or type errors.

The following declarations remain intentionally blocked under Archon:

| Field | Archon contract code | Archon enforcement phase | Current legacy meaning and warning code |
| --- | --- | ---: | --- |
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

### Normalizer v4

Normal authoring contracts and new Archon admissions select normalizer v4. An
installed integration or contract test retrieves its authoritative inventory
with the ordinary default call:

```python
from plugins.workflow.language_schema import workflow_authoring_contract
from plugins.workflow.models import WorkflowLanguageProfile

contract = workflow_authoring_contract(
    WorkflowLanguageProfile.ARCHON_2026_07,
)
```

Compile, validate, trust, and admit that exact contract, and pin v4 in the
immutable run snapshot. Pass `normalizer_version=1`, `2`, or `3` only to read or
operate an explicit historical contract; those selections retain compatibility
and do not change the current Archon default.

V4 adds `include` as a compile-only source directive. It is not executable and
its only fields are `id`, literal `include`, optional `depends_on`, and optional
`trigger_rule`. Root policy is authoritative. Ignored child companions remain
authenticated package bytes, including their required-secret declarations,
limits, and services. The
compiler expands depth-first, namespaces child nodes, and removes every include
before scheduling.

The complete root closure is bounded to include depth 3, 64 distinct selected
dependencies, 512 executable nodes, 4,096 edges, 2 MiB of selected source, 2
MiB of expanded definition, 512 authenticated files, 1 MiB per authenticated
file, and 8 MiB total authenticated bytes.

An include's entries are nodes without internal dependencies. Its sinks are
nodes without internal consumers. Parent dependencies connect to all entries;
downstream dependencies wait for all sinks. An include output alias selects the
first sink in definition order. It never exposes a deep child or a completion-
ordered result.

Every included named command, script, and MCP resource remains bound to its
logical child package and the sealed snapshot. The composite digest and
dependency manifest cover that origin. Source deletion after admission does not
change execution or resume; a snapshot mismatch fails closed. Diagnostics use
bounded logical include provenance rather than host paths. Review warnings and
all stable include codes before trust.

A v4 loop has exactly one of inline `prompt` or named `command`. A named command
body is resolved and sealed before execution. Effective interactivity requires
both workflow and loop `interactive`; the loop also supplies `gate_message`.
`signal_completes` defaults false for that effective interactive case and true
otherwise. False is invalid without an operator path.

When a completion signal needs confirmation, reuse the existing wire actions:
`status`, `events`, `approve`, `provide-input`, and `cancel`. Before the final
iteration, mutation choices are approve, provide-input, or cancel. The final
iteration removes provide-input and offers approve or cancel. Approval accepts
the sealed result without re-running the provider; feedback permits the next
bounded iteration.

Runtime child workflows, `include.with`, and `loop_group` are deliberate later
Archon omissions. Do not synthesize them from v4. Portable `maxBudgetUsd` and
sandbox guarantees also remain blocked.

## Portable YAML shape

Required top-level fields are `name`, `description`, and a nonempty `nodes`
array. Each node has `id`, exactly one node-type payload, and optional
`depends_on`. Node types are `command`, `prompt`, `bash`, `script`, `loop`,
`approval`, and `cancel`. Graph and `$node.output` references must be upstream.

Common fields include `when`, `trigger_rule`, `context`, `idle_timeout`,
`always_run`, `output_type`, and supported `retry`. AI nodes may use
provider/model selection, `persist_session`, `allowed_tools`, `denied_tools`,
`hooks`, `mcp`, `skills`, inline `agents`, reasoning controls, `systemPrompt`, and
fallbacks when doctor confirms the Hermes mapping. Tool aliases such as
`Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, and
`Agent` are resolved by doctor; unknown aliases block.

MCP and skills remain options on `command` and `prompt`. They are not node
kinds. Script nodes with `uv` or `bun` are existing execution behavior, not a
new structured-data node kind.

Treat timeout and retry as profile-sensitive. Hermes legacy timeout values are
seconds and legacy `max_attempts` counts total attempts. Archon timeout values
are milliseconds and Archon `max_attempts` counts retries after the initial
attempt. Always consult the generated field metadata before converting.

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

Phase 3 Bash values through the 32,768-byte UTF-8 boundary render inline;
larger values are consumed as bounded contents, never pathnames. Only ordinary
authenticated token contexts are rewritten. Escaped/comment references stay
literal, and ambiguous expansions fail before launch. The complete rendered
command, including descriptor prologue and every inline replacement, is capped
at 98,304 UTF-8 bytes before materialization or launch.

Only a confirmed missing cross-run session may select one fresh execution,
with zero provider attempts before recovery. Same-run missing context fails;
storage errors remain operational failures; fingerprint mismatch retains its
warning/fresh behavior. Use `workflow doctor`, generated
`compatibility_codes`, and Run Inspector recovery evidence as the operator
authority.

MCP and skills remain options, not node kinds. V4 adds compile-only
includes and the sealed ordinary-loop contract above; it adds no executable
node kind. Do not synthesize runtime child workflows, include parameters,
`loop_group`, Phase 5 `maxBudgetUsd`, sandbox, or provider-portability
guarantees.

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
