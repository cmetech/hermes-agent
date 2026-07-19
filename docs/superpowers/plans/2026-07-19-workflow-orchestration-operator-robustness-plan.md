# Workflow Orchestration Operator Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make workflow orchestration honest and predictable under busy inboxes,
capacity pressure, foreground adoption, repair/cleanup gaps, clock steps,
concurrent reloads, and long-running Gateway delivery state.

**Architecture:** Keep workflow authority in the plugin's RunStore, journals,
and notification outbox. Reuse the existing signed cursor, lease-clock,
cleanup-preview, host reload, and Gateway receipt seams; add no model-facing
tool or workflow import to generic host files. Operator fixes are implemented
as nine separately evidenced red/green slices in one reviewed behavior commit,
followed by four invariant pins in one test-hardening commit.

**Tech Stack:** Python 3.11+, FastAPI/Pydantic, SQLite WAL, filesystem journals,
threading/multiprocessing with spawn, pytest, Bash merge-gate scripts, and
Windows reparse-point file metadata.

**Normative inputs:**

- `AGENTS.md`
- `docs/superpowers/specs/2026-07-19-workflow-orchestration-operator-robustness-design.md`
- `docs/reviews/2026-07-19-workflow-orchestration-remediation-fix-adversarial-review.md`
- `docs/reviews/2026-07-19-workflow-orchestration-followup-fixes-adversarial-review.md`

## Global Constraints

- Do not add a permanent model-facing workflow tool or mutate model tools,
  prompts, prior conversation messages, or strict message alternation.
- Do not import workflow modules into generic host/lifecycle files. Generic
  Gateway retention and provider reload code must remain workflow-agnostic.
- Do not add user-facing non-secret `HERMES_*` settings.
- Do not accept background work without a fresh coordinator or execute a
  workflow tail in an HTTP/Gateway mutation.
- Do not replay outward work or delivery while the previous outcome is
  uncertain.
- Do not delete workflow evidence when journal/index/admission evidence is
  missing, empty, corrupt, inconsistent, unreadable, oversized, or unsafe.
- Every slice starts with a focused failing behavior test and records its red
  and green commands independently, even though slices are packaged into two
  reviewed commits.
- Use real SQLite, filesystem, middleware, thread, process, and Windows paths
  where the acceptance contract names them.
- The foreground lease migration advances `_STORE_SCHEMA_VERSION` from 12 to
  13. Legacy rows with `NULL` corroboration fields use their UTC deadline.
- Preserve the hash-pinned v2.0.9 fixture byte-for-byte and run the cumulative
  migration twice. The installed-wheel test must reach schema 13 too.
- Stage only the exact paths listed by the current task. Preserve the existing
  reviewer-authored modification to
  `docs/reviews/2026-07-19-workflow-orchestration-followup-fixes-adversarial-review.md`
  without staging or editing it.

---

## File responsibility map

- `plugins/workflow/dashboard/plugin_api.py`: authenticated attention cursor
  parsing, source merge, and public response shape.
- `plugins/workflow/store.py`: attention candidate selection, admission,
  foreground lease state/freshness, durable adoption evidence, cleanup gating,
  and schema 13 migration.
- `plugins/workflow/notifications.py`: keyset attention rows, oversized repair
  progress, and targeted per-run crash-gap reconciliation.
- `plugins/workflow/cli.py`: plugin-owned parser contract, adoption notice, and
  sanitized command failures.
- `gateway/plugin_delivery.py`: generic, bounded route/receipt retention.
- `hermes_cli/web_server.py`: generic host reload conflict classification and
  compare-and-restore provider configuration.
- `tests/plugins/workflow/*`: real store/process/operator contracts.
- `tests/gateway/test_plugin_delivery.py`: generic delivery receipt retention.
- `tests/hermes_cli/test_plugin_provider_hot_reload.py` and
  `tests/hermes_cli/test_web_server.py`: host-controlled reload behavior.
- `scripts/test_workflow_merge_gate.sh` and
  `tests/scripts/test_workflow_merge_gate.py`: green-gate selection and its
  self-enforcing meta-test.

## Explicitly deferred debt

- NF-L4: legacy-namespace retry duplication requires a pre-upgrade stable-key
  field path that did not ship.
- NF2-L2: upgrade dedup one-shot resend likewise depends on a pre-fix build
  having shipped.
- NF-L5: the remaining unfenced mutations are serialized and convergent; the
  disruptive pressure interrupt is tied to pressure both leaders observe.
- NF-L12: multi-profile Gateway delivery remains deliberately fail closed
  until that deployment topology is supported.
- NF2-L3: raw capability storage in `facts.destination` remains masked on all
  reads and protected by the local permission boundary.

## Task 1: Harden the nine operator-facing behavior contracts

**Findings:** NF-L2; NF2-L5/NF-L13; foreground-adoption notice; NF-L10;
NF-L9; NF-L8; NF-L6; NF-L7; NF-L11 (retention half).

**Files:**

- Modify: `plugins/workflow/dashboard/plugin_api.py`
- Modify: `plugins/workflow/store.py`
- Modify: `plugins/workflow/notifications.py`
- Modify: `plugins/workflow/cli.py`
- Modify: `gateway/plugin_delivery.py`
- Modify: `hermes_cli/web_server.py`
- Modify: `tests/plugins/workflow/test_desktop_api.py`
- Modify: `tests/plugins/workflow/test_admission.py`
- Modify: `tests/plugins/workflow/test_cli.py`
- Modify: `tests/plugins/workflow/test_coordinator.py`
- Modify: `tests/plugins/workflow/test_coordinator_multiprocess.py`
- Modify: `tests/plugins/workflow/test_notifications.py`
- Modify: `tests/plugins/workflow/test_retention.py`
- Modify: `tests/plugins/workflow/test_notification_delivery.py`
- Modify: `tests/plugins/workflow/test_schema_migrations.py`
- Modify: `tests/plugins/workflow/test_installed_distribution_e2e.py`
- Modify: `tests/gateway/test_plugin_delivery.py`
- Modify: `tests/hermes_cli/test_plugin_provider_hot_reload.py`
- Modify: `tests/hermes_cli/test_web_server.py`

**Interfaces:**

- Produces `RunStore.attention_candidates(...)` and
  `NotificationOutbox.pending_attention_page(...)` as descending keyset
  sources consumed only by the plugin dashboard adapter.
- Adds projection field
  `execution_handoff={"transition": "foreground_execution_adopted", ...}`.
- Adds schema-13 run columns `foreground_boot_id`,
  `foreground_heartbeat_monotonic`, and `foreground_lease_seconds`.
- Produces `NotificationOutbox.reconcile_run(run_id, ...)` for fail-closed,
  targeted cleanup corroboration.
- Produces generic `GatewayPluginDeliveryPort.prune_expired(limit=100)`.
- Produces generic HTTP error code `plugin_reload_in_progress` for a concurrent
  provider reload.

### Slice 1.1: NF-L2 attention ordering and cursor

- [ ] **Step 1: Write the failing newest-first and traversal tests**

Add `test_attention_is_newest_first_and_cursor_traverses_more_than_100_items`
and `test_attention_cursor_is_scope_bound_and_stable_on_timestamp_ties` to
`tests/plugins/workflow/test_desktop_api.py`. Create 135 failed/paused runs
with colliding timestamps in real SQLite, request pages of 37, and assert:

```python
assert first_page[0]["run_id"] == newest_run_id
assert len(seen) == len(set(seen)) == 135
assert set(seen) == expected_attention_ids
assert every_page_cursor_except_last
assert cross_scope_response.status_code == 410
assert cross_scope_response.json()["detail"]["code"] == "cursor_expired"
```

- [ ] **Step 2: Run the focused red test**

Run:

```bash
pytest -q tests/plugins/workflow/test_desktop_api.py \
  -k 'attention_is_newest_first or attention_cursor_is_scope_bound'
```

Expected red evidence: the first page contains the oldest items, only 100 are
reachable, and `next_cursor` is `None`.

- [ ] **Step 3: Add bounded attention keysets and signed composite cursor**

In `RunStore`, add a candidate query with this contract:

```python
def attention_candidates(
    self,
    *,
    operator_scope: str | None,
    observed_at: datetime,
    limit: int,
    before: tuple[str, str] | None = None,
    include_coordinator_unavailable: bool = False,
) -> tuple[dict[str, object], ...]:
    """Newest failed/paused/stalled candidate projections, bounded by keyset."""
```

The SQL predicate is applied before `LIMIT`: always include `failed` and
`paused`; include expired/missing foreground-owner `running` rows; include
coordinator-dependent nonterminal rows only when the sampled coordinator is
unavailable. Filter `updated_at <= observed_at`, order by
`updated_at DESC, run_id DESC`, fetch `limit + 1`, and load only those run
projections.

In `NotificationOutbox`, add:

```python
def pending_attention_page(
    self,
    *,
    limit: int,
    observed_at: datetime,
    before: tuple[str, str] | None = None,
) -> tuple[dict[str, object], ...]:
    """Newest pending/leased/dead attention rows by updated_at/id."""
```

Change `/attention` to accept `cursor: str | None`. Expand each candidate run
into deterministic item keys
`(updated_at, source, run_id, node_id_or_empty, kind, notification_id_or_empty)`.
Keyset-merge the run and outbox pages in descending order, deduplicate the
same run/kind/version projection in favor of the richer run interaction, and
return `limit + 1`. The signed `kind="attention"` cursor contains
`observed_at`, the last consumed run-source key, the last consumed outbox key,
and any within-run item key so a run with multiple pending nodes can continue
without a gap. Advance a source cursor only through items actually emitted.

- [ ] **Step 4: Run the focused green test and existing attention contracts**

Run:

```bash
pytest -q tests/plugins/workflow/test_desktop_api.py \
  -k 'attention or runs_pagination'
```

Expected: all selected tests pass; every page is bounded and no scope can
reuse another scope's cursor.

### Slice 1.2: NF2-L5/NF-L13 queue-policy admission at capacity

- [ ] **Step 5: Write the failing admission tests**

Add table-driven real-store tests to `tests/plugins/workflow/test_admission.py`:

```python
@pytest.mark.parametrize("has_held_lane", [False, True])
def test_queue_policy_start_queues_at_execution_capacity(...):
    result = store.start_run(queue_request, immutable_snapshot=prepared)
    assert result.disposition == "queued"
    assert store.load_run(result.run_id)["status"] == "queued"

def test_queue_policy_still_rejects_when_queued_bound_is_full(...):
    assert result.reason_code == "queued_capacity"
```

Also pin that `allow` does not bypass execution capacity and `forbid` still
rejects a held same-lane overlap.

- [ ] **Step 6: Run the focused red test**

Run:

```bash
pytest -q tests/plugins/workflow/test_admission.py \
  -k 'queue_policy_start_queues_at_execution_capacity or queued_bound_is_full'
```

Expected red evidence: the queue-policy request without an older eligible
waiter returns `executing_capacity` instead of a queued run.

- [ ] **Step 7: Route execution-capacity pressure into the queue branch**

In `RunStore.start_run`, calculate `execution_at_capacity` before the branch.
Use the existing bounded queued check and queue-sequence allocator when:

```python
must_queue = (
    (active is not None and request.concurrency_policy == "queue")
    or (older_queued is not None and request.concurrency_policy != "allow")
    or (execution_at_capacity and request.concurrency_policy == "queue")
)
```

Keep `executing_capacity` for non-queue policies at full execution capacity.
Do not call `RunScheduler.advance`; persist the queued projection and use the
existing coordinator wake.

- [ ] **Step 8: Run admission and race greens**

Run:

```bash
pytest -q tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_approval_races.py
```

Expected: both files pass, including FIFO, held-lane, and capacity races.

### Slice 1.3: Foreground adoption notice and machine evidence

- [ ] **Step 9: Write failing durable-evidence and CLI-output tests**

Extend the real adoption test in
`tests/plugins/workflow/test_coordinator_multiprocess.py`:

```python
assert projection["execution_mode"] == "background"
assert projection["execution_handoff"]["transition"] == (
    "foreground_execution_adopted"
)
```

Add CLI tests asserting human output contains the run ID and
`workflow status <run-id>`, while JSON output has no stray prose and contains:

```python
assert envelope["result"]["execution_mode"] == "background"
assert envelope["result"]["execution_handoff"]["transition"] == (
    "foreground_execution_adopted"
)
```

- [ ] **Step 10: Run the focused red tests**

Run:

```bash
pytest -q tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  -k 'adopted or adoption_notice or execution_handoff'
```

Expected red evidence: adoption is present only as a journal event; neither
the final status projection nor human output explains the handoff.

- [ ] **Step 11: Persist and render the handoff**

Before appending `foreground_execution_adopted`, set:

```python
projection["execution_handoff"] = {
    "transition": "foreground_execution_adopted",
    "execution_mode": "background",
    "coordinator_epoch": fence.owner_epoch,
    "occurred_at": instant.isoformat(),
}
```

Do not set this for reconciliation-required adoption. In `_cmd_run`, after the
final status read and before `_emit`, print only in human mode:

```python
if (
    not args.json
    and payload.get("execution_mode") == "background"
    and isinstance(payload.get("execution_handoff"), Mapping)
    and payload["execution_handoff"].get("transition")
        == "foreground_execution_adopted"
):
    print(
        "This run was adopted by the background coordinator and continues; "
        f"watch it with workflow status {admitted.run_id}."
    )
```

- [ ] **Step 12: Run the adoption greens**

Run the command from Step 10. Expected: selected tests pass and JSON stdout is
one valid envelope.

### Slice 1.4: NF-L10 top-level CLI JSON and sanitized OS errors

- [ ] **Step 13: Write failing parser and OSError tests**

In `tests/plugins/workflow/test_cli.py`, assert `workflow bogus --json` raises
`SystemExit(2)` with exactly one `invalid_request` envelope on stdout and empty
stderr. Monkeypatch a valid handler to raise
`OSError("/private/profile/workflows/admission.sqlite3")` and assert neither
the absolute path nor raw exception text is returned.

- [ ] **Step 14: Run the focused red tests**

Run:

```bash
pytest -q tests/plugins/workflow/test_cli.py \
  -k 'top_level_json_parse_error or os_error_is_sanitized'
```

Expected red evidence: the invalid action uses raw argparse stderr, and the
OSError response exposes the path.

- [ ] **Step 15: Install the plugin-owned parser contract and stable error**

Bind `_WorkflowArgumentParser.parse_known_args` and `.error` to the workflow
root parser inside `register_cli`, so the parent parser sees `--json` before an
invalid subcommand is rejected:

```python
subparser.parse_known_args = MethodType(
    _WorkflowArgumentParser.parse_known_args, subparser
)
subparser.error = MethodType(_WorkflowArgumentParser.error, subparser)
```

Import `MethodType` from `types`. Keep ordinary human argparse behavior.
Replace the OSError mapping with:

```python
error = MachineError(
    "action_failed",
    "workflow storage operation failed",
    details={"exception_type": type(exc).__name__},
)
```

Do not include `str(exc)` or a path in either output mode.

- [ ] **Step 16: Run all workflow CLI tests**

Run:

```bash
pytest -q tests/plugins/workflow/test_cli.py
```

Expected: the file passes with human parser behavior and all machine envelopes
unchanged except the corrected failure cases.

### Slice 1.5: NF-L9 oversized repair row

- [ ] **Step 17: Write the failing oversized-journal repair test**

In `tests/plugins/workflow/test_notifications.py`, create a background run,
append padding until its valid journal exceeds a deliberately small repair
budget, remove its terminal outbox/fact rows to simulate the crash gap, run
`reconcile_journal`, and assert:

```python
assert repaired == 1
assert outbox.pending_attention(run_id=run_id)[0]["kind"] == "failure"
assert repair_cursor_run_id == run_id
```

- [ ] **Step 18: Run the focused red test**

Run:

```bash
pytest -q tests/plugins/workflow/test_notifications.py \
  -k 'oversized_first_journal_is_repaired'
```

Expected red evidence: `repaired == 0` and the cursor remains unable to pass
the oversized first row.

- [ ] **Step 19: Permit one bounded first-row overrun**

Change the byte guard to:

```python
if consumed_bytes and consumed_bytes + journal_bytes > byte_budget:
    break
```

The first row is therefore read even when it exceeds the page budget; its
size remains bounded by `RunStore.max_journal_bytes`. Add its bytes after a
successful read and advance the cursor only after its terminal facts are
recorded or already exist. On unreadable/corrupt evidence, durably record a
notification-repair failure through the existing repair-event/repair-required
path before advancing; never skip it silently. Log the single-row budget
overrun with only the run ID, actual byte count, and configured page budget.

- [ ] **Step 20: Run repair and crash-gap greens**

Run:

```bash
pytest -q tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_notification_delivery.py \
  -k 'repair or reconcile or crash_gap'
```

Expected: all selected tests pass and the oversized run has a real repaired
notification.

### Slice 1.6: NF-L8 cleanup crash-gap protection

- [ ] **Step 21: Write failing cleanup reconciliation tests**

Add to `tests/plugins/workflow/test_retention.py`:

```python
def test_cleanup_repairs_terminal_notification_gap_before_preview(...):
    # Delete only the outbox/fact projection after a real terminal journal.
    preview = store.cleanup_runs(older_than=timedelta(0))
    assert preview["confirmation_token"] is None
    assert preview["notification_dependencies"]["count"] == 1
    assert store.run_directory(run_id).is_dir()

@pytest.mark.parametrize("damage", ["missing", "empty", "corrupt", "oversized"])
def test_cleanup_preserves_run_when_notification_corroboration_fails(...):
    assert preview["confirmation_token"] is None
    assert "notification_reconciliation_unverified" in blocked_reasons
```

- [ ] **Step 22: Run the focused red tests**

Run:

```bash
pytest -q tests/plugins/workflow/test_retention.py \
  -k 'terminal_notification_gap or notification_corroboration_fails'
```

Expected red evidence: the crash-gap run receives a valid cleanup token.

- [ ] **Step 23: Add targeted reconciliation before cleanup eligibility**

Refactor journal candidate projection into:

```python
def reconcile_run(
    self,
    run_id: str,
    *,
    max_journal_bytes: int | None = None,
) -> int:
    """Corroborate one complete bounded journal and repair missing facts."""
```

It must read `events.jsonl` with the descriptor-based
`_read_contained_regular_file(directory, journal_path, max_journal_bytes + 1)`
helper, reject a reported size above the enforced store quota, and parse the
returned bytes through a `RunStore` journal-frame decoder with torn-tail repair
disabled. It raises a typed reconciliation error on missing/corrupt/unsafe
evidence; it never trusts path resolution or `stat()` alone. Use the same safe
reader in the periodic reconciliation scanner. In `_preview_cleanup`, select at most
201 ordered rows, operate on the first 200, and call `reconcile_run` before
acquiring the candidate lock and invoking `_cleanup_candidate`. A repaired
pending/leased/dead row adds the existing
`pending_notification_delivery` block. A reconciliation exception adds
`notification_reconciliation_unverified`. The 201st row only sets
`more_candidates=True`; it remains untouched for a later cleanup batch.

Execution calls `reconcile_run` for every preview candidate before entering its
existing sorted `ExitStack` of run locks. It then re-runs `_cleanup_candidate`
under those locks. A failed reconciliation invalidates the token; an
intervening journal mutation changes the candidate digest and triggers the
existing `cleanup preview changed` rejection. Neither case moves evidence.

- [ ] **Step 24: Run cleanup, notification, and authority greens**

Run:

```bash
pytest -q tests/plugins/workflow/test_retention.py \
  tests/plugins/workflow/test_notification_delivery.py \
  tests/plugins/workflow/test_desktop_api.py \
  -k 'cleanup or notification_gap or notification_corroboration'
```

Expected: selected tests pass; missing evidence never produces an executable
cleanup token.

### Slice 1.7: NF-L6 monotonic foreground leases and schema 13

- [ ] **Step 25: Write failing clock-step, legacy, and migration tests**

In `tests/plugins/workflow/test_coordinator.py`, use a controllable
`LeaseClockSample` sequence to prove:

```python
# Backward UTC step after monotonic duration elapsed:
assert store.claim_foreground_execution(...) is not None
# Forward UTC step while monotonic duration remains fresh:
with pytest.raises(ForegroundExecutionConflict, match="still active"):
    store.adopt_expired_foreground(...)
# Legacy NULL corroboration before UTC deadline:
assert store.claim_foreground_execution(...) is None
```

Then advance past the legacy UTC deadline and assert the claim/adoption is
allowed. Extend the v2.0.9 migration test to require the three new columns,
`PRAGMA user_version == 13`, identical first/second manifests, clean integrity
and foreign keys, and unchanged evidence hashes. Extend the installed-wheel
integration test to copy the pinned fixture into a clean home, instantiate the
wheel's `RunStore`, and print/assert schema 13 plus all three columns. Before
opening the copied store, relocate its legacy `run_directory` prefix to that
temporary home's copied evidence tree exactly as the cumulative migration test
does; do not modify the source fixture.

- [ ] **Step 26: Run the focused red tests**

Run:

```bash
pytest -q tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_schema_migrations.py \
  -k 'foreground_lease_clock or legacy_foreground or pre_amendment_v209'
```

Expected red evidence: foreground freshness follows wall clock and the schema
manifest lacks the corroboration columns/version 13.

- [ ] **Step 27: Migrate and apply corroborated foreground freshness**

Set `_STORE_SCHEMA_VERSION = 13` and add nullable columns to both the create
schema and additive migration map:

```sql
foreground_boot_id TEXT,
foreground_heartbeat_monotonic REAL,
foreground_lease_seconds REAL
```

Extend `ForegroundExecutionLease` with protocol-compatible fields
`boot_id`, `heartbeat_monotonic`, and `lease_seconds`. Add a private row decoder
and use `lease_is_fresh(decoded, sample)` in start/claim/renew/adopt and
`get_run_status` foreground-health checks. New/renewed leases persist the
sample's boot identity and monotonic heartbeat plus the validated duration.
Release and successful adoption clear the corroboration columns while setting
or clearing the UTC deadline as today. Rows with any corroboration value
missing fall through `lease_is_fresh` to the UTC deadline.

Update all index/projection comparison-and-swap tuples so the journal and runs
row cannot disagree about foreground ownership evidence.

- [ ] **Step 28: Run schema, clock, and installed-wheel greens**

Run:

```bash
pytest -q tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_schema_migrations.py
pytest -q -m integration \
  tests/plugins/workflow/test_installed_distribution_e2e.py
```

Expected: all commands pass, the cumulative fixture reaches schema 13 twice,
and installed code—not the checkout import path—reports the same schema.

### Slice 1.8: NF-L7 concurrent reload conflict and conditional rollback

- [ ] **Step 29: Write failing real-host concurrency tests**

In `tests/hermes_cli/test_plugin_provider_hot_reload.py`, start two async
provider changes against one real `PluginManager`: hold the winner inside
`reload_background_services`, issue the loser, and assert the loser receives
HTTP 409 `plugin_reload_in_progress`, the winner's provider is usable, and
saved config matches the winner. In `tests/hermes_cli/test_web_server.py`, add
a concurrent unrelated config mutation before rollback and assert it survives;
also force rollback persistence to fail and assert the typed code is
`provider_config_consistency_failed`.

- [ ] **Step 30: Run the focused red tests**

Run:

```bash
pytest -q tests/hermes_cli/test_plugin_provider_hot_reload.py \
  tests/hermes_cli/test_web_server.py \
  -k 'concurrent_provider_reload or conditional_reload_rollback'
```

Expected red evidence: the loser receives 500/`RuntimeError`, or rollback
overwrites a later value.

- [ ] **Step 31: Add a nonblocking provider-reload slot and leaf CAS rollback**

In `hermes_cli/web_server.py`, add one process-local `threading.Lock` dedicated
to provider config + reload. Provider-changing routes acquire it with
`blocking=False`; failure raises:

```python
HTTPException(
    status_code=409,
    detail={"code": "plugin_reload_in_progress"},
)
```

Hold it across save and awaited host reload, releasing in `finally`. Catch only
the PluginManager's exact already-in-progress `RuntimeError` in
`_reload_plugin_background_services` and return the same typed code; unrelated
runtime errors remain 500s.

For a blocked reload after a save, recursively enumerate only the leaf paths
changed between `previous_config` and `attempted_config`. Reload current
config, restore a leaf only when its current value still equals the attempted
value, preserve all other leaves, and save once. This compare-and-restore must
support both restoring a value and deleting a newly added leaf. If that
rollback write fails, return a typed
`provider_config_consistency_failed` response and do not claim the attempted
provider is active.

- [ ] **Step 32: Run reload greens including the real provider path**

Run:

```bash
pytest -q tests/hermes_cli/test_plugin_provider_hot_reload.py \
  tests/hermes_cli/test_web_server.py \
  -k 'provider_reload or hot_add or plugin_providers'
```

Expected: selected tests pass, including the existing test that dispatches a
newly configured real provider after host-controlled reload.

### Slice 1.9: NF-L11 safe route and receipt pruning

- [ ] **Step 33: Write failing retention and negative-protection tests**

In `tests/gateway/test_plugin_delivery.py`, age an expired route with delivered
and permanent-failure receipts beyond 30 days and assert a bounded prune
removes them. Add explicit negative cases proving an arbitrarily old
`sending` receipt survives and an unexpired route survives.

In `tests/plugins/workflow/test_notification_delivery.py`, parameterize
`pending`, `leased`, and `dead` Gateway outbox rows referencing an unexpired
capability, run generic pruning, and assert the route remains authorized and
the outbox destination is unchanged.

- [ ] **Step 34: Run the focused red tests**

Run:

```bash
pytest -q tests/gateway/test_plugin_delivery.py \
  tests/plugins/workflow/test_notification_delivery.py \
  -k 'prune or pruning_preserves'
```

Expected red evidence: `GatewayPluginDeliveryPort` has no bounded retention
operation and expired terminal rows remain indefinitely.

- [ ] **Step 35: Add generic bounded expiry retention**

Implement:

```python
def prune_expired(
    self,
    *,
    limit: int = 100,
    receipt_retention: timedelta = timedelta(days=30),
) -> dict[str, int]:
    """Prune only expired routes and old terminal receipts in one IMMEDIATE txn."""
```

Within one `BEGIN IMMEDIATE` transaction:

1. Select at most `limit` receipts whose route is expired, receipt
   `updated_at` is older than 30 days, and state is a terminal non-retryable
   result (`delivered`, `permanent_failure`, or `unauthorized`).
2. Delete those exact rows with the same state/time predicates.
3. Select and delete at most the remaining limit of expired routes for which
   no receipt row remains.

Never select `sending` or `retryable_failure`. Never select an unexpired route.
Call the bounded prune best-effort before minting a new route; a SQLite pruning
failure is logged without rejecting the authenticated command. Ordinary
delivery still uses the existing transaction and receipt fence.

- [ ] **Step 36: Run retention and delivery greens**

Run:

```bash
pytest -q tests/gateway/test_plugin_delivery.py \
  tests/plugins/workflow/test_notification_delivery.py
```

Expected: both files pass, including never-replay, topology, pending-route,
and receipt-loss contracts.

### Task 1 verification and commit

- [ ] **Step 37: Run the complete operator-robustness set**

Run:

```bash
pytest -q \
  tests/plugins/workflow/test_desktop_api.py \
  tests/plugins/workflow/test_admission.py \
  tests/plugins/workflow/test_approval_races.py \
  tests/plugins/workflow/test_cli.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_retention.py \
  tests/plugins/workflow/test_notification_delivery.py \
  tests/plugins/workflow/test_schema_migrations.py \
  tests/gateway/test_plugin_delivery.py \
  tests/hermes_cli/test_plugin_provider_hot_reload.py \
  tests/hermes_cli/test_web_server.py
pytest -q -m integration \
  tests/plugins/workflow/test_installed_distribution_e2e.py
```

Expected: both commands exit 0 with no unexpected skips. The installed-wheel
test is allowed only its declared integration marker selection.

- [ ] **Step 38: Inspect and commit exactly the Task 1 files**

Run:

```bash
git diff --check
git status --short
git diff --stat
git diff -- plugins/workflow gateway/plugin_delivery.py \
  hermes_cli/web_server.py tests/plugins/workflow \
  tests/gateway/test_plugin_delivery.py \
  tests/hermes_cli/test_plugin_provider_hot_reload.py \
  tests/hermes_cli/test_web_server.py
```

Verify the reviewer addendum is not staged, then stage the exact Task 1 paths
listed above and commit:

```bash
git commit -m "fix(workflow): harden operator-facing orchestration"
```

## Task 2: Add the four production-invariant pins and lock them into the gate

**Findings:** NF-L1; NF-L3; NF2-L1; NF2-L6.

**Files:**

- Modify: `tests/plugins/workflow/test_evidence_api.py`
- Modify: `tests/plugins/workflow/test_idempotency_multiprocess.py`
- Modify: `tests/plugins/workflow/test_notification_delivery.py`
- Modify: `scripts/test_workflow_merge_gate.sh`
- Modify: `tests/scripts/test_workflow_merge_gate.py`

**Interfaces:** Test-only. No production schema or API changes.

### Slice 2.1: NF-L1 Windows fallback containment

- [ ] **Step 1: Add fallback and native-Windows hostile-path tests**

Add a platform-neutral fallback test that calls
`_read_fallback_contained_file` and supplies an `os.stat_result` proxy whose
`st_file_attributes` includes `_FILE_ATTRIBUTE_REPARSE_POINT`; assert
`_UnsafeEvidencePath` before any read. Add a post-open identity-swap test whose
second `_reject_reparse_components` result has a different identity; assert no
outside sentinel is returned. Add a Windows-only test that creates a real
directory/file symlink or junction inside the run evidence tree and asserts
`EvidenceReader.query(..., kind="logs")` returns no item plus
`unsafe_evidence_path`. A failure to create the Windows hostile link is a test
failure, not a skip.

- [ ] **Step 2: Prove the test detects a weakened fallback, then restore**

Temporarily make `_is_reparse_point` return `False`, run:

```bash
pytest -q tests/plugins/workflow/test_evidence_api.py \
  -k 'fallback_reparse or fallback_identity_swap'
```

Expected sensitivity evidence: at least the reparse test fails. Restore the
production line with `apply_patch`, rerun the same command, and expect pass.
Do not commit the temporary mutation.

### Slice 2.2: NF-L3 synchronized cross-process idempotency

- [ ] **Step 3: Add a spawn race against one SQLite store**

Extend the child helper to accept a spawn-context `Event`, wait immediately
before `args.func(args)`, and send its envelope through a shared queue. Start
two children with the same semantic key/input, release both together, and
assert:

```python
envelopes = [envelope for _code, envelope in child_results]
assert sorted(
    envelope["result"]["admission_disposition"] for envelope in envelopes
) \
    == ["created", "existing"]
assert len({envelope["result"]["run_id"] for envelope in envelopes}) == 1
assert all(code == 0 for code, _result in child_results)
assert len(RunStore(profile).list_runs()) == 1
```

No `database is locked`, uniqueness error, or raw traceback may appear.

- [ ] **Step 4: Run the multiprocess pin repeatedly**

Run:

```bash
pytest -q tests/plugins/workflow/test_idempotency_multiprocess.py \
  -k 'concurrent_same_semantic_start' --count=5
```

If `pytest-repeat` is unavailable, run the same command five times in a shell
loop. Expected: every iteration passes with one durable run.

### Slice 2.3: NF2-L1 concurrent outbox drainers

- [ ] **Step 5: Add a real concurrent drainer test**

Create one real Gateway outbox row. Start drainer A in a thread with a sender
that signals after the row lease and waits on a release event. While A is held,
run drainer B against a separate `NotificationOutbox`/SQLite connection. Assert
B returns zero, release A, and assert exactly one sender call and one delivered
history row:

```python
assert sorted(drained_counts) == [0, 1]
assert sender_calls == [notification_id]
assert outbox.history(run_id=run_id)[0]["state"] == "delivered"
```

- [ ] **Step 6: Run the concurrency pin repeatedly**

Run the named test five times as in Step 4. Expected: exactly one outward send
on every iteration.

### Slice 2.4: NF2-L6 merge-gate self-enforcement

- [ ] **Step 7: Write the failing meta-test**

Extend `test_merge_gate_enforces_async_reload_and_delivery_regressions` to
require `tests/scripts/test_workflow_merge_gate.py` in the base pytest list.

Run:

```bash
pytest -q tests/scripts/test_workflow_merge_gate.py \
  -k 'enforces_async_reload_and_delivery_regressions'
```

Expected red evidence: the gate script does not name its own test module.

- [ ] **Step 8: Add the meta-test module to the base gate**

Add this exact path to the non-FAST base pytest invocation:

```bash
tests/scripts/test_workflow_merge_gate.py \
```

Keep `WORKFLOW_MERGE_GATE_FAST=1` around nested gate probes so selecting the
meta-test cannot recurse into another pytest run.

- [ ] **Step 9: Run the meta-test and FAST gate green**

Run:

```bash
pytest -q tests/scripts/test_workflow_merge_gate.py
WORKFLOW_MERGE_GATE_FAST=1 scripts/test_workflow_merge_gate.sh --phase base
```

Expected: pytest exits 0 and the FAST gate prints the exact current
`TESTED_BASE_SHA` without recursion.

### Task 2 verification and commit

- [ ] **Step 10: Run all four invariant pins**

Run:

```bash
pytest -q \
  tests/plugins/workflow/test_evidence_api.py \
  tests/plugins/workflow/test_idempotency_multiprocess.py \
  tests/plugins/workflow/test_notification_delivery.py \
  tests/scripts/test_workflow_merge_gate.py
```

Expected: exit 0; on Windows the native reparse test runs rather than skips.

- [ ] **Step 11: Inspect and commit exactly the Task 2 files**

Run `git diff --check`, inspect the exact five paths, verify the reviewer
addendum remains unstaged, then commit:

```bash
git commit -m "test(workflow): strengthen production invariants"
```

## Task 3: Run the final branch gate and record exact evidence

**Files:**

- Create: `docs/reviews/2026-07-19-workflow-orchestration-operator-robustness-verification.md`

- [ ] **Step 1: Run the strengthened base gate**

Run:

```bash
scripts/test_workflow_merge_gate.sh --phase base
```

Expected: Python gate suite, installed-wheel integration, Desktop Vitest set,
and Desktop TypeScript check all exit 0; capture the exact pass/skip counts and
`TESTED_BASE_SHA`.

- [ ] **Step 2: Run the cumulative migration and focused real-process tests once more**

Run:

```bash
pytest -q tests/plugins/workflow/test_schema_migrations.py \
  tests/plugins/workflow/test_idempotency_multiprocess.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_notification_delivery.py
pytest -q -m integration \
  tests/plugins/workflow/test_installed_distribution_e2e.py
```

Expected: both commands exit 0.

- [ ] **Step 3: Write the verification record**

Record each slice's red cause, green command/count, implementation commit ID,
schema-13 migration evidence, merge-gate output, deliberately deferred
findings, and any platform-specific skip. Do not claim the native Windows
assertion ran if the current host is not Windows; cite the merge-gate/CI path
that enforces it.

- [ ] **Step 4: Verify and commit only the record**

Run:

```bash
git diff --check
git status --short
git diff -- docs/reviews/2026-07-19-workflow-orchestration-operator-robustness-verification.md
```

Force-add only the ignored review record if required and commit:

```bash
git commit -m "docs(workflow): record operator robustness verification"
```

## Completion gate

Before any completion claim:

1. Re-read the approved design and map every acceptance criterion to a green
   test or gate line in the verification record.
2. Run `git diff --check`, `git status --short`, and `git log -4 --oneline`.
3. Confirm the only remaining unstaged path is the pre-existing reviewer
   addendum, unless the maintainer has changed it again.
4. Confirm no merge, tag, release, or push occurred during implementation
   unless explicitly requested later.
5. Report exact red/green evidence, files changed, test counts, commit IDs, and
   any remaining non-blocking debt without upgrading an unrun test into a pass
   claim.
