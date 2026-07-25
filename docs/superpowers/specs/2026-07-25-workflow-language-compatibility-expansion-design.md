# Workflow Language Compatibility Expansion Design

**Date:** 2026-07-25

**Status:** Conversation-approved umbrella design; awaiting written-spec review
before implementation planning proceeds as independently releasable phases

**Target branch:** `base`

**Upstream policy:** No upstream pull requests or mutations. Literal `main`
remains synchronization-only; upstream Hermes changes flow through the
customization-ledger merge process into `base`.

## Summary

Hermes will expand its portable workflow YAML support toward Archon's current
authoring language without weakening the durability, containment, prompt-cache,
Desktop, or upstream-merge guarantees already built into the workflow harness.

The compatibility posture is **safe Archon compatibility**:

- Match Archon semantics wherever Hermes can enforce them safely.
- Preserve stronger Hermes reliability and security semantics where they differ.
- Diagnose every intentional difference through one authoritative compatibility
  system.
- Never accept a field that is silently ineffective under the Archon profile.
- Preserve all existing unversioned workflow behavior.

The implementation introduces a compatibility-normalization boundary between
raw workflow packages and the existing durable runtime. Existing workflows
resolve to a legacy profile. Newly authored portable workflows declare an
Archon compatibility profile in the existing `.hermes.yaml` companion file.
The normalized semantics are pinned at admission so an upgrade cannot
reinterpret an active or resumed run.

The work is delivered in six independently releasable phases:

1. language foundation;
2. structured data and typed artifacts;
3. semantic compatibility and resilience;
4. ordinary loops and includes;
5. provider portability;
6. durable `loop_group`.

Each phase must leave the full workflow and Desktop harness green. A later phase
may be deferred without leaving parser-accepted, runtime-ineffective YAML behind.

## Grounded baseline

The workflow engine is an additive edge capability under `plugins/workflow/`.
Its durable coordinator, run store, executor fencing, repair containment,
resource limits, authenticated APIs, and Desktop projections are already
production-oriented and extensively tested.

The existing design of record is
`docs/design/portable-workflow-orchestration.md`, amended by the subsequent
workflow coordination, operator robustness, repair-containment, and Desktop-run
designs under `docs/superpowers/specs/`.

Upstream-owned Hermes seams are tracked in
`docs/upstream-customizations/workflow-orchestration.yaml` and validated by:

- `scripts/check_upstream_customizations.py`;
- `scripts/test_workflow_merge_gate.sh`;
- `scripts/test_workflow_upstream_merge.sh`.

The Desktop is an independent Electron and React surface whose backend remains
authoritative for workflow state. It observes and operates workflow runs through
authenticated projections; it does not own or reproduce the execution state
machine.

Archon references reviewed for this design:

- <https://archon.diy/guides/authoring-workflows/>
- <https://archon.diy/guides/loop-nodes/>
- <https://archon.diy/guides/approval-nodes/>
- <https://archon.diy/guides/script-nodes/>
- <https://archon.diy/guides/hooks/>
- <https://archon.diy/guides/mcp-servers/>
- <https://archon.diy/guides/skills/>
- <https://archon.diy/reference/variables/>
- <https://archon.diy/reference/workflow-language-constitution/>

## Goals

1. Support the useful Archon authoring surface without trading away Hermes
   reliability.
2. Preserve current behavior for existing unversioned Hermes workflows.
3. Make structured output an enforceable generation and validation contract.
4. Make `output_type` a real durable artifact contract.
5. Align conditions, timeout units, retry meanings, loops, includes, model
   references, and provider capability handling under an explicit profile.
6. Add `loop.command`, `signal_completes`, load-time `include`, and durable
   `loop_group` support.
7. Preserve prompt-cache stability and strict message-role alternation.
8. Preserve run fencing, bounded retries, reconciliation, repair isolation,
   resource ceilings, and append-only evidence.
9. Keep Desktop operational and read-only for workflow definitions.
10. Keep upstream Hermes synchronization routine through symbol-level
    customization records and tested merge rehearsals.
11. Publish one authoritative schema and compatibility contract to the loader,
    CLI, `doctor`, website documentation, authoring skill, API, and Desktop.

## Non-goals

- No visual YAML editor.
- No Desktop-side YAML parser, schema validator, include expander, model resolver,
  or execution engine.
- No new permanent core model tool.
- No implicit context inheritance between sequential AI nodes.
- No general-purpose expression language, Python evaluation, functions, or
  arbitrary YAML computation.
- No new OS sandbox framework.
- No claim that resource limits are a sandbox.
- No user-facing behavioral `HERMES_*` environment variables.
- No resurrection of the legacy OTTO `steps`, `produces`, `context_from`,
  `verify`, or `iterate` schema.
- No upstream pull request, push, or direct upstream repository mutation.
- No automatic reinterpretation of already-admitted runs after an upgrade.
- No unbounded schema repair, dependency expansion, nested orchestration, or
  artifact publication.

## Terminology

### Hermes companion file

The existing `workflows/<name>.hermes.yaml` file is called the **Hermes
companion file** in user-facing documentation. Internal code may continue to
call it a sidecar.

It is a metadata file, not a process, daemon, worker, container sidecar, or
background service. The existing workflow coordinator remains the only
background workflow service.

The companion file owns Hermes-specific policy such as immutable inputs,
delivery defaults, trust-relevant declarations, overlap policy, execution
environment, limits, required secret names, scheduling policy, and the language
compatibility declaration. It never contains secret values or changes the
portable DAG.

### Language profiles

The initial profiles are:

- `hermes-legacy`: current unversioned Hermes semantics;
- `archon-2026-07`: safe compatibility with the reviewed July 2026 Archon
  authoring contract.

Absence of a language declaration resolves to `hermes-legacy`.

A newly authored portable package declares:

```yaml
language_compatibility: archon-2026-07
```

in its Hermes companion file.

The profile is included in package validation, the digest-bound risk review,
admission evidence, and the immutable run snapshot. Changing it changes the
package digest and follows the normal revalidation and retrust path.

## Approach decision

### Selected: compatibility normalization boundary

The selected architecture is:

```text
portable YAML + Hermes companion file
                |
                v
bounded parse and profile resolution
                |
                v
profile-specific validation and semantic normalization
                |
                v
immutable normalized workflow definition
                |
                v
existing trust, admission, scheduler, store, executors, APIs, and Desktop
```

The scheduler and executors receive normalized internal values rather than raw
profile-dependent YAML.

This keeps compatibility branching out of coordinator election, run-store
repair, retry wakeups, Desktop projections, and other reliability-sensitive
paths.

### Rejected: distributed profile conditionals

Adding profile checks independently to the parser, scheduler, retry code,
resources, every executor, APIs, and Desktop would be initially smaller but
would distribute semantics across the exact surfaces whose invariants must
remain stable. It also makes resumed-run behavior and future upstream merges
harder to prove.

### Rejected: import/transpile-only compatibility

Rewriting Archon definitions into generated Hermes YAML would reduce runtime
changes but lose direct portability, create a second definition for users to
maintain, and allow source and generated semantics to drift.

## Language normalization and durable semantics

### Normalized package contract

After loading, a workflow package carries:

- its original portable definition;
- its effective language profile;
- a normalizer-version identifier;
- an immutable normalized definition;
- structured compatibility findings;
- a semantic fingerprint tied to the package digest;
- bound resource origins for commands, scripts, MCP definitions, skills, and
  included packages.

Normalization runs before trust and admission. It cannot call a model, connect
to MCP, access the network, start a worker, mutate a session, or modify a system
prompt.

### Compatibility findings

Findings have stable codes, severity, source path, effective profile, a concise
message, and migration guidance when applicable.

Example:

```json
{
  "code": "legacy_timeout_seconds",
  "severity": "warning",
  "path": "nodes[2].timeout",
  "effective_profile": "hermes-legacy",
  "message": "This value is interpreted as seconds.",
  "migration": "Declare archon-2026-07 and convert the value to milliseconds."
}
```

Under `archon-2026-07`, unsupported or runtime-ineffective fields are blocking.
Legacy workflows retain current warnings where compatibility requires them.

`doctor`, the catalog API, detail/preflight APIs, CLI, authoring skill, website,
and Desktop consume these findings. They do not reproduce field-specific
compatibility decisions.

### Admission pinning

The admitted run records:

- effective language profile;
- normalizer version;
- normalized semantic fingerprint;
- normalized timeout and retry values;
- resolved provider and model;
- provider capability strategy;
- sealed resource and include bindings.

Resumption uses the admitted snapshot rather than reparsing the currently
installed package.

If a future runtime cannot understand a recorded normalizer version, it fails
closed with a typed compatibility error. It does not guess or silently migrate
the active run.

The implementation should extend the existing durable JSON snapshot before
adding SQLite columns. A database migration is justified only if tests prove
the existing snapshot cannot preserve the required atomicity or query bounds.

### Performance

Parsing and normalization are bounded and cached by source digest plus language
profile. Coordinator sweeps do not repeatedly parse or normalize package YAML.
Catalog list projections do not carry full schemas, expanded definitions, or
artifact bodies.

## Structured output

### Enforceable `output_format`

`output_format` becomes an execution contract rather than post-hoc validation.

At load time:

- validate and normalize the JSON Schema;
- reject external URL references;
- allow bounded local `$defs` references;
- bound schema bytes, nesting depth, property count, regex size, and generated
  output size;
- include the normalized schema in the node cache fingerprint;
- statically reject impossible downstream field access where the schema proves
  it impossible.

At admission, select one strategy through the central provider-capability
resolver:

- native JSON Schema;
- native JSON mode followed by validation;
- prompt-constrained generation followed by validation;
- unsupported.

At execution:

- generate through the resolved strategy;
- parse and validate the result;
- canonicalize valid JSON;
- make canonical JSON the authoritative node output;
- record bounded strategy and validation evidence without reasoning or secrets.

A schema change forces fresh context. The fallback schema instruction is added
to a new user turn or fresh isolated request; it never mutates a live system
prompt.

### Bounded repair

Non-native invalid output receives at most one repair request:

- charged against the existing combined attempt and cost budgets;
- fresh isolated context;
- no tools, hooks, MCP, skills, inline agents, or delegation;
- only the normalized schema and bounded invalid response;
- no replay of the original workflow node;
- no outward action;
- canonical valid JSON or terminal `structured_output_invalid`.

If the original node is classified as outward or its outcome is uncertain, no
automatic repair follows the action. The node pauses or fails through explicit
reconciliation evidence.

Provider retries, workflow retries, and structured repair share one total
attempt budget.

### Generic plugin-agent seam

If the isolated agent contract needs structured-generation parameters, the
change belongs to the generic `PluginAgentRunRequest` boundary. It must not
import workflow modules or expose workflow-specific concepts.

The upstream-owned change receives its own commit, customization-ledger entry,
invariant tests, merge guidance, and removal condition.

## Strict output references and conditions

Structured outputs are retained as typed canonical values rather than being
reparsed independently by conditions and substitution.

For `archon-2026-07`:

- output references must name declared upstream nodes;
- missing outputs and missing fields are typed errors, never empty strings;
- conditions and substitutions use one output resolver;
- ordered comparisons require finite numeric values;
- Archon's documented quoted-number form is accepted for numeric comparison;
- booleans, arrays, objects, non-finite values, and ambiguous coercions are
  rejected.

The condition language supports only:

- `==`, `!=`, `<`, `<=`, `>`, `>=`;
- `&&` with stronger binding than `||`.

It does not support code execution, functions, arbitrary operators, or
parentheses.

Legacy workflows retain current missing-path and substitution behavior until
migrated.

## Typed artifacts

`output_type` becomes a durable publication contract.

For a successful node declaring `output_type`:

- preserve the immutable winning-attempt artifact;
- publish a canonical typed output atomically;
- publish a bounded metadata sidecar atomically;
- record output type, media type, content hash, producer node, winning attempt,
  run ID, language profile, schema fingerprint where applicable, size,
  production time, and session ID when available;
- prevent losing retries from publishing competing canonical artifacts;
- treat typed output and metadata as one cleanup and retention unit.

Publication integrates with node completion so an interruption cannot expose a
corroborated metadata record for missing or losing-attempt content. Recovery may
reconstruct publication only from a corroborated winning attempt.

Cross-run mirroring, when supported, uses content hashes and immutable content.
It never points at a mutable provider session path.

Desktop receives bounded typed-artifact metadata and uses existing safe preview
and download paths. It does not interpret artifact declarations independently.

## Timeout, retry, variable, and session semantics

### Timeout normalization

Under `archon-2026-07`, documented millisecond fields are validated and
normalized once to finite internal seconds. Profile execution ceilings still
cap the result.

Under `hermes-legacy`, existing seconds-based behavior remains unchanged and is
reported by `doctor`.

### Retry normalization

Under `archon-2026-07`, `retry.max_attempts` means retries after the initial
execution. The normalized total is still capped by Hermes's combined
provider/workflow attempt budget.

Deterministic Bash and script nodes do not retry unless explicitly configured.
Loops and loop groups reject `retry`.

Outward, unknown, or uncertain side effects are never automatically replayed.
Cancellation continues to win over due retry wakeups.

### Large Bash values

Small values retain context-aware shell quoting.

Large Archon-profile values:

- spill to a private bounded run file;
- substitute through a context-specific safe reader expression that supplies
  contents rather than an ambiguous pathname;
- receive separate unquoted, double-quoted, and single-quoted regression tests;
- remain inside the run root;
- reject symlink or escape behavior;
- follow the run's cleanup and evidence limits.

Legacy workflows retain current large-value path substitution until migration.

### Missing persistent sessions

A cross-run persisted-session registry entry that points to a missing Hermes
session starts fresh, replaces the stale entry through compare-and-set, and
emits `persistent_session_missing_fresh_start`.

An explicit same-run `context: shared` reference to a missing predecessor
session fails with `context_missing_session`. Hermes does not silently alter the
author's declared same-run semantics.

Cache-fingerprint mismatch continues to force fresh context. No session history
is rewritten, transplanted, or attached to a different profile.

## Ordinary loops

The existing loop executor gains:

- exactly one of `loop.prompt` or `loop.command`;
- `signal_completes`;
- immutable per-run command loading;
- consistent completion and interactive-gate evidence.

Under `archon-2026-07`, `signal_completes` defaults to `false` for interactive
loops. A signal-bearing result pauses for confirmation unless the author opts
into autonomous completion.

At an interactive signal-bearing gate:

- approval without feedback accepts the already-computed result without a new
  iteration;
- approval with feedback discards the signal and starts the next iteration;
- state version and interaction ID are required.

Legacy workflows retain current immediate signal completion.

Completion markers are stripped from downstream output. `max_iterations`
remains a hard failure bound. Cancellation is checked between iterations and
propagated into active child execution. `until_bash` uses the normalized
variable system.

`loop.command` is loaded once from the digest-bound package snapshot and reused
across pauses and iterations. Editing or deleting its installed source cannot
change an admitted run. Hermes intentionally does not execute a mutable
repo/home command resolution result outside the sealed package; `doctor`
explains this safety difference.

## Reusable sub-DAGs with `include`

`include` is a load-time construct only. It does not create a child run, worker,
process, coordinator, nested scheduler, or independent lifecycle.

Expansion rules:

- literal target names only;
- maximum nesting depth of three;
- cycle detection;
- bounded expanded node and edge counts;
- namespaced IDs using `<include-id>__<child-id>`;
- rewired internal dependencies and output references;
- parent dependencies and join semantics attached to entry nodes;
- downstream dependency on the include waits for every sink;
- `$include.output` resolves to the first sink in definition order;
- no `with:` mapping or deep child access in the first implementation;
- no include inside a `loop_group` body.

Only the included portable nodes are inlined. Root workflow defaults and the
root Hermes companion file remain authoritative.

### Immutable include dependencies

An include resolved from another discovered workflow package becomes a sealed
dependency:

- verify its bytes and resources independently;
- preserve the selected catalog source and precedence;
- include every dependency digest in a composite root digest;
- bind inlined resource references to the resource's origin package;
- snapshot the complete resolved dependency closure before admission;
- list included packages and their effective executable risk in the root risk
  summary;
- invalidate future admission when any selected dependency changes.

Already-admitted runs continue from their sealed dependency closure.

This provides normal workflow-name reuse without allowing a trusted root to
execute mutable, newly shadowed, or unreviewed dependency content.

## Durable `loop_group`

`loop_group` is delivered only after the earlier phases pass the full base
gate. It receives a dedicated subordinate implementation specification before
its executable plan because it changes durable nested-state behavior.

From the outer DAG, a group remains one node. Internally, one bounded group
controller runs a sealed body DAG through existing node executors and resource
limits. It does not recursively invoke the top-level scheduler and does not
create a second ownership model.

The initial hardened contract is:

- body dependencies reference body siblings only;
- outer completed-node output remains available through normal variables;
- `$LOOP_PREV.<node>.output` exposes only the immediately previous iteration;
- the first terminal body node in definition order supplies outer output;
- body topological layers may run concurrently within existing run limits;
- body failure fails the group;
- `retry` is rejected on the group;
- includes and nested loop groups are rejected;
- body approvals use existing durable interaction authorization;
- interactive completion uses the ordinary-loop confirmation contract;
- child events, artifacts, attempts, sessions, and interactions are namespaced
  by group, iteration, and body node.

Hermes deliberately provides stronger failure recovery than Archon's current
whole-group restart:

- corroborated completed body nodes are not blindly rerun;
- interrupted body work is classified as replay-safe, uncertain, or outward;
- replay-safe work may resume;
- uncertain and outward work enters reconciliation;
- the outer execution claim retains cancellation, epoch fencing, and cleanup
  authority.

The maximum product of iterations, body nodes, and attempts is checked at
admission against node, worker, artifact, and journal ceilings.

## Provider and model capabilities

### Central capability resolver

One resolver classifies:

- structured output;
- session resumption;
- tool restrictions;
- hooks;
- MCP;
- skills and inline agents;
- effort and thinking controls;
- fallback models;
- web execution;
- cost budgets;
- provider-native sandboxing.

Each accepted field resolves to:

- `native`;
- `hermes_adapter`;
- `degraded_with_explicit_semantics`;
- `unsupported`.

Unsupported fields block under `archon-2026-07`. Nothing is silently accepted
or audit-only. Explicit providers are checked during `doctor`; implicit/default
providers are rechecked at admission and execution.

### Portable model references

`model` accepts:

- `small`, `medium`, and `large` tiers;
- `@alias` configured aliases;
- literal model IDs.

Tiers and aliases live in `config.yaml`, not `.env`.

Resolution produces and pins a concrete provider, model, and supported provider
options. Literal model IDs pass through without a static allow-list. A tier or
alias whose provider conflicts with an explicit workflow provider wins with a
visible warning. Project-specific aliases receive a portability diagnostic when
used by global or distributed packages.

Desktop displays resolved values but never resolves them.

### Tools, skills, MCP, hooks, and inline agents

- `allowed_tools: []` exposes no built-in tools.
- Archon aliases normalize before cache fingerprinting.
- Unknown aliases block admission.
- Skills are fully read, snapshotted, and added to the new user turn.
- Skills never mutate the system prompt.
- MCP definitions and executable resources are package-contained,
  digest-bound, isolated, and bounded during startup and teardown.
- Documented MCP wrapper shapes normalize into one internal representation.
- Secret values remain outside packages.
- Hook events are classified individually; unsupported events block rather
  than disappearing.
- Inline agents inherit the parent's remaining attempts, cost and resource
  limits, workdir containment, and cancellation.
- Unrestricted raw delegation remains unavailable.

Any provider, model, schema, tools, skills, MCP, hooks, agents, system prompt, or
other cache-fingerprint change forces fresh context.

### `maxBudgetUsd`

The field is enforceable or unsupported, never advisory.

- Providers with authoritative usage and pricing receive a hard node budget.
- Estimated-only providers cannot claim hard enforcement.
- Unsupported providers produce a blocking `doctor` finding.
- Child agents and structured repair share the same budget.
- Retries cannot reset it.
- Budget exhaustion is terminal and not retried.
- Cost evidence is bounded and excludes credentials.

Any generic plugin-agent budget seam is isolated, ledgered, and tested.

### `sandbox`

Provider-native sandbox configuration is accepted only when the provider
capability guarantees enforcement.

Otherwise the field blocks under `archon-2026-07`, and `doctor` recommends the
existing `execution_environment: isolated_backend_required` companion policy
where appropriate.

The work does not build a new OS sandbox and never describes resource limits as
one.

## Desktop operational experience

Desktop remains an operational, read-only surface for workflow definitions.

### Additive projections

Server responses may add:

- effective language profile;
- legacy-semantics status;
- compatibility finding codes;
- resolved capability summary;
- structured-output strategy and validation state;
- typed-artifact metadata;
- loop and group interaction state;
- normalizer version and semantic fingerprint where operationally useful.

List responses omit full schemas, expanded definitions, artifact bodies, and
detailed findings. Bounded detail and evidence requests load them on demand.

Older runs without language metadata project as legacy/unknown and remain
operable.

### Desktop responsibilities

Existing Desktop surfaces continue to own:

- catalog and workflow detail;
- preflight and trust review;
- immediate and one-shot scheduled admission;
- run status and history;
- attention;
- approve, reject, cancel, resume, reconcile, and cleanup;
- evidence and artifact inspection.

They may present:

- Archon-compatible or legacy status;
- server-authored incompatibility guidance;
- resolved provider/model and capability limits;
- structured-output validated, repaired, or failed status;
- typed-artifact metadata and safe preview/download;
- interactive loop accept-versus-feedback actions;
- namespaced group progress.

Desktop does not parse YAML, expand includes, resolve aliases, validate schemas,
infer mutation safety, create retry decisions, or edit definitions.

### Version skew and failure behavior

- Missing capability hides or disables the new affordance with honest copy.
- Transient failure preserves cached data and offers bounded retry.
- Older backends retain current workflow operations.
- Unknown future finding codes use safe server-authored fallback text.
- Stale mutation responses trigger authoritative refetch.
- A compatibility fallback never retargets a write to another profile,
  connection, source, or workflow.

### Desktop performance invariants

- Catalog normalization caches by source digest and profile.
- Coordinator sweeps do not render Desktop projections.
- List responses remain bounded.
- Detail and evidence remain paginated or byte-bounded.
- Cosmetic progress coalesces.
- Terminal and attention transitions flush immediately.
- Background runs never navigate, move focus, open dialogs, or replace
  foreground state.
- Activity-board row identity and reference-stability optimizations remain.
- Performance fixtures include long expanded workflows and repeated group
  iterations.

## Workflow harness invariants

The compatibility program must preserve:

- prompt caching and byte-stable system prompts;
- strict message-role alternation;
- claim and execution-epoch fencing;
- cancellation winning over retry wakeups;
- lane ownership and overlap policy;
- coordinator election and bounded sweeps;
- run-scoped repair containment;
- append-only evidence;
- notification recovery;
- cleanup confirmation binding;
- machine, process-tree, resource, output, artifact, and descendant limits;
- authenticated admission and operator scope;
- one lifecycle authority per run;
- no renderer-owned execution state.

No phase may bypass these invariants to achieve syntax compatibility.

## Upstream merge policy

### Branch flow

```text
upstream Hermes
      |
      v
literal main          synchronization only
      |
      v
base                  development main plus customizations
      |
      +----> otto      only from an exact tested base commit
      |
      +----> loop24    only from an exact tested base commit
```

Feature work starts from `base`, may use a focused feature branch, and targets
`base`. Literal `main` never receives feature development, release work, or
workflow commits.

### Upstream-owned touch rules

Most work remains additive under the workflow plugin, skills, documentation,
and tests.

Every necessary upstream-owned change:

- exposes a generic capability;
- uses an independent commit boundary;
- adds or amends its ledger entry in the same commit;
- records exact owned symbols/contracts, rationale, invariant tests, expected
  commit boundary, merge guidance, last verified upstream commit,
  upstreamability, and removal condition;
- adds regression coverage for the pre-existing seam;
- passes missing-ledger coverage checks.

`last_verified_upstream` advances only through the controlled upstream merge,
not during feature development.

### Future synchronization procedure

1. Synchronize literal `main` with upstream Hermes.
2. Compare the ledger baseline with the new upstream commit.
3. Produce a machine-readable overlap report.
4. Require `preserve`, `adapt`, or `remove-as-upstream-equivalent` for every
   owned-symbol or possible-equivalent overlap.
5. Rehearse the merge in a detached temporary base worktree.
6. Forbid whole-file `ours` or `theirs` resolution for ledger-owned files.
7. Run entry-specific invariants and the complete base workflow gate.
8. Produce machine-readable merge evidence.
9. Merge into real `base` only after rehearsal succeeds.
10. Propagate the exact tested base commit to branded branches and regenerate
    only authorized brand overlays.

No upstream PR, push, or repository mutation is part of this process.

## Verification strategy

Every phase follows TDD and lands independently.

The verification ladder is:

1. schema and profile-resolution unit tests;
2. legacy-versus-Archon normalization contract tests;
3. minimal compatibility fixtures derived from current official examples;
4. executor and variable-resolution tests;
5. real SQLite, store, and journal integration tests;
6. failure injection at claim, attempt, artifact, pause, resume, and completion
   boundaries;
7. authenticated API and middleware tests;
8. Desktop adapter, component, operation, and performance tests;
9. installed-distribution tests with a temporary `HERMES_HOME`;
10. supported-platform portability tests;
11. customization-ledger validation;
12. complete base merge gate;
13. temporary upstream and brand merge rehearsal when generic core seams change.

Required scenarios include:

- existing YAML on the new runtime;
- existing admitted runs resumed after upgrade;
- Archon-profile YAML using new semantics;
- new backend with an older Desktop;
- new Desktop with an older backend;
- provider capability change between discovery and admission;
- missing persistent sessions;
- invalid, malicious, and oversized schemas and outputs;
- repair and cost-budget exhaustion;
- cancellation during repair or nested execution;
- crash during typed-artifact publication;
- include dependency changes, cycles, shadowing, and expansion limits;
- stale interactive-loop approval;
- group crash during parallel body execution;
- uncertain or outward body-node reconciliation;
- corrupt evidence remaining isolated to one run;
- lock contention not stopping coordinator progress.

Performance assertions favor bounded work and invariants over brittle timing
snapshots.

## Delivery phases

### Phase 1: language foundation

- companion-file language profile;
- normalization boundary and durable profile pinning;
- structured compatibility findings;
- truthful unsupported-field handling;
- generated machine-readable schema;
- website and workflow-builder authoring reference;
- legacy behavior diagnostics;
- removal or replacement guidance for incompatible external authoring skills.

### Phase 2: structured data

- provider structured-output capability contract;
- native and adapted `output_format`;
- bounded repair;
- canonical typed output references;
- `output_type` artifact publication;
- Desktop operational evidence.

### Phase 3: semantic compatibility and resilience

- timeout and retry normalization;
- typed condition evaluation;
- strict output references;
- safe large Bash substitution;
- missing persistent-session recovery.

### Phase 4: ordinary loops and includes

- `loop.command`;
- `signal_completes`;
- interactive finalization semantics;
- immutable composite include resolution and expansion.

### Phase 5: provider portability

- model tiers and aliases;
- hook and MCP shape normalization;
- enforceable cost budgets;
- truthful sandbox capability handling;
- final provider capability matrix.

### Phase 6: durable `loop_group`

- subordinate detailed design and plan;
- sealed body DAG;
- namespaced durable state and evidence;
- bounded body parallelism;
- pause, resume, cancellation, and reconciliation;
- Desktop operational projection.

No phase begins implementation until its predecessor's full base gate is green.

## Documentation and authoring UX

One schema authority should generate or drive:

- `hermes workflow schema --json`;
- complete website YAML reference;
- workflow-builder reference material;
- editor and validation tooling;
- compatibility codes and migration guidance.

The first-party workflow-builder continues to author complete, digest-bound
packages rather than isolated YAML. It uses the Archon profile for new portable
packages and runs `doctor` before offering execution.

The globally installed legacy `create-workflow` skill that emits OTTO V1
`steps` definitions is incompatible with this runtime and must not be presented
as a Hermes authoring authority.

## Acceptance criteria

The umbrella program is complete when:

1. Existing unversioned workflows and admitted runs preserve their current
   semantics.
2. `doctor` identifies legacy behavior and gives explicit migration guidance.
3. Archon-profile YAML is normalized once and pinned durably.
4. Every Archon-profile accepted field has enforceable runtime behavior.
5. `output_format` uses native enforcement or one bounded isolated repair.
6. `output_type` publishes recoverable typed artifacts.
7. Conditions and structured references are typed and strict.
8. Timeout and retry meanings match the declared profile without multiplying
   attempts.
9. Missing cross-run sessions recover fresh with visible evidence.
10. Ordinary loops support command resources and explicit signal-completion
    policy.
11. Includes expand immutably with composite digest and trust coverage.
12. `loop_group` preserves durable ownership, reconciliation, and boundedness.
13. Provider budgets and sandbox fields are enforced or blocked.
14. Desktop remains operational, backward-compatible, bounded, and free of
    duplicated workflow logic.
15. Existing workflow resilience, repair, performance, and Desktop gates remain
    green.
16. Every upstream-owned touch is generic, ledgered, invariant-tested, and
    merge-rehearsed.
17. No upstream PR or upstream repository mutation occurs.
