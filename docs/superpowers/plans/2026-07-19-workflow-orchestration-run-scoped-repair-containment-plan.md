# Workflow Orchestration Run-Scoped Repair Containment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Contain run-local evidence failures to the damaged run, keep notification-repair lock contention from aborting coordinator work, and execute native evidence-containment tests in the three-OS release matrix.

**Architecture:** Reuse the append-only `repair_events` table as a per-run state-transition log, deriving active reasons from the latest transition without adding schema. Six damage-scoped read sites—including both nested `load_run` corroboration catches and the published-run admission reconciliation catch—record or resolve only their own run-scoped reason, while index/generation, cross-run, claim, and journal-reserve global-marker writers remain unchanged. Notification-repair lock timeouts retain the durable cursor and return normally, with a bounded process-local diagnostic streak on `RunStore`.

**Tech Stack:** Python 3.11, SQLite, pytest, FastAPI real middleware, filesystem advisory locks, GitHub Actions YAML, Bash merge gate.

## Global Constraints

- Work inline in the existing isolated worktree; do not delegate or spawn subagents.
- Use strict TDD: add and run each focused failing test before production changes.
- Use `apply_patch` for every source, test, workflow, and documentation edit.
- Workflow behavior remains plugin-owned; generic host/lifecycle code gains no workflow import.
- Keep strict descriptor-contained notification journal reads; do not recover or rewrite torn tails in the scanner.
- Never set the global repair marker for damage confined to one run at the six governed read/scan sites.
- Never delete damaged evidence; damaged-run cleanup remains fail-closed.
- Never advance the notification repair cursor beyond a contended run.
- Gateway delivery and scheduler submission must continue in the same sweep after repair lock contention.
- Do not add a schema migration, model-facing tool, user-facing `HERMES_*` variable, or synchronous workflow tail.
- Preserve the two reviewer-owned files already present in the worktree; never stage or edit them.
- Stage only the exact files owned by the current task. Do not merge, tag, release, or push.
- Before each commit, run fresh focused verification, `git diff --check`, and an exact staged-file audit.

---

## File map

- `plugins/workflow/store.py` — owns append-only run-repair transitions, active-reason lookup, list/status degradation, attention selection, and timeout-streak state.
- `plugins/workflow/notifications.py` — records/resolves notification reconciliation reasons and converts `WorkflowLockTimeout` into cursor-retaining cadence completion.
- `plugins/workflow/dashboard/plugin_api.py` — maps damaged-run reads to a typed conflict before mutation dispatch.
- `tests/plugins/workflow/test_notifications.py` — strict torn-tail, repair-resolution, cursor-retention, retry, and timeout-diagnostic failure injection.
- `tests/plugins/workflow/test_retention.py` — damaged-run cleanup containment plus unrelated cleanup/admission availability.
- `tests/plugins/workflow/test_desktop_api.py` — real middleware proof that an active run-scoped reason is visible in the operator attention surface.
- `tests/plugins/workflow/test_coordinator.py` — real lock plus fenced-sweep proof that Gateway drain and scheduler submission still run.
- `.github/workflows/ci.yml` — exact three-OS workflow portability selection.
- `tests/scripts/test_workflow_merge_gate.py` — meta-test pin requiring the native evidence test in CI.
- `docs/reviews/2026-07-19-workflow-orchestration-operator-robustness-verification.md` — corrected historical matrix statement and fresh follow-up evidence.

---

### Task 1: Contain run-scoped read damage and repair-lock contention

**Files:**
- Modify: `plugins/workflow/store.py:301-355, 1094-1148, 3587-3780`
- Modify: `plugins/workflow/notifications.py:125-145, 534-635`
- Modify: `plugins/workflow/dashboard/plugin_api.py:34, 257-265`
- Modify: `tests/plugins/workflow/test_notifications.py`
- Modify: `tests/plugins/workflow/test_retention.py`
- Modify: `tests/plugins/workflow/test_desktop_api.py`
- Modify: `tests/plugins/workflow/test_coordinator.py`

**Interfaces:**
- Consumes: `RunStore._record_repair_event(connection, *, reason_code, outcome, run_id, ...)`, `RunStore.attention_candidates(...)`, `NotificationOutbox.reconcile_journal(...)`, `WorkflowCoordinatorService._sweep_once(...)`.
- Produces: `RunStore._transition_run_repair(reason_code: str, *, run_id: str, outcome: str) -> bool`, `RunStore._active_run_repair_reasons(run_id: str) -> tuple[str, ...]`, `RunStore._note_notification_repair_timeout(run_id: str) -> int`, and `RunStore._clear_notification_repair_timeout(run_id: str) -> None`.

#### Slice 1.1: run-scoped state and six-site containment

- [ ] **Step 1: Add a strict-scanner torn-tail failure test**

Add a test to `tests/plugins/workflow/test_notifications.py` that creates a terminal background run with a missing outbox projection, appends a partial JSON frame to `events.jsonl`, invokes `reconcile_journal(limit_runs=1)`, and inspects real SQLite state:

```python
def _terminal_background_failure(tmp_path, workflow_writer, *, name: str):
    store = RunStore(tmp_path / "home")
    now = datetime.now(timezone.utc)
    identity = CoordinatorIdentity(
        owner_id=f"{name}-owner",
        host_kind="web",
        host_instance_id=f"{name}-host",
        pid=1,
        process_start_time=None,
    )
    leadership = CoordinatorStore(store.database).try_acquire(
        identity, now=now, lease_seconds=60
    )
    assert leadership.is_leader
    package = load_workflow(
        workflow_writer(
            tmp_path / "package",
            name=name,
            nodes=[{"id": "fail", "bash": "exit 7"}],
        )
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="api",
            idempotency_key=name,
            concurrency_key=name,
            execution_mode="background",
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(
        store,
        owner_id=f"coordinator:{identity.owner_id}:{leadership.lease.epoch}",
        execution_fence=ExecutionFence(identity.owner_id, leadership.lease.epoch),
    ).advance(admitted.run_id)
    return store, admitted.run_id


def test_torn_tail_repair_is_run_scoped_visible_and_later_verified(
    tmp_path, workflow_writer
) -> None:
    store, run_id = _terminal_background_failure(
        tmp_path, workflow_writer, name="run-scoped-torn-tail"
    )
    outbox = NotificationOutbox(store)
    with store._connect() as connection:
        connection.execute(
            "DELETE FROM workflow_notification_facts WHERE run_id=?", (run_id,)
        )
        connection.execute(
            "DELETE FROM workflow_notification_outbox WHERE run_id=?", (run_id,)
        )
    journal = store.run_directory(run_id) / "events.jsonl"
    with journal.open("ab") as stream:
        stream.write(b'{"sequence":999')

    assert outbox.reconcile_journal(limit_runs=1) == 0
    assert store.storage_health() == {"status": "healthy", "reasons": []}
    assert not store.repair_marker.exists()
    assert store._active_run_repair_reasons(run_id) == (
        "notification_reconciliation_unverified",
    )

    store.get_run_status(run_id)  # ordinary reader heals the torn suffix
    assert outbox.reconcile_journal(limit_runs=1) == 1
    assert store._active_run_repair_reasons(run_id) == ()
    assert store.list_repair_events()[-1]["outcome"] == "repair_verified"
```

Reuse this helper from `test_oversized_first_journal_is_repaired` if doing so removes duplicated setup without changing that test's assertions.

- [ ] **Step 2: Add the standing all-entry-surface damage/visibility/cleanup test**

First extend the existing `_terminal_run` test helper with a nullable scope and pass it into `RunAdmissionRequest`:

```python
def _terminal_run(store, tmp_path, workflow_writer, *, name: str, scope=None):
    package = load_workflow(workflow_writer(tmp_path / name, name=name))
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=name,
            concurrency_key=name,
            operator_scope=scope,
        ),
        immutable_snapshot=prepared,
    )
    RunScheduler(store).advance(admitted.run_id)
    return admitted.run_id
```

Then add a direct store test to `tests/plugins/workflow/test_retention.py` using two operator scopes and genuine mid-file corruption. It is the standing completeness proof and must exercise direct status, list, attention, evidence read, an unrelated admission, unrelated cleanup preview and execution, and notification repair while checking `storage_health()` after every entry surface. The first unrelated admission must detect and preserve the damaged published run once; a second admission must skip its active run-scoped repair state without rereading the full journal. Repairing the journal through the existing path must clear the active run-scoped state without deleting evidence.

```python
def test_run_read_damage_is_contained_while_unrelated_cleanup_and_admission_work(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path / "home")
    damaged = _terminal_run(
        store, tmp_path, workflow_writer, name="damaged", scope="scope-damaged"
    )
    clean = _terminal_run(
        store, tmp_path, workflow_writer, name="clean", scope="scope-clean"
    )
    journal = store.run_directory(damaged, operator_scope="scope-damaged") / "events.jsonl"
    original_journal = journal.read_bytes()
    frames = original_journal.splitlines(keepends=True)
    journal.write_bytes(frames[0] + b"{not-json}\n" + b"".join(frames[1:]))

    damaged_list = store.list_runs(operator_scope="scope-damaged")
    assert damaged_list[0]["blocking_reason"] == "run_evidence_uncorroborated"
    assert store.storage_health() == {"status": "healthy", "reasons": []}
    assert not store.repair_marker.exists()

    attention = store.attention_candidates(
        operator_scope="scope-damaged",
        observed_at=datetime.now(timezone.utc),
        limit=10,
    )
    assert attention[0]["warnings"] == ["run_evidence_uncorroborated"]
    damaged_preview = store.cleanup_runs(
        older_than=timedelta(0), operator_scope="scope-damaged"
    )
    assert damaged_preview["confirmation_token"] is None
    assert "notification_reconciliation_unverified" in damaged_preview[
        "candidates"
    ][0]["blocked_reasons"]

    clean_preview = store.cleanup_runs(
        older_than=timedelta(0), operator_scope="scope-clean"
    )
    assert clean_preview["confirmation_token"]
    assert clean in clean_preview["run_ids"]
    package = load_workflow(
        workflow_writer(tmp_path / "new-valid", name="new-valid")
    )
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name="new-valid",
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key="new-valid",
            concurrency_key="new-valid",
            operator_scope="scope-clean",
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id

    journal.write_bytes(original_journal)
    restored = store.list_runs(operator_scope="scope-damaged")
    assert restored[0]["health"] != "storage_degraded"
    assert store._active_run_repair_reasons(damaged) == ()
```

Add `from plugins.workflow.notifications import NotificationOutbox` to `tests/plugins/workflow/test_desktop_api.py`, then add a real middleware test. Create a strict scanner failure, request `/api/plugins/workflow/attention`, and assert the public item is explicit:

```python
def test_attention_surfaces_run_scoped_notification_repair_damage(
    tmp_path, monkeypatch, workflow_writer
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    store = RunStore(home)
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="repair-attention")
    )
    admitted = _start(store, package, "repair-attention")
    journal = store.run_directory(admitted.run_id) / "events.jsonl"
    with journal.open("ab") as stream:
        stream.write(b'{"sequence":999')
    assert NotificationOutbox(store).reconcile_journal(limit_runs=1) == 0

    response = TestClient(_app(_router())).get(
        "/api/plugins/workflow/attention?limit=10"
    )

    assert response.status_code == 200
    item = next(
        item
        for item in response.json()["items"]
        if item["run_id"] == admitted.run_id
    )
    assert item["kind"] == "stalled"
    assert item["health"] == "storage_degraded"
    assert item["cause"] == "notification_reconciliation_unverified"
    assert "events.jsonl" not in response.text
```

Add a second real-middleware test with an approval-paused run. Save its journal bytes, insert a malformed complete middle frame, remove `run.json` to force corroborating replay, then attempt the otherwise-valid approval:

```python
response = TestClient(_app(_router())).post(
    f"/api/plugins/workflow/runs/{run_id}/approve",
    json={
        "expected_version": state_version,
        "interaction_id": interaction_id,
        "comment": "approved",
    },
)
assert response.status_code == 409
assert response.json()["detail"]["code"] == "run_evidence_uncorroborated"
assert journal.read_bytes() == corrupted_journal
assert store.storage_health() == {"status": "healthy", "reasons": []}
assert store._active_run_repair_reasons(run_id) == (
    "run_evidence_uncorroborated",
)
```

This request must fail in `_load_authorized` before `store.approve_run` can append a decision. The test uses a real FastAPI/TestClient route and real SQLite/filesystem evidence.

- [ ] **Step 3: Run the NR-1 tests red**

Run:

```bash
python -m pytest -q \
  tests/plugins/workflow/test_notifications.py::test_torn_tail_repair_is_run_scoped_visible_and_later_verified \
  tests/plugins/workflow/test_retention.py::test_run_read_damage_is_contained_while_unrelated_cleanup_and_admission_work \
  tests/plugins/workflow/test_desktop_api.py::test_attention_surfaces_run_scoped_notification_repair_damage \
  tests/plugins/workflow/test_desktop_api.py::test_corrupted_run_rejects_mutation_with_typed_error
```

Expected: all new tests fail for the reviewed defect class: the global marker is created, no active-reason helper exists, the damaged terminal run is absent from attention, and/or the mutation returns an untyped server error. No failure may be caused by fixture setup or import errors before production work starts.

- [ ] **Step 4: Add append-only run-repair state helpers**

In `RunStore`, initialize the diagnostic state alongside the existing locks:

```python
self._notification_repair_timeout_lock = threading.Lock()
self._notification_repair_timeout_run_id: str | None = None
self._notification_repair_timeout_count = 0
```

Add state helpers beside `_record_repair_event`:

```python
_RUN_SCOPED_REPAIR_REASONS = frozenset(
    {"notification_reconciliation_unverified", "run_evidence_uncorroborated"}
)

def _transition_run_repair(
    self, reason_code: str, *, run_id: str, outcome: str
) -> bool:
    if reason_code not in _RUN_SCOPED_REPAIR_REASONS:
        raise ValueError("reason_code is not run-scoped")
    if outcome not in {"repair_required", "repair_verified"}:
        raise ValueError("invalid run repair outcome")
    with self._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        latest = connection.execute(
            "SELECT outcome FROM repair_events WHERE run_id=? AND reason_code=? "
            "ORDER BY sequence DESC LIMIT 1",
            (run_id, reason_code),
        ).fetchone()
        if latest is not None and str(latest["outcome"]) == outcome:
            return False
        if outcome == "repair_verified" and latest is None:
            return False
        self._record_repair_event(
            connection,
            reason_code=reason_code,
            outcome=outcome,
            run_id=run_id,
        )
    return True

def _active_run_repair_reasons(self, run_id: str) -> tuple[str, ...]:
    with self._connect() as connection:
        rows = connection.execute(
            "SELECT events.reason_code FROM repair_events AS events "
            "WHERE events.run_id=? AND events.reason_code IN (?, ?) "
            "AND events.sequence=(SELECT MAX(latest.sequence) FROM repair_events "
            "AS latest WHERE latest.run_id=events.run_id "
            "AND latest.reason_code=events.reason_code) "
            "AND events.outcome='repair_required' ORDER BY events.reason_code",
            (
                run_id,
                "notification_reconciliation_unverified",
                "run_evidence_uncorroborated",
            ),
        ).fetchall()
    return tuple(str(row["reason_code"]) for row in rows)
```

Keep `_mark_repair_required` unchanged for all existing store-global callers.

- [ ] **Step 5: Apply the helper at exactly the six governed sites**

In `NotificationOutbox.reconcile_journal`, replace the global-marker branch with:

```python
except NotificationReconciliationError:
    run_id = str(row["run_id"])
    self.store._transition_run_repair(
        "notification_reconciliation_unverified",
        run_id=run_id,
        outcome="repair_required",
    )
    processed_rows.append(row)
    continue
```

After a successful `_journal_candidates` call, resolve only the scanner-owned reason:

```python
self.store._transition_run_repair(
    "notification_reconciliation_unverified",
    run_id=str(row["run_id"]),
    outcome="repair_verified",
)
```

In both `RunStore.list_runs` and `RunStore.attention_candidates`, replace `_mark_repair_required("run_evidence_uncorroborated", ...)` with `_transition_run_repair(..., outcome="repair_required")`. Set the degraded result's `blocking_reason` to `run_evidence_uncorroborated`, not `storage_repair_required`.

In the two `RunStore.load_run` catches around `_journal_matches_projection` and `_rebuild_projection`, replace only the `run_evidence_uncorroborated` global-marker write with `_transition_run_repair(..., outcome="repair_required")`. Those two operations inspect one resolved run directory; they do not diagnose index/generation or cross-run integrity. Leave every index/generation, cross-run inconsistency, claim-retention/reconciliation, and journal-reserve `_mark_repair_required` caller unchanged. On either successful corroboration path, append `repair_verified` before returning the projection.

In the published-run evidence-corruption catch in `RunStore._reconcile_admission`, record active `run_evidence_uncorroborated` state instead of setting the global marker. Preserve the existing `published_evidence_uncorroborated/evidence_preserved` audit event on the first transition. Load active run-scoped repair IDs once per admission sweep and skip a marked run before journal corroboration on later sweeps, so unrelated starts neither reread the damaged journal nor append duplicate repair events. Keep the adjacent `index_status_inconsistent`, generation, orphan/index, claim, and journal-reserve global-marker branches unchanged.

A successful status read resolves only `run_evidence_uncorroborated`:

```python
self._transition_run_repair(
    "run_evidence_uncorroborated",
    run_id=str(row["run_id"]),
    outcome="repair_verified",
)
```

Extend the attention SQL predicate with a correlated latest-transition `EXISTS` clause for active run-scoped reasons. Before returning each result, overlay an active `notification_reconciliation_unverified` reason on an otherwise healthy projection:

```python
active_reasons = self._active_run_repair_reasons(str(row["run_id"]))
if "notification_reconciliation_unverified" in active_reasons:
    result = {
        **result,
        "status_authoritative": False,
        "health": "storage_degraded",
        "blocking_reason": "notification_reconciliation_unverified",
        "next_actions": [],
        "warnings": ["notification_reconciliation_unverified"],
    }
```

If a repair-only `run_evidence_uncorroborated` reason is successfully resolved during that attention call and the run does not meet an ordinary attention state, omit it rather than emitting a stale item. Preserve keyset ordering and the `limit + 1` bound.

Import `JournalRecoveryError` beside `RunStore` in `plugins/workflow/dashboard/plugin_api.py`. In `_load_authorized`, catch it before the existing not-found mapping and raise a typed conflict:

```python
except JournalRecoveryError as exc:
    raise HTTPException(
        status_code=409,
        detail={"code": "run_evidence_uncorroborated"},
    ) from exc
```

This mapping applies equally to reads and the mutation preflight; it does not catch index/generation health failures or authorize the underlying mutation.

- [ ] **Step 6: Run the NR-1 tests green and audit global callers**

Run the Step 3 command again. Expected: `4 passed`.

Then run:

```bash
rg -n "_mark_repair_required\(" plugins/workflow/store.py plugins/workflow/notifications.py
python -m pytest -q \
  tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_retention.py \
  tests/plugins/workflow/test_desktop_api.py
```

Expected: the scanner and the two reviewed store read catches no longer appear in the grep output; all remaining callers match the approved store-level list. The complete notification, retention, and real-middleware suites pass.

The design record's complete classification table must match every remaining production `_mark_repair_required` caller. The standing real-corruption test must keep the store healthy across every listed entry surface, keep the damaged run visible and fail-closed, prove unrelated admission and cleanup remain available, prove second admission avoids rereading the damaged journal, and prove successful repair clears the run-scoped state.

#### Slice 1.2: contention retention, diagnostics, and same-sweep liveness

- [ ] **Step 7: Add a real held-lock retry and diagnostic test**

In `tests/plugins/workflow/test_notifications.py`, add `import logging`, `import threading`, and `from plugins.workflow.locks import workflow_lock`. Hold `workflow_lock(store._run_lock_path(run_id))` in a worker thread, synchronized with `threading.Event`. Call `reconcile_journal(limit_runs=1)` three times under `caplog.at_level(logging.WARNING)`, then release and retry:

```python
assert [outbox.reconcile_journal(limit_runs=1) for _ in range(3)] == [0, 0, 0]
with store._connect() as connection:
    cursor = connection.execute(
        "SELECT cursor_run_id FROM workflow_notification_reconcile_state "
        "WHERE singleton=1"
    ).fetchone()
assert cursor["cursor_run_id"] is None
assert sum("cursor retained" in record.message for record in caplog.records) == 1
assert not store._active_run_repair_reasons(run_id)

release.set()
holder.join(timeout=2)
assert not holder.is_alive()
assert outbox.reconcile_journal(limit_runs=1) == 1
assert outbox.pending_attention(run_id=run_id)[0]["kind"] == "failure"
```

- [ ] **Step 8: Add the coordinator same-sweep failure-injection test**

In `tests/plugins/workflow/test_coordinator.py`, add `from plugins.workflow.locks import workflow_lock`, extend the existing mock import to `from unittest.mock import ANY, MagicMock`, and add this test using the existing `_identity` and `_service` helpers:

```python
def test_repair_lock_timeout_does_not_block_delivery_or_scheduling(
    tmp_path, workflow_writer
) -> None:
    store = RunStore(tmp_path)
    coordinator = CoordinatorStore(store.database)
    identity = _identity("repair-contention")
    now = datetime.now(timezone.utc)
    leadership = coordinator.try_acquire(identity, now=now, lease_seconds=30)
    assert leadership.is_leader
    fence = ExecutionFence(identity.owner_id, leadership.lease.epoch)
    package = load_workflow(
        workflow_writer(tmp_path / "package", name="repair-contention")
    )

    terminal_snapshot = store.prepare_run_snapshot(package)
    terminal = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=terminal_snapshot.definition_digest,
            policy_digest=terminal_snapshot.policy_digest,
            input_manifest_digest=terminal_snapshot.input_manifest_digest,
            trigger_source="api",
            idempotency_key="repair-contention-terminal",
            concurrency_key="repair-contention-terminal",
            execution_mode="background",
        ),
        immutable_snapshot=terminal_snapshot,
    )
    RunScheduler(
        store,
        owner_id=f"coordinator:{identity.owner_id}:{leadership.lease.epoch}",
        execution_fence=fence,
    ).advance(terminal.run_id)
    for wake in coordinator.pending_wakes(
        identity,
        epoch=leadership.lease.epoch,
        now=now,
        limit=100,
    ):
        if wake.run_id == terminal.run_id:
            assert coordinator.complete_wake(
                wake.generation,
                identity,
                epoch=leadership.lease.epoch,
                now=now,
                outcome="test_terminal_setup",
            )

    queued_snapshot = store.prepare_run_snapshot(package)
    queued = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=queued_snapshot.definition_digest,
            policy_digest=queued_snapshot.policy_digest,
            input_manifest_digest=queued_snapshot.input_manifest_digest,
            trigger_source="api",
            idempotency_key="repair-contention-queued",
            concurrency_key="repair-contention-queued",
            concurrency_policy="allow",
            execution_mode="background",
        ),
        immutable_snapshot=queued_snapshot,
    )
    NotificationOutbox(store).record(
        run_id=queued.run_id,
        kind="failure",
        destination="gateway:opaque-capability",
        transition_version=999,
        payload={"workflow": package.definition.name},
        now=now,
    )

    delivered = []

    class Port:
        def deliver(self, capability: str, text: str, key: str) -> DeliveryReceipt:
            delivered.append((capability, text, key))
            return DeliveryReceipt(status="delivered", transport_id="message-1")

    ready = threading.Event()
    release = threading.Event()

    def hold_run_lock() -> None:
        with workflow_lock(store._run_lock_path(terminal.run_id)):
            ready.set()
            assert release.wait(timeout=2)

    holder = threading.Thread(target=hold_run_lock)
    holder.start()
    assert ready.wait(timeout=1)
    service = _service(
        tmp_path,
        host_kind="gateway",
        host_instance_id="repair-contention",
        delivery_port=Port(),
    )
    scheduler = MagicMock()
    scheduler.submit.return_value = True
    try:
        actionable, _cursor, _progress = service._sweep_once(
            store,
            coordinator,
            identity,
            leadership.lease.epoch,
            scheduler,
        )
    finally:
        release.set()
        holder.join(timeout=2)

    assert not holder.is_alive()
    assert actionable is True
    assert delivered == [("opaque-capability", ANY, ANY)]
    scheduler.submit.assert_any_call(queued.run_id, fence)
```

The test uses real SQLite, the actual `NotificationOutbox`, and the actual coordinator fence; only delivery transport and workflow execution submission are test doubles.

- [ ] **Step 9: Run the NR-2 tests red**

Run:

```bash
python -m pytest -q \
  tests/plugins/workflow/test_notifications.py::test_repair_lock_timeout_retains_cursor_warns_and_retries \
  tests/plugins/workflow/test_coordinator.py::test_repair_lock_timeout_does_not_block_delivery_or_scheduling
```

Expected: both fail because `WorkflowLockTimeout` escapes `reconcile_journal`; the coordinator test stops before delivery and scheduler submission.

- [ ] **Step 10: Implement cursor-retaining timeout handling and bounded diagnostics**

Add the RunStore diagnostic methods:

```python
def _note_notification_repair_timeout(self, run_id: str) -> int:
    with self._notification_repair_timeout_lock:
        if self._notification_repair_timeout_run_id == run_id:
            self._notification_repair_timeout_count += 1
        else:
            self._notification_repair_timeout_run_id = run_id
            self._notification_repair_timeout_count = 1
        return self._notification_repair_timeout_count

def _clear_notification_repair_timeout(self, run_id: str) -> None:
    with self._notification_repair_timeout_lock:
        if self._notification_repair_timeout_run_id == run_id:
            self._notification_repair_timeout_run_id = None
            self._notification_repair_timeout_count = 0
```

Catch the timeout before `_NotificationRepairPageFull` in `reconcile_journal`:

```python
except WorkflowLockTimeout:
    run_id = str(row["run_id"])
    timeout_count = self.store._note_notification_repair_timeout(run_id)
    if timeout_count == 3 or timeout_count % 10 == 0:
        logger.warning(
            "workflow notification repair lock contention run_id=%s "
            "consecutive_timeouts=%d cursor retained",
            run_id,
            timeout_count,
        )
    break
```

After a successful `_journal_candidates` call, invoke `_clear_notification_repair_timeout(run_id)`. Do not append the contended row to `processed_rows`, do not write a repair event, and do not change the cursor.

- [ ] **Step 11: Run NR-2 green and the complete Task 1 verification set**

Run the Step 9 command again. Expected: `2 passed`.

Run:

```bash
python -m pytest -q \
  tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_retention.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_fault_injection.py \
  tests/plugins/workflow/test_schema_migrations.py
```

Expected: all selected tests pass with only pre-existing platform skips. The schema migration suite proves no accidental schema/version change.

- [ ] **Step 12: Inspect, verify, and commit Task 1**

Run:

```bash
git diff --check
git diff --stat
git status --short
git diff -- plugins/workflow/store.py plugins/workflow/notifications.py \
  plugins/workflow/dashboard/plugin_api.py \
  tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_retention.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_coordinator.py
```

Confirm the two reviewer-owned files are the only unrelated paths. Stage exactly the seven Task 1 files and audit the staged set:

```bash
git add -- \
  plugins/workflow/store.py \
  plugins/workflow/notifications.py \
  plugins/workflow/dashboard/plugin_api.py \
  tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_retention.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_coordinator.py
git diff --cached --check
git diff --cached --name-only
git commit -m "fix(workflow): contain run-scoped repair failures"
```

Expected: the staged name list contains exactly those seven files; commit succeeds.

---

### Task 2: Enforce native evidence containment in CI

**Files:**
- Modify: `tests/scripts/test_workflow_merge_gate.py:28-45`
- Modify: `.github/workflows/ci.yml:145-180`

**Interfaces:**
- Consumes: the exact `workflow-portability` pytest path list.
- Produces: a meta-test requirement and CI selection containing `tests/plugins/workflow/test_evidence_api.py` exactly once.

- [ ] **Step 1: Tighten the meta-test first**

Add the evidence file to the existing exact tuple in `test_native_workflow_matrix_covers_every_release_gate`:

```python
for required_test in (
    "tests/plugins/workflow/test_evidence_api.py",
    "tests/plugins/workflow/test_idempotency_multiprocess.py",
    "tests/plugins/workflow/test_coordinator.py",
    "tests/plugins/workflow/test_coordinator_multiprocess.py",
    "tests/plugins/workflow/test_schema_migrations.py",
    "tests/plugins/workflow/test_notification_delivery.py",
    "tests/plugins/workflow/test_shutdown_recovery.py",
    "tests/plugins/workflow/test_retention.py",
):
    assert required_test in source
```

- [ ] **Step 2: Run the meta-test red**

Run:

```bash
python -m pytest -q \
  tests/scripts/test_workflow_merge_gate.py::test_native_workflow_matrix_covers_every_release_gate
```

Expected: `1 failed`; the assertion reports that `tests/plugins/workflow/test_evidence_api.py` is absent from `.github/workflows/ci.yml`.

- [ ] **Step 3: Add exactly the native evidence file to CI**

In the `workflow-portability` pytest command, add one explicit line:

```yaml
            tests/plugins/workflow/test_evidence_api.py \
```

Do not remove, wildcard, reorder away, or weaken any existing path.

- [ ] **Step 4: Run the meta-test and native evidence file green**

Run:

```bash
python -m pytest -q \
  tests/scripts/test_workflow_merge_gate.py::test_native_workflow_matrix_covers_every_release_gate \
  tests/plugins/workflow/test_evidence_api.py
```

Expected on macOS: the meta-test and all platform-neutral evidence tests pass, and exactly the native Windows reparse test skips. At the approved HEAD this file has no `mkfifo` or `O_NOFOLLOW` skip-marked test. Record the exact collected/pass/skip counts from fresh output rather than assuming an inverse Windows skip.

- [ ] **Step 5: Inspect, verify, and commit Task 2**

Run:

```bash
git diff --check -- .github/workflows/ci.yml tests/scripts/test_workflow_merge_gate.py
git diff -- .github/workflows/ci.yml tests/scripts/test_workflow_merge_gate.py
git status --short
git add -- .github/workflows/ci.yml tests/scripts/test_workflow_merge_gate.py
git diff --cached --check
git diff --cached --name-only
git commit -m "test(workflow): enforce native evidence containment"
```

Expected: the staged name list contains exactly the workflow and meta-test files; commit succeeds.

---

### Task 3: Run the final gate and correct verification evidence

**Files:**
- Modify: `docs/reviews/2026-07-19-workflow-orchestration-operator-robustness-verification.md`

**Interfaces:**
- Consumes: final Task 1/Task 2 commit SHAs and fresh verification output.
- Produces: an honest addendum with NR-1/NR-2/NR-3 red/green evidence, exact local counts, TESTED_BASE_SHA, and platform arithmetic boundaries.

- [ ] **Step 1: Run focused production verification from the final code HEAD**

Run:

```bash
python -m pytest -q \
  tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_retention.py \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_fault_injection.py \
  tests/plugins/workflow/test_schema_migrations.py \
  tests/plugins/workflow/test_evidence_api.py \
  tests/scripts/test_workflow_merge_gate.py
```

Expected: exit 0 with only platform-declared skips. Record exact pass/skip counts.

- [ ] **Step 2: Run the installed-distribution and strengthened base gates**

Run:

```bash
python -m pytest -q -m integration \
  tests/plugins/workflow/test_installed_distribution_e2e.py
scripts/test_workflow_merge_gate.sh --phase base
```

Expected: installed-distribution migration passes; Python base selection, Desktop Vitest, and Desktop TypeScript all pass; `TESTED_BASE_SHA` equals the current final code commit.

- [ ] **Step 3: Check CI platform arithmetic without inventing remote results**

From `test_evidence_api.py`, report the conditional split explicitly:

- macOS/Linux: the one native Windows reparse test skips; all platform-neutral evidence tests run.
- Windows: the native reparse test and all platform-neutral evidence tests run; there is no inverse POSIX-only skip in this file at the approved HEAD.

If the live GitHub matrix has not run on the final commit, state that local macOS results plus exact CI selection are verified, but do not claim Windows execution. If live matrix results are available, record each OS's exact pass/skip totals and reconcile them against the single current platform marker.

- [ ] **Step 4: Correct and extend the verification document**

Use `apply_patch` to replace the stale sentence claiming the native Windows test was already matrix-selected with historically accurate text: the earlier local result had one macOS skip, and NR-3 subsequently added the file to the matrix. Append a dated `Run-scoped repair containment follow-up` section that records the observed NR-1 global-marker red failure and green active-reason/cleanup/admission assertions; the observed NR-2 escaping-timeout red failure and green held-lock/same-sweep assertions; the NR-3 meta-test red/green transition; the exact final Python, installed-distribution, Desktop, and TypeScript results; the full `TESTED_BASE_SHA`; and the exact local/platform skip boundary. Every number and SHA in the section must come from Steps 1-3 rather than an estimate.

- [ ] **Step 5: Final diff/status verification and documentation commit**

Run:

```bash
git diff --check
git status --short
git log -3 --oneline
git diff -- docs/reviews/2026-07-19-workflow-orchestration-operator-robustness-verification.md
```

Stage only the verification document, not either reviewer-authored report:

```bash
git add -- docs/reviews/2026-07-19-workflow-orchestration-operator-robustness-verification.md
git diff --cached --check
git diff --cached --name-only
git commit -m "docs(workflow): record repair containment verification"
```

Expected: one documentation file is staged and committed.

- [ ] **Step 6: Verify final repository state**

Run:

```bash
git rev-parse HEAD
git status --short
git log -5 --oneline
git diff --check
```

Expected: only the two preserved reviewer-owned files remain modified/untracked. Report the design, plan, fix, CI, and verification commit IDs; all red/green and gate evidence; exact changed files; and that no merge, tag, release, or push occurred.

---

### Task 4: Contain corrupt legacy policy evidence to its run

**Files:**
- Modify: `plugins/workflow/store.py`
- Test: `tests/plugins/workflow/test_schema_migrations.py`
- Modify: `docs/superpowers/specs/2026-07-19-workflow-orchestration-run-scoped-repair-containment-design.md`

**Interfaces:**
- Consumes: `RunStore._transition_run_repair(...)`, `RunStore.node_effect_classification(...)`, `RunStore.attention_candidates(...)`.
- Produces: active `legacy_effect_policy_uncorroborated` run state that clears after successful policy corroboration.

- [ ] **Step 1: Write the genuine legacy-fixture failure test**

Copy the hash-pinned v2.0.9 fixture into a temporary home, relocate its indexed run directory, instantiate `RunStore`, and corrupt only `policy.yaml`. Assert `node_effect_classification` raises `JournalRecoveryError`, `storage_health()` remains healthy, the exact run-scoped reason is active and visible in attention, and an unrelated run is admitted. Restore the policy bytes, assert classification returns `outward`, and assert the active reason clears.

- [ ] **Step 2: Run the focused test red**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/plugins/workflow/test_schema_migrations.py::test_legacy_policy_damage_is_run_scoped_visible_and_self_clearing
```

Expected: fail because the legacy policy catch writes `.repair-required.json`, making storage health and unrelated admission fail globally.

- [ ] **Step 3: Implement the third run-scoped reason**

Add `legacy_effect_policy_uncorroborated` to `_RUN_SCOPED_REPAIR_REASONS` and every active-reason SQL predicate. Replace the legacy policy global marker with `_transition_run_repair(..., outcome="repair_required")`. After either persisted classification or successful legacy policy corroboration, append `repair_verified`. Overlay any active exact run-scoped reason in attention rather than special-casing only notification reconciliation.

- [ ] **Step 4: Verify and commit**

Run the focused test, the complete schema-migration and containment selections, `git diff --check`, and an exact staged-file audit. Commit:

```bash
git commit -m "fix(workflow): contain legacy policy damage"
```

### Task 5: Make workflow-test gate membership opt-out

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/scripts/test_workflow_merge_gate.py`

**Interfaces:**
- Consumes: explicit pytest paths in the base merge gate and portability matrix.
- Produces: matrix coverage for `test_notifications.py` and `test_desktop_api.py`, plus a complete inventory-minus-selected-minus-opt-outs invariant.

- [ ] **Step 1: Write the red membership assertions**

Add both critical files to `test_native_workflow_matrix_covers_every_release_gate`. Add a meta-test that enumerates `tests/plugins/workflow/test_*.py`, extracts explicit workflow test paths from the gate and CI YAML, and fails for uncovered files, stale opt-outs, empty reasons, or wildcard opt-outs. Start with no opt-outs so the structural test demonstrates the current uncovered inventory.

- [ ] **Step 2: Run the two meta-tests red**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/scripts/test_workflow_merge_gate.py::test_native_workflow_matrix_covers_every_release_gate \
  tests/scripts/test_workflow_merge_gate.py::test_every_workflow_test_is_selected_or_explicitly_opted_out
```

Expected: the first test reports the two missing matrix paths; the second reports every existing workflow test that is neither explicitly selected nor explicitly opted out.

- [ ] **Step 3: Add exact matrix paths and explicit opt-outs**

Add `test_desktop_api.py` and `test_notifications.py` to the portability pytest command. Populate a path-to-reason opt-out mapping with the exact current unselected inventory; use no globs or prefix exemptions. Require opt-outs to be removed if a file becomes selected.

- [ ] **Step 4: Verify and commit**

Run the meta-tests, both newly selected test files, the final focused selection, installed-distribution integration, and the base merge gate. Audit the exact two-file staged set and commit:

```bash
git commit -m "test(workflow): require explicit gate membership"
```
