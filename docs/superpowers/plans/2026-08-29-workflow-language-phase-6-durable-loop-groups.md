# Workflow Language Phase 6: Durable Loop Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add durable bounded `loop_group` execution to the pinned `archon-2026-07` workflow language, migrate Jira Defect Loop as its representative consumer, and preserve shared-worker fairness, exact recovery, and profile isolation.

**Architecture:** Normalizer v6 seals a one-level nested body as immutable `WorkflowNode` values. The outer group is a durable controller stored inside the existing authenticated run projection; ready body children become scoped work items in the existing `RunScheduler.advance_all()` pool and use namespaced rows in the existing `worker_claims` table. Existing executors, provider authority, resource sealing, interactions, recovery, evidence, and profile-scoped API routes remain authoritative.

**Tech Stack:** Python 3.11, frozen dataclasses, SQLite and authenticated JSONL run journals, existing workflow executors and scheduler, FastAPI/Pydantic, React 19/TypeScript/nanostores/TanStack Query, Vitest, pytest through `scripts/run_tests.sh`, YAML workflow packages.

**Spec:** `docs/superpowers/specs/2026-08-29-workflow-language-phase-6-durable-loop-groups-design.md`

## Global Constraints

- Work from `base`; literal `main` is synchronization-only. Verify `git branch --show-current` before the first edit and after the final gate.
- Preserve all unrelated user changes and untracked files. Stage only files named by the active task.
- Use `scripts/run_tests.sh` for Python tests. Use the existing `ui-tui`/Desktop package commands for TypeScript; do not invoke ad hoc test runners.
- Follow RED/GREEN TDD for each task. Confirm each RED fails for the intended missing behavior before changing production code.
- Keep `CURRENT_NORMALIZER_BY_PROFILE[ARCHON_2026_07]` at `5` until Task 9. Versions 1 through 5 must replay through their recorded normalizers without semantic drift.
- Keep snapshot format `2`. Do not add a database migration, new table, nested scheduler, nested worker pool, new core model tool, or cross-profile workflow registry.
- Reuse the existing `worker_claims`, run lock, execution fence, fair cursor, executor instances, provider authority, resource manifests, interaction methods, process lifecycle, evidence routes, and profile routing.
- The outer controller consumes no worker while waiting. Body claims count against the same profile-global and per-run worker limits as top-level nodes.
- Reject includes, nested `loop_group`, runtime `workflow`, group-level `retry`, invalid cross-scope references, and unbounded products before run creation or connector/provider work.
- Treat controller generation, iteration, body node, attempt, output, approval, artifact, and process identity as authenticated scope. Never use caller- or model-supplied values as filesystem paths or worker keys.
- Preserve byte-compatible top-level attempt and artifact paths. Child paths must remain contained below the approved group/iteration roots.
- Expected Jira outcomes are successful structured results. Ambiguous outward writes, integrity loss, or uncertain post-crash effects stop for existing attention/reconciliation handling.
- Every Desktop REST call, socket stream, query key, cache mutation, and action remains selected-profile scoped. A run ID from another profile must be indistinguishable from missing.
- Each task ends with its own commit and a spec-coverage/code-quality review before the next task begins.

## File and Interface Map

- `plugins/workflow/language_schema.py`: dependency-neutral v6 field inventory, node descriptors, generated authoring schemas, and durable diagnostic codes.
- `plugins/workflow/schema.py`: source parsing, nested body validation, scoped reference validation, graph validation, and v6 snapshot reload checks.
- `plugins/workflow/language.py`: dormant normalizer v6, semantic identity, normalized digest, and final activation switch.
- `plugins/workflow/models.py`: `LoopGroupChildScope` and immutable normalized nested-node representation; no parallel node class hierarchy.
- `plugins/workflow/topology.py`: stable layers, terminal-node selection, and one shared scoped-node traversal used by admission and runtime.
- `plugins/workflow/dependency_manifest.py`, `resources.py`, `provider_authority.py`, `runner_binding.py`, `trust.py`: include nested body obligations in existing sealing and authority contracts.
- `plugins/workflow/executors/base.py` plus existing executors: explicit effective attempt/publication directories with unchanged top-level defaults.
- `plugins/workflow/store.py`: nested controller state transitions, namespaced child claims, stale-claim rebuilding, cancellation, and reconciliation using existing tables and journal.
- `plugins/workflow/scheduler.py`: one `SchedulerWorkItem` path for top-level and child candidates, durable controller advancement, existing-executor dispatch, and iteration completion.
- `plugins/workflow/resources.py`, `bash_rendering.py`, `output_resolution.py`: strict current-body, approved outer dependency, and `$LOOP_PREV` resolution.
- `plugins/workflow/sanitize.py` and `dashboard/plugin_api.py`: bounded public `loop_group` node summary on existing run routes.
- `apps/desktop/src/types/hermes.ts`, `lib/workflow-public-codec.ts`, `app/workflows/adapter.ts`, and `app/workflows/run-inspector.tsx`: strict decoding, compact card progress, and bounded inspector summaries.
- `capabilities/workflow-packages/ericsson/`, `capabilities/workflows/`, `capabilities/ericsson.json`, and onboarding references: distributed Jira Defect Loop workflow and authenticated digest metadata.

---

### Task 1: Add dormant normalizer v6 and the bounded authoring contract

**Files:**
- Modify: `plugins/workflow/language_schema.py`
- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/topology.py`
- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/language.py`
- Modify: `skills/software-development/workflow-builder/references/portable-schema.md`
- Modify: `skills/software-development/workflow-builder/references/authoring-checklist.md`
- Create: `tests/plugins/workflow/test_phase6_language.py`
- Modify: `tests/plugins/workflow/test_language_schema.py`
- Modify: `tests/plugins/workflow/test_phase5_language.py`
- Modify: `tests/plugins/workflow/test_doctor.py`
- Modify: `tests/plugins/workflow/test_catalog_cli.py`

**Interfaces:**
- Produces: `supports_phase6_semantics(profile, normalizer_version) -> bool`.
- Produces: `LoopGroupChildScope(run_id, group_id, controller_generation, iteration, node_id)` with deterministic internal identity but no path parsing.
- Produces: `iter_scoped_workflow_nodes(definition) -> Iterator[ScopedWorkflowNode]`, `stable_node_layers(nodes)`, and `primary_terminal_node(nodes)` in `topology.py`.
- Produces: normalizer v6 nested `value["nodes"]` as `tuple[WorkflowNode, ...]` and scoped semantic keys `group_id/body_id`.
- Keeps: current Archon admissions on v5 until Task 9; tests exercise v6 through `load_workflow_snapshot(..., normalizer_version=6)`.

- [ ] **Step 1: Add RED schema and normalizer tests**

Write a minimal valid group fixture and assert its exact defaults and immutable body:

```python
def test_v6_normalizes_one_bounded_loop_group(tmp_path, workflow_writer):
    path = workflow_writer(
        tmp_path,
        name="bounded-group",
        language_compatibility="archon-2026-07",
        nodes=[{
            "id": "process-items",
            "loop_group": {
                "until": "<promise>DONE</promise>",
                "max_iterations": 25,
                "nodes": [
                    {"id": "read", "command": "read-item"},
                    {"id": "record", "depends_on": ["read"], "command": "record-item"},
                ],
            },
        }],
    )
    package = load_workflow_snapshot(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=path.with_name("bounded-group.hermes.yaml").read_bytes(),
        normalizer_version=6,
    )
    group = package.definition.nodes[0]
    assert group.node_type == "loop_group"
    assert tuple(node.id for node in group.value["nodes"]) == ("read", "record")
    assert group.value["fresh_context"] is False
    assert primary_terminal_node(group.value["nodes"]).id == "record"
```

Add table-driven failures for: v5 admission of `loop_group`, empty body, more than 512 body nodes, more than 4096 body edges, invalid/missing `until`, `max_iterations` outside 1..100, unknown group fields (including an unsupported `returns` selector), include, nested group, runtime workflow, group-level retry, body cycles, duplicate body IDs, body dependency outside the sibling body, and more than one node-type field. Assert stable Phase 6 codes and authored paths such as `nodes[0].loop_group.nodes[1].depends_on`.

Add version regressions asserting supported versions become `{1,2,3,4,5,6}` while current Archon remains `5`, and that identical v1-v5 fixtures retain their existing normalized digests.

- [ ] **Step 2: Run the RED tests**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_language.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_doctor.py tests/plugins/workflow/test_catalog_cli.py tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_phase5_language.py -v
```

Expected: v6 is unsupported and `loop_group` is absent from the field/node contract; all pre-v6 assertions still pass.

- [ ] **Step 3: Add only the v6 schema surface**

In `language_schema.py`, add `loop_group` to executable node kinds only for Phase 6, define its exact fields, and keep retry structurally invalid on the group:

```python
LOOP_GROUP_FIELDS = frozenset({
    "nodes", "until", "max_iterations", "fresh_context", "until_bash",
    "interactive", "signal_completes", "gate_message",
})

def loop_group_field_names() -> frozenset[str]:
    return LOOP_GROUP_FIELDS
```

Register bounded diagnostic codes for invalid group shape/topology/scope/product. Replace the Phase 4 compatibility guidance that lists `loop_group` as a future feature with the Phase 6 contract. Derive JSON schema, field inventory, editor guidance, code catalog, and compatibility output from the same inventory; do not add a hand-maintained second schema.

Update the workflow-builder portable schema and checklist with the exact one-level body shape, bounds, primary-sink rule, reference scopes, interactivity fields, and rejected constructs. Extend schema CLI/doctor/catalog tests so every surface derives the same v6 fields and stable diagnostic codes.

- [ ] **Step 4: Normalize nested nodes and share topology traversal**

Add the minimal cross-store/scheduler identity in `models.py`:

```python
@dataclass(frozen=True, slots=True)
class LoopGroupChildScope:
    run_id: str
    group_id: str
    controller_generation: int
    iteration: int
    node_id: str

    @property
    def worker_node_id(self) -> str:
        return (
            f"loop-group/{self.run_id}/{self.group_id}/{self.controller_generation}/"
            f"{self.iteration:04d}/{self.node_id}"
        )
```

The constructor validates the store-owned run ID, already-normalized portable node IDs, and positive generation/iteration. The string is internal-only and is never split to recover identity.

In `topology.py`, generalize the existing stable-layer algorithm to accept any tuple of `WorkflowNode`; keep `project_topology()` calling it for the outer graph. Add one iterator that yields every outer node and every body node with its group ID, semantic ID (`group/body`), authored schema path, and group options. No recursion beyond the approved one body level; reject nested groups in schema instead of engineering recursive runtime types.

In `schema.py`, parse body entries through the existing `_source_node()` and `_normalize_node()` machinery with an explicit nested path. Reject compile directives and runtime workflow references there. Validate body graph locally; choose the first terminal body node in definition order. Validate:

- body references target a sibling dependency;
- an outer reference targets a direct dependency of the group;
- `$LOOP_PREV.<body>.output` names a known body node;
- whole-output previous references are allowed on iteration one and strict field references retain declared schema checks; and
- group/body defaults are frozen and body explicit options remain distinguishable.

In `language.py`, add `_normalize_v6()` after v5, `supports_phase6_semantics()`, and version 6 support without changing the current-profile mapping. Key body structured outputs and node semantics by scoped semantic ID so two groups may reuse the same body IDs safely. Derive the outer group's structured-output contract from the selected primary sink so downstream `$group.output.<field>` validation uses the same canonical schema/fingerprint; reject a missing or contradictory promoted contract on snapshot reload.

- [ ] **Step 5: Run schema and historical replay suites GREEN**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_language.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_doctor.py tests/plugins/workflow/test_catalog_cli.py tests/plugins/workflow/test_language_snapshot.py tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_phase5_language.py tests/plugins/workflow/test_phase5_provider_snapshot.py -v
```

Expected: explicit v6 snapshots normalize groups; new admissions still select v5; recorded v1-v5 snapshots and digests are unchanged.

- [ ] **Step 6: Commit Task 1**

```bash
git add plugins/workflow/language_schema.py plugins/workflow/models.py plugins/workflow/topology.py plugins/workflow/schema.py plugins/workflow/language.py skills/software-development/workflow-builder/references/portable-schema.md skills/software-development/workflow-builder/references/authoring-checklist.md tests/plugins/workflow/test_phase6_language.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_phase5_language.py tests/plugins/workflow/test_doctor.py tests/plugins/workflow/test_catalog_cli.py
git commit -m "feat(workflow): define durable loop groups"
```

---

### Task 2: Seal nested admission, authority, resources, and worst-case bounds

**Files:**
- Modify: `plugins/workflow/compilation.py`
- Modify: `plugins/workflow/dependency_manifest.py`
- Modify: `plugins/workflow/resources.py`
- Modify: `plugins/workflow/provider_authority.py`
- Modify: `plugins/workflow/runner_binding.py`
- Modify: `plugins/workflow/trust.py`
- Modify: `plugins/workflow/language.py`
- Modify: `plugins/workflow/schema.py`
- Create: `tests/plugins/workflow/test_phase6_admission.py`
- Modify: `tests/plugins/workflow/test_phase5_provider_authority.py`
- Modify: `tests/plugins/workflow/test_phase5_admission_parity.py`
- Modify: `tests/plugins/workflow/test_phase4_dependency_manifest.py`

**Interfaces:**
- Consumes: `iter_scoped_workflow_nodes()` from Task 1 instead of adding sibling traversal loops.
- Produces: scoped provider/resource/structured-output keys using the exact `group/body` semantic ID.
- Produces: deterministic v6 work-bound metadata in the existing normalized definition/manifest.
- Keeps: the companion/root trust and config scope authoritative; body nodes cannot add a companion or trust scope.

- [ ] **Step 1: Add RED recursive-sealing tests**

Add one mixed group fixture whose body contains prompt, command, bash, script, approval, and ordinary loop nodes. Assert its nested obligations appear in:

- workflow risk and required services/connectors;
- command/script/skill/MCP/hook resource discovery;
- provider/model/fallback/options authority with group defaults inherited and body explicit values winning;
- timeout/retry/budget/process bounds;
- structured output and reference declarations; and
- dependency manifest and normalized definition digests.

Tamper one nested script byte, scoped provider route, primary-sink identity, structured-output fingerprint, and body topology in authenticated snapshot material. Each reload must fail closed instead of rediscovering the installed package.

Add a pre-admission spy test proving an excessive product invokes neither connector capability probing nor provider resolution:

```python
def test_v6_rejects_work_product_before_external_resolution(...):
    probes = []
    with pytest.raises(WorkflowValidationError, match="loop_group.*work bound"):
        admit_v6_fixture(
            max_iterations=100,
            body_nodes=fixture_nodes_with_retries_and_ordinary_loop(),
            connector_probe=lambda *_: probes.append("connector"),
            provider_resolver=lambda *_: probes.append("provider"),
        )
    assert probes == []
```

- [ ] **Step 2: Run the RED tests**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_admission.py tests/plugins/workflow/test_phase5_provider_authority.py tests/plugins/workflow/test_phase5_admission_parity.py tests/plugins/workflow/test_phase4_dependency_manifest.py -v
```

Expected: nested nodes are missing from one or more sealed contracts and excessive products are not rejected at the required boundary.

- [ ] **Step 3: Route all admission walkers through the shared scoped iterator**

Replace only outer-node loops that calculate executable obligations with `iter_scoped_workflow_nodes()`. Keep outer graph operations outer-only. For a body node:

```python
semantic_id = scoped.semantic_id       # "group/body"
effective_options = {
    **definition.options,
    **scoped.group_options,
    **scoped.node.options,
}
```

Use `semantic_id` for provider routes, structured outputs, hook/resource identities, and manifest entries. Continue using the authored body ID inside body dependency/reference validation. Do not mutate body nodes to bake root defaults into their explicit options.

Bind command/script/resource bytes from the existing authenticated run root and include their logical origin in the same dependency manifest. Preserve existing Phase 5 authority digest calculation; extend its input set instead of adding a second authority document.

- [ ] **Step 4: Add deterministic worst-case accounting before admission side effects**

Compute with checked integer multiplication:

```python
child_executions = max_iterations * len(body_nodes)
child_attempts = max_iterations * sum(max_attempts(node) for node in body_nodes)
```

For an ordinary loop body node, multiply its own maximum iterations and attempts before summing. Compare the products with existing definition node/edge, retry/iteration, artifact, run-byte, and journal-reserve ceilings. Report the group ID, calculated product, and exceeded ceiling without prompt/resource content. Do not add a Phase 6 config knob.

Persist the calculated bounds and primary sink in the existing normalized semantic/manifest material so v6 snapshot reload verifies identical values. Keep snapshot format 2 and reject missing/contradictory v6 fields.

- [ ] **Step 5: Run focused admission, trust, and snapshot suites GREEN**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_admission.py tests/plugins/workflow/test_phase5_provider_authority.py tests/plugins/workflow/test_phase5_admission_parity.py tests/plugins/workflow/test_phase5_provider_snapshot.py tests/plugins/workflow/test_phase4_dependency_manifest.py tests/plugins/workflow/test_trust_policy.py tests/plugins/workflow/test_language_snapshot.py -v
```

Expected: every nested executable obligation is sealed once, products fail before external work, and v1-v5 fixtures remain unchanged.

- [ ] **Step 6: Commit Task 2**

```bash
git add plugins/workflow/compilation.py plugins/workflow/dependency_manifest.py plugins/workflow/resources.py plugins/workflow/provider_authority.py plugins/workflow/runner_binding.py plugins/workflow/trust.py plugins/workflow/language.py plugins/workflow/schema.py tests/plugins/workflow/test_phase6_admission.py tests/plugins/workflow/test_phase5_provider_authority.py tests/plugins/workflow/test_phase5_admission_parity.py tests/plugins/workflow/test_phase4_dependency_manifest.py
git commit -m "feat(workflow): seal loop group admission"
```

---

### Task 3: Give every executor explicit contained output directories

**Files:**
- Modify: `plugins/workflow/executors/base.py`
- Modify: `plugins/workflow/executors/ai.py`
- Modify: `plugins/workflow/executors/approval.py`
- Modify: `plugins/workflow/executors/bash.py`
- Modify: `plugins/workflow/executors/script.py`
- Modify: `plugins/workflow/executors/loop.py`
- Create: `tests/plugins/workflow/test_phase6_execution_context.py`
- Modify: `tests/plugins/workflow/test_bash_e2e.py`
- Modify: `tests/plugins/workflow/test_script_executor.py`
- Modify: `tests/plugins/workflow/test_ai_executor.py`
- Modify: `tests/plugins/workflow/test_approval.py`
- Modify: `tests/plugins/workflow/test_loop_executor.py`

**Interfaces:**
- Produces: optional `attempt_directory` and `publication_directory` inputs on `NodeExecutionContext`.
- Produces: `effective_attempt_directory` and `effective_publication_directory` properties with exact top-level defaults.
- Consumed later: Task 5 supplies group/iteration-specific directories for child execution.

- [ ] **Step 1: Add RED path-contract tests**

Assert an unchanged top-level context resolves exactly as today and an explicit child context is honored by every executor:

```python
def test_context_preserves_top_level_paths_and_accepts_scoped_paths(tmp_path):
    base = context(tmp_path, node_id="n", attempt_id="a")
    assert base.effective_attempt_directory == tmp_path / "nodes" / "n" / "a"
    assert base.effective_publication_directory == tmp_path / "artifacts"

    child = replace(
        base,
        attempt_directory=tmp_path / "nodes/g/1/iterations/0001/nodes/n/a",
        publication_directory=tmp_path / "artifacts/loop-groups/g/iterations/0001/n",
    )
    assert child.effective_attempt_directory == child.attempt_directory
    assert child.effective_publication_directory == child.publication_directory
```

Use existing executor fakes to assert files/artifact descriptors are written below the explicit directories. Add traversal/symlink containment cases at the boundary that constructs the paths; executors receive already-validated `Path` values and do not parse group IDs.

- [ ] **Step 2: Run the RED tests**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_execution_context.py tests/plugins/workflow/test_bash_e2e.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_loop_executor.py -v
```

Expected: explicit context fields/properties do not exist and executors still hardcode top-level paths.

- [ ] **Step 3: Add two optional context paths and remove executor hardcoding**

Add to `NodeExecutionContext`:

```python
attempt_directory: Path | None = None
publication_directory: Path | None = None

@property
def effective_attempt_directory(self) -> Path:
    return self.attempt_directory or (
        self.run_directory / "nodes" / self.node.id / self.attempt_id
    )

@property
def effective_publication_directory(self) -> Path:
    return self.publication_directory or self.run_directory / "artifacts"
```

Replace executor-local `run_directory / "nodes" ...` and `run_directory / "artifacts"` construction with those properties. Keep resource reads rooted at `run_directory`. Do not create a directory service, path factory, or new executor interface.

- [ ] **Step 4: Run executor and top-level byte-compatibility suites GREEN**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_execution_context.py tests/plugins/workflow/test_bash_e2e.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_loop_executor.py tests/plugins/workflow/test_typed_publication.py -v
```

Expected: child overrides are honored and all existing top-level descriptor/path assertions remain byte-for-byte unchanged.

- [ ] **Step 5: Commit Task 3**

```bash
git add plugins/workflow/executors/base.py plugins/workflow/executors/ai.py plugins/workflow/executors/approval.py plugins/workflow/executors/bash.py plugins/workflow/executors/script.py plugins/workflow/executors/loop.py tests/plugins/workflow/test_phase6_execution_context.py tests/plugins/workflow/test_bash_e2e.py tests/plugins/workflow/test_script_executor.py tests/plugins/workflow/test_ai_executor.py tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_loop_executor.py
git commit -m "refactor(workflow): scope executor output paths"
```

---
### Task 4: Persist controller state and scoped child claims without a migration

**Files:**
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/models.py`
- Create: `tests/plugins/workflow/test_phase6_store.py`
- Modify: `tests/plugins/workflow/test_store.py`
- Modify: `tests/plugins/workflow/test_crash_recovery.py`
- Modify: `tests/plugins/workflow/test_idempotency_multiprocess.py`

**Interfaces:**
- Produces: `initialize_loop_group()`, `claim_loop_group_child()`, `complete_loop_group_child()`, `record_loop_group_iteration()`, and `fail_loop_group()` on `RunStore`.
- Produces: private store helpers that locate a child only from a validated `LoopGroupChildScope`; API callers never supply worker keys.
- Reuses: existing `NodeClaim`, `worker_claims`, run mutation lock, state version, journal chain, execution fence, retry/interaction/attempt shapes, and worker ceilings.

- [ ] **Step 1: Add RED durable-state and capacity tests**

Create a real `RunStore` in a temporary profile and cover these exact transitions:

1. ready outer group initializes generation 1, iteration 1, body pending/ready states, and primary sink once;
2. a ready child claim inserts one existing-table `worker_claims` row with the namespaced internal node key;
3. a child claim increments both profile-global and per-run capacity exactly like a top-level claim;
4. a second claim for the same scope is rejected atomically across two store instances;
5. child completion records the existing attempt/output/artifact shapes under the group state;
6. iteration commit carries only authenticated output descriptors into `previous_outputs` and resets body state for N+1;
7. stale generation/iteration/claim/state-version transitions cannot mutate current state; and
8. the SQLite schema/table/index inventory is unchanged.

Include `max_total_workers=1`: a group controller initializes without a `worker_claims` row, and its one child can claim the only worker.

- [ ] **Step 2: Add RED restart and corruption tests**

Close and reopen the store after child claim, process start, child output publication, child completion, and iteration commit. Assert `_reconcile_worker_claims()` rebuilds only corroborated active child rows. Tamper nested attempt identity, output digest, controller generation, iteration, and worker key; initialization must surface journal/projection integrity failure rather than flattening or silently dropping child state.

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_store.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_idempotency_multiprocess.py -v
```

Expected: group state methods are absent and reconciliation only walks top-level nodes.

- [ ] **Step 3: Store the controller under the existing outer node projection**

Use one versioned nested mapping under the group node, preserving existing outer-node state fields:

```python
"loop_group": {
    "schema_version": 1,
    "controller_generation": 1,
    "iteration": 1,
    "max_iterations": 25,
    "state": "running",
    "primary_sink": "record-item",
    "previous_outputs": {},
    "body": {
        "select-item": existing_node_state_shape(),
        "record-item": existing_node_state_shape(),
    },
}
```

Add a single private locator:

```python
def _loop_group_child_state(
    projection: Mapping[str, object], scope: LoopGroupChildScope
) -> MutableMapping[str, object]:
    ...
```

It validates outer group ID, controller schema/generation, current iteration, and body ID before returning state. Every child transition receives both `LoopGroupChildScope` and the current execution fence/claim, then uses the existing locked mutation/journal helpers. Do not introduce a generic state repository or copy the complete top-level transition engine.

- [ ] **Step 4: Reuse the existing claim table and limits**

`claim_loop_group_child()` must perform the same owner/fence, desired-status, lease, total-worker, per-run-worker, retry, and state checks as `claim_node()`, then insert:

```python
(attempt_id, run_id, scope.worker_node_id, owner_id, lease_expires_at)
```

Store full scope fields in the authenticated nested attempt state; never recover scope by parsing `worker_node_id`. Factor only the common capacity query/insert transaction shared by top-level and child claims. Existing top-level behavior and SQL remain unchanged.

Extend `_reconcile_worker_claims()`, stale expiry, heartbeat renewal, active-process lookup, cancellation, cleanup, and run deletion to walk authenticated nested child attempts. Rebuild rows from nested state and discard orphan rows only after journal/projection verification.

- [ ] **Step 5: Run store, migration-inventory, and multiprocess suites GREEN**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_store.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_idempotency_multiprocess.py tests/plugins/workflow/test_schema_migrations.py -v
```

Expected: nested state survives restart, child claims share existing capacity, cross-process duplicates lose atomically, and no schema migration appears.

- [ ] **Step 6: Commit Task 4**

```bash
git add plugins/workflow/store.py plugins/workflow/models.py tests/plugins/workflow/test_phase6_store.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_idempotency_multiprocess.py
git commit -m "feat(workflow): persist loop group children"
```

---

### Task 5: Feed body work into the existing fair scheduler

**Files:**
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/resources.py`
- Modify: `plugins/workflow/output_resolution.py`
- Modify: `plugins/workflow/bash_rendering.py`
- Create: `tests/plugins/workflow/test_phase6_scheduler.py`
- Modify: `tests/plugins/workflow/test_parallel_scheduler.py`
- Modify: `tests/plugins/workflow/test_scheduler.py`
- Modify: `tests/plugins/workflow/test_phase4_references.py`

**Interfaces:**
- Produces: frozen scheduler-local `SchedulerWorkItem` containing `run_id`, `node`, and optional `LoopGroupChildScope`.
- Produces: `_ready_work_items()` and `_execute_work_item()` as the shared top-level/child path inside `RunScheduler`; no public scheduler API change.
- Consumes: Task 3 effective directories and Task 4 child claim/transition methods.
- Keeps: one `ThreadPoolExecutor`, fair cursor by run, deterministic source order within a run, and existing worker ceilings.

- [ ] **Step 1: Add RED one-worker, fairness, and ordering tests**

Use real stores and deterministic blocking executors to prove:

- `max_total_workers=1` and `max_parallel_nodes=1` complete a two-node group without controller deadlock;
- two group runs and one ordinary run never exceed the existing profile-global/per-run claim counts;
- the fair cursor gives each runnable run a turn under sustained group work;
- independent nodes in one body layer may overlap;
- iteration N+1 never claims before every required child and the N completion decision commit; and
- ready body candidates follow source order, not lexical ID order.

Instrument pool construction and assert exactly one scheduler execution pool is created; no controller or executor creates another.

- [ ] **Step 2: Add RED scoped execution and reference tests**

Execute prompt/command/bash/script/approval/ordinary-loop children and assert:

- the existing executor instance handles the child;
- attempt/publication paths are exactly
  `nodes/<group>/<generation>/iterations/<0001>/nodes/<body>/<attempt>/` and
  `artifacts/loop-groups/<group>/iterations/<0001>/<body>/`;
- current body outputs resolve only from completed direct dependencies;
- outer outputs resolve only from direct dependencies of the outer group;
- `$LOOP_PREV.<body>.output` is empty on iteration one and authenticated previous-iteration content afterward;
- strict `$LOOP_PREV.<body>.output.<field>` rejects unavailable/invalid fields; and
- a failed group never exposes its last successful iteration to downstream outer nodes.

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_scheduler.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_phase4_references.py -v
```

Expected: the scheduler only produces top-level node IDs and no scoped child can execute.

- [ ] **Step 3: Introduce one internal work-item path**

Inside `scheduler.py`, add:

```python
@dataclass(frozen=True, slots=True)
class SchedulerWorkItem:
    run_id: str
    node: WorkflowNode
    loop_group_scope: LoopGroupChildScope | None = None

    @property
    def semantic_id(self) -> str:
        if self.loop_group_scope is None:
            return self.node.id
        return f"{self.loop_group_scope.group_id}/{self.node.id}"
```

Refactor candidate collection in `advance_all()` from `dict[str, list[str]]` to `dict[str, list[SchedulerWorkItem]]`. `_resolve_graph()` initializes/advances the outer controller but never submits it to the worker pool. `_ready_work_items()` returns top-level ready nodes plus ready group children from the current committed iteration. Keep run-level fair selection unchanged; sort candidates by `node.source_index` within a run.

Use `store.claim_node()` for top-level work and `store.claim_loop_group_child()` for scoped work. Futures continue to map back to `run_id`, preserving session-registry reconciliation and active-run accounting.

- [ ] **Step 4: Reuse executor dispatch with scoped context data**

Extract the existing context construction/dispatch block only far enough for `_execute_work_item()` to accept either state location. For a child:

```python
group_root = run_directory / "nodes" / group_id / str(generation)
attempt_directory = (
    group_root / "iterations" / f"{iteration:04d}" /
    "nodes" / body_id / claim.attempt_id
)
publication_directory = (
    run_directory / "artifacts" / "loop-groups" / group_id /
    "iterations" / f"{iteration:04d}" / body_id
)
```

Construct paths only from validated authored IDs and positive store-owned integers, resolve them, and verify containment under the run directory before passing them to `NodeExecutionContext`. Look up structured output, semantics, provider route, intended authority, and shared-context fingerprint through `work_item.semantic_id`. Merge group defaults before body explicit options at execution without changing the frozen body declaration.

Route completion through the matching top-level or child store method. Preserve existing retries, provider attempt accounting, outward-action policy, session-registry updates, process lifecycle, and publication verification.

- [ ] **Step 5: Implement scoped variable snapshots with existing renderers**

Extend the existing variable context—not the grammar—with explicit current-body, allowed-outer, and previous-body output maps. The child strict resolver searches only those sealed maps. The `until_bash` context uses current body outputs, approved outer dependencies, `$LOOP_PREV`, and ordinary loop input/feedback through the existing contained `BashExecutor` path.

Do not put child outputs into the outer global node-ID map where duplicate body IDs could collide. Keep full previous outputs as authenticated descriptors and load/verify bytes only when rendering.

- [ ] **Step 6: Run scheduler, reference, executor, and capacity suites GREEN**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_scheduler.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_phase4_references.py tests/plugins/workflow/test_phase6_store.py tests/plugins/workflow/test_phase6_execution_context.py -v
```

Expected: one shared pool executes both work kinds fairly, one-worker progress succeeds, and current/outer/previous scopes cannot cross.

- [ ] **Step 7: Commit Task 5**

```bash
git add plugins/workflow/scheduler.py plugins/workflow/store.py plugins/workflow/resources.py plugins/workflow/output_resolution.py plugins/workflow/bash_rendering.py tests/plugins/workflow/test_phase6_scheduler.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_phase4_references.py
git commit -m "feat(workflow): schedule loop group bodies"
```

---

### Task 6: Complete iterations, interactions, cancellation, and exact recovery

**Files:**
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/executors/loop.py`
- Modify: `plugins/workflow/actions.py`
- Modify: `plugins/workflow/sanitize.py`
- Create: `tests/plugins/workflow/test_phase6_interactions_recovery.py`
- Modify: `tests/plugins/workflow/test_fault_injection.py`
- Modify: `tests/plugins/workflow/test_crash_recovery.py`
- Modify: `tests/plugins/workflow/test_cancel_node.py`
- Modify: `tests/plugins/workflow/test_phase4_loop_interactions.py`
- Modify: `tests/plugins/workflow/test_evidence_api.py`

**Interfaces:**
- Produces: idempotent controller transitions for body terminal, iteration decision, next iteration, group success, hard maximum failure, and cancellation.
- Produces: stable private event families for controller/iteration/child claim, start, pause, retry, completion, failure, decision, recovery, reconciliation, cancellation, and group terminal transitions.
- Reuses: ordinary-loop signal stripping, `until_bash`, between-iteration confirmation/input, approval interactions, stale-interaction validation, effect policy, reconciliation, and process-tree cleanup.
- Keeps: existing public action vocabulary; no loop-group-specific mutation endpoint or action name.

- [ ] **Step 1: Add RED completion and interaction tests**

Cover:

- first terminal body node in definition order is the primary sink;
- exact completion marker stripping yields the clean outer output;
- signal completion wins and skips `until_bash`;
- `until_bash` runs only when no signal completed;
- `fresh_context` and shared-session behavior match ordinary loops;
- effective interactivity follows node/group/workflow precedence already used by ordinary loops;
- body approval pauses and resumes its exact child without rerunning succeeded siblings;
- between-iteration confirmation/input uses existing interaction types and feedback artifact rules; and
- iteration 100 without completion fails `loop_group_max_iterations` without creating an unusable final interaction.

Add a structured primary-sink case proving its validated logical value/fields become the outer group output contract.

Assert every private event carries the authenticated run/group/generation/iteration/body/attempt scope applicable to that transition, and that no event payload contains rendered prompts, commands, tool inputs/results, feedback text, output bytes, credentials, environment values, or private paths.

- [ ] **Step 2: Add RED fault, cancellation, and reconciliation tests**

Inject a crash immediately before and after each boundary: child claim, spawn intent, process start, output publication, child completion, iteration completion, completion decision, next-iteration creation, and outer completion. On restart assert:

- corroborated terminal children do not rerun;
- replay-safe interrupted children become ready only after cleanup confirmation;
- live processes remain monitored and are not duplicated;
- outward/unknown effects enter existing reconciliation;
- paused interactions retain exact group/generation/iteration/body/artifact identity;
- controller resumes at the last committed transition; and
- journal reserve exhaustion stops before an unjournaled effect.

Cancel before claim, during a subprocess, during an approval, after child completion, and racing a stale completion. Assert no new claims, process-tree cleanup, unstarted-child terminalization, group/run cancellation, and stale write rejection.

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_interactions_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_cancel_node.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_evidence_api.py -v
```

Expected: body execution exists from Task 5 but completion, recovery, and interaction scope are incomplete.

- [ ] **Step 3: Reuse the ordinary-loop completion primitives**

Extract only pure helpers needed by both loop types from `executors/loop.py`: exact signal detection/stripping and effective interactivity. Do not create a second loop executor. The group controller authenticates the primary sink output, calls the shared helper, optionally runs the existing contained Bash decision path, then commits one of:

```text
iteration_completed -> group_succeeded
iteration_completed -> confirmation/input pause
iteration_completed -> next_iteration_created
iteration_completed -> loop_group_max_iterations
```

Every store call requires expected controller generation, iteration, projection state version, and execution fence. Iteration N+1 becomes visible only in the same durable transition that records the N decision.

- [ ] **Step 4: Bind existing interactions and effect recovery to child scope**

Extend existing approval/input/signal/reconciliation state with optional authenticated group scope fields. Top-level interactions omit them and retain the exact old shape. Mutation methods resolve the selected profile's run first, then verify interaction ID, group, generation, iteration, body node, attempt, and feedback/approval artifact before acting.

Apply the existing effect policy per nested child during stale-claim reconciliation. Do not infer success from files. Require projection, journal, attempt, artifact descriptor, process identity, provider authority, and execution fence to agree. Ambiguous Jira/GitLab writes remain in reconciliation and are never automatically retried.

- [ ] **Step 5: Terminalize cancellation through existing process handling**

When run/group cancellation is desired, stop candidate production, invoke existing process-tree termination for active child attempts, mark unstarted body nodes cancelled/skipped as the existing trigger rules require, and terminalize the group only after active attempt cleanup is corroborated. Reject stale completions by generation/claim/fence checks.

- [ ] **Step 6: Run interaction, recovery, cancellation, and ordinary-loop guards GREEN**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_interactions_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_cancel_node.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_process_lifecycle_soak.py -v
```

Expected: every fault resumes or stops at its authenticated boundary, no completed sibling replays, and ordinary loops remain unchanged.

- [ ] **Step 7: Commit Task 6**

```bash
git add plugins/workflow/scheduler.py plugins/workflow/store.py plugins/workflow/executors/loop.py plugins/workflow/actions.py plugins/workflow/sanitize.py tests/plugins/workflow/test_phase6_interactions_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_cancel_node.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_evidence_api.py
git commit -m "feat(workflow): recover durable loop groups"
```

---

### Task 7: Expose one bounded parent summary and prove profile isolation

**Files:**
- Modify: `plugins/workflow/sanitize.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Create: `tests/plugins/workflow/test_phase6_public_projection.py`
- Modify: `tests/plugins/workflow/test_desktop_api.py`
- Modify: `tests/plugins/workflow/test_phase5_public_projection_contract.py`
- Modify: `tests/plugins/workflow/test_evidence_api.py`
- Modify: `apps/desktop/src/types/hermes.ts`
- Modify: `apps/desktop/src/lib/workflow-public-codec.ts`
- Modify: `apps/desktop/src/lib/workflow-public-codec.test.ts`
- Modify: `apps/desktop/src/app/workflows/adapter.ts`
- Modify: `apps/desktop/src/app/workflows/adapter.test.ts`
- Modify: `apps/desktop/src/app/workflows/run-inspector.tsx`
- Modify: `apps/desktop/src/app/workflows/workflow-run-drawer.test.tsx`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx`

**Interfaces:**
- Produces: optional `loop_group` on `WorkflowNodeProjection`; run DTO schema version and route vocabulary remain unchanged.
- Produces: optional bounded loop-group scope on timeline/evidence items using the existing event/evidence routes.
- Produces: bounded controller/iteration/body summaries only; no prompt, command, tool payload, raw output, feedback, credential, private path, or unbounded child history.
- Consumes: existing profile-scoped REST helpers, TanStack query keys, selected-run store, and stale-response guards.

- [ ] **Step 1: Add RED closed-projection and redaction tests**

Define strict bounded DTOs:

```python
class WorkflowLoopGroupBodyProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., min_length=1, max_length=128)
    node_type: str = Field(..., min_length=1, max_length=32)
    state: str = Field(..., min_length=1, max_length=32)
    attempt_count: StrictInt = Field(..., ge=0)
    duration_ms: StrictInt | None = Field(None, ge=0)
    failure_code: str | None = Field(None, max_length=128)

class WorkflowLoopGroupIterationProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iteration: StrictInt = Field(..., ge=1, le=100)
    state: str = Field(..., min_length=1, max_length=32)
    completed_nodes: StrictInt = Field(..., ge=0, le=512)
    total_nodes: StrictInt = Field(..., ge=1, le=512)
    duration_ms: StrictInt | None = Field(None, ge=0)
    failure_code: str | None = Field(None, max_length=128)

class WorkflowLoopGroupScopeProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: str = Field(..., min_length=1, max_length=128)
    controller_generation: StrictInt = Field(..., ge=1, le=1_000_000)
    iteration: StrictInt = Field(..., ge=1, le=100)
    body_node_id: str | None = Field(None, min_length=1, max_length=128)

class WorkflowLoopGroupProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iteration: StrictInt = Field(..., ge=1, le=100)
    max_iterations: StrictInt = Field(..., ge=1, le=100)
    completed_iterations: StrictInt = Field(..., ge=0, le=100)
    primary_sink: str = Field(..., min_length=1, max_length=128)
    body: list[WorkflowLoopGroupBodyProjection] = Field(..., max_length=512)
    iterations: list[WorkflowLoopGroupIterationProjection] = Field(
        ..., max_length=25
    )
```

Add sanitizer tests with oversized/malformed nested state and every forbidden private field. Assert only definition-ordered current-body summaries and the latest 25 iteration summaries survive; malformed group material is omitted/fails closed without weakening the enclosing run DTO.

Add route tests showing list/detail/events/evidence/attention/cleanup/actions continue using the same endpoints and Pydantic rejects unexpected fields. Public timeline/evidence items may include the closed `WorkflowLoopGroupScopeProjection`; omit it for top-level events and never project attempt/output content through it.

- [ ] **Step 2: Add RED Desktop rendering and profile-race tests**

Extend strict codec fixtures and assert:

- one workflow run remains one board card;
- an active group adds one `7/25` badge, not ticket/body cards;
- the inspector shows the outer group, bounded body state/attempt counts, and no raw private values;
- malformed or over-bounded `loop_group` data rejects the DTO;
- a late Profile A list/detail/event response cannot update Profile B's board/drawer; and
- Profile B mutation of Profile A's run ID receives not-found, sends no second mutation, and leaves Profile A unchanged when switching back.

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_public_projection.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_phase5_public_projection_contract.py tests/plugins/workflow/test_evidence_api.py -v
cd apps/desktop && npm test -- --run src/lib/workflow-public-codec.test.ts src/app/workflows/adapter.test.ts src/app/workflows/workflow-run-drawer.test.tsx src/app/workflows/index.test.tsx
```

Expected: the Python DTO has no group summary and the strict TypeScript codec rejects it.

- [ ] **Step 3: Project the nested summary from the existing node projection**

In `_public_node_projection()`, call one bounded `_public_loop_group_projection()` only when authenticated nested state is present. Calculate durations only from valid timestamps already in state, cap current body entries at the admitted 512-node ceiling, retain only the latest 25 iteration summaries, sanitize categorical failure/warning codes with existing helpers, and preserve definition order. Omit previous outputs, attempt metadata, interactions' rendered text, artifact paths, and child raw outputs.

Add the optional Pydantic field to `WorkflowNodeProjection` and the optional closed scope to existing timeline/evidence projections. Do not add a child enumeration route, query parameter, or run schema-version bump.

- [ ] **Step 4: Decode and render the same bounded shape in Desktop**

Mirror the closed DTO with TypeScript interfaces and an exact-key decoder. In `workflowBoardModel()`, select the active outer node's `loop_group` and add one `${iteration}/${max_iterations}` badge. In `RunInspector`, render a small semantic list/table beneath the parent node with body ID, type, state, attempt count, optional duration, and categorical failure. Reuse existing colors/components/i18n patterns and keep action handling on the outer run.

Do not add child selection state, child cards, a new route, or a cross-profile cache. Keep every query key beginning with the current profile and retain selected-profile guards around optimistic updates/invalidation.

- [ ] **Step 5: Run Python, Desktop, and profile-isolation suites GREEN**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_public_projection.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_phase5_public_projection_contract.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_workflow_language_desktop_e2e.py -v
cd apps/desktop && npm test -- --run src/lib/workflow-public-codec.test.ts src/app/workflows/adapter.test.ts src/app/workflows/workflow-run-drawer.test.tsx src/app/workflows/index.test.tsx
cd apps/desktop && npm run typecheck
```

Expected: one bounded parent summary renders, Profile B cannot see or mutate Profile A's run, and switching back restores Profile A state/actions.

- [ ] **Step 6: Commit Task 7**

```bash
git add plugins/workflow/sanitize.py plugins/workflow/dashboard/plugin_api.py tests/plugins/workflow/test_phase6_public_projection.py tests/plugins/workflow/test_desktop_api.py tests/plugins/workflow/test_phase5_public_projection_contract.py tests/plugins/workflow/test_evidence_api.py apps/desktop/src/types/hermes.ts apps/desktop/src/lib/workflow-public-codec.ts apps/desktop/src/lib/workflow-public-codec.test.ts apps/desktop/src/app/workflows/adapter.ts apps/desktop/src/app/workflows/adapter.test.ts apps/desktop/src/app/workflows/run-inspector.tsx apps/desktop/src/app/workflows/workflow-run-drawer.test.tsx apps/desktop/src/app/workflows/index.test.tsx
git commit -m "feat(workflow): show loop group progress"
```

---

### Task 8: Migrate Jira Defect Loop as the single Phase 6 consumer

**Files:**
- Create: `capabilities/workflow-packages/ericsson/workflows/jira-defect-loop.yaml`
- Create: `capabilities/workflow-packages/ericsson/workflows/jira-defect-loop.hermes.yaml`
- Modify: `capabilities/workflow-packages/ericsson/digests.json`
- Create: `capabilities/workflows/jira-defect-loop.yml`
- Create: `capabilities/workflows/jira-defect-loop.hermes.yaml`
- Modify: `capabilities/ericsson.json`
- Modify: `capabilities/ericsson-vendored-paths.json`
- Modify: `skills/ericsson/jira-to-gitlab/SKILL.md`
- Modify: `skills/ericsson/onboard-ericsson-capabilities/references/catalog.json`
- Modify: `skills/ericsson/onboard-ericsson-capabilities/references/capabilities/jira-defect-loop.md`
- Create: `tests/plugins/workflow/test_phase6_jira_defect_loop.py`
- Modify: `tests/hermes_cli/test_ericsson_connector_distribution.py`
- Modify: `tests/plugins/workflow/test_ericsson_connector_toolsets.py`
- Modify: `scripts/__tests__/vendor-ericsson.test.mjs`

**Interfaces:**
- Produces: distributed `jira-defect-loop` Archon v6 workflow with a 25-ticket immutable manifest and one `loop_group`.
- Reuses: existing `jira_my_tickets`, `jira_get_issue`, Ericsson GitLab tools, approval node, outward-action policy, structured outputs, and authenticated workflow run history.
- Keeps: `jira-to-gitlab` single-ticket workflow available; does not migrate or claim migration of the other seven assessed legacy loops.

- [ ] **Step 1: Add RED package/distribution and semantic contract tests**

Load the real distributed workflow and inspect its sealed definition. Assert:

- exactly one `jira_my_tickets` call is possible and its authored `max_results` is 25;
- first-occurrence Jira order is preserved while duplicate keys are removed;
- the run-scoped manifest is immutable and tickets appearing later wait for a new run;
- an empty manifest skips the group and produces empty JSON/Markdown aggregates;
- the group maximum is 25 and processes exactly one manifest key per iteration;
- every Jira/GitLab write node is outward-action protected by its exact current approval dependency;
- no spreadsheet/email tool/resource/delivery exists; and
- the capability/catalog/distribution metadata lists `jira-defect-loop` without removing `jira-to-gitlab`.

Add execution fixtures for empty, one-ticket, 25-ticket, duplicate, malformed, and over-limit results. Expected outcomes (`not_found`, `permission`, `needs_info`, `manual_review`, `not_a_code_fix`, `safely_skipped`) must be successful terminal records that allow the next iteration. Ambiguous write results must pause/fail for reconciliation with no automatic duplicate call.

- [ ] **Step 2: Run the RED migration tests**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_jira_defect_loop.py tests/hermes_cli/test_ericsson_connector_distribution.py tests/plugins/workflow/test_ericsson_connector_toolsets.py -v
node --test scripts/__tests__/vendor-ericsson.test.mjs
```

Expected: the new workflow and metadata are absent.

- [ ] **Step 3: Author the workflow with one read and one bounded group**

The outer graph is:

```yaml
nodes:
  - id: fetch-ticket-manifest
    prompt: ...
    allowed_tools: [jira_my_tickets]
    maxTurns: 2
    retry:
      max_attempts: 1
    # Prompt/tool contract requires max_results: 25, first-occurrence order,
    # dedupe, and a closed immutable structured manifest.

  - id: process-ticket-manifest
    depends_on: [fetch-ticket-manifest]
    when: "$fetch-ticket-manifest.output.count > 0"
    loop_group:
      max_iterations: 25
      until: "<promise>BATCH_COMPLETE</promise>"
      nodes: ...

  - id: publish-empty-aggregate
    depends_on: [fetch-ticket-manifest]
    when: "$fetch-ticket-manifest.output.count == 0"
    ...
```

Seal the fetch node to only `jira_my_tickets`, two model turns (one tool request plus one structured response), and one workflow attempt. Its tool trace must contain exactly one call with `max_results: 25`; zero, duplicate, parallel, or wrong-argument calls fail the node instead of creating a manifest.

The group body selects the exact current manifest key, reads that ticket, triages it, conditionally follows the existing GitLab research/fix path, prepares each write, requires an approval bound to that exact write intent/digest, performs only the approved write, and records both per-ticket and cumulative state. The terminal record emits valid structured JSON plus `<promise>BATCH_COMPLETE</promise>` only when every manifest key is terminal; marker stripping must leave valid JSON.

Use structured schemas to cap keys, text, warnings, action lists, artifacts, and aggregate size. Carry expected domain outcomes as data. Treat uncertain outward results as engine/reconciliation failures, never `safely_skipped`.

- [ ] **Step 4: Produce authenticated per-ticket and aggregate artifacts**

Publish one per-ticket history record plus final aggregate JSON and Markdown through existing typed artifact/publication paths. The aggregate must preserve manifest order, contain exactly one record per unique key, and reconcile counts with terminal categories. Hermes run history remains the system of record. Do not add spreadsheet/email output or a second storage service.

Update the Jira skill/onboarding references to describe the new batch workflow, its 25-ticket bound, approvals, expected outcomes, reconciliation stop, and artifacts. Remove only the statement that this batch loop is deferred. State explicitly that the other seven legacy iterative flows remain unmigrated.

- [ ] **Step 5: Stamp the existing digest/distribution metadata**

Add the package workflow and sidecar, compute its composite digest with the existing workflow compiler used by capability staging, and update `digests.json`. Copy the identical vendored workflow/sidecar to `capabilities/workflows/`, add both paths to `capabilities/ericsson-vendored-paths.json`, and add the workflow to `capabilities/ericsson.json`. Use `scripts/vendor-ericsson.mjs` verification; do not hand-invent a digest algorithm.

- [ ] **Step 6: Run migration, connector, staging, and distribution suites GREEN**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_jira_defect_loop.py tests/hermes_cli/test_ericsson_connector_distribution.py tests/plugins/workflow/test_ericsson_connector_toolsets.py tests/hermes_cli/test_capability_staging.py tests/hermes_cli/test_baked_seed.py tests/plugins/workflow/test_showcase_distribution_e2e.py -v
node --test scripts/__tests__/vendor-ericsson.test.mjs
```

Expected: the real staged profile discovers the authenticated workflow, all manifest/output cases pass, approvals guard every write, and ambiguous writes never replay.

- [ ] **Step 7: Commit Task 8**

```bash
git add capabilities/workflow-packages/ericsson/workflows/jira-defect-loop.yaml capabilities/workflow-packages/ericsson/workflows/jira-defect-loop.hermes.yaml capabilities/workflow-packages/ericsson/digests.json capabilities/workflows/jira-defect-loop.yml capabilities/workflows/jira-defect-loop.hermes.yaml capabilities/ericsson.json capabilities/ericsson-vendored-paths.json skills/ericsson/jira-to-gitlab/SKILL.md skills/ericsson/onboard-ericsson-capabilities/references/catalog.json skills/ericsson/onboard-ericsson-capabilities/references/capabilities/jira-defect-loop.md tests/plugins/workflow/test_phase6_jira_defect_loop.py tests/hermes_cli/test_ericsson_connector_distribution.py tests/plugins/workflow/test_ericsson_connector_toolsets.py scripts/__tests__/vendor-ericsson.test.mjs
git commit -m "feat(workflow): migrate Jira Defect Loop"
```

---

### Task 9: Run full gates, activate normalizer v6, and verify installed behavior

**Files:**
- Modify: `plugins/workflow/language.py`
- Modify: `tests/plugins/workflow/test_phase6_language.py`
- Modify: `tests/plugins/workflow/test_phase3_language.py`
- Modify: `tests/plugins/workflow/test_phase5_language.py`
- Modify: `tests/plugins/workflow/test_installed_distribution_e2e.py`
- Modify: `tests/plugins/workflow/test_workflow_language_desktop_e2e.py`
- Modify: `tests/plugins/workflow/test_performance_bounds.py`
- Modify: `docs/design/portable-workflow-orchestration.md`

**Interfaces:**
- Changes: `CURRENT_NORMALIZER_BY_PROFILE[WorkflowLanguageProfile.ARCHON_2026_07]` from 5 to 6 only after all dormant-v6 gates pass.
- Keeps: legacy current normalizer 2, supported versions 1..6, snapshot format 2, v1-v5 exact replay, and all existing public route/action versions.

- [ ] **Step 1: Run every focused Phase 6 suite before activation**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_language.py tests/plugins/workflow/test_phase6_admission.py tests/plugins/workflow/test_phase6_execution_context.py tests/plugins/workflow/test_phase6_store.py tests/plugins/workflow/test_phase6_scheduler.py tests/plugins/workflow/test_phase6_interactions_recovery.py tests/plugins/workflow/test_phase6_public_projection.py tests/plugins/workflow/test_phase6_jira_defect_loop.py -v
```

Expected: all explicit v6 tests pass while ordinary new Archon admission still records v5.

- [ ] **Step 2: Run historical compatibility and runtime guard suites**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase3_language.py tests/plugins/workflow/test_phase3_execution_semantics.py tests/plugins/workflow/test_phase4_language.py tests/plugins/workflow/test_phase4_snapshot.py tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase5_language.py tests/plugins/workflow/test_phase5_provider_snapshot.py tests/plugins/workflow/test_phase5_execution_authority_continuity.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_cancel_node.py -v
```

Expected: all pass with no re-recorded old digest/snapshot fixture.

- [ ] **Step 3: Activate v6 and update the exact version assertions**

Change only:

```python
CURRENT_NORMALIZER_BY_PROFILE = MappingProxyType({
    WorkflowLanguageProfile.HERMES_LEGACY: 2,
    WorkflowLanguageProfile.ARCHON_2026_07: 6,
})
SUPPORTED_NORMALIZER_VERSIONS = frozenset({1, 2, 3, 4, 5, 6})
```

Update current-version tests and installed-distribution contract output. Add one ordinary `load_workflow()` assertion proving new Archon admission now records v6, while `load_workflow_snapshot(..., normalizer_version=5)` still rejects/retains old semantics exactly.

- [ ] **Step 4: Update the durable architecture document**

In `docs/design/portable-workflow-orchestration.md`, replace the Phase 6 deferred note with the shipped contract: one-level bounded groups, shared scheduler/claims, snapshot v6/format 2, recovery identity, profile isolation, public bounded summary, and Jira Defect Loop as the sole migrated consumer. Keep the other seven flows and out-of-scope items explicitly deferred.

- [ ] **Step 5: Run activation, installed, Windows, restart, and performance gates**

Run:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_phase6_language.py tests/plugins/workflow/test_installed_distribution_e2e.py tests/plugins/workflow/test_workflow_language_desktop_e2e.py tests/plugins/workflow/test_performance_bounds.py tests/plugins/workflow/test_idempotency_multiprocess.py tests/plugins/workflow/test_process_lifecycle_soak.py -v
```

Expected: installed imports admit v6 without source-tree fallback; Windows paths/processes remain contained; restart/multiprocess recovery does not duplicate work; admission and scheduling remain within existing performance ceilings.

- [ ] **Step 6: Run Desktop and package gates**

Run:

```bash
cd apps/desktop && npm test -- --run src/lib/workflow-public-codec.test.ts src/app/workflows/adapter.test.ts src/app/workflows/workflow-run-drawer.test.tsx src/app/workflows/index.test.tsx
cd apps/desktop && npm run typecheck
cd apps/desktop && npm run lint
node --test scripts/__tests__/vendor-ericsson.test.mjs
```

Expected: all pass; one run card renders bounded progress and profile switches cannot leak late results or actions.

- [ ] **Step 7: Run the canonical repository gate**

Run:

```bash
scripts/run_tests.sh
```

Expected: complete suite passes. If the repository documents a separate brand gate for the active checkout, run that exact gate too; do not switch to literal `main` or a release brand branch for this implementation.

- [ ] **Step 8: Perform final invariant review**

Verify with targeted searches and runtime assertions:

- no `ThreadPoolExecutor` was added outside the existing scheduler path for groups;
- no new SQLite table/migration or core model tool exists;
- no child workflow/include/nested group/group retry/cross-profile route slipped in;
- every outward child is approval/effect-policy bound;
- public DTOs contain no private output/path/prompt/tool/feedback data;
- every profile request/cache key retains profile scope;
- v1-v5 replay tests use recorded versions and pass; and
- only Jira Defect Loop among the eight assessed legacy flows is marked migrated.

Check the worktree and staged set explicitly:

```bash
git diff --check
git status --short
git branch --show-current
```

Expected: no whitespace errors, no unrelated files staged, and branch is `base`.

- [ ] **Step 9: Commit Task 9**

```bash
git add plugins/workflow/language.py tests/plugins/workflow/test_phase6_language.py tests/plugins/workflow/test_phase3_language.py tests/plugins/workflow/test_phase5_language.py tests/plugins/workflow/test_installed_distribution_e2e.py tests/plugins/workflow/test_workflow_language_desktop_e2e.py tests/plugins/workflow/test_performance_bounds.py docs/design/portable-workflow-orchestration.md
git commit -m "feat(workflow): activate durable loop groups"
```

---

## Completion Checklist

- [ ] A new `archon-2026-07` workflow admits through normalizer v6 and runs a bounded multi-node group.
- [ ] The group body uses existing executors and the existing fair profile worker pool; a one-worker configuration progresses.
- [ ] Child claims, outputs, approvals, artifacts, processes, cancellation, and recovery are generation/iteration/body/attempt bound.
- [ ] Completed child work survives restart; uncertain outward work stops for reconciliation and is not blindly replayed.
- [ ] The first terminal body node supplies the clean outer output; signal, `until_bash`, maximum, fresh/shared context, and strict references match the approved contract.
- [ ] Snapshot format remains 2 and recorded v1-v5 runs replay without reinterpretation.
- [ ] Desktop shows one run card with bounded group progress and a bounded inspector summary.
- [ ] Profile B cannot list, receive late UI state from, or mutate Profile A's run; switching back restores Profile A's state/actions.
- [ ] Jira Defect Loop reads one immutable manifest of at most 25 unique ordered keys and produces per-ticket plus aggregate JSON/Markdown history.
- [ ] Every Jira/GitLab write requires exact current approval; expected outcomes continue and ambiguous writes stop.
- [ ] No database migration, second scheduler/pool, core model tool, cross-profile board, spreadsheet/email delivery, nested group, runtime child workflow, or unrelated legacy migration ships.
