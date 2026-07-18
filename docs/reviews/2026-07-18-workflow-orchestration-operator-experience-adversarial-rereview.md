# Adversarial Re-Review: Workflow Orchestration Operator Experience (Planning Pass 2)

**Date:** 2026-07-18
**Reviewed commits:** `bc7754daa` (reconcile coordinator architecture), `6037278af` (reorder production remediation plan), `83aee7409` (pin coordinator operating contracts), on `fix/windows-index-materialization` @ `83aee7409`.
**Reviewed artifacts:** the reconciliation document, the new plugin-background-services/coordinator design, the rewritten operator-experience design, the reordered 10-phase remediation plan, and the base-design production amendment. Candidate worktree verified untouched at `43edb4d4b`.
**Prior review:** `2026-07-18-workflow-orchestration-operator-experience-adversarial-review.md` (verdict NOT READY).

## Executive verdict

**READY WITH CONDITIONS** — as a plan. (Implementation obviously remains NOT READY; nothing has been built.)

The second planning pass is a substantively correct response, not a cosmetic one. Every Critical and High finding from the prior review now has a named owner, a durable mechanism, a failing-test-first phase, and an ordered release blocker; the reconciliation document's two corrections to my findings (F-03 wording, F-26 CLAUDE.md subclaim) are accurate and I accept both. The architecture choice — a minimal generic host-owned blocking service lifecycle with the workflow plugin owning election, wakes, sweeps, and the outbox — is the right shape: it fixes the ownerless-execution root cause without widening the model-facing core, without workflow imports in shared hosts, and with an honest reload/no-overlap tradeoff. The conditions below are five design-level ambiguities that should be pinned **before Phase 3 code exists**, because each one changes durable schema or user-visible semantics.

## Verification of the handoff's claims

| Claim | Verified |
|---|---|
| HEAD `83aee7409`, tracked tree clean, worktree unchanged at `43edb4d4b` | Yes (`git log`, `git status`, worktree list) |
| Prior review + reconciliation committed | Yes (`bc7754daa` adds both under `docs/reviews/`) |
| Base design amended, superseding foreground-owned scheduling language | Yes (`docs/design/portable-workflow-orchestration.md` 2026-07-18 amendment block; scheduling §4, shutdown, admission-lane, and touch-budget sections rewritten) |
| Seven state machines present | Yes — generic lifecycle, coordinator ownership, continuation/lanes, coordinator-unavailable, lease-expiry/reconciliation, notifications, archive/cleanup (`...plugin-background-services...md`) |
| Ten risk-ordered phases + 17-item ordered blocker checklist | Yes (`...operator-experience-plan.md`) |
| Coordinator defaults pinned | Yes (`plugins.entries.workflow.coordinator`: heartbeat 5 s, lease 30 s, sweep 5 s, 100 runs/2 s bounds, runnable-stall 60 s, semantic-stall 300 s) |
| No implementation merged/modified | Yes — all three commits are docs-only |

Finding-by-finding: the reconciliation table's dispositions for F-01–F-28 match my evidence; the plan's Phase 1 covers F-01/F-02/F-03/F-09/F-10/F-20/F-21a, Phases 2–3 cover F-04/F-05 (and explicitly reject both candidate commits as-is, correctly), Phase 4 covers F-12/F-13/F-06, Phase 5 covers F-14/F-25, Phase 6 covers F-07/F-11/F-17/F-23, Phase 7 covers F-22/F-18(partially), Phase 8 covers F-16, Phase 9 covers F-08/F-24, Phase 10 covers F-15 and the UAT gates. No prior finding is unaddressed.

## Decisions under review — verdicts

### 1. Lifecycle API: host-owned blocking `run(stop_event)` + `health()` — **APPROVE**

The selection rationale is sound and matches the first consumer (a synchronous scheduler). The rejected alternatives are rejected for the right reasons: B invites unaccounted tasks, C cannot prove quiescence before reload — and quiescence-before-generation-N+1 is the property that makes hosted reload safe at all. Three conditions, all already implied but worth making test-enforced:

- **Factory dormancy is a rule without a mechanism.** "A factory may not start a thread, acquire a durable lease, spawn a process, or open a listener before returning" is unverifiable by the host. Add a conformance test pattern (thread-count/child-process delta across factory invocation) to `test_plugin_background_services.py` so at least first-party violations are caught.
- Keep `health()` O(1)/cached as specified; add a test that a blocking `health()` implementation cannot stall `snapshot()`.
- The stop-timeout → refuse-replacement rule is correct; ensure the snapshot distinguishes `stop_timeout` (old thread still referenced) from `failed` so operators don't restart a host thinking the service crashed.

### 2. Execution-lane release while paused/retrying/interrupted — **APPROVE FOR `waiting_retry`; CONDITIONS FOR `paused` AND `interrupted`**

Releasing the lane at retry-wait is unambiguously right (a sleeping backoff should never pin a lane). The other two change user-visible semantics of `queue` overlap policy:

- **`paused` release creates interleaving the old contract forbade.** Under `queue`, "one active run per concurrency key" previously meant strict serialization. With lane release, the moment run A pauses at its approval gate, queued run B **starts executing the same workflow** — including, in the incident's shape, the accidental duplicate. Two showcase runs would now both run their analyze branches instead of one being stranded. For workflows serializing access to an external resource (the reason to choose `queue`), interleave-at-human-gate may be a correctness violation, not an optimization. **Condition:** make pause-release policy-controlled in the sidecar/overlap policy (e.g., `queue` releases the lane at pause; `queue_strict` holds it), or at minimum default-hold for workflows with declared outward-action nodes and document the interleaving semantics explicitly. Idempotency (Phase 4) reduces the *accidental* duplicate case but does not address the *intentional* queued-second-run case.
- **`interrupted` release can promote a successor over half-applied external state.** An interrupted run with outward-effect nodes may have partially mutated an external system; promoting the queued next run into the lane before the interruption is resolved risks conflicting external writes. **Condition:** hold the lane (or require reconciliation/abandon first) when the interrupted run has any outward-classified attempt; release freely otherwise. This composes with the Phase 1.3 effect-classification work already planned.

### 3. Coordinator operating defaults — **APPROVE WITH TWO AMENDMENTS**

The numbers are internally consistent (lease ≥ 3× heartbeat holds; sweep bounds prevent unbounded scans; wakes can trigger early sweeps). Two gaps:

- **Laptop suspend/wake will shred long-running nodes.** With a 30 s lease, every lid-close longer than 30 s expires the claims of in-flight nodes. The lease-expiry state machine has `LeaseExpired → StillRunning` (identity proves the same live process) but its only exits are `Interrupted` (terminate) or `ReconciliationRequired` — there is **no re-adoption transition**. A healthy AI node surviving a 2-minute sleep would be killed or escalated to an operator decision on every wake. Add `StillRunning → Reclaimed` (re-lease under the current epoch) when the process identity matches, the attempt is the newest for its node, and the coordinator holds a fresh lease — unconditionally for side-effect-free nodes, and for outward nodes when the epoch/fencing checks pass. Without this, the coordinator converts a previously tolerated suspend into a routine failure mode.
- **5 s periodic sweep has no idle backoff.** Worst case 2 s of sweep work per 5 s period is up to 40 % duty cycle in the Desktop backend on battery. Since durable wakes already trigger early sweeps, the periodic sweep is purely a recovery net — back it off (e.g., toward 30–60 s) when the previous sweep found zero nonterminal runs, and restore the 5 s cadence on any wake. Also ensure stall classification distinguishes "runnable but waiting for a lane" (a `waiting` state with a queue-position explanation) from "runnable with no owner" (a true `runnable_stall_seconds` stall) — the spec's health taxonomy supports this; make it an explicit test.

### 4. Deterministic CLI/exit-code contract — **APPROVE, ONE MIGRATION NOTE**

The envelope (single JSON object on stdout for success *and* failure, versioned, typed error codes, `retryable`, `next_actions`) and the 0/2/3/4/5/6/7/8/70 table resolve F-13 completely, including doctor-nonzero, real tail semantics, and CAS-as-typed-result. "0 includes idempotent existing-result reuse" is the right call for `already_decided`-with-matching-decision. One note: exit code 3 currently means "decision not applied" on `approve`/`reject` (`cli.py:1353`) and is being repurposed to "not found." Any existing script or skill text that keyed on the old 3 must be migrated in the same phase, and the envelope's `schema_version` — not exit codes — should be documented as the primary machine dispatch key. Add "not-found errors include bounded `candidates` in `error.details`" (both workflow and showcase namespaces) to Phase 4's checklist — it is in the spirit of the design's namespace rules but not yet an explicit task item.

### 5. Durable notification policy — **APPROVE WITH TWO AMENDMENTS**

The outbox state machine (Pending/Leased/Delivered/DeadLetter/Superseded/Dismissed), transactional enqueue, per-destination receipts, and "suppressed delivery never omits the durable transition" are exactly what F-08 required, and grounding Gateway delivery in the kanban-notifier pattern is the right reuse. Amendments:

- **Add a coalescing/rate policy.** Dedup is per `(transition, state_version, destination)`, so a flapping run (fail → retry → fail …) legitimately generates a new row per transition. Define per-run collapse for same-kind notifications within a window (supersede older undelivered rows of the same kind) so a retry loop cannot storm a channel while still preserving the durable transition history.
- **Name the Desktop receipt semantics.** For the pull-based Desktop destination, specify that the outbox row is `Leased` by the web API read and `Delivered` only on Electron's acknowledgement callback — otherwise a crashed renderer between fetch and display silently consumes the notification. The spec implies this ("Electron may ... acknowledge the delivery receipt"); make it normative in Phase 9's tests.

Also carry one explicit statement into the operator docs: on a CLI-only install (no web/gateway host ever running), there is no delivery owner **and no coordinator** — background admission is refused and notification facts are query-only. This is a coherent posture; it just must be documented as an operational requirement (cron-driven background workflows require a running gateway or Desktop/headless-serve host).

### 6. Seven-day terminal-board visibility default — **APPROVE**

As a pure display-window policy under `plugins.entries.workflow.retention`, never invoking cleanup, with aging affecting only the board projection: correct and consistent with goals 12–15. Keep the previous plan's discipline that the window is computed in UTC from `updated_at` with an injectable clock for tests (the reordered plan no longer states this explicitly — restore it as a Phase 8 test item), and ensure an *archived-then-restored* run re-enters History, not the active board, per the design.

## New findings in the updated planning artifacts

### R-01: Chat/agent provenance cannot be "authenticated" through a bash-spawned CLI — pin the trust wording (Medium, design gap)

The operator-experience design says adapters supply provenance "through typed internal APIs, not arbitrary CLI strings" and `actor_id` is a "verified principal." But the chat path is skill → terminal tool → CLI subprocess: the only channel is CLI arguments/environment from an unauthenticated local process. Any local process can claim `source=chat, actor=X`. This is acceptable under the design's own "CLI is a separate local-admin trust boundary" — but then chat-sourced provenance is a **trusted local claim**, not an authenticated fact, and the design should say so rather than promise verification it cannot deliver (the same overpromise pattern that produced F-11). Concretely for Phase 6: REST/Desktop admission carries authenticated provenance; CLI admission carries local-admin-claimed provenance and is labeled as such in evidence; the gateway may optionally mint a per-session token/environment handle for skill-spawned CLI processes later, but that is an enhancement, not a Phase 6 requirement.

### R-02: Foreground execution vs live coordinator needs an explicit arbitration statement (Medium, design gap)

`--foreground` remains available even when a healthy leader exists. Two dispatchers can then operate one run store concurrently. Node-level at-most-once is already guaranteed by the `worker_claims` UNIQUE constraint and attempt CAS, so this is likely safe — but the coordinator's sweep must not treat a foreground executor's live claims as stale (it heartbeats, so lease logic covers it) and must not concurrently dispatch *other* nodes of a foreground-owned run in a way the foreground process doesn't expect (parallel branches could execute half in-process, half in the coordinator host). Pin one rule in the coordinator design: a run admitted/executed in foreground mode records a run-level execution mode, and the coordinator does not dispatch for foreground-mode runs while their owner's claims are fresh; it adopts them only through the normal lease-expiry machine. One sentence plus one two-process test in Phase 3.

### R-03: Leader placement is arbitrary between a sleepy Desktop host and an always-on gateway (Low, optimization)

Both host kinds run standbys; election is first-CAS-wins. A laptop Desktop backend that suspends frequently is a materially worse leader than a continuously running gateway, and every suspend forces a takeover cycle (and, per condition 3 above, lease churn). Consider a host-kind election bias (gateway preferred; web acquires only after N missed gateway heartbeats) or a takeover-hygiene note. Not a blocker.

### R-04: Migration/versioning of the new durable schema is implied but not itemized (Low, plan gap)

Phases 1.1/1.4 cover corrupt/partially-migrated DBs and projection versioning, and Phase 3 adds coordinator/wake tables, Phase 8 archive metadata, Phase 9 the outbox. No phase explicitly requires an upgrade test from a **pre-amendment fixture database** (current v2.0.9 schema) through the full new schema set, on all three platforms. Add one migration-fixture test item to Phase 10.2 (or Phase 1.4) so update-path installs — the stated UAT posture — are proven, not assumed.

### R-05: Desktop read-path fixes are split and partially unassigned (Low, plan gap)

Phase 7 replaces the 1 s full reads with bounded summaries/cursors (good), but the prior review's per-request `RunStore` construction (`plugin_api.py:28-29`) and a concurrency cap on sync long-polls sharing the chat threadpool are not named in any phase checklist. Add both to Phase 6 (evidence API implementation) where the API surface is being rebuilt anyway.

## Ordered conditions (all planning-level; resolve before the named phase starts)

1. Before **Phase 3**: pin lane-release policy for `paused`/`interrupted` (decision 2 conditions), the `StillRunning → Reclaimed` re-adoption transition and sweep idle backoff (decision 3), and the foreground-vs-leader arbitration rule (R-02). Each changes durable schema or fencing semantics — cheapest to decide now.
2. Before **Phase 4**: record the exit-code-3 repurposing migration note and add not-found `candidates` to the checklist (decision 4).
3. Before **Phase 6**: adopt the R-01 provenance trust wording (authenticated vs local-admin-claimed) in the operator-experience design.
4. Before **Phase 8**: restore the UTC/injectable-clock visibility-window test item (decision 6).
5. Before **Phase 9**: add notification coalescing and Desktop receipt semantics (decision 5); document the CLI-only-install posture in `docs/workflow-orchestration.md`.
6. Before **Phase 10 sign-off**: add the migration-fixture test (R-04) and the Desktop read-path items (R-05).

None of these invalidates the plan's structure or ordering; all are additive amendments to already-owned sections.

## What is now right (and should not be re-litigated)

- The generic lifecycle is genuinely minimal: two service methods, two host kinds, no restart loops, no election, no scheduling in base Hermes; ledger rows with removal conditions exist for every shared file before any code.
- The coordinator design correctly refuses to derive background-execution authority from process-local health or file locks, is SQLite/Windows-first, and epoch-fences dispatch.
- Durable wakes committed in the same transaction as the mutation, with local signals demoted to latency optimizations, is precisely the crash-safe continuation contract the incident demanded.
- Phase 1's inversion — evidence-safety before any new capability — matches the prior review's recommended order, and the plan explicitly forbids merging either candidate commit wholesale.
- The reconciliation document is honest: it narrows two findings with correct code citations rather than accepting or dismissing them wholesale, and it declines to use severity counts as a completion metric in favor of the ordered blocker list.

## Verdict restated

Planning: **READY WITH CONDITIONS** (the six ordered conditions above). Implementation: blocked pending maintainer approval as the plan itself states, and the 17-item release-blocker checklist — with blocker 17's fresh adversarial review — remains the exit gate.

---

*Prepared as a review artifact only; the only repository write is this document. Evidence from `83aee7409` and the diffs of `bc7754daa`/`6037278af`/`83aee7409`.*
