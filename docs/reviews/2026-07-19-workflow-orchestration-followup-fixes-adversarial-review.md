# Portable Workflow Orchestration Follow-up Fixes Adversarial Review

**Date:** 2026-07-19
**Branch:** `feat/workflow-production-remediation` @ `b850e2cc75cd4ccc16819224d151642e3b6c0f76` (Draft PR #3 → `main`)
**Scope:** the six fix commits addressing the High and five Mediums from
`2026-07-19-workflow-orchestration-remediation-fix-adversarial-review.md`
(`1be33f389` NF-H1, `326193c63` NF-M1, `e7e9db9b7` NF-M4, `883b33ad5` NF-M2,
`fefed8eb2` NF-M3, `ace002436` NF-M5, plus docs `b850e2cc7`).
**Method:** four independent adversarial reviewers (delivery topology;
delivery redaction/retry; store fencing/FIFO; gate reproduction + whole-delta
regression sweep), each instructed to refute the fix. ~230 targeted tests
executed plus a full merge-gate reproduction.

## Verdict

**READY FOR MAINTAINER MERGE REVIEW — no Critical or High merge blocker
remains.**

All six findings are verified closed at HEAD. The merge gate reproduces
exactly (**652 passed / 1 skipped**, installed-distribution 1, Desktop 17,
tsc pass, exit 0; the +17 over the prior 635 reconciles precisely to the 15
newly-gated async tests plus 2 new admission tests). The whole-delta
regression sweep found **zero weakening** of previously verified closures: the
coordinator change is purely additive on the non-leader path, the store hunks
stay strictly inside the two intended scopes, the gate script gained files and
lost nothing, no new env vars, no workflow imports in generic hosts. The docs
honestly record that the earlier "no High" verdict was corrected rather than
obscuring it, and the committed copy of the prior review report is untampered.

The fixes introduce **one new Medium** (a failover-latency liveness cost of
the NF-H1 fix — below) and a small number of new Lows. Under the remediation
plan's completion condition (no Critical or High), the branch is mergeable;
the new Medium is a fix-or-accept maintainer decision, and its fix is small.

## Per-finding closure

### NF-H1 (High) — CLOSED

The standby (non-leader) gateway host now drains `gateway:*` outbox rows at
the top of every election-loop iteration (`coordinator.py:539`, ~5 s cadence),
reusing the identical `_deliver_gateway_notifications` path: same per-row
`lease_gateway` acquisition under `BEGIN IMMEDIATE` (distinct
`delivery:<owner>` owner id), same ack/fail/terminal classification, same
transport idempotency key backstop. The drain touches only notification
outbox/facts state — it constructs no execution fence and can never claim,
dispatch, adopt, or advance a run, so the leader-only execution model is
intact. The topology test is real: two actual `WorkflowCoordinatorService`
instances (web leader + gateway standby) against one shared store, real
election, delivery asserted via the durable outbox `delivered` state with
leadership unchanged (`test_coordinator.py:443-525`). Web hosts run the same
drain harmlessly (port `None` → immediate return). Works with no leader at
all (drains while contending).

### NF-M1 (Medium) — CLOSED

Both leak sites closed: `sanitize.py` `_SECRET_KEY` now matches
`return[_-]?route` (provenance redacted on every sanitized surface), and
gateway `transition_key` values are masked at presentation
(`<prefix>:gateway:opaque`, covering pre-fix stored rows) while new rows store
`gateway:sha256:<digest>`. An exhaustive hunt across every read surface —
list/get/attention/events/evidence/cleanup/notification-lease endpoints, CLI
JSON and human output, gateway command responses, `_public` payloads — found
no remaining exposure; the HTTP lease endpoint is hard-scoped to `desktop` so
gateway rows are unleaseable over HTTP. The cleanup `confirmation_token`
passthrough is preserved (explicit allowlist carve-outs untouched, tests
pass).

### NF-M2 (Medium) — CLOSED

`claim_node` now validates foreground authority **inside** the claim's
`BEGIN IMMEDIATE` transaction (`store.py:4461-4490`): mode, owner id, epoch,
and lease freshness against the DB row; non-foreground mode fails hard. The
downstream mutations (`mark_node_started`, spawn records, `complete_node`)
are already claim-identity-gated under the run lock, and adoption atomically
pops the claim — so a stale owner's post-adoption mutations fail
deterministically, and an executor spawned inside the intent→register window
is terminated when `record_process_started` refuses. The residual
two-executor window is genuinely closed, not merely shrunk. Adoption cannot
interleave with a fresh-lease claim (adoption requires an expired lease). The
resumed CLI exits cleanly (no unhandled exception).

### NF-M3 (Medium) — CLOSED

A single bounded SQL helper (`_eligible_queued_predecessor`,
`store.py:3104-3131`) now treats a queued run as a blocking FIFO predecessor
only when its lane is not held, wired at all three sites (start_run ingress,
`_request_runnable_locked`, `try_promote_run`) inside the same transaction as
the lane/capacity checks — last-slot atomicity intact. Same-lane fairness is
preserved (the untouched lane `active` check still refuses younger same-lane
runs; the durable `queue_sequence` keeps the skipped head authoritative once
its lane frees). New tests cover the independent-lane promotion and the
ingress case; the profile-wide freeze is gone.

### NF-M4 (Medium) — CLOSED

Classification is by receipt state, and the boundary is exactly right: all
pre-send storage work lives in `_begin_delivery`, whose `sqlite3.Error` now
returns `retryable_failure` → `outbox.fail()` (bounded 8-attempt backoff →
dead-letter); the durable `sending` receipt commits **before** the transport
send, so a "failed after send, before receipt" misclassification is
structurally impossible — post-send failures still map to
`terminal_fail(outcome_uncertain=True)` and stranded `sending` rows are never
replayed. Tests cover pre-send contention with zero transport attempts,
retry-then-success, and post-send receipt loss with no resend. The standby
drain (NF-H1 fix) shares this exact classification.

### NF-M5 (Medium) — CLOSED

All three async surfaces (`tests/gateway/test_plugin_background_services.py`,
`tests/gateway/test_plugin_delivery.py`,
`tests/hermes_cli/test_plugin_provider_hot_reload.py`) are now in the green
merge-gate selection, collect and pass there (pytest-asyncio present in the
gate venv), and a new meta-test locks them in. The NF-H1/M2/M3 regression
tests are green-gated via files already in the CI portability matrix.

## New findings from the fixes

- **NF2-M1 (Medium) — standby drain can delay coordinator failover by
  minutes.** The drain runs synchronously on the election-loop thread
  (`coordinator.py:539`); with a hung messaging adapter, up to 20 leased rows
  × the sender's 15 s timeout ≈ 300 s per iteration with no `try_acquire`.
  If the web leader dies at the start of such a drain, the gateway standby
  cannot contend until it finishes — coordinator vacancy (all run scheduling
  stalled) for up to ~5 minutes instead of ~30 s. Compound-failure scenario
  (adapter outage + leader crash), liveness only, no safety impact. Fix is
  small: drain on a worker thread (mirroring the leader's sweep pool) or cap
  per-iteration drain wall time / lease fewer rows. Fix-or-accept before
  merge.
- **NF2-L1 (Low)** — no workflow-layer test that two concurrent drainers
  honor each other's outbox lease (exclusivity is code-verified plus
  transport-dedup-tested only).
- **NF2-L2 (Low)** — gateway transition-key dedup discontinuity across the
  fix boundary: a crash-replay of a pre-fix-recorded transition after upgrade
  can re-enqueue and re-send one already-delivered gateway notification
  (one-shot; moot on this unreleased surface, would matter only if a build
  containing the pre-fix format had shipped).
- **NF2-L3 (Low)** — the raw capability is still persisted in
  `workflow_notification_facts.destination` (masked on every read path, but
  at rest indefinitely since facts outlive outbox pruning); hashing it there
  too would complete the at-rest story.
- **NF2-L4 (Low)** — no coordinator-level test pins
  `retryable_failure → fail()`; dropping that branch would silently
  reintroduce NF-M4.
- **NF2-L5 (Low)** — the NF-M3 fix widens an NF-L13 corner: at full
  executing capacity behind a lane-held head, a new `queue`-policy start is
  now **rejected** (`executing_capacity`) where it previously queued — a
  typed rejection, not a hang, but the ingress/central predicate drift is now
  reachable in a new corner. Recommend fixing NF-L13 (queue at capacity for
  queue-policy starts) or documenting the rejection as intended.
- **NF2-L6 (Low)** — the meta-test locking the gate additions
  (`tests/scripts/test_workflow_merge_gate.py`) is itself enforced only by
  the currently-red repo-wide lane; adding `tests/scripts/` to the merge-gate
  selection would give the lock teeth.
- **Info** — commit-message inaccuracies (1be33f389 cites test additions in
  `test_notification_delivery.py` it doesn't contain; e7e9db9b7 cites
  coordinator changes it doesn't contain — the coordinator half pre-existed
  and is correct at HEAD); no user-visible CLI notice when a foreground run
  is adopted by the coordinator (journal + status payload only); an
  embedded-API `RunScheduler` constructed with neither fence nor owner
  silently stalls at foreground-lease expiry (all in-repo constructors pass
  one — API hygiene); CI run 29692156577's cited portability jobs all passed,
  but its overall conclusion is "cancelled" on pre-existing baseline lanes —
  readers should not infer overall CI green.

## Disposition

1. **NF2-M1**: fix (small, contained in `coordinator.py`) or explicitly
   accept the failover-latency trade-off with a recorded rationale. This is
   the only finding above Low from this pass.
2. **NF2-L1..L6** join the existing NF-L1..L13 backlog (now ~19 Lows); none
   is merge-blocking alone. NF2-L4 and NF2-L1 are pure test additions and
   cheap.
3. The prior review's required actions are otherwise fully discharged; under
   the remediation plan's completion condition, this branch now qualifies for
   maintainer merge review. Merge scope unchanged: PR #3 only;
   `feat/workflow-operator-experience` remains excluded.
