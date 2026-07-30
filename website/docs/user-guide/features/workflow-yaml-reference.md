---
sidebar_position: 14
title: "Workflow YAML reference"
description: "Author profile-aware portable workflow packages for the Phase 1 language contract"
---

# Workflow YAML reference

Hermes reads a portable workflow definition and an optional Hermes companion
file. The portable file describes the DAG. The companion selects the language
profile and adds Hermes admission and execution policy.

This page describes the Phase 1 contract. It does not claim Phase 2 or later
execution semantics.

## Authoritative schema

Generate the current contract from the backend that will run the package:

```bash
hermes workflow schema --profile archon-2026-07 --json
hermes workflow schema --profile hermes-legacy --json
```

Branded installations use their branded executable in place of `hermes`. The
output is a versioned envelope containing `definition_schema`,
`sidecar_schema`, and stable `compatibility_codes`. The tables below were
checked against both generated profile envelopes. The loader remains
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

| Profile | Declaration in `example.hermes.yaml` | Phase 1 behavior |
| --- | --- | --- |
| `hermes-legacy` | Omit `language_compatibility`, or declare `language_compatibility: hermes-legacy` on a capable backend. | Preserves existing unversioned Hermes meanings and emits migration warnings. It does not reinterpret old packages. |
| `archon-2026-07` | `language_compatibility: archon-2026-07` | Opts into the reviewed July 2026 shape. Unknown definition fields and unavailable semantics fail closed. New first-party packages use this profile. |

An Archon companion requires a Phase-1-capable backend. A pre-Phase-1 backend
rejects `language_compatibility` as an unknown companion field, so an Archon
package is intentionally unreadable there. A workflow directory or package
shared with an older Hermes, OTTO, LOOP24, or other brand runtime must stay
unversioned (effective `hermes-legacy`) until every consumer recognizes the
declaration. An explicit `hermes-legacy` declaration is safe only after every
consumer can parse `language_compatibility`.

Changing the declaration changes the package digest and requires validation,
doctor, and the normal digest-bound trust review again.

## Status vocabulary

| Status | Meaning on this page |
| --- | --- |
| **Enforced** | Phase 1 validates and executes the stated structural/runtime meaning. |
| **Mapped** | Hermes supplies an equivalent through its agent, provider, tool, or policy system. Doctor decides whether the selected environment has that capability. |
| **Legacy-only** | The current meaning is preserved under `hermes-legacy`, usually with a warning; it is not an Archon-profile guarantee. |
| **Blocked pending Phase N** | The generated Archon contract carries `x-hermes-status: blocking`. The number is enforcement-phase metadata, not a delivery date or availability promise; schema shape alone does not make the field runnable. |

### Generated compatibility codes

The current generated envelopes publish all of these stable codes. An
enforcement phase classifies the contract dependency; it does not promise when
support ships.

| Profile | Code | Fields | Status | Enforcement phase |
| --- | --- | --- | --- | ---: |
| Both | `workflow_language_profile_unsupported` | `sidecar.language_compatibility` | Blocking | 1 |
| Both | `workflow_normalizer_version_unsupported` | `normalizer_version` | Blocking | 1 |
| Archon | `archon_unknown_top_level_field` | Any unknown top-level field | Blocking | 1 |
| Archon | `archon_output_format_unavailable` | `nodes[].output_format` | Blocking | 2 |
| Archon | `archon_output_type_unavailable` | `nodes[].output_type` | Blocking | 2 |
| Archon | `archon_idle_timeout_semantics_unavailable` | `nodes[].idle_timeout` | Blocking | 3 |
| Archon | `archon_retry_semantics_unavailable` | `nodes[].retry` | Blocking | 3 |
| Archon | `archon_timeout_semantics_unavailable` | `nodes[].timeout` | Blocking | 3 |
| Archon | `archon_budget_enforcement_unavailable` | `nodes[].maxBudgetUsd` | Blocking | 5 |
| Archon | `archon_sandbox_enforcement_unavailable` | `sandbox`, `nodes[].sandbox` | Blocking | 5 |
| Legacy | `legacy_language_profile` | `sidecar.language_compatibility` | Warning | 1 |
| Legacy | `unknown_top_level_field` | Any unknown top-level field | Warning | 1 |
| Legacy | `legacy_output_format_post_validation` | `nodes[].output_format` | Warning | 2 |
| Legacy | `legacy_output_type_not_published` | `nodes[].output_type` | Warning | 2 |
| Legacy | `legacy_idle_timeout_seconds` | `nodes[].idle_timeout` | Warning | 3 |
| Legacy | `legacy_retry_total_attempts` | `nodes[].retry.max_attempts` | Warning | 3 |
| Legacy | `legacy_timeout_seconds` | `nodes[].timeout` | Warning | 3 |

## Portable definition inventory

### Top-level fields

`name`, `description`, and `nodes` are required. The Archon schema rejects
unknown top-level fields; legacy reports them without changing existing
behavior.

| Field | Shape and present meaning | Phase 1 status |
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
| `sandbox` | Provider/backend mapping object. Resource limits are not a sandbox. | Legacy-only; blocked pending Phase 5 (`archon_sandbox_enforcement_unavailable`) |

The generated nested helpers are also closed shapes: `worktree` contains only
`enabled`; enabled `thinking` contains `type` and `budgetTokens`; and a script's
`runtime` is `uv` or `bun`.

### Common node fields

Every node has a nonempty `id` and exactly one node-type payload. IDs,
dependencies, and references are validated as one acyclic graph.

| Field | Shape and present meaning | Phase 1 status |
| --- | --- | --- |
| `id` | Nonempty node identifier, required. | Enforced |
| `depends_on` | Array of upstream node IDs. | Enforced |
| `when` | Nonempty condition over upstream `$node.output` values. | Enforced with current Phase 1 condition behavior |
| `trigger_rule` | `all_success`, `one_success`, `none_failed_min_one_success`, or `all_done`. | Enforced |
| `context` | `fresh` or `shared`; shared resumes only a cache-fingerprint-compatible predecessor. | Mapped and cache-enforced |
| `idle_timeout` | Positive number. Hermes legacy executes the authored value as seconds; Archon millisecond normalization is deferred. | Legacy-only; blocked pending Phase 3 (`archon_idle_timeout_semantics_unavailable`) |
| `retry` | Retry object documented below; unavailable as an Archon semantic block in Phase 1. | Legacy-only; blocked pending Phase 3 (`archon_retry_semantics_unavailable`) |
| `always_run` | Boolean graph scheduling flag. | Enforced |
| `output_type` | Nonempty type label. Legacy accepts it but does not publish a typed artifact. | Legacy-only; blocked pending Phase 2 (`archon_output_type_unavailable`) |

### Node variants

| Node | Required payload | Additional fields | Phase 1 status |
| --- | --- | --- | --- |
| `command` | `command: nonempty string`; inline text or a name below `commands/`. | AI fields below. | Mapped to an isolated Hermes agent worker |
| `prompt` | `prompt: nonempty string`; inline prompt text. | AI fields below. | Mapped to an isolated Hermes agent worker |
| `bash` | `bash: nonempty string`. | Optional deferred `timeout`. | Enforced through the contained process runner |
| `script` | `script: nonempty string` and `runtime: uv | bun`. | `deps` string array; optional deferred `timeout`. Named scripts resolve below `scripts/`. | Enforced when the runtime and resource exist |
| `loop` | `loop` object below. | Common fields except node `retry`. | Enforced with the current Phase 1 loop shape; later Archon loop expansion is not claimed |
| `approval` | `approval` object below. | Common fields, including legacy retry. | Enforced durable compare-and-set user gate |
| `cancel` | `cancel: nonempty string` reason. | Common fields, including legacy retry. | Enforced durable cancellation |

For `bash` and `script`, node `timeout` is a positive number interpreted as
seconds only under `hermes-legacy`. Archon timeout semantics are blocked in
Phase 1, with enforcement-phase metadata 3
(`archon_timeout_semantics_unavailable`); no delivery timing is promised. Use
companion `limits.subprocess_timeout_seconds` today when a package only needs a
stricter Hermes process-policy ceiling; that is not an Archon `timeout`
conversion.

Node `idle_timeout` follows the same profile boundary: its current
`hermes-legacy` runtime value remains seconds and emits
`legacy_idle_timeout_seconds`. The `archon-2026-07` field blocks with
`archon_idle_timeout_semantics_unavailable` until Phase 3 provides the reviewed
millisecond normalization. Phase 1 does not reinterpret the authored value.

### Command and prompt fields

| Field | Shape and present meaning | Phase 1 status |
| --- | --- | --- |
| `persist_session` | Boolean node override for profile-scoped session persistence. | Mapped; fresh context wins |
| `provider` | Nonempty provider profile override. | Mapped; doctor checks authorization and availability |
| `model` | Nonempty model override. | Mapped; doctor checks authorization and availability |
| `output_format` | JSON Schema object. Legacy validates generated output after execution; Phase 1 does not provide the Archon generation/enforcement contract. | Legacy-only; blocked pending Phase 2 (`archon_output_format_unavailable`) |
| `allowed_tools` | Array of nonempty aliases/names; an empty array means no built-in tools. | Mapped and enforced after alias resolution |
| `denied_tools` | Array of nonempty aliases/names; deny is applied after allow. | Mapped and enforced after alias resolution |
| `hooks` | Hook event object documented below. | Mapped per event; doctor blocks events without an equivalent |
| `mcp` | Nonempty package-local MCP definition name. | Mapped when Hermes MCP support is installed; server is worker-scoped |
| `skills` | Array of nonempty skill names. | Mapped to immutable user-message snapshots, not the system prompt |
| `agents` | Mapping from portable agent ID to the inline-agent shape below. | Mapped to bounded `workflow_agent` children |
| `effort` | `low`, `medium`, `high`, or `max`. | Mapped; provider capability applies |
| `thinking` | `adaptive`, `disabled`, or enabled object with positive `budgetTokens`. | Mapped; provider capability applies |
| `maxBudgetUsd` | Positive number. Phase 1 cannot guarantee an enforceable portable cost budget. | Legacy-only/provider-conditional; blocked pending Phase 5 (`archon_budget_enforcement_unavailable`) |
| `systemPrompt` | Nonempty initial worker system prompt. | Mapped only for a fresh/fingerprint-safe context; changing a shared session blocks |
| `fallbackModel` | Nonempty fallback identifier. | Mapped; provider capability applies |
| `betas` | Array of nonempty provider beta names. | Mapped; provider capability applies |
| `sandbox` | Provider/backend mapping object. | Legacy-only/provider-conditional; blocked pending Phase 5 (`archon_sandbox_enforcement_unavailable`) |

Published aliases include `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`,
`WebFetch`, `WebSearch`, `Agent`, and `Task`. Doctor shows the concrete Hermes
mapping. An unknown capitalized alias or an unavailable mapped tool blocks
before a model call.

### Loop, approval, and inline-agent objects

| Object | Field | Shape and present meaning | Status |
| --- | --- | --- | --- |
| `loop` | `prompt` | Required nonempty prompt string. | Enforced |
| `loop` | `until` | Required nonempty completion condition. | Enforced |
| `loop` | `max_iterations` | Required integer from 1 through 100. | Enforced |
| `loop` | `fresh_context` | Current truth-tested option controlling per-iteration context. | Mapped/cache-enforced |
| `loop` | `until_bash` | Current truth-tested deterministic completion command. | Enforced through contained Bash execution |
| `loop` | `interactive` | Current truth-tested interactive-gate option. | Enforced |
| `loop` | `gate_message` | Required and JSON-truthy when `interactive` is true. | Enforced |
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

The generated inventory exposes the nested shape, but the enclosing `retry`
field is blocked for `archon-2026-07` throughout Phase 1.

| Field | Legacy shape and units | Phase 1 status |
| --- | --- | --- |
| `max_attempts` | Integer 1–5 counting total workflow/provider attempts, not retries after the first attempt. | Legacy-only warning `legacy_retry_total_attempts`; Archon retry blocked pending Phase 3 |
| `delay_ms` | Integer 1,000–60,000 milliseconds. | Legacy-only while the Archon retry object is blocked |
| `on_error` | `transient` or `all`. | Legacy-only while the Archon retry object is blocked |

Companion `limits.combined_retries` is an enforceable Hermes total-attempt
ceiling and can tighten package policy now. It is not Archon retry semantics.

## Hook inventory

All published event keys are structurally recognized so doctor can issue an
exact finding. Recognition does not imply a runtime mapping.

| Hook event | Phase 1 status |
| --- | --- |
| `PreToolUse`, `PostToolUse`, `PostToolUseFailure` | Mapped to isolated worker tool lifecycle |
| `SubagentStart`, `SubagentStop`, `TaskCompleted` | Mapped to declared `workflow_agent` child lifecycle |
| `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `Setup`, `InstructionsLoaded` | Mapped to isolated worker/session lifecycle |
| `PermissionRequest` | Mapped to Hermes permission/approval policy; hardline policy remains authoritative |
| `Elicitation`, `ElicitationResult` | Mapped only when MCP support is available |
| `Notification`, `Stop`, `PreCompact`, `TeammateIdle`, `ConfigChange`, `WorktreeCreate`, `WorktreeRemove` | Blocked by doctor: no equivalent Phase 1 node-worker contract |

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
claim OS sandbox portability or enforce `maxBudgetUsd`.

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

This package has no invocation input. It deliberately omits node `timeout` and
`retry`, which are blocked under the Archon profile in Phase 1.

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
3. Convert units or semantics only when a later generated contract no longer
   blocks them. An enforcement-phase number alone is not evidence of
   availability. In Phase 1, do not convert `output_format`, `output_type`,
   `idle_timeout`, `timeout`, `retry`, `maxBudgetUsd`, or `sandbox` into Archon
   claims.
4. Remove any blocker, then declare
   `language_compatibility: archon-2026-07` in the companion.
5. Rerun validate and doctor. Review and trust the new exact digest before a
   run.

Phase 1 explicitly blocks structured `output_format`, published `output_type`,
Archon idle-timeout/timeout and retry meanings, enforceable portable budget,
and sandbox portability. It does not execute Phase 2+ semantics early.

The legacy global `create-workflow` skill is not an authoring authority for
Hermes. OTTO V1 `steps`, `produces`, `context_from`, `verify`, and `iterate`
documents are rejected; convert the desired behavior to the `nodes` DAG and
Hermes companion described here.
