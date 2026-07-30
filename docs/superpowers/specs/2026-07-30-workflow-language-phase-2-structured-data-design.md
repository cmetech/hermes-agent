# Workflow Language Phase 2: Structured Data and Typed Artifacts

**Status:** Approved subordinate design

**Date:** 2026-07-30

**Parent:** `2026-07-25-workflow-language-compatibility-expansion-design.md`

**Target profile:** `archon-2026-07`

## Purpose

Phase 2 makes structured model output and typed node artifacts enforceable,
durable workflow contracts. It builds the typed-data foundation that Phase 3
will use for strict conditions, references, timeout normalization, and retry
semantics without implementing those later semantics early.

The implementation must preserve prompt caching, role alternation, immutable
admission snapshots, attempt fencing, and the narrow-waist architecture of the
Hermes agent core.

## Approved compatibility boundary

All new behavior in this design is gated by `archon-2026-07`.

`hermes-legacy` remains behaviorally frozen:

- `output_format` remains post-execution validation;
- `output_type` remains accepted without typed publication;
- existing warnings, retry behavior, output paths, and downstream resolution
  behavior remain unchanged.

Future workflow-language improvements target the Archon profile. A shared
primitive may live in generic code when it has a concrete non-workflow shape,
but no legacy workflow is silently upgraded to Archon behavior.

## Existing node and extension surface

Hermes currently supports seven node kinds:

- `command`
- `prompt`
- `bash`
- `script`
- `loop`
- `approval`
- `cancel`

Script nodes already support inline and named Bun/JavaScript/TypeScript and
uv/Python execution, uv dependencies, bounded output, sealed resources,
cancellation, and process-tree cleanup.

MCP and skills are not additional node kinds. They are already supported
per-node options on `command` and `prompt` nodes:

- `mcp` resolves a sealed package resource, starts only the requested servers
  inside the isolated worker, fails closed when required servers cannot be
  established, and tears them down during worker cleanup;
- `skills` resolves and snapshots only the selected skills, then places their
  content in the node user message rather than the system prompt.

Phase 2 preserves these mappings. It does not schedule redundant MCP-node or
skills-node work.

## Goals

1. Validate and normalize bounded JSON Schema at package load time.
2. Select a truthful provider structured-output strategy centrally.
3. Enforce `output_format` natively when proven and through an explicit
   adapter otherwise.
4. Permit at most one isolated, action-free repair of invalid adapted output.
5. Retain one canonical typed value for downstream consumers.
6. Make `output_type` an atomic, winner-only publication contract.
7. Recover publications only from corroborated winning attempts.
8. Expose bounded operational evidence, preview, and download in Desktop.

## Non-goals

Phase 2 does not implement:

- Archon timeout units or defaults;
- Archon retry-count or retry-class semantics;
- strict missing-output or missing-field behavior;
- strict condition coercion and precedence;
- large Bash-value spill and quoting behavior;
- missing persistent-session recovery;
- `maxBudgetUsd` portability;
- new node kinds, `include`, or `loop_group`;
- new MCP or skill capability.

Those boundaries prevent Phase 2 from partially changing Phase 3 semantics.

## Architectural overview

```text
package load
    -> normalize and fingerprint schema
    -> reject impossible declared field references
    -> resolve and seal provider strategy at admission
    -> execute in isolated worker
         -> native schema | native JSON | prompt adapter
         -> parse, validate, canonicalize
         -> optional one-turn isolated repair
    -> return winning-attempt publication candidate
    -> RunStore atomically publishes content + metadata
    -> journal records publication
    -> evidence/API/Desktop project bounded metadata
```

The normalized schema is data in an immutable request and user message. It is
never inserted into or used to rewrite a live system prompt.

## 1. Schema contract

### Supported schema dialect

`output_format` is a JSON Schema Draft 2020-12 mapping. The loader uses the
installed `jsonschema` implementation and fails closed when validation support
is unavailable. This preserves the existing approved dependency policy: a
lean installation may run workflows without `output_format`, while doctor and
admission give the established extra-install guidance when structured output
requires the validator.

The first implementation supports self-contained schemas only:

- `$defs` is allowed;
- `$ref` may address only a JSON Pointer below the document's own `$defs`;
- external URLs, file references, absolute identifiers, unresolved pointers,
  `$dynamicRef`, and reference cycles are rejected;
- `$id` and anchors may not redefine resolution scope.

### Bounds

Bounds are centralized constants and contract-tested rather than duplicated in
loaders and executors. Initial ceilings are:

| Dimension | Ceiling |
|---|---:|
| Canonical schema bytes | 65,536 |
| Schema nesting depth | 32 |
| Traversed schema nodes | 4,096 |
| Object properties across the schema | 1,024 |
| Local references | 256 |
| One regex | 1,024 bytes |
| All regex text | 16,384 bytes |
| One enum | 1,024 values |
| Canonical structured output | 500,000 bytes |
| Repair invalid-response excerpt | 256,000 bytes |
| Repair validation diagnostics | 16,384 bytes |
| Typed metadata sidecar | 65,536 bytes |

Patterns must compile. Instance validation remains inside a resource- and
wall-time-bounded isolated worker so a hostile but compilable pattern cannot
stall the coordinator.

The normalizer rejects non-finite numeric schema values and does not infer
provider-specific restrictions into the portable schema. A native provider
adapter may produce a strategy-specific wire schema, but it must record every
lossless normalization and must reject lossy transformations.

### Fingerprints and admission snapshots

The canonical normalized schema has a SHA-256 fingerprint. The schema and
fingerprint enter:

- the normalized workflow definition digest;
- the immutable language snapshot;
- the admitted provider-capability decision;
- the AI worker cache fingerprint;
- structured-output evidence;
- typed-artifact metadata.

A schema change therefore prevents reuse of a shared worker context. Existing
cache-mismatch handling gives `context: fresh` guidance; no history or system
message is rewritten.

### Static field-reference checks

During package validation, direct `$node.output.field` references are compared
with the declared upstream schema. A reference fails statically only when all
applicable schema branches prove that the field cannot exist. Open records,
optional declared fields, unions that permit the field, and schemaless outputs
remain runtime decisions.

This check catches provable typos without importing Phase 3's strict runtime
missing-field or condition rules.

## 2. Provider structured-output capability

### Central contract

A generic immutable capability contract lives beside provider/runtime
resolution. It is not owned by the workflow plugin. The resolver returns:

- `native_json_schema` — the transport grammar-constrains the supplied schema;
- `native_json_mode` — the transport guarantees JSON syntax and Hermes
  validates the schema;
- `prompt_json_schema` — Hermes adds a schema instruction to the user message
  and validates the response;
- `unsupported` — Hermes cannot constrain and validate this runtime safely.

The decision includes the effective provider, model, API mode, declaration
source, adapter version, schema fingerprint, and a bounded rationale.

### Authority and defaults

Provider/runtime declarations are authoritative:

- direct bundled routes may declare native support only with transport tests;
- a provider plugin may declare its supported mode explicitly;
- verified live model capability data may narrow or confirm support;
- community model-catalog data cannot promote an unknown route to native;
- custom and aggregator endpoints never inherit native support merely because
  they use a similarly named API mode;
- an unknown runtime that uses Hermes' complete agent loop defaults to the
  prompt adapter when the adapter can bound and validate it;
- delegated runtimes outside Hermes' loop are unsupported unless they expose a
  specific tested structured-output contract.

Native declarations are intentionally conservative. Unsupported or partially
implemented endpoint parameters must not be discovered through failed user
requests.

### Admission and drift

Compatibility assessment resolves the prospective strategy before a run is
admitted. The immutable run snapshot records it. The isolated worker resolves
its actual credential/provider/runtime mapping again before its first request.

If the actual mapping cannot honor the admitted strategy, execution fails with
`structured_output_capability_drift`. It does not silently downgrade from
native to prompt enforcement or change provider/model.

Catalog lists carry only a bounded strategy summary. They do not carry full
schemas or mutable live capability documents.

## 3. Generic isolated-agent seam

`PluginAgentRunRequest` gains an optional generic structured-output value
containing:

- canonical schema;
- schema fingerprint;
- admitted strategy and adapter version;
- output byte ceiling;
- canonicalization version.

The request validator enforces all sizes and rejects contradictory strategies.
The worker verifies the resolved runtime decision before constructing
`AIAgent`.

Transport integrations consume the generic value:

- native JSON Schema routes emit their tested provider wire field;
- native JSON-mode routes emit only their tested JSON-mode field;
- prompt routes append a deterministic bounded instruction block to the
  initial user message;
- unsupported routes fail before a provider request.

`request_overrides` is not the public structured-output contract. Provider
wire details remain transport-owned and cannot be supplied as arbitrary
workflow YAML.

The worker result includes exact structured-output strategy evidence and exact
provider-attempt accounting. This seam is a concrete generic capability and
must be committed separately from workflow-specific behavior so it can be
reviewed or reverted independently.

## 4. Generation, validation, and canonicalization

The authoritative response is one complete JSON value. Surrounding prose,
Markdown fences, multiple values, non-finite numbers, and trailing non-space
content are invalid.

After parsing, Hermes validates against the canonical portable schema. Native
enforcement is not trusted as a substitute for validation because refusals,
truncation, and provider bugs can bypass a grammar guarantee.

Valid data is serialized with one versioned canonicalizer:

- UTF-8;
- object keys sorted by Unicode code point order;
- compact separators;
- JSON booleans and null preserved;
- no NaN or Infinity;
- no surrounding prose or Markdown fence;
- no trailing newline;
- maximum 500,000 encoded bytes.

The canonical bytes are the authoritative node output. Conditions,
substitution, evidence, publication, hashing, and preview do not independently
reparse the provider's original response.

## 5. Bounded repair

Only `prompt_json_schema` output is repairable. A native-schema validation miss
fails immediately because it represents refusal, truncation, capability drift,
or a broken provider guarantee.

A prompt-adapted miss may receive exactly one repair when all of these hold:

- the original worker completed and returned a bounded response;
- the node is not declared outward-acting;
- the attempt has no unknown-side-effect or reconciliation marker;
- cancellation has not won;
- provider attempts, model iterations, wall time, and any host-enforced budget
  remain.

The repair request:

- starts a fresh isolated process and fresh session;
- has one model turn;
- uses the already-resolved provider and model;
- contains only the canonical schema, bounded invalid response, and bounded
  validation errors;
- omits the original task, conversation history, system-prompt override,
  fallback chain, and persistent session;
- has `allowed_tools=()`, no enabled toolsets, denied delegation, no hooks, no
  MCP, no skills, and no inline agents;
- cannot request approval, secrets, sudo, or user clarification.

Repair transforms captured text only. It never invokes or replays the original
node, tools, scripts, hooks, MCP calls, subagents, or outward actions.

Original generation and repair share one accounting object. Exact provider
attempts and model calls are charged before the remaining allowance is handed
to repair. Repair never receives a second allowance. Usage evidence aggregates
both workers. This does not activate Archon `maxBudgetUsd`; that field remains
blocked until Phase 5.

An invalid or ineligible result ends as `structured_output_invalid`. Metadata
marks the Archon execution terminal so the legacy scheduler's retry policy
cannot multiply generation and repair. Legacy retry behavior is untouched.

Raw invalid responses are not copied into `run.json`, compatibility reports,
or general evidence. Evidence keeps only bounded validation summaries, digest,
size, strategy, repair eligibility/reason, and aggregate usage.

## 6. Canonical output resolution

An Archon-only shared resolver returns an immutable value containing:

- canonical bytes;
- parsed JSON when applicable;
- deterministic text rendering;
- media type;
- SHA-256;
- producing node and winning attempt;
- artifact/publication identity when present.

All Archon downstream output consumers obtain their value from this resolver.
During Phase 2, compatibility adapters preserve existing runtime condition and
missing-field outcomes. Phase 3 replaces those adapters with strict reference,
comparison, and Bash-substitution semantics.

Legacy workflows continue through the existing resolution path.

## 7. `output_type` publication

`output_type` is an open, case-sensitive semantic label. It does not select a
serializer or file extension.

For every successful Archon node declaring `output_type`, the scheduler passes
the immutable primary-output candidate to `RunStore.complete_node`. This
applies to `command`, `prompt`, `bash`, `script`, `loop`, and `approval` output.
A `cancel` node does not publish because it cannot complete successfully.

The canonical media contract is:

- `application/json` and `content.json` for canonical structured JSON;
- `text/markdown; charset=utf-8` and `content.md` for other UTF-8 output.

An empty successful output is a valid zero-byte text publication.

### Publication bundle

One immutable bundle contains:

- the canonical content file;
- `metadata.json` with output type, media type, content hash, producer node,
  winning attempt, run ID, language profile, schema fingerprint when present,
  byte size, production time, session ID when present, canonicalization
  version, and publication ID.

The on-disk path uses an opaque publication ID rather than a user-authored node
or output-type string. Paths are never accepted from API callers.

### Atomic completion order

`RunStore.complete_node()` owns publication under the run lock:

1. verify the active execution claim and candidate attempt;
2. verify the attempt-local regular file, containment, size, and digest;
3. write content and metadata into a private unique staging directory;
4. flush both files and the staging directory;
5. atomically rename the complete directory to its immutable final path;
6. append and flush the node-completion journal event with the publication
   descriptor;
7. atomically replace the derived `run.json` projection.

The final path must not exist before publication. A competing completion that
loses the attempt/claim comparison never reaches the staging step.

Content and metadata are one cleanup and retention unit. No projection exposes
metadata for absent content, and no metadata names a losing attempt.

### Crash recovery

Recovery follows journal authority:

- incomplete staging directories are removed;
- final bundles absent from the journal are unreferenced orphans and removed;
- a journaled bundle missing only from `run.json` is restored by normal
  projection rebuild;
- a journaled bundle that is missing or corrupt may be reconstructed only from
  the corroborated winning-attempt artifact with the recorded digest;
- absent or mismatched winning content moves the run into a typed
  artifact-integrity/reconciliation state rather than inventing success.

Recovery never chooses content by modification time, directory order, node ID,
or "latest attempt" heuristics.

### Cross-run mirror

An effective persistent-session node receives a profile-scoped mirror after
the run publication is durable. Content is stored by hash; immutable mirror
entries identify workflow, node, scope, run, attempt, output type, and content
hash. A small scope index is updated atomically under its own lock and points to
an immutable entry, never a provider-session path or mutable content file.

The node-completion journal records an idempotent mirror obligation before the
run lock is released. Completion of that obligation is journaled separately.
After a crash, recovery may finish the mirror from the verified run publication
or leave it explicitly pending; cold-session recovery never advertises a
pending or unverified mirror. This prevents a crash between run publication and
scope-index replacement from becoming an untracked best-effort loss.

Concurrent runs retain immutable history even when the scope index advances.
Cold recovery names artifact references and does not paste artifact bodies into
the model context.

## 8. Evidence and API

Run projections and evidence contain bounded typed-artifact metadata, not
bodies. Existing artifact evidence gains additive publication fields rather
than a second unbounded evidence system.

Authenticated endpoints provide:

- bounded text or JSON preview with truncation metadata;
- streamed download by opaque publication ID.

Both paths enforce profile and run ownership, read scope, regular-file and
symlink checks, containment, recorded size/digest, safe media type, and safe
`Content-Disposition`. No endpoint accepts a filesystem path. Catalog and
coordinator sweeps never open artifact bodies.

Compatibility and operational evidence use stable codes for:

- invalid or excessive schema;
- unavailable schema validator;
- unsupported structured-output strategy;
- admission/runtime capability drift;
- invalid structured output;
- repair ineligibility or exhaustion;
- typed-publication integrity or recovery failure.

## 9. Desktop contract

The Desktop run inspector renders backend-confirmed publications only. It does
not interpret workflow YAML or `output_type` declarations independently.

The typed-artifact view shows:

- output type and media type;
- producer and winning attempt;
- size and SHA-256;
- schema fingerprint when present;
- production time and session identity when present;
- integrity/recovery state;
- formatted canonical JSON or bounded text preview;
- an explicit download action.

Compatibility is additive:

- a new Desktop against an older backend retains the generic evidence view;
- an older Desktop ignores new backend fields;
- unknown future media types remain downloadable without unsafe inline
  interpretation;
- preview or metadata failure does not impair the primary chat/TUI surface.

## 10. Testing and verification

Implementation follows red-green-refactor. Required coverage includes:

### Schema and references

- valid Draft 2020-12 schemas and canonical equivalence;
- external, unresolved, cyclic, and excessive local references;
- byte, depth, node, property, enum, and regex ceilings;
- non-finite and malicious inputs;
- provably impossible versus optional/open downstream fields;
- unchanged legacy diagnostics and behavior.

### Provider and worker

- capability resolution matrices for known, custom, aggregator, and unknown
  runtimes;
- native transport wire shapes;
- prompt adapter user-message placement;
- admission/runtime drift before the first provider request;
- canonical JSON across equivalent provider encodings;
- refusal, truncation, prose, fence, multiple-value, and oversized failures;
- exact aggregate provider-attempt and usage accounting.

### Repair

- eligible single repair success;
- native validation miss with no repair;
- no tools, hooks, MCP, skills, history, system override, fallback, agents, or
  delegation in the repair request;
- outward and uncertain outcomes never repaired;
- cancellation and attempt/wall-budget exhaustion;
- invalid output remains terminal under Archon without changing legacy retry.

### Publication and recovery

- all successful output-producing node kinds;
- empty text and canonical JSON;
- winner/loser and stale-claim races;
- crash injection before and after content write, metadata write, directory
  flush, rename, journal append, and projection replace;
- orphan cleanup;
- corroborated reconstruction and corrupt/missing rejection;
- symlink, traversal, digest, quota, retention, and profile-isolation tests;
- immutable cross-run mirror and concurrent scope-index updates.

### API and Desktop

- metadata and evidence bounds;
- authenticated preview/download ownership and path attacks;
- JSON/text truncation behavior;
- new Desktop/old backend and old Desktop/new backend compatibility;
- preview failures leave the terminal/chat surface operational.

### Gates

- focused workflow, provider, agent, API, and Desktop suites;
- installed-distribution tests with a temporary `HERMES_HOME`;
- full canonical `scripts/run_tests.sh` suite;
- Desktop typecheck, lint, and tests;
- customization-ledger validation if the generic seam intersects branded
  integrations;
- temporary upstream and brand merge rehearsals because the generic isolated
  agent and transport contracts change.

Tests assert invariants and relationships rather than exact catalog counts,
model lists, or other change-detector snapshots.

## 11. Documentation updates

Phase 2 updates the generated language contract, workflow YAML reference,
provider capability descriptions, `output_format` examples, `output_type`
artifact documentation, and Desktop operational guidance.

Documentation must state explicitly:

- Archon-only activation;
- native versus adapted enforcement;
- one-repair limit and action-safety boundary;
- validator installation requirement;
- canonical JSON behavior;
- atomic typed publication and recovery;
- Phase 3 boundaries for conditions, timeout, retry, and strict references;
- existing per-node MCP and skills support.

## References

- Approved umbrella design:
  `docs/superpowers/specs/2026-07-25-workflow-language-compatibility-expansion-design.md`
- Phase 2 continuation handoff:
  `.superpowers/sdd/2026-07-30-workflow-language-phases-2-6/continue.md`
- Archon workflow authoring:
  <https://archon.diy/guides/authoring-workflows/>
- Archon script nodes:
  <https://archon.diy/guides/script-nodes/>
- Archon per-node MCP:
  <https://archon.diy/guides/mcp-servers/>
- Archon per-node skills:
  <https://archon.diy/guides/skills/>

## Approved design decisions

The user approved these decisions during the design review:

1. Future workflow-language improvements go to `archon-2026-07`; legacy is
   frozen.
2. Provider enforcement is declared native-first with bounded adaptation.
3. Repair is one isolated transformation call and never replays actions.
4. Typed publication is winner-only, atomic, journaled, and recoverable only
   from corroborated content.
5. Desktop renders bounded backend-confirmed metadata, preview, and download.
6. Script nodes, MCP, and skills are recognized as existing capabilities, not
   new Phase 2 or Phase 3 node kinds.
