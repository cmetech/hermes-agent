# Workflow Language Phase 4: Ordinary Loops and Immutable Includes

**Status:** Approved design sections — awaiting written-spec review

**Date:** 2026-08-05

**Parent:** `2026-07-25-workflow-language-compatibility-expansion-design.md`

**Predecessor:** `2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience-design.md`

**Target profile:** `archon-2026-07`

## Purpose

Phase 4 adds ordinary Archon loop completion semantics and reusable workflow
sub-DAGs without weakening Hermes' immutable-admission, durable-recovery, or
trust boundaries.

For newly admitted `archon-2026-07` workflows, normalizer v4 adds:

- exactly one of `loop.prompt` or `loop.command`;
- `loop.signal_completes` and durable confirmation of signal-bearing results;
- immutable, origin-bound command resources for ordinary loops; and
- load-time `include` expansion into one sealed workflow DAG.

The phase is one externally atomic compatibility release. Loop and include
work may be implemented and reviewed in separate internal stages, but
normalizer v4 does not become the current Archon normalizer until both stages
pass their complete gates.

Existing unversioned and `hermes-legacy` workflows stay on normalizer v2.
Already-admitted v1 through v3 runs reload with their recorded semantics. In
particular, a v3 loop that emits its completion signal still completes
immediately before any interactive input gate.

The design preserves byte-stable conversation prompt prefixes, strict message
role alternation, and the plugin-edge architecture. It adds no core model tool
and does not resolve includes or command files during execution.

## Inputs and compatibility posture

This design is subordinate to the umbrella workflow-language design and the
completed Phase 3 contract. It implements the pinned `archon-2026-07` profile,
not every feature present in later live Archon documentation.

The Phase 4 compatibility target includes:

- one inline prompt or named command per ordinary loop;
- signal completion that defaults to confirmation for interactive loops;
- acceptance of an already-computed signal-bearing result without another AI
  call;
- feedback that discards the pending signal and starts the next iteration;
- load-time workflow includes with literal targets, bounded depth, cycle
  detection, namespaced nodes, and entry/sink rewrites; and
- immutable dependency closure, resource origin, trust, and resume behavior.

Hermes intentionally strengthens that surface:

1. A loop can pause for confirmation only when both the workflow and loop opt
   into interactivity. An included loop cannot unexpectedly turn an unattended
   root workflow into an interactive run.
2. Included workflow policies never gain authority. The root sidecar is the
   only active policy, while child sidecars are authenticated and retained as
   explicitly ignored provenance.
3. Includes compile into the root DAG before admission. They never create a
   child run, scheduler, worker, process, or lifecycle.
4. Every command and dependency resource is read, authenticated, origin-bound,
   and sealed before admission. The runtime never falls back to mutable
   repository, profile, or home-directory files.

The current live Archon surface also documents runtime child-workflow nodes
and broader nested-loop behavior. Those later behaviors are outside this
pinned profile and remain unsupported unless a later design versions them
explicitly.

## Confirmed product decisions

The following decisions were approved before this specification was written:

1. **Atomic Phase 4 release.** Loops and includes are internally staged but
   publicly enabled together as normalizer v4.
2. **Two-level interaction gate.** A v4 loop is effectively interactive only
   when both root `interactive: true` and `loop.interactive: true` are present.
3. **Root policy authority.** Included workflows contribute nodes, not
   permissions, defaults, or runtime limits.
4. **Authenticated ignored sidecars.** Child sidecars are hashed, snapshotted,
   surfaced as ignored provenance, and included in future-admission identity,
   but none of their settings execute.
5. **Compile and seal.** The complete include closure is selected, expanded,
   normalized, validated, trusted, and snapshotted before a run exists.
6. **Security-gate-safe validation.** Deterministic defensive invariants are
   mandatory. A narrowly scoped adversarial security review is attempted, but
   a documented Codex platform gate does not independently fail the phase.

## Goals

1. Make normalizer versions cumulative so v4 inherits every v2 and v3
   guarantee without changing old snapshots.
2. Normalize ordinary-loop prompt source, effective interactivity, and signal
   completion once at admission.
3. Finalize a signal-bearing result through a compare-and-set store action
   without replaying the provider call or node executor.
4. Resume feedback-driven loops with bounded durable input and exactly one next
   iteration.
5. Resolve a bounded workflow dependency closure from one catalog snapshot.
6. Expand includes into a deterministic flat DAG with stable provenance,
   dependency, trigger, and output-reference behavior.
7. Bind command, script, MCP, and loop-command resources to their origin
   packages and copy them to collision-proof sealed paths.
8. Make the composite closure, expanded semantics, active root policy, and
   executable risk the unit of trust and scheduled revalidation.
9. Expose backend-authored actions and bounded diagnostics consistently through
   CLI, Gateway, REST, evidence, and Desktop.
10. Preserve prompt caching, cancellation, attempt fencing, cleanup,
    customization, installed-wheel, and cross-platform invariants.

## Non-goals

Phase 4 does not:

- change unversioned, `hermes-legacy`, or admitted v1-v3 behavior;
- add runtime workflow child nodes, subprocesses, nested schedulers, child
  lifecycle controls, or `loop_group`;
- add `include.with`, input mapping, deep child-output access, dynamic include
  names, paths, URLs, or expressions;
- allow an include inside a future `loop_group` body;
- import child top-level defaults, language declarations, sidecar limits,
  secrets, approvals, outward-action declarations, or execution-environment
  settings;
- add provider aliases, portable capability resolution, cost budgets, or
  sandbox claims from Phase 5;
- add a new core tool, mutate the system prompt mid-conversation, or change the
  model tool schema;
- add a Desktop YAML parser, include compiler, trust engine, or workflow
  executor;
- create an operating-system sandbox; or
- reproduce later live Archon features outside `archon-2026-07`.

## Approaches considered

### A. Compile and seal the complete DAG before admission

Discovery captures one catalog view. A bounded resolver reads the root and
literal dependencies, an expander creates one namespaced graph, normalizer v4
applies cumulative semantics, trust evaluates the complete graph, and the
store seals an expanded definition plus its entire closure. The existing
scheduler executes only that snapshot.

**Selected:** this preserves one scheduler, deterministic resumption, immutable
resources, root policy authority, and existing run/evidence APIs.

### B. Resolve includes while the scheduler executes

An executor could encounter an include and load the child package on demand.
That creates mutable behavior, trust checks during execution, ambiguous crash
recovery, and nested ownership. A resumed run could select different bytes or
catalog precedence.

**Rejected:** it violates load-time-only and immutable-admission requirements.

### C. Require authors to generate flattened YAML before running

A CLI command could materialize a standalone workflow that the current loader
understands. This avoids runtime compiler work but creates stale generated
files, duplicate catalog entries, manual regeneration, and weaker provenance.

**Rejected as the execution architecture:** a future export/debug command may
render the expanded graph, but admission itself must compile authoritative
source.

## Architectural overview

```text
catalog roots
    -> immutable discovery snapshot
       -> root source + literal dependency closure
          -> bounded cycle/depth/resource verification
             -> deterministic namespace/reference/edge expansion
                -> root-profile normalizer v4
                   -> final graph + semantic validation
                      -> composite digest + root-policy risk review
                         -> snapshot format 2
                            -> existing scheduler and executors
```

The implementation uses four explicit conceptual contracts:

1. **Source package:** authenticated raw definition and optional sidecar bytes,
   source kind, precedence, logical relative location, and source lines.
2. **Resolved closure:** the root and selected dependencies from one discovery
   snapshot, with package digests, resource bytes, and include edges.
3. **Compiled workflow:** one normalized `WorkflowPackage`, expanded nodes,
   node-origin map, rewritten resource bindings, and composite digest material.
4. **Sealed run snapshot:** canonical expanded definition, active root policy,
   closure manifest, origin-bound resources, language semantics, and evidence.

The first three contracts exist only before admission. The scheduler consumes
the fourth and has no include-resolution capability.

## 1. Normalizer v4 and cumulative capabilities

### Version selection

Phase 4 advances only the Archon profile:

```python
LATEST_NORMALIZER_VERSION = 4
CURRENT_NORMALIZER_BY_PROFILE = {
    WorkflowLanguageProfile.HERMES_LEGACY: 2,
    WorkflowLanguageProfile.ARCHON_2026_07: 4,
}
SUPPORTED_NORMALIZER_VERSIONS = frozenset({1, 2, 3, 4})
```

Normalizer v4 requires `archon-2026-07`, just as v3 does. An explicitly sealed
v1, v2, or v3 snapshot remains accepted under its existing profile rules.

| Input | Normalizer | Behavior |
|---|---:|---|
| New unversioned package | 2 | Exact current legacy semantics |
| New `hermes-legacy` package | 2 | Exact current legacy semantics |
| Existing admitted v1-v3 run | recorded 1-3 | Exact recorded snapshot semantics |
| New `archon-2026-07` package | 4 | Phase 2 + Phase 3 + Phase 4 semantics |

### Capability predicates

Phase 4 removes exact-version feature checks from production behavior. A
single language capability authority defines at least:

```python
supports_structured_outputs(profile, version)  # Archon and version >= 2
supports_phase3_semantics(profile, version)    # Archon and version >= 3
supports_phase4_semantics(profile, version)    # Archon and version >= 4
```

Schema, normalizer, trust, snapshot, scheduler, resource, condition, and
executor code consume these predicates. They do not independently compare
`normalizer_version == 3` or guess that the latest version owns earlier
features.

The normalization chain is cumulative:

```text
v4 source
  -> v2 structured-output normalization
  -> v3 timeout/retry/reference/session normalization
  -> v4 loop/include normalization
```

The v4 normalized-definition digest includes the fully expanded graph, the v2
structured-output projection, the inherited v3 node semantics, and the v4
ordinary-loop semantic projection. The composite package digest additionally
binds source selection, dependency identities, ignored sidecars, provenance,
and resource origins.

### V4 loop semantics projection

Each admitted v4 loop has an exact immutable semantic entry:

```json
{
  "loop": {
    "prompt_source": "inline",
    "command_binding": null,
    "effective_interactive": true,
    "signal_completes": false
  }
}
```

`prompt_source` is `inline` or `command`. A command-backed loop stores a
nonempty collision-proof snapshot-relative `command_binding`; an inline loop
stores JSON null. The projection contains no prompt body, user feedback,
absolute path, or resource contents.

Snapshot readers reject extra fields, missing required fields, invalid
combinations, or a v4 semantic projection attached to another profile/version.
They never upgrade an old projection in place.

## 2. Source loading and immutable catalog selection

### Split raw parsing from root-profile compilation

The current loader parses, profile-selects, normalizes, graph-validates, and
constructs one package in a single path. Includes require a bounded split:

1. Parse YAML bytes with the existing safe loader, byte limits, exact-integer
   protection, source-line capture, field validation, and self-trust rejection.
2. Resolve the root sidecar and root language profile.
3. Select and parse literal dependency definitions as source packages without
   granting their profile, defaults, or sidecar execution authority.
4. Expand the source graph under the root profile.
5. Run normal normalization, graph validation, static reference validation,
   compatibility findings, and package construction once on the expanded DAG.

Legacy public `load_workflow()` and `load_workflow_snapshot()` retain their
current behavior for v1-v3. V4 admission uses the closure compiler. Snapshot
format 2 reload reads the already-expanded definition and never resolves the
catalog again.

### Discovery snapshot

One discovery request captures every candidate needed by a compile attempt:

- logical workflow name;
- source kind and numeric precedence;
- logical relative definition and sidecar locations;
- definition and optional sidecar bytes;
- stable file identities used to detect changes during the read; and
- the existing duplicate/ambiguity result.

The snapshot contains no executable trust decision. It is a bounded input to
resolution. The resolver never performs a second discovery lookup for a child
and never silently changes candidate after the root has been selected.

Existing source and precedence rules select an include target exactly as they
select a top-level workflow. A same-precedence ambiguity remains blocking. A
higher-precedence package deliberately shadows a lower-precedence package, and
that selection is recorded in the composite identity and diagnostics.

The discovery parse cache key expands from one definition/sidecar signature to
the selected closure signatures. A child definition, sidecar, or covered
resource change invalidates a future compiled result.

## 3. Include authoring and expansion

### Authoring shape

V4 accepts an include directive in the root or another included workflow:

```yaml
nodes:
  - id: checks
    include: reusable-checks
    depends_on: [build]
    trigger_rule: all_success
```

The directive allows only `id`, `include`, `depends_on`, and `trigger_rule`.
The include value must be a nonempty literal portable workflow name. Paths,
URLs, variables, output references, expressions, `with`, `when`, execution
options, and deep child access are rejected.

An include is a compile directive, not a `WorkflowNode` delivered to the
scheduler. It disappears after expansion.

### Closure traversal

Resolution is depth-first in authored node order and uses a package-identity
stack for cycle detection.

- The root has include depth 0.
- A direct include has depth 1.
- Include edges through depth 3 are accepted.
- An include that would create depth 4 fails with
  `include_depth_exceeded`.
- A package already on the active stack fails with `include_cycle` and a
  bounded logical-name chain.
- Reusing the same package under separate include IDs is allowed; it creates
  separate node instances but one distinct dependency-manifest entry.

An included graph must contain at least one executable node after recursive
expansion. Includes remain prohibited inside a future `loop_group` body.

### Namespace and provenance

Each child node ID is prefixed with the complete include-instance path:

```text
checks + lint          -> checks__lint
checks + deep + scan   -> checks__deep__scan
```

Authored IDs may contain `__`. Hermes does not add a new identifier ban; it
builds the entire final ID map and rejects any authored/generated collision
with `include_id_collision` before normalization or trust.

Every expanded node records:

- root-relative include-instance path;
- logical origin package key and workflow name;
- catalog source and precedence;
- logical definition location;
- original node index and source line; and
- final expanded node ID.

Absolute filesystem locations are internal only and never enter composite
digests, public projections, or normal diagnostics.

### Entry and sink wiring

For each expanded include instance:

- an entry is a child node with no dependency on another node in that included
  instance;
- a sink is a child node that no other node in that included instance depends
  on;
- the include directive's `depends_on` values are attached to every entry;
- the include directive's `trigger_rule` becomes the join rule for those
  attached parent dependencies;
- internal dependencies retain their child semantics after namespace rewrite;
- a downstream dependency on the include ID is replaced by dependencies on
  every sink, in child definition order; and
- `$checks.output` resolves to the first sink in child definition order.

The first-sink alias is the only included-output projection in Phase 4.
`$checks.lint.output` and other navigation that names a specific child are
rejected. Ordinary structured access such as `$checks.output.field` remains
valid after the alias has been resolved to the first sink.

### Reference rewriting

Rewriting is syntax-aware and operates on parsed references, never unrestricted
string replacement. It covers every existing reference-bearing field,
including:

- `depends_on`;
- `when`;
- prompt, command, Bash, and script templates;
- loop prompt, `until_bash`, and gate message;
- approval and rejection prompts; and
- supported hook/agent option templates already recognized by the schema.

A child reference to another child is namespaced. A child reference to an
include alias resolves through that include's sink rules. A reference that
escapes the included graph or violates inherited v3 direct-dependency rules is
blocking after expansion.

### Root sidecar node references

The root sidecar may name a root executable node or a root include directive in
`outward_action_nodes`. Naming an include marks every node expanded from that
include instance as outward, which is the conservative root-policy meaning.
Phase 4 does not allow root sidecars to address a specific deep child. Unknown
root node/include references remain blocking.

Child `outward_action_nodes`, `required_secrets`, limits, pause-lane settings,
environment requirements, and other sidecar fields are ignored. `doctor`
shows these ignored declarations so the root author can deliberately copy any
required policy into the root sidecar.

## 4. Bounds and deterministic failures

All limits apply to the complete root plus dependency closure, not separately
to each include instance:

| Resource | Hard limit |
|---|---:|
| Include depth | 3 |
| Distinct dependency packages | 64 |
| Expanded executable nodes | 512 |
| Expanded dependency edges | 4,096 |
| Selected source-definition bytes | 2 MiB total |
| Expanded canonical definition | 2 MiB |
| Authenticated resource files | 512 |
| One authenticated resource file | 1 MiB |
| Total authenticated resource bytes | 8 MiB |

Repeated include instances count their expanded nodes and edges each time.
Distinct package count and authenticated file/byte count deduplicate identical
selected package resources by logical package key and digest.

Bounds are checked during traversal as well as after expansion. The resolver
stops at the first exceeded hard limit and does not allocate the complete
oversized graph first. Existing YAML depth, scalar, container, schema, input,
run-storage, and journal limits remain in force.

Stable blocking codes include:

| Code | Meaning |
|---|---|
| `include_not_found` | No selected literal target exists |
| `include_ambiguous` | Catalog precedence does not select one target |
| `include_cycle` | Active dependency stack repeats a package |
| `include_depth_exceeded` | Traversal would exceed depth 3 |
| `include_dependency_limit` | More than 64 dependency packages are selected |
| `include_expansion_limit` | Definition, node, edge, file, or byte bound is exceeded |
| `include_id_collision` | Final expanded node IDs are not unique |
| `include_reference_invalid` | A dependency or output reference cannot be rewritten safely |
| `include_resource_invalid` | An origin-bound resource is missing, unsafe, unreadable, or changed |

Compilation failure creates no run, no trust grant, and no partially published
snapshot. Temporary staging is removed through the existing recoverable
snapshot-cleanup path.

## 5. Immutable dependencies, resources, and trust

### Independent package digests

Each selected source package is verified independently with the existing
contained-resource rules. Its package digest covers:

- definition bytes;
- optional sidecar bytes, even though child sidecars are inactive;
- named command and script resources;
- MCP definitions and their discovered local resources; and
- loop-command resources introduced by this phase.

Regular-file, symlink, containment, before/after identity, per-file size, file
count, and total-byte checks remain mandatory. A shared bounded read budget is
sealed after verification; later admission steps may read only cached bytes.
A cache miss or changed identity fails rather than reopening mutable source.

### Resource origins

Each executable resource binding contains a logical package key, normalized
relative source path, SHA-256, byte size, media type where applicable, and a
collision-proof snapshot-relative path.

Snapshot paths use a deterministic package namespace derived from the ordered
logical package identity and digest. They do not use absolute paths. Two
packages may safely contain `commands/review.md`; their compiled node bindings
point to distinct sealed paths.

Before the expanded definition is written, command, named-script, MCP, and
loop-command references are rewritten to their sealed snapshot-relative
bindings. On resume, the snapshot root is the only resource root.

### Composite digest

The composite definition digest is SHA-256 over canonical JSON with an exact
schema version and these logical fields:

```json
{
  "schema_version": 1,
  "root_package_digest": "<sha256>",
  "dependencies": [],
  "expanded_definition_digest": "<sha256>",
  "node_origins_digest": "<sha256>",
  "resource_bindings_digest": "<sha256>",
  "active_root_policy_digest": "<sha256>"
}
```

Dependency entries are sorted canonically and contain logical workflow name,
catalog source, precedence, definition location, package digest, and whether a
sidecar was present and ignored. Include-instance expansion is represented by
the expanded definition and origin digests. No field contains an absolute path,
file timestamp, inode, or unredacted resource contents.

### Trust and risk

The composite digest, not an individual child trust record, is the unit of root
execution trust. Trusting a child separately does not automatically trust a new
composition, and trusting one composition does not trust a later dependency
selection.

The risk summary evaluates the final expanded nodes under the active root
sidecar. It includes per-origin contributions for shell/script nodes, requested
tools and skills, MCP servers, providers, expanded outward nodes, required root
secrets, and execution-environment requirements. Child-sidecar declarations
are shown separately as ignored provenance and never lower the root's risk.

Any selected dependency definition, sidecar, resource, catalog source, or
precedence change produces a different composite identity for future
admission. Scheduled runs resolve and compare the whole closure before each new
admission. A change follows the existing trust/revalidation workflow rather
than silently launching new bytes.

An admitted run never revalidates against live source. It resumes its sealed
closure even if the installed packages are edited, deleted, or shadowed.

## 6. Snapshot format 2 and recovery

V4 runs use snapshot format 2. The sealed snapshot contains:

- `definition.yaml`: canonical expanded and normalized workflow definition;
- `policy.yaml`: the active root sidecar, or canonical empty policy;
- `dependencies.json`: exact closure, include edges, ignored-sidecar markers,
  node origins, resource bindings, counts, and composite digest inputs;
- origin-namespaced copies of root and dependency definitions, sidecars, and
  covered resources for audit and deterministic reload;
- the existing `inputs.json`, node/agent skill snapshots, and inputs;
- `resources.json` with language v4 semantics, inherited Phase 3 execution
  semantics, dependency-manifest digest, and complete sealed paths; and
- the existing sealed snapshot digest over every authenticated path.

The published run projection records snapshot format 2, composite definition
digest, active root-policy digest, dependency-manifest digest, normalizer v4,
and expanded node list. `definition_digest` remains the public trust identity
but now carries the composite digest for v4.

Snapshot format 2 reload:

1. authenticates `resources.json` and every sealed path;
2. authenticates and parses `dependencies.json` with exact fields and bounds;
3. loads `definition.yaml` with recorded normalizer v4 without discovery;
4. verifies normalized definition, origin, binding, policy, and composite
   digests; and
5. constructs the same execution package rooted at the snapshot directory.

Any mismatch fails closed with existing snapshot mismatch/recovery isolation.
The loader never repairs semantic data from current installed packages. Format
1 remains byte-compatible and is read through the current path.

## 7. Ordinary loop schema and prompt sources

### Authoring

V4 loops require exactly one prompt source:

```yaml
- id: refine
  loop:
    prompt: "Improve the previous result"
    until: DONE
    max_iterations: 5
    interactive: true
    signal_completes: false
    gate_message: "Accept this result or provide feedback"
```

or:

```yaml
- id: refine
  loop:
    command: refine-result
    until: DONE
    max_iterations: 5
```

The exact V4 loop fields are `prompt`, `command`, `until`, `max_iterations`,
`fresh_context`, `until_bash`, `interactive`, `signal_completes`, and
`gate_message`.

- Exactly one of `prompt` and `command` must be a nonempty string.
- `until` remains a nonempty completion signal.
- `max_iterations` remains an integer from 1 through 100.
- `interactive` and `signal_completes`, when present, are booleans.
- `gate_message` is required when loop interactivity is true.
- `signal_completes: false` is valid only for an effectively interactive loop.
- Existing `fresh_context` and normalized `until_bash` semantics remain.

### Effective interactivity and defaults

For v4:

```text
effective_interactive =
    workflow.options.interactive is true
    and loop.interactive is true
```

`signal_completes` defaults to `false` when `effective_interactive` is true and
to `true` otherwise. An explicit `true` always permits autonomous signal
completion. An explicit `false` without effective interactivity fails at load
time because no operator-confirmation path exists.

The workflow-level requirement applies after expansion. A child loop cannot
make a noninteractive root workflow pause. Child top-level `interactive` is
ignored with the other child defaults.

### `loop.command`

`loop.command` names a command resource using the existing command-resource
resolution and Markdown/frontmatter parser. It is not a shell command and does
not invoke the terminal merely because the field is named `command`.

The resolver binds the resource to the loop node's origin package, validates
and parses it from the shared authenticated read cache, rejects missing,
unreadable, invalid, or empty bodies before admission, and copies it into the
snapshot. Every iteration uses the same sealed body. Editing or deleting the
installed source after admission has no effect.

The language snapshot records only the sealed binding identity. Prompt bodies
remain in authenticated resources and are not duplicated into public metadata,
logs, risk summaries, or interaction events.

## 8. Loop execution and interaction state machine

### Iteration order

For each iteration, the loop executor:

1. checks cancellation;
2. builds the next variable context from sealed semantics, prior output, and
   optional bounded user feedback;
3. executes the inline or sealed command prompt through the existing AI node
   executor and shared/fresh-context rules;
4. commits the iteration artifacts and metadata;
5. reads the bounded output, detects and strips the completion marker, and
   republishes the cleaned artifact identity;
6. records durable iteration evidence;
7. handles a signal-bearing result;
8. only when no signal was found, evaluates normalized `until_bash`; and
9. succeeds, pauses for ordinary input, continues, cancels, or reaches the hard
   iteration failure.

A signal takes precedence over `until_bash`, preserving existing evaluation
order. `until_bash` success completes immediately and does not create a signal
confirmation gate.

### Outcome matrix

| Iteration result | V4 outcome |
|---|---|
| Signal and effective `signal_completes: true` | Succeed immediately |
| Signal and confirmation required | Pause at `loop_signal_confirmation` |
| No signal and `until_bash` succeeds | Succeed immediately |
| No completion, iterations remain, and effective interactivity | Existing `loop_input` pause |
| No completion, iterations remain, and noninteractive | Start next iteration |
| No completion on the final iteration | Fail `loop_max_iterations` without an unusable input pause |
| Cancellation before/between work | Propagate existing cancelled/interrupted result |

Completion markers are absent from downstream output regardless of immediate
completion or confirmation. The artifact digest stored in the pending
interaction is the digest after marker removal.

### Signal-confirmation interaction

The paused node records an exact bounded interaction:

```json
{
  "type": "loop_signal_confirmation",
  "interaction_id": "<sha256>",
  "message": "Accept this result or provide feedback",
  "iteration": 2,
  "result_artifact": "nodes/refine/...",
  "result_sha256": "<sha256>"
}
```

The interaction identity binds run ID, node ID, iteration, result digest, and
gate message. The journaled state and artifact already exist before the pause
becomes externally visible.

Backend-authored actions are:

- `approve`: atomically accept the existing result and complete the loop node;
- `provide-input`: when another iteration remains, atomically discard the
  pending signal, persist nonempty bounded feedback, and make the loop ready
  for its next iteration; and
- `cancel`: use the existing cancellation path.

Both mutating decisions require the current `expected_version` and exact
`interaction_id`. Duplicate or stale actions change nothing. Approval never
re-enters the executor and never creates another provider request. Feedback is
consumed by exactly the next iteration and then cleared through the existing
loop input lifecycle.

At the final permitted iteration, a signal confirmation offers only `approve`
and `cancel`; `provide-input` is absent and rejected without mutation because
no next iteration can run. An approval comment remains audit metadata and is
not treated as loop feedback. Feedback must be nonempty UTF-8 text within the
existing input-byte bound.

### Journal and recovery evidence

Stable events include:

- `loop_iteration_completed` with bounded artifact identities;
- `loop_signal_confirmation_required`;
- `loop_signal_accepted`;
- `loop_feedback_provided`; and
- existing cancellation, failure, and terminal events.

Recovery reconstructs a pending confirmation entirely from the journal and
sealed artifacts. A crash after iteration publication but before pause
publication either finishes the idempotent pause transition or leaves a
reconcilable internal transition; it does not rerun the provider. A crash after
approval but before downstream scheduling reconstructs the completed node and
makes downstream work runnable once. Artifact publication, interaction
creation, and action consumption remain fenced by the current run/node state
version and execution ownership.

## 9. Public actions, APIs, and Desktop

### Reuse existing wire actions

Phase 4 does not add `accept-result` or `provide-feedback` mutation endpoints.
Those are presentation labels for the existing `approve` and `provide-input`
wire actions.

For `loop_signal_confirmation` before the final iteration, the authoritative
action list is:

```json
["status", "events", "approve", "provide-input", "cancel"]
```

On the final permitted iteration it is
`["status", "events", "approve", "cancel"]`.

The action validator, store, CLI, Gateway, REST endpoint, attention projection,
notifications, and Desktop all consume the pending interaction type plus this
backend-authored list. No client infers valid mutations from run status alone.

### Version skew

- A new backend with an older Desktop remains usable because the older client
  already understands `approve`, `provide-input`, and `cancel`. It may show
  generic labels for the new interaction type.
- A new Desktop uses interaction-aware labels: **Accept result** and
  **Continue with feedback**.
- A new Desktop connected to an older backend receives no v4 interaction and
  does not synthesize one.
- Unknown future interaction types remain inspectable and cancellable; clients
  do not crash or invent a mutation.
- Conflict responses include the current bounded public run projection so a
  client can refresh authoritative state.

Desktop continues to call the existing workflow REST surface through its
shared client. It does not parse definitions, expand includes, inspect local
package paths, or decide trust.

### Diagnostics and projections

`workflow validate`, `show`, `doctor`, catalog detail, run detail, evidence, and
Desktop expose bounded backend-authored Phase 4 information:

- effective profile and normalizer;
- snapshot and dependency-manifest schema versions;
- selected logical dependencies, sources, and precedence;
- expanded node/edge counts and include depth;
- composite digest;
- ignored child sidecars and ignored child policy fields;
- per-package executable-risk contributions;
- logical node/resource origins;
- current loop iteration and completion mechanism; and
- pending interaction, result artifact identity, state version, and valid
  actions.

Diagnostics never include command/prompt bodies, feedback contents, secrets,
provider responses, raw resource contents, or unnecessary absolute paths.
`doctor` explains that admitted loops and includes use sealed resources and
that later source edits affect only future admission.

## 10. Prompt caching and model-loop invariants

Phase 4 compilation occurs before a run and outside every model conversation.
It does not change the system prompt, core tool list, or tool schemas.

An inline or command-backed loop continues to use the existing AI executor.
The sealed command body replaces the inline loop prompt at the same prompt
position; it is not injected as a new system message. Feedback is supplied
through the next iteration's existing variable context. Hermes does not insert
a synthetic user message into the outer agent loop or mutate prior history.

Shared loop context retains the current explicit session and cache-fingerprint
rules. Included node namespacing changes session identity only for newly
admitted v4 nodes and is sealed in the expanded definition. Resume never
renames a session or transplants history.

## 11. Error handling and observability

Every Phase 4 failure has a stable code, bounded safe message, logical source
location, and root include chain when applicable. Errors distinguish:

- source parsing;
- catalog selection;
- include traversal or expansion;
- graph/reference validation;
- resource authentication and origin binding;
- trust or scheduled revalidation;
- snapshot authentication;
- loop validation;
- stale interaction conflict; and
- runtime provider, Bash, cancellation, or hard iteration failure.

Expected user-authored validation failures do not log tracebacks at warning or
error level. Unexpected internal failures retain normal redacted structured
logging. Interaction logs carry IDs and artifact digests, never feedback or
prompt contents.

The existing workflow event stream remains the durable observability source.
Phase 4 does not add outbound telemetry, third-party attribution, or a new
analytics destination.

## 12. Mandatory defensive validation

Security-sensitive Phase 4 behavior is expressed as ordinary deterministic
invariants with benign fixtures. Completion requires proof that:

1. Definition, sidecar, command, script, and MCP symlinks cannot escape or
   bypass package containment.
2. Every selected dependency and executable resource is represented in the
   closure manifest, composite digest, sealed paths, and snapshot digest.
3. Identically named resources in different packages retain distinct origin
   bindings.
4. No executor, resume path, action, or scheduler revalidation path reads live
   dependency or command source after admission.
5. Definition, dependency, node, edge, file, byte, input, event, and run-storage
   bounds fail before unbounded allocation or publication.
6. Catalog shadowing or dependency mutation changes future-admission identity.
7. Existing admitted runs remain unchanged after source mutation or deletion.
8. State-version and interaction-ID checks prevent stale, duplicate, or
   cross-run signal actions.
9. Approval cannot replay a provider call or duplicate an output artifact.
10. Public diagnostics do not expose secret values, prompt/resource contents,
    user feedback, provider responses, or unnecessary absolute paths.

These tests are mandatory security-boundary evidence even if a broader
adversarial review cannot run.

## 13. Best-effort adversarial security review

After deterministic gates pass, the phase attempts a narrowly scoped defensive
review covering include/resource containment, digest completeness, trust
revalidation, stale-action fencing, disclosure, and crash/replay behavior.

The review request does not ask for exploit development, destructive payloads,
credential handling, external targeting, or instructions to bypass safeguards.

If Codex stops the review through a platform security gate:

1. stop rather than repeatedly rephrasing to evade the gate;
2. record `BLOCKED_BY_PLATFORM_GATE` and the exact intended defensive scope;
3. retain the completed deterministic, static-analysis, and human-review
   evidence;
4. list the blocked review as an exclusion, not a pass; and
5. continue Phase 4 completion when every mandatory functional, durability,
   compatibility, and defensive invariant is green.

A platform-blocked optional adversarial review does not independently fail the
phase. A failing mandatory invariant, reproducible defect, or unresolved
Critical/High finding does.

## 14. Test strategy and acceptance matrix

### Unit and contract tests

- Version predicates for every profile/version combination.
- V4 normalization inheriting structured outputs, strict references, retry,
  timeout, Bash, and persistent-session semantics.
- Exact v4 loop fields, defaults, invalid combinations, and language snapshot
  round trips.
- Raw source parsing separated from root-profile compilation.
- Literal target selection and same-precedence ambiguity.
- Depth, cycle, repeated-package, empty-graph, namespace, and final collision
  behavior.
- Entry/sink selection and deterministic first-sink output aliases.
- Syntax-aware dependency, condition, template, and typed-output rewrites.
- Origin-bound resource lookup and collision-proof compiled paths.
- Independent package and canonical composite digests.
- Exact dependency manifest and snapshot format 2 readers.
- Root sidecar authority and include-alias outward-action fan-out.

### Loop runtime tests

- V3 immediate signal completion remains unchanged on the v4 runtime.
- V4 noninteractive signal completion defaults to immediate.
- V4 effective interactive signal completion defaults to confirmation.
- Explicit `signal_completes: true` bypasses confirmation.
- Invalid `false` without effective interactivity blocks admission.
- Approval succeeds from the committed result with zero additional provider
  calls.
- Feedback removes the pending signal and starts exactly one next iteration.
- Final-iteration confirmation omits and rejects feedback while preserving
  approval and cancellation.
- Empty, oversized, stale, duplicate, cross-run, and wrong-interaction feedback
  is rejected without mutation.
- Signal marker removal is identical for immediate and confirmed completion.
- Signal precedence over `until_bash` and immediate `until_bash` success.
- Missing, invalid, edited, and deleted `loop.command` resources.
- Fresh/shared context, feedback consumption, cancellation, and hard iteration
  limits.

### Include and admission integration tests

- Root -> child and root -> child -> grandchild -> depth-3 closure.
- Depth-4 rejection, direct and indirect cycles, and repeated safe reuse.
- Multiple entries, multiple sinks, parent join rules, downstream sink fan-out,
  first-sink output, and typed first-sink field references.
- No `with`, dynamic target, deep child access, or include in `loop_group`.
- Root and child resources with identical relative names.
- Child definition, ignored sidecar, and resource changes each invalidate future
  composite identity.
- Catalog source/precedence changes and shadowing require revalidation.
- An admitted run resumes after all original source packages are removed.
- Scheduled admission resolves the closure once per new run and blocks changed
  untrusted bytes.
- Trust summary and doctor show complete per-origin risk and ignored policies.

### Crash, race, and recovery tests

- Crash before and after iteration artifact publication.
- Crash before and after confirmation-pause journal publication.
- Crash before and after approval commits node success.
- Crash before and after feedback makes the node ready.
- Concurrent approve/feedback/cancel actions yield one authoritative winner.
- Coordinator takeover does not repeat a completed iteration or lose the
  pending signal.
- Snapshot tampering, missing resource, manifest mismatch, and wrong origin
  enter existing fail-closed recovery isolation.

### Surface and distribution tests

- CLI validate/show/doctor/run/status/events/approve/provide-input/cancel.
- Gateway command dispatch and notification categorization.
- REST mutation, attention inbox, evidence, conflict refresh, and bounded
  projections.
- Desktop labels, feedback input, double-click suppression, stale refresh, and
  unknown-interaction fallback.
- New backend with old-client action vocabulary and new client with old backend.
- Generated schema/metadata consistency and customization checker.
- Built wheel contains the Phase 4 modules, schemas, examples, and resources;
  installed-wheel tests execute outside the repository.
- Complete Python, plugin, API, Gateway, Desktop, and `base` gates.

## 15. Delivery order and atomic activation

Implementation follows this dependency order:

1. cumulative normalizer capabilities and v1-v3 regression fixtures;
2. raw source/discovery snapshot contracts;
3. include closure resolver, expander, origins, and bounds;
4. composite digest, trust/risk, snapshot format 2, and scheduled
   revalidation;
5. v4 ordinary-loop normalization and sealed `loop.command` resources;
6. signal-confirmation store transitions, executor behavior, and recovery;
7. CLI, Gateway, REST, evidence, notifications, and Desktop presentation;
8. generated schema, examples, docs, installed-wheel proof, full base gate, and
   adversarial functional review; and
9. mandatory defensive evidence plus the best-effort security review policy.

Internal stages remain unreachable from newly admitted public workflows until
the final activation change sets the Archon current normalizer to 4. Tests may
request v4 explicitly while the implementation is incomplete. The activation
commit is small, reviewable, and occurs only after all required gates pass.

Phase 4 is complete only when:

- every requirement in this specification has an implementing task and test;
- all Critical and High functional/adversarial findings are resolved;
- all mandatory defensive invariants pass;
- any platform-blocked security review is documented as an exclusion;
- the full predecessor and `base` gates are green;
- installed-wheel execution is green;
- status and handoff artifacts describe the actual integrated state; and
- completed feature work is merged to `base` according to repository branch
  policy.

## References

- `docs/superpowers/specs/2026-07-25-workflow-language-compatibility-expansion-design.md`
- `docs/superpowers/specs/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience-design.md`
- `docs/reviews/2026-08-04-workflow-language-phase-3-adversarial-remediation.md`
- <https://archon.diy/guides/loop-nodes/>
- <https://archon.diy/guides/authoring-workflows/>
