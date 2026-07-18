# Workflow Orchestration Production Findings Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve every Critical, High, Medium, and Low finding in the
2026-07-18 production implementation adversarial rereview before the workflow
branch is eligible to merge to base.

**Architecture:** Preserve RunStore and its journal as workflow authority. Add
explicit authorization and semantic-idempotency boundaries before widening API
or Gateway access; then fence coordinator work transactionally by epoch and
make every runnable transition pass through one capacity/lane admission path.
Base Hermes changes remain generic: authenticated plugin invocation, an opaque
Gateway delivery capability, and single-generation plugin-service hosting have
the workflow plugin as their immediate consumer.

**Tech Stack:** Python 3.11+, FastAPI/Pydantic, SQLite WAL, filesystem journals,
threading/multiprocessing, pytest, Electron/React/TypeScript, Vitest, TanStack
Query, platform-native process primitives, GitHub Actions.

**Normative inputs:**

- `docs/superpowers/specs/2026-07-18-plugin-background-services-workflow-coordination-design.md`
- `docs/superpowers/specs/2026-07-18-workflow-orchestration-operator-experience-design.md`
- `docs/superpowers/plans/2026-07-18-workflow-orchestration-operator-experience-plan.md`
- `docs/reviews/2026-07-18-workflow-orchestration-production-implementation-adversarial-review.md`
- `docs/reviews/2026-07-18-workflow-orchestration-production-implementation-adversarial-rereview.md`

## Global Constraints

- Do not add a permanent model-facing workflow tool or mutate model tools,
  prompts, or prior conversation messages.
- Do not import workflow modules into `hermes_cli/plugins.py`,
  `hermes_cli/plugin_services.py`, `hermes_cli/web_server.py`, or
  `gateway/run.py`.
- Do not add user-facing non-secret `HERMES_*` settings; use `config.yaml`.
- Do not accept background work without a fresh coordinator.
- Do not replay an outward attempt while its prior outcome is uncertain.
- Do not delete evidence because an index is missing, empty, corrupt, or
  inconsistent.
- Do not execute workflow tails in HTTP or Gateway command requests.
- Notification/UI state is a projection; RunStore/outbox remains authority.
- Each task begins with a failing behavioral test, uses real SQLite/filesystem/
  process/middleware boundaries where applicable, and ends with an exact-file
  commit.
- Preserve the v2.0.9 migration fixture byte-for-byte and migrate only copies.
- Stage only the files listed by the task being committed.

---

## Dependency and merge policy

Tasks are ordered by safety dependency, not UI convenience. Tasks 1-12 build
the safety/correctness substrate. Tasks 13-18 close promised product surfaces
and Low hardening. Task 19 runs native/release evidence. All nineteen tasks are
merge-blocking for this branch under the maintainer's “fix all findings before
merge” decision. No task may be marked complete because a mocked unit test
passes when its acceptance test names middleware, SQLite, filesystem, process,
restart, or multiprocess behavior.

The corrected reopened blocker set is 3, 5, 7, 9, 12, 13, and 14. Previously
blocked items 6, 11, 15, 16, and 17 also remain open.

### Task 1: Enforce workflow read, write, delivery, and admin authorization

**Findings:** C-01; prerequisite for H-10/H-02 and H-11/H-01.

**Files:**

- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `plugins/workflow/store.py`
- Modify: `tests/hermes_cli/test_workflow_dashboard_auth.py`
- Modify: `tests/plugins/workflow/test_desktop_api.py`
- Modify: `tests/plugins/workflow/test_notification_delivery.py`
- Modify: `tests/plugins/workflow/test_schema_migrations.py`

**Interfaces:**

- Produces `WorkflowAuthority.capabilities` and `.require(capability)`.
- Adds the required keyword-only `authority_binding: str` parameter to
  `RunStore.cleanup_runs`; the store
  hashes the binding into cleanup-preview state and requires the same binding
  at execution.
- Session callers receive read/write/delivery only within their verified
  principal scope. `workflow:read` tokens receive read only;
  `workflow:write` receives read/write; `workflow:delivery` receives
  read/delivery for its bound projection scope; `workflow:admin` and
  local-admin authentication receive every capability.

- [ ] **Step 1: Write failing real-middleware authorization tests**

```python
def test_read_token_cannot_cancel_or_approve(real_workflow_app, running_run):
    response = real_workflow_app.post(
        f"/api/plugins/workflow/runs/{running_run.run_id}/cancel",
        headers=read_token_headers(),
        json={"expected_version": running_run.state_version},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "workflow_write_required"

def test_write_token_cannot_execute_cleanup(real_workflow_app):
    response = real_workflow_app.get(
        "/api/plugins/workflow/cleanup/preview?older_than=0d",
        headers=write_token_headers(),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "workflow_admin_required"
```

Also prove session scope cannot expand through
`X-Hermes-Operator-Scope`, a cleanup token minted for principal A fails for
principal B, a delivery authority cannot lease or acknowledge another bound
destination, and admin/local-admin succeeds.

- [ ] **Step 2: Run the tests and verify the bypass**

Run:

```bash
pytest -q tests/hermes_cli/test_workflow_dashboard_auth.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_notification_delivery.py \
  -k 'read_token or write_token or delivery_scope or cleanup_binding'
```

Expected: read-token mutation and cross-principal cleanup execution return 200
before the fix.

- [ ] **Step 3: Replace `high_trust` with explicit capabilities**

Implement an immutable plugin-owned authority record and route guards:

```python
@dataclass(frozen=True, slots=True)
class WorkflowAuthority:
    principal: str
    scope: str | None
    unrestricted: bool
    capabilities: frozenset[str]

    def require(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise HTTPException(
                status_code=403,
                detail={"code": f"workflow_{capability}_required"},
            )
```

Call `require("write")` before workflow-state mutations,
`require("delivery")` before scoped notification lease/ack/fail/dismiss
receipts, and `require("admin")` before cleanup preview/execute/history,
dead-letter repair, or notification pruning. A delivery authority may only
operate on notifications whose durable destination binding matches its
verified projection scope.
Persist `authority_binding_digest` in `cleanup_previews`; validate it before
candidate comparison or deletion. Do not authorize from client headers.

- [ ] **Step 4: Verify migration and authorization behavior**

Run:

```bash
pytest -q tests/hermes_cli/test_workflow_dashboard_auth.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_notification_delivery.py \
  tests/plugins/workflow/test_schema_migrations.py
```

Expected: PASS, including the hash-pinned v2.0.9 fixture.

- [ ] **Step 5: Commit exact files**

```bash
git add plugins/workflow/dashboard/plugin_api.py plugins/workflow/store.py \
  tests/hermes_cli/test_workflow_dashboard_auth.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_notification_delivery.py \
  tests/plugins/workflow/test_schema_migrations.py
git commit -m "fix(workflow): enforce operator capabilities"
```

### Task 2: Contain evidence log reads and unify evidence sanitization

**Findings:** C-02 and the evidence portion of L-05.

**Files:**

- Modify: `plugins/workflow/evidence.py`
- Modify: `plugins/workflow/sanitize.py`
- Modify: `tests/plugins/workflow/test_evidence_api.py`
- Modify: `tests/plugins/workflow/test_security_boundaries.py`
- Modify: `tests/plugins/workflow/test_compat_matrix.py`

**Interfaces:**

- Produces `_read_contained_regular_file(root: Path, candidate: Path,
  limit: int) -> tuple[bytes, int]`.
- The helper rejects a symlink/reparse point in any path component, verifies
  containment and regular-file type after open, and returns bytes plus the
  authoritative file size.

- [ ] **Step 1: Write failing escape tests**

```python
def test_log_evidence_rejects_symlink_outside_run(store, admitted, tmp_path):
    secret = tmp_path / "outside-secret"
    secret.write_text("OUTSIDE_SENTINEL")
    stdout = store.run_directory(admitted.run_id) / "nodes/n1/a1/stdout.txt"
    stdout.parent.mkdir(parents=True)
    stdout.symlink_to(secret)
    page = EvidenceReader(store).query(admitted.run_id, kind="logs")
    assert "OUTSIDE_SENTINEL" not in str(page)
    assert page["warnings"] == ["unsafe_evidence_path"]
```

Add a symlinked parent test, a replace-between-enumeration-and-open test on
POSIX, a non-regular FIFO/device refusal test, and a Windows reparse-point test.

- [ ] **Step 2: Verify the sentinel is currently returned**

Run:

```bash
pytest -q tests/plugins/workflow/test_evidence_api.py \
  tests/plugins/workflow/test_security_boundaries.py -k 'symlink or reparse or regular_file'
```

Expected: FAIL with `OUTSIDE_SENTINEL` present or no unsafe-path warning.

- [ ] **Step 3: Implement descriptor-based safe reads**

Use `os.open` with `O_NOFOLLOW` where available, `os.fstat` regular-file
validation, resolved-root containment, and a second identity check after open.
On Windows reject reparse points before reading. Treat unsafe paths as omitted
evidence plus a stable warning, never as a 500. Continue using
`sanitize_projection`/`sanitize_text` and preserve the 256 KiB aggregate cap.

- [ ] **Step 4: Run evidence and platform tests**

```bash
pytest -q tests/plugins/workflow/test_evidence_api.py \
  tests/plugins/workflow/test_security_boundaries.py \
  tests/plugins/workflow/test_compat_matrix.py
```

Expected: PASS on the current platform; Windows-specific tests are collected
for Task 12 native CI.

- [ ] **Step 5: Commit exact files**

```bash
git add plugins/workflow/evidence.py plugins/workflow/sanitize.py \
  tests/plugins/workflow/test_evidence_api.py \
  tests/plugins/workflow/test_security_boundaries.py \
  tests/plugins/workflow/test_compat_matrix.py
git commit -m "fix(workflow): contain evidence log reads"
```

### Task 3: Require exact durable interaction identities

**Findings:** M-13.

**Files:**

- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `plugins/workflow/showcase.py`
- Modify: `tests/plugins/workflow/test_approval_races.py`
- Modify: `tests/plugins/workflow/test_desktop_api.py`
- Modify: `tests/plugins/workflow/test_showcase_resilience_e2e.py`

**Interfaces:** Every approve, reject, provide-input, and reconcile mutation
consumes a non-empty `interaction_id`. Idempotent reuse matches only the exact
recorded interaction ID and decision.

- [ ] **Step 1: Add a two-gate failing test**

```python
def test_null_interaction_does_not_reuse_prior_gate_decision(two_gate_run):
    first = approve_current_gate(two_gate_run)
    second = pause_at_next_gate(two_gate_run)
    response = post_approve(second.run_id, second.state_version, interaction_id=None)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "interaction_id_required"
    assert load(second.run_id)["state_version"] == second.state_version
```

- [ ] **Step 2: Run the exact test and observe incorrect reuse**

```bash
pytest -q tests/plugins/workflow/test_approval_races.py \
  tests/plugins/workflow/test_desktop_api.py -k 'null_interaction or two_gate'
```

Expected: FAIL because the first recorded decision is returned.

- [ ] **Step 3: Make null identity invalid at every boundary**

Change `_already_decided` to return `None` for a missing ID. Validate IDs in the
REST action dispatcher and have showcase wrappers load and submit the current
authoritative interaction ID. Preserve exact-ID idempotent retries.

- [ ] **Step 4: Verify all interaction surfaces**

```bash
pytest -q tests/plugins/workflow/test_approval_races.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_showcase_resilience_e2e.py
```

- [ ] **Step 5: Commit exact files**

```bash
git add plugins/workflow/store.py plugins/workflow/dashboard/plugin_api.py \
  plugins/workflow/showcase.py tests/plugins/workflow/test_approval_races.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_showcase_resilience_e2e.py
git commit -m "fix(workflow): bind decisions to exact interactions"
```

### Task 4: Separate semantic admission identity from provenance and delivery

**Findings:** H-06, H-07, and L-04.

**Files:**

- Modify: `plugins/workflow/admission.py`
- Modify: `plugins/workflow/provenance.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `tests/plugins/workflow/test_provenance.py`
- Modify: `tests/plugins/workflow/test_cli.py`
- Modify: `tests/plugins/workflow/test_schema_migrations.py`
- Create: `tests/plugins/workflow/test_idempotency_multiprocess.py`

**Interfaces:**

- `RunAdmissionRequest.idempotency_namespace: str` is mandatory and
  server/adapter derived.
- `TriggerProvenance.semantic_record()` contains stable source, assurance, and
  verified identity namespace only. PID, display actor, source instance, and
  return-route capability remain durable audit metadata but are excluded from
  `start_digest`.
- SQLite uniqueness becomes `(idempotency_namespace_digest, workflow_name,
  idempotency_digest)`.

- [ ] **Step 1: Add a real cross-process retry test**

```python
def test_same_cli_key_from_new_process_joins_existing(tmp_path, workflow_path):
    first = run_cli_process(tmp_path, workflow_path, key="stable-key")
    second = run_cli_process(tmp_path, workflow_path, key="stable-key")
    assert first["run_id"] == second["run_id"]
    assert second["disposition"] == "existing"
```

Also test changed inputs conflict, different verified principals do not join,
return-route rotation does not conflict, and legacy missing-trigger projections
report `source="unknown"`, `assurance="legacy_unknown"`.

- [ ] **Step 2: Verify the PID test fails**

```bash
pytest -q tests/plugins/workflow/test_idempotency_multiprocess.py \
  tests/plugins/workflow/test_provenance.py
```

Expected: second process reports `idempotency_conflict`.

- [ ] **Step 3: Migrate semantic identity atomically**

Add `idempotency_namespace_digest` to copied legacy databases and derive a
stable legacy namespace without rewriting journal evidence. Because the old
uniqueness rule is an inline SQLite constraint, migrate through a transactional
shadow `runs` table with the new uniqueness rule, copy and count-verify every
row, recreate indexes, swap tables, run `foreign_key_check` and
`integrity_check`, then commit. Update admission lookup. CLI uses a stable
profile-local namespace;
API/Gateway tasks later use verified principal/scope. Keep full provenance in
the projection. Never update or replace a stored return route merely because an
unverified retry supplies a different value.

- [ ] **Step 4: Verify migration and cross-process behavior**

```bash
pytest -q tests/plugins/workflow/test_idempotency_multiprocess.py \
  tests/plugins/workflow/test_provenance.py \
  tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_schema_migrations.py
```

- [ ] **Step 5: Commit exact files**

```bash
git add plugins/workflow/admission.py plugins/workflow/provenance.py \
  plugins/workflow/store.py plugins/workflow/cli.py \
  tests/plugins/workflow/test_provenance.py tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_schema_migrations.py \
  tests/plugins/workflow/test_idempotency_multiprocess.py
git commit -m "fix(workflow): stabilize semantic idempotency"
```

### Task 5: Harden showcase starts, preflight, and trust behavior

**Findings:** M-14 and L-06.

**Files:**

- Modify: `plugins/workflow/showcase.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `tests/plugins/workflow/test_showcase_catalog.py`
- Modify: `tests/plugins/workflow/test_showcase_offline_e2e.py`
- Modify: `tests/skills/test_workflow_operator_behavior.py`

**Interfaces:** JSON or no-wait showcase starts require caller idempotency;
preflight returns `input_requirements`; running a trusted bundled showcase does
not mutate the trust store.

- [ ] **Step 1: Add failing machine-contract tests**

```python
def test_showcase_json_no_wait_requires_key(parser, capsys):
    args = parser.parse_args(["showcase", "run", "laptop-diagnostic", "--json", "--no-wait"])
    assert args.func(args) == EXIT_INVOCATION
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "idempotency_key_required"
```

Assert preflight lists `symptom` before execution and trust-store bytes are
unchanged across a showcase run.

- [ ] **Step 2: Run and confirm random-key/trust side effects**

```bash
pytest -q tests/plugins/workflow/test_showcase_catalog.py \
  tests/plugins/workflow/test_showcase_offline_e2e.py \
  tests/skills/test_workflow_operator_behavior.py
```

- [ ] **Step 3: Reuse the general machine contract**

Apply the same key gate as general workflow starts. Build preflight input
requirements from the package definition/delivery defaults. Replace per-run
`WorkflowTrustStore.trust` with immutable bundled-distribution verification and
the real risk digest used by doctor/trust.

- [ ] **Step 4: Run showcase and skill behavior tests**

Use the Step 2 command; expected PASS.

- [ ] **Step 5: Commit exact files**

```bash
git add plugins/workflow/showcase.py plugins/workflow/cli.py \
  tests/plugins/workflow/test_showcase_catalog.py \
  tests/plugins/workflow/test_showcase_offline_e2e.py \
  tests/skills/test_workflow_operator_behavior.py
git commit -m "fix(workflow): harden showcase machine starts"
```

### Task 6: Fence all coordinator execution by durable epoch

**Findings:** H-03 and H-04.

**Files:**

- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/coordinator.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/store.py`
- Modify: `tests/plugins/workflow/test_coordinator.py`
- Modify: `tests/plugins/workflow/test_coordinator_multiprocess.py`
- Modify: `tests/plugins/workflow/test_shutdown_recovery.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ExecutionFence:
    owner_id: str
    owner_epoch: int
```

`NodeClaim` carries the fence. Claim, pre-spawn authorization, heartbeat,
process-start/stop, loop-iteration, completion, retry scheduling, and
owner-filtered interruption validate the fence against the fresh
`coordinator_lease` row while holding `BEGIN IMMEDIATE`.

- [ ] **Step 1: Add a mid-node takeover multiprocess test**

The test blocks old epoch 1 after it selects an outward node, expires/takes over
with epoch 2, then releases epoch 1. Assert epoch 1 cannot spawn, complete,
schedule retry, or interrupt epoch-2 claims, and only epoch 2 creates outward
effect evidence.

- [ ] **Step 2: Verify current stale dispatch and broad interruption**

```bash
pytest -q tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_shutdown_recovery.py -k 'epoch or takeover or successor'
```

Expected: stale dispatch or successor-claim interruption is observed.

- [ ] **Step 3: Implement transactional fencing**

Add `RunStore.assert_execution_fence(connection, fence, now)` and call it inside
every coordinator-owned state transaction. Check again immediately before
spawn intent. Change `interrupt_active_claims` to require an exact fence and
ignore claims from another owner/epoch. Losing leadership sets scheduler stop
before awaiting sweep shutdown.

- [ ] **Step 4: Run coordinator, scheduler, and shutdown tests**

```bash
pytest -q tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_scheduler.py \
  tests/plugins/workflow/test_shutdown_recovery.py
```

- [ ] **Step 5: Commit exact files**

```bash
git add plugins/workflow/models.py plugins/workflow/coordinator.py \
  plugins/workflow/scheduler.py plugins/workflow/store.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_shutdown_recovery.py
git commit -m "fix(workflow): fence coordinator execution epochs"
```

### Task 7: Close spawn, abandon, and index-drift recovery windows

**Findings:** M-09, M-10, and M-11.

**Files:**

- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/executors/bash.py`
- Modify: `plugins/workflow/executors/script.py`
- Modify: `tests/plugins/workflow/test_crash_recovery.py`
- Modify: `tests/plugins/workflow/test_shutdown_recovery.py`
- Modify: `tests/plugins/workflow/test_fault_injection.py`

**Interfaces:** A durable `spawn_intent` precedes process creation. Only an
explicit durable `spawn_failed` proves not-started; an identityless unresolved
intent is `outcome_uncertain`. Abandon refuses any live/unobserved claim.
Corroborated load resynchronizes all derived index fields, including status.

- [ ] **Step 1: Add three failing crash-window tests**

Inject process death after `spawn_intent` but before `process_started`; pause one
parallel node while another has a live claim and attempt abandon; append a
terminal journal frame while suppressing the SQLite status update and load it
again in the same process.

- [ ] **Step 2: Run and verify unsafe classifications**

```bash
pytest -q tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_shutdown_recovery.py \
  tests/plugins/workflow/test_fault_injection.py -k 'spawn_intent or live_claim or status_drift'
```

- [ ] **Step 3: Implement explicit spawn state and corroborated resync**

Persist executor nonce/effect class in `spawn_intent`; on spawn exception append
`spawn_failed`; after spawn append process identity. Extend terminal/abandon
gates to inspect active claims and process identity without deleting them.
After journal/projection corroboration, update status, desired status,
execution mode, queue fields, state version, and integrity columns together.

- [ ] **Step 4: Run crash/fault/process tests**

```bash
pytest -q tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_shutdown_recovery.py \
  tests/plugins/workflow/test_fault_injection.py \
  tests/plugins/workflow/test_process_lifecycle_soak.py
```

- [ ] **Step 5: Commit exact files**

```bash
git add plugins/workflow/store.py plugins/workflow/scheduler.py \
  plugins/workflow/executors/bash.py plugins/workflow/executors/script.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_shutdown_recovery.py \
  tests/plugins/workflow/test_fault_injection.py
git commit -m "fix(workflow): close executor recovery windows"
```

### Task 8: Add durable foreground-owner recovery

**Findings:** H-05.

**Files:**

- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/coordinator.py`
- Modify: `plugins/workflow/actions.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `tests/plugins/workflow/test_coordinator_multiprocess.py`
- Modify: `tests/plugins/workflow/test_operator_e2e.py`

**Interfaces:** `RunStore.adopt_expired_foreground(run_id, fence, now)` performs
one CAS transition. It converts a safe expired foreground run to background
ownership or persists reconciliation/interruption; it never returns a silent
success with unchanged state.

- [ ] **Step 1: Add owner-death plus live-coordinator tests**

Use separate processes: admit foreground with no leader, kill its owner, start
a coordinator, and assert a replay-safe run continues. Repeat with unresolved
outward spawn intent and assert reconciliation instead of replay. Assert a
stalled `resume` either changes state/version or returns a typed conflict.

- [ ] **Step 2: Verify the run remains permanently stalled**

```bash
pytest -q tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_operator_e2e.py -k 'foreground_owner or adopt'
```

- [ ] **Step 3: Implement fenced adoption**

Include expired foreground runs in coordinator recovery enumeration. Under the
run lock and one SQLite write transaction, verify current leader epoch and
expired foreground epoch, observe claims/processes, append
`foreground_execution_adopted` or reconciliation evidence, and update the
index. Remove `resume` from next actions unless it can invoke a real transition.

- [ ] **Step 4: Run coordinator/operator tests**

Use the Step 2 command plus `tests/plugins/workflow/test_coordinator.py`; expect
PASS.

- [ ] **Step 5: Commit exact files**

```bash
git add plugins/workflow/store.py plugins/workflow/coordinator.py \
  plugins/workflow/actions.py plugins/workflow/cli.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_operator_e2e.py
git commit -m "fix(workflow): adopt expired foreground owners"
```

### Task 9: Guarantee terminal and recovery journal affordability

**Findings:** M-08.

**Files:**

- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/models.py`
- Modify: `tests/plugins/workflow/test_fault_injection.py`
- Modify: `tests/plugins/workflow/test_loop_executor.py`
- Modify: `tests/plugins/workflow/test_resources.py`

**Interfaces:** Each claimed attempt owns a reserved terminal/recovery frame
budget that loop/progress events cannot consume. Worker claims release only
after terminal state is durably appended and indexed.

- [ ] **Step 1: Add quota-at-completion fault injection**

Run a loop until ordinary event quota is exhausted, then complete/fail it.
Assert one terminal or storage-recovery record is durable, the run is not
`running` with terminal nodes, and no second worker can claim uncertain work.

- [ ] **Step 2: Verify terminal append currently wedges**

```bash
pytest -q tests/plugins/workflow/test_fault_injection.py \
  tests/plugins/workflow/test_loop_executor.py -k 'terminal_reserve or journal_quota'
```

- [ ] **Step 3: Implement per-attempt reserve accounting**

Compute a conservative terminal/recovery reserve from bounded projection size;
reject loop/progress frames before they invade it. Do not place
`_release_worker_claim` in an unconditional `finally`. If terminal persistence
cannot consume its reserve, retain the claim, mark store repair-required in the
independent SQLite repair table, and refuse replay.

- [ ] **Step 4: Run quota/resource tests**

```bash
pytest -q tests/plugins/workflow/test_fault_injection.py \
  tests/plugins/workflow/test_loop_executor.py \
  tests/plugins/workflow/test_resources.py
```

- [ ] **Step 5: Commit exact files**

```bash
git add plugins/workflow/store.py plugins/workflow/models.py \
  tests/plugins/workflow/test_fault_injection.py \
  tests/plugins/workflow/test_loop_executor.py \
  tests/plugins/workflow/test_resources.py
git commit -m "fix(workflow): reserve terminal journal capacity"
```

### Task 10: Bound coordinator sweeps and implement real stall thresholds

**Findings:** M-04 and M-05.

**Files:**

- Modify: `plugins/workflow/models.py`
- Modify: `plugins/workflow/coordinator_store.py`
- Modify: `plugins/workflow/coordinator.py`
- Modify: `plugins/workflow/scheduler.py`
- Modify: `plugins/workflow/store.py`
- Modify: `tests/plugins/workflow/test_coordinator.py`
- Modify: `tests/plugins/workflow/test_performance_bounds.py`

**Interfaces:**

- Config: `plugins.entries.workflow.runtime.runnable_stall_seconds: 60` and
  `semantic_stall_seconds: 300`; lease must be at least three heartbeats.
- `RunScheduler.submit(run_id, fence) -> bool` is non-blocking and deduplicates
  active runs.
- Sweep cursor is `(created_at, run_id)` and advances through every eligible
  background run within a two-second sweep budget.

- [ ] **Step 1: Add >200-run, long-node, and clock-threshold tests**

Assert run 201 is eventually submitted, one long node does not block another
run's promotion, idle backoff increases when scans find no actionable work, and
stalled events occur only at exact 60/300-second boundaries using injected UTC
and monotonic clocks.

- [ ] **Step 2: Verify cursor and threshold failures**

```bash
pytest -q tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_performance_bounds.py -k 'cursor or head_of_line or stall_threshold or idle_backoff'
```

- [ ] **Step 3: Implement bounded enumeration and dispatch**

Query keyset pages directly from indexed `runs` rows. Process durable wakes
first. Submit work to a bounded scheduler pool without waiting for graph tails.
Persist the tuple cursor and wrap only after reaching the end. Define
`actionable_work` separately from `rows_seen` so idle backoff works. Evaluate
last runnable/semantic progress into a single deduplicated stalled transition.

- [ ] **Step 4: Run coordinator/performance tests**

Use the Step 2 command without `-k`; expected PASS and each bounded timing
assertion remains below its stated deadline.

- [ ] **Step 5: Commit exact files**

```bash
git add plugins/workflow/models.py plugins/workflow/coordinator_store.py \
  plugins/workflow/coordinator.py plugins/workflow/scheduler.py \
  plugins/workflow/store.py tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_performance_bounds.py
git commit -m "fix(workflow): bound coordinator sweeps and stalls"
```

### Task 11: Centralize lane, capacity, and FIFO runnable admission

**Findings:** M-06 and M-07.

**Files:**

- Modify: `plugins/workflow/schema.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/actions.py`
- Modify: `tests/plugins/workflow/test_admission.py`
- Modify: `tests/plugins/workflow/test_parallel_scheduler.py`
- Modify: `tests/plugins/workflow/test_retry.py`
- Modify: `tests/plugins/workflow/test_approval_races.py`

**Interfaces:** `RunStore.request_runnable(run_id, reason, expected_version)` is
the only paused/retry/resume/input/reconcile-to-runnable transition. It either
sets `running` or durable `queued` with an immutable FIFO sequence.

Sidecar policy accepts `pause_lane_policy: hold|release`; default is `hold`.
`waiting_retry` releases its lane. Interrupted runs with unresolved outward
attempts always hold regardless of sidecar policy.

- [ ] **Step 1: Add capacity and ordering races**

Resume/approve/provide-input concurrently at `max_executing_runs`; assert the
limit is never exceeded. Create older/newer queued runs and assert the older is
promoted first. Assert default paused outward run blocks a duplicate, explicit
safe release works, waiting retry releases, and uncertain interrupted holds.

- [ ] **Step 2: Verify direct `running` transitions oversubscribe**

```bash
pytest -q tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_parallel_scheduler.py \
  tests/plugins/workflow/test_approval_races.py -k 'capacity or fifo or lane_policy'
```

- [ ] **Step 3: Route all runnable transitions through one transaction**

Allocate a monotonically increasing queue sequence, check profile capacity and
lane ownership under `BEGIN IMMEDIATE`, and update journal/index consistently.
Remove direct `projection["status"] = "running"` from interaction/recovery
methods. Promotion orders by queue sequence, never newest-first list order.

- [ ] **Step 4: Run admission/scheduler/retry/approval tests**

```bash
pytest -q tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_parallel_scheduler.py \
  tests/plugins/workflow/test_retry.py \
  tests/plugins/workflow/test_approval_races.py
```

- [ ] **Step 5: Commit exact files**

```bash
git add plugins/workflow/schema.py plugins/workflow/store.py \
  plugins/workflow/actions.py tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_parallel_scheduler.py \
  tests/plugins/workflow/test_retry.py \
  tests/plugins/workflow/test_approval_races.py
git commit -m "fix(workflow): centralize runnable admission"
```

### Task 12: Prove Windows process-tree termination

**Findings:** M-12 and native portion of M-01.

**Files:**

- Modify: `tools/managed_process.py`
- Modify: `plugins/workflow/executors/base.py`
- Modify: `plugins/workflow/store.py`
- Modify: `tests/tools/test_managed_process.py`
- Modify: `tests/plugins/workflow/test_process_lifecycle_soak.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:** On Windows, every workflow process is assigned to a kill-on-
close Job Object. `termination_confirmed` requires job/tree quiescence; root-
only signaling is never sufficient.

- [ ] **Step 1: Add a native detached-grandchild test**

Spawn a parent that launches a detached grandchild and exits. Kill/recover the
owned job and assert both PIDs are gone before the store records confirmation.
Simulate unavailable `taskkill` and assert the result is uncertain, not true.

- [ ] **Step 2: Run portable tests and observe fallback overclaim**

```bash
pytest -q tests/tools/test_managed_process.py \
  tests/plugins/workflow/test_process_lifecycle_soak.py -k 'windows or descendant'
```

- [ ] **Step 3: Implement Job Object ownership**

Use ctypes/Win32 handles without introducing a required third-party runtime.
Set `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, assign immediately after spawn, retain
the handle for the owner, and treat assignment/query/termination failures as
`outcome_uncertain`. Keep existing POSIX process-group behavior unchanged.

- [ ] **Step 4: Run native Windows and POSIX tests**

Run the Step 2 command on Windows, Linux, and macOS. Expected: PASS; the Windows
test must not be skipped or marked `pragma: no cover`.

- [ ] **Step 5: Commit exact files**

Record the generic process-ownership change, immediate workflow consumer,
boundary tests, and upstream-equivalent removal condition in the downstream
ledger. Stage only the actual existing CI matrix file selected in Step 1 plus
listed source/tests/ledger, then:

```bash
git commit -m "fix(workflow): prove Windows process termination"
```

### Task 13: Enforce one plugin-service generation and restore safe reload

**Findings:** M-16 and lifecycle/resource parts of L-08/L-09.

**Files:**

- Modify: `hermes_cli/plugins.py`
- Modify: `hermes_cli/plugin_services.py`
- Modify: `hermes_cli/web_server.py`
- Modify: `gateway/run.py`
- Modify: `tools/tts_tool.py`
- Modify: `tools/transcription_tools.py`
- Modify: `tools/video_generation_tool.py`
- Modify: `tools/image_generation_tool.py`
- Modify: `tests/hermes_cli/test_plugin_background_services.py`
- Modify: `tests/hermes_cli/test_web_server.py`
- Modify: `tests/gateway/test_plugin_background_services.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:** `PluginManager.start_background_services(host_kind)` rejects or
returns the existing live generation; it never creates overlap. Production
configuration reload is host-owned and calls
`reload_background_services(timeout=10.0)` outside service/tool worker threads.

- [ ] **Step 1: Add same-kind overlap and provider-reload tests**

Start a wedged service, call start again, and assert no second factory/thread.
Change provider configuration in a web/gateway host and assert the host either
quiesces/reloads successfully or returns explicit `plugin_reload_blocked` while
the old provider and service generation remain usable.

- [ ] **Step 2: Verify overlap and silent provider failure**

```bash
pytest -q tests/hermes_cli/test_plugin_background_services.py \
  tests/hermes_cli/test_web_server.py \
  tests/gateway/test_plugin_background_services.py -k 'same_kind or provider_reload or dormancy'
```

- [ ] **Step 3: Implement host-owned reload routing**

Index active hosts by kind. A `stop_timeout` remains active until both run and
health threads terminate. Remove swallowed forced rescans from tool dispatch;
configuration mutation schedules a host-controller reload. CLI processes with
no bound host may still force discovery directly. Strengthen factory dormancy
to inspect the real workflow lease/database path and prove cached `health()`
returns within 100 ms without I/O.

- [ ] **Step 4: Run lifecycle host tests**

Use the Step 2 command without `-k`; expected PASS.

- [ ] **Step 5: Update ledger and commit exact files**

Record why each shared file changed, its generic contract, workflow consumer,
tests, and upstream removal condition. Stage only listed files and commit:

```bash
git commit -m "fix(plugins): prevent background service overlap"
```

### Task 14: Complete durable notification retry, retention, and repair

**Findings:** M-15 and notification resource portion of L-08.

**Files:**

- Modify: `plugins/workflow/notifications.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/coordinator.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `tests/plugins/workflow/test_notifications.py`
- Modify: `tests/plugins/workflow/test_notification_delivery.py`
- Modify: `tests/plugins/workflow/test_retention.py`

**Interfaces:**

- `NotificationOutbox.retry_dead(notification_id, authority_scope)` requeues a
  dead notification with an audit fact.
- History is newest-first keyset paginated.
- Retention prunes delivered/dismissed delivery rows only after policy age;
  transition facts remain queryable until explicit workflow cleanup.
- Reconciliation reads one bounded run page and only its candidate fact keys;
  it does not load the entire facts table every sweep.

- [ ] **Step 1: Add dead-letter, cleanup, and bounded-repair tests**

Fail delivery eight times, retry it through the authenticated API, ack it, and
assert cleanup is no longer permanently blocked. Insert more than 200 facts and
assert newest history is visible. Instrument journal/fact reads and assert one
repair tick respects its page/byte budget.

- [ ] **Step 2: Verify dead letters cannot recover**

```bash
pytest -q tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_notification_delivery.py \
  tests/plugins/workflow/test_retention.py -k 'dead or prune or newest or bounded_repair'
```

- [ ] **Step 3: Implement explicit retry and retention semantics**

Require workflow admin for dead-letter retry/prune. Preserve existing per-run,
kind, destination coalescing and Electron-ack delivery semantics. Move journal
repair to its own bounded cadence/cursor rather than every active sweep. Record
retry, prune, and terminal dead-letter decisions as immutable facts.

- [ ] **Step 4: Run notification/retention tests**

Use the Step 2 command without `-k`; expected PASS.

- [ ] **Step 5: Commit exact files**

```bash
git add plugins/workflow/notifications.py plugins/workflow/store.py \
  plugins/workflow/coordinator.py plugins/workflow/dashboard/plugin_api.py \
  tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_notification_delivery.py \
  tests/plugins/workflow/test_retention.py
git commit -m "fix(workflow): complete notification recovery"
```

### Task 15: Add generic authenticated plugin invocation and Gateway delivery

**Findings:** H-11/H-01; approved decision 1.

**Files:**

- Create: `hermes_cli/plugin_invocation.py`
- Create: `gateway/plugin_delivery.py`
- Modify: `hermes_cli/plugins.py`
- Modify: `hermes_cli/plugin_services.py`
- Modify: `gateway/run.py`
- Modify: `plugins/workflow/__init__.py`
- Create: `plugins/workflow/gateway_command.py`
- Modify: `plugins/workflow/coordinator.py`
- Modify: `plugins/workflow/notifications.py`
- Create: `tests/hermes_cli/test_authenticated_plugin_commands.py`
- Create: `tests/gateway/test_plugin_delivery.py`
- Modify: `tests/plugins/workflow/test_notification_delivery.py`
- Modify: `docs/upstream-customizations/workflow-orchestration.yaml`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class PluginInvocationContext:
    boundary: Literal["cli", "tui", "desktop", "gateway", "api"]
    principal: str
    operator_scope: str
    assurance: Literal["verified_adapter", "local_admin_claim"]
    return_route_capability: str | None

class PluginDeliveryPort(Protocol):
    def deliver(
        self, capability: str, text: str, idempotency_key: str
    ) -> DeliveryReceipt:
        raise NotImplementedError
```

Gateway mints an unguessable capability and stores only its digest plus the
normalized verified route, principal, profile, expiry, and delivery metadata in
a profile-local generic registry. The plugin/outbox sees only the opaque token.
The port resolves it server-side and performs bounded adapter delivery. It does
not own a competing queue.

- [ ] **Step 1: Add forgery, restart, and receipt-loss tests**

Assert a caller-supplied route string is rejected; a server-minted capability
survives Gateway restart; wrong principal/profile fails; duplicate delivery key
does not send twice after receipt loss; CLI/TUI contexts cannot mint verified
routes; workflow start/gate commands receive authenticated Gateway provenance.

- [ ] **Step 2: Run tests and verify no generic contract exists**

```bash
pytest -q tests/hermes_cli/test_authenticated_plugin_commands.py \
  tests/gateway/test_plugin_delivery.py \
  tests/plugins/workflow/test_notification_delivery.py
```

Expected: collection/import failures for the new interfaces.

- [ ] **Step 3: Implement the narrow generic surfaces**

Add `PluginContext.register_authenticated_command` for two-argument handlers;
leave existing one-argument commands unchanged. Pass only verified invocation
facts. Add the optional delivery port to Gateway-hosted background-service
context; web hosts receive none. The workflow plugin registers the immediate
command/service consumer, persists capability separately from start digest,
and projects outbox rows through the port.

- [ ] **Step 4: Run generic and workflow integration tests**

Use the Step 2 command plus lifecycle tests from Task 13. Expected: PASS with no
workflow import in any generic host file.

- [ ] **Step 5: Update ledger and commit exact files**

Stage only listed files and commit:

```bash
git commit -m "feat(plugins): add authenticated gateway delivery"
```

### Task 16: Add authenticated background-only REST admission

**Findings:** H-10/H-02; approved decision 2.

**Files:**

- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Create: `plugins/workflow/api_admission.py`
- Modify: `plugins/workflow/provenance.py`
- Modify: `tests/plugins/workflow/test_desktop_api.py`
- Modify: `tests/plugins/workflow/test_operator_e2e.py`
- Modify: `tests/hermes_cli/test_workflow_dashboard_auth.py`

**Interfaces:** `POST /api/plugins/workflow/runs` accepts a catalog workflow
name, bounded input values, required idempotency key, and supported concurrency
policy. It always requests background execution. Source, principal, namespace,
scope, source instance, assurance, and return route come from the authenticated
server context; caller source headers are ignored/rejected.

- [ ] **Step 1: Add real middleware admission tests**

Test read denied, write allowed, no key rejected, no coordinator returns 503
`coordinator_unavailable` with no run directory, same request joins existing,
changed inputs conflict, and forged source/principal/channel headers have no
effect. On existing approve/reject/provide-input routes, assert the durable
actor and channel come from the authenticated server context. Assert admission
returns before a blocking node advances.

- [ ] **Step 2: Verify route absence**

```bash
pytest -q tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_operator_e2e.py \
  tests/hermes_cli/test_workflow_dashboard_auth.py -k 'api_admission or post_runs'
```

Expected: 404 for `POST /runs`.

- [ ] **Step 3: Implement admission adapter only**

Resolve packages through the existing catalog/trust/preflight path, prepare
immutable inputs, call `RunStore.start_run`, persist verified API provenance,
record a wake, and return a stable envelope. Replace the existing mutation
adapter's hardcoded `channel="desktop"` and missing actor with values derived
from the same authenticated authority; never accept either from request
headers. Do not import or instantiate `RunScheduler`; do not call `advance`.

- [ ] **Step 4: Run API/operator tests**

Use the Step 2 command without `-k`; expected PASS.

- [ ] **Step 5: Commit exact files**

```bash
git add plugins/workflow/dashboard/plugin_api.py \
  plugins/workflow/api_admission.py plugins/workflow/provenance.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_operator_e2e.py \
  tests/hermes_cli/test_workflow_dashboard_auth.py
git commit -m "feat(workflow): add authenticated API admission"
```

### Task 17: Complete Desktop attention, pagination, and resilient actions

**Findings:** H-08, H-09, L-07, and Desktop portions of L-09.

**Files:**

- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `apps/desktop/src/hermes.ts`
- Modify: `apps/desktop/src/hermes.test.ts`
- Modify: `apps/desktop/src/types/hermes.ts`
- Modify: `apps/desktop/src/app/workflows/attention-inbox.tsx`
- Modify: `apps/desktop/src/app/workflows/adapter.ts`
- Modify: `apps/desktop/src/app/workflows/index.tsx`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx`
- Modify: `apps/desktop/src/app/workflows/store.ts`
- Modify: `apps/desktop/src/app/workflows/workflow-operations.e2e.test.tsx`
- Modify: `apps/desktop/src/i18n/types.ts`
- Modify: `apps/desktop/src/i18n/en.ts`
- Modify: `apps/desktop/src/i18n/ja.ts`
- Modify: `apps/desktop/src/i18n/zh.ts`
- Modify: `apps/desktop/src/i18n/zh-hant.ts`

**Interfaces:** Run listing/filtering is SQL keyset-paginated before limit.
Attention returns item-level action metadata. A timeline request error degrades
only timeline; it does not disable authoritative run mutations. Hidden/closed
inspectors release long-poll permits.

- [ ] **Step 1: Add >200 and actionable-attention tests**

Seed 250 recent board runs plus older history/archive runs and traverse every
page without gaps/duplicates. Render approval, input, stalled, failure, and
reconciliation attention rows with origin, age, cause, and valid action. Force
events 429/500 and assert Approve/Cancel remain available from current run
state. Add `aria-busy`, translated labels, and laptop-width coverage.

- [ ] **Step 2: Verify truncation and count-only UI**

```bash
pytest -q tests/plugins/workflow/test_run_queries.py tests/plugins/workflow/test_desktop_api.py -k 'pagination or attention'
cd apps/desktop && npx vitest run src/app/workflows/index.test.tsx \
  src/app/workflows/workflow-operations.e2e.test.tsx
```

- [ ] **Step 3: Implement authoritative pagination and UI isolation**

Move board/history/archive predicates into indexed SQL and sign cursors over
view/scope/filter/keyset. Render attention rows rather than a count. Derive
action disabled state from run mutation/query state, not event-query error.
Cancel/disable long polls when inspector visibility or selected run changes.
Replace the adapter-only operations test with a mounted `WorkflowsView` flow
that exercises query results, mutation construction, conflict refresh, and
authoritative repaint through the real `hermes.ts` adapter. Remove dead
`WORKFLOW_NODE_COLUMNS` and `$workflowAttentionFirst` exports after confirming
there are no runtime consumers.

- [ ] **Step 4: Run Python, Vitest, typecheck, and scoped lint**

```bash
pytest -q tests/plugins/workflow/test_run_queries.py tests/plugins/workflow/test_desktop_api.py
cd apps/desktop && npx vitest run src/app/workflows && npm run typecheck
```

Run ESLint only on the task-owned Desktop files and require zero new errors.

- [ ] **Step 5: Commit exact files**

Stage only the files actually modified by the commands above and commit:

```bash
git commit -m "feat(desktop): complete workflow attention and history"
```

### Task 18: Finish CLI envelopes, typed failures, and bounded resource hygiene

**Findings:** L-01, L-02, L-03, remaining L-05, L-08, and remaining L-09.

**Files:**

- Modify: `plugins/workflow/cli.py`
- Modify: `plugins/workflow/machine_contract.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/locks.py`
- Modify: `plugins/workflow/coordinator_store.py`
- Modify: `tests/plugins/workflow/test_cli.py`
- Modify: `tests/skills/test_workflow_operator_behavior.py`
- Modify: `tests/plugins/workflow/test_performance_bounds.py`
- Modify: `tests/plugins/workflow/test_process_lifecycle_soak.py`

**Interfaces:** All `--json` parser, validation, domain, and internal failures
emit one schema-versioned stdout envelope and a documented table exit code.
Domain exceptions are typed, never classified by message substrings. CLI status,
runs, and events share `sanitize_projection` and report truncation explicitly.

- [ ] **Step 1: Add parser/exception/sanitizer/resource tests**

Cover unknown flags under `--json`, no-arg `KeyError`, internal lookup errors,
typed CAS conflict, off-table exit 1, digest omission, truncation marker,
10,000 coordinator events/wakes, and 10,000 run-lock paths. Assert stable exit
code documentation is present in `operator_command_contract`.

- [ ] **Step 2: Verify plaintext stderr and unbounded state**

```bash
pytest -q tests/plugins/workflow/test_cli.py \
  tests/skills/test_workflow_operator_behavior.py \
  tests/plugins/workflow/test_performance_bounds.py \
  tests/plugins/workflow/test_process_lifecycle_soak.py -k 'argparse or typed or sanitize or prune or lock_registry'
```

- [ ] **Step 3: Implement typed machine and resource contracts**

Override parser error handling when machine mode is selected; map
`WorkflowNotFound`, `WorkflowConflict`, `WorkflowAuthorization`,
`CoordinatorUnavailable`, and `WorkflowActionFailed` by type. Sanitize every
machine payload and add `truncated`/`next_cursor`. Prune only
completed/consumed wakes and expired diagnostic events by retention; never
prune an unprocessed wake. Use a ref-counted process-lock registry and evict
only entries with zero owners and zero waiters.

- [ ] **Step 4: Run CLI, skill, performance, and soak tests**

Use the Step 2 command without `-k`; expected PASS.

- [ ] **Step 5: Commit exact files**

```bash
git add plugins/workflow/cli.py plugins/workflow/machine_contract.py \
  plugins/workflow/store.py plugins/workflow/locks.py \
  plugins/workflow/coordinator_store.py tests/plugins/workflow/test_cli.py \
  tests/skills/test_workflow_operator_behavior.py \
  tests/plugins/workflow/test_performance_bounds.py \
  tests/plugins/workflow/test_process_lifecycle_soak.py
git commit -m "fix(workflow): finish machine and resource contracts"
```

### Task 19: Execute native, packaging, update, lint, and adversarial gates

**Findings:** M-01, M-02, M-03, and release blockers 6/16/17.

**Files:**

- Modify only if evidence requires correction: `scripts/test_workflow_merge_gate.sh`
- Modify only if evidence requires correction: `tests/scripts/test_workflow_merge_gate.py`
- Create: `docs/reviews/2026-07-18-workflow-orchestration-production-remediation-verification.md`
- Modify: `docs/reviews/2026-07-18-workflow-orchestration-production-implementation-adversarial-review.md`

**Interfaces:** No new runtime interface. This task produces immutable evidence
for merge and separates branch-owned lint from the unrelated Desktop baseline.

- [ ] **Step 1: Run the complete local merge gate**

```bash
scripts/test_workflow_merge_gate.sh
git diff --check
```

Expected: every branch-owned test/typecheck/scoped-lint/package check passes.

- [ ] **Step 2: Run native CI matrix**

Require Linux, macOS, and native Windows jobs to execute SQLite locking,
atomic replace, process identity/tree termination, coordinator takeover,
foreground adoption, migration fixture, notification restart, and cleanup.
No workflow-native test may be skipped merely because the runner is Windows.

- [ ] **Step 3: Rehearse install, update, and rollback**

Build wheel and sdist outside the repository; install with dependencies into an
empty environment and Hermes home; run CLI/API/Gateway/foreground smoke tests;
upgrade from the pinned pre-amendment version; rollback; upgrade again; compare
run/evidence hashes and idempotent lookup throughout.

- [ ] **Step 4: Run surface UAT and soak**

Exercise CLI human/JSON, Desktop, Gateway, cron, background agent, chat skill,
direct API, host restart, coordinator loss/takeover, >200-run sweep/history,
lease expiry, retry wake, interaction continuation, queued promotion,
notification delivery/dead-letter, archive/restore, cleanup, and evidence. Run
the resource soak long enough to cross notification/coordinator retention
windows.

- [ ] **Step 5: Record evidence and commission fresh adversarial review**

The verification document records commands, commit, OS/runtime versions,
terminal results, skipped tests, artifacts, and unresolved failures. Update the
implementation review's blocker table: no item is complete without current
evidence. A fresh review must report no Critical or High merge blockers.

- [ ] **Step 6: Commit exact review/evidence files**

```bash
git add docs/reviews/2026-07-18-workflow-orchestration-production-remediation-verification.md \
  docs/reviews/2026-07-18-workflow-orchestration-production-implementation-adversarial-review.md
git commit -m "docs(workflow): record remediation verification"
```

Do not merge, tag, or release in this task. Stop for maintainer review.

## Finding-to-task traceability

| Finding | Task | Red evidence | Completion evidence |
|---|---:|---|---|
| C-01 | 1 | read-token mutation succeeds | real middleware capability matrix |
| C-02 | 2 | outside sentinel returned | symlink/reparse/race refusal |
| H-03 | 6 | stale epoch dispatches | mid-node takeover fencing |
| H-04 | 6 | old shutdown interrupts successor | exact-owner shutdown test |
| H-05 | 8 | foreground run remains stalled | owner-death adoption/reconcile |
| H-06 | 4 | PID retry conflicts | separate-process existing result |
| H-07 | 4 | route/actor changes digest | semantic/audit identity tests |
| H-08 | 17 | count-only attention | item/action UI tests |
| H-09 | 17 | history/archive hidden after 200 | complete keyset traversal |
| H-10/H-02 | 1, 4, 16 | no route/hardcoded channel | authenticated background admission |
| H-11/H-01 | 13-15 | Desktop-only destination | opaque Gateway delivery UAT |
| M-04 | 10 | long node blocks/run 201 unseen | bounded cursor sweep |
| M-05 | 10 | immediate stalled | exact threshold clock tests |
| M-06 | 11 | hardcoded lane hold | explicit safe lane policy |
| M-07 | 11 | interaction oversubscribes | centralized FIFO capacity test |
| M-08 | 9 | quota wedges completion | reserved terminal/recovery frame |
| M-09 | 7 | abandon drops live claim | live-claim refusal test |
| M-10 | 7 | identityless spawn marked safe | spawn-intent crash test |
| M-11 | 7 | healthy load leaves stale status | same-process index resync |
| M-12 | 12 | root-only Windows proof | native Job Object descendant test |
| M-13 | 3 | null ID reuses prior decision | exact two-gate identity test |
| M-14 | 5 | showcase random key/input omission | machine gate/preflight test |
| M-15 | 14-15 | dead forever/unbounded repair | retry/prune/Gateway receipt tests |
| M-16 | 13 | same-kind overlap/reload failure | no-overlap production reload tests |
| L-01 | 18 | argparse plaintext/off-table 1 | enveloped parser/exit table |
| L-02 | 18 | no-arg/internal KeyError escape | typed not-found tests |
| L-03 | 18 | substring classification | typed domain exceptions |
| L-04 | 4 | absent trigger shown as CLI | legacy unknown projection test |
| L-05 | 2, 18 | inconsistent/unsanitized output | shared sanitizer/truncation tests |
| L-06 | 5 | showcase mutates trust | trust bytes unchanged |
| L-07 | 17 | event error disables all actions | query-failure isolation test |
| L-08 | 13, 14, 18 | unbounded registries/polling | retention and soak assertions |
| L-09 | 13, 17 | vacuous tests/a11y/dead exports | real-path lifecycle/UI gate |
| Prior M-01 | 12, 19 | no native Linux/Windows evidence | required native matrix |
| Prior M-02 | 19 | incomplete update/rollback | recorded rehearsal |
| Prior M-03 | 17, 19 | repository lint baseline red | scoped no-regression evidence |

## Per-task review checklist

- [ ] The named finding has a failing test that fails for the reported reason.
- [ ] The implementation fixes the whole sibling-path class, not one caller.
- [ ] Durable authority, owner, CAS/fence, and recovery state are explicit.
- [ ] No client string becomes authenticated provenance or a delivery route.
- [ ] No claim is released before durable terminal/recovery evidence.
- [ ] No shared Hermes file imports workflow code.
- [ ] Shared changes update the downstream ledger and removal condition.
- [ ] Tests use real boundaries in addition to focused unit tests.
- [ ] Exact files only are staged; unrelated work remains untouched.
- [ ] The task commit is independently reviewable and revertible.

## Final completion conditions

This remediation is complete only when all nineteen tasks are checked, the
finding matrix has no unowned row, blockers 1-17 have current evidence, native
platform and update/rollback gates pass, and a fresh adversarial review reports
no Critical or High merge blocker. Completion authorizes maintainer merge
review only; it does not authorize release, tagging, or deployment.
