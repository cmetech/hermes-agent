# Adversarial Code Review: Workflow Production Remediation Implementation

**Date:** 2026-07-18
**Branch:** `feat/workflow-production-remediation` @ `38e2c5b5e` (worktree clean; nothing merged/tagged/pushed)
**Base of contract:** the approved plan (`docs/superpowers/plans/2026-07-18-workflow-orchestration-operator-experience-plan.md`), the coordinator spec (`docs/superpowers/specs/2026-07-18-plugin-background-services-workflow-coordination-design.md`), the operator-experience design, and the accepted re-review conditions.
**Method:** five parallel deep-code audits (Phase-1 safety; generic lifecycle hosts; coordinator; machine/provenance/auth contracts; Desktop/retention/notifications), each reading the real modules and tests with file:line citations. Change surface: 102 files, +14,058/−949 from `83aee7409`.

## Executive verdict

**NOT READY — for merge to base, not only for release.**

The self-review's verdict (NOT READY FOR RELEASE) and its two acknowledged gaps are honest, but it **materially understates the defect surface**. The Phase-1 evidence-safety substrate (index reconciliation, cleanup token binding, journal framing, reclaim fencing) is genuinely production-grade and backed by real byte-level fault-injection tests. But this review found **two security-grade defects the self-review missed** (a `workflow:read` token can perform every mutation; the evidence log reader follows symlinks out of the run directory), **three High coordinator-correctness defects** (a deposed leader keeps dispatching and its shutdown interrupts the new leader's claims; a foreground run is permanently orphaned under a healthy coordinator while `resume` is advertised as a silent no-op), and **a High idempotency regression that reintroduces the F-06 duplicate-run class for the exact crash-retry case the whole design exists to prevent** (the CLI folds its own PID into the start digest). Several headline coordinator contract claims — epoch fencing of claims, time-bounded/cursor-paginated sweeps, the 60 s/300 s stall thresholds, policy-controlled lane release, the count-only attention inbox — are **not implemented as specified**. None of these depend on the two decisions the handoff asks for; they must be fixed regardless.

## Severity summary

| Severity | Count | Meaning |
|---|---:|---|
| Critical | 2 | Authorization bypass; arbitrary-file exfiltration through an authenticated API |
| High | 9 | Lifecycle corruption, duplicate-run/duplicate-effect, stranded run with a lying recovery action, unmet headline deliverable |
| Medium | 13 | Contract violation with a workaround, residual safety window, operational weakness |
| Low | 9 | Quality, bound, growth, and polish gaps |

The two acknowledged blockers (Gateway delivery owner absent; `POST /runs` absent + Desktop-hardcoded decision channel) are carried as **H-01/H-02** unchanged.

---

## Critical findings

### C-01: A `workflow:read` token can perform every state mutation

- **Area:** API authorization. **Status:** implementation defect (auth bypass).
- `_verified_operator` gates on `workflow:read` **OR** `workflow:admin` (`plugins/workflow/dashboard/plugin_api.py:109-112`), and `mutate_run` performs **no write-scope check** (`plugin_api.py:624-739`). A service token minted with only `workflow:read` can approve, reject, provide-input, cancel, abandon, retry, reconcile, archive, and restore. Only cleanup/notification endpoints require `high_trust` (`plugin_api.py:216-220, 272, 294, 324, 354, 378, 398, 420`). Additionally every verified dashboard session is `high_trust=True` unconditionally (`plugin_api.py:97-102`), so any org session can execute destructive cleanup.
- **Impact:** the design's "fail-closed authorization" and "least authority" contract is violated; a read-only integration credential can approve outward-action gates and destroy evidence.
- **No test** exercises a read-scoped token attempting a mutation.
- **Fix:** require `workflow:admin` (or an explicit write scope) for `mutate_run` and every write route; make `high_trust` derive from an explicit admin scope, not from session presence. Add read-token-mutation-denied tests through the real middleware.
- **Blocks merge to base.**

### C-02: Evidence log reader follows symlinks out of the run directory

- **Area:** evidence API / path containment. **Status:** implementation defect (arbitrary file read).
- `EvidenceReader._logs` globs `nodes/*/*/std*.txt` and calls `path.read_bytes()` with **no symlink or containment check** (`plugins/workflow/evidence.py:132-150`). A workflow node (untrusted package content) that writes `nodes/<n>/<a>/stdout.txt` as a symlink to `~/.ssh/id_ed25519` exfiltrates up to the read bound through the authenticated `/runs/{id}/evidence?kind=logs` endpoint.
- The codebase already knows the correct pattern: `_contained` resolve+containment (`plugins/workflow/showcase.py:149-160`) and artifact digest verification (`showcase.py:536-537`). The evidence reader simply doesn't use it. **No symlink/traversal test** exists in `tests/plugins/workflow/test_evidence_api.py`.
- **Fix:** resolve each candidate under the run root with strict containment and symlink rejection before reading; add traversal/symlink regression tests. Same containment must apply to any future raw-artifact download (currently unimplemented, so not yet exposed).
- **Blocks merge to base.**

---

## High findings

### H-03: Deposed coordinator keeps dispatching — claims/completions are not epoch-fenced

- **Area:** coordinator correctness. **Status:** contract claim ("epoch fencing scheduling+claims") not implemented.
- `claim_node` checks only node `state=="ready"` + worker capacity; `owner_epoch` is stored as opaque text and never validated against `coordinator_lease` (`store.py:3054-3164`). `complete_node` fences only on `attempt_id`, not epoch (`store.py:3288-3338`). The coordinator's scheduler sets `owner_id` but never `execution_owner_id`, so `_renew_execution_owner` returns `True` unconditionally (`coordinator.py:158-163`, `scheduler.py:301-310`) — the sweep's `advance()` loop has no per-iteration ownership check.
- **Trace:** leader A (epoch N) stalls mid-sweep; lease expires; B acquires epoch N+1 and dispatches. A resumes inside `scheduler.advance` (`coordinator.py:201`) and keeps claiming/dispatching **new** nodes (incl. outward) under stale epoch N until its `_lead` loop next fails `renew` (~heartbeat 5 s) + shutdown deadline (~8 s). Two coordinators dispatch concurrently for that window.
- **Fix:** verify a live matching `coordinator_lease` inside `claim_node`/`complete_node`/`schedule_retry`'s `BEGIN IMMEDIATE` (the exact check `_reclaim_still_running_claim` already performs at `store.py:4206-4227`). **No test kills a live leader mid-sweep** and asserts the deposed leader cannot dispatch — which is why the hole survived.
- **Blocks merge to base.**

### H-04: A deposed leader's shutdown interrupts the *new* leader's claims

- **Area:** coordinator correctness. **Status:** implementation defect (cross-epoch interference).
- `scheduler.shutdown` → `interrupt_active_claims(run_id)` pops **any** claim on the run with no owner filter (`store.py:4327-4330`; invoked from `coordinator.py:311-313`). The dying epoch-N leader can interrupt/pause claims that epoch-N+1 just created on the same run, flipping a healthy run to `interrupted`/`paused`.
- **Fix:** filter `interrupt_active_claims` by `owner_id`/epoch so a coordinator only tears down its own claims.
- **Blocks merge to base** (pairs with H-03).

### H-05: A foreground run is permanently orphaned under a healthy coordinator, and `resume` is advertised as a silent no-op

- **Area:** recovery / next_actions integrity. **Status:** implementation defect + lying action vocabulary.
- If a `--foreground` run's owner process dies while a coordinator is healthy: the coordinator skips foreground runs unconditionally (`coordinator.py:189, 197-199`), and `claim_foreground_execution` refuses takeover while any fresh coordinator lease exists (`store.py:2707-2728`), and the CLI `_continue_foreground_if_owned` refuses too (`cli.py:1613-1615`). Net: no execution path. `get_run_status` correctly reports `stalled`/`foreground_owner_unavailable` and advertises `resume` (`actions.py:45-46`) — but `resume_run` only acts on `{failed, interrupted}` and otherwise returns the projection unchanged (`store.py:4861-4862`), so REST returns 200 having done nothing. The only real recovery is `cancel`.
- **Fix:** either let the coordinator adopt a stale-owner foreground run (expire its claims via the lease-expiry machine), or let `claim_foreground_execution` take over an expired foreground lease even with a live coordinator, or stop advertising `resume` for that state. Advertising an action that no-ops is the exact "untruthful next_actions" class the plan set out to kill.
- **Blocks merge to base.**

### H-06: The CLI poisons its own idempotency with the caller PID — cross-process stable-key retries are rejected

- **Area:** idempotency (reintroduces the F-06 class). **Status:** implementation defect.
- `--source-instance` defaults to `f"cli:pid:{os.getpid()}"` (`cli.py:1452`; showcase `showcase.py:394`), and `source_instance` is inside `digest_record()` → the start digest (`provenance.py:105-120`, `store.py:1681-1682`). Retrying the **same stable intent key from a new process** — precisely the crash/retry case the skill mandates key reuse for (`skills/productivity/workflow/SKILL.md:34-36`) — yields a different start digest and is **rejected `idempotency_conflict` exit 5** instead of joining as `existing` (`store.py:1764-1773`). The behavioral test passes only because both invocations share one pytest PID (`tests/skills/test_workflow_operator_behavior.py:112-120`); **no cross-process retry test exists.**
- **Fix:** derive `source_instance` from a stable source identity (profile/session/host), never the transient PID, for machine starts; add a cross-process same-key retry test asserting `existing`.
- **Blocks merge to base** — this is the headline duplicate-run guarantee failing under the one scenario it must hold.

### H-07: `return_route` (and actor identity) are inside the start digest, contradicting the provenance contract

- **Area:** provenance. **Status:** contract violation.
- The design states `return_route` is stored **outside** the start digest; `digest_record()` pops only `admitted_at` (`provenance.py:117-120`), so `return_route`, `actor_id`, and `claimed_actor` are all digest-covered. A verified adapter retrying the same intent with a refreshed return route gets `idempotency_conflict`.
- **Fix:** exclude `return_route` and volatile actor fields from the digest; keep only workflow/source/intent/inputs identity. Pairs with H-06.
- **Blocks merge to base.**

### H-08: The attention inbox is still the count-only stub — the phase's own headline deliverable

- **Area:** Desktop UX. **Status:** deliverable unmet.
- `apps/desktop/src/app/workflows/attention-inbox.tsx` (18 lines) renders **only `items.length`** (`:15`) — no itemization, no per-item run/interaction, no deep links. The server side *is* itemized (`plugin_api.py:480-543`); the renderer discards it. This is exactly the "prior stub was count-only" condition the phase claimed to fix (prior finding F-22). `aria-label` is hardcoded English (`:13`).
- **Impact:** compounded by H-01, a Gateway-originating operator with Desktop-only delivery sees only a bare number; the "make attention visible and actionable" goal is not met on the primary surface.
- **Fix:** render itemized attention with run/workflow/interaction/kind and click-through to `selectWorkflowRun`; localize the label.
- **Blocks release; blocks the Desktop UAT gate.**

### H-09: Archive and History are truncated to the newest ~200 runs

- **Area:** retention/history. **Status:** implementation defect.
- The board/history view filter runs **after** an SQL `ORDER BY created_at DESC LIMIT 200` scan (`store.py:2511-2517` then `:2566-2581`), and `_authorized_runs` re-caps at 200 (`plugin_api.py:180-187`). Archived or aged runs outside the newest 200 overall are unreachable from both API and UI, and `next_cursor` pages a truncated universe. Cleanup (which needs History to find old terminal runs) therefore cannot reach exactly the runs most eligible for it.
- **Fix:** push view/archive/age filters into SQL with real keyset pagination over the filtered set, not a post-filter of a capped scan.
- **Blocks release** (History/retention UAT).

### H-10 / H-02 (carried): Direct authenticated API admission absent; REST decision channel hardcoded `desktop`

- The REST router has no `POST /runs` (`plugin_api.py` exposes only inspect/mutate/cleanup); `mutate_run` records approve/reject `channel="desktop"` regardless of principal (`plugin_api.py:664, 675`) and passes **no `actor`** even though `operator.principal` is in hand — the durable decision event records no authenticated actor from REST (`store.py:5049-5052`). Matches the handoff's H-02.
- **Fix:** the plugin-owned background-only `POST /runs` in the handoff's decision 2, plus server-derived channel/actor on decisions. **Must not** inherit H-06/H-07 (derive `source=api`, stable server-side `source_instance`, `return_route` outside digest).
- **Blocks release** (API UAT, blockers 9/11).

### H-11 / H-01 (carried): Gateway notifications have no authenticated delivery owner

- Confirmed by audit: the only destination ever written is `"desktop"` (`store.py:3672`, `notifications.py:308`), the only lease caller hardcodes `destination="desktop"` (`plugin_api.py:357`), no Gateway adapter exists. Undelivered attention **is** durable and visible via `/attention` and blocks cleanup — but on the Gateway surface it is never delivered. Matches the handoff's H-01.
- **Fix:** the handoff's decision 1 (generic authenticated plugin-command context + narrow Gateway delivery port). **Blocks release** (blockers 15/16/17).

---

## Medium findings

### M-04: Coordinator sweep has no time bound, head-of-line blocks, and its cursor is dead code
`_sweep_once` advances up to ~300 runs **synchronously to completion/pause** on a single worker (`coordinator.py:165-221, 236`; `scheduler.advance` waits on every node, `scheduler.py:700-799`). One 30-minute AI node blocks every other background run. The "100 runs / 2 s" bound does not exist; `run_ids[-1]` is persisted as a cursor (`coordinator.py:221, 266-272`) but **nothing reads it** — `list_runs(limit=200)` takes no offset (`store.py:2476-2517`), so with >200 nonterminal runs (cap raisable to 200,000) older runs are never scanned. Use `advance_all` interleaving + real pagination + the documented time budget.

### M-05: The 60 s runnable-stall and 300 s semantic-stall thresholds do not exist
No `runnable_stall_seconds`/semantic-stall config anywhere (`models.py:200-266`); `stalled` fires **instantly** (e.g., the moment a foreground lease lapses). `last_semantic_progress_at` and the leader's `last_progress_at` are persisted but never evaluated into health. Config also sits under `plugins.entries.workflow.runtime`, not `...coordinator` as the spec states, and lease≥3×heartbeat is not enforced (only `heartbeat<lease`, `models.py:261-262`).

### M-06: Lane-release policy for paused/interrupted is not policy-controlled
The re-review condition required a sidecar knob (release vs `queue_strict`, outward-hold). Implementation hardcodes **always-hold** for `paused`/`interrupted` (`store.py:1845, 2349, 3866-3867`); `pause_lane_policy` exists only in the plan doc (grep finds no code). Safe direction (conservative), but the stated contract is unmet and a forgotten approval wedges the lane.

### M-07: Resume/approve/provide-input/retry bypass the executing-capacity limit and the fair queue
These set `status="running"` directly (`store.py:4892, 5081, 5216, 5279`), skipping the queue and the `limits["executing"]` check — executing-capacity oversubscription is possible. `queue_position` is assigned but never used for promotion order (`try_promote_run` has no ordering, `store.py:2335-2374`); promotion is wake-generation then `created_at DESC` (newest-first), so the "durable fair queue" for resumed work is absent.

### M-08: F-21a residual — claim release not in `finally`; loop frames unbudgeted
`_release_worker_claim` sits at `store.py:3492`, **not** in a `finally`. `_ensure_run_capacity` reserves `(node_count+8)` frames at claim time (`store.py:1601-1634`), but loop nodes append a full-projection frame per iteration (`record_loop_iteration`, `store.py:3496-3541`) outside that budget; a long loop can exhaust `max_journal_bytes` so the terminal `complete_node` append raises `StorageQuotaError` (a `RuntimeError`, escapes the scheduler's stale-filter at `scheduler.py:607-609`), leaving the claim held and the run wedged `running` with terminal nodes — the original F-21a. Recovery is blocked because `expire_stale_claims` must itself append. **No test injects quota inside `complete_node`.** Wrap release in `finally`; make terminal transitions always-affordable.

### M-09: F-20 residual — abandon ignores a live claim carrying a process identity
`_set_terminal`'s new atomic gate inspects only `node["recovery"]`, never a live `claim` with a bound PID (`store.py:5426-5435`). Sequence: parallel node A pauses (run→`paused`) while node B executes; coordinator crashes before B's lease expires (no `recovery` yet); CLI `workflow abandon` (no CAS, `cli.py:1715-1717`) passes — B's claim is popped and `worker_claims` deleted with B's process alive and never terminated (unlike `cancel_run`). Extend the gate to fail/observe any node with an active claim whose identity isn't proven stopped.

### M-10: F-03 residual — spawn-before-persist identity window
`BashExecutor` spawns the tree **before** journaling `process_started` (`executors/bash.py:70-83`). A crash in that window leaves an outward node with no `process_identity` → observation `not_started` → `termination_confirmed=True` (`store.py:4107-4110`), which **unlocks `safe-to-retry`** (`store.py:5330-5338`) while the orphan still runs → duplicate outward effects. Record a spawn-intent marker pre-spawn, or treat a claimed-but-identityless attempt as `outcome_uncertain`.

### M-11: F-10 residual — mid-session index status drift not repaired on healthy load
`_sync_loaded_integrity` repairs integrity columns but never `runs.status` (`store.py:784-818`). A crash between a journal append and its `UPDATE runs SET status` leaves the index lagging until the next process restart; capacity queries read the stale status (`store.py:2352-2354, 3871-3873`) → transient over-admission past `max_executing_runs`. The F-10 phantom-capacity shape, in miniature. Reconcile status on healthy load, not only on rebuild/restart.

### M-12: Windows termination proof is weak and entirely untested
`process_tree_active` short-circuits on `nt` when the direct child exits, hiding detached grandchildren (`executors/base.py:117-118`); `terminate_existing`'s taskkill-unavailable fallback returns `True` after a single `os.kill` root-only (`tools/managed_process.py:427-432`); `_terminate_owned_posix_group` is a no-op on Windows (`managed_process.py:318-319`). All are `pragma: no cover`. So `termination_confirmed=True` can be recorded on Windows while descendants keep running outward work — directly undermining F-03's replay fence on the target platform. **Blocks any Windows workflow claim** (already blocker 6/16).

### M-13: `_already_decided` null-interaction path is still reachable from REST
The fix skips non-matching decisions **only when an interaction ID is supplied** (`store.py:4925-4927`); with `interaction_id=None` it still returns the first recorded decision on any node (`store.py:5022-5025`). REST `ActionRequest.interaction_id` defaults to `None` and is passed straight through for approve/reject (`plugin_api.py:616, 663, 674`); `approve_showcase`/`reject_showcase` also pass none (`showcase.py:408, 414`). The two-gate misattribution (prior F-19) survives on the REST and showcase surfaces. Require the current interaction ID for every decision, or reject a null ID when a pending interaction exists.

### M-14: Showcase JSON/no-wait starts still mint random idempotency keys; preflight omits required inputs
`showcase run <id> --json --no-wait` with no key silently generates `secrets.token_urlsafe(24)` per invocation (`showcase.py:383`; no CLI gate at `cli.py:1816-1828`) — the exact random-key-on-machine-start the contract forbids and that produced the original duplicate. Separately, showcase preflight still does not enumerate required inputs (`showcase.py:265-276`); `symptom` surfaces only at run time as `input_required` — the re-review condition is unmet (contrast `workflow doctor`, which publishes `input_requirements`).

### M-15: Notification dead-letter has no retry surface and never prunes; sweep repair is expensive
Dead-lettered notifications (`notifications.py:406-419`) have **no** retry endpoint or CLI anywhere and permanently block cleanup (`store.py:5646-5647`). Outbox and facts rows are never pruned (survive run cleanup), and `history()` returns oldest-first so recent history is unreadable past 200 rows (`notifications.py:466`). `reconcile_journal` loads all fact keys into memory and re-reads up to 200 full journals **every sweep** (as often as every 5 s, `coordinator.py:175`) — the dominant steady-state I/O, degrading with history size. Add an explicit dead-letter retry, prune delivered/old rows, and bound the repair scan.

### M-16: Generic-lifecycle overlap and killed provider hot-reload
(a) `start_background_services` prunes only *quiescent* hosts, then binds a fresh generation of the same host kind while a wedged `stop_timeout` generation's `run()` thread is still alive (`plugins.py:1446-1463`) — the spec's "prevents in-process replacement" is enforced only on the reload path. (b) The new `discover_and_load(force=True)` interlock (`plugins.py:1339-1342`) now always raises inside a bound host, and four tool dispatchers (`tts_tool.py:525`, `transcription_tools.py:942`, `video_generation_tool.py:239`, `image_generation_tool.py:1319`) swallow it and degrade to "provider unavailable" — mid-session provider reconfiguration in Desktop/gateway is silently dead, and `reload_background_services` (the sanctioned replacement) has **zero production callers**.

---

## Low findings

- **L-01:** argparse errors and exit code **1** sit outside the enveloped machine contract — `workflow run --json --bogus` prints stderr usage, exits 2 with no JSON (`cli.py:312-515`); `_cmd_run`/`_cmd_validate` return an off-table `1` (`cli.py:1501, 646`), and the behavioral harness codifies it (`test_workflow_operator_behavior.py:93`). The exit-code table isn't published in `operator_command_contract` (`machine_contract.py:92-209`).
- **L-02:** `KeyError` handler does `exc.args[0]` (`cli.py:1959`) → `IndexError` on a no-arg `KeyError` (escapes the envelope); and any internal dict-lookup `KeyError` is mislabeled `not_found` exit 3 — the over-broad repurposing the plan meant to bound.
- **L-03:** `RuntimeError` classified by substring (`"stale"`/`"conflict"` in message, `cli.py:1974`) — fragile; `"admission reservation is not active"` becomes non-retryable exit 8, any "conflict" message becomes retryable.
- **L-04:** legacy projection with no `trigger` falls back to `source="cli"` (`provenance.py:124`) — the "never show cli for absent data" condition; untested for the missing-trigger case.
- **L-05:** three sanitizers coexist — API+notifications share `sanitize.sanitize_projection`, but CLI `events` uses the weaker `store._sanitize` (`store.py:164-181`) and CLI `status`/`run`/`runs` JSON is emitted with **no** sanitizer (`cli.py:1497-1516`), leaking `operator_scope_digest`/`idempotency_key_digest` the API deliberately drops. Silent 200-entry truncation in `sanitize_projection` (`sanitize.py:31-39`) and the 200-run listing cap carry no marker.
- **L-06:** `run_showcase` writes a `trusted_distribution` trust record with `risk_digest=package_digest` on **every** run (`showcase.py:381-382`) — a trust-store write as a side effect, with a bogus risk digest.
- **L-07:** `events.isError` disables **all** run actions (`index.tsx:190`) — a timeline hiccup or a waiter-capacity 429 locks out Approve/Cancel. Each open inspector permanently holds one of 16 long-poll slots (`runtime.py:50`).
- **L-08:** unbounded growth — `coordinator_wakes`/`coordinator_events` never pruned (`coordinator_store.py:119-157`); `_process_locks` interns one RLock per run path forever (`locks.py:25-33`); `$workflowSelectedRunId` can pin a cleaned-up run into a 20 s 404 loop.
- **L-09:** dormancy conformance test's lease dimension is vacuous (points at a temp dir the coordinator never uses, `tests/hermes_cli/test_plugin_background_services.py:392, 398`); `workflow-operations.e2e.test.tsx` is adapter-only despite the name; no `aria-busy`, no laptop-width test; two hardcoded English aria-labels; `WORKFLOW_NODE_COLUMNS`/`$workflowAttentionFirst` dead exports.

---

## What is genuinely right (preserve)

- **Phase-1 evidence safety is real and well-tested.** Index-loss preserves and quarantines (never deletes) with corroboration-as-authority and a durable generation handshake (`store.py:325-364, 919-1128`); cleanup is preview-by-default with a content+version+expiry-bound single-use token and quarantine-before-delete (`store.py:5485-5797`); the `--dry-run` inversion is structurally gone; journal framing (`schema_version:2`/`frame_sha256`) recovers only the torn **tail**, quarantines the bytes, and isolates per-run load failures from listings (`store.py:2079-2158, 2519-2545`); reclaim re-adoption is tightly fenced to the same fresh coordinator epoch. Fault injection is byte-level, not mocked.
- **Backward compatibility is proven** by a hash-pinned v2.0.9 fixture migration test (additive `ALTER TABLE`, legacy unframed events accepted, evidence bytes untouched, idempotency still matches).
- **Election, admission fencing, foreground tokens, R-05 runtime fixes, and the REST no-advance rule are sound and tested** — one-leader/one-standby multiprocess tests, admission refusal inside the transaction with no run dir on refusal, refcounted store LRU + 16-permit long-poll semaphore, and a plugin_api that imports no scheduler and never calls `advance`.
- **The behavioral skill harness is real** — it renders argv from the published contract, parses through the real CLI, executes real handlers against a real store, and proves one-run-per-intent (within a process).
- **The generic lifecycle host is faithful** — blocking `run(stop_event)`, cached non-blocking `health()` on a dedicated probe thread, no lock held during joins, abort-on-timeout leaves registrations intact, safe mode calls no factory, no workflow imports in the four host files (AST-guarded).
- **The self-review is honest** about its two gaps and claims no readiness from local-only evidence.

---

## Answers to the three decisions requested

**These decisions are secondary.** They unblock the two self-identified gaps (H-01/H-02), but C-01, C-02, H-03, H-04, H-05, H-06, H-07 must be fixed regardless of how they are decided.

1. **Generic authenticated plugin-command context + narrow Gateway delivery port — APPROVE THE DIRECTION, with conditions.** Option 1 in the self-review is the right shape (smallest change that carries verified route provenance across the start and delivery halves; workflow plugin as the sole consumer; no workflow imports in Gateway core; return-route verification preserved). Condition it on: the delivery port accepting **only** an opaque server-minted return-route capability (never a caller string — the C-01/H-07 lesson), the outbox coalescing/dead-letter/prune fixes (M-15) landing with it, and the same generic-lifecycle overlap guard (M-16) applying to the new consumer. Do **not** adopt option 2 (broader broker) or option 3 (declare Gateway unsupported) — option 3 contradicts approved goals and would require a documented scope reduction.

2. **Plugin-owned background-only `POST /runs` — APPROVE THE SHAPE, but it must not inherit the CLI's idempotency/provenance bugs.** Background-only, required caller idempotency, coordinator-refusal, no synchronous execution, `RunStore.start_run` reuse: all correct. **Mandatory pre-conditions:** derive `source=api`, the verified principal, operator scope, and `source_instance` from the **server authentication context** (not a client field, not a PID — fix H-06 at the source), store `return_route` **outside** the start digest (fix H-07), and enforce the write-scope gate from C-01 on the new route. A caller-controlled `source` header must not be authoritative.

3. **Scoped no-regression Desktop lint — ACCEPT for this branch; require baseline cleanup as a separate change.** The repository-wide red baseline is in untouched Electron/release-update/cron/icon/theme files; sweeping it into this branch would enlarge an already-large diff and muddy the merge. Accept the scoped no-regression comparison here and file the baseline cleanup separately. This is the lowest-stakes of the three and should not gate the security/correctness work.

## Release-blocker status (delta from the self-review's table)

The self-review marks blockers 1–5, 7, 8, 10, 12, 13, 14 "implemented; local gate passes." This review **reopens**:

- **Blocker 3** (lease/uncertain-effect fencing): residual F-03 spawn window (M-10) + Windows termination proof (M-12) + F-20 live-claim gap (M-09).
- **Blocker 7** (durable continuation of every transition): H-03/H-04 (deposed-leader dispatch/interrupt), H-05 (foreground orphan), M-04 (sweep bound/pagination), M-07 (capacity bypass).
- **Blocker 9** (deterministic idempotency): H-06 (PID-in-digest), H-07 (return-route in digest), M-14 (showcase random keys), L-01 (envelope gaps).
- **Blocker 12** (authorization): C-01 (read→mutate), C-02 (symlink exfiltration), M-13 (null-interaction), L-05 (CLI sanitizer gaps).
- **Blocker 13** (Desktop actions/evidence): H-08 (attention stub), H-09 (history truncation), M-15 (dead-letter/prune).

Blockers 6, 9, 11, 15, 16, 17 remain blocked as the self-review states.

## Recommended fix order (smallest risk-reducing first)

1. **Security first, isolated and cheap:** C-01 (write-scope gate), C-02 (evidence containment), M-13 (require interaction ID). Each is a few lines + a denied-path test through real middleware.
2. **Idempotency/provenance digest correctness:** H-06 (stable server `source_instance`), H-07 (`return_route` outside digest), M-14 (showcase key gate + preflight inputs). One coherent change to `provenance.digest_record` and the two start paths, plus a cross-process retry test.
3. **Coordinator fencing:** H-03 (epoch check in claim/complete), H-04 (owner-filtered interrupt), then the mid-sweep-kill test that would have caught both.
4. **Foreground recovery:** H-05 (adopt-or-don't-advertise), and the residual safety windows M-09/M-10; M-11 status resync on load.
5. **F-21a `finally` + terminal-append affordability (M-08).**
6. **Sweep bounds/pagination/stall thresholds (M-04/M-05), lane policy (M-06/M-07).**
7. **Desktop deliverables:** H-08 (attention inbox), H-09 (history pagination), notification prune/dead-letter (M-15).
8. **Then** the two decision-gated surfaces (Gateway delivery port, `POST /runs`) with the conditions above, and the Windows/CI/UAT matrix (M-12, blockers 6/16).

---

*Prepared as a review artifact only; the only repository write is this document. Evidence from five parallel deep-code audits at `38e2c5b5e`, each cited to file:line in the working tree.*
