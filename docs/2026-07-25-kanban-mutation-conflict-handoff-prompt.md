# Handoff — finish the `TaskMutationConflict` caller gap + kanban test reconciliation

> Copy everything below the horizontal rule into a fresh session whose working
> directory is `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`.
> All analysis is already done; this is an implementation handoff, not an
> investigation. Every claim here was verified against the code on 2026-07-25 —
> re-verify anything you intend to rely on, but you should not need to re-derive it.

---

## 0. STOP — read this first or your test results will be garbage

`scripts/run_tests.sh` probes `$REPO_ROOT/.venv` **before** `$REPO_ROOT/venv`. On
2026-07-25 the local `.venv` was **Python 3.13 with none of the project extras**,
while CI uses:

```bash
uv sync --locked --python 3.11 --extra all --extra dev
```

Consequence: **1309 tests silently did not run** (`ModuleNotFoundError: No module
named 'acp'`) and several more failed for interpreter reasons — all of which looks
identical to real regressions. This wasted a lot of the previous session.

`.venv` has since been re-provisioned with CI's exact command. **Verify before
trusting any result:**

```bash
.venv/bin/python -V                      # expect Python 3.11.x
.venv/bin/python -c "import acp"         # must succeed silently
```

If either fails, re-provision:

```bash
rm -rf .venv
uv sync --locked --python 3.11 --extra all --extra dev --python-preference managed
```

**Never call `pytest` directly for a verdict** — always `scripts/run_tests.sh`
(it enforces CI parity: unset credential vars, `TZ=UTC`, `LANG=C.UTF-8`,
subprocess-per-test-file). Bare `pytest` runs all files in one process and will
show cross-file contamination that `run_tests.sh` does not.

Also: a stray `~/.qwen/oauth_creds.json` containing `test-access-token` was
overriding a test's own mock. It is moved to
`~/.qwen/oauth_creds.json.test-junk-moved-by-claude`. If qwen-oauth tests fail
again, check whether a test re-created it — the durable fix is for that test to
patch `hermes_cli.auth._qwen_cli_auth_path`, which it currently does not.

## 1. Current state

- **Branch:** `base` (this is the fork's development main; literal `main` is
  upstream-sync only). Tree clean at handoff.
- **HEAD:** `e7a0e763e test(file_tools): assert canonical paths in write/patch handlers`
- **Released:** OTTO/LOOP24 **v4.0.2** are built and published as full releases
  (otto `257bebfb0`, loop24 `34f480181`). This work is NOT in them and does not
  block the pending corporate-laptop checkpoint
  (`docs/plans/2026-07-25-enrolled-browser-checkpoint-runbook.md`).
- **Desktop suite: fully green** — 319 files, 2964 passed, 0 failed, plus 34
  `node:test` assertions that previously never executed.

Already fixed and committed (do not redo):

| Commit | What |
|---|---|
| `495e14e6c` | electron `node:test` split + `profile-theme` dark default |
| `abc033a29` | outlook-mcp `stdin=` (real bug; also fixed in vendor source) |
| `1e08e07a0` | yuanbao / tools_config brand-curation isolation; `test_approval` macOS symlink |
| `e7a0e763e` | `test_file_tools` canonical paths |

## 2. The finding this handoff exists to act on

Downstream commit `cc7df69aa "feat(kanban): add mutation preconditions"`
(2026-07-17, ours — NOT upstream) changed the `expected_run_id` contract in
`hermes_cli/kanban_db.py`:

- **Before:** a stale `expected_run_id` made `complete_task` / `block_task` /
  `heartbeat_worker` return **falsy**.
- **After:** it raises **`TaskMutationConflict`** (`kanban_db.py:1194`, the only
  raise site; the legacy arg is funnelled into a `TaskMutationPrecondition` by
  `_with_expected_run`, whose docstring says *"Preserve the legacy
  expected_run_id argument without ambiguity"*).

The design change is **good** — `False` conflated "stale run" with "unknown id"
and "already terminal". The safety property is intact: a stale worker still
cannot complete/block/heartbeat another run.

**The gap:** only the dashboard learned about the exception. `TaskMutationConflict`
is caught in exactly **3 places, all in `plugins/kanban/dashboard/plugin_api.py`**
(lines 1094, 1169, 1685). There are **8 production call sites** passing
`expected_run_id`, and the CLI ones are unprotected.

**Do not just make the tests green.** Updating the assertions without fixing the
callers would leave the suite certifying a behaviour the CLI mishandles — green
would mean less than it does today.

## 3. Verified caller map

`git grep -n "expected_run_id=" -- . ':!tests/' ':!*.md'`

### CLI — `hermes_cli/kanban.py` — ALL FOUR UNPROTECTED → raw Python traceback

| Line | Function | Current stale-path behaviour |
|---|---|---|
| 1425 | `_cmd_heartbeat` (1419–1434) | no `except Exception` at all |
| 2199 | `_cmd_complete` (2119–2208) | has two `except Exception`, but both are **nested inside earlier** try blocks (JSON parse, judge) and do **not** wrap the `kb.complete_task` call |
| 2251 | `_cmd_block` (2236–2273) | no `except Exception` |
| 2286 | `_cmd_schedule` (2273–2295) | no `except Exception` |

Each of these currently has an `if not <call>: print("cannot ...", file=sys.stderr); return 1`
path that is now **unreachable** for the stale case. Intended messages to preserve:

- `_cmd_heartbeat`: `cannot heartbeat {task_id} (not running?)`
- `_cmd_complete`: `cannot complete {tid} (unknown id or terminal state)`

### Agent tools — `tools/kanban_tools.py` — caught, but degraded

| Line | Function | Behaviour |
|---|---|---|
| 674 | `_handle_complete` (539–718) | specific handler catches only `ArtifactPreservationError`; falls to the function's outer `except Exception` → `logger.exception(...)` + `tool_error(f"kanban_complete: {e}")` |
| 773 | `_handle_block` (718–799) | outer `except Exception` only |
| 837 | `_handle_heartbeat` (799–853) | outer `except Exception` only |
| 309 | `heartbeat_current_worker_from_env` (264–…) | **already correct** — `except Exception` → `logger.debug`. Leave alone. |

Two problems at these three sites:
1. The agent loses the curated message (e.g. `could not complete {tid} (unknown id or already terminal)`, `kanban_tools.py:703-706`) and gets the raw exception string.
2. `logger.exception(...)` emits a **full ERROR-level stack trace for a routine, expected condition** — noise that buries real errors.

## 4. Step 1 — fix the callers (do this FIRST)

Catch `TaskMutationConflict` at each of the 7 sites that need it (all except
`kanban_tools.py:309`) and map it to the intended user-facing outcome:

- **CLI (4 sites):** print a stale-specific message to `sys.stderr` and return a
  non-zero exit code, matching the surrounding style. Distinguish it from the
  existing "unknown id / not running" message — that distinction is the whole
  point of the new exception. `TaskMutationConflict` carries
  `.current` (a `TaskMutationSnapshot` with `task_id`, `status`,
  `current_run_id`, `latest_event_id`) — use it for a genuinely useful message.
- **Agent tools (3 sites):** catch it in the *specific* handler, before the outer
  catch-all, and return `tool_error(...)` with actionable guidance ("your run is
  no longer the current run for this task; re-read the task and retry"). This
  also stops the spurious `logger.exception` stack traces.

Suggested import style: these modules already reference `kanban_db as kb`, so
`except kb.TaskMutationConflict as exc:`.

**Add regression tests for the caller behaviour** — the existing tests only cover
`kanban_db`, which is why this gap survived. At minimum: one CLI test asserting a
stale `complete` exits non-zero with a message and **no traceback**, and one
agent-tool test asserting `tool_error` rather than a generic exception string.

## 5. Step 2 — reconcile the 3 mechanical kanban tests

Only after Step 1. File: `tests/hermes_cli/test_kanban_core_functionality.py`.

1. **`test_stale_run_cannot_complete_new_attempt`** — line ~1539
   `assert not kb.complete_task(...)` → expect `pytest.raises(kb.TaskMutationConflict)`.
2. **`test_stale_run_cannot_block_or_heartbeat_new_attempt`** — lines ~1580-1581
   `assert not kb.heartbeat_worker(...)` and `assert not kb.block_task(...)` → same treatment.
3. **`test_reclaim_task_resets_running_to_ready`** — line ~4175
   The reclaim event no longer carries `termination_attempted`/`terminated`.
   `cc7df69aa` put `"termination_pending": bool(worker_pid)` in the reclaim event
   (`kanban_db.py:4587`) and moved the termination *outcome* into a separate
   `reclaim_termination` event. Assert `termination_pending` on the reclaim event,
   and move the `termination_attempted`/`terminated` assertions onto the
   `reclaim_termination` event so the coverage is not silently dropped.
   Related: `_worker_survived_termination` (`kanban_db.py:~7046`) still reads
   `termination_attempted` + `host_local` + `terminated`, so those fields do
   still exist — on the other event.

## 6. Step 3 — the 2 dashboard tests ⚠ NEEDS A DECISION FROM COREY

`test_dashboard_direct_status_change_off_running_closes_run` and
`test_dashboard_direct_status_change_within_same_state_is_noop_for_runs` fail with:

```
ImportError: cannot import name '_set_status_direct' from 'plugins.kanban.dashboard.plugin_api'
```

Downstream commit `4f8cd4622 "feat(kanban): harden desktop board api"` (ours)
**deleted** `_set_status_direct`. Status changes now flow through the public
`update_task` (`plugin_api.py:962`) / `bulk_update` (`:1236`) with
`_mutation_precondition`.

**Do not improvise these.** Rewriting them means reconstructing the correct
payload and precondition against the new API, and a subtly wrong rewrite yields a
**green test that asserts nothing** — worse than the current red, and the exact
opposite of the goal. Ask Corey to confirm the intended `update_task` semantics
for (a) moving a task off `running` (should it close the run?) and (b) a
same-state no-op, then implement against that.

## 7. Governance — REQUIRED, same commit as the code

`hermes_cli/kanban_db.py`, `plugins/kanban/dashboard/plugin_api.py`, and
`tools/kanban_tools.py` are all **UNION seams** in
`docs/upstream-customizations/workflow-orchestration.yaml` (78 entries).

- Add/extend a ledger entry describing the `expected_run_id` → exception contract
  and the caller obligation, so a future merge cannot silently restore the old
  `if not ...` pattern.
- Add the new caller tests to that entry's `tests:` list. **The previous session
  found that none of the three affected workflow test files were listed** — that
  omission is precisely why this drift went uncaught.
- Validate: `.venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml` (**exit 0**).

Context worth knowing: that ledger was **broken for an entire release cycle**
because one entry pointed at a file upstream deleted (`desktop-controller.tsx`).
A stale path makes the checker **abort**, silently disabling protection for all
78 entries. Fixed in `495e14e6c`; do not reintroduce.

## 8. Verification

```bash
# environment sanity FIRST (see §0)
.venv/bin/python -V && .venv/bin/python -c "import acp"

scripts/run_tests.sh tests/hermes_cli/test_kanban_core_functionality.py   # target: 172 passed, 0 failed
scripts/run_tests.sh tests/hermes_cli/test_kanban_db.py tests/plugins/workflow/
.venv/bin/python scripts/check_upstream_customizations.py \
  --manifest docs/upstream-customizations/workflow-orchestration.yaml     # exit 0

# CLI smoke — must print a message, NOT a traceback
hermes kanban complete <stale-task-id> ; echo "exit=$?"
```

The brand generator gate (`generate <brand> --check`) **fails on `base` by
design** — base is neutral, so the 8 emitter-covered files hold upstream Hermes
values. Do not "fix" it here; it runs after `base → otto` / `base → loop24`.

## 9. Out of scope — the remaining suite failures

Do **not** fold these in. Recorded so they are not lost.

**Platform-specific (8) — expected to pass on Linux CI, fail on a macOS dev box:**
`test_live_system_guard_self_test` (4, needs `systemctl`),
`test_execution_flag_detection` (3, probes real BSD vs GNU `man`/`sort`),
`test_computer_use` (1, Linux gnome-shell helper — upstream test, upstream-owned).
These need a policy decision (skip markers vs a documented expected-fail list),
not ad-hoc fixes.

**Not yet diagnosed (~15):** `test_update_autostash` (7 — biggest single item,
likely one root cause), `test_service_manager` (2), `test_web_ui_build` (1),
`test_feishu` (1), `test_workflow_detail_api` (1),
`test_desktop_workflow_test_gate` (1), `test_approved_command_clean_slate` (1),
`test_mcp_tool_issue_948` (1).

**Three files that do not run in the full parallel sweep but pass in isolation**
(`test_auxiliary_client`, `test_slack`, `test_telegram_noise_filter`) — almost
certainly load timeouts under `-n auto` with 56 workers, not import errors.

**CI does not run on this branch.** `.github/workflows/ci.yml` triggers on
`pull_request` and `push: branches: [main]` only. Since development pushes
directly to `base`, no Python or JS tests run in CI on `base`/`otto`/`loop24` —
the reason all of the above accumulated unnoticed. Enabling it is a one-line
change with a cleanup tail; it needs the platform-test policy decided first.

## 10. Traps

- Don't make the kanban tests green without Step 1 (§2).
- Don't improvise the two dashboard rewrites (§6).
- Don't trust a test result without checking `.venv` is 3.11 with extras (§0).
- Don't use bare `pytest` for a verdict (§0).
- Don't "fix" a `generate <brand> --check` failure on `base` (§8).
- Don't touch `kanban_tools.py:309` — already correct.
