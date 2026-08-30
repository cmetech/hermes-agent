# Workflow Language Phase 6 — Adversarial Review (Claude lane)

**Reviewer lane:** Claude (independent; did not implement Phase 6)
**Date:** 2026-08-30
**Production candidate:** `d850707a25d0eb161d3bedd2db935d01f3573255`
**Merge base:** `1001a6705563a2f2a001b4ad8a608a2d12a6ad33`

---

## 1. Scope verification and starting state

All scope checks were run in the detached review checkout at
`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-loop-groups-phase-6-review-claude`.

| Check | Command | Result |
| --- | --- | --- |
| Working tree | `git status --short --branch` | `## HEAD (no branch)` — clean, detached |
| Exact HEAD | `git rev-parse HEAD` | `d850707a25d0eb161d3bedd2db935d01f3573255` ✔ matches candidate |
| Merge base | `git merge-base base HEAD` | `1001a6705563a2f2a001b4ad8a608a2d12a6ad33` ✔ matches |
| Ancestry | `git merge-base --is-ancestor 1001a67055 HEAD` | exit 0 (merge base is an ancestor) |
| Commit count | `git rev-list --count 1001a67055..d850707a25` | `29` ✔ matches |
| Changed paths | `git diff --name-status 1001a67055..d850707a25 \| wc -l` | `113` ✔ matches |
| Diff summary | `git diff --shortstat 1001a67055..d850707a25` | `113 files changed, 20613 insertions(+), 1351 deletions(-)` ✔ matches |
| Whitespace/conflict | `git diff --check 1001a67055..d850707a25` | no output, exit 0 |

No scope error. The detached checkout is byte-identical to the production
candidate.

**Environment note.** The review worktree has no local `.venv`/`venv`, and
`$HOME/.hermes/hermes-agent/venv` has no pytest, so `scripts/run_tests.sh`
refuses to start unaided. I used the runner's own documented fallback,
`HERMES_PYTHON=<main checkout>/.venv/bin/python`, after verifying that the
editable-install finder is appended to `sys.meta_path` (it therefore loses to
`sys.path`) and that `plugins.workflow.language` resolves to the **review
worktree** file, not the main checkout. All test output below therefore
exercises the candidate tree.

Review method: read the final production files and their unchanged callers;
the combined package diff was used only as an inventory. All probes were run
against synthetic workflows in `mkdtemp` directories with `HERMES_HOME`
redirected out of tree and with no network. No prior reviewer verdict and no
SDD progress ledger was read before reaching these findings.

---

## 2. Verdict

**BLOCK** — 5 IMPORTANT findings. No CRITICAL findings.

The language, admission, bounds, sealing, scoped-reference, tool-call-contract,
public-projection, profile-isolation, packaging, and Jira-migration work is
solid and holds up under attack. The defects are concentrated in one seam:
**durable-recovery and operator-lifecycle code paths that were written before
nested node state existed and were never widened to see it**, plus the
`artifacts: false` publication-accounting design.

Concretely, every `loop_group` run that reaches a `failed` or `interrupted`
state is unrecoverable: the two operator recovery actions (`resume`, `retry`)
silently and permanently wedge it, and `abandon` terminalizes it while a body
executor may still be alive.

---

## 3. Findings

| ID | Severity | Location | Summary |
| --- | --- | --- | --- |
| P6-01 | IMPORTANT | `plugins/workflow/store.py:18616`, `:19929` | `resume_run`/`retry_run` reset only the outer `loop_group` node and leave the nested controller/body state stale, permanently wedging the run in `running` with no work, no error, and the concurrency key held. |
| P6-02 | IMPORTANT | `plugins/workflow/scheduler.py:2811` + `plugins/workflow/executors/bash.py:60` | The `loop_group` `until_bash` predicate attempt directory is iteration-scoped, not attempt-scoped; any post-crash predicate re-dispatch raises an unhandled `FileExistsError` out of `advance_all()`, permanently. |
| P6-03 | IMPORTANT | `plugins/workflow/store.py:18604`, `:20256` | The "prior executor still running / outcome uncertain" guards on `resume_run` and `_set_terminal` iterate only top-level nodes, so a loop-group body child with a live, unproven executor is invisible; `abandon` terminalizes the run and deletes that child's retained `worker_claims` row. |
| P6-04 | IMPORTANT | `plugins/workflow/executors/bash.py:66-75,350-362`; `plugins/workflow/executors/script.py:356-365,538-549` | `artifacts: false` compares the publication tree against the state at attempt start, so residue left by a prior failed/interrupted attempt of the **same node** is baselined in and survives a later successful attempt. |
| P6-05 | IMPORTANT | `plugins/workflow/executors/base.py:129-131` + `plugins/workflow/scheduler.py:2259-2267` | A top-level `artifacts: false` node snapshots the whole shared `run/artifacts` tree, which contains every loop-group publication subtree; a concurrent sibling or body-child publication fails the node with `artifact_limit` and forces operator reconciliation on a correct workflow. |

---

## 4. Full proofs

### P6-01 — IMPORTANT — `resume`/`retry` permanently wedge any loop-group run

**1. ID and severity.** P6-01, IMPORTANT.

**2. Exact production location at the candidate.**
- `plugins/workflow/store.py:18616-18632` (`resume_run` node loop)
- `plugins/workflow/store.py:19927-19955` (`retry_run` candidate selection and state reset)
- Consumers of the resulting state: `plugins/workflow/scheduler.py:2445`
  (`state.get("state") == "ready" and state.get("loop_group") is None`) and
  `plugins/workflow/scheduler.py:2497-2503` (`if state.get("state") != "running": continue`)
- Operator entry points: `plugins/workflow/cli.py:2660` (`hermes workflow resume`),
  `plugins/workflow/dashboard/plugin_api.py:2985` (`resume`) and `:2992` (`retry`),
  offered as `next_actions` by `plugins/workflow/actions.py:85-87`.

**3. Violated invariant.** Locked invariants 5 ("terminal/continue decisions are
deterministic across restart") and 9 ("crash boundaries cannot … finish early");
spec §"Recovery" ("a controller with no active child resumes from its last
committed body or iteration transition").

**4. Realistic trigger and step-by-step production path.**
1. A `loop_group` run fails for any ordinary reason — a body node fails
   (`fail_loop_group`, `scheduler.py:2608`), the group hits
   `loop_group_max_iterations`, or a coordinator crash expires a child lease and
   `expire_stale_claims` (`store.py:17330`) marks the run `interrupted`.
2. The operator sees `resume`/`retry` in `next_actions` (`actions.py:85-87`) and
   clicks Resume in Desktop or runs `hermes workflow resume <run>`.
3. `resume_run` iterates `projection["nodes"].items()` — **top-level nodes only**
   (`store.py:18616`). The `loop_group` node is set to `state="ready"` and
   `projection["last_error"]` is cleared. `node["loop_group"]` — the durable
   controller, its `state`, and its `body` child states — is never touched.
   `retry_run` does the same at `store.py:19944-19955`.
4. On the next scheduler pass, `_advance_loop_group_controllers`
   (`scheduler.py:2426`) refuses both branches:
   - initialization requires `state.get("loop_group") is None` (`:2445`) — it is
     not `None`, so the controller is not re-created;
   - advancement requires `state.get("state") == "running"` (`:2497-2503`) — it
     is `"ready"`, so it `continue`s.
5. `_ready_work_items` (`scheduler.py:2054`) still emits ready body children, but
   `claim_loop_group_child` refuses them because `group.get("state") != "running"`
   (`store.py:11284-11288`). Nothing is claimable.

**5. Concrete wrong result and consequence.** The run flips from a clear terminal
`failed` (with `last_error`) to `running` **forever**, with `last_error` cleared,
zero executor dispatches, and `next_actions` reduced to
`['status', 'events', 'cancel']`. Because the wedged run is non-terminal it keeps
holding its concurrency key, so a fresh admission of the same workflow is
`queued` rather than `created` — for `jira-defect-loop`, whose sidecar sets
`overlap_policy: forbid`, the workflow is blocked until an operator manually
cancels the zombie run. The operator has also lost the original failure cause.

**6. Code evidence plus bounded reproduction.**

`store.py:18616-18632` (resume):

```python
for node_id, node in projection["nodes"].items():   # top-level only
    if node["state"] == "succeeded" and node_id not in always_run:
        continue
    node.pop("claim", None)
    ...
    node["state"] = ("ready" if all(...) else "pending")
projection["last_error"] = None
```

Compare `reconcile_run` (`store.py:20063-20090`), which *does* maintain nested
state (`if "/" in selected_id: … controller["state"] = …`), and `cancel_run`
(`store.py:18492`), which uses `_iter_projection_node_states` — the nested-aware
iterator. `resume_run`/`retry_run` were not widened.

Bounded reproduction (synthetic workflow, temp `HERMES_HOME`, no network;
`/tmp/p6probe/probe_resume.py`) — a two-node body where `worker` fails once:

```
after failure: status = failed | last_error = validation
  group state: failed | controller: failed | iteration: 1
  body: {'sink': 'cancelled', 'worker': 'failed'}
after operator action (resume): status = running
  group state: ready | controller: failed | iteration: 1
  body: {'sink': 'cancelled', 'worker': 'failed'}
advance 1: status=running groupstate=ready controller=failed executor_calls=[]
advance 2: status=running groupstate=ready controller=failed executor_calls=[]
advance 3: status=running groupstate=ready controller=failed executor_calls=[]

--- consequences ---
run status: running | last_error: None
next_actions: ['status', 'events', 'cancel']
second admission disposition: queued | reason: None
```

Re-running the same probe with `P6_USE_RETRY=1` (i.e. `store.retry_run(run_id,
node_id="group")` instead of `resume_run`) produces byte-identical wedge output,
confirming both operator actions share the defect.

The crash variant is identical (`/tmp/p6probe/probe_resume2.py`): claim a body
child, let its lease expire via `store.expire_stale_claims`, then resume:

```
after crash: status = interrupted | group: running | controller: running
             | body: {'sink': 'pending', 'worker': 'interrupted'}
after resume: status = running   | group: ready   | controller: running
             | body: {'sink': 'pending', 'worker': 'interrupted'}
advance 1..3: status=running group=ready controller=running calls=[]
final next_actions: ['status', 'events', 'cancel']
```

**7. Why existing tests miss it.** `resume_run` and `retry_run` are referenced
**zero** times across all eight Phase 6 test files
(`grep -c 'resume_run\|retry_run' tests/plugins/workflow/test_phase6_*.py` → 0
for every file). The Phase 6 recovery suite exercises `fail_loop_group`,
`expire_stale_claims`, `reconcile_run`, `cancel_run`, and the store restart path,
but never the two operator actions that the API itself advertises for exactly
these run states. The pre-Phase-6 `test_crash_recovery.py` resume tests use
top-level nodes only, where the code is correct.

**8. Smallest safe root-cause remediation.** In `resume_run` and `retry_run`,
after resetting an outer node that carries `node["loop_group"]`, reset the
controller in the same transaction: clear `_pending_group_transition` /
`_pending_loop_decision` only if they are unrecoverable, set
`controller["state"] = "running"`, and re-derive each body child's state from its
dependency closure (`"pending" if depends_on else "ready"`, preserving
`succeeded`/`skipped` children of the current iteration and dropping stale
claims) — i.e. reuse the body-reset expression already present in
`_drain_loop_group_transition_locked` (`store.py:11554-11563`). Alternatively,
refuse the action with a stable code when the outer node is a `loop_group` whose
controller is not resumable, so the run stays terminal and legible rather than
becoming a silent zombie.

**9. Required regression test.** For each of `resume_run` and `retry_run`, and
for each of the `failed` and `interrupted` entry states: admit a v6 `loop_group`
run, drive it to that state, invoke the action, then assert that a subsequent
`RunScheduler.advance_all()` **either** dispatches at least one body executor and
reaches a terminal status, **or** that the action raised a stable refusal and the
run remained terminal. Assert explicitly that the run never sits in `running`
with an empty ready set across three consecutive scheduler passes.

---

### P6-02 — IMPORTANT — `until_bash` predicate re-dispatch raises `FileExistsError` forever

**1. ID and severity.** P6-02, IMPORTANT.

**2. Exact production location at the candidate.**
- `plugins/workflow/scheduler.py:2758-2766` — the predicate publication path is
  `…/artifacts/loop-groups/<group>/iterations/<NNNN>/decision`, keyed by
  **iteration**, with no attempt component.
- `plugins/workflow/scheduler.py:2811` — `attempt_directory=publication / "attempt"`.
- `plugins/workflow/executors/bash.py:59-60` —
  `attempt = context.effective_attempt_directory; attempt.mkdir(parents=True, exist_ok=False)`.
- Re-dispatch enablers: `plugins/workflow/store.py:12113`
  (`claim_recorded_loop_group_predicate` re-claims a stale predicate lease) and
  `plugins/workflow/store.py:12247` (`prepare_recorded_loop_group_predicate`
  authorizes up to 8 predicate recoveries).

**3. Violated invariant.** Locked invariants 8 ("isolating nested attempts …
processes, artifacts") and 9 ("crash boundaries cannot duplicate work, lose a
required result … or finish early"); spec §"Failure, cancellation, and
recovery".

**4. Realistic trigger and step-by-step production path.**
1. An admitted `archon-2026-07` v6 workflow declares
   `loop_group: { until: DONE, until_bash: "…", … }`. `until_bash` is an
   admitted `loop_group` field (`LOOP_GROUP_FIELDS`, validated at
   `schema.py:1079-1087`).
2. The body finishes an iteration without the completion signal, so the
   controller journals `until_bash_pending`, records a predicate claim under the
   deterministic attempt id `loop-group-predicate-<gen>-<NNNN>`
   (`store.py:11851-11855`), and dispatches the real `BashExecutor`
   (`scheduler.py:2775-2813`).
3. `BashExecutor.execute` creates `…/iterations/0001/decision/attempt` with
   `exist_ok=False` and runs the predicate.
4. The coordinator process dies (SIGKILL, host restart, container eviction, or a
   crash anywhere between predicate completion and
   `record_loop_group_predicate_decision`). The decision is not journaled.
5. A later coordinator pass re-claims the predicate
   (`claim_recorded_loop_group_predicate`, `store.py:12206-12237`),
   `prepare_recorded_loop_group_predicate` confirms the prior process stopped and
   returns `True`, and the scheduler dispatches the predicate again — with the
   **same** `publication / "attempt"` path.
6. `attempt.mkdir(parents=True, exist_ok=False)` raises `FileExistsError`.

**5. Concrete wrong result and consequence.** `FileExistsError` propagates out of
`BashExecutor.execute` → `_advance_loop_group_controllers` → `_resolve_graph` →
`RunScheduler.advance_all()`. It is not caught anywhere on the path. In the
foreground CLI path (`hermes workflow run`, `hermes workflow resume`) the
operator gets a raw traceback and the run is left `running`. In the background
coordinator path the exception is raised inside
`RunScheduler.submit`'s pool callable (`scheduler.py:6270-6276`), whose `Future`
is never awaited, so it is **silently discarded** — the run simply never
progresses again and no error is logged. The run is wedged permanently: every
subsequent pass takes the same branch and fails identically. It holds its
concurrency key indefinitely (`overlap_policy: forbid` blocks new runs) and the
only remaining action is `cancel`.

**6. Code evidence plus bounded reproduction.**

```python
# scheduler.py:2758-2766, 2811
publication = (run_directory / "artifacts" / "loop-groups" / group.id
               / "iterations" / f"{scope.iteration:04d}" / "decision")
...
attempt_directory=publication / "attempt",     # no attempt id
```

```python
# executors/bash.py:59-60
attempt = context.effective_attempt_directory
attempt.mkdir(parents=True, exist_ok=False)
```

Every other executor path derives the attempt directory from the attempt id —
`effective_attempt_directory` defaults to `run/nodes/<node.id>/<attempt_id>`
(`base.py:124-127`), and the nested body path appends
`/<attempt_id>` (`scheduler.py:2248-2258`). The ordinary v4 loop executor also
scopes per iteration *inside* an already attempt-scoped root
(`executors/loop.py:351-359`). The group predicate is the only place where a
process attempt directory omits the attempt id.

Bounded reproduction (`/tmp/p6probe/probe_predicate.py`; synthetic workflow,
temp `HERMES_HOME`, real `BashExecutor`, `until_bash: "true"`, coordinator crash
simulated by making `record_loop_group_predicate_decision` raise once, then
expiring the predicate lease):

```
predicate attempt dir exists after crash: True
contents: ['stderr.txt', 'stdout.txt', 'variables']
events: [... 'loop_group_iteration_completed', 'loop_group_predicate_pending',
         'spawn_intent', 'process_started', 'process_reaped']
expired predicate lease; re-advancing
pass 1: RAISED FileExistsError: [Errno 17] File exists: '…/artifacts/loop-groups/group/iterations/0001/decision/attempt'
pass 2: RAISED FileExistsError: …
pass 3: RAISED FileExistsError: …
pass 4: RAISED FileExistsError: …
final run status: running desired: None
group state: running controller: running
recoveries: 1
```

Note `recoveries: 1` — the 8-recovery bound is never even consumed, because
`prepare_recorded_loop_group_predicate` short-circuits on subsequent passes
(`store.py:12290-12291`, `if not isinstance(spawn, Mapping): return True`). The
wedge is unbounded in time.

**7. Why existing tests miss it.** Every Phase 6 predicate test replaces the real
executor: `tests/plugins/workflow/test_phase6_interactions_recovery.py:148`
(`scheduler.executors["bash"] = PredicateExecutor()`), `:211`
(`FailedPredicate`), and the same pattern at `:697`. The stubs never touch the
filesystem, so `attempt.mkdir(exist_ok=False)` is never executed on the predicate
path at all — the only property they assert about the directory is that no worker
claim exists. There is also no test that dispatches the predicate twice for one
iteration.

**8. Smallest safe root-cause remediation.** Make the predicate attempt directory
attempt-scoped, mirroring every other executor call site:
`attempt_directory=publication / "attempts" / predicate_claim.attempt_id` is not
enough on its own because the predicate attempt id is deterministic per
iteration; include the recovery generation, e.g.
`publication / "attempts" / f"{predicate_claim.attempt_id}-{len(history)}"`, or
have `prepare_recorded_loop_group_predicate` mint a fresh recovery ordinal and
thread it into the path. A defensive `exist_ok=True` in `BashExecutor` would
mask, not fix, the identity collision and would let a recovered predicate observe
a prior attempt's `stdout.txt`.

**9. Required regression test.** With the **real** `BashExecutor`, admit a v6
`loop_group` with `until_bash`, run to `loop_group_predicate_pending`, simulate a
coordinator crash before `record_loop_group_predicate_decision`, expire the
predicate lease, and assert the next `advance_all()` completes without raising,
re-runs the predicate in a directory disjoint from the first attempt's, and
reaches a terminal decision. Add a second case that repeats the crash/recovery
cycle up to the 8-recovery bound and asserts a stable failure code rather than an
exception.

---

### P6-03 — IMPORTANT — live-executor guards on `resume`/`abandon` do not see body children

**1. ID and severity.** P6-03, IMPORTANT.

**2. Exact production location at the candidate.**
- `plugins/workflow/store.py:18604-18615` — `resume_run`'s
  "still running / outcome uncertain" guard iterates
  `projection["nodes"].values()`.
- `plugins/workflow/store.py:20256-20270` — `_set_terminal` (used by
  `abandon_run`, `store.py:20217-20229`) applies the same two guards over
  `projection["nodes"].values()`.
- `plugins/workflow/store.py:20312-20314` —
  `DELETE FROM worker_claims WHERE run_id=?` after those guards pass.
- Contrast: `expire_stale_claims` (`store.py:17360`) and `cancel_run`
  (`store.py:18492`) use the nested-aware `_iter_projection_node_states`
  (`store.py:264-290`).

**3. Violated invariant.** Locked invariant 9 ("crash boundaries cannot duplicate
work … or accept stale authority"); spec §"Recovery" ("a live process remains
owned and monitored rather than duplicated").

**4. Realistic trigger and step-by-step production path.**
1. A `loop_group` body child (`bash`/`script`) is claimed and records a process
   identity (`record_process_started`).
2. The coordinator crashes and restarts under a new owner epoch. On the next
   sweep, `expire_stale_claims` runs; `_reclaim_still_running_claim`
   (`store.py:17536-17556`) refuses to re-adopt because the owner epoch changed,
   so the body child gets
   `recovery = {observation: "still_running", termination_confirmed: False}`
   (`store.py:17421-17444`) and state `interrupted`, and the run becomes
   `interrupted`. The child's `worker_claims` row is deliberately **retained**
   because the process is still alive.
3. The operator issues `resume` or `abandon` (both are offered in
   `next_actions` for `interrupted` runs).
4. Both guards read `projection["nodes"].values()`, which yields only the outer
   `loop_group` node. The outer node has no `recovery` and no `claim` — the
   recovery record lives on `node["loop_group"]["body"]["<child>"]`. Both guards
   pass.

**5. Concrete wrong result and consequence.** For `resume`: the documented
protection `RuntimeError("cannot resume while the prior executor is still
running or its identity is uncertain")` never fires; the run is flipped to
`running` while a body executor is still alive (and, per P6-01, then wedges).
For `abandon`: `RuntimeError("cannot abandon while executor termination is
unproven")` never fires; the run is marked terminal `abandoned` **and**
`DELETE FROM worker_claims WHERE run_id=?` drops the retained claim of the live
process. The live process is now orphaned: it is no longer accounted for against
`max_total_workers`, it keeps writing stdout/stderr/artifacts into the run
directory, and the run is now in a status (`abandoned`) that makes its directory
eligible for `cleanup_runs` deletion (`store.py:20370-20376` selects
`status IN ('succeeded','failed','cancelled','abandoned')`) underneath a live
writer. The identical situation on a top-level node is correctly refused.

**6. Code evidence plus bounded reproduction.**

```python
# store.py:18604-18615 (resume_run) and 20256-20270 (_set_terminal) — both:
for node in projection["nodes"].values():        # top-level only
    recovery = node.get("recovery")
    if (isinstance(recovery, Mapping)
        and recovery.get("observation") in {"still_running", "outcome_uncertain"}
        and not recovery.get("termination_confirmed")):
        raise RuntimeError("cannot resume while the prior executor is still running …")
```

```python
# store.py:264-290 — the nested-aware iterator that these two call sites do not use
def _iter_projection_node_states(projection): ...
    yield scope.worker_node_id, child           # loop-group body children
```

Bounded reproduction (`/tmp/p6probe/probe_guards.py`; synthetic v6 group,
`ProcessIdentity.is_current` patched to report the child alive, lease expired):

```
run status: interrupted
body child recovery: still_running | termination_confirmed: False
top-level group node has recovery key: False
worker_claims rows for run: 1

-- resume_run (guard should refuse: prior executor still running) --
  RESUMED anyway: status = running | group: ready | child: interrupted

-- abandon_run (guard should refuse: termination unproven) --
  ABANDONED anyway: status = abandoned
  worker_claims rows for run after abandon: 0
  body child state after abandon: interrupted | controller: running
```

**7. Why existing tests miss it.** `tests/plugins/workflow/test_phase6_store.py`
builds exactly this state (`test_loop_group_failure_retains_live_child_until_cleanup_is_corroborated`,
`:522-570`) and correctly asserts that `fail_loop_group` refuses and that the
`worker_claims` row is retained — but it stops there. No Phase 6 test then
invokes `resume_run` or `abandon_run` against that state, and the pre-existing
`abandon`/`resume` guard tests use top-level nodes, where the guards work.

**8. Smallest safe root-cause remediation.** Replace
`projection["nodes"].values()` with `_iter_projection_node_states(projection)` in
both guards (`store.py:18604`, `:20256`, `:20266`). This is the same one-line
change already made for `expire_stale_claims` and `cancel_run`; the guard bodies
need no other modification.

**9. Required regression test.** Reuse the existing
`test_loop_group_failure_retains_live_child_until_cleanup_is_corroborated`
fixture; after the restart that yields
`recovery.observation == "still_running"` and `termination_confirmed is False` on
the body child, assert that `resume_run` raises the still-running refusal, that
`abandon_run` raises the unproven-termination refusal, and that the child's
`worker_claims` row survives both attempts. Add a matching case for a live body
child claim without recovery, asserting the "live executor claim" refusal.

---

### P6-04 — IMPORTANT — `artifacts: false` residue from a prior attempt survives a successful attempt

**1. ID and severity.** P6-04, IMPORTANT.

**2. Exact production location at the candidate.**
- `plugins/workflow/executors/bash.py:66-75` (baseline snapshot at attempt start)
  and `:350-362` (comparison at attempt end).
- `plugins/workflow/executors/script.py:356-365` and `:538-549` (same shape).
- `plugins/workflow/executors/base.py:147-206` (`publication_tree_snapshot`).
- `plugins/workflow/scheduler.py:5129-5135` (`max_artifact_bytes: 0` iff
  `artifacts is False`).

**3. Violated invariant.** Locked invariant 10 verbatim: "no artifact
attributable to that node across initial execution, crash, retry, resume, or
reconciliation may survive a successful attempt."

**4. Realistic trigger and step-by-step production path.**
1. An admitted v6 workflow declares a `bash` or `script` node with
   `artifacts: false` (a new, documented Phase 6 field — the shipped
   `capabilities/workflows/jira-defect-loop.yml` uses it on six nodes).
2. The node's sealed command performs an *idempotent* write into
   `$ARTIFACTS_DIR` — e.g. `[ -f "$f" ] || printf … > "$f"`, `mkdir -p`,
   `cp -n`, or "materialize a cache only when absent". `$ARTIFACTS_DIR` is
   exported to the process unconditionally, even when `artifacts: false`
   (`bash.py:157`, `script.py:388`).
3. Attempt 1 creates the file. The end-of-attempt snapshot differs from the
   start-of-attempt snapshot, so the executor returns
   `artifact_limit` (`bash.py:355-362`). **The offending file is not removed.**
4. `classify_failure` maps `artifact_limit` (not in `_TRANSIENT_FAILURES`, not in
   `_FATAL_FAILURES`, `known_no_effect=False`) to `UNKNOWN_OUTCOME`
   (`scheduler.py:385-390`), which pauses the node with a `reconcile`
   interaction (`scheduler.py:5864-5883`).
5. The operator resolves it with the documented `safe-to-retry` outcome;
   `reconcile_run` sets the node back to `ready` (`store.py:20058-20060`).
6. Attempt 2 captures a *new* baseline that already contains the residue, the
   idempotent command does not touch it, the snapshots match, and the node
   **succeeds**.

**5. Concrete wrong result and consequence.** A node declared `artifacts: false`
completes `succeeded` while a file it created is still present in the run's
publication tree. For a top-level node this is the shared `run/artifacts`
directory, so the residue is visible to every later `artifacts:false` baseline
and to any downstream consumer of the run's artifact area; for a body child it
persists in that iteration's publication directory. The guarantee that
`artifacts: false` means "this node publishes nothing" is silently broken on
exactly the recovery paths the invariant enumerates. The same mechanism applies
after a crash-interrupted attempt 1 (residue written, attempt aborted before the
comparison) followed by any successful re-execution.

**6. Code evidence plus bounded reproduction.**

```python
# executors/bash.py:66-75  — baseline is "whatever exists right now"
artifact_free_before = None
if context.max_artifact_bytes == 0:
    artifact_free_before = publication_tree_snapshot(artifacts_dir)
...
# executors/bash.py:350-362 — comparison is relative, never against empty
if artifact_free_after != artifact_free_before:
    return NodeExecutionResult("failed", tuple(artifacts), "artifact_limit", …)
```

Bounded reproduction (`/tmp/p6probe/probe_residue.py`; real `BashExecutor`, one
sealed idempotent command, two attempt ids, `max_artifact_bytes=0`):

```
attempt-1: status=failed    error=artifact_limit leak_present=True
attempt-2: status=succeeded error=None           leak_present=True
```

**7. Why existing tests miss it.** The `artifacts: false` coverage in
`tests/plugins/workflow/test_script_executor.py` and
`tests/plugins/workflow/test_phase6_execution_context.py` exercises a **single**
attempt against a directory whose relevant state does not already contain the
node's own residue. No test executes the same `artifacts: false` node twice with
the publication directory carrying forward, so the "baseline includes my own
prior leak" case is never reached. Mutation check: deleting the entire
`artifact_free_before`/`artifact_free_after` block still leaves attempt 2 green
in the current suite — only the first-attempt assertions fail.

**8. Smallest safe root-cause remediation.** For `max_artifact_bytes == 0`, do
not diff against a mutable baseline. Give the node an attempt-private
publication root (a fresh `…/<attempt_id>/` directory exported as
`$ARTIFACTS_DIR`), and after execution require that root to be **empty** rather
than unchanged; fail closed and delete the root if it is not. This also removes
the dependency on `st_mtime_ns`/`st_ino` equality that P6-05 exploits.

**9. Required regression test.** Execute the same sealed `artifacts: false`
command twice against one publication directory, where the command writes only
when the target is absent. Assert attempt 1 fails with `artifact_limit` **and**
that attempt 2 does not report `succeeded` while any file attributable to the
node remains. Add a crash variant where attempt 1 is aborted after the write and
before the comparison, driven through `reconcile_run(outcome="safe-to-retry")`.

---

### P6-05 — IMPORTANT — `artifacts: false` is not concurrency-safe against sibling or body-child publications

**1. ID and severity.** P6-05, IMPORTANT.

**2. Exact production location at the candidate.**
- `plugins/workflow/executors/base.py:129-131` —
  `effective_publication_directory` defaults to the **shared**
  `run_directory / "artifacts"` for every top-level node.
- `plugins/workflow/scheduler.py:2259-2267` — every loop-group body child's
  publication directory is created *inside* that same tree, at
  `run/artifacts/loop-groups/<group>/iterations/<NNNN>/<child>`.
- `plugins/workflow/executors/bash.py:69,352` and
  `plugins/workflow/executors/script.py:359,541` — `publication_tree_snapshot`
  walks and hashes that whole tree at attempt start and attempt end.
- `plugins/workflow/models.py:1118` — `max_parallel_nodes: int = 4` by default.

**3. Violated invariant.** Locked invariant 8 ("isolating nested attempts,
publications, processes, artifacts") and, in its concurrent form, invariant 10
("permits unrelated … prior publications").

**4. Realistic trigger and step-by-step production path.**
1. An admitted v6 workflow has a top-level `bash`/`script` node with
   `artifacts: false` that is ready at the same time as any other artifact-
   producing node — an ordinary sibling `script`, or a `loop_group` whose body
   children are executing. Nothing in the language forbids this and
   `max_parallel_nodes` is 4 by default.
2. The `artifacts: false` node captures `artifact_free_before` over
   `run/artifacts`.
3. While it runs, the concurrent node publishes. For a `loop_group` this needs no
   authored artifact at all: `bash.py:65` / `script.py:355` call
   `artifacts_dir.mkdir(parents=True, exist_ok=True)` for *every* body child on
   *every* attempt, creating
   `run/artifacts/loop-groups/<group>/iterations/<NNNN>/<child>` and mutating the
   ancestors' `st_mtime_ns`.
4. `publication_tree_snapshot` at attempt end differs. The node returns
   `artifact_limit`.

**5. Concrete wrong result and consequence.** A correctly authored node that
created nothing is failed with `artifact_limit`, which `classify_failure` maps to
`UNKNOWN_OUTCOME` (`scheduler.py:385-390`), pausing the node with a `reconcile`
interaction and the run with it (`scheduler.py:5864-5883`). The operator is asked
to reconcile an "unknown outcome" for a node that provably did nothing, and the
outcome is timing-dependent: the same workflow succeeds when the sibling happens
not to overlap. No shipped workflow currently interleaves this way
(`jira-defect-loop`'s four top-level `artifacts: false` nodes are all
`when`-exclusive or strictly sequential), but any adopter following the
documented `artifacts: false` guidance in a parallel DAG — including one that
simply places such a node beside a `loop_group` — hits it.

**6. Code evidence plus bounded reproduction.**

```python
# executors/base.py:129-131
@property
def effective_publication_directory(self) -> Path:
    return self.publication_directory or self.run_directory / "artifacts"
```

```python
# scheduler.py:2259-2267 — nested publications live under the same root
publication = (run_directory / "artifacts" / "loop-groups" / scope.group_id
               / "iterations" / f"{scope.iteration:04d}" / scope.node_id)
```

Bounded reproduction (`/tmp/p6probe/probe_artifacts.py`; real `BashExecutor`,
`artifacts: false`, an unrelated `prior.txt` already published, and a concurrent
thread that performs only the body-child directory creation from
`scheduler.py:2259-2267`):

```
sibling published during execution: True
status      : failed
error_code  : artifact_limit
error_msg   : bash artifacts are disabled for this node
control (no concurrent sibling) status: succeeded None
```

The control run proves the "unrelated **unchanged** prior publication"
allowance works (`prior.txt` is present in both runs); only the concurrent
mutation trips it. An earlier variant of the same probe that wrote a plain
`sibling.txt` file produced the identical failure.

**7. Why existing tests miss it.** Every `artifacts: false` test executes a
single node in isolation against a quiescent publication directory. There is no
test that runs an `artifacts: false` node concurrently with any other publisher,
and the loop-group execution-context tests
(`tests/plugins/workflow/test_phase6_execution_context.py`) assert only that the
scoped directories are *distinct paths*, not that they are outside any other
node's accounting root.

**8. Smallest safe root-cause remediation.** Same fix as P6-04: scope the
`artifacts: false` accounting to an attempt-private publication root rather than
diffing a shared tree. If the shared `$ARTIFACTS_DIR` semantics must be
preserved for top-level nodes, at minimum exclude the `loop-groups/` subtree from
`publication_tree_snapshot` and restrict the comparison to entries the node
itself could have produced (e.g. by comparing only paths absent from the
run-level artifact descriptor list).

**9. Required regression test.** Run an `artifacts: false` top-level node
concurrently with (a) a sibling `script` node that publishes an artifact and
(b) a `loop_group` body child that merely creates its scoped publication
directory. Assert the `artifacts: false` node succeeds in both cases and that
neither concurrent publication is attributed to it.

---

## 5. Twenty-row invariant matrix

| # | Invariant | Verdict | Evidence |
| --- | --- | --- | --- |
| 1 | v6 current for Archon only; legacy v2; v1-v5 exact replay; snapshot format 2 | **PASS** | `language.py:36-42` (`ARCHON_2026_07: 6`, `HERMES_LEGACY: 2`, supported `{1..6}`); `select_normalizer_version` rejects ≥3 for non-Archon (`:524-531`); `_normalize_v6` is gated on `supports_phase6_semantics` (`:369`, `:933`); no format-3 envelope anywhere in the diff. Phase 3/4/5 language + snapshot + execution-semantics suites all green (408 tests). |
| 2 | Admission rejects nested groups/includes/runtime workflows/group retry/invalid scopes/products/resources before run creation | **PASS** | `schema.py:1119-1133` (rejects `include`/`workflow`/`loop_group` in a body), `:1282-1292` (`loop_group_version_unsupported`, `retry is not supported on durable loop groups`), `:1102-1106` (512-node body), `:1166-1171` (4096 edges), `:1173-1183` (`_LOOP_GROUP_WORK_LIMIT = 4096` on `max(child_executions, child_attempts)`), `:1184-1210` (cross-scope deps, cycles). `compile_workflow` runs `validate_v6_provider_capacity` + `validate_v6_storage_capacity` (`compilation.py:252-263`) before dependency digests and before any run exists. 26 admission tests green. |
| 3 | Identities authenticated, bounded, canonical; never caller paths or worker keys | **PASS** | `LoopGroupChildScope.__post_init__` (`models.py`) validates `run_id` against `_STORE_RUN_ID` and `group_id`/`node_id` against `_PORTABLE_NODE_ID`; `worker_node_id` is a derived property and `from_durable_record` re-derives and compares it. Node ids are `[A-Za-z_][A-Za-z0-9_-]*` (`language_schema.py:75`) — no `.`, `/`, or `..` — and body ids are re-checked with `is_reference_safe_node_id` (`schema.py:1295-1301`). `_scoped_directories` re-resolves and asserts `is_relative_to(run_directory)` (`scheduler.py:2268-2271`). No API surface accepts a worker key. |
| 4 | Controller durable and workerless; body claims consume existing capacity; no second scheduler | **PASS** | `claim_loop_group_child` delegates to the shared `_claim_ready_node_locked` → `_insert_worker_claim_if_capacity` (`store.py:10971-11000`), which enforces `self.limits["workers"]` and `max_run_workers` against the same `worker_claims` table. No new table or pool in the diff (`grep 'CREATE TABLE'` shows none added). `test_phase6_interactions_recovery.py:139-142` asserts the predicate attempt holds **zero** `worker_claims` rows. `_advance_loop_group_controllers` creates no executor. |
| 5 | Iterations never overlap; source-order admission, sink selection, `maxIterations`, decisions deterministic across restart and competing coordinators | **FAIL** | Non-overlap and determinism *within* a live coordinator hold: `_ready_work_items` emits only the committed iteration's children (`scheduler.py:2050-2079`), `_work_item_state` re-checks generation+iteration and raises `stale loop group work item` (`:1995-2000`), the next iteration is only created inside `_drain_loop_group_transition_locked` (`store.py:11549-11563`), and `primary_terminal_node` is definition-ordered (`topology.py:83-89`). **Determinism across restart fails**: P6-01 (resume/retry can never re-drive the controller) and P6-02 (predicate re-dispatch raises). |
| 6 | Current / `$LOOP_PREV` / outer are distinct immutable scopes on every admitted surface | **PASS** (with a documented narrowing) | `StrictSubstitutionRenderer.resolve_outputs` keys the cache on `(previous, node_id, path)` (`resources.py:1084-1108`), so a current and a previous reference to the same node/path cannot collide. `_references` masks `$LOOP_PREV` spans out of the ordinary grammar before parsing (`resources.py:1021-1046`; mirrored in `bash_rendering.bash_output_references:1513-1571`) and preserves authored order by `start`. Static admission covers prompts, `when`, bash/script, approval text, ordinary-loop prompt and `until_bash`, and gate messages via `_interpolated_node_templates` (`schema.py:2003-2060`) under `_validate_v6_loop_group_references`. Narrowing: the **group's own** `until_bash`/`gate_message` are validated by the top-level `_validate_v3_static_output_references` (`schema.py:1791-1875`), which requires every reference to be in the group node's `depends_on`; body-output references there are therefore rejected at admission even though `_scoped_variables` would resolve them at runtime (`scheduler.py:2753-2772`). This is stricter than the spec's "until_bash resolves current body outputs", i.e. fail-closed, and does not weaken any admitted surface. |
| 7 | Iteration-one previous-output absence; later iterations resolve only authenticated winning publications from N-1 | **PASS** | `_resolve_loop_child_output(previous=True)` requires `controller["previous_outputs"][node_id]`, then requires **exactly one** run artifact matching group + generation + `iteration - 1` + node + relative path + media type + size + sha256 (`scheduler.py:2109-2151`); anything else raises `output_reference_integrity`. Absent previous output returns `None`, and `previous_output_reference` maps that to `""` only for a whole-output reference, raising for a field path (`resources.py:871-887`). `previous_outputs` is rebuilt from authenticated candidates at iteration commit (`store.py:11798-11812`). |
| 8 | Child executor context keeps exact existing top-level paths while isolating nested attempts, publications, processes, artifacts, resources, evidence | **FAIL** | Top-level byte-compatibility holds (`base.py:124-131` reproduces the previous hardcoded paths; the pre-existing executor suites are green). Isolation fails twice: the `until_bash` predicate attempt directory omits the attempt id (P6-02), and nested publications are created inside the shared top-level accounting root `run/artifacts` (P6-05). Unproven side note: `effective_scoped_node_options` (`resources.py:83-93`) injects **all** group options — including `output_type`, `when`, `trigger_rule`, `context`, `always_run` — into every body child at runtime, while `_normalize_v6` merges only `_PHASE6_GROUP_DEFAULT_FIELDS` (`language.py:108-124`) at sealing time; I confirmed the divergence (`runtime options for group/worker : {'output_type': 'GroupSummary'}` against `sealed body node options: [{}, {}]`) but could not construct a wrong observable result from it with a stub executor. |
| 9 | One generation-fenced winner per transition; crash boundaries cannot duplicate, lose, accept stale authority, or finish early | **FAIL** | The fencing itself is strong: every controller mutation runs under `workflow_lock(run)` plus `_execution_fence_transaction`, compares `expected_state_version`, and re-verifies `loop_group_scope`/generation/iteration (`store.py:11279-11296`, `:11455-11463`, `:12154-12163`, `:12327-12350`); interactions additionally bind attempt id and artifact digest (`_authenticate_loop_group_interaction`, `store.py:18650-18717`). But P6-01, P6-02, and P6-03 are all crash-boundary defects: recovery wedges, predicate re-dispatch crashes, and terminalization proceeds against a live executor. |
| 10 | `artifacts:false` permits unrelated unchanged prior publications but no node-attributable artifact survives a successful attempt; unsafe entries fail closed | **FAIL** | The fail-closed half is well built: `publication_tree_snapshot` (`base.py:147-206`) rejects symlinks, non-regular entries, and `st_nlink != 1`, opens with `O_NOFOLLOW`, and re-stats before/during/after to catch check/use races; the script executor separately rejects symlinks (`script.py:551-557`). The survival half fails: P6-04 shows a node-attributable file surviving a successful second attempt. P6-05 additionally makes the check fail on unrelated concurrent publications. Windows path/process behaviour is **UNPROVEN** (see §9). |
| 11 | Structured AI output under an exact tool-call contract cannot be replaced by unconstrained repair; manifest identities corroborated by immutable tool results; ordinary repair intact | **PASS** | `executors/ai.py:663-664` returns `ineligible_tool_call_contract` before any repair path, and the sealed-fallback route is disabled when a contract is present (`plugin_agent_worker.py:2826`). `_ToolCallAudit.finalize` (`plugin_agent_worker.py:1350-1400`) requires exactly one non-parallel tool call whose name **and** arguments equal the contract, exactly one recorded result, no invalid projections, and that the model's JSON output equals the projection derived from the raw tool result. The audit consumes `display_function_result`, which is the **full** result before offloading (`tool_executor.py:1828`), and `redact_tool_args_for_display` only rewrites `browser_type.text` (`display.py:400-414`), so the argument comparison is exact. `_correlate_tool_call_contract_result` (`plugin_agent.py:1049-1067`) re-checks the evidence in the parent process. Ordinary non-contract repair is untouched. `test_ai_executor.py`, `test_plugin_agent_tool_audit.py`, and the Jira suite are green. |
| 12 | Every outward Jira/GitLab write consumes exact current approval and effect authority once; fallback/repair/retry/restart/inline/sibling cannot replay or widen it | **UNPROVEN** | In-tree I verified: the sidecar names the four write nodes with their scoped semantic ids (`process-ticket-manifest/create-branch`, …) and `outward_action_policy: approval_required`; the scheduler resolves `outward_action` from `work_item.semantic_id` so body children are covered (`scheduler.py:4809-4811`); `consume_action_grant` pops the grant durably before spawn and is nested-aware via `_claim_node_state` (`store.py:19730-19757`, `:331-345`); `classify_failure` forces every outward failure to `UNKNOWN_OUTCOME` with no retry (`scheduler.py:377-378`); each write node carries `retry: {max_attempts: 1}` and a `when` gate on the corresponding approval's success. What I could **not** verify is the second half of the claim — the Ericsson connector host's per-action approval and its consumption semantics live in vendored connector plugins outside this diff, and exercising them requires live Jira/GitLab, which is prohibited. I therefore cannot certify "consumed exactly once" end to end. |
| 13 | Expected Jira outcomes remain structured success; unknown/ambiguous writes stop for reconciliation and are never blindly replayed or converted | **PASS** | The seven expected outcomes are a closed `enum` in the sealed `output_format` and flow into the aggregate (`jira-defect-loop.yml`, `terminal_outcome`). Ambiguity is detected from the immutable tool result by `_contains_write_ambiguous` (`plugin_agent_worker.py:1273-1296`), surfaces as `write_ambiguous`/`outcome_unknown`/`reconciliation_required` in the audit, is folded into `_uncertain_effects` (`ai.py:355-358`), and forces a terminal `outcome_unknown` failure for any outward node (`ai.py:2083-2098`). `outcome_unknown` is in `never_retry` (`scheduler.py:5834`) and outward failures are `UNKNOWN_OUTCOME` → paused for reconcile. |
| 14 | Jira reducers consume bounded authenticated predecessor publications, enforce schemas, retain real evidence, publish required aggregates deterministically | **PASS** | `publish-ticket-record` reads `HERMES_WORKFLOW_PREDECESSORS_FILE` (populated from authenticated predecessor state, `scheduler.py:5153-5180`, gated on `normalizer_version >= 6` and `script`), and throws on any missing/uncorroborated write result, identity mismatch, or MR evidence mismatch. `record-cumulative-state` enforces manifest order, count reconciliation, and duplicate-key rejection, and only then emits the exact `<promise>BATCH_COMPLETE</promise>` marker. `publish-aggregate-json` re-verifies the final set and order against the immutable manifest. All records/aggregates are schema-bounded (`maxItems: 25/50`, `additionalProperties: false`). 65 Jira tests green. |
| 15 | Public projections are bounded parent-only summaries with no private data; hidden children cannot corrupt counts or leak through errors | **PASS** (with one gap) | `_public_loop_group_projection` (`sanitize.py:405-472`) emits only ids, node types, states, attempt counts, durations, and categorical failure codes; any malformed child omits the whole summary rather than degrading. The 513th body node still affects `current_iteration_completed` but is not listed (`sanitize.py:452-459`), and the Pydantic models forbid extras with hard bounds (`plugin_api.py:202-245`). Event scopes are re-projected to `{group_id, controller_generation, iteration, body_node_id}` (`sanitize.py:487-509`), and the interaction evidence path feeds `public_event_projection` output into `_interaction_event_item` (`evidence.py:337`), so `run_id`/`worker_node_id` from `durable_record()` never reach the API. A new redactor blocks output/path/message/command/result keys in loop-group event payloads (`sanitize.py:260-290`). Gap (not a finding): nothing ever writes `controller["iterations"]`, so the public `iterations` history is permanently `[]` and the Desktop inspector can never show per-iteration history. |
| 16 | Profile B cannot list, inspect, mutate, acknowledge, cache, or receive late results for Profile A; no cross-profile board or cache key | **PASS** | `tests/plugins/workflow/test_desktop_api.py:3191` (`test_profile_b_cannot_list_detail_event_or_mutate_profile_a_run`) exercises two `RunStore` homes and asserts list/detail/event/mutate isolation; it is green in this run. Desktop query keys retain the profile component and mutations now capture the **origin** profile at dispatch (`index.tsx:238-283`), invalidating with `refetchType: 'none'` when the active profile has changed, so a late Profile A response cannot paint Profile B. No cross-profile aggregate route was added. |
| 17 | Desktop consumes backend truth additively, remains non-authoritative, settles late mutations against their origin profile | **UNPROVEN** | Static reading supports it: the codec is additive and closed (`workflow-public-codec.ts:412-483` — `exact()` forbids unknown keys, all loop-group fields are range-checked, and `iteration <= max_iterations`, `completed_iterations <= iteration` are cross-checked), the inspector only renders decoded fields, and the mutation origin capture is correct. But the launcher's required Desktop commands (`npm test -- --run …`, `npm run typecheck`) could **not** be executed — see §9 — so I have no runtime evidence and will not claim a pass. |
| 18 | Existing core tools, prompt prefix, alternation, executors, routing, scheduler, tables, migrations, API versions unchanged except where the spec extends existing data | **PASS** | `run_agent.py`, `model_tools.py`, `toolsets.py`, and the provider adapters are absent from the 113 changed paths. No `CREATE TABLE`/migration is added; body claims reuse `worker_claims`. The only public-version movement is `normalizer_version` `le=5 → le=6` on two catalog models (`plugin_api.py:780`, `:795`); routes, action names, and payload shapes are additive-only (`loop_group`, `loop_group_scope`). `agent/turn_finalizer.py:164-173` adds a branch gated on `getattr(agent, "strict_iteration_limit", False) is True`, which only `plugin_agent_worker.py:2216` sets and only when the workflow node declares `maxTurns` under v6, so non-workflow callers are unaffected (`test_turn_finalizer_iteration_limit_exit.py` green). |
| 19 | Only Jira Defect Loop is migrated; the other seven legacy flows remain deferrals and gain no v6 syntax | **PASS** | `grep -rln 'loop_group' capabilities/` returns exactly `capabilities/workflows/jira-defect-loop.yml` and `capabilities/workflow-packages/ericsson/workflows/jira-defect-loop.yaml`. `inbox-digest`, `jira-single-ticket-showcase`, `jira-to-gitlab`, `my-tickets-summary`, and `sharepoint-document-intake` are untouched by the diff except for the manifest/digest entries that add the new pair. |
| 20 | Generated schema, installed distribution, website, builder references, customization ownership, and runtime agree on v6 fields, bounds, codes, current version, and v1-v5 compatibility | **PASS** | `website/docs/user-guide/features/workflow-yaml-reference.md:87,99` and `skills/software-development/workflow-builder/references/portable-schema.md:49,61` both state current `archon-2026-07 → 6` and `supported_normalizer_versions: [1,2,3,4,5,6]`, matching `language.py:36-42`. The five new stable codes (`loop_group_version_unsupported`, `loop_group_shape_invalid`, `loop_group_topology_invalid`, `loop_group_scope_invalid`, `loop_group_product_limit`) are registered in the inventory (`language_schema.py:970-999`) and used verbatim by `schema.py`. `node --test scripts/__tests__/vendor-ericsson.test.mjs` (48 pass) asserts the checked-in `capabilities/workflows/jira-defect-loop.{yml,hermes.yaml}` are **byte-identical** to the distributed `capabilities/workflow-packages/ericsson/workflows/` pair and that both are listed in `ericsson.json` and `ericsson-vendored-paths.json`. `test_installed_distribution_e2e.py`, `test_capability_staging.py`, and `test_ericsson_connector_distribution.py` are green. |

---

## 6. Top adversarial reproductions and wrong observable results

All probes ran with `HERMES_HOME` redirected to `mkdtemp`, synthetic workflows in
`mkdtemp`, no network, and no writes inside the review checkout.

1. **Operator resume wedges a failed loop-group run** (`probe_resume.py`) —
   `resume` (and `retry`) turns a legible `failed` run into `running` forever
   with `last_error: None`, `next_actions: ['status','events','cancel']`, zero
   executor dispatches across three passes, and a second admission of the same
   workflow downgraded to `queued`. (P6-01)
2. **Crash-interrupt then resume wedges identically** (`probe_resume2.py`) — a
   body child interrupted by lease expiry stays `interrupted` after `resume`
   while the group flips to `ready` and the controller stays `running`; nothing
   is ever claimable again. (P6-01)
3. **`until_bash` predicate re-dispatch crashes the scheduler permanently**
   (`probe_predicate.py`) — four consecutive `advance_all()` calls each raise
   `FileExistsError: …/iterations/0001/decision/attempt`; run status remains
   `running`, and the 8-recovery bound is never consumed. (P6-02)
4. **`resume` and `abandon` ignore a live body executor** (`probe_guards.py`) —
   with `recovery.observation == "still_running"`,
   `termination_confirmed: False`, and one retained `worker_claims` row, `resume`
   returns `running` instead of refusing, and `abandon` returns `abandoned` and
   drops the claim row to 0. (P6-03)
5. **`artifacts: false` residue survives** (`probe_residue.py`) —
   `attempt-1: failed/artifact_limit, leak_present=True`;
   `attempt-2: succeeded, leak_present=True`. (P6-04)
6. **`artifacts: false` fails on an unrelated concurrent publication**
   (`probe_artifacts.py`) — creating only the loop-group body-child publication
   *directory* during the node's run flips it from `succeeded` to
   `failed/artifact_limit`; the control run with the same pre-existing
   `prior.txt` and no concurrency succeeds. (P6-05)

---

## 7. Test-integrity assessment

**Overall.** The Phase 6 suites are unusually strong on language, admission,
bounds, projection sanitization, and store CAS/fencing. They are weakest exactly
where the findings are: **operator lifecycle actions** and **real executor
filesystem composition**.

Mutation reasoning on load-bearing tests:

- **Would removing the guard fail a test?** For the admission bounds — yes.
  Deleting `_LOOP_GROUP_WORK_LIMIT` enforcement, the 512-node body cap, the
  `retry`-on-group rejection, or the include/workflow/nested-group rejection each
  fails a dedicated case in `test_phase6_admission.py`. Same for the projection
  redaction in `test_phase6_public_projection.py` (which explicitly tests the
  513th hidden node, malformed body entries, and sub-millisecond timestamp
  reversal — genuinely adversarial).
- **Would removing the guard fail a test? — no, in three places.**
  1. `resume_run`/`retry_run` nested-state handling: it does not exist, and no
     test would notice if it did and were removed (P6-01).
  2. `_set_terminal` / `resume_run` still-running guards: replacing
     `projection["nodes"].values()` with an empty iterator changes no Phase 6
     test outcome (P6-03).
  3. The `artifacts: false` before/after comparison: deleting the
     `artifact_free_after` block entirely still leaves attempt 2 in P6-04 green,
     because no test executes the node twice (P6-04).

**Mocks hiding composition.** The most consequential is the `until_bash`
predicate: every predicate test substitutes `scheduler.executors["bash"]` with a
stub (`test_phase6_interactions_recovery.py:148`, `:211`, `:697`). Those stubs do
not create the attempt directory, so the entire filesystem contract of the
predicate path — the only executor call site in the codebase whose attempt
directory omits the attempt id — is untested. That single substitution is why
P6-02 shipped. A secondary case: `OutputExecutor`/`SucceedingExecutor` in
`test_phase6_scheduler.py` do call `context.effective_attempt_directory` and
`mkdir(exist_ok=False)`, which is good discipline, but they never re-execute the
same scope twice.

**Positives worth recording.** `test_phase6_store.py` builds a genuine live-child
state with a patched `ProcessIdentity.is_current` and asserts claim retention;
`test_phase6_interactions_recovery.py` verifies the predicate consumes zero
worker claims and that an obligation reserve exists *before* dispatch;
`test_phase6_public_projection.py` uses parametrized malformed mutations rather
than snapshot equality; `scripts/__tests__/vendor-ericsson.test.mjs` asserts
byte-identity of the distributed workflow pair rather than a digest literal.
None of the Phase 6 tests I read are change-detector tests.

**Candidate defects vs. baseline.** The one reproduced baseline failure,
`tests/agent/test_prompt_cache_ttl_propagation.py::TestFailoverRestartsPreflight::test_every_fallback_activation_restarts_preflight`,
is unrelated: `git diff 1001a67055..d850707a25 -- tests/agent/test_prompt_cache_ttl_propagation.py agent/conversation_loop.py` is **empty**, so both files are byte-identical
to the merge base. I attribute nothing to Phase 6 on that basis. All five
findings above are demonstrated on changed Phase 6 code paths with a probe.

---

## 8. Verification ledger

Python, via `scripts/run_tests.sh` with
`HERMES_PYTHON=<main checkout>/.venv/bin/python` (see §1):

| # | Command | Exit | Result |
| --- | --- | --- | --- |
| 1 | `scripts/run_tests.sh tests/plugins/workflow/test_phase6_{language,admission,execution_context,store,scheduler,interactions_recovery,public_projection,jira_defect_loop}.py -q` | 0 | 8 files, **230 passed, 0 failed**, 101.6s |
| 2 | `scripts/run_tests.sh tests/plugins/workflow/test_phase3_{language,execution_semantics}.py test_phase4_{language,snapshot,loops,loop_interactions}.py test_phase5_{language,provider_snapshot,execution_authority_continuity}.py test_parallel_scheduler.py test_crash_recovery.py test_fault_injection.py test_cancel_node.py -q` | 0 | 13 files, **408 passed, 0 failed**, 18.0s |
| 3 | `scripts/run_tests.sh tests/plugins/workflow/test_ai_executor.py test_script_executor.py test_loop_executor.py test_phase6_execution_context.py test_phase6_jira_defect_loop.py -q` | 0 | 5 files, **267 passed, 0 failed**, 10.9s |
| 4 | `scripts/run_tests.sh tests/plugins/workflow/{test_store,test_desktop_api,test_evidence_api,test_language_schema,test_catalog_api,test_installed_distribution_e2e,test_workflow_language_desktop_e2e,test_portable_compatibility_e2e,test_idempotency_multiprocess,test_performance_bounds,test_phase5_public_projection_contract,test_phase5_admission_parity}.py tests/hermes_cli/{test_ericsson_connector_distribution,test_capability_staging}.py tests/agent/test_plugin_agent_tool_audit.py -q` | 0 | 15 files, **1099 passed, 0 failed, 1 skipped**, 78.0s |
| 5 | `scripts/run_tests.sh tests/agent/test_prompt_cache_ttl_propagation.py -q` | 1 | **1 failed, 10 passed** — the known baseline failure; both implicated files byte-identical to merge base |
| 6 | `node --test scripts/__tests__/vendor-ericsson.test.mjs` | 0 | **48 pass, 0 fail**, 2.2s |

No retries were triggered (no `⚠ FLAKY` section in any run). One skip in batch 4
(not attributed; the runner reports it at file granularity and no failure
accompanied it). No warnings section was emitted by the runner.

**Not run (see §9):** `cd apps/desktop && npm test -- --run …` and
`npm run typecheck`.

Disposable probes (all outside the review checkout, synthetic data, isolated
temp paths, no network, no credentials, no live services):
`/tmp/p6probe/probe_predicate.py`, `probe_resume.py`, `probe_resume2.py`,
`probe_guards.py`, `probe_artifacts.py`, `probe_residue.py`, `probe_optdiv.py`.

---

## 9. Unverified platforms and dependencies

1. **Desktop vitest and typecheck — NOT RUN.** `apps/desktop/node_modules` and
   the repo-root `node_modules` do not exist in this review worktree (they exist
   only in the main checkout). Running the required
   `npm test -- --run src/lib/workflow-public-codec.test.ts
   src/app/workflows/adapter.test.ts src/app/workflows/workflow-run-drawer.test.tsx
   src/app/workflows/index.test.tsx` and `npm run typecheck` would require an
   `npm install` into the review checkout, which the launcher's write constraint
   forbids (the report path is the sole authorized write). I did not substitute a
   narrower command and claim equivalence. Consequently invariant 17 is marked
   **UNPROVEN**, and the TypeScript changes were reviewed statically only
   (codec bounds, `exact()` semantics, adapter/board badge derivation, inspector
   rendering, and the mutation origin-profile capture). Node 26.7.0 / npm 11.19.0
   are present, so these commands should run cleanly in a checkout with
   dependencies installed.
2. **Native Windows paths and process handling — UNPROVEN.** All probes and test
   runs were on macOS (Darwin 25.5.0, POSIX). `publication_tree_snapshot` relies
   on `O_NOFOLLOW`, `st_nlink`, `st_ino`, and `st_ctime_ns`, and
   `BashExecutor` has an explicit `if os.name == "nt"` branch
   (`executors/bash.py:139`). The no-follow traversal and exact authenticated
   roots are verified on POSIX only; I make no claim about native Windows.
3. **Jira/GitLab connector approval semantics — UNPROVEN.** The connector host's
   current-action approval lives in vendored Ericsson connector plugins that are
   not part of this diff, and exercising it needs live services, which is
   prohibited. Only the in-tree half of invariant 12 is verified. No connector,
   Jira, or GitLab call was made; only synthetic fixtures were used.
4. **Multi-process / multi-coordinator races — partially verified.**
   `test_idempotency_multiprocess.py` passed, and I reasoned over the
   fence/CAS code paths, but I did not build a two-process barrier harness for
   the loop-group controller specifically. The interleavings I report (P6-02's
   predicate re-dispatch, P6-03's live-child terminalization) were reproduced
   single-process by driving the exact durable state transitions the multi-process
   case would produce.
5. **`effective_scoped_node_options` divergence — UNPROVEN.** Recorded under
   invariant 8; the option divergence is real and demonstrated, but I could not
   construct a concrete wrong observable result, so it is not filed as a finding.

---

## 10. Final worktree status

```
$ git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.worktrees/workflow-loop-groups-phase-6-review-claude status --short --branch
## HEAD (no branch)

$ git rev-parse HEAD
d850707a25d0eb161d3bedd2db935d01f3573255
```

The detached review checkout is unchanged and clean: no tracked file was
modified, no commit, branch, ref, stash, or worktree was created or mutated, and
no untracked file was added. (`test_durations.json`, written by
`scripts/run_tests.sh`, is listed in `.gitignore:40` and therefore does not
appear in `git status`; it is the runner's own cache, not a review artifact.) The
only authorized persistent write is this report, which lives outside the review
checkout at
`docs/reviews/2026-08-30-workflow-language-phase-6-adversarial-review-claude.md`
in the feature worktree.
