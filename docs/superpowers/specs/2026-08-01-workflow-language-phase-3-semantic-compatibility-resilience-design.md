# Workflow Language Phase 3: Semantic Compatibility and Resilience

**Status:** Proposed subordinate design — awaiting user approval

**Date:** 2026-08-01

**Parent:** `2026-07-25-workflow-language-compatibility-expansion-design.md`

**Predecessor:** `2026-07-30-workflow-language-phase-2-structured-data-design.md`

**Target profile:** `archon-2026-07`

## Purpose

Phase 3 makes timeout, retry, condition, output-reference, large Bash value,
and missing persistent-session behavior explicit and enforceable for the
`archon-2026-07` workflow language profile.

It builds on Phase 2's canonical typed output and durable language snapshot.
It removes the Phase 2 truthiness, reparsing, empty-string, retry, and pathname
adapters only for newly admitted Archon runs. Unversioned and
`hermes-legacy` workflows retain their exact current behavior.

The design preserves byte-stable conversation prompt prefixes and keeps
workflow capability at the plugin edge instead of widening the core model-tool
schema.

## Inputs and compatibility posture

This design is subordinate to the umbrella workflow-language design and the
reviewed Phase 2 contract. It also checks the current upstream Archon authoring
and variable references dated 2026-08-01.

Relevant upstream behavior includes:

- Bash and script `timeout` values are milliseconds with a 120,000 ms default;
- AI nodes default to two retries, while deterministic Bash and script nodes
  run once unless they carry an explicit `retry` block;
- `retry.max_attempts` counts retries after the initial attempt;
- ordered conditions accept quoted numeric operands and reject non-finite
  numeric comparisons;
- output references are restricted to declared dependencies;
- Bash values larger than 32 KiB spill and are read as contents; and
- a missing cross-run persisted session continues fresh and retains the new
  session identity.

Hermes uses **safe Archon compatibility**, not bug-for-bug emulation. Two
upstream behaviors are deliberately strengthened:

1. Archon's independent provider and node retry layers can multiply calls.
   Hermes instead grants both layers from one sealed total-attempt budget.
2. Archon's large-value `$(cat path)` substitution can lose trailing newlines
   and changes quoting behavior by size. Hermes uses a sentinel-preserving
   prologue plus context-specific variable expansion so the substituted value
   remains the value in every quote context.

These differences are visible in normalized admission evidence. They are not
silent compatibility claims.

## Goals

1. Normalize Archon millisecond timeout fields once into finite seconds and cap
   them by the sealed run policy.
2. Normalize retry-after-initial authoring into one non-multiplying total
   attempt ceiling shared by provider and workflow retries.
3. Evaluate conditions against canonical typed outputs with fixed precedence
   and stable failures.
4. Resolve every Archon output reference through one strict resolver.
5. Substitute large Bash values by contents with byte bounds, command-byte
   evidence, and contained private files.
6. Recover only confirmed-missing cross-run persistent sessions by starting
   fresh and replacing the stale registry generation through compare-and-set.
7. Expose bounded, backend-authored compatibility and recovery evidence through
   existing APIs and the Desktop inspector.
8. Preserve all legacy, prompt-cache, attempt-fencing, cleanup, and branded
   customization invariants.

## Non-goals

Phase 3 does not:

- change unversioned or `hermes-legacy` behavior, defaults, snapshots, warnings,
  substitution, condition evaluation, or retry accounting;
- add MCP or skills node kinds; those remain per-node command/prompt options;
- add a core model tool, mutate a live system prompt, rewrite history, or
  transplant session history;
- add `loop.command`, `loop_group`, `include`, `signal_completes`, interactive
  loop finalization, or any other Phase 4 behavior;
- implement `maxBudgetUsd`, model aliases, portable hook normalization, or
  sandbox enforcement from Phase 5;
- add path-taking artifact or recovery endpoints;
- expose raw provider responses, raw spilled values, provider session history,
  or mutable provider storage paths; or
- change the known full-Desktop Prettier baseline or native-Windows platform
  gate.

## Approaches considered

### A. Add profile checks at each existing use site

This would teach the scheduler, each executor, `VariableContext`, session
registry code, API projection, and Desktop independently how Archon differs.
It is a small initial diff, but it creates multiple authorities for units,
attempt counts, output coercion, and recovery classification. A resumed run
could then depend on which call path reparsed it.

**Rejected:** it repeats the distributed-profile-conditional failure mode the
umbrella design explicitly excludes.

### B. Translate Archon YAML into generated legacy Hermes YAML

This could rewrite milliseconds into seconds and retries into totals before
execution. It cannot faithfully represent strict runtime references, typed
condition failures, quote-context Bash materialization, or session recovery.
It would also make generated YAML rather than authenticated source plus the
language snapshot the apparent authority.

**Rejected:** translation loses provenance and cannot encode the resilience
contracts.

### C. Versioned semantic bundle with one strict resolver

The selected design introduces Archon normalizer v3, a sealed per-node
execution-semantics projection, one canonical output-reference resolver, and
small generic seams for missing-session classification and contained byte
materialization. The scheduler consumes only the sealed v3 contracts. Legacy
continues through the existing v2 adapters.

**Selected:** this keeps normalization, admission, execution, persistence, and
projection boundaries explicit while reusing Phase 2's durable authorities.

## Architectural overview

```text
authenticated source + sidecar
    -> resolve profile
       -> legacy: current normalizer v2 and current adapters
       -> Archon: normalizer v3
            -> canonical Phase 2 structured outputs
            -> requested timeout/retry semantics
            -> strict-reference policy
    -> admission intersects requests with sealed run limits
    -> durable language + effective-execution snapshots
    -> scheduler reloads the admitted version
         -> strict condition/reference resolver
         -> one combined attempt ledger
         -> context-safe Bash renderer
         -> classified persistent-session recovery
    -> store journals bounded failures/recovery/command evidence
    -> existing authenticated API and Desktop inspector project backend truth
```

## 1. Versioning and immutable admission

### Profile-specific current versions

Phase 3 adds supported normalizer version 3 without changing the version used
by live legacy packages:

```python
LATEST_NORMALIZER_VERSION = 3
CURRENT_NORMALIZER_BY_PROFILE = {
    WorkflowLanguageProfile.HERMES_LEGACY: 2,
    WorkflowLanguageProfile.ARCHON_2026_07: 3,
}
SUPPORTED_NORMALIZER_VERSIONS = frozenset({1, 2, 3})
```

Loader internals accept `normalizer_version: int | None`. `None` means choose
the current version after resolving the sidecar profile. A sealed run always
passes its recorded integer explicitly.

| Input | Normalizer | Behavior |
|---|---:|---|
| New unversioned package | 2 | Exact current legacy definition, digest, snapshot, and adapters |
| New `hermes-legacy` package | 2 | Exact current legacy definition, digest, snapshot, and adapters |
| Existing admitted v1/v2 run | recorded 1/2 | Reconstructed with its recorded semantics; never upgraded |
| New `archon-2026-07` package | 3 | Phase 2 structured output plus all Phase 3 semantics |

Version 1 and version 2 snapshot shapes remain exact. Version 3 has the exact
versioned fields:

```json
{
  "effective_profile": "archon-2026-07",
  "normalizer_version": 3,
  "normalized_definition_digest": "<sha256>",
  "semantic_fingerprint": "<sha256>",
  "structured_outputs": {},
  "node_semantics": {}
}
```

`node_semantics` is keyed by admitted node ID and sorted canonically. Each
entry contains only applicable normalized requested fields:

```json
{
  "wall_timeout_seconds": 30.0,
  "idle_timeout_seconds": 15.0,
  "retry": {
    "explicit": true,
    "requested_retries": 2,
    "requested_total_attempts": 3,
    "delay_ms": 3000,
    "on_error": "transient"
  }
}
```

The v3 normalized-definition digest and semantic fingerprint include this
projection and the Phase 2 structured-output projection. Exact field sets,
finite numeric encoding, node count, and canonical byte limits are validated
when writing and reading the snapshot.

### Requested versus effective semantics

The language snapshot records normalized **requested** semantics. Admission
also records `phase3_effective_node_semantics` in the existing bounded
`resources.json` execution-policy document. That projection intersects
requested values with the sealed `RunExecutionLimits`:

- timeout values are capped by the appropriate wall, idle, and subprocess
  ceilings;
- retry totals are capped by `combined_retries`; and
- `capped: true` records an intersection that changed the request.

The projection is bounded to the workflow's existing maximum node count and a
fixed exact field set. It contains no source bodies, output values, paths, or
provider response data.

On resume, Hermes authenticates the sealed bytes, reloads them with the
recorded version, verifies the language snapshot and fingerprint, verifies the
effective projection against the sealed limits, and executes that projection
rather than current config. A mismatch is
`workflow_language_snapshot_mismatch` or
`workflow_execution_semantics_mismatch`. There is no in-place active-run
migration.

## 2. Timeout normalization

### Archon v3 contract

Only these Phase 3 combinations are enforceable:

| Field | Node kinds | Authored unit | Absent value | Effective cap |
|---|---|---|---|---|
| `timeout` | `bash`, `script` | milliseconds | no authored override; sealed subprocess default applies | `subprocess_timeout_seconds` |
| `idle_timeout` | `command`, `prompt` | milliseconds | no authored override; sealed AI idle default applies | minimum of AI idle and wall ceilings |

An authored value must be a positive finite integer or finite float. Boolean,
zero, negative, NaN, and infinity are rejected at load time. Normalization is
`float(milliseconds) / 1000.0`; the finite positive result is stored once in
`node_semantics`. Executors never reinterpret the source field.

An Archon `timeout` or `idle_timeout` on `loop`, `approval`, `cancel`, or a node
kind where it has no enforceable Phase 3 meaning is blocking with
`archon_timeout_node_unsupported` or
`archon_idle_timeout_node_unsupported`. Loop timing remains a Phase 4 concern.

At execution, one absolute monotonic `DeadlineBudget` remains authoritative.
Nested provider requests receive only the remaining wall duration and the
sealed provider-request ceiling. A retry cannot reset or extend the admitted
node deadline.

### Legacy contract

Legacy continues to read raw `timeout` and `idle_timeout` as seconds through
the current code paths. It retains the existing defaults, caps, errors, and
doctor codes `legacy_timeout_seconds` and `legacy_idle_timeout_seconds`.

## 3. Retry normalization and attempt accounting

### Authoring defaults

For Archon v3:

| Node kinds | No `retry` block | `retry` block |
|---|---|---|
| `command`, `prompt` | 2 retries, 3,000 ms delay, `transient` | omitted fields use those AI defaults |
| `bash`, `script` | exactly 1 total attempt | `max_attempts` required; delay defaults to 3,000 ms and `on_error` to `transient` |
| `loop`, `approval`, `cancel` | current non-retry behavior | `retry` is blocking/invalid in Phase 3 |

`max_attempts` accepts integers 1 through 5 and means retries after the initial
execution. Normalization derives:

```text
requested_total_attempts = 1 + requested_retries
effective_total_attempts =
    min(requested_total_attempts, sealed combined_retries)
```

The sealed Hermes combined budget remains 1 through 5. An authored value of 5
requests six total attempts but receives at most five. Admission records the
request, effective total, and `capped: true`; it never silently turns five
retries into five additional provider attempts.

Legacy continues to interpret `max_attempts` as the current total-attempt
ceiling with the current 1,000 ms default delay and fallback behavior.

### One ledger, not two multiplying loops

The node projection's `retry_consumed` remains the durable total charge. Terms
are fixed:

- **workflow attempt**: one claimed executor invocation;
- **provider attempt**: one model/provider API try inside an AI executor;
- **additional provider attempts**: provider attempts after the first provider
  attempt in the current workflow attempt; and
- **effective total attempts**: the maximum combined charge for the node.

Before execution:

```text
remaining = effective_total_attempts - retry_consumed
max_provider_attempts = remaining
```

After execution:

```text
charged = 1 workflow attempt + exact additional_provider_attempts
retry_consumed = min(effective_total_attempts, prior + charged)
```

The isolated agent protocol continues to expose `provider_attempts` as the
exact additional-attempt count. Structured-output evidence reports total
provider calls and is converted exactly once by subtracting the initial call.
If exact evidence is missing or invalid, Hermes conservatively charges the
entire grant. Provider repair and fallback calls draw from the same grant.

For example, an AI node with two retries has an effective total of three. If
the provider consumes all three calls in the first workflow attempt, no node
retry remains. If it consumes one call and returns a retryable failure, at most
two later workflow attempts remain.

### Retry eligibility

Stable classes are:

- `FATAL`: authentication, authorization, credit exhaustion, validation,
  invalid request, output/resource limit, cleanup failure, and sealed-contract
  drift; never retried, even with `on_error: all`;
- `TRANSIENT`: provider stall/timeout, rate limit, service unavailability,
  network disconnect, and a contained deterministic failure explicitly proven
  retryable; retried when capacity remains;
- `UNKNOWN_ERROR`: an unclassified failure with a known no-effect outcome;
  retried only with `on_error: all`; and
- `UNKNOWN_OUTCOME`: outward, uncertain, or potentially completed effects;
  never replayed and routed to reconciliation where applicable.

Nodes listed in authenticated `outward_action_nodes`, results carrying
`unknown_side_effect` or `outcome_unknown`, and failures whose process/provider
effect cannot be classified are never automatically replayed. An explicit
Bash/script retry block authorizes retry only for a known retryable outcome; it
does not override effect uncertainty.

Cancellation is checked before waking due retries, before a claim, before
provider launch, and while waiting. Cancellation or shutdown wins without
consuming a new attempt.

Retry evidence uses `requested_retries`, `effective_total_attempts`,
`retry_consumed`, `remaining_attempts`, `additional_provider_attempts`, and
`capped`. Ambiguous outward `max_attempts` remains only in source-facing
compatibility findings.

## 4. One strict output-reference resolver

### Static admission rules

Archon v3 scans every authenticated interpolation surface:

- `when`;
- inline `prompt`, `bash`, and `script` bodies;
- authenticated command bodies;
- approval messages and rejection prompts; and
- existing loop prompt and `until_bash` bodies, without adding loop syntax or
  execution behavior.

Every `$producer.output` and `$producer.output.path` reference must name a node
listed directly in the consumer's `depends_on`. A transitive ancestor is not
enough; migration adds it to `depends_on`. Unknown, self, downstream, and
parallel references fail with `output_reference_not_declared_dependency` at
the exact source surface.

Field references require a Phase 2 declared structured-output contract on the
producer. A schema-proven-impossible path remains
`structured_output_field_impossible`. Whole-output references remain valid for
canonical text outputs.

Command files are checked only after authenticated bytes are resolved and
sealed. The check occurs before run promotion; command content is never
reopened through a mutable pathname.

Legacy retains the existing condition-only upstream check and Phase 2 static
field check.

### Runtime resolver

`plugins/workflow/output_resolution.py` owns an immutable resolver result and a
typed `WorkflowOutputReferenceError(code, node_id, path)` hierarchy. Conditions
and all substitution methods call this resolver; none reparse provider text.

Strict rules are:

1. The producer has one successful winning attempt and one verified canonical
   output descriptor.
2. A whole-output reference returns verified canonical text.
3. A field path traverses only a Phase 2 canonical structured value whose
   output has a schema fingerprint.
4. Mapping segments are exact keys. Sequence segments are canonical
   non-negative decimal indexes. Missing keys, invalid indexes, and scalar
   descent are distinct typed failures.
5. JSON-looking schemaless text never gains structured-field semantics.
6. Strings render as exact text; null, booleans, numbers, arrays, and objects
   render as finite canonical JSON for prompt/script substitution.
7. Integrity failure is terminal. A transient host read failure remains
   transient and is not cached as a missing output.

Stable runtime codes are:

| Code | Meaning | Outcome |
|---|---|---|
| `output_reference_missing` | declared producer has no winning output | consumer fails |
| `output_reference_not_structured` | field access targets schemaless text | consumer fails |
| `output_reference_field_missing` | mapping key or sequence index is absent | consumer fails |
| `output_reference_path_type` | path descends through an incompatible value | consumer fails |
| `output_reference_integrity` | descriptor, digest, media, or winning identity changed | consumer fails |
| `output_reference_temporarily_unavailable` | retryable host read failed | scheduler yields without changing node state |

For a claimed executor, resolver failure is converted at the scheduler boundary
to a terminal `NodeExecutionResult` with zero additional provider attempts and
`archon_terminal_failure: true`. It cannot become `executor_crash`, validation
fallback, empty text, or an `on_error: all` retry.

## 5. Typed condition evaluation

### Grammar and precedence

V3 remains deliberately small:

```text
expression := and_group ("||" and_group)*
and_group  := clause ("&&" clause)*
clause     := output_reference operator literal
operator   := "==" | "!=" | "<" | "<=" | ">" | ">="
literal    := single_quoted_string | double_quoted_string | decimal_number
```

There are no parentheses, functions, truthiness expressions, arithmetic,
contains operators, code evaluation, or implicit environment variables.
Evaluation is left-to-right and short-circuiting; `&&` binds more strongly than
`||`.

### Typed comparisons

Equality is type-directed:

- a quoted RHS compares only with a string LHS;
- an unquoted decimal RHS compares only with a finite numeric LHS;
- string/number mismatches are errors, not false/true coercions; and
- booleans, null, arrays, and objects are invalid operands.

Ordered comparison requires finite numeric operands. The LHS may be a
canonical integer/float or a string matching the exact decimal grammar after
outer ASCII whitespace is removed. The RHS may be an unquoted decimal or the
documented quoted-number form. Numeric strings are the only permitted
condition coercion. Exponents, locale formats, hexadecimal, booleans, empty
strings, NaN, infinity, arrays, objects, and partial parses are rejected.

Comparison uses a finite decimal representation derived from the canonical
value, avoiding binary-float ordering surprises. It does not turn the value
back into provider text.

Stable condition failures are:

- `condition_operand_type`;
- `condition_operand_nonfinite`;
- `condition_numeric_invalid`; and
- `condition_runtime_syntax_invalid` for a sealed expression that no longer
  matches the admitted grammar.

A valid false condition transitions the node to `skipped` with
`condition_false`. A typed condition/reference failure transitions the pending
node directly to `failed`, records the stable code and bounded message, and
causes ordinary dependency/trigger propagation. It is not a skip warning.

`RunStore` performs the pending-to-failed compare-and-set under the run lock,
appends `node_failed`, sets bounded `last_error`, and records zero attempts and
zero retry consumption. The error is durable even though no executor is
claimed.

Legacy continues through current `evaluate_condition` and `VariableContext`
adapters, including existing skip-on-evaluation-error, reparsing, and
empty-string behavior.

## 6. Safe large Bash substitution

### Bounds

Archon v3 measures UTF-8 bytes, never Python characters:

- inline values: at most 32,768 bytes;
- one spilled value: at most 500,000 bytes;
- distinct spill files per Bash attempt: at most 64; and
- total spilled bytes per Bash attempt: at most 2 MiB.

Repeated references to the same canonical bytes reuse one spill entry. A NUL
byte is `bash_substitution_nul` because POSIX shell variables cannot carry it.
A count or byte overflow is `bash_substitution_limit`. Values at or below
32,768 bytes use current context-aware inline quoting.

Legacy retains the current 8,192-character threshold and pathname substitution
exactly.

### Contained materialization

Spills live below the current attempt directory in `variables-v3/`. Creation
uses descriptor-relative traversal, `O_NOFOLLOW` where required, `O_EXCL`,
regular-file and single-link verification, mode `0600`, bounded writes, and
`fsync`. A host without required safe primitives fails closed with
`bash_spill_integrity`; it does not fall back to pathname checks.

Filenames are opaque deterministic indexes. Paths are engine-internal and never
accepted from YAML, API parameters, or provider output. Existing run cleanup
owns the files as part of the attempt tree.

### Content-preserving shell rendering

For each distinct spill, the renderer prepends a deterministic POSIX prologue:

```sh
__HERMES_WF_SPILL_abcd=$(command cat '/contained/file'; printf x)
__HERMES_WF_SPILL_abcd=${__HERMES_WF_SPILL_abcd%x}
```

The sentinel prevents command substitution from stripping trailing newlines.
Removing the shortest trailing `x` removes only the appended byte, even when
the value itself ends in `x`.

The placeholder replacement depends on lexical quote context:

| Context | Replacement |
|---|---|
| unquoted | `"${__HERMES_WF_SPILL_abcd}"` |
| inside double quotes | `${__HERMES_WF_SPILL_abcd}` |
| inside single quotes | `'"${__HERMES_WF_SPILL_abcd}"'` (close, expand quoted, reopen) |

The result supplies contents, not a pathname, and preserves spaces, quotes,
dollar signs, backticks, globs, Unicode, and trailing newlines without
evaluation.

### Command-byte authority and evidence

The renderer returns immutable `RenderedBashCommand` containing:

- the exact command passed as `argv[-1]` to `/bin/sh -c` or the existing
  platform-gated Bash path;
- SHA-256 and UTF-8 byte size of the authenticated template;
- SHA-256 and UTF-8 byte size of the rendered command; and
- spill count, total bytes, and content digests.

The executor executes that exact command; it never reconstructs it from
evidence or rereads a mutable command source. Attempt metadata carries only
bounded sizes and digests, never command text, values, or spill paths.

The Phase 4 loop `until_bash` path does not gain the v3 spill prologue in this
phase. It may use strict references, but its Bash materialization remains
current loop behavior until Phase 4 defines the complete loop contract.

## 7. Missing persistent-session recovery

### Generic classification seam

The generic isolated plugin-agent facade adds
`PluginAgentSessionMissingError`, carrying no provider response or history.
Its parent-process preflight raises this type only when the profile-local
`SessionDB.get_session(exact_id)` returns `None`. Session database open/read
errors remain distinct operational failures.

The child worker emits sanitized
`failure_kind: persistent_session_missing` if the session disappears between
parent preflight and worker load. It must report zero provider attempts to be
recovery-eligible. This concrete seam adds no model tool and no workflow
dependency to the agent core.

### Source-sensitive workflow behavior

The AI executor records shared-context source as `same_run_predecessor` for
explicit `context: shared`, or `cross_run_registry` for an implicit persistent
registry record.

For Archon v3 only:

1. **Same-run predecessor missing:** terminal `context_missing_session`, zero
   provider attempts, never fresh.
2. **Cross-run record confirmed missing:** retain its generation as the CAS
   expectation, record recovery evidence, and submit once as fresh.
3. **Session database unavailable, corrupt, ambiguous, or denied:** fail as
   `persistent_session_recovery_unavailable`. Only confirmed absence recovers.
4. **Fingerprint mismatch:** preserve current fresh-context behavior and
   warning; it is not missing-session recovery.

If only the worker observes the race, the executor may replace the zero-provider
shared request with one fresh request inside the same workflow attempt. It does
this at most once, uses the original provider grant, and does not charge a retry
for the zero-provider preflight failure.

After successful fresh execution returns a non-empty session ID:

```text
compare_and_set(
    key,
    expected_stale_generation,
    new_session_id,
    fingerprint,
)
```

CAS success replaces stale state. CAS loss retains a newer concurrent entry
without rerunning the successful node. Fresh execution failure leaves the
registry generation unchanged.

The profile, workflow, node, operator-scope digest, provider, and runtime
profile remain in the key. Recovery never crosses those boundaries and never
copies old messages into the fresh session.

Legacy and admitted Archon v1/v2 runs retain current missing-session behavior.

### Durable recovery evidence

Before the fresh provider request, an active-claim store callback appends
`persistent_session_missing_fresh_start` and one bounded
`session_recoveries` entry. It includes:

- attempt ID;
- registry generation;
- SHA-256 of the missing session ID and cache fingerprint;
- source `cross_run_registry`;
- bounded provider and runtime-profile identifiers;
- `provider_attempts_before_recovery: 0`; and
- outcome `fresh_start_selected`.

A fenced post-execution update records `stale_entry_replaced`,
`newer_entry_retained`, or `fresh_execution_failed`. The list is capped by the
admitted combined-attempt ceiling. Journal reserve includes both recovery
frames before worker allocation.

The existing `recovery` evidence kind projects these alongside process
recovery with `recovery_kind: persistent_session`. It never exposes raw session
IDs, fingerprints, history, provider responses, or provider storage paths.

## 8. Persistence and crash behavior

Phase 3 extends existing JSON snapshot and journal documents before considering
SQLite columns:

- v3 language requests live in `resources.json.language`;
- capped node execution semantics live in sealed execution policy;
- condition failures and retry consumption remain run-journal authority;
- Bash spill files remain attempt-owned and are never an index;
- session recovery selection/outcome is fenced to the active claim; and
- the session registry remains generation-CAS SQLite state.

Crash rules are:

- before `persistent_session_missing_fresh_start`, no evidence claims recovery;
- after selection but before provider launch, ordinary zero-effect interrupted
  claim recovery applies;
- after provider launch, existing uncertainty rules apply and no silent replay
  is allowed;
- after provider success but before session CAS, no registry state is invented
  from unjournaled response data;
- a stale CAS loser never overwrites the winner; and
- Bash spill evidence without a corroborated attempt never becomes output or
  command authority.

A SQLite migration is added only if tests prove existing snapshot/journal
authorities cannot meet atomicity or bounded-query requirements. None is
expected for normalization, conditions, references, or Bash substitution.

## 9. API and Desktop boundaries

### API

Backend projections remain authoritative and bounded:

- language status accepts normalizer version 3 and exposes existing profile,
  digest, and finding fields;
- catalog lists retain only bounded compatibility summaries;
- workflow detail may add bounded Phase 3 finding/migration text, never source
  expansion or output bodies;
- run summaries expose unambiguous v3 retry/error fields;
- `/runs/{run_id}/evidence?kind=recovery` includes bounded persistent-session
  recovery records; and
- existing artifact routes remain publication-ID based.

There is no path-taking endpoint, spill-file endpoint, session-history
endpoint, or raw provider-response field. Profile/scope authorization and
sanitization remain in force.

### Desktop

Desktop remains a projection of backend truth:

- language status accepts additive normalizer version 3;
- compatibility blockers and migration guidance come from the backend;
- the existing Run Inspector recovery tab renders generic bounded evidence,
  including `recovery_kind: persistent_session`; and
- typed failures use existing run/node error surfaces.

No renderer-side condition parser, retry calculator, output resolver, Bash
renderer, session probe, or filesystem access is added. An older Desktop
ignores additive v3 fields; a newer Desktop treats missing fields from an older
backend as unavailable rather than inferring them.

## 10. Prompt caching and narrow-waist guarantees

Phase 3 does not change the model tool schema. MCP and skills remain selected
per command/prompt node and execute inside the existing isolated request.

Normalized schema, retry, timeout, and recovery data never enter a live system
prompt. Strict substitution changes only the initial node prompt or
authenticated command body before its isolated conversation begins.
Persistent-session recovery selects fresh before constructing the isolated
agent. It never edits historical messages or alternation.

Generic core additions are limited to a typed missing-session classification at
the existing isolated plugin-agent boundary. Contained Bash materialization
stays inside the workflow plugin unless implementation proves a second
concrete generic consumer.

## 11. Compatibility findings and migration

For valid v3 Archon fields, Phase 2 blockers
`archon_timeout_semantics_unavailable`,
`archon_idle_timeout_semantics_unavailable`, and
`archon_retry_semantics_unavailable` disappear. Invalid node applicability,
unsafe shapes, and later-phase fields remain blocking.

Legacy doctor findings remain warnings and gain exact guidance:

- multiply legacy timeout seconds by 1,000 before declaring Archon;
- for legacy explicit total attempts `N >= 2`, use Archon
  `max_attempts: N - 1`, then check the sealed combined cap;
- for a legacy one-attempt deterministic node, omit `retry` under Archon;
- for an AI node that must have only one total attempt, do not migrate until an
  explicit compatible opt-out exists; v3 defaults AI nodes to three attempts;
- add every referenced producer directly to `depends_on`;
- add `output_format` before `.field` references;
- replace boolean/object/array/string-number coercion conditions with a
  structured scalar decision value; and
- validate Bash at the 32,768-byte boundary and remove pathname assumptions.

`workflow doctor`, generated schema/editor contracts, website docs, and the
workflow-builder reference derive status, units, defaults, and stable codes
from the central field inventory.

## 12. Error and evidence contract

Every new durable error has a stable short code, bounded message, exact node
identity, and no raw value.

| Area | Stable codes/events |
|---|---|
| Normalization | `workflow_execution_semantics_mismatch`, `archon_timeout_node_unsupported`, `archon_idle_timeout_node_unsupported` |
| References | `output_reference_not_declared_dependency`, `output_reference_missing`, `output_reference_not_structured`, `output_reference_field_missing`, `output_reference_path_type`, `output_reference_integrity`, `output_reference_temporarily_unavailable` |
| Conditions | `condition_operand_type`, `condition_operand_nonfinite`, `condition_numeric_invalid`, `condition_runtime_syntax_invalid` |
| Bash | `bash_substitution_nul`, `bash_substitution_limit`, `bash_spill_integrity` |
| Sessions | `context_missing_session`, `persistent_session_recovery_unavailable`, `persistent_session_missing_fresh_start` |

Codes enter the central compatibility/evidence catalog and its duplicate and
completeness tests. Messages may improve; code meaning and version
applicability do not change within v3.

## 13. Testing and verification

Implementation begins only after design and plan approval. Every task proves
RED through `scripts/run_tests.sh` before production edits and uses fresh
implementer plus independent specification and quality review handoffs.

### Normalization and admission

Tests cover exact legacy v2 digests and behavior; v1/v2 resume; deterministic
v3 snapshot fields; positive finite millisecond conversion; default, explicit,
capped, and invalid retry shapes by node kind; requested/effective mismatch;
immutable sealed limits across config changes; and generated schema/editor,
doctor, catalog, and detail agreement.

### Conditions and references

Tests cover direct dependencies on every authenticated surface; whole text and
declared structured fields; mappings, arrays, indexes, missing paths, scalar
descent, and integrity loss; schemaless JSON-looking text; string and decimal
comparison matrices; quoted numeric ordering; rejected booleans, null,
containers, exponents, locale numbers, NaN, and infinity; precedence and short
circuit; false-to-skipped versus typed-error-to-failed; transient I/O yielding
without cached misses or hot loops; and exact legacy adapters.

### Retry and cancellation

Tests cover AI default two retries; deterministic default one attempt; explicit
1 and 5; combined caps 1 through 5; provider-only, workflow-only, mixed, repair,
fallback, and unknown-count accounting proving calls never exceed the ceiling;
fatal/transient/unknown/outward/reconciliation classification; explicit
deterministic retry; cancellation at every wake/claim/backoff/provider
boundary; and multiprocess races.

### Bash

Real `/bin/sh` tests cover 32,767, 32,768, and 32,769 UTF-8 bytes; multibyte
boundaries; unquoted, double-quoted, and single-quoted placeholders; shell
metacharacters, empty values, terminal `x`, and trailing newlines; deduplication;
64/65 files and total bounds; NUL; symlink/escape attacks; Windows command
construction; exact `argv[-1]` evidence; cleanup; and legacy pathname behavior.

### Persistent sessions

Tests use real profile-local `SessionDB`, registry, store, journal, and isolated
runner seams for first run, warm resume, confirmed missing, same-run missing,
fingerprint mismatch, DB failure, worker race, fresh failure, CAS success/loss,
cancellation, crash boundaries, scope/profile/provider separation, sanitized
evidence, and bounded history.

### API, Desktop, and installed flows

Tests cover authenticated catalog/detail/run/evidence projection, old/new
backend-Desktop additive compatibility, generic recovery rendering, absence of
path parameters/raw values, installed discovery under temporary `HERMES_HOME`,
and representative official Archon fixtures adapted only where Hermes
documents a stronger safe contract.

### Final gates

The final task runs:

1. all focused tests through `scripts/run_tests.sh`;
2. the canonical Python suite through `scripts/run_tests.sh` with flaky retries
   disabled for evidence;
3. scoped Desktop typecheck, Vitest, ESLint, and Prettier without rewriting the
   20 unrelated known Prettier failures;
4. language schema/website generation drift checks;
5. installed-distribution smoke tests;
6. upstream/OTTO/LOOP24 customization-ledger validation; and
7. temporary merge rehearsals against pinned refs when generic seams changed.

No push, publication, worktree deletion, literal-`main` mutation, or brand
propagation is part of Phase 3 unless separately authorized.

## 14. Documentation updates

Implementation updates:

- `website/docs/user-guide/features/workflow-yaml-reference.md` with v3 units,
  defaults, strict failures, Bash byte behavior, recovery, and migration;
- `skills/software-development/workflow-builder/references/portable-schema.md`
  with generated field status and authoring guidance;
- generated JSON Schema/editor metadata from the central inventory;
- operator guidance for `workflow doctor` and Run Inspector recovery; and
- retained SDD progress and review reports for implementation tasks.

Documentation explicitly says MCP and skills are options, not node kinds, and
loops/includes remain Phase 4.

## References

- Parent design: `docs/superpowers/specs/2026-07-25-workflow-language-compatibility-expansion-design.md`
- Phase 2 design: `docs/superpowers/specs/2026-07-30-workflow-language-phase-2-structured-data-design.md`
- Archon authoring workflows: <https://archon.diy/guides/authoring-workflows/>
- Archon variable reference: <https://archon.diy/reference/variables/>
- Archon script nodes: <https://archon.diy/guides/script-nodes/>
- Archon constitution: <https://archon.diy/reference/workflow-language-constitution/>

## Proposed design decisions

1. New legacy packages stay on normalizer v2; new Archon packages use v3.
2. V3 records requested language semantics and capped effective execution
   semantics separately, then verifies both on resume.
3. Retry authoring counts retries after initial, but provider and workflow
   layers share one total-attempt ledger capped at five.
4. AI nodes default to two retries; deterministic nodes default to no retry.
5. All v3 output references use one canonical resolver and direct-dependency
   admission rule.
6. A false condition skips; a typed condition/reference error fails durably
   without an executor attempt.
7. Large Bash values use a 32,768-byte threshold, bounded descriptor-relative
   spills, sentinel-preserving content loading, and exact command digests.
8. Missing same-run shared context fails; only a confirmed-missing cross-run
   registry session may start fresh.
9. Recovery replaces stale session state only after successful fresh execution
   and only through generation CAS.
10. Existing authenticated APIs and generic Desktop inspection extend
    additively; no path-taking or raw-provider surface is introduced.
11. Phase 4 loops/includes and Phase 5 provider portability remain out of scope.
