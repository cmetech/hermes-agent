# Task 8 report — bounded coordinator pages and cache returns

## RED

- `test_scheduled_due_pages_advance_past_a_stably_lane_blocked_first_page`
  failed on the task base: after two sweeps the independent 101st due run was
  never submitted because scheduled candidates always started at page one.
- `test_verified_loader_rechecks_generation_after_restoring_warm_cache_bytes`
  failed on the task base: a paused warm reader returned the original cached
  package after a concurrent forced reverify replaced it.

## GREEN

- The coordinator retains a separate, leadership-term scheduled cursor while
  preserving its existing periodic cursor. Each `_lead` term resets scheduled
  paging to page one; exhausted scans also reset, so restart and newly-due
  discovery remain safe.
- A warm cache hit snapshots request-owned budget contents, rechecks generation
  after restoration, and restores the exact pre-hit budget before a clean miss.
  Tree hashing remains outside the cache lock.

## Evidence

- Targeted RED and GREEN commands were run with `.venv/bin/python -m pytest`.
- Focused GREEN: coordinator/showcase targeted regressions passed; the
  coordinator and catalog two-file matrix passed `94 passed`.
- Restart RED: `test_new_leadership_term_restarts_scheduled_paging_at_page_one`
  failed because `_scheduled_sweep_cursor` retained the prior term's final key.
  GREEN: that test and the 101-row page test passed together (`2 passed`).

## Scope and concerns

Only the Task 8 owned files were changed. No API, configuration, model tool,
digest, MCP, or caller-authority surface changed. The required eight-file
workflow matrix passed `247 passed`.
