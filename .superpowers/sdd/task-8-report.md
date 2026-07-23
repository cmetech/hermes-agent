# Task 8 report — bounded coordinator pages and cache returns

Task base: `61aadd443b45aee88af78a1dfcdeb0508e3ff119`

First fix: `e039bef3ddce76ba614bf1a3568fd4e8418f05bc`

Sustained-stream fix: `77b73f37f3c93c048f97d70ad152c9fb023ceaa0`

## RED evidence

### Saturated scheduled page

The real-store regression
`test_scheduled_due_pages_advance_past_a_stably_lane_blocked_first_page`
admitted 100 due rows behind one stable lane followed by an independently
runnable 101st row. On the task base, two bounded sweeps submitted the same
first 100 rows and never submitted the independent row.

### Post-restore cache generation change

The deterministic regression
`test_verified_loader_rechecks_generation_after_restoring_warm_cache_bytes`
paused a warm reader after authenticated bytes were restored, completed a
forced reverify in another thread, then released the reader. On the task base,
the reader returned the superseded package rather than the current
authenticated entry.

### Leadership-term reset

The first fix review exposed that initializing the scheduled cursor only in
`__init__` retained it when the same service object entered a new leadership
term. Before the correction,
`test_new_leadership_term_restarts_scheduled_paging_at_page_one` failed because
`_scheduled_sweep_cursor` retained the prior term's final key.

### Sustained newly-due stream

The independent Task 8 reviewer found that a future-scheduled row created
before the active continuation cursor could become due behind that cursor and
remain excluded while later admissions kept every forward page non-exhausted.
The implementer reproduced that finding before changing production code:

```bash
.venv/bin/python -m pytest -q \
  tests/plugins/workflow/test_coordinator.py \
  -k 'scheduled_due_prefix_is_resampled_during_a_sustained_forward_stream'
```

```text
FAILED tests/plugins/workflow/test_coordinator.py::test_scheduled_due_prefix_is_resampled_during_a_sustained_forward_stream
AssertionError: assert '8a9e0d52f0bd4f22a19c326f5797adad' in [submitted run ids]
1 failed, 37 deselected in 27.16s
```

## GREEN implementation and invariants

- Every sweep re-samples the current due prefix from page one.
- A separate scheduled continuation generation captures a fixed observed due
  instant and an inclusive durable scheduled `queue_sequence` fence from the
  O(1) store metadata counter. Later admissions cannot extend that generation,
  including under equal or backward wall-clock timestamps.
- Under saturation, one periodic head and one scheduled-continuation head are
  reserved within the shared hard 100-run budget. The remaining slots retain
  normal global `created_at`/`run_id` order.
- Scheduled and periodic cursors advance only through their actually processed
  prefixes. Deadline truncation cannot skip an unprocessed row.
- Every leadership term resets scheduled cursor, observed instant, and
  queue-sequence fence state to page one. The durable periodic cursor remains
  independent.
- A warm cache hit rechecks generation and entry identity after bounded
  in-memory resource restoration. An invalidated attempt restores the exact
  request-owned contents, aliases, counters, and container identities before
  one bounded verify-lock retry.
- Tree-signature hashing remains outside the cache lock, and forced fire-time
  verification remains unchanged.

Targeted GREEN reported by the implementer:

```text
4 passed, 119 deselected in 27.83s
```

Controller verification after the review correction:

```bash
.venv/bin/python -m pytest -q \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_scheduled_runs.py \
  tests/plugins/workflow/test_showcase_catalog.py \
  -k 'scheduled_due_pages_advance_past_a_stably_lane_blocked_first_page or scheduled_due_prefix_is_resampled_during_a_sustained_forward_stream or reserves_periodic_and_scheduled_continuation_heads or new_leadership_term_restarts_scheduled_paging_at_page_one or scheduled_candidate_generation_excludes_admissions_after_its_fence or verified_loader_rechecks_generation_after_restoring_warm_cache_bytes'
```

```text
5 passed, 118 deselected in 33.67s
```

```bash
.venv/bin/python -m pytest -q \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_scheduled_runs.py \
  tests/plugins/workflow/test_showcase_catalog.py
```

```text
123 passed in 99.09s
```

```bash
.venv/bin/python -m pytest -q \
  tests/plugins/workflow/test_scheduled_runs.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_runner_binding.py \
  tests/plugins/workflow/test_schedule_revalidation.py \
  tests/plugins/workflow/test_schedule_store_identity.py \
  tests/plugins/workflow/test_schema_migrations.py \
  tests/plugins/workflow/test_notifications.py \
  tests/plugins/workflow/test_showcase_catalog.py
```

```text
250 passed in 109.16s
```

## Files

- `plugins/workflow/coordinator.py`
- `plugins/workflow/showcase.py`
- `plugins/workflow/store.py`
- `tests/plugins/workflow/test_coordinator.py`
- `tests/plugins/workflow/test_scheduled_runs.py`
- `tests/plugins/workflow/test_showcase_catalog.py`
- `docs/upstream-customizations/workflow-orchestration.yaml`
- `.superpowers/sdd/task-8-report.md`
- `.superpowers/sdd/progress.md` (ignored ledger only)

## Self-review and concerns

The implementation adds no public API, configuration, schema migration, model
tool, caller authority, digest, MCP, or generated-brand surface. The finite
scan fence is internal and read-only; it reuses the scheduled index and exact
due corroboration. The two candidate streams may read up to two bounded
100-row pages before merging, but execution remains capped at 100 rows and all
queries retain an indexed keyset bound.

The first fix was not sufficient under a sustained forward stream; that gap is
now pinned by the future-first regression and finite generation fence. The
seven configured-Gateway/Electron manual gates remain outstanding and are not
substituted by this automated work.

## Reviewer correction round 2 — clock-independent generation membership

The review of `77b73f37f` found that its inclusive
`(created_at, run_id)` tail was not a true admission fence: a scheduled row
admitted after capture could sort inside the fence when `_utc_now()` returned
the same or an earlier timestamp. Both cases were reproduced before production
edits:

```bash
.venv/bin/python -m pytest -q \
  tests/plugins/workflow/test_scheduled_runs.py \
  -k 'scheduled_generation_created_key_fence_excludes_post_capture_admission'
```

```text
FF
AssertionError: assert '00000000000000000000000000000000' not in {
  '00000000000000000000000000000000',
  '11111111111111111111111111111111',
  'ffffffffffffffffffffffffffffffff'
}
2 failed, 27 deselected in 0.31s
```

Generation membership now captures the existing monotonic scheduled
`queue_sequence` metadata in one O(1) read. Candidate selection validates an
optional non-negative inclusive queue-sequence fence. The fixed observed due
instant, every-sweep fresh prefix, `(created_at, run_id)` processing order,
reserved continuation/periodic heads, shared 100-run cap, processed-prefix
cursor advancement, and leadership reset remain unchanged. The whole-due-set
descending high-water query was removed.

Targeted GREEN:

```text
5 passed, 63 deselected in 27.56s
```

Affected three-file matrix:

```text
3 files, 125 tests passed, 0 failed
```

Required eight-file matrix:

```text
8 files, 252 tests passed, 0 failed
```

Round 2 changes remain confined to the coordinator/store internals, their two
test files, this report, the customization manifest, and the ignored progress
ledger. No public API, configuration, schema, authority, model-tool, digest,
MCP, brand, or provider surface changed.
