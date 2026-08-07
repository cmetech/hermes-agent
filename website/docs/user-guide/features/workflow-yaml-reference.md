---
sidebar_position: 14
title: "Workflow YAML reference"
description: "Author profile-aware portable workflows with current Phase 5 provider semantics"
---

# Workflow YAML reference

Hermes reads a portable workflow definition and an optional Hermes companion
file. The portable file describes the DAG. The companion selects the language
profile and adds Hermes admission and execution policy.

This page documents the staged normalizer v5 contract for provider portability.
Until the final activation gate, new Archon admission remains on v4; explicit v5
is available only for compatibility validation. V5 inherits Phase 4 ordinary
loops and immutable compile-time includes plus the Phase 3 timeout, retry,
structured-output, and reference semantics. Older sealed language versions
remain compatibility inputs.

## Authoritative schema

Generate the current contract from the backend that will run the package:

```bash
hermes workflow schema --profile archon-2026-07 --json
hermes workflow schema --profile hermes-legacy --json
```

Branded installations use their branded executable in place of `hermes`. The
output is a versioned envelope containing `definition_schema`,
`sidecar_schema`, generated documentation topics, and stable
`compatibility_codes`. Read the complete current code set, meaning, profile,
normalizer version, and migration guidance from that generated catalog; this
page intentionally does not maintain a second exhaustive code list. The loader remains
authoritative for graph references, package paths, provider capabilities, and
other checks that JSON Schema cannot express.

After writing a package, run both behavioral gates:

```bash
hermes workflow validate ./package/workflows/example.yaml --json
hermes workflow doctor ./package/workflows/example.yaml --compat-report --json
```

Neither schema, validate, nor doctor runs a workflow. Doctor does not call a
model, connect to MCP, or make a provider request.

## Package layout

```text
package/
├── workflows/example.yaml
├── workflows/example.hermes.yaml
├── commands/long-prompt.md
├── scripts/helper.py
└── mcp/server.yaml
```

Only include resource directories the definition references. Referenced files
must remain below the package root and cannot be symlinks. Their bytes, the
definition, and the companion are covered by the package digest.

The Hermes companion is metadata. It is not a process, daemon, worker,
container sidecar, or background service, and it does not execute beside the
workflow. It cannot change the portable `nodes` graph, hold secret values, or
declare its package trusted.

## Language profiles and backend floor

| Profile | Declaration in `example.hermes.yaml` | Current behavior |
| --- | --- | --- |
| `hermes-legacy` | Omit `language_compatibility`, or declare `language_compatibility: hermes-legacy` on a capable backend. | Preserves existing unversioned Hermes meanings and emits migration warnings. It does not reinterpret old packages. |
| `archon-2026-07` | `language_compatibility: archon-2026-07` | Opts into the reviewed July 2026 shape. Unknown definition fields and unavailable semantics fail closed. New first-party packages use this profile. |

An Archon companion requires a profile-aware backend. A pre-Phase-1 backend
rejects `language_compatibility` as an unknown companion field, so an Archon
package is intentionally unreadable there. A workflow directory or package
shared with an older Hermes, OTTO, LOOP24, or other brand runtime must stay
unversioned (effective `hermes-legacy`) until every consumer recognizes the
declaration. An explicit `hermes-legacy` declaration is safe only after every
consumer can parse `language_compatibility`.

Changing the declaration changes the package digest and requires validation,
doctor, and the normal digest-bound trust review again.

### Current normalizer selection

New and default `archon-2026-07` contracts and admissions select normalizer v4
until the reviewed Phase 5 activation commit.
Current `hermes-legacy` contracts select v2. Explicit and sealed v1 through v4
remain readable with their original meanings; resume uses the version pinned in
the immutable run snapshot rather than the moving profile default.

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

Installed integrators can retrieve a historical sealed-reader contract
explicitly when inspecting or migrating pinned snapshots:

```python
from plugins.workflow.language_schema import workflow_authoring_contract
from plugins.workflow.models import WorkflowLanguageProfile

contract = workflow_authoring_contract(
    WorkflowLanguageProfile.ARCHON_2026_07,
    normalizer_version=3,
)
```

The default call without `normalizer_version` is the authoritative current v4
syntax and diagnostic inventory. Any explicit version must remain pinned
through compilation, validation, trust, admission, and the immutable run
snapshot; explicit v1-v3 selection is compatibility behavior, while explicit
v5 selects the staged Phase 5 validator without changing the current profile
default. Explicit version selection is not a way to
change the current profile default.

## Phase 5 provider portability

Normalizer v5 resolves every accepted provider-dependent field through one
backend capability authority. Each requested feature receives exactly one
disposition: `native`, `hermes_adapter`,
`degraded_with_explicit_semantics`, or `unsupported`. Unsupported Archon
semantics are blocking; trust cannot override them, and no client may downgrade
them to a warning or silently omit them. The same sealed authority drives
validate, doctor, admission, execution, evidence, catalog/detail, and Desktop.

### Model references and `config.yaml`

Workflow `model` and `fallbackModel` values accept three forms:

- `small`, `medium`, or `large` selects a configured tier;
- `@name` selects a configured alias;
- any other nonempty value is a literal model ID.

Tiers and aliases are behavioral configuration and belong in profile or managed
`config.yaml`, never `.env`. Credentials remain in `.env` or the configured
credential store. A representative profile configuration is:

```yaml
model_tiers:
  small:
    provider: openrouter
    model: google/gemini-3.6-flash
    options:
      effort: low
  medium:
    provider: anthropic
    model: claude-sonnet-4.6
  large:
    provider: openrouter
    model: anthropic/claude-opus-4.6

model_aliases:
  review:
    provider: openrouter
    model: anthropic/claude-opus-4.6
    options:
      effort: high
```

Managed configuration overrides profile configuration leaf-by-leaf. Within a
workflow route, node options override workflow options, which override tier or
alias defaults. A tier or alias owns its configured provider; a conflicting
node or workflow `provider` is ignored with
`model_reference_provider_overridden`. Literal IDs select provider in this
order: non-`auto` node provider, non-`auto` workflow provider, then the active
configured provider. An unresolved `auto` provider blocks. Admission pins the
concrete provider, model, API mode, supported option set, and authority digest;
Desktop only displays that backend result.

Any provider, option, hook, MCP, skill, or inline-agent change that changes the
sealed cache fingerprint starts fresh context. Fallback runs in a separately
sealed fresh worker context. No Phase 5 path edits a cached system prompt or
injects synthetic conversation messages.

### Tools, hooks, MCP, skills, and inline agents

`allowed_tools: []` means exactly no callable built-in tools. Deny rules apply
after allow rules, and the backend resolves aliases before enforcing either.
Skills are read completely, snapshotted, and added to the current user turn;
they never mutate the system prompt. Inline agents are declared only through
the bounded `agents` object and inherit the parent attempt, cost, resource,
workdir, deadline, and cancellation authority. Raw unrestricted delegation is
not available.

Hook entries use the closed event and response shapes in the inventory below.
Unsupported events or operations block instead of disappearing. An MCP file is
package-local and may use one direct server object, a server-name mapping, or
exactly one `mcp_servers`/`mcpServers` wrapper. Each server defines exactly one
`command` or `url`; command transports are `stdio`, while URLs use
`streamable_http` (or `http` as its alias) or `sse`. Definitions, referenced
executables, and supporting resources for accepted local servers are
digest-bound into the sealed package closure. Phase 5 canonicalizes remote
HTTP/SSE definitions but classifies them as `unsupported`; only a
package-contained Python entry script launched by the attested Hermes
interpreter can execute. Workers apply existing process/resource bounds and
deterministic teardown. Secret values are resolved outside the package and
never appear in public evidence.

### Cost budgets and sandbox truth

`maxBudgetUsd` is accepted only on a route whose reviewed bundled adapter
provides authoritative billed-cost settlement for every billable outcome.
Estimated usage and local price tables cannot satisfy this contract. The
parent, retries, structured repair, fallback, and inline agents share one
budget; retry never resets it. A started provider call is settled atomically,
so its authoritative final charge may exceed the remaining amount by that one
call. Once exhausted, the run terminates with `cost_budget_exhausted` and is not
retried. Public evidence contains only bounded totals and digests.

No current provider profile proves provider-native `sandbox` enforcement.
Every Archon v5 `sandbox` request therefore blocks with
`provider_native_sandbox_unavailable`. When isolation is required, set the
companion policy `execution_environment: isolated_backend_required` and use a
backend that advertises that existing containment contract. Process RSS, CPU,
descendant, timeout, and workdir limits are availability/containment controls;
they are not a sandbox or security boundary.

## Phase 4 ordinary loops and immutable includes

An include is a compile directive, not an eighth executable node kind. Its
closed source shape is `id`, `include`, optional `depends_on`, and optional
`trigger_rule`. The target is one literal portable workflow name; paths, URLs,
expressions, `with`, runtime fields, and nested `loop_group` graphs are
rejected. Compilation expands the selected child depth-first, namespaces its
nodes, and removes the include directive before scheduling.

Only the root companion supplies language and execution policy. Child
companions remain authenticated package bytes, but their required-secret
declarations, limits, services, and other policy are ignored. A child cannot
weaken or augment the root policy.

For each include instance, entries are child nodes with no internal
dependencies and sinks are child nodes with no internal consumers. Parent
dependencies fan out to every entry. A downstream dependency on the include
waits for every sink. `$checks.output` selects the first sink in child
definition order; it is not a set, a last-completed result, or access to a deep
child. Direct deep-child and graph-escape references fail validation.

Compilation applies these hard closure ceilings before admission:

| Dimension | Maximum |
| --- | ---: |
| Include depth | 3 |
| Distinct selected dependencies | 64 |
| Expanded executable nodes | 512 |
| Expanded edges | 4,096 |
| Selected source bytes | 2 MiB |
| Expanded definition bytes | 2 MiB |
| Authenticated files | 512 |
| One authenticated file | 1 MiB |
| All authenticated files | 8 MiB |

Named commands, scripts, and MCP resources used by an included node resolve
from that node's logical child package. Compilation records that origin, and
admission materializes the complete authenticated closure into the immutable
run snapshot. Execution and resume read only the sealed binding; deleting or
changing the original root or child source cannot redirect it. Diagnostics use
bounded logical provenance such as `include[checks]/child.yaml`, never a host
filesystem path.

### Root and child authoring shape

`root/workflows/release.yaml`:

```yaml
name: release
description: Run shared checks and confirm a bounded refinement
interactive: true
nodes:
  - id: prepare
    bash: "printf '%s\\n' ready"
  - id: checks
    include: shared-checks
    depends_on: [prepare]
  - id: refine
    depends_on: [checks]
    loop:
      command: refine-release
      until: DONE
      max_iterations: 3
      interactive: true
      signal_completes: false
      gate_message: Accept this result or provide feedback
```

`root/workflows/release.hermes.yaml` selects
`language_compatibility: archon-2026-07` and owns all policy. The named loop
body lives at `root/commands/refine-release.md`.

`child/workflows/shared-checks.yaml`:

```yaml
name: shared-checks
description: Reusable deterministic checks
nodes:
  - id: lint
    bash: "printf '%s\\n' linted"
  - id: test
    depends_on: [lint]
    bash: "printf '%s\\n' tested"
```

A neighboring `shared-checks.hermes.yaml` may be present in the authenticated
closure, but it has no policy effect. In this example `lint` is the entry,
`test` is the only sink, and `$checks.output` aliases `test`.

### Confirmed loop signals and operator actions

A v4 loop supplies exactly one of inline `prompt` or a named `command`, plus
`until` and `max_iterations` from 1 through 100. A command body is resolved and
sealed before execution. The loop is effectively interactive only when both
workflow-level `interactive` and loop-level `interactive` are true; an
interactive loop also requires `gate_message`.

`signal_completes` defaults to `false` for an effectively interactive loop and
to `true` otherwise. Setting it to `false` without an effective operator path
is rejected. When a completion signal requires confirmation, Hermes pauses on
the authenticated cleaned result and reuses the existing run wire actions:

- before the final iteration: `approve`, `provide-input`, or `cancel`;
- on the final iteration: `approve` or `cancel` only.

`status` and `events` remain available in both states. Approval accepts the
sealed result and completes without replaying the provider. `provide-input`
records feedback and permits the next bounded iteration. The final iteration
cannot request another one, so it never advertises `provide-input`. Resume,
approval, and input use the run's existing interaction ID and expected state
version; no Phase 4-specific mutation endpoint or action name is introduced.

Phase 4 deliberately does not implement runtime child workflows,
parameterized `include.with`, or `loop_group`. Phase 5 adds no such graph
features. Do not synthesize those meanings from provider portability, includes,
or ordinary loops.

## Status vocabulary

| Status | Meaning on this page |
| --- | --- |
| **Enforced** | Hermes validates and executes the stated structural/runtime meaning. |
| **Mapped** | Hermes supplies an equivalent through its agent, provider, tool, or policy system. Doctor decides whether the selected environment has that capability. |
| **Legacy-only** | The current meaning is preserved under `hermes-legacy`, usually with a warning; it is not an Archon-profile guarantee. |
| **Blocked pending Phase N** | The generated Archon contract carries `x-hermes-status: blocking`. The number identifies later work; schema shape alone does not make the field runnable. |

### Generated stable codes

The `compatibility_codes` object is the versioned public authority for both
compatibility findings and durable Phase 3 through v5 runtime/evidence codes. Operator
surfaces preserve those codes, while messages may improve. Run `workflow
doctor` for package-specific findings and use Run Inspector for bounded attempt
or recovery evidence. Do not copy the catalog into package metadata or prose.

## Portable definition inventory

### Top-level fields

`name`, `description`, and `nodes` are required. The Archon schema rejects
unknown top-level fields; legacy reports them without changing existing
behavior.

| Field | Shape and present meaning | Current status |
| --- | --- | --- |
| `name` | Nonempty portable workflow identifier. | Enforced |
| `description` | Nonempty human description. | Enforced |
| `nodes` | Nonempty array of the node variants below. | Enforced |
| `provider` | Nonempty default Hermes provider profile name. | Mapped; doctor checks resolution |
| `model` | Nonempty default model identifier. | Mapped; doctor checks provider support |
| `modelReasoningEffort` | Nonempty provider reasoning control. | Mapped; doctor checks the provider capability |
| `webSearchMode` | Nonempty provider web-execution control. | Mapped; doctor checks the provider capability |
| `interactive` | Boolean invocation metadata. | Mapped |
| `requires` | Array of nonempty service names checked at preflight. | Mapped; a missing service blocks |
| `worktree` | Exactly `{enabled: boolean}`; `true` requires a caller-supplied isolated workdir. | Mapped; missing isolation blocks |
| `tags` | Array of nonempty display/indexing strings. | Mapped |
| `persist_sessions` | Boolean default for eligible AI node sessions. | Mapped to the profile-scoped node-session registry |
| `effort` | `low`, `medium`, `high`, or `max`. | Mapped; provider capability applies |
| `thinking` | `adaptive`, `disabled`, or `{type: enabled, budgetTokens: positive integer}`. | Mapped; provider capability applies |
| `fallbackModel` | Nonempty fallback model identifier. | Mapped; provider capability applies |
| `betas` | Array of nonempty provider beta names. | Mapped; provider capability applies |
| `sandbox` | Provider-native mapping object. Resource limits are not a sandbox. | Provider-capability checked; currently blocked for Archon v5 (`provider_native_sandbox_unavailable`) |

The generated nested helpers are also closed shapes: `worktree` contains only
`enabled`; enabled `thinking` contains `type` and `budgetTokens`; and a script's
`runtime` is `uv` or `bun`.

### Common node fields

Every node has a nonempty `id` and exactly one node-type payload. IDs,
dependencies, and references are validated as one acyclic graph.

| Field | Shape and present meaning | Current status |
| --- | --- | --- |
| `id` | Nonempty node identifier, required. | Enforced |
| `depends_on` | Array of direct upstream node IDs. Every Phase 3 output reference must name one. | Enforced |
| `when` | Typed scalar comparisons over direct-dependency `$node.output` values. False skips; syntax, missing-value, and type errors fail before execution. | Enforced under Archon v3-v5; legacy behavior is unchanged |
| `trigger_rule` | `all_success`, `one_success`, `none_failed_min_one_success`, or `all_done`. | Enforced |
| `context` | `fresh` or `shared`; shared resumes only a cache-fingerprint-compatible predecessor. | Mapped and cache-enforced |
| `idle_timeout` | Positive finite milliseconds on Archon AI nodes; omission uses the sealed Hermes AI idle ceiling. Legacy values remain seconds. | Enforced under Archon v3-v5; legacy warning retained |
| `retry` | Retry object documented below. Archon `max_attempts` counts retries after the initial attempt. | Enforced on Archon command, prompt, Bash, and script nodes; legacy total-attempt meaning retained |
| `always_run` | Boolean graph scheduling flag. | Enforced |
| `output_type` | Nonempty, case-sensitive semantic label, at most 16,384 characters. Under Archon, a successful output-producing node publishes one typed artifact for its winning attempt. | Enforced for Archon; legacy accepts the label but does not publish |

### Node variants

| Node | Required payload | Additional fields | Current status |
| --- | --- | --- | --- |
| `command` | `command: nonempty string`; inline text or a name below `commands/`. | AI fields below. | Mapped to an isolated Hermes agent worker |
| `prompt` | `prompt: nonempty string`; inline prompt text. | AI fields below. | Mapped to an isolated Hermes agent worker |
| `bash` | `bash: nonempty string`. | Optional millisecond `timeout` and `retry`. | Enforced through the contained process runner |
| `script` | `script: nonempty string` and `runtime: uv | bun`. | `deps` string array; optional millisecond `timeout` and `retry`. Named scripts resolve below `scripts/`. | Enforced when the runtime and resource exist |
| `loop` | `loop` object below. | Common fields except node `retry`. | Current v4 seals exactly one prompt/command source and confirmed-signal semantics; sealed v3 behavior is preserved |
| `approval` | `approval` object below. | Common fields; node retry is not supported in Archon v3-v5. | Enforced durable compare-and-set user gate |
| `cancel` | `cancel: nonempty string` reason. | Common fields; node retry is not supported in Archon v3-v5. | Enforced durable cancellation; it never publishes because it cannot complete successfully |

For Archon Bash and script nodes, `timeout` is a positive finite millisecond
value. Omission requests the Archon 120,000 ms default before Hermes intersects
it with the sealed subprocess ceiling. AI `idle_timeout` is also milliseconds;
omission uses the sealed AI idle ceiling. Both deadlines are per workflow
attempt. Under `hermes-legacy`, the same authored timeout fields remain seconds
and retain their generated migration warnings.

### Command and prompt fields

| Field | Shape and present meaning | Current status |
| --- | --- | --- |
| `persist_session` | Boolean node override for profile-scoped session persistence. | Mapped; fresh context wins |
| `provider` | Nonempty provider profile override. | Mapped; doctor checks authorization and availability |
| `model` | Nonempty model override. | Mapped; doctor checks authorization and availability |
| `output_format` | Bounded, self-contained JSON Schema Draft 2020-12 object. Archon seals a provider strategy before execution and retains one canonical JSON value. Legacy keeps post-execution validation. | Enforced for Archon; legacy behavior frozen |
| `allowed_tools` | Array of nonempty aliases/names; an empty array means no built-in tools. | Mapped and enforced after alias resolution |
| `denied_tools` | Array of nonempty aliases/names; deny is applied after allow. | Mapped and enforced after alias resolution |
| `hooks` | Hook event object documented below. | Mapped per event; doctor blocks events without an equivalent |
| `mcp` | Nonempty package-local MCP definition name. | Mapped when Hermes MCP support is installed; server is worker-scoped |
| `skills` | Array of nonempty skill names. | Mapped to immutable user-message snapshots, not the system prompt |
| `agents` | Mapping from portable agent ID to the inline-agent shape below. | Mapped to bounded `workflow_agent` children |
| `effort` | `low`, `medium`, `high`, or `max`. | Mapped; provider capability applies |
| `thinking` | `adaptive`, `disabled`, or enabled object with positive `budgetTokens`. | Mapped; provider capability applies |
| `maxBudgetUsd` | Positive number shared by the parent, retries, repair, fallback, and inline agents. | Enforced only with authoritative provider settlement; otherwise blocked (`authoritative_cost_unavailable`) |
| `systemPrompt` | Nonempty initial worker system prompt. | Mapped only for a fresh/fingerprint-safe context; changing a shared session blocks |
| `fallbackModel` | Nonempty fallback identifier. | Mapped; provider capability applies |
| `betas` | Array of nonempty provider beta names. | Mapped; provider capability applies |
| `sandbox` | Provider-native mapping object. | Capability checked; currently blocked for Archon v5 (`provider_native_sandbox_unavailable`) |

Published aliases include `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`,
`WebFetch`, `WebSearch`, `Agent`, and `Task`. Doctor shows the concrete Hermes
mapping. An unknown capitalized alias or an unavailable mapped tool blocks
before a model call.

MCP and skills are options on `command` and `prompt`; they are not node kinds.
The seven node kinds are `command`, `prompt`, `bash`, `script`, `loop`,
`approval`, and `cancel`. Script execution with `uv` or `bun` is existing
workflow behavior, not a structured-output node variant.

## Phase 3 references, conditions, Bash, and sessions

Archon v3 and v4 use the closed `$ID.output(.path)*` grammar. The producer must be a
direct dependency. Whole schemaless output remains text; field traversal
requires structured output. Conditions compare typed scalar values with
`==`, `!=`, `<`, `<=`, `>`, and `>=`, joined by bounded `&&` and `||`.
Structured strings never become numbers implicitly, and a missing or invalid
operand fails before an executor attempt instead of becoming false.

Bash substitution measures UTF-8 bytes. Values through 32,768 bytes are
rendered inline. Larger values are read as contents, not pathnames, with a
500,000-byte per-value ceiling, at most 64 distinct spills, and a 2,000,000-byte
total per attempt. The complete rendered command has a separate 98,304-byte
UTF-8 ceiling, including inline values and the descriptor prologue.
Authenticated tokens in ordinary unquoted, double-quoted, and safely rewritten
single-quoted command-word contexts preserve contents; escaped tokens and
comments remain literal. Ambiguous shell expansions and aggregate command
overflow fail before launch.

For persistent AI sessions, a missing same-run shared session fails without a
provider attempt. Only a confirmed missing cross-run registry session may
select one fresh execution. Storage errors do not masquerade as absence, and a
fingerprint mismatch keeps the established warning/fresh behavior. Inspect
the generated stable code in `workflow doctor` and the bounded
`recovery_kind: persistent_session` record in Run Inspector; raw session data
is not part of that evidence.

## Structured output contract

`output_format` is active only under `archon-2026-07`. The schema dialect is
JSON Schema Draft 2020-12. Schemas must be self-contained: `$defs` is allowed,
and `$ref` may point only to a JSON Pointer below the same document's `$defs`.
External, absolute, unresolved, or cyclic references are rejected, as are
`$dynamicRef`, `$id`, `$anchor`, and `$dynamicAnchor`. Patterns must compile,
and numeric schema values must be finite.

The loader applies these exact ceilings before a run is admitted:

| Dimension | Maximum |
| --- | ---: |
| Canonical schema | 65,536 bytes |
| Schema nesting depth | 32 |
| Traversed schema nodes | 4,096 |
| Object properties across the schema | 1,024 |
| Local references | 256 |
| One regex | 1,024 bytes |
| All regex text | 16,384 bytes |
| One enum | 1,024 values |
| Canonical structured output | 500,000 bytes |
| Invalid response eligible for repair | 256,000 bytes |
| Repair validation diagnostics | 16,384 bytes |
| Typed-artifact metadata | 65,536 bytes |

Structured validation uses the optional `jsonschema` dependency. A lean
installation can run schemaless workflows, but an Archon node with
`output_format` fails closed before any provider request when the validator is
missing or unusable. Install it with the Hermes `mcp` or `all` extra, for
example `python -m pip install 'hermes-agent[mcp]'`, then rerun doctor before
admission. `workflow validate` remains the static package gate.

### Provider enforcement and repair

Hermes chooses one strategy from the sealed provider/runtime declaration:

- a trusted direct route may use declared native JSON Schema or native JSON
  mode;
- an undeclared route inside the complete Hermes agent loop uses bounded prompt
  adaptation;
- a custom endpoint, aggregator, or community catalog entry is never promoted
  to native support by its name or API shape;
- an explicitly unsupported or delegated runtime fails closed.

The worker resolves its actual route before its first provider request. If it
cannot honor the admitted strategy, it fails with capability drift rather than
silently downgrading. Native output is still parsed and validated locally.

Only invalid prompt-adapted output can receive a repair, and at most once. The
repair is a fresh, one-turn transformation of the bounded response against the
sealed schema. It receives no original task or history and has no tools, hooks,
MCP, skills, agents, delegation, fallback, persistent session, or approval,
secret, or clarification path. Outward-acting nodes, uncertain side effects,
cancellation, or exhausted provider, model, or wall budgets make repair
ineligible. Native validation misses fail without repair.

### Canonical JSON and field references

The authoritative response is one complete JSON value. Markdown fences,
surrounding prose, multiple values, non-finite numbers, and trailing non-space
content are invalid. Hermes serializes accepted output as UTF-8 with keys
sorted by Unicode code-point order, compact separators, preserved JSON
booleans and null, no trailing newline, and a 500,000-byte ceiling. That same
canonical value and SHA-256 feed downstream references, evidence, publication,
and preview.

A direct `$node.output.field` reference is rejected during validation only when
every applicable schema branch proves the field cannot exist. Open objects,
optional declared fields, unions that permit the field, and schemaless outputs
remain runtime decisions. Phase 2 does not add Phase 3's strict missing-output,
missing-reference, or missing-field behavior.

## Typed artifact publication

For a successful Archon `command`, `prompt`, `bash`, `script`, `loop`, or
`approval` node with `output_type`, Hermes publishes the winning attempt's
primary output. `output_type` is an open, case-sensitive semantic label; it
does not select a serializer, filename, or extension. Empty successful text is
a valid zero-byte publication.

The canonical bundle uses an opaque publication ID and contains:

| Output | Content file | Media type |
| --- | --- | --- |
| Structured JSON | `content.json` | `application/json` |
| Other UTF-8 output | `content.md` | `text/markdown; charset=utf-8` |

`metadata.json` records the content digest, size, semantic output type,
producer, winning attempt, run, profile, publication ID, production time,
canonicalization version, optional schema fingerprint, and optional session
identity. Evidence contains this bounded metadata, never the artifact body.

Completion verifies the active claim and attempt before filesystem mutation,
stages both files on the run filesystem, flushes them and the staging
directory, atomically installs the complete directory without replacement,
flushes its parent, appends and flushes the journal event, and then replaces
the `run.json` projection. A stale or losing attempt is neither published nor
journaled.

Recovery follows the journal: incomplete staging and unjournaled final bundles
are removed; a journaled bundle missing only from `run.json` returns during
projection rebuild. Missing or corrupt content is reconstructed only from the
corroborated winning attempt with the recorded digest. Otherwise the run enters
an explicit typed-artifact integrity or reconciliation state rather than
guessing from timestamps or directory order.

Authenticated read APIs accept only the run ID and opaque publication ID. They
verify profile and run ownership, containment, regular files, recorded size,
and digest. Preview is bounded to 64 KiB: JSON is returned only when its
complete canonical body fits, while UTF-8 text may be truncated. Downloads
stream the verified original with a safe attachment name.

### Loop, approval, and inline-agent objects

| Object | Field | Shape and present meaning | Status |
| --- | --- | --- | --- |
| `loop` | `prompt` | Nonempty inline prompt; v4 requires exactly one of `prompt` or `command`. | Enforced |
| `loop` | `command` | Named package command; v4 requires exactly one of `command` or `prompt` and seals the resolved body. | Enforced in v4 |
| `loop` | `until` | Required nonempty completion condition. | Enforced |
| `loop` | `max_iterations` | Required integer from 1 through 100. | Enforced |
| `loop` | `fresh_context` | Current truth-tested option controlling per-iteration context. | Mapped/cache-enforced |
| `loop` | `until_bash` | Current truth-tested deterministic completion command. | Enforced through contained Bash execution |
| `loop` | `interactive` | Current truth-tested interactive-gate option. | Enforced |
| `loop` | `gate_message` | Required and JSON-truthy when `interactive` is true. | Enforced |
| `loop` | `signal_completes` | Boolean signal outcome; v4 defaults false only for an effectively interactive loop, otherwise true. | Enforced in v4 |
| `approval` | `message` | Required nonempty review message. | Enforced |
| `approval` | `capture_response` | Current truth-tested response-capture option. | Enforced |
| `approval` | `on_reject` | Optional object with required nonempty `prompt` and optional `max_attempts` from 1 through 10. | Enforced; this is approval rework, not node retry |
| inline agent | `description` | Required nonempty role description. | Mapped |
| inline agent | `prompt` | Required nonempty child prompt. | Mapped |
| inline agent | `model` | Optional nonempty model. | Mapped/provider-checked |
| inline agent | `tools` | Optional array of nonempty tool names. | Mapped/tool-checked |
| inline agent | `disallowedTools` | Optional array of nonempty denied tools. | Mapped/tool-checked |
| inline agent | `skills` | Optional array of nonempty skill names. | Mapped and snapshotted |
| inline agent | `maxTurns` | Optional positive integer iteration ceiling. | Enforced as turns, not seconds |

### Retry object

| Field | Archon v3-v5 meaning | Legacy meaning |
| --- | --- | --- |
| `max_attempts` | Required integer 1–5 counting retries after the initial attempt. | Integer 1–5 counting total workflow/provider attempts; warning `legacy_retry_total_attempts`. |
| `delay_ms` | Optional integer 1,000–60,000 milliseconds between workflow retries. | Same unit with the legacy total-attempt ledger. |
| `on_error` | Optional `transient` or `all`; deterministic nodes retry only known eligible outcomes. | Existing legacy classification. |

When `retry` is omitted, Archon AI nodes request two retries after their
initial attempt, while deterministic Bash and script nodes request none. The
sealed combined ceiling can reduce the effective total. Workflow and provider
layers share one non-multiplying attempt ledger.

## Hook inventory

All published event keys are structurally recognized so doctor can issue an
exact finding. Recognition does not imply a runtime mapping.

| Hook event | Current status |
| --- | --- |
| `PreToolUse`, `PostToolUse`, `PostToolUseFailure` | Mapped to isolated worker tool lifecycle |
| `SessionStart`, `SessionEnd`, `UserPromptSubmit` | Mapped to isolated worker/session lifecycle |
| `SubagentStart`, `SubagentStop`, `TaskCompleted`, `Elicitation`, `ElicitationResult`, `PermissionRequest`, `Setup`, `InstructionsLoaded` | Recognized and sealed, but blocked: the current worker does not provide the exact event-and-response contract |
| `Notification`, `Stop`, `PreCompact`, `TeammateIdle`, `ConfigChange`, `WorktreeCreate`, `WorktreeRemove` | Blocked by doctor: no equivalent node-worker contract |

Each event value is a nonempty array of hook entries:

| Scope | Field | Shape and present meaning | Status |
| --- | --- | --- | --- |
| entry | `matcher` | Any JSON value accepted by the current matcher bridge. | Mapped/event-dependent |
| entry | `response` | Required response object. | Enforced structurally, mapped behaviorally |
| entry | `timeout` | Positive number of seconds for the hook handler deadline; mapped runtime bounds still apply. | Mapped |
| response | `hookSpecificOutput` | Event-specific object or explicit `null`. Non-null objects require matching `hookEventName`. | Mapped/event-dependent |
| response | `systemMessage` | Nonempty message. | Mapped without mutating a shared cached system prompt |
| response | `continue` | Boolean continuation decision. | Mapped/event-dependent |
| response | `decision` | `approve` or `block`. | Mapped/event-dependent |
| response | `stopReason` | Nonempty stop reason. | Mapped/event-dependent |
| response | `suppressOutput` | Boolean output suppression. | Mapped/event-dependent |
| specific | `hookEventName` | Required event name, and must equal the containing event. | Enforced |
| specific | `permissionDecision` | `deny`, `allow`, or `ask`. | Mapped for permission-capable events |
| specific | `permissionDecisionReason` | Nonempty reason. | Mapped for permission-capable events |
| specific | `updatedInput` | Object containing the mapped input update. | Mapped/event-dependent |
| specific | `additionalContext` | Nonempty added context. | Mapped through result/user content |
| specific | `updatedMCPToolOutput` | Any JSON value. | Mapped only for applicable MCP events |
| specific | `action` | `accept`, `decline`, or `cancel`. | Mapped for elicitation events |
| specific | `content` | Any JSON value. | Mapped/event-dependent |

Unknown hook fields and mismatched `hookEventName` values are rejected.

## Hermes companion inventory

Unknown companion fields are rejected under both profiles. Mappings such as
delivery and scheduling remain Hermes metadata; they never alter graph
topology.

| Field | Shape and present meaning | Status |
| --- | --- | --- |
| `language_compatibility` | `hermes-legacy` or `archon-2026-07`. Absence means legacy. | Enforced profile selection; absence/explicit legacy is legacy-only behavior |
| `delivery_defaults` | Mapping of delivery metadata; `inputs` declarations are described below. | Mapped and doctor-checked |
| `required_services` | Array of nonempty service names. | Mapped to preflight |
| `retention` | Hermes retention-policy mapping. | Mapped |
| `tags` | Array of nonempty display/indexing strings. | Mapped |
| `outward_action_nodes` | Array of node IDs; each must exist. | Enforced and included in risk review |
| `outward_action_policy` | Nonempty Hermes policy identifier. | Mapped |
| `execution_environment` | `trusted_local` or `isolated_backend_required`. | Enforced admission policy |
| `overlap_policy` | `queue`, `allow`, or `forbid`. | Enforced; new packages should default to `queue` |
| `pause_lane_policy` | `hold` or `release`; allowed only with `overlap_policy: queue`. | Enforced |
| `concurrency_key` | Nonempty matching-run key. | Enforced admission policy |
| `limits` | Mapping that may only tighten known profile lifecycle ceilings. | Enforced Hermes execution policy |
| `resource_limits` | Mapping that may only tighten process RSS, CPU, and descendant ceilings. | Enforced availability policy, not a sandbox |
| `required_secrets` | Array of nonempty secret names; never values. | Enforced and risk-reviewed |
| `scheduling` | Hermes scheduling-policy mapping. | Mapped; actual schedules use the Hermes cron path |

### Immutable inputs

Declare inputs at `delivery_defaults.inputs.NAME`:

| Field | Shape | Present meaning |
| --- | --- | --- |
| `kind` | `text`, `file`, `directory`, or `json`; default `text`. | Selects admission validation/snapshot handling. |
| `required` | Boolean; default `true`. | Whether admission must supply the input. |
| `max_bytes` | Optional positive integer bytes. | Per-input ceiling, still capped by backend hard limits. |

File and document sources are copied into the run's immutable `inputs/`
snapshot before admission. Nodes must read that snapshot, not reopen the
original mutable source path.

### Runtime scratch state and artifacts

Legacy workflows may keep ordinary scratch or state files under the directory
advertised by `HERMES_WORKFLOW_RUN_DIR`. This preserves scripts that wrote
beside run metadata before language profiles were introduced. Those added
files are never added to the admitted sealed resource set and cannot become a
command, script, skill, input, MCP definition, or other execution resource.
Symlinks and special files remain invalid, and any missing or changed sealed
member still stops verification.

Use `ARTIFACTS_DIR` for durable node output whenever possible. It is the
recommended executor-owned output location and avoids coupling a workflow to
the layout of the run root. The legacy scratch allowance is compatibility
behavior, not an authority or portability guarantee for the Archon profile.

### Lifecycle and resource units

`limits` recognizes these current profile keys. A companion value can tighten,
never widen, the configured profile ceiling.

| Unit | Keys |
| --- | --- |
| Counts | `max_parallel_nodes`, `max_total_workers`, `max_executing_runs`, `max_queued_runs`, `max_paused_runs`, `max_nonterminal_runs`, `combined_retries` |
| Starts per minute | `max_start_requests_per_minute` |
| Seconds | `ai_idle_timeout_seconds`, `ai_wall_timeout_seconds`, `provider_request_timeout_seconds`, `subprocess_timeout_seconds`, `heartbeat_seconds`, `lease_seconds`, `coordinator_web_election_grace_seconds`, `runnable_stall_seconds`, `semantic_stall_seconds`, `cooperative_shutdown_seconds`, `term_grace_seconds`, `kill_reap_grace_seconds` |

`resource_limits` recognizes:

| Field | Unit and meaning |
| --- | --- |
| `process_tree_rss_bytes` | Bytes of process-tree resident memory. |
| `process_tree_cpu_seconds` | Aggregate process-tree CPU seconds. |
| `max_descendants` | Process/worker descendant count. |

These ceilings control execution availability and containment. They do not
claim OS sandbox portability. Cost-budget enforcement is the separate
authoritative provider-settlement contract described above.

## Examples

### Minimal parameterless package

`workflows/hello.yaml`:

```yaml
name: hello
description: Print one deterministic greeting
nodes:
  - id: greet
    bash: "printf '%s\\n' 'hello from Hermes'"
```

`workflows/hello.hermes.yaml`:

```yaml
language_compatibility: archon-2026-07
overlap_policy: queue
execution_environment: trusted_local
outward_action_nodes: []
```

This package has no invocation input. Omitted Bash `timeout` requests the
120,000 ms Archon default before policy capping; omitted deterministic `retry`
means one initial attempt and no retries.

### Structured output and downstream field package

`workflows/report.yaml`:

```yaml
name: structured-report
description: Produce one validated report and pass its status downstream
nodes:
  - id: summarize
    prompt: Return the report status and issue count as JSON.
    output_type: OpsReport/V1
    output_format:
      $schema: https://json-schema.org/draft/2020-12/schema
      type: object
      required: [status, issue_count]
      properties:
        status:
          type: string
          enum: [ready, blocked]
        issue_count:
          type: integer
          minimum: 0
      additionalProperties: false

  - id: announce
    depends_on: [summarize]
    bash: 'printf "status=%s\n" "$summarize.output.status"'
```

`workflows/report.hermes.yaml`:

```yaml
language_compatibility: archon-2026-07
overlap_policy: queue
execution_environment: trusted_local
outward_action_nodes: []
```

The producer's response is validated and canonicalized before `announce`
resolves the `status` field. `OpsReport/V1` remains exactly case-sensitive; it
creates one backend-confirmed typed publication for the winning `summarize`
attempt. It does not create a file named after the output type. Inspect or
download it from the run's artifact evidence in Desktop.

### Immutable-input package

```text
evidence-package/
├── workflows/read-evidence.yaml
├── workflows/read-evidence.hermes.yaml
└── scripts/read-evidence.py
```

`workflows/read-evidence.yaml`:

```yaml
name: read-evidence
description: Read an admitted evidence snapshot without reopening its source
nodes:
  - id: read
    script: read-evidence
    runtime: uv
```

`workflows/read-evidence.hermes.yaml`:

```yaml
language_compatibility: archon-2026-07
delivery_defaults:
  inputs:
    evidence:
      kind: file
      required: true
      max_bytes: 65536
overlap_policy: queue
execution_environment: trusted_local
limits:
  subprocess_timeout_seconds: 30
resource_limits:
  max_descendants: 2
outward_action_nodes: []
```

`scripts/read-evidence.py`:

```python
import os
from pathlib import Path

run_dir = Path(os.environ["HERMES_WORKFLOW_RUN_DIR"])
print((run_dir / "inputs" / "evidence").read_text(encoding="utf-8"))
```

The script reads the admitted copy. It never receives or reopens the original
path. `subprocess_timeout_seconds` is explicitly Hermes policy, not the deferred
Archon node `timeout` field.

## Migration from legacy

1. Leave the declaration absent (or explicit legacy on declaration-capable
   backends) and run `workflow validate`.
2. Run `workflow doctor --compat-report --json` and review every stable legacy
   warning and environment mapping.
3. Convert legacy timeout seconds to Archon milliseconds. Convert a legacy
   explicit total attempt count `N >= 2` to Archon `max_attempts: N - 1`, then
   inspect the sealed combined cap. Add every referenced producer directly to
   `depends_on` and make field references structured.
4. Remove any blocker, then declare
   `language_compatibility: archon-2026-07` in the companion.
5. Rerun validate and doctor. Review and trust the new exact digest before a
   run.

Phase 4 does not add an executable node kind: `include` is compile-only, and
the existing `loop` node receives the sealed prompt/command and signal contract
described above. Phase 5 adds provider portability without a new node kind.
MCP and skills remain options on AI nodes. Runtime child workflows,
`include.with`, and `loop_group` remain out of scope. Provider-native sandbox
requests and providers without authoritative cost settlement remain explicit
blocking compatibility findings.

The legacy global `create-workflow` skill is not an authoring authority for
Hermes. OTTO V1 `steps`, `produces`, `context_from`, `verify`, and `iterate`
documents are rejected; convert the desired behavior to the `nodes` DAG and
Hermes companion described here.
