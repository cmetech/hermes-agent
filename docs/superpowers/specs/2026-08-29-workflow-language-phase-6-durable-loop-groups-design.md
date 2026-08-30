# Workflow Language Phase 6: Durable Loop Groups Design

**Status:** Review-ready draft; implementation is not authorized

**Date:** 2026-08-29

**Branch:** `base`

**Baseline:** `7f323641fe4bf56df0fc787cc42941dff698f2d3`

## Purpose

Phase 6 adds durable `loop_group` execution to the `archon-2026-07`
workflow-language profile and proves it with one representative migration of the
legacy **Issue_ JIRA Defect Triage** flow.

A `loop_group` repeats a bounded multi-node DAG body. The outer workflow sees
one node. Internally, each iteration uses the existing node executors,
provider authority, approvals, output validation, artifact accounting,
cancellation, worker limits, and recovery rules. Phase 6 does not create a
recursive scheduler, child workflow runs, or a second ownership model.

The result is stronger than a whole-group restart: completed body work is
journaled at child-node granularity, replay-safe interrupted work may resume,
and uncertain outward work stops for reconciliation instead of being repeated.

## Locked product decisions

The following decisions were approved during design discussion:

- Implement the engine plus one representative Jira Defect Loop migration.
- Fetch an immutable run-scoped manifest of at most 25 Jira ticket keys before
  batch processing starts. Tickets discovered later wait for the next run.
- Record each ticket outcome in Hermes workflow history and publish bounded JSON
  and Markdown aggregate artifacts.
- Jira and GitLab receive only exact, current, individually approved writes.
  Phase 6 adds no spreadsheet or email destination.
- Expected ticket-specific outcomes are recorded and the batch continues.
  Ambiguous writes, integrity loss, cancellation, and lost execution authority
  stop the group for reconciliation.
- Workflow listing and mutation remain profile-scoped. Profile B neither sees
  nor acts on a run owned by Profile A. The run becomes visible and actionable
  again only after explicitly switching back to Profile A.
- The Workflow board shows one parent run card, not one card per iteration.

## Baseline and invariants

Implementation starts from the verified Phase 5 closure:

- new `archon-2026-07` admissions use normalizer v5;
- legacy workflows remain on normalizer v2;
- supported normalizer readers are versions 1 through 5;
- snapshot format 2 authenticates the complete compiled definition, resource
  closure, provider-resolution manifest, and language snapshot;
- `RunStore` is the profile-scoped durable authority;
- `RunScheduler.advance_all()` fairly replenishes ordinary ready nodes from
  several runs through one bounded profile worker pool;
- worker claims, leases, process identities, journal reserves, cancellation,
  epoch fences, and stale-claim recovery already exist;
- ordinary loops already persist authenticated iteration output before making a
  continuation or interactive decision;
- Phase 5 provider decisions and execution identities are sealed before a node
  can run;
- public projections are bounded backend-authored summaries, never raw prompts,
  credentials, provider payloads, or unrestricted filesystem paths; and
- Desktop workflow reads, mutations, query keys, and caches are scoped to the
  selected profile.

Phase 6 preserves strict role alternation and the byte-stable system-prompt
contract. Reusing a body-node session across iterations adds new user turns to
that node session; it never rewrites prior messages or changes the session's
model-visible system prefix.

## Existing infrastructure to extend

Phase 6 adds no parallel authority.

| Existing authority | Phase 6 extension |
| --- | --- |
| `plugins.workflow.language` | Normalize and hash a bounded nested body as v6 semantics. |
| `plugins.workflow.language_schema` | Publish the authoring schema, diagnostics, inventory, and editor projection. |
| Compilation and dependency manifests | Traverse group bodies for dependencies, resources, provider obligations, risk, and semantic identity. |
| Snapshot format 2 | Store v6 nested-body semantics and authenticated resources without changing the envelope version. |
| `RunScheduler.advance_all()` | Treat ready body children as scoped work candidates in the existing fair pool. |
| Existing node executors | Execute a child with an explicit scoped attempt/artifact directory. Top-level defaults remain byte-compatible. |
| `RunStore` | Journal namespaced controller, iteration, child, claim, interaction, artifact, and recovery state. |
| Existing worker-claim table | Count namespaced child attempts against the same profile/run limits. No new worker pool or table is needed. |
| Ordinary loop decisions | Reuse completion signal, `until_bash`, interaction, and confirmation semantics. |
| Existing workflow API and Desktop | Add bounded group progress and child summaries while retaining the same run/action routes. |

## Alternatives considered

### Selected: subordinate controller using the existing scheduler

The outer DAG contains one `loop_group` node. A durable controller owns its
iteration/body state, but it does not own an executor pool. It exposes ready
body children to `RunScheduler.advance_all()`, which schedules them alongside
ordinary nodes through the existing round-robin and worker-claim path.

This preserves one scheduling authority, one capacity limit, and one recovery
model while allowing body layers to run concurrently.

### Rejected: flatten every possible iteration into the outer DAG

Admission could expand `max_iterations * body_nodes` into ordinary top-level
nodes. That makes completion dynamic, previous-iteration references awkward,
inflates public topology, and cannot represent an interactive stop without
pre-creating misleading future nodes. It also turns a durable controller
problem into a large compile-time graph with worse operator UX.

### Rejected: recursively invoke the top-level scheduler

Starting a scheduler or child workflow run per iteration duplicates ownership,
capacity, cancellation, and recovery. It can deadlock at small worker limits,
creates a second run identity, and conflicts with the approved rule that the
outer DAG sees one node.

## Version and activation boundary

### Normalizer v6 is required

`loop_group` introduces nested node identity, scoped references, resource
inheritance, child execution identities, and durable recovery state. Reusing v5
would make already-admitted definitions acquire new meaning.

Phase 6 therefore:

- adds normalizer v6;
- changes the current `archon-2026-07` normalizer from 5 to 6 only in the final
  activation commit;
- leaves `hermes-legacy` at v2;
- expands the reader set to `{1, 2, 3, 4, 5, 6}`; and
- executes v1-v5 snapshots through their recorded readers without v6
  reinterpretation.

### Snapshot format remains 2

Format 2 already seals an arbitrary authenticated resource set plus a
versioned language snapshot. A v6 snapshot adds nested body semantics,
resource bindings, body-node provider obligations, and their digests to the
existing authenticated documents. It does not require a format-3 envelope.

No historical run is rewritten or backfilled. The admission SQLite schema also
does not need a destructive migration: namespaced body claims reuse the
existing `worker_claims` rows, and durable child state remains inside the
authenticated run projection and journal. Claim reconciliation learns to walk
that nested state.

All v6 code remains dormant until language, admission, scheduler, recovery,
surface, migration, installed-distribution, and profile-isolation tests pass.

## Authoring contract

### Shape

The pinned Phase 6 shape is:

```yaml
- id: process-items
  depends_on: [prepare-items]
  model: medium
  loop_group:
    nodes:
      - id: select-item
        script: |
          print("select the next item deterministically")
        runtime: uv

      - id: process-item
        prompt: >
          Process $select-item.output using the immutable manifest from
          $prepare-items.output and the previous result
          $LOOP_PREV.record-item.output.
        depends_on: [select-item]

      - id: record-item
        prompt: "Record the bounded outcome from $process-item.output"
        depends_on: [process-item]

    until: BATCH_COMPLETE
    max_iterations: 25
```

The `loop_group` mapping accepts the same bounded loop-control fields as the
v4 ordinary-loop contract plus `nodes`:

- `nodes`;
- `until`;
- `max_iterations`;
- `fresh_context`;
- `until_bash`;
- `interactive`;
- `signal_completes`; and
- `gate_message`.

For the pinned `archon-2026-07` profile, `until` remains required and
`max_iterations` remains an integer from 1 through 100. Phase 6 does not import
newer upstream experiments such as optional `until`, declared gate decisions,
runtime child workflows, or parameterized includes.

### Body node surface

Body nodes use already-supported v6 node kinds and options. Phase 6 does not
invent a special batch-task node or a core model tool.

- `prompt`, `command`, `bash`, `script`, `approval`, `cancel`, and ordinary
  `loop` body nodes are accepted when their existing contracts pass.
- An `include` inside a group body is rejected.
- A nested `loop_group` is rejected.
- A runtime `workflow` child remains unsupported.
- `retry` on the outer group is rejected because it would restart a durable
  controller. A body node may use its existing admitted retry contract.
- Group-level provider/model/options are body defaults. An explicit body-node
  value overrides the default and is resolved by the existing Phase 5
  authority.
- `fresh_context: true` selects a fresh body-node session each iteration.
  Otherwise the same body-node session may advance across iterations only when
  its sealed session fingerprint remains identical.

### Body topology

The body is a sealed DAG:

- body IDs are unique within the group;
- `depends_on` names body siblings only;
- body cycles are rejected;
- body conditions and trigger rules use the existing semantics;
- independent ready body nodes form a topological layer and may run
  concurrently through the shared scheduler; and
- the first terminal body node in definition order is the primary sink.

The primary sink supplies the iteration result and, on final completion, the
outer `$group.output`. A primary sink that is skipped, failed, or produces no
required output fails the group with a stable output-unavailable code. Phase 6
does not add a `returns` selector.

### Reference scopes

Each reference has one unambiguous scope:

- `$body-node.output` reads that sibling's current-iteration output and requires
  a body dependency under the existing strict reference rules.
- `$outer-node.output` reads a completed outer node only when that outer node is
  a direct dependency of the `loop_group` node. Body nodes do not repeat outer
  IDs in their own `depends_on` lists.
- `$LOOP_PREV.<body-node>.output` reads exactly the immediately previous
  iteration of that direct body sibling.
- `$LOOP_PREV.<body-node>.output.<field>` uses the producer's declared structured
  schema and existing strict field rules.
- On iteration one, a known whole-output `$LOOP_PREV` reference resolves to the
  empty string. Unknown body IDs and invalid field paths fail loudly.
- Outputs from iteration N-2 or earlier are never addressable through workflow
  variables.

Current and previous scopes are resolved before process launch and bound to
authenticated output descriptors. A live file edit, stale artifact, failed
producer, or digest mismatch cannot supply a value.

### Interactivity

A group is effectively interactive only when both the root workflow and the
group opt in, exactly like an ordinary v4 loop.

Between iterations, `signal_completes` and `gate_message` reuse ordinary-loop
confirmation behavior and existing action names. An approval node inside the
body is a separate durable interaction at an exact group/iteration/body
identity. Its decision resumes that child and then the remaining body DAG; it
does not restart the iteration.

Only one pending interaction may own a run at a time. Interaction IDs bind the
profile-scoped run, outer group, controller generation, iteration, body node,
artifact digest, and allowed action. An old interaction cannot act on a later
iteration or another profile.

## Admission and sealing

### Recursive normalization without recursive scheduling

Normalizer v6 recursively normalizes the authored body into immutable nested
`WorkflowNode` values. This recursion is compile-time data normalization only.
The runtime scheduler remains single-level and receives explicitly scoped work
candidates from the controller.

Every nested node contributes to:

- semantic identity;
- workflow and node risk;
- service and connector requirements;
- command/script/skill/MCP/hook resource discovery;
- provider/model capability resolution;
- timeout, retry, budget, and process limits;
- output-reference declaration and schema validation;
- dependency-manifest identity; and
- public compatibility diagnostics.

The root companion remains policy authority. A nested body cannot introduce a
second companion, trust decision, or config scope.

### Bounds

Admission computes worst-case work before creating a run. At minimum it binds:

```text
child_executions = max_iterations * body_node_count
child_attempts = max_iterations * sum(body_node_max_attempts)
```

Those quantities must fit the existing definition, worker, retry/iteration,
artifact, run-byte, and journal-reserve ceilings. Admission also accounts for
ordinary loops inside the body, their maximum iterations, and their maximum
attempts. Bounds multiply; they are never averaged or checked only at runtime.

The implementation plan will use the existing 512-node definition ceiling and
4096-edge/expansion scale as hard upper reference bounds rather than adding an
unbounded Phase 6 setting. The representative Jira flow additionally fixes its
business maximum at 25 tickets.

Rejection occurs before work, provider cost, connector reads, or run
publication. Diagnostics name the authored group, calculated product, and the
ceiling exceeded without exposing prompt/resource contents.

### Snapshot reload

A v6 reload:

1. authenticates the format-2 run snapshot and provider manifest;
2. decodes the nested body with recorded normalizer v6;
3. verifies body topology, primary sink, scoped reference declarations,
   resource origins, and semantic digests;
4. reconstructs identical nested executor requests without discovery; and
5. rejects any missing or contradictory v6 material as snapshot integrity loss.

The loader never repairs v6 state from the currently installed package.

## Runtime architecture

### Controller state

The outer node stores bounded private state equivalent to:

```json
{
  "schema_version": 1,
  "controller_generation": 1,
  "iteration": 3,
  "max_iterations": 25,
  "state": "running",
  "primary_sink": "record-item",
  "previous_outputs": {
    "record-item": {
      "relative_path": "...",
      "size_bytes": 123,
      "sha256": "..."
    }
  },
  "body": {
    "select-item": {"state": "succeeded", "attempts": []},
    "process-item": {"state": "running", "attempts": []},
    "record-item": {"state": "pending", "attempts": []}
  }
}
```

The actual projection uses the existing sanitized claim, attempt, output,
interaction, recovery, retry, timeout, and artifact shapes rather than
inventing weaker duplicates. Only the nesting and scope identity are new.

Controller transitions occur while holding the existing run lock and execution
fence. The holder of the run's current execution ownership drives the
controller; the controller has no independent process-global owner or lease.
Every transition is idempotent against controller generation and projection
state version.

### Shared scheduling

The controller never creates a `ThreadPoolExecutor`. Instead:

1. graph resolution initializes a ready outer group into iteration one;
2. the controller reports its ready body children as scoped candidates;
3. `RunScheduler.advance_all()` mixes those candidates with ordinary ready
   nodes for the same and other runs;
4. the existing fair cursor chooses runs, and deterministic source order chooses
   work within a run;
5. `RunStore.claim_loop_group_child()` atomically checks run/profile capacity and
   inserts a namespaced row in the existing `worker_claims` table; and
6. the same executor pool runs the child through its existing executor.

A worker key is an internal canonical identity containing run, group,
controller generation, iteration, and body node. It is never accepted from an
API caller or reused as a filesystem path. The claim table therefore counts
body work against the same `max_total_workers` and per-run limits as ordinary
work.

An outer group controller does not consume a worker while merely waiting for a
child, approval, capacity, or the next scheduler pass. This avoids the
single-worker deadlock in which a controller holds the only slot needed by its
own body.

### Scoped executor paths

Child attempts use deterministic contained paths:

```text
nodes/<group-id>/<group-generation>/iterations/<0001>/nodes/<body-id>/<attempt-id>/
artifacts/loop-groups/<group-id>/iterations/<0001>/<body-id>/
```

`NodeExecutionContext` gains explicit attempt and publication directories.
Every existing executor uses those directories; top-level defaults preserve its
current paths. Resource resolution continues from the authenticated run root.
No body ID, ticket key, model output, or API value is concatenated directly
into a path.

Artifacts retain descriptor-relative identity, size, digest, media type,
producer scope, and attempt identity. The public API exposes controlled
publication IDs and bounded logical labels, not raw private paths.

## Iteration state machine

For each iteration, the controller:

1. verifies execution ownership, cancellation, deadline, budget, and sealed
   package/provider identity;
2. creates the iteration state and carries only authenticated previous-iteration
   output descriptors;
3. resolves ready body nodes and yields them to the shared scheduler;
4. journals every claim, start, pause, retry, completion, failure, output, and
   process lifecycle under the child scope;
5. resolves body dependencies until the body is terminal;
6. fails immediately if any required body node fails;
7. authenticates the primary sink result and strips an exact completion marker;
8. commits the iteration result and previous-output snapshot;
9. evaluates signal completion, then `until_bash` only when no signal completed;
10. succeeds, pauses for ordinary-loop confirmation/input, starts the next
    iteration, cancels, or fails at the hard maximum.

Iteration N+1 is never visible as ready before iteration N and its completion
decision are durably committed. Independent nodes within one iteration may be
concurrent; different iterations of the same group never overlap.

### Completion and output

Completion detection uses the primary sink's raw output, matching the approved
first-terminal-node contract. Completion tags are removed before the result is
published downstream. `until_bash` resolves current body outputs, approved
outer dependencies, and previous-iteration values through the same strict
renderer and contained Bash execution path as ordinary loops.

The final outer group output is the cleaned, authenticated primary-sink output
from the completing iteration. If the primary sink has structured output, its
validated logical value and declared fields remain available to downstream
strict references.

Reaching the hard maximum without completion fails with
`loop_group_max_iterations`. No unusable interaction is created after the last
iteration.

## Failure, cancellation, and recovery

### Failure classification

A failed required body node fails the group. The group does not restart from
iteration one, and the outer group cannot be retried as a unit.

Expected domain outcomes must be successful, structured node results. For the
Jira migration those include `not_found`, `permission`, `needs_info`,
`manual_review`, `not_a_code_fix`, and `safely_skipped`. They are retained in
the aggregate and do not masquerade as engine failure.

The following stop the group and require the existing failed/attention or
reconciliation path:

- ambiguous Jira or GitLab write outcome;
- conflicting existing outward object that cannot be corroborated;
- output, artifact, journal, snapshot, provider-authority, or execution-fence
  integrity loss;
- an outward child whose post-crash outcome is uncertain;
- lost foreground/coordinator execution ownership;
- cancellation or unreaped process-tree uncertainty; and
- exhausted retry, deadline, budget, storage, or journal reserve.

Downstream outer nodes cannot consume the last successful iteration output from
a failed group. Strict failed-producer checks remain authoritative.

### Recovery

On startup or stale-claim expiry, the store rebuilds child worker claims from
authenticated nested state and applies the existing effect policy per body
node:

- corroborated succeeded/skipped children remain terminal;
- replay-safe interrupted children with confirmed cleanup may become ready;
- a live process remains owned and monitored rather than duplicated;
- outward or uncertain children enter reconciliation;
- a paused interaction remains bound to its original child and artifact; and
- a controller with no active child resumes from its last committed body or
  iteration transition.

The recovery reader never infers success from a file alone. The projection,
journal chain, attempt state, artifact descriptors, process identity, and
execution fence must corroborate one another.

### Cancellation

Cancellation prevents new child claims, interrupts active child processes
through existing process-tree handling, terminalizes unstarted body children,
and then terminalizes the outer group and run. Stale child completions cannot
win after cancellation because every store transition checks the group
generation, active claim, desired status, and execution fence.

## Evidence and public projections

Private events are namespaced by group, generation, iteration, body node, and
attempt. Stable event families cover:

- controller and iteration start/completion;
- body claim/start/retry/pause/completion/failure;
- completion decision;
- recovery/reconciliation; and
- group completion/failure/cancellation.

Public evidence contains bounded identity and state only: iteration counts,
body node IDs/types/states, durations, attempt counts, categorical failure
codes, interaction summaries, artifact publication IDs, and sanitized warnings.
It excludes prompt text, command/script bodies, tool inputs/results, Jira
descriptions, GitLab file contents, provider responses, feedback text,
credentials, environment values, and private filesystem paths.

The ordinary run event, evidence, attention, cleanup, and mutation routes remain
the API. Phase 6 adds a bounded `loop_group` summary to the node projection; it
does not add an unbounded child-enumeration endpoint.

## Desktop and profile isolation

The Workflows board continues to show one card per workflow run. A running group
may add compact card metadata such as `7 / 25` and an attention indicator. The
existing inspector renders bounded iterations and body-node summaries beneath
the outer group node. It does not create separate board cards for tickets or
child nodes.

Every list, detail, attention, event, artifact, cleanup, and action request
continues to carry the selected Desktop profile. Query and cache keys retain the
profile component. Backend routing resolves that profile's `RunStore` before a
run ID is looked up or mutated.

Required isolation behavior is:

1. start a v6 group run in Profile A;
2. switch to Profile B and start another run;
3. Profile B lists only Profile B's run;
4. a Profile A run ID sent through Profile B's scoped mutation route is not
   found and causes no mutation;
5. switch back to Profile A; its run and valid actions are still present.

Profiles remain a product isolation boundary for workflow state, not separate
OS-user authentication. A local operator who can explicitly switch to Profile A
can then act as Profile A. Phase 6 adds no cross-profile aggregate board.

## Representative Jira Defect Loop migration

### Scope

The migrated workflow proves a bounded item-oriented group using the existing
Ericsson Jira and GitLab connector tools. It does not introduce a general batch
tool, invoke another workflow at runtime, or claim migration of the other seven
legacy iterative flows.

The existing single-ticket showcase and Jira-to-GitLab workflow remain useful
independent paths. Shared package command/script resources may be factored for
reuse, but the batch group owns an explicit static body DAG and all its admitted
tools.

### Ticket manifest

The first outer node performs one `jira_my_tickets` read with
`max_results: 25`. It validates the response, preserves first-occurrence Jira
order, deduplicates, and writes at most 25 exact ticket keys as an authenticated
run output.

This is a runtime ticket manifest, not part of the pre-run definition snapshot.
Once the node completes, every iteration reads that exact artifact. A ticket
added or reassigned afterward is not appended to the active run.

An outer condition skips the group for an empty manifest and routes to an empty
aggregate, so no body iteration is manufactured. A malformed, unbounded, or
uncorroborated manifest fails before any ticket write.

### Body behavior

Each iteration has an explicit static path equivalent to:

1. select the next key deterministically from the manifest and prior cumulative
   record;
2. read the exact Jira ticket;
3. produce a bounded triage classification;
4. resolve the linked GitLab project and inspect only the admitted repository
   context when the classification warrants it;
5. prepare any proposed Jira comment, branch, commit, or merge request without
   treating triage confidence as authorization;
6. request current approval for each exact write target and payload;
7. perform the one approved write, reconciling ambiguous results read-only; and
8. publish a bounded per-ticket outcome and deterministic cumulative record.

The exact GitLab path reuses current connector operations and the reviewed
single-ticket contracts. Phase 6 does not add raw shell access to Jira/GitLab,
an unrestricted API client, or a hidden all-ticket approval.

A deterministic terminal record step may append the exact
`<promise>BATCH_COMPLETE</promise>` marker only after its cumulative record
shows every manifest key has one terminal outcome. Marker removal leaves valid
bounded JSON as the group output. This avoids model-controlled batch
termination and avoids a new item-iteration language field.

### Writes

Each outward operation requires the existing workflow admission authorization
and the connector host's current-action approval. Approval binds:

- profile and run;
- group, iteration, and body node;
- Jira ticket and GitLab project;
- operation name;
- exact proposed comment/branch/commit/MR target and bounded payload digest;
- current attempt and interaction; and
- expiry/consumption state.

Batch-level approval, a prior ticket's approval, or a model's classification
cannot authorize another write. An ambiguous write is never retried blindly.

### Outcomes and artifacts

Every ticket receives one bounded terminal record containing at least:

- ticket key;
- triage category and status;
- GitLab project/branch/commit/MR identities when corroborated;
- Jira comment identity when corroborated;
- warnings;
- attention needed; and
- reconciliation status.

Per-iteration child artifacts provide the per-ticket history. After the group
completes, a deterministic outer node consumes the final cumulative JSON and
publishes:

- an aggregate JSON artifact; and
- a human-readable Markdown summary.

Hermes workflow history is the system of record for the batch result. Jira and
GitLab contain only the individually approved objects they own. Phase 6 does
not send email or write a spreadsheet.

## Verification strategy

### Language and admission

- Valid group schema, defaults, body topology, primary sink, and provider
  inheritance.
- Rejection of cycles, cross-group dependencies, bad outer references,
  unknown `$LOOP_PREV` IDs, includes, nested groups, runtime workflows, and
  group-level retry.
- Product-bound rejection before connector reads or provider cost.
- Normalizer v6 activation with exact v1-v5 replay.
- Snapshot/resource/provider-manifest tamper failures.
- Catalog, schema CLI, doctor, builder guidance, and public compatibility
  projection parity.

### Scheduler and execution

- One-worker progress without controller deadlock.
- Shared worker ceiling under multiple groups and ordinary runs.
- Fair replenishment across runs and deterministic body ordering.
- Parallel body layer execution without cross-iteration overlap.
- Current-iteration, outer-dependency, and previous-iteration reference
  integrity.
- Primary-sink output, signal stripping, `until_bash`, maximum failure, fresh
  and shared sessions, and provider fingerprint changes.

### Interactions and recovery

- Mid-body approval pause/resume without rerunning completed siblings.
- Between-iteration ordinary-loop confirmation behavior.
- Interaction replay, stale interaction, wrong iteration, wrong artifact, and
  wrong-profile rejection.
- Crash/fault injection before and after claim, spawn, output publication,
  child completion, iteration completion, completion decision, and outer
  completion.
- Replay-safe recovery, outward uncertainty, process cleanup, cancellation,
  lease expiry, epoch fencing, and journal reserve exhaustion.

### Jira migration

- Empty, one-ticket, 25-ticket, duplicate-key, malformed, and over-limit
  manifests.
- Tickets appearing after manifest creation wait for the next run.
- Expected ticket outcomes continue and appear in both aggregates.
- Every Jira/GitLab write requires its exact current approval.
- Ambiguous writes stop without duplicate retry.
- Aggregate JSON/Markdown matches the set and order of per-ticket records.
- No spreadsheet/email write and no claim of migrating other legacy loops.

### Surfaces and profiles

- Existing CLI, Gateway, API, notification, evidence, attention, cleanup, and
  action vocabularies remain compatible.
- Desktop card progress and inspector child summaries remain bounded.
- Profile switch race: late Profile A responses never paint Profile B.
- Profile B cannot enumerate or mutate Profile A's group run.
- Installed-distribution, Windows path/process, restart, and multi-process
  tests use a temporary profile home and real imports.

## Out of scope

- Runtime child workflows, child-run recursion, or `workflow:` in a body.
- Includes inside a group body or parameterized `include.with`.
- Nested `loop_group`.
- Dynamic `items`, `map`, fan-out, or model-authored body topology.
- Group-level retry or whole-group automatic restart.
- Migrating the other seven assessed iterative legacy flows.
- A cross-profile workflow board or cross-profile mutation authority.
- Spreadsheet/email delivery for Jira Defect Loop.
- New Jira/GitLab core model tools, a general HTTP client, or unrestricted
  shell adapters.
- New telemetry, analytics, or provider attribution.
- Reinterpreting or rewriting admitted v1-v5 runs.
- Unrelated adoption of post-July Archon workflow-language changes.

## Delivery order

1. Add dormant v6 schema, nested normalization, bounds, semantic identity, and
   snapshot reload.
2. Add scoped child state/claims and executor output directories with top-level
   byte compatibility.
3. Feed group children into the existing fair scheduler and implement the
   iteration/completion controller.
4. Add interaction, cancellation, recovery, reconciliation, evidence, and
   defensive fault-injection coverage.
5. Add bounded API/Desktop group projections and cross-profile isolation tests.
6. Migrate Jira Defect Loop and verify its read/write/artifact contract.
7. Run focused, canonical, installed-distribution, Windows, restart,
   multi-process, Desktop, and brand gates.
8. Activate normalizer v6 only after every gate passes.

## Design authorities

Local project contracts are authoritative over later upstream changes:

- `docs/superpowers/specs/2026-07-25-workflow-language-compatibility-expansion-design.md`;
- `docs/superpowers/specs/2026-08-05-workflow-language-phase-4-ordinary-loops-immutable-includes-design.md`;
- `docs/superpowers/specs/2026-08-06-workflow-language-phase-5-provider-portability-design.md`;
- `docs/design/portable-workflow-orchestration.md`;
- `docs/assessments/loop24-migration/legacy-workflow-portability.md`; and
- Archon's DAG workflow guide for the July `loop_group` authoring shape:
  <https://github.com/coleam00/Archon/blob/dev/packages/docs-web/src/content/docs/book/dag-workflows.md>.

The upstream link is supporting provenance, not rolling semantic authority.
Phase 6 implements the approved pinned contract in this document.

## Acceptance criteria

Phase 6 is complete only when:

- newly admitted `archon-2026-07` workflows can run a bounded multi-node group;
- the body uses existing executors and the existing fair profile worker pool;
- completed child work survives restart without blind replay;
- uncertain outward work stops for reconciliation;
- output, approvals, artifacts, events, cancellation, and recovery are bound to
  group/iteration/body identity;
- old snapshots execute through their recorded normalizers;
- Jira Defect Loop processes one immutable manifest of at most 25 keys and
  produces per-ticket plus JSON/Markdown aggregate history;
- no Jira/GitLab write occurs without exact current approval;
- expected ticket outcomes continue while ambiguous writes stop;
- Profile B cannot see or mutate Profile A's workflow run; and
- no second scheduler, core model tool, cross-profile board, or unrelated
  migration ships.
