# Adversarial Code Review — Portable Workflow Orchestration (S01–S14)

**Reviewer role:** hostile principal-level reviewer (Python, TypeScript/React, subprocess
lifecycles, durable workflow engines, security boundaries, release engineering).
**Date:** 2026-07-17
**Review target:** OTTO / LOOP24 v2.0.0 "Portable Workflow Orchestration" delivery.
**Verdict:** **CONDITIONAL** — do not ship the Desktop operations slice or advertise
full Archon provider/model + bash-variable compatibility until the findings below are
resolved; the durable-runtime core (S01–S08) is sound and may ship once the
conditional items are closed or explicitly de-scoped.

---

## 1. Scope and immutable refs actually reviewed

| Meaning | Commit | Verified |
|---|---|---|
| Design/plan baseline (implementation starts after) | `46fa66af60073dfc71ea2223668a4512d4ea1b32` | `git cat-file -e` OK |
| Released, tested neutral base | `365e1605ba4864c35f64a9be8e77d97b09845e5f` | OK; ancestor of both brands |
| Released OTTO v2.0.0 source | `15165df332ebe60fea3e0d21b13765421f9a2735` | OK; descends from base |
| Released LOOP24 v2.0.0 source | `bfc378da533e9558c28d221f8cb030adef6c0f37` | OK; descends from base |

- Primary range `46fa66af..365e1605` confirmed: **222 changed files, 27,002
  insertions, 1,713 deletions** (matches the prompt). `git diff --check` on the range
  reports only "new blank line at EOF" whitespace notices in vendored Ericsson/showcase
  fixtures — cosmetic, no conflict markers.
- Range contains the expected S01–S14 commits plus the release-metadata commits
  (`d654f6a74`, `27c9b59a0`, `644d1f368`, `365e1605b`) and hardening follow-ups
  (`97296af0d`, `e2a31d3e7`, `32e9eed89`, `6364000c3`, `fcd912f9e`, `3aaa3bcf9`,
  `9efca7e2b`). All confirmed by `git log --reverse --oneline`.
- Brand overlays (`365e1605..15165df3`, `365e1605..bfc378da5`) touch only generated
  brand identity, installers, package-lock, and per-brand art. **Every generic
  workflow/runtime file I sampled is byte-identical across base/otto/loop24**
  (`agent/plugin_agent.py`, `agent/plugin_agent_worker.py`, `tools/managed_process.py`,
  `tools/registry.py`, `tools/process_registry.py`, `plugins/workflow/{scheduler,store,
  admission,executors/ai}.py` all hash-equal on the three commits). No generic-runtime
  divergence between brands — the load-bearing merge invariant holds.
- I reviewed from the shared checkout (currently `otto`, tree clean apart from
  untracked `dist/`, `docs/reviews/`, and one unrelated `docs/2026-07-16-*.md`), plus
  three **detached** worktrees at the exact base/otto/loop24 commits for gate execution.
  No refs were advanced, nothing was reset/cleaned/stashed. The only repository write is
  this document.

---

## 2. Release verdict

**CONDITIONAL.**

The durable core — the isolated worker/process boundary, `RunStore` compare-and-set
state machine, admission idempotency, digest-bound trust, cancellation race contract,
and topology safety — is genuinely well-engineered and stands up to adversarial reading
and the focused gates (brand gates pass; the base gate's only failures are load-induced
flakes, see F5). It is not the reason to withhold a ship.

The **Desktop workflow/Kanban operations slice (S10) and its S14 release gates are not
substantiated**: acceptance behaviors the plan's Definition of Done lists as release
requirements (keyboard/focus/reduced-motion/screen-reader a11y across four locales,
virtualization at scale, laptop-width layout, cursor-gap recovery, **stale-write / 409
rollback, stale-disable**, notification dedup, local/remote/OAuth auth from the renderer)
are neither implemented in two places I checked (F1) nor covered by the shipped tests,
whose "e2e" and "performance" files are pure adapter unit tests and whose page test
asserts only that an export is a function. Two documented **Archon compatibility
promises contradict runtime behavior** (per-node provider/model selection — F2; bash-node
variable substitution — F3). One showcase report field is fabricated rather than
evidence-derived (F4), and the base merge gate is non-deterministic under load (F5).

None of the findings are CRITICAL: I found no credential disclosure, no arbitrary
unauthorized execution, no durable-state corruption, no cross-profile authority breach,
and no cleanup path capable of orphaning a fleet.

---

## 3. Findings (sorted by severity)

| ID | Sev | Slice(s) | File : line | Violated invariant | Failure scenario | Evidence | Minimal safe fix | Missing regression test |
|---|---|---|---|---|---|---|---|---|
| **F1** | HIGH | S10, S14 | `apps/desktop/src/app/workflows/index.tsx:60-70`, `apps/desktop/src/app/workflows/run-inspector.tsx:45-53`; test files listed below | Plan DoD (l.1755-1757) + design §"desktop board and inspector": mutations disable on stale/disconnected and roll back/refetch on `409`; a11y/virtualization/cursor/auth behaviors pass tests | A stale run inspector issues an action with a now-stale `expected_version`; the API returns 409; `useMutation` has **no `onError`** and the `selected` query has **no `refetchInterval`**, so the inspector keeps the stale `state_version` and every subsequent click re-sends it → repeated 409, wedged until an incidental window-refocus refetch. Buttons never disable, so a double-click fires two mutations. Independently, none of the plan-required renderer behaviors are exercised by any test. | `index.tsx` mutation object has only `onSuccess`; `run-inspector.tsx` buttons have no `disabled`. Test inventory: `activity-board.test.tsx`=2 trivial tests, `activity-board.performance.test.tsx`=1 (calls the pure `workflowBoardModel` adapter, not the virtualized column), `workflow-operations.e2e.test.tsx`/`kanban-operations.e2e.test.tsx`=1 adapter-only test each, `workflows/index.test.tsx`=`expect(typeof WorkflowsView).toBe('function')`. `grep` across `apps/desktop/src` finds no test asserting reduced-motion, 320/768/1440 layout, focus-on-reorder, keyboard traversal, cursor_reset recovery, or 409 rollback for these pages. | Add `onError` to the run mutation that refetches the selected run on 409 and disables mutating buttons while `mutation.isPending` or `model.stale`; give the `selected` query a bounded `refetchInterval`. Then add the DoD-required renderer tests (real render, jsdom): keyboard/focus/virtualization/laptop-width/reduced-motion/409-rollback/cursor-gap. | `apps/desktop/src/app/workflows/*.test.tsx` and `activity-board.test.tsx` currently prove none of these; add rendering+interaction tests, not adapter unit tests. |
| **F2** | MEDIUM | S04, S08 | `plugins/workflow/executors/ai.py:286-289` + gate at `agent/plugin_agent.py:457-460` (`_agent_override_allowed`) | Compat table: `provider`,`model` are "Mapped when the selected provider advertises the capability"; doctor reports such a node `runnable=True` | A portable package pins `provider:`/`model:` on an AI node (extremely common in real Archon packages). `doctor` reports it runnable; at run time `PluginAgentRunner("workflow").run()` calls `_agent_override_allowed("workflow","provider",...)`, which requires `plugins.entries.workflow.agent.allow_provider_override: true`. That key is **never seeded** in `DEFAULT_CONFIG` or `plugins/workflow/plugin.yaml`, so the call returns `False` → `PermissionError` → node fails `authorization`. Compat promise and runtime disagree; the required config is undocumented. | Reproduced: `_agent_override_allowed("workflow","provider","custom")` → `False`; `"model"` → `False`; `None` → `True` (default provider still works). `test_provider_compat.py` only exercises `assess_compatibility`, never the runtime gate; no shipped workflow/showcase declares a provider so no gate path is covered. | Either seed the workflow plugin's `agent.allow_provider_override`/`allow_model_override` to the values the compat report certifies, or have `assess_compatibility`/`doctor` mark a provider/model-pinned node blocking unless the override is configured, so the report cannot promise a run that will fail. | An e2e test that runs an AI node with an explicit `provider`/`model` through the **real** `PluginAgentRunner` (not a fake) and asserts it executes when compat says runnable. |
| **F3** | MEDIUM | S06 | `plugins/workflow/executors/bash.py:44,47` (uses `str(context.node.value)` raw); `plugins/workflow/resources.py:265` (`render_bash` exists) | Compat table + Plan Task 4 Step 1: shell-node substitutions receive Archon-compatible variable substitution with safe quoting/spill | A portable `bash` node with `echo "$ARGUMENTS"` or `cat $collect.output` runs `/bin/sh -c` on the **raw** template; `$ARGUMENTS`, `$node.output`, `$1`, `$USER_MESSAGE`, `$WORKFLOW_ID` are undefined shell variables → expand to empty. Only `ARTIFACTS_DIR`, `HERMES_WORKFLOW_RUN_DIR/RUN_ID` happen to resolve (they are injected into the env). Result: silently wrong (empty) output, no diagnostic. | `render_bash` (the safe-quote+spill routine) is called only by `loop.py:201` for `until_bash` and by `test_resources.py`; the `BashExecutor` never calls it and never consumes `context.variable_context`. `test_bash_e2e.py` uses static commands and does not reference any workflow variable, so the gap is untested. | Render bash-node values through `VariableContext.render_bash` (safe-quoted, spill-on-oversize) in `BashExecutor.execute` before spawning, exactly as `until_bash` already does. | A bash-node e2e that references `$ARGUMENTS` and a predecessor `$node.output` and asserts the substituted value appears in stdout, plus an injection canary asserting no shell breakout. |
| **F4** | MEDIUM | S13 | `plugins/workflow/showcase.py:491` | Design §showcase + Plan Task 13 Step 4: every report field derives from durable RunStore/cleanup evidence; "reports cannot be forged from catalog claims" | `build_showcase_report` returns `cleanup={"owned_processes_live": 0, "staging_present": False}` as a **hardcoded literal**. A resilience run that emitted a `cleanup_failed` event (an uninterruptible child, WinError-5 pack lock, etc.) still reports zero live processes and no staging — the exact "declared success" the harness is meant to prevent. The per-capability `process-cleanup` claim *is* evidence-derived (`reaped >= started`), but the top-level cleanup summary that operators read is fabricated. | Line 491 is a constant; `cancel_run`/`block_cleanup_failed` emit `cleanup_failed` events that this summary ignores. | Derive `owned_processes_live` from the count of `process_started` minus `process_reaped` events (or presence of `cleanup_failed`), and `staging_present` from an actual staging-dir probe. | A showcase-report test that injects a `cleanup_failed` event and asserts the report does not claim `owned_processes_live: 0`. |
| **F5** | MEDIUM | S01, S14 | `tests/agent/test_plugin_agent.py:605-626` and `:586-601` (run by `scripts/test_workflow_merge_gate.sh --phase base`) | Plan §"A task is not complete until focused tests pass"; gate must be deterministic | `test_worker_stderr_does_not_reset_semantic_idle_deadline` asserts `time.monotonic() - started < 1` with `idle_timeout_seconds=0.2`; `test_worker_stderr_is_never_exposed_to_plugin` races worker exit against a 2 s idle timeout. Under CPU contention both fail as false negatives, so the **base merge gate is non-deterministic** — it can block a valid `base` promotion or train operators to "just rerun." | Observed: base gate reported `2 failed, 591 passed` with `assert (…- …) < 1 == 3.07 > 1` while four heavy jobs ran concurrently; re-running the two tests in isolation 3× → `2 passed` every time (0.74 s / 0.73 s / 1.12 s). | Replace wall-clock tolerance assertions with deterministic checks (e.g. assert the raised type/idle-vs-wall classification and that elapsed ≤ a generous multiple of `idle_timeout`, or use an injected clock), so the invariant holds without depending on host load. | The tests already exist; convert them to clock-injected/relative assertions so the gate is load-independent. |
| **F6** | LOW | S14 | `scripts/check_upstream_customizations.py:196-217` | Design AC #13 / §"checker covers every upstream-owned feature file" | `ignored_prefixes` includes `plugins/` and `scripts/`. Any *existing upstream-owned* file that lives under those prefixes (today: the ledgered `plugins/kanban/dashboard/plugin_api.py`) is exempt from the missing-coverage completeness check — coverage relies solely on it being listed by hand. A future upstream-owned change under `plugins/` would not be flagged as missing. | Harmless now (the one such file is ledgered), but the completeness guarantee is weaker than advertised. | Narrow the ignore to the additive new dirs actually introduced (`plugins/workflow/`, generated dashboards), or explicitly whitelist known upstream-owned `plugins/*` files as required-covered. | A checker test where an existing `plugins/kanban/...` file changes without a ledger entry and the checker fails. |
| **F7** | LOW | S02/S06 (design residual) | `plugins/workflow/cli.py:1199-1226` (`_cmd_run`) | Design §trust: `execution_environment` gates local vs isolated execution | `_cmd_run` enforces trust (untrusted → refused, verified below) but never calls `preflight_execution`, so a **trusted** package whose sidecar declares `execution_environment: isolated_backend_required` still runs on the local scheduler. The security-critical direction (untrusted must not run locally) is enforced; this only weakens a trusted package's *voluntary* isolation request. | `_cmd_run` calls `WorkflowTrustStore.check` then `start_run`/`advance`; `preflight_execution` is unreferenced on the run path. | Call `preflight_execution(summary, trusted=…, backend_capabilities=…)` in `_cmd_run` and refuse local execution when the resolved requirement is `isolated_backend_required`. | A run-path test that a trusted `isolated_backend_required` sidecar refuses local execution without an advertised backend. |
| **F8** | LOW | S09/S14 (harness) | `apps/desktop/package.json` scripts | Prompt/AGENTS.md reference `cd apps/desktop && npm test` | The desktop package has **no `test` script**; tests are split across `test:ui` (vitest jsdom) and `test:desktop:platforms` (`node --test` for `electron/*.test.ts`). A naive `vitest run --environment jsdom` over the whole tree fails 53 files (electron tests need node env; a `requestAnimationFrame` uncaught error poisons jsdom). "Run the desktop tests" is therefore ambiguous and easy to run wrong. | `npm test` errors "Missing script: test"; global `vitest run --environment jsdom` → 53 failed files / 24 failed tests, all environment artifacts; the CI-correct per-file invocation of the 11 workflow files → 11 passed. | Add a top-level `test` script that runs both `test:ui` and the node `--test` platform suite, or document the exact CI invocation in the runbook. | N/A (harness/doc). |

---

## 4. S01–S14 coverage matrix

Legend: **proven** = production path traced and behavior established from code + a test
that exercises the real path; **partial** = implemented and mostly tested but with a gap
or an unproven sub-claim; **contradicted** = a documented contract disagrees with runtime.

| Slice | Status | Production evidence | Test evidence | Gap |
|---|---|---|---|---|
| **S01** Plugin agent runner + managed process tree + ledger | **proven** | `agent/plugin_agent.py`, `agent/plugin_agent_worker.py`, `tools/managed_process.py`; bounded IPC framing, mandatory deadlines (all validated finite/positive, idle≤wall), spawn-not-fork, registry `scoped_names`, coordinator lifeline (stdin EOF → cancel), reap in `finally` | `tests/agent/test_plugin_agent.py`, `tests/tools/test_managed_process.py`, `tests/tools/test_registry.py` exercise real subprocesses, disjoint scopes, EOF, escalation | Two invariant tests are load-flaky (F5) |
| **S02** Discovery/validation/topology/trust/CLI | **proven** | `schema.py`, `discovery.py`, `topology.py` (injection-safe, §6), `trust.py` (content digest incl. commands/scripts/mcp+referenced files, symlink-reject, 0600 atomic store, fails closed), `compat.py` | `test_schema`, `test_discovery`, `test_topology`, `test_trust_policy`, `test_compat_matrix`, `test_catalog_cli` | `execution_environment` sidecar not enforced on run path (F7) |
| **S03** Admission/RunStore/bash DAG | **proven** | `admission.py`, `store.py` `start_run` (SQLite `BEGIN IMMEDIATE` + cross-process `workflow_lock`; idempotency, rate/nonterminal/queued/executing/storage caps, reserve→publish→mark), atomic journal+projection with fsync | `test_admission`, `test_store`, `test_scheduler`, `test_bash_e2e`, `test_run_queries`, `test_retention` | — |
| **S04** Command/prompt AI nodes | **partial / contradicted** | `executors/ai.py` fresh/shared fingerprint gate, structured-output validation, `output` stripped from durable state (`store.py:1802`), artifact-only persistence | `test_ai_executor`, `test_ai_e2e`, `test_persisted_sessions` (fake provider) | Provider/model override blocked at runtime vs compat "Mapped" (F2) |
| **S05** Parallel/retry/crash recovery | **proven** | `scheduler.py` bounded pool ≤ `max_parallel_nodes` and global worker cap at `claim_node`; monotonic deadlines; combined retry budget; `_rebuild_projection` journal replay; suspend/wake reconciliation | `test_parallel_scheduler`, `test_retry`, `test_crash_recovery`, `test_deadlines`, `test_shutdown_recovery`, `test_provider_failures` | — |
| **S06** Script/loop/cancel | **partial** | `executors/{script,loop,cancel}.py`; argv-vector (`uv run`, Bun) not shell; loop `max_iterations` mandatory; `until_bash` uses `render_bash` safe-quote | `test_script_executor`, `test_loop_executor`, `test_cancel_node` | `bash` node variable substitution not wired (F3) |
| **S07** Durable approval/rework | **proven** | `store.py` `approve_run`/`reject_run`/`_decide_run` CAS on gate node + pause generation + undecided status; one-shot exact-digest grant (`consume_action_grant`, worker `approval` callback); sudo/secret never persisted | `test_approval`, `test_approval_races` | — |
| **S08** Per-node tools/skills/hooks/MCP/provider | **partial / contradicted** | worker scopes registry, denies `delegate_task`+`workflow_agent`(unless declared), verifies agent-owned schema ⊆ visible, MCP torn down in `finally`, secrets interpolated in-worker only | `test_node_tool_policy`, `test_node_skills`, `test_node_hooks`, `test_node_mcp`, `test_node_agents`, `test_provider_compat` | Provider gate (F2); hook translation is broad but tests assert mapping not counter-adversarial escape |
| **S09** Chat/gateway/desktop-chat/cron activation | **proven** | `skills/productivity/workflow/SKILL.md`, `cli.py` JSON contracts (no prompt/secret/arg leakage), cron `schedule_id + UTC-fire` idempotency key | `test_workflow_skill_command`, `test_workflow_cron`, `test_workflow_skill_dispatch` (gateway+tui), `test_operator_scope` | — |
| **S10** Native Desktop workflow/Kanban | **partial** | `plugins/workflow/dashboard/plugin_api.py` (cursor/409/410, profile scope), `hermes_cli/kanban_db.py` CAS preconditions + two-phase reclaim, `apps/desktop/src/app/{workflows,kanban}`, `activity-board` (real virtualization >50 cards) | Python `test_desktop_api`, `test_kanban_mutation_preconditions` are real; **renderer tests are adapter-only / tautological** (F1) | Renderer a11y/virtualization/409-rollback/cursor/auth unproven; 409-rollback+stale-disable missing (F1) |
| **S11** Builder skill + doctor | **proven** | `skills/software-development/workflow-builder/`, `compat.py`/`cli.py` doctor emits risk+trust+input+overlap without model/network | `test_workflow_builder_skill`, `test_doctor` | Doctor inherits the F2 compat/runtime mismatch |
| **S12** Ericsson conversion + staging | **proven** | `capabilities/workflow-packages/ericsson/**` Archon-shaped, `hermes_cli/capability_staging.py` atomic complete-package staging + distribution digest trust; old `kind:` schema removed | `test_capability_staging`, `test_baked_seed`, `vendor-ericsson.test.mjs` | — |
| **S13** Offline showcase | **partial** | `showcase.py` rigorous catalog validation (digest, safety-class, network/wall bounds, forbidden-text scan, symlink+traversal reject); timeout stays truthfully `failed`; trust seeded only on digest match | `test_showcase_*` (catalog/evidence/offline/resilience/ai/schedule/distribution) | Report `cleanup` summary hardcoded (F4) |
| **S14** Production + merge gates | **partial** | `check_upstream_customizations.py`, `test_workflow_merge_gate.sh`, `test_workflow_upstream_merge.sh`, `merge-evidence.schema.json`; brand gates pass, checker exit 0 | `test_check_upstream_customizations`, `test_workflow_merge_gate`, `test_workflow_upstream_merge` | Base gate non-deterministic (F5); checker completeness gap (F6); Desktop gates inherit F1 |

---

## 5. Concrete reproductions (top findings)

### F2 — provider/model override blocked while compat says runnable
```
$ ./venv/bin/python - <<'PY'
from agent.plugin_agent import _agent_override_allowed
print("provider 'custom':", _agent_override_allowed("workflow","provider","custom"))
print("model 'custom-model':", _agent_override_allowed("workflow","model","custom-model"))
print("provider None:", _agent_override_allowed("workflow","provider",None))
PY
provider 'custom': False
model 'custom-model': False
provider None: True
```
`plugins/workflow/executors/ai.py:286` sets `provider=node.options.get("provider")…`; the
runner gate (`agent/plugin_agent.py:457-460`) rejects it because no
`plugins.entries.workflow.agent.allow_provider_override` exists in `DEFAULT_CONFIG` or
`plugins/workflow/plugin.yaml`. `test_provider_compat.py` reports the same node
`runnable=True`. Ordering: doctor(runnable) → user trusts → run → AI node → PermissionError
→ node `failed:authorization`. Wrong result: a compat-certified package fails at the first
provider-pinned node.

### F3 — bash node loses workflow-variable substitution
`plugins/workflow/executors/bash.py:47` → `argv = ["/bin/sh","-c", str(context.node.value)]`.
`render_bash` is referenced only at `loop.py:201`. Grep confirms no caller of `render_bash`
in `bash.py` and no `variable_context` use there. A node `bash: 'echo "arg=$ARGUMENTS out=$collect.output"'`
executes with `$ARGUMENTS`/`$collect.output` as undefined shell vars → `arg= out=`.
`ARTIFACTS_DIR`/`HERMES_WORKFLOW_RUN_DIR` resolve only because `bash.py:54-58` injects them
into the process env. No error is raised.

### F1 — 409 wedge + fabricated test coverage
`apps/desktop/src/app/workflows/index.tsx:60-70`: the run `useMutation` has `onSuccess`
but no `onError`; the `selected` query (`:38-42`) has no `refetchInterval`.
`run-inspector.tsx:45-53`: action buttons render with no `disabled`. Ordering:
select run (state_version N) → server advances to N+1 (poll of `runs` list is 20 s) →
operator clicks Cancel → mutation sends `expected_version:N` → API 409 → nothing refetches
the selected run → next click resends `expected_version:N` → 409 again. Test inventory
(exact counts): `activity-board.test.tsx` 2, `activity-board.performance.test.tsx` 1
(exercises the `workflowBoardModel` pure function, not the virtualized column render),
`workflow-operations.e2e.test.tsx` 1 (adapter only), `workflows/index.test.tsx` 1
(`expect(typeof WorkflowsView).toBe('function')`). Wrong result: the DoD's tested
renderer behaviors do not exist and one behavior (409 recovery) is absent from the code.

### F4 — showcase report claims clean cleanup unconditionally
`plugins/workflow/showcase.py:491` returns literal
`cleanup={"owned_processes_live": 0, "staging_present": False}` for **every** run. A run
whose `cancel_run`/`block_cleanup_failed` path emitted `cleanup_failed`
(`store.py:1964`, `store.py:2582-2603`) still reports zero live owned processes. Wrong
result: the operator-facing evidence report asserts a cleanup fact it never measured.

### F5 — non-deterministic base merge gate
`scripts/test_workflow_merge_gate.sh --phase base` under concurrent load →
`FAILED tests/agent/test_plugin_agent.py::test_worker_stderr_is_never_exposed_to_plugin`,
`FAILED …::test_worker_stderr_does_not_reset_semantic_idle_deadline`,
`assert (4054773.63 - 4054770.55) < 1` (3.07 s). Isolated re-run ×3 → `2 passed`
(0.74/0.73/1.12 s). The gate's pass/fail depends on host load.

---

## 6. What I verified safe, and how

- **Worker/process boundary (S01).** Traced `PluginAgentRunner.run` →
  `_validate_request` (empty prompt, non-positive/`None`/infinite deadlines, `idle>wall`,
  `provider_request>wall`, bad digests, oversized name lists, >128 hooks, >32 mcp, >16
  inline agents all rejected) → `_exchange_worker`. IPC frames are bounded on **both**
  ends: `_read_stream` caps each `readline(_MAX_FRAME_BYTES+1)`, the event queue is
  `maxsize=8`, oversized stdout frames raise, stderr is tail-bounded and **never**
  surfaced through the plugin-facing exception (a spawned worker that writes a secret to
  stderr and exits raises a generic `RuntimeError` with the secret absent — test present
  and passing in isolation). Idle timer resets only on `progress`/`interaction` frames
  (not stderr, not heartbeat); wall/idle use `time.monotonic`. Every path terminates in
  `finally: tree.close()` (terminate→TERM→KILL→wait/reap). Deadlines are mandatory before
  spawn; no `None`/0 becomes infinite. Dangerous-tool/clarify/sudo/secret callbacks
  fail closed (`approval` returns `"deny"` unless the exact one-shot digest matches; sudo
  returns `""`; secret returns `validated:False`) and pause+terminate the worker.
- **Scoped AI execution (S08).** The worker enters `registry.scoped_names(allowed,denied)`
  before constructing `AIAgent`, force-denies `delegate_task` (+`workflow_agent` unless
  declared), and after construction prunes `agent.tools`/`valid_tool_names` to the visible
  set, raising if the agent kept a non-registry schema (`plugin_agent_worker.py:595-605`).
  MCP is resolved in-worker post-IPC and torn down in `finally`; per-node MCP config is
  swapped via a monkeypatched loader that is restored in `finally`. Parallel nodes run in
  **separate processes**, so process-global tool state cannot leak — the design's core
  rationale, and it holds.
- **Durable state & races (S03/S05/S07).** `RunStore.start_run` is the sole creation path
  (`RunAdmissionController` just delegates), guarded by `_admission_gate` +
  `workflow_lock(admission_lock)` + SQLite `BEGIN IMMEDIATE`; duplicate key+digest →
  `existing`, key+different digest → `idempotency_conflict`, capacity → typed rejection
  with the staging dir removed (no allocation leak). `claim_node`/`complete_node`/
  `mark_node_started`/`record_process_*` all CAS on `attempt_id`; a stale worker raises
  `stale node completion`. `complete_node` rejects a non-`cancelled` completion when
  `desired_status=='cancelled'` (late-success rejection). `cancel_run` sets desired_status
  under lock, **releases the lock before OS process control**, then re-locks to reap and
  finalize; reconciliation-required nodes short-circuit to `reconciliation_required`;
  unreapable processes → `cleanup_failed` (never a false `reaped`). Locks are reentrant
  (thread-local depth + RLock serializes threads, flock/msvcrt serializes processes) and
  never held during model/tool/shell work. `_utc_now` and the rate-cutoff both use
  `datetime.now(timezone.utc).isoformat()` so the string comparison is sound.
- **Trust/secrets (S02/S06).** `compute_package_digest` hashes the YAML, sidecar, and
  every referenced command/script/MCP file plus MCP-referenced resources; symlinks are
  rejected; trust is profile-owned external state (a package cannot self-trust), keyed by
  content digest (moving a package does not grant trust; changing any covered byte
  revokes it). The store is 0600, written atomically with fsync + dir-fsync under a
  cross-process lock. `_cmd_run` refuses untrusted local execution
  (`cli.py:1205`). Bash node env is allowlisted to `PATH/HOME/TMPDIR/TEMP/SystemRoot/
  ComSpec/PATHEXT` + workflow vars — parent-process/API-key secrets do not reach bash
  nodes, and `.env` is not loaded in the bash executor. Event payloads pass through
  key-based `_sanitize` (secret/password/token/api_key/authorization → `[REDACTED]`),
  and `complete_node` pops `output` before persisting (`store.py:1802`).
- **Topology injection (S02/S09).** `sanitize_topology_label` builds labels only from
  node id + type, replacing any char outside Unicode L/N + ` -_.:/()` with `_`, wrapped in
  quotes; the emitter produces only `flowchart LR`, `nX["label"]`, and `nX --> nY`. A node
  id containing `"`, `%%{init}`, `<script>`, backticks, or newlines cannot escape the
  quoted label or inject a directive. Byte/node/edge limits null out Mermaid while keeping
  text. Verified by reading the sanitizer and the emitter grammar.
- **Showcase safety (S13).** Catalog load verifies catalog+package tree digests against
  `digests.json`, rejects `destructive` safety class, `requires_network`, wall-time over
  300 s (600 s AI), forbidden tokens, symlinks, and path escapes; the timeout mode remains
  a truthful `failed` terminal outcome while the `typed-timeout` demonstration claim
  passes (design-conformant). `reset_showcase` enumerates ownership evidence, not cron
  names.
- **Gates.** OTTO and LOOP24 `test_workflow_merge_gate.sh --phase brand` both pass
  (8/8 emitters `OK`, `TESTED_BRAND_SHA` matches the released commit); the customization
  checker exits 0; desktop `typecheck` exits 0; the 11 workflow-specific desktop vitest
  files pass under the CI-correct invocation.

---

## 7. Verification evidence

| Command | Where | Result | Source |
|---|---|---|---|
| `git cat-file -e` ×4 immutable commits | shared checkout | all OK | real |
| `git log --reverse --oneline 46fa66af..365e1605` | shared | 40 commits, S01–S14 map confirmed | real |
| `git diff --stat/--check 46fa66af..365e1605` | shared | 222 files / +27002 −1713; only EOF-blank notices | real |
| generic-runtime hash-equality across base/otto/loop24 | shared | 9/9 sampled files identical | real |
| `check_upstream_customizations.py --manifest …` | base worktree | exit 0 | real |
| `test_workflow_merge_gate.sh --phase base` | base worktree (detached) | **2 failed / 591 passed** under load; both flakes (F5) | real |
| `test_workflow_merge_gate.sh --phase brand --brand otto` | otto worktree | pass; `TESTED_BRAND_SHA=15165df3…` | real |
| `test_workflow_merge_gate.sh --phase brand --brand loop24` | loop24 worktree | pass | real |
| the two S01 stderr tests, isolated ×3 | shared | `2 passed` each (0.74/0.73/1.12 s) — proves F5 flakiness | real |
| `apps/desktop npm run typecheck` | shared | exit 0 | real |
| 11 workflow desktop vitest files (CI invocation) | shared | 11 files / 12 tests passed | real |
| global `vitest run --environment jsdom` | shared | 53 files fail — environment artifact, **not** a workflow defect (F8) | real |
| `_agent_override_allowed` probe | shared | provider/model `False`, `None` `True` (F2) | real |
| `render_bash` caller grep | shared | only `loop.py:201` + test (F3) | real |
| `scripts/run_tests.sh` (full ~17k suite) | base worktree | **not completed** — stopped after ~12 min to free CPU for F5 isolation; focused suites + brand gates substitute | partial/skipped |
| wheel/sdist build + install-into-isolated-env | — | **not run** — packaging assertions inspected via `tests/test_packaging_metadata.py` + `MANIFEST.in`/`pyproject` `plugins/workflow/showcases/**` entries and `showcase.py` `importlib.resources` loader; no live install performed | inspection |
| `test_workflow_upstream_merge.sh` full rehearsal | — | **not run** (multi-worktree, long); per-phase gates run instead | skipped |
| OTTO/LOOP24 Desktop `dist:*` build | — | **not run** (electron-builder, signing, long); typecheck + vitest substitute | skipped |
| native Windows process/uninstall paths | — | **not run** (macOS host) — residual risk | skipped |

No unrun gate is reported as passed. Detached worktrees at
`/private/tmp/.../scratchpad/wt/{base,otto,loop24}` were created for gate execution and
should be removed with `git worktree remove` (they share the main venv via symlink and
were not modified).

---

## 8. Required remediation before release/merge (ordered by risk × dependency)

1. **F1 (HIGH, S10/S14).** Implement 409 `onError` refetch + button `disabled`-on-
   stale/pending in `workflows/index.tsx` and `run-inspector.tsx`, and add the real
   renderer tests the DoD requires (keyboard/focus/virtualization/laptop-width/reduced-
   motion/409-rollback/cursor-gap, four locales). Until then, either withhold the Desktop
   operations pages or explicitly document them as unverified preview. This is the
   verdict-driving item.
2. **F2 (MEDIUM, S04/S08).** Reconcile the provider/model override gate with the compat
   report: seed the workflow plugin's override permission to what `assess_compatibility`
   certifies, or make provider/model-pinned nodes blocking in doctor unless configured.
   Add the real-runner e2e.
3. **F3 (MEDIUM, S06).** Route bash-node values through `VariableContext.render_bash`
   before spawn; add the substitution + injection-canary e2e.
4. **F4 (MEDIUM, S13).** Derive the report `cleanup` summary from RunStore
   `process_started/process_reaped/cleanup_failed` evidence; add the `cleanup_failed`
   report test.
5. **F5 (MEDIUM, S01/S14).** De-flake the two stderr invariant tests (clock injection or
   generous relative bounds) so the base merge gate is deterministic.
6. **F6/F7/F8 (LOW).** Tighten the checker ignore-prefixes; call `preflight_execution` on
   the run path for trusted `isolated_backend_required` sidecars; add a top-level desktop
   `test` script (or document the exact CI invocation).

---

## 9. Residual risks and unverified platform paths

- **Native Windows** (macOS host): `taskkill /T /F`, job-object process-tree termination,
  msvcrt file locking, WinError-5 pack-lock cleanup, and the uninstall console are covered
  only by simulation/`node --test` on this host — the DoD's "native Windows CI must pass"
  is not evidenced here. Highest residual.
- **Installed-distribution behavior**: wheel/sdist were not built and installed into an
  isolated env; the packaging claim (every showcase catalog/digest/script/MCP present at
  its relative path, runnable via `importlib.resources` without the source tree) rests on
  `test_packaging_metadata.py` + manifest inspection, not a live install.
- **Full temporary-worktree merge rehearsal** (`test_workflow_upstream_merge.sh`) and the
  **full ~17k `run_tests.sh` suite** were not completed; per-phase brand/base gates and
  focused suites substitute. A real `main→base→brand` merge with owned-symbol conflicts
  was not exercised end-to-end here.
- **Outward-action reconciliation**: the design promises "an outward action with a lost
  response becomes reconciliation_required," but there is no generic detector — only nodes
  that surface `unknown_side_effect`/`outcome_unknown` error codes route to reconcile
  (`scheduler.py` `FailureClass.RECONCILE`). A bash/command node that performed a real
  outward write and then crashed without emitting that code would be treated as an ordinary
  failure. Inherent to the model; flagged as residual, not a defect.
- **Coordinator stdin write**: `_exchange_worker` writes the (≤1 MiB) request to worker
  stdin synchronously before the monitoring loop; a request larger than the OS pipe buffer
  sent to a worker that hangs without reading stdin would block the coordinator thread
  (it unblocks with `BrokenPipeError` if the worker *exits*). The shipped worker reads
  stdin as its first action, so this is not reachable via the host-controlled
  `worker_argv`; noted as a theoretical robustness gap.
- **Shared prompt-cache / role-alternation invariant**: verified by construction (skills
  fold into the user turn, never the system prompt; fresh context uses a new session;
  shared context requires an exact fingerprint match) but not stress-tested against a live
  provider here.

---

*Prepared as a review artifact only. No implementation code, refs, releases, or unrelated
local changes were modified. The three detached review worktrees under the scratchpad may
be removed with `git worktree remove`.*
