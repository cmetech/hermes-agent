# Workflow Language Phase 3: Semantic Compatibility and Resilience

**Status:** Revised after independent review — awaiting user approval

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
   remains the value in every admitted quote context.

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

The trust risk identity also includes effective profile, normalizer version,
and normalized-definition digest. Therefore a previously trusted Archon
package must be reviewed and trusted again when a runtime moves its new-run
semantics from v2 to v3, even when source bytes are unchanged. Legacy remains
on v2 and does not acquire a trust-digest change from this phase.

### Requested versus effective semantics

The language snapshot records normalized **requested** semantics. Admission
also records `phase3_execution_semantics` in the existing bounded
`resources.json` document. Its exact version-1 shape is:

```json
{
  "schema_version": 1,
  "normalizer_version": 3,
  "limits": {
    "ai_idle_timeout_seconds": 300.0,
    "ai_wall_timeout_seconds": 1800.0,
    "provider_request_timeout_seconds": 300.0,
    "subprocess_timeout_seconds": 120.0,
    "combined_total_attempts": 5
  },
  "nodes": {
    "node-id": {
      "requested_attempt_wall_timeout_seconds": 120.0,
      "attempt_wall_timeout_seconds": 120.0,
      "requested_idle_timeout_seconds": null,
      "idle_timeout_seconds": null,
      "provider_request_timeout_seconds": null,
      "timeout_source": "archon_default",
      "timeout_capped": false,
      "retry": {
        "explicit": false,
        "requested_retries": 0,
        "requested_total_attempts": 1,
        "effective_total_attempts": 1,
        "delay_ms": 3000,
        "on_error": "transient",
        "capped": false
      }
    }
  }
}
```

The `limits` object has exactly those five fields. The existing internal
`RunExecutionLimits.combined_retries` value is normalized once into
`combined_total_attempts`; despite the historical name, its v3 unit is the
maximum combined charge including the initial attempt. Every node entry has
exactly the shown fields; inapplicable requested/effective idle/provider values
are JSON null. Valid
`timeout_source` values are `authored`, `archon_default`, and
`profile_ceiling`. `timeout_capped` is true whenever an effective wall or idle
value is lower than its requested value. All non-null numbers are positive
finite JSON numbers. Retry ranges are field-specific:
`requested_retries` is 0 through 5, `requested_total_attempts` is 1 through 6,
and both `effective_total_attempts` and the limit
`combined_total_attempts` are 1 through 5. Thus authored `max_attempts: 5`
round-trips as requested total 6, effective total 5, and `capped: true`.

Admission resolves current profile configuration plus authenticated sidecar
limits once, before `RunStore.prepare_run_snapshot()` publishes the immutable
snapshot. The resolved limit object is passed explicitly through CLI, API,
gateway, showcase, and scheduled admission; `RunStore` never guesses caller
configuration. Direct test/store callers receive the documented default limit
object unless they pass one explicitly. The projection then intersects
requested values with those limits:

- timeout values are capped by the appropriate wall, idle, and subprocess
  ceilings;
- retry totals are capped by `combined_total_attempts`; and
- `timeout_capped` or retry `capped` records an intersection that changed its
  request.

The projection is bounded to the workflow's existing maximum node count and a
fixed exact field set. Its canonical JSON bytes participate in
`input_manifest_digest` and the sealed resource manifest. It contains no
source bodies, output values, paths, or provider response data.

On resume, Hermes authenticates the sealed bytes, reloads them with the
recorded version, verifies the language snapshot and fingerprint, reads the
sealed v3 execution projection, verifies it against the normalized request,
and executes it directly. It does not call current-config limit resolution for
v3 nodes. A mismatch is
`workflow_language_snapshot_mismatch` or
`workflow_execution_semantics_mismatch`. There is no in-place active-run
migration.

## 2. Timeout normalization

### Archon v3 contract

Only these Phase 3 combinations are enforceable:

| Field | Node kinds | Authored unit | Absent value | Effective cap |
|---|---|---|---|---|
| `timeout` | `bash`, `script` | milliseconds | Archon default 120,000 ms | `subprocess_timeout_seconds` |
| `idle_timeout` | `command`, `prompt` | milliseconds | no authored override; sealed AI idle default applies | minimum of AI idle and wall ceilings |

An authored value must be a positive finite integer or finite float. Boolean,
zero, negative, NaN, and infinity are rejected at load time. Normalization is
`float(milliseconds) / 1000.0`; the finite positive result is stored once in
`node_semantics`. Executors never reinterpret the source field.

An omitted Archon Bash/script timeout normalizes to requested `120.0` seconds
with `timeout_source: archon_default`. Its effective attempt timeout is
`min(120.0, subprocess_timeout_seconds)`, so a ceiling below 120 seconds
tightens it and a ceiling above 120 seconds does not replace the upstream
default. An authored timeout uses `timeout_source: authored`. Command/prompt
wall duration comes from the sealed AI wall ceiling and uses
`timeout_source: profile_ceiling`.

An Archon `timeout` or `idle_timeout` on `loop`, `approval`, `cancel`, or a node
kind where it has no enforceable Phase 3 meaning is blocking with
`archon_timeout_node_unsupported` or
`archon_idle_timeout_node_unsupported`. Loop timing remains a Phase 4 concern.

Timeout is a per-workflow-attempt contract, matching Archon's node-retry
behavior. At each claim, Hermes creates one monotonic `DeadlineBudget` from the
sealed effective attempt duration. Nested provider requests and structured
repair within that attempt receive only its remaining duration and the sealed
provider-request ceiling. A workflow retry receives a new attempt deadline;
retry backoff is outside the prior attempt and cannot multiply the separately
sealed attempt count. Coordinator restart cannot extend an active claimed
attempt: existing claim/process recovery decides whether its outcome is known,
stopped, or uncertain before any later claim. Phase 3 does not invent a
cross-retry total-node deadline absent from the parent or upstream contract.

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

V3 uses one ASCII reference grammar in field inventory, schema validation,
admission scanning, condition parsing, and rendering:

```text
node_id      := [A-Za-z_][A-Za-z0-9_-]*
reference    := "$" node_id ".output" ("." path_segment)*
path_segment := [A-Za-z_][A-Za-z0-9_-]* | "0" | [1-9][0-9]*
```

New Archon packages reject node IDs outside that grammar with
`archon_node_id_not_reference_safe`. Legacy keeps its current identifier
acceptance. Dots separate fields and are not permitted inside a referencable
mapping key; Phase 3 does not add bracket or escape syntax. A structured schema
may contain such keys, but attempting to reference one is a load-time
`output_reference_path_unsupported` error.

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

Named script resources are authenticated executable bytes but are not runtime
interpolation surfaces: current named scripts receive workflow values through
their environment. Under v3, a recognized output reference in sealed named
script bytes blocks with `named_script_output_reference_unsupported` rather
than remaining silently literal. Phase 3 does not create a generated mutable
script copy. Inline scripts remain strict interpolation surfaces.

Legacy retains the existing condition-only upstream check and Phase 2 static
field check.

### Runtime resolver

`plugins/workflow/output_resolution.py` owns an immutable
`ResolvedOutputReference` plus a typed
`WorkflowOutputReferenceError(code, node_id, path)` hierarchy. The result has
two explicit facets:

- `typed_value`: the Phase 2 canonical value for a declared structured output,
  or the exact string for schemaless whole output; and
- `rendered_text`: canonical whole-output bytes decoded as UTF-8 for a whole
  reference, or deterministic field rendering (raw string, otherwise finite
  canonical JSON) for a field reference.

Conditions consume `typed_value`. Prompt, inline-script, approval, and Bash
substitution consume `rendered_text`. No consumer reparses provider text or
substitutes one facet for the other.

Strict rules are:

1. The producer has one successful winning attempt and one verified canonical
   output descriptor.
2. A whole-output reference returns both verified canonical typed value and
   verified canonical text when structured, or the same string in both facets
   when schemaless.
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
| `output_reference_temporarily_unavailable` | retryable host read failed | fenced resolution wait; no executor attempt |
| `output_reference_unavailable` | bounded resolution reads exhausted | consumer fails |

The transient code is excluded from terminal conversion. Before a condition or
claim, `RunStore.defer_output_resolution()` compare-and-sets a pending/ready
consumer into a resolution wait with `resolution_read_count`, immutable
producer publication identity, and `next_resolution_at`. The first failed
observation schedules 250 ms; failed wake observations one through four
schedule 500 ms, 1 s, 2 s, and 4 s. The fifth failed wake (six failed
observations total) transitions to terminal `output_reference_unavailable`.
Ordinary runnable selection and graph evaluation skip the node until the
durable wake. A successful read clears the fields. Multiprocess coordinators
race through the same store CAS, so only one wake is recorded. These reads
consume neither workflow attempts nor provider budget and survive restart
without a hot loop.

Every other resolver failure arising after a claim is converted at the
scheduler boundary to a terminal `NodeExecutionResult` with zero additional
provider attempts and `archon_terminal_failure: true`. It cannot become
`executor_crash`, validation fallback, empty text, or an `on_error: all` retry.

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
canonical structured integer/float. A schemaless whole-output string may also
match the exact decimal grammar after outer ASCII whitespace is removed,
preserving the documented plain-text numeric condition. A string-valued field
from a declared structured output remains a string and is never coerced to a
number. The RHS may be an unquoted decimal or the documented quoted-number
form. Those two syntax-directed cases are the only numeric text conversions.
Exponents, locale formats, hexadecimal, booleans, empty strings, NaN,
infinity, arrays, objects, partial parses, and structured strings are rejected
for ordering.

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

Root structured scalar behavior follows the same facets: a structured root
number is numeric, a structured root string is a string without JSON quote
characters, and root boolean/null/array/object values reach the typed rejection
path. Substitution of the same root uses its canonical `rendered_text` rather
than the condition facet.

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
`fsync`. After writing, the materializer opens each file read-only through the
verified descriptor chain, checks identity, size, and digest, rewinds it, and
keeps that descriptor open through process launch. A host without safe
descriptor creation and inheritance fails closed with `bash_spill_integrity`;
it does not fall back to pathname checks.

Filenames are opaque deterministic indexes. Paths are engine-internal and never
accepted from YAML, API parameters, or provider output. The shell never reopens
them. Existing run cleanup owns the files as part of the attempt tree.

### Content-preserving shell rendering

For each distinct spill, the renderer assigns one inherited read-only file
descriptor and prepends a deterministic POSIX prologue:

```sh
__HERMES_WF_SPILL_abcd=$(command cat <&17; __hermes_rc=$?; printf x; exit "$__hermes_rc") || exit $?
__HERMES_WF_SPILL_abcd=${__HERMES_WF_SPILL_abcd%x}
```

The sentinel prevents command substitution from stripping trailing newlines.
Removing the shortest trailing `x` removes only the appended byte, even when
the value itself ends in `x`. The captured `cat` status prevents `printf` from
masking a failed read. The exact descriptor numbers are part of the rendered
command and attempt evidence. `ManagedProcessTree.spawn()` receives only the
bounded spill descriptors as explicit inherited handles, closes them in the
parent after spawn, and does not make unrelated descriptors inheritable.

The placeholder replacement depends on lexical quote context:

| Context | Replacement |
|---|---|
| unquoted | `"${__HERMES_WF_SPILL_abcd}"` |
| inside double quotes | `${__HERMES_WF_SPILL_abcd}` |
| inside single quotes | `'"${__HERMES_WF_SPILL_abcd}"'` (close, expand quoted, reopen) |

The result supplies contents, not a pathname, and preserves spaces, quotes,
dollar signs, backticks, globs, Unicode, and trailing newlines without
evaluation.

Those three replacements apply only in v3 **simple-token contexts** proven by
a bounded shell lexer. The lexer recognizes ordinary unquoted, single-quoted,
and double-quoted command-word text, backslash escapes, comments, redirection,
and nesting boundaries. It ignores escaped references and comment text. A
recognized output reference inside a here-document delimiter/body, command
substitution, backticks, arithmetic expansion, parameter expansion, or an
unterminated/ambiguous lexical state blocks admission with
`bash_reference_context_unsupported`. Phase 3 does not claim to parse or
rewrite arbitrary POSIX shell grammar.

### Command-byte authority and evidence

The renderer returns immutable `RenderedBashCommand` containing:

- the exact command passed as `argv[-1]` to `/bin/sh -c` or the existing
  platform-gated Bash path;
- SHA-256 and UTF-8 byte size of the authenticated template;
- SHA-256 and UTF-8 byte size of the rendered command; and
- spill count, total bytes, and content digests.

It also contains the fixed descriptor-to-digest manifest used for spawn.

The executor executes that exact command; it never reconstructs it from
evidence or rereads a mutable command source. Attempt metadata carries only
bounded sizes and digests, never command text, values, or spill paths.

On native Windows, where the existing platform-gated Bash path cannot provide
the same fixed descriptor inheritance contract, an Archon Bash value above the
inline limit fails closed before launch. Tests still cover exact Windows Bash
command construction and containment for inline values. No weaker pathname
fallback is introduced.

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

After successful fresh execution returns a non-empty session ID, the executor
returns a private `SessionRegistryUpdateCandidate` with the exact key,
expected stale generation, new session ID, cache fingerprint, and winning
attempt identity. The executor does **not** mutate the registry.

`RunStore.complete_node()` first validates the active claim and winning
attempt, then atomically journals both the successful node result and a
`pending_session_registry_update` obligation in the run's authoritative
journal. The obligation contains the protected exact candidate needed for an
idempotent update. It is private store state, is never copied into evidence or
API projections, and is bounded to one obligation for the winning node result.
The run cannot become terminal-complete while one is pending.

Only after that durable boundary does the coordinator apply:

```text
compare_and_set_or_observe(
    key,
    expected_stale_generation,
    new_session_id,
    fingerprint,
)
```

The registry operation has three idempotent semantic outcomes:

- generation equals the expectation: write generation + 1 and return
  `stale_entry_replaced`;
- generation is expectation + 1 and exact session/fingerprint identity already
  matches: return `stale_entry_replaced_already_applied`; or
- any newer or different entry exists: do not write and return
  `newer_entry_retained`.

The coordinator fences against the winning result, journals the bounded
outcome, and clears the obligation. An operational registry failure leaves the
obligation durable, records bounded retry state and `next_registry_update_at`,
and blocks run finalization; it never reruns the provider or changes the
successful node result. Automatic reconciliation waits 1, 2, 4, 8, then 16
seconds. After the fifth failed application, the run remains durably
`recovery_pending` with code `persistent_session_registry_update_pending`
until ordinary resume or an operator retry re-enters the idempotent operation;
there is no hot loop and the obligation is never discarded. Fresh execution
failure creates no obligation and leaves the registry generation unchanged.

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

A fenced post-execution update projects `stale_entry_replaced`,
`stale_entry_replaced_already_applied`, `newer_entry_retained`,
`registry_update_deferred`, or `fresh_execution_failed`. Evidence contains
only digests and bounded identifiers; the exact pending key, session ID, and
fingerprint remain private. The list is capped by the admitted combined-attempt
ceiling plus the one winning update obligation. Journal reserve includes the
selection, winning obligation, and bounded outcome frames before worker
allocation.

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
- session recovery selection is fenced to the active claim, while a winning
  result and its exact pending registry obligation share one atomic journal
  transition;
- the session registry remains generation-CAS SQLite state.

Crash rules are:

- before `persistent_session_missing_fresh_start`, no evidence claims recovery;
- after selection but before provider launch, ordinary zero-effect interrupted
  claim recovery applies;
- after provider launch, existing uncertainty rules apply and no silent replay
  is allowed;
- after provider success but before `complete_node`, no registry write occurs;
- after atomic completion but before registry CAS, recovery applies the durable
  obligation without rerunning the provider;
- after registry CAS but before outcome journaling, idempotent observation
  recognizes the exact already-applied generation and clears the obligation;
- cancellation after winning completion does not discard the internal
  obligation: the coordinator resolves it before final cancellation is
  published, without another provider request;
- an operational CAS failure leaves a fenced pending obligation and bounded
  wake rather than declaring the run complete;
- a stale CAS loser never overwrites the winner; and
- Bash spill evidence without a corroborated attempt never becomes output or
  command authority.

The run snapshot/journal schema gains the exact pending-obligation and wake
fields above. A session-registry SQLite migration is added only if the existing
generation record cannot implement idempotent exact-identity observation.
None is expected for normalization, conditions, references, or Bash
substitution.

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
  recovery records while the exact pending registry obligation remains private;
  and
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
| Normalization | `workflow_execution_semantics_mismatch`, `archon_timeout_node_unsupported`, `archon_idle_timeout_node_unsupported`, `archon_node_id_not_reference_safe` |
| References | `output_reference_not_declared_dependency`, `output_reference_path_unsupported`, `named_script_output_reference_unsupported`, `output_reference_missing`, `output_reference_not_structured`, `output_reference_field_missing`, `output_reference_path_type`, `output_reference_integrity`, `output_reference_temporarily_unavailable`, `output_reference_unavailable` |
| Conditions | `condition_operand_type`, `condition_operand_nonfinite`, `condition_numeric_invalid`, `condition_runtime_syntax_invalid` |
| Bash | `bash_substitution_nul`, `bash_substitution_limit`, `bash_spill_integrity`, `bash_reference_context_unsupported` |
| Sessions | `context_missing_session`, `persistent_session_recovery_unavailable`, `persistent_session_missing_fresh_start`, `persistent_session_registry_update_pending` |

Codes enter the central compatibility/evidence catalog and its duplicate and
completeness tests. Messages may improve; code meaning and version
applicability do not change within v3.

## 13. Testing and verification

Implementation begins only after design and plan approval. Every task proves
RED through `scripts/run_tests.sh` before production edits and uses fresh
implementer plus independent specification and quality review handoffs.

### Normalization and admission

Tests cover exact legacy v2 digests and behavior; v1/v2 resume; deterministic
v3 snapshot fields; positive finite millisecond conversion; the omitted
120-second Bash/script default under ceilings below, equal to, and above 120;
default, explicit, capped, and invalid retry shapes by node kind;
exact snapshot round-trip and changed-config resume for requested retries 5,
requested total 6, effective total 5, and `capped: true`;
requested/effective mismatch; identical effective projections across CLI, API,
gateway, showcase, schedule, and direct-store admission; immutable sealed
limits across config changes; profile/version/digest trust identity and retrust
on v2-to-v3 Archon migration; and generated schema/editor, doctor, catalog, and
detail agreement.

### Conditions and references

Tests cover the exact ASCII v3 grammar, rejected v3 identifiers, dotted keys,
named-script blockers, and direct dependencies on every authenticated surface;
whole text and declared structured fields; root number, string, boolean, null,
array, and object values; mappings, arrays, indexes, missing paths, scalar
descent, and integrity loss; schemaless JSON-looking and numeric text versus
structured string values; string and decimal comparison matrices; quoted
numeric ordering; rejected booleans, null, containers, exponents, locale
numbers, NaN, and infinity; precedence and short circuit; false-to-skipped
versus typed-error-to-failed; exact transient observation/backoff exhaustion,
restart, and multiprocess fencing without cached misses or hot loops; and exact
legacy adapters.

### Retry and cancellation

Tests cover AI default two retries; deterministic default one attempt; explicit
1 and 5; combined caps 1 through 5; provider-only, workflow-only, mixed, repair,
fallback, and unknown-count accounting proving calls never exceed the ceiling;
fatal/transient/unknown/outward/reconciliation classification; explicit
deterministic retry; cancellation at every wake/claim/backoff/provider
boundary; and multiprocess races.

### Bash

Real `/bin/sh` tests cover 32,767, 32,768, and 32,769 UTF-8 bytes; multibyte
boundaries; unquoted, double-quoted, and single-quoted placeholders; rejection
inside quoted/unquoted heredocs, comments, command substitutions, backticks,
arithmetic/parameter expansion, and ambiguous lexer states; shell
metacharacters, empty values, terminal `x`, and trailing newlines; deduplication;
64/65 files and total bounds; NUL; symlink/escape and post-verification pathname
swap attacks; inherited-descriptor isolation and read failure; native-Windows
large-value fail-closed behavior and inline command construction; exact
`argv[-1]` evidence; cleanup; and legacy pathname behavior.

### Persistent sessions

Tests use real profile-local `SessionDB`, registry, store, journal, and isolated
runner seams for first run, warm resume, confirmed missing, same-run missing,
fingerprint mismatch, DB failure, worker race, fresh failure, CAS success/loss,
operational CAS deferral, cancellation, crash before atomic completion, crash
between completion and CAS, crash after CAS before outcome journaling,
idempotent already-applied observation, no provider replay, run-finalization
blocking, scope/profile/provider separation, private-obligation sanitization,
bounded evidence, and bounded history.

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
7. Large Bash values use a 32,768-byte threshold, inherited verified read-only
   descriptors, bounded safe lexical contexts, sentinel-preserving content
   loading, and exact command digests.
8. Missing same-run shared context fails; only a confirmed-missing cross-run
   registry session may start fresh.
9. A winning fresh result and its private exact registry-update obligation are
   journaled atomically before an idempotent generation CAS; run finalization
   waits for its bounded outcome and never replays the provider.
10. Existing authenticated APIs and generic Desktop inspection extend
    additively; no path-taking or raw-provider surface is introduced.
11. Phase 4 loops/includes and Phase 5 provider portability remain out of scope.

## Independent review disposition

The first independent specification review is retained at
`.superpowers/sdd/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience/design-review-1.md`.
Its nine Important findings are resolved as follows:

1. the exact sealed execution projection and all admission authorities are now
   specified;
2. omitted Archon Bash/script timeouts normalize to 120 seconds before capping;
3. timeout authority is explicitly per workflow attempt, matching upstream,
   with active-attempt crash handling rather than an invented cross-retry
   deadline;
4. v3 has one closed reference grammar and named scripts fail explicitly;
5. the resolver exposes typed and rendered facets;
6. transient reads use one durable bounded wake protocol;
7. Bash consumption uses inherited verified descriptors, not reopened paths;
8. Bash references are admitted only in contexts proven safe by a bounded
   lexer; and
9. session CAS follows an atomically journaled winning-result obligation and
   is recoverably idempotent.

An independent rereview must close these dispositions before implementation
planning is considered reviewed.
